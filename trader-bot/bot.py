#!/usr/bin/env python3
"""Trading bot for trader-bot: runs the opening-range breakout end to end and
places the orders itself. Standard library only.

It chains the pack's own tools, in process, so every order it sends passed
the same rules a hand-made one does:

    entry.evaluate      the first 5-minute close above the 15-minute range
    exit.compute        stop at the range midpoint, target 1R (config)
    size.size_trade     shares from your budget and max loss per trade
    check.check         trader-risk's PASS / BLOCK, with today's log
    Alpaca REST         one bracket order (entry, stop-loss, take-profit)

and it flattens its own positions by flat_by (15:55 New York).

    python3 bot.py run                       # Alpaca PAPER account, until the close
    python3 bot.py run --dry-run             # live Yahoo bars, simulated fills, no broker
    python3 bot.py once                      # one pass, then exit (for a scheduled task)
    python3 bot.py status                    # today's state, positions, P&L
    python3 bot.py stop                      # kill switch: no entries, flatten, exit
    python3 bot.py resume                    # remove the kill switch
    python3 bot.py flatten                   # close the bot's positions now
    python3 bot.py replay --offline DIR --day 2026-10-19   # a past day, simulated

LIVE MONEY needs all three, or the bot refuses to start:
    "paper": false in trader/config.json
    --live on the command line
    TRADER_BOT_LIVE=real-money in the environment
Any two without the third is an error, not a fallback. Paper is the default.

Keys: ALPACA_API_KEY and ALPACA_SECRET_KEY (or APCA_API_KEY_ID and
APCA_API_SECRET_KEY) in the environment, never in a file. Paper keys go to
paper-api.alpaca.markets, live keys to api.alpaca.markets.

Symbols, first found wins: --symbols, "bot": {"symbols": [...]} in the
config, --watchlist FILE, trader/watchlist.json (trader-scanner's hits),
trader/shortlist.json (the original Trader skill's fundamental screen, its
"passed" tickers). At most "max_symbols" (25) are watched.

The guardrails, on top of trader-risk's rules:
  kill switch     trader/STOP exists: no entries, flatten the bot's positions, exit
  daily loss      realized + open P&L of today's bot trades at or under
                  -max_daily_loss_usd: no more entries, flatten
  own positions   it only trades symbols you do not already hold, and it only
                  ever closes what it opened, never your other holdings
  stale trigger   a trigger older than max_trigger_age_min (6) is skipped
  chasing         a price more than max_chase_pct (0.5 %) over the trigger is skipped
  idempotent      each order carries a client_order_id; after a network error
                  the bot asks Alpaca whether it went through before anything else
  P&L unknown     if today's fills cannot be read, no new entries that pass

State: trader/state/bot.json (today), trader/state/bot.log (one line per
event), and every order it places is appended to trader/state/broker.json in
trader-broker's format, so trader-risk and ticket.py log see it.

Exit codes: 0 done for the day, 1 refused or stopped on an error, 2 bad input.
"""

import argparse
import datetime
import importlib.util
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

PAPER_URL = "https://paper-api.alpaca.markets"
LIVE_URL = "https://api.alpaca.markets"
DATA_URL = "https://data.alpaca.markets"
LIVE_ENV = "TRADER_BOT_LIVE"
LIVE_WORD = "real-money"
KEY_ENVS = (("ALPACA_API_KEY", "ALPACA_SECRET_KEY"), ("APCA_API_KEY_ID", "APCA_API_SECRET_KEY"))
BOT_DEFAULTS = {
    "symbols": [],
    "max_symbols": 25,
    "or_minutes": 15,
    "stop": "or-mid",
    "target": "1R",
    "min_volume_x": None,
    "max_trigger_age_min": 6,
    "max_chase_pct": 0.5,
    "max_open_positions": 1,
    "poll_seconds": 20,
    "scan_seconds": 60,
}
PAUSE = 0.5          # seconds between Yahoo chart requests
CANCEL_WAIT = 1.0    # seconds for Alpaca to release a bracket's legs before closing the position


class BotError(Exception):
    pass


class BrokerError(Exception):
    def __init__(self, msg, code=None, sent=None):
        Exception.__init__(self, msg)
        self.code = code
        self.sent = sent  # None: unknown whether the request reached the broker (timeout, reset)


def _sibling(folder, name):
    path = os.path.join(ROOT, folder, name + ".py")
    if not os.path.exists(path):
        raise BotError("trader-bot needs %s/%s.py next to it: install the whole pack" % (folder, name))
    spec = importlib.util.spec_from_file_location("trader_bot_" + name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


entry = _sibling("trader-entry", "entry")
exitp = _sibling("trader-exit", "exit")
size = _sibling("trader-budget", "size")
risk = _sibling("trader-risk", "check")


# ---------------------------------------------------------------- files

def read_json(path, default=None):
    if not path or not os.path.exists(path):
        return default
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except ValueError as e:
        raise BotError("%s is not valid JSON: %s" % (path, e))


def write_json(path, doc):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


def load_config(path):
    cfg = dict(size.DEFAULTS)
    doc = read_json(path, {})
    if not isinstance(doc, dict):
        raise BotError("%s is not a JSON object" % path)
    cfg.update(doc)
    bot = dict(BOT_DEFAULTS)
    bot.update(doc.get("bot") or {})
    cfg["bot"] = bot
    if not size._f(cfg.get("budget")):
        raise BotError("no \"budget\" in %s: the bot sizes every trade to it" % path)
    if not size._f(cfg.get("max_loss_per_trade_usd")) or not size._f(cfg.get("max_daily_loss_usd")):
        raise BotError("the bot needs max_loss_per_trade_usd and max_daily_loss_usd in %s" % path)
    if not cfg.get("require_stop", True):
        raise BotError("the bot always trades with a stop: \"require_stop\" must stay true")
    for k in ("allow_short", "allow_margin", "allow_options"):
        if cfg.get(k):
            raise BotError("the bot trades long stock only: set \"%s\": false" % k)
    exitp.parse_stop(bot["stop"])
    exitp.parse_target(bot["target"])
    return cfg


def mode_of(cfg, live_flag, dry_run, env):
    """'dry-run', 'paper' or 'live'. Live needs the config, the flag and the env word, all three."""
    paper = cfg.get("paper", True) is not False
    if dry_run:
        if live_flag:
            raise BotError("--dry-run and --live together: pick one")
        return "dry-run"
    if paper:
        if live_flag:
            raise BotError("--live, but the config says \"paper\": true. Set \"paper\": false to trade real money.")
        return "paper"
    if not live_flag:
        raise BotError("the config says \"paper\": false, but --live is missing. Real money needs --live, "
                       "or set \"paper\": true.")
    if env.get(LIVE_ENV) != LIVE_WORD:
        raise BotError("live trading also needs %s=%s in the environment (it is %s)"
                       % (LIVE_ENV, LIVE_WORD, "unset" if LIVE_ENV not in env else "%r" % env[LIVE_ENV]))
    return "live"


def keys_from(env):
    for k, s in KEY_ENVS:
        if env.get(k) and env.get(s):
            return env[k], env[s]
    raise BotError("no Alpaca keys: set ALPACA_API_KEY and ALPACA_SECRET_KEY in the environment")


def symbols_from(a, cfg):
    """(symbols, where they came from)."""
    cap = int(cfg["bot"].get("max_symbols") or 25)

    def done(syms, src):
        out = []
        for s in syms:
            s = str(s).strip().upper()
            if s and s not in out:
                out.append(s)
        if not out:
            raise BotError("no symbols in %s" % src)
        return out[:cap], src + (" (first %d)" % cap if len(out) > cap else "")

    if getattr(a, "symbols", None):
        return done(a.symbols.split(","), "--symbols")
    if cfg["bot"].get("symbols"):
        return done(cfg["bot"]["symbols"], "the config's bot.symbols")
    for path in [p for p in (getattr(a, "watchlist", None), "trader/watchlist.json", "trader/shortlist.json") if p]:
        if not os.path.exists(path):
            if path == getattr(a, "watchlist", None):
                raise BotError("no watchlist at %s" % path)
            continue
        return done(symbols_in_file(path), path)
    raise BotError("no symbols: pass --symbols, set bot.symbols in the config, or run trader-scanner first")


def symbols_in_file(path):
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    if text.lstrip().startswith(("{", "[")):
        try:
            doc = json.loads(text)
        except ValueError as e:
            raise BotError("%s is not valid JSON: %s" % (path, e))
        if isinstance(doc, dict):
            items = doc.get("hits") or doc.get("passed") or doc.get("symbols") or []
        else:
            items = doc
        return [x.get("symbol") or x.get("ticker") if isinstance(x, dict) else x for x in items if x]
    return [ln.split("|")[0].strip() for ln in text.splitlines() if ln.strip() and not ln.startswith("#")]


# ---------------------------------------------------------------- brokers

def usd(x):
    """-46.88 -> "-$46.88"."""
    x = round(float(x), 2)
    return ("-" if x < 0 else "") + size.money(abs(x))


def iso_utc(utc):
    return utc.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(text):
    """Alpaca's RFC 3339 time -> naive UTC."""
    t = str(text).replace("Z", "+00:00")
    if "." in t:
        head, tail = t.split(".", 1)
        frac = "".join(ch for ch in tail if ch.isdigit())
        tz = tail[len(frac):]
        t = head + "." + frac[:6] + tz
    d = datetime.datetime.fromisoformat(t)
    if d.tzinfo:
        d = d.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    return d


def alpaca_body(order, client_id):
    """Our order (place_stock_order field names) -> Alpaca's POST /v2/orders body."""
    body = {"symbol": order["symbol"], "qty": str(order["qty"]), "side": order["side"],
            "type": order.get("type", "market"), "time_in_force": order.get("time_in_force", "day"),
            "client_order_id": client_id}
    if order.get("limit_price") is not None:
        body["limit_price"] = str(order["limit_price"])
    if order.get("order_class") == "bracket":
        body["order_class"] = "bracket"
        body["take_profit"] = {"limit_price": str(order["take_profit_limit_price"])}
        body["stop_loss"] = {"stop_price": str(order["stop_loss_stop_price"])}
    return body


class Alpaca(object):
    """Alpaca's trading REST API, the few calls the bot needs."""

    def __init__(self, key, secret, live=False, opener=None, timeout=15):
        self.base = LIVE_URL if live else PAPER_URL
        self.live = live
        self.name = "Alpaca %s" % ("LIVE" if live else "paper")
        self._key, self._secret = key, secret
        self._open = opener or urllib.request.urlopen
        self.timeout = timeout

    def _req(self, method, path, body=None, base=None, query=None):
        url = (base or self.base) + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={
            "APCA-API-KEY-ID": self._key, "APCA-API-SECRET-KEY": self._secret,
            "Content-Type": "application/json", "Accept": "application/json"})
        try:
            with self._open(req, timeout=self.timeout) as r:
                raw = r.read()
        except urllib.error.HTTPError as e:
            try:
                msg = json.loads(e.read().decode("utf-8")).get("message")
            except Exception:
                msg = None
            raise BrokerError("%s %s: HTTP %s%s" % (method, path, e.code, (" " + msg) if msg else ""),
                              code=e.code, sent=True)
        except (urllib.error.URLError, OSError) as e:
            raise BrokerError("%s %s: %s" % (method, path, getattr(e, "reason", e)), sent=None)
        if not raw:
            return None
        return json.loads(raw.decode("utf-8"))

    def account(self):
        a = self._req("GET", "/v2/account")
        return {"cash": float(a["cash"]), "equity": float(a.get("equity") or a["cash"]),
                "status": a.get("status"), "blocked": bool(a.get("trading_blocked") or a.get("account_blocked")),
                "number": a.get("account_number")}

    def positions(self):
        out = {}
        for p in self._req("GET", "/v2/positions") or []:
            out[p["symbol"].upper()] = {"qty": float(p["qty"]), "price": float(p.get("current_price") or 0),
                                        "market_value": float(p.get("market_value") or 0)}
        return out

    def open_orders(self, symbol=None):
        q = {"status": "open", "nested": "true", "limit": "500"}
        if symbol:
            q["symbols"] = symbol
        return self._req("GET", "/v2/orders", query=q) or []

    def order_by_client_id(self, client_id):
        try:
            return self._req("GET", "/v2/orders:by_client_order_id", query={"client_order_id": client_id})
        except BrokerError as e:
            if e.code == 404:
                return None
            raise

    def submit(self, order, client_id):
        o = self._req("POST", "/v2/orders", body=alpaca_body(order, client_id))
        return {"id": o.get("id"), "status": o.get("status"), "client_order_id": o.get("client_order_id")}

    def cancel(self, order_id):
        self._req("DELETE", "/v2/orders/%s" % order_id)

    def close(self, symbol):
        o = self._req("DELETE", "/v2/positions/%s" % urllib.parse.quote(symbol))
        return {"id": (o or {}).get("id"), "status": (o or {}).get("status")}

    def fills(self, day):
        rows = self._req("GET", "/v2/account/activities/FILL", query={"date": day, "page_size": "100"}) or []
        return [{"symbol": r["symbol"].upper(), "side": r["side"], "qty": float(r["qty"]),
                 "price": float(r["price"])} for r in rows]

    def last_price(self, symbol, chart=None):
        try:
            t = self._req("GET", "/v2/stocks/%s/trades/latest" % urllib.parse.quote(symbol), base=DATA_URL,
                          query={"feed": "iex"})["trade"]
            return float(t["p"]), parse_iso(t["t"])
        except (BrokerError, KeyError, TypeError, ValueError):
            return chart_price(chart)

    def observe(self, symbol, chart, now_utc):
        pass  # the real broker fills its own brackets


def chart_price(chart):
    """(last price, naive UTC time) from a Yahoo chart, or (None, None)."""
    if not chart:
        return None, None
    p = chart.get("meta", {}).get("regularMarketPrice")
    t = chart.get("data_time")
    if p is None and chart.get("bars"):
        p, t = chart["bars"][-1]["c"], chart["bars"][-1]["t"]
    return (float(p), entry.utc_of(t)) if p is not None and t else (None, None)


class SimBroker(object):
    """Simulated fills from the bars the bot sees: no network, no keys. A market
    order fills at the last price; a bracket leg fills when a later closed bar
    reaches it (stop first when one bar touches both, and at the bar's open on
    a gap through the stop)."""

    live = False
    name = "simulated (dry run)"

    def __init__(self, cash):
        self.cash = float(cash)
        self.pos = {}          # sym -> {"qty", "price", "stop", "target", "since"}
        self.orders = []       # open bracket legs, one record per symbol
        self.fill_log = []     # {"day", "symbol", "side", "qty", "price", "why"}
        self.prices = {}
        self.n = 0
        self.by_client = {}

    def account(self):
        eq = self.cash + sum(p["qty"] * self.prices.get(s, p["price"]) for s, p in self.pos.items())
        return {"cash": round(self.cash, 2), "equity": round(eq, 2), "status": "ACTIVE", "blocked": False,
                "number": "SIM"}

    def positions(self):
        return {s: {"qty": p["qty"], "price": self.prices.get(s, p["price"]),
                    "market_value": round(p["qty"] * self.prices.get(s, p["price"]), 2)}
                for s, p in self.pos.items()}

    def open_orders(self, symbol=None):
        return [o for o in self.orders if symbol is None or o["symbol"] == symbol]

    def order_by_client_id(self, client_id):
        return self.by_client.get(client_id)

    def _fill(self, day, sym, side, qty, price, why):
        price = round(price, 2)
        if side == "buy":
            self.cash -= qty * price
        else:
            self.cash += qty * price
        self.fill_log.append({"day": day, "symbol": sym, "side": side, "qty": qty, "price": price, "why": why})

    def submit(self, order, client_id):
        sym, qty = order["symbol"], float(order["qty"])
        price = self.prices.get(sym)
        if price is None:
            raise BrokerError("no price for %s yet" % sym, sent=False)
        if qty * price > self.cash + 0.005:
            raise BrokerError("insufficient buying power", code=403, sent=True)
        self.n += 1
        oid = "sim-%d" % self.n
        self._fill(self.day, sym, "buy", qty, price, "entry")
        self.pos[sym] = {"qty": qty, "price": price, "stop": float(order["stop_loss_stop_price"]),
                         "target": float(order["take_profit_limit_price"]), "since": self.now_ts}
        self.orders = [o for o in self.orders if o["symbol"] != sym]
        self.orders.append({"id": oid + "-legs", "symbol": sym})
        rec = {"id": oid, "status": "filled", "client_order_id": client_id}
        self.by_client[client_id] = rec
        return rec

    def cancel(self, order_id):
        self.orders = [o for o in self.orders if o["id"] != order_id]

    def close(self, symbol):
        p = self.pos.pop(symbol, None)
        if p:
            self._fill(self.day, symbol, "sell", p["qty"], self.prices.get(symbol, p["price"]), "flatten")
        self.orders = [o for o in self.orders if o["symbol"] != symbol]
        return {"id": "sim-close-%s" % symbol, "status": "filled"}

    def fills(self, day):
        return [f for f in self.fill_log if f["day"] == day]

    def last_price(self, symbol, chart=None):
        """The last CLOSED bar's close (observe sets it), never a later tick from the file."""
        p = self.prices.get(symbol)
        return (p, entry.utc_of(self.now_ts)) if p is not None else (None, None)

    def set_clock(self, now_utc):
        self.now_ts = entry.ts_of(now_utc)
        self.day = entry.to_eastern(now_utc).date().isoformat()

    def observe(self, symbol, chart, now_utc):
        """Price the symbol from the chart and fill any bracket leg a closed bar reached."""
        self.set_clock(now_utc)
        step = entry.INTERVALS.get(chart.get("meta", {}).get("dataGranularity") or "5m", 300)
        closed = [b for b in chart["bars"] if b["t"] % step == 0 and b["t"] + step <= self.now_ts]
        p, _ = chart_price(chart)
        if closed:
            p = closed[-1]["c"]
        if p is not None:
            self.prices[symbol] = float(p)
        pos = self.pos.get(symbol)
        if not pos:
            return
        for b in closed:
            if b["t"] < pos["since"]:
                continue
            if b["l"] <= pos["stop"]:
                self._exit(symbol, min(pos["stop"], b["o"]), "stop-loss")
                return
            if b["h"] >= pos["target"]:
                self._exit(symbol, max(pos["target"], b["o"]), "take-profit")
                return

    def _exit(self, symbol, price, why):
        p = self.pos.pop(symbol)
        self._fill(self.day, symbol, "sell", p["qty"], price, why)
        self.orders = [o for o in self.orders if o["symbol"] != symbol]


# ---------------------------------------------------------------- the bot

class Bot(object):
    def __init__(self, cfg, broker, symbols, mode, charts=None, state_dir="trader/state", kill_path="trader/STOP",
                 emit=print, sleep=time.sleep):
        self.cfg = cfg
        self.b = cfg["bot"]
        self.broker = broker
        self.symbols = symbols
        self.mode = mode
        self.charts = charts or (lambda sym, day: entry.get_chart(sym, "5m", day=day))
        self.state_path = os.path.join(state_dir, "bot.json")
        self.log_path = os.path.join(state_dir, "bot.log")
        self.broker_path = os.path.join(state_dir, "broker.json")
        self.kill_path = kill_path
        self.emit = emit
        self.sleep = sleep
        self.st = None

    # -- state

    def state(self, today):
        if self.st is None:
            self.st = read_json(self.state_path, None)
        if not self.st or self.st.get("date") != today:
            self.st = {"date": today, "mode": self.mode, "symbols": {s: {"status": "watching"} for s in self.symbols},
                       "entered": [], "halted": None, "flattened": False, "last_scan": None, "pnl": None}
        for s in self.symbols:
            self.st["symbols"].setdefault(s, {"status": "watching"})
        return self.st

    def save(self):
        write_json(self.state_path, self.st)

    def log(self, now_utc, event, text, symbol=None):
        stamp = entry.to_eastern(now_utc).strftime("%Y-%m-%d %H:%M:%S")
        line = "%s  %-8s %s%s" % (stamp, event.upper(), (symbol + "  ") if symbol else "", text)
        self.emit(line)
        d = os.path.dirname(self.log_path)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(self.log_path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    def broker_log(self, rec):
        doc = read_json(self.broker_path, None) or {"orders": []}
        doc.setdefault("orders", []).append(rec)
        write_json(self.broker_path, doc)

    # -- one pass

    def tick(self, now_utc):
        """One pass at now_utc. Returns {"done": bool, "why": str}."""
        et = entry.to_eastern(now_utc)
        day = et.date()
        today = day.isoformat()
        st = self.state(today)
        if hasattr(self.broker, "set_clock"):
            self.broker.set_clock(now_utc)
        ok, why = entry.trading_day(day)
        if not ok:
            return {"done": True, "why": "the NYSE is closed today (%s)" % why}
        o, c = entry.session(day)
        if et.time() < o:
            return {"done": False, "why": "before the open"}
        if os.path.exists(self.kill_path) and not st["halted"]:
            st["halted"] = "kill switch (%s)" % self.kill_path
            self.log(now_utc, "halt", "kill switch file found: no new entries, flattening")
        flat_by, _ = exitp.resolve_flat_by(day, None, self.cfg, None)
        flat_t = entry.parse_hhmm(flat_by, "flat_by")
        self.observe(now_utc, today)
        if et.time() >= flat_t or et.time() >= c:
            if not st["flattened"]:
                self.flatten(now_utc, "flat by %s" % flat_by)
            self.save()
            return {"done": st["flattened"], "why": "past flat_by %s" % flat_by}
        if not st["halted"]:
            self.loss_guard(now_utc, today)
        if st["halted"]:
            if not st["flattened"]:
                self.flatten(now_utc, st["halted"])
            self.save()
            return {"done": st["flattened"], "why": "halted: %s" % st["halted"]}
        if self.due(now_utc):
            self.scan(now_utc, day)
        self.save()
        return {"done": False, "why": "watching"}

    def bot_symbols(self):
        return [e["symbol"] for e in self.st["entered"]]

    def observe(self, now_utc, today):
        """Simulated fills need the bars; a real broker fills its own brackets."""
        if not isinstance(self.broker, SimBroker):
            return
        for sym in self.bot_symbols():
            if sym in self.broker.pos:
                try:
                    self.broker.observe(sym, self.charts(sym, entry.to_eastern(now_utc).date()), now_utc)
                except entry.EntryError as e:
                    self.log(now_utc, "warn", "no bars to mark the position: %s" % e, sym)

    def due(self, now_utc):
        last = self.st.get("last_scan")
        if last and (now_utc - parse_iso(last)).total_seconds() < float(self.b["scan_seconds"]) - 0.5:
            return False
        self.st["last_scan"] = iso_utc(now_utc)
        return True

    def day_pnl(self, today):
        """Realized + open P&L of today's bot symbols (they were flat at the start). None if unknown."""
        syms = set(self.bot_symbols())
        if not syms:
            return 0.0
        try:
            fills = [f for f in self.broker.fills(today) if f["symbol"] in syms]
            pos = self.broker.positions()
        except BrokerError:
            return None
        flow = sum((f["qty"] * f["price"]) * (1 if f["side"] == "sell" else -1) for f in fills)
        held = sum(p["market_value"] for s, p in pos.items() if s in syms)
        return round(flow + held, 2)

    def loss_guard(self, now_utc, today):
        pnl = self.day_pnl(today)
        self.st["pnl"] = pnl
        cap = float(self.cfg["max_daily_loss_usd"])
        if pnl is not None and pnl <= -cap:
            self.st["halted"] = "daily loss %s reached your max_daily_loss_usd %s" % (usd(pnl), size.money(cap))
            self.log(now_utc, "halt", self.st["halted"])

    def scan(self, now_utc, day):
        st = self.st
        n_entries = len(st["entered"])
        if n_entries >= int(self.cfg["max_trades_per_day"]):
            return
        try:
            held = self.broker.positions()
        except BrokerError as e:
            self.log(now_utc, "error", "could not read positions, no entries this pass: %s" % e)
            return
        open_bot = [s for s in self.bot_symbols() if s in held]
        if len(open_bot) >= int(self.b["max_open_positions"]):
            return
        first = True
        for sym in self.symbols:
            rec = st["symbols"][sym]
            if rec["status"] != "watching":
                continue
            if sym in held:
                rec.update(status="skipped", reason="already held in the account: the bot only trades fresh positions")
                self.log(now_utc, "skip", rec["reason"], sym)
                continue
            if not first:
                self.sleep(PAUSE)
            first = False
            try:
                chart = self.charts(sym, day)
                res = entry.evaluate(chart, now_utc, setup="orb", or_minutes=int(self.b["or_minutes"]), interval="5m",
                                     min_volume_x=self.b.get("min_volume_x"), cfg=self.cfg)
            except entry.EntryError as e:
                rec["errors"] = rec.get("errors", 0) + 1
                self.log(now_utc, "warn", "no chart: %s" % e, sym)
                if rec["errors"] >= 5:
                    rec.update(status="error", reason=str(e))
                continue
            if res["status"] == "expired":
                rec.update(status="expired", reason=res["reason"])
                self.log(now_utc, "expired", res["reason"], sym)
            elif res["status"] == "triggered":
                if isinstance(self.broker, SimBroker):
                    self.broker.observe(sym, chart, now_utc)
                if self.enter(now_utc, day, sym, res, chart, held):
                    return  # one entry per pass: the next pass re-reads the account
        return

    def enter(self, now_utc, day, sym, res, chart, held):
        rec = self.st["symbols"][sym]
        today = day.isoformat()
        trig, orr = res["trigger"], res["opening_range"]

        def skip(status, why):
            rec.update(status=status, reason=why)
            self.log(now_utc, "skip", why, sym)
            return False

        age = res.get("minutes_since") or 0
        if age > float(self.b["max_trigger_age_min"]):
            return skip("skipped", "triggered at %s, %d minutes ago: too late to chase (max_trigger_age_min %s)"
                        % (trig["time"], age, self.b["max_trigger_age_min"]))
        price, ptime = self.broker.last_price(sym, chart)
        if price is None:
            return skip("skipped", "no current price to size at")
        over = 100.0 * (price / trig["price"] - 1)
        if over > float(self.b["max_chase_pct"]):
            return skip("skipped", "last %s is %.2f%% over the trigger %s, more than max_chase_pct %s"
                        % (size._price(price), over, size._price(trig["price"]), self.b["max_chase_pct"]))
        # a market order fills near the last price, not the trigger: plan from the higher of the two,
        # so the target keeps its R multiple and the risk is not understated
        planned_at = max(trig["price"], size.round_cent(price))
        try:
            sr, sp = exitp.parse_stop(self.b["stop"])
            tr, tp = exitp.parse_target(self.b["target"])
            if sr == "atr":
                raise exitp.PlanError("the bot supports or-mid, or-low, pct:X and price:X stops, not atr")
            c = exitp.compute(planned_at, sr, sp, tr, tp, orr["high"], orr["low"])
            flat_by, _ = exitp.resolve_flat_by(day, None, self.cfg, entry.parse_hhmm(trig["close_time"], "trigger"))
        except exitp.PlanError as e:
            return skip("skipped", "no plan: %s" % e)
        plan = {"symbol": sym, "side": "buy", "date": today, "setup": "orb", "entry": float(c["entry"]),
                "entry_time": trig["time"], "stop": float(c["stop"]), "target": float(c["target"]),
                "stop_label": c["stop_label"], "target_label": c["target_label"], "flat_by": flat_by}
        try:
            acct = self.broker.account()
        except BrokerError as e:
            self.log(now_utc, "error", "could not read the account, no entry this pass: %s" % e, sym)
            return False
        if acct["blocked"] or (acct.get("status") and acct["status"] != "ACTIVE"):
            self.st["halted"] = "the broker account is %s%s" % (acct.get("status"), ", trading blocked" if acct["blocked"] else "")
            self.log(now_utc, "halt", self.st["halted"])
            return False
        pnl = self.day_pnl(today)
        if pnl is None:
            self.log(now_utc, "error", "could not read today's fills, so today's P&L is unknown: no entry this pass", sym)
            return False
        try:
            s = size.size_trade(plan, self.cfg, None, None, price, acct["cash"], today)
        except size.SizeError as e:
            return skip("skipped", "sizing: %s" % e)
        order = size.bracket_order(s)
        if not order:
            return skip("skipped", s["refusal"])
        portfolio = {"cash": acct["cash"], "positions": [{"ticker": t, "shares": p["qty"]} for t, p in held.items()]}
        prices = {t: p["price"] for t, p in held.items() if p["price"]}
        prices[sym] = price
        times = {t: now_utc for t in prices}
        times[sym] = ptime or now_utc
        verdict = risk.check([order], self.cfg, portfolio, prices, read_json(self.broker_path, {"orders": []}),
                             today, now_utc, times)["results"][0]
        if verdict["verdict"] != "PASS":
            return skip("blocked", "trader-risk BLOCK: " + "; ".join(verdict["reasons"]))
        fp = risk.fingerprint(order)
        cid = "tbot-%s-%s-%s" % (today.replace("-", ""), sym, fp[:8])
        try:
            placed = self.broker.submit(order, cid)
        except BrokerError as e:
            placed = None
            if e.sent is None:
                try:
                    placed = self.broker.order_by_client_id(cid)
                except BrokerError:
                    placed = None
                if placed is None:
                    rec.update(status="error", reason="submit failed and Alpaca has no order %s: %s" % (cid, e))
                    self.log(now_utc, "error", rec["reason"] + ". Check the broker before you restart.", sym)
                    self.st["halted"] = "an order's outcome was unknown (%s)" % sym
                    return False
            else:
                return skip("error", "the broker refused the order: %s" % e)
        oid = placed.get("id")
        self.st["entered"].append({"symbol": sym, "qty": int(order["qty"]), "entry": s["entry"], "stop": s["stop"],
                                   "target": s["target"], "max_loss": s["max_loss"], "order_id": oid,
                                   "client_order_id": cid, "at": iso_utc(now_utc)})
        rec.update(status="entered", order_id=oid)
        self.broker_log({"at": entry.to_eastern(now_utc).strftime("%Y-%m-%dT%H:%M:%S"), "date": today,
                         "fingerprint": fp, "symbol": sym, "side": "buy", "qty": order["qty"], "kind": "entry",
                         "max_loss": s["max_loss"], "stop": s["stop"], "status": "placed",
                         "plain": "Buy %s %s at market, stop-loss %s, take-profit %s" % (
                             order["qty"], sym, size._price(s["stop"]), size._price(s["target"])),
                         "broker_order_id": oid, "client_order_id": cid, "placed_by": "trader-bot",
                         "mode": self.mode})
        self.log(now_utc, "entry", "BUY %s at ~%s, stop %s, target %s, max loss %s (%s) [%s]" % (
            order["qty"], size._price(price), size._price(s["stop"]), size._price(s["target"]),
            size.money(s["max_loss"]), self.broker.name, oid), sym)
        return True

    def flatten(self, now_utc, why):
        """Cancel the bot's open orders and close the bot's positions. Never touches anything else."""
        syms = self.bot_symbols()
        clean = True
        try:
            held = self.broker.positions()
        except BrokerError as e:
            self.log(now_utc, "error", "flatten: could not read positions, will retry: %s" % e)
            return False
        for sym in syms:
            try:
                legs = self.broker.open_orders(sym)
                for o in legs:
                    self.broker.cancel(o["id"])
                if sym in held:
                    if legs:
                        self.sleep(CANCEL_WAIT)
                    r = self.broker.close(sym)
                    qty = held[sym]["qty"]
                    self.broker_log({"at": entry.to_eastern(now_utc).strftime("%Y-%m-%dT%H:%M:%S"),
                                     "date": self.st["date"], "symbol": sym, "side": "sell", "qty": "%g" % qty,
                                     "kind": "exit", "status": "placed", "plain": "Sell %g %s at market (flatten)" % (qty, sym),
                                     "broker_order_id": r.get("id"), "placed_by": "trader-bot", "mode": self.mode})
                    self.log(now_utc, "exit", "SELL %g at market: %s" % (qty, why), sym)
            except BrokerError as e:
                clean = False
                self.log(now_utc, "error", "flatten failed, will retry: %s" % e, sym)
        if clean:
            self.st["flattened"] = True
            pnl = self.day_pnl(self.st["date"])
            self.st["pnl"] = pnl
            self.log(now_utc, "flat", "%s. Today's bot P&L: %s" % (
                why, usd(pnl) if pnl is not None else "unknown (could not read fills)"))
        return clean

    # -- the loop

    def next_wake(self, now_utc):
        et = entry.to_eastern(now_utc)
        o, _ = entry.session(et.date()) or (entry.OPEN, entry.CLOSE)
        open_dt = datetime.datetime.combine(et.date(), o)
        if et < open_dt:
            return min(max((open_dt - et).total_seconds(), 1.0), 300.0)
        return float(self.b["poll_seconds"])

    def run(self, clock, sleep=None, max_ticks=None):
        sleep = sleep or self.sleep
        n = 0
        while True:
            now = clock()
            out = self.tick(now)
            n += 1
            if out["done"]:
                self.log(now, "done", out["why"])
                return 0
            if max_ticks and n >= max_ticks:
                return 0
            sleep(self.next_wake(now))


# ---------------------------------------------------------------- commands

def report(bot, now_utc):
    st = bot.state(entry.to_eastern(now_utc).date().isoformat())
    lines = ["trader-bot, %s, %s (%s)" % (st["date"], bot.broker.name, bot.mode)]
    if st["halted"]:
        lines.append("HALTED: %s" % st["halted"])
    for s in bot.symbols:
        r = st["symbols"].get(s, {})
        lines.append("  %-6s %-9s %s" % (s, r.get("status", "?"), r.get("reason", "")))
    for e in st["entered"]:
        lines.append("  entered %s x%d at ~%s, stop %s, target %s, max loss %s" % (
            e["symbol"], e["qty"], size._price(e["entry"]), size._price(e["stop"]), size._price(e["target"]),
            size.money(e["max_loss"])))
    pnl = bot.day_pnl(st["date"])
    lines.append("Today's bot P&L: %s. Flattened: %s." % (usd(pnl) if pnl is not None else "unknown",
                                                           "yes" if st["flattened"] else "no"))
    lines.append("Not financial advice. Most people who day trade lose money.")
    return "\n".join(lines)


def build(a, env=None, charts=None):
    env = os.environ if env is None else env
    cfg = load_config(a.config)
    mode = mode_of(cfg, getattr(a, "live", False), getattr(a, "dry_run", False) or getattr(a, "offline", None), env)
    syms, src = symbols_from(a, cfg)
    if mode == "dry-run":
        broker = SimBroker(cfg["budget"])
    else:
        key, secret = keys_from(env)
        broker = Alpaca(key, secret, live=(mode == "live"))
    if getattr(a, "offline", None):
        off, cache = a.offline, {}

        def offline_chart(sym, day):
            if sym not in cache:
                cache[sym] = entry.get_chart(sym, "5m", offline=off)
            return cache[sym]
        charts = charts or offline_chart
    bot = Bot(cfg, broker, syms, mode, charts=charts, state_dir=a.state_dir, kill_path=a.kill)
    return bot, src


def banner(bot, src):
    c = bot.cfg
    bot.emit("trader-bot: %s. Watching %d symbols from %s: %s" % (
        bot.broker.name, len(bot.symbols), src, ", ".join(bot.symbols)))
    bot.emit("Rules: ORB %d min, stop %s, target %s, max loss %s a trade, %s a day, %s trades a day, flat by %s. "
             "Kill switch: touch %s" % (bot.b["or_minutes"], bot.b["stop"], bot.b["target"],
                                       size.money(c["max_loss_per_trade_usd"]), size.money(c["max_daily_loss_usd"]),
                                       c["max_trades_per_day"], c["flat_by"], bot.kill_path))
    if bot.mode == "live":
        bot.emit("LIVE ACCOUNT: these orders use real money. Not financial advice.")


def cmd_run(a, once=False):
    bot, src = build(a)
    if bot.mode != "dry-run":
        acct = bot.broker.account()
        if acct["blocked"] or acct.get("status") != "ACTIVE":
            raise BotError("the %s account is %s%s: not starting" % (bot.broker.name, acct.get("status"),
                                                                    ", trading blocked" if acct["blocked"] else ""))
        bot.emit("Account %s: cash %s, equity %s." % (acct.get("number"), size.money(acct["cash"]),
                                                     size.money(acct["equity"])))
    banner(bot, src)
    clock = (lambda: entry.parse_now(a.now)) if a.now else entry.utc_now
    if once:
        out = bot.tick(clock())
        bot.emit(out["why"])
        return 0
    return bot.run(clock)


def cmd_replay(a):
    if not a.offline:
        raise BotError("replay needs --offline DIR (a folder of <TICKER>.json 5-minute charts)")
    a.dry_run = True
    bot, src = build(a)
    day = datetime.date.fromisoformat(a.day)
    ok, why = entry.trading_day(day)
    if not ok:
        raise BotError("%s is not a trading day (%s)" % (a.day, why))
    banner(bot, src)
    t = entry.eastern_to_utc(datetime.datetime.combine(day, entry.parse_hhmm(a.start, "--from")))
    end = entry.eastern_to_utc(datetime.datetime.combine(day, entry.session(day)[1]))
    step = datetime.timedelta(seconds=float(bot.b["poll_seconds"]))
    while t <= end:
        if bot.tick(t)["done"]:
            break
        t += step
    for f in bot.broker.fill_log:
        bot.emit("  fill %s %-4s %g %s at %s (%s)" % (f["day"], f["side"], f["qty"], f["symbol"],
                                                    size._price(f["price"]), f["why"]))
    bot.emit(report(bot, t))
    return 0


def cmd_status(a):
    a.dry_run = getattr(a, "dry_run", False)
    bot, _ = build(a)
    now = entry.parse_now(a.now) if a.now else entry.utc_now()
    print(report(bot, now))
    if bot.mode != "dry-run":
        for s, p in sorted(bot.broker.positions().items()):
            print("  position %-6s %g at %s" % (s, p["qty"], size._price(p["price"])))
    print("Kill switch: %s" % ("ON (%s)" % a.kill if os.path.exists(a.kill) else "off"))
    return 0


def cmd_flatten(a):
    bot, _ = build(a)
    now = entry.parse_now(a.now) if a.now else entry.utc_now()
    bot.state(entry.to_eastern(now).date().isoformat())
    ok = bot.flatten(now, "flatten by hand")
    bot.save()
    return 0 if ok else 1


def cmd_stop(a):
    d = os.path.dirname(a.kill)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(a.kill, "w", encoding="utf-8") as fh:
        fh.write("stopped %s\n" % iso_utc(entry.utc_now()))
    print("Kill switch ON (%s). A running bot stops new entries, flattens its positions and exits on its next "
          "pass (within poll_seconds). Run `bot.py resume` to clear it." % a.kill)
    return 0


def cmd_resume(a):
    if os.path.exists(a.kill):
        os.remove(a.kill)
        print("Kill switch cleared. A bot halted today stays halted until tomorrow; start it again to trade.")
    else:
        print("No kill switch at %s." % a.kill)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="ORB trading bot. Paper by default; live needs --live, "
                                             "\"paper\": false and %s=%s." % (LIVE_ENV, LIVE_WORD))
    sub = ap.add_subparsers(dest="cmd")

    def common(p, trade=True):
        p.add_argument("--config", default="trader/config.json")
        p.add_argument("--state-dir", default="trader/state")
        p.add_argument("--kill", default="trader/STOP", help="the kill-switch file")
        if trade:
            p.add_argument("--symbols", help="comma-separated tickers (overrides the config and the watchlists)")
            p.add_argument("--watchlist", help="a watchlist.json, shortlist.json or text file of tickers")
            p.add_argument("--live", action="store_true", help="trade the LIVE account (see the top of this file)")
            p.add_argument("--dry-run", action="store_true", help="simulated fills, no broker, no keys")
            p.add_argument("--now", help="pin the clock, New York time (one pass only makes sense with once)")
            p.add_argument("--offline", help="read <TICKER>.json charts from this folder (implies --dry-run)")

    for name in ("run", "once", "status", "flatten"):
        common(sub.add_parser(name))
    p = sub.add_parser("replay")
    common(p)
    p.add_argument("--day", required=True, help="YYYY-MM-DD")
    p.add_argument("--from", dest="start", default="09:30")
    for name in ("stop", "resume"):
        common(sub.add_parser(name), trade=False)
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 2
    try:
        return {"run": cmd_run, "once": lambda x: cmd_run(x, once=True), "status": cmd_status,
                "flatten": cmd_flatten, "replay": cmd_replay, "stop": cmd_stop, "resume": cmd_resume}[a.cmd](a)
    except (BotError, BrokerError, entry.EntryError, exitp.PlanError, size.SizeError, risk.RiskError) as e:
        print("trader-bot: %s" % e, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\ntrader-bot: interrupted. Open positions keep their stop-loss and take-profit at the broker; "
              "run `bot.py flatten` to close them now.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
