#!/usr/bin/env python3
"""Robinhood mode for trader-bot. Standard library only, no network.

Robinhood is reachable only through Claude's Robinhood connector, not from a
program, so in this mode Claude is the loop and this file is the brain. Every
pass (every 5 minutes, just after a bar closes):

  1. Claude reads Robinhood: get_portfolio, get_equity_positions, today's
     agent-initiated get_equity_orders, 5-minute get_equity_historicals and
     get_equity_quotes for the watchlist, and writes them, raw, into one
     snapshot file.
  2. python3 robinhood.py pass --snapshot trader/rh/snapshot.json
     prints the actions for this pass as JSON, nothing else is decided.
  3. Claude carries out exactly those actions with review_equity_order and
     place_equity_order / cancel_equity_order, in order, and nothing else.

It keeps no state of its own: what it entered, what filled, what was lost
today all come from the account's own orders, every pass. A restarted
container picks up where the last pass left off.

The rule is trader-bot's, through the same code (entry.evaluate,
exit.compute, size.size_trade, check.check):
  entry   first 5-minute close above the 15-minute opening range, at most
          max_trigger_age_min (6) old, price at most max_chase_pct (0.5 %) over it;
          a limit buy at the ask (a marketable limit), cancelled if not filled
          by the next pass
  stop    a stop_market sell at the range midpoint, placed as soon as the buy
          fills (Robinhood has no bracket orders)
  target  1R; the pass sells at market when the last price reaches it
  flat    every bot position is sold at market at flat_by (15:55)
Limits (the "bot" block of the config, or these defaults):
  max_loss_pct_per_trade  1      % of the account value, per trade, at the stop
  max_trades_per_day      4      entries
  max_losing_trades       2      losing round trips, then no more entries today
  backstop                today's bot P&L at or under -max_losing_trades x the
                          max loss (a gap through a stop): sell everything it holds
  kill switch             trader/STOP: sell everything it holds, no entries

    python3 robinhood.py pass --snapshot trader/rh/snapshot.json
    python3 robinhood.py pass --snapshot snap.json --now "2026-10-28 10:16"

Only symbols the bot bought today are ever sold. Not financial advice.
"""

import argparse
import datetime
import json
import math
import os
import sys
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bot  # noqa: E402

entry, exitp, size, risk = bot.entry, bot.exitp, bot.size, bot.risk

RH_DEFAULTS = {
    "max_loss_pct_per_trade": 1.0,
    "max_trades_per_day": 4,
    "max_losing_trades": 2,
    "max_trigger_age_min": 6,
    "max_chase_pct": 0.5,
    "max_open_positions": 1,
    "entry_timeout_sec": 120,
    "or_minutes": 15,
    "stop": "or-mid",
    "target": "1R",
    "no_entries_before": "09:45",
    "no_entries_after": "15:30",
    "flat_by": "15:55",
    "cash_buffer_pct": 10,
    "max_position_pct": 75,
}
OPEN_STATES = {"new", "queued", "confirmed", "unconfirmed", "partially_filled", "pending"}
NS = uuid.UUID("6f1d8a52-3c41-4f0e-9a8e-6b7f3d2c1a00")


class RHError(Exception):
    pass


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _data(doc):
    """A raw tool result is {"data": {...}, "guide": ...}; accept it or its data."""
    if isinstance(doc, dict) and "data" in doc and isinstance(doc["data"], (dict, list)):
        return doc["data"]
    return doc


def ref_id(day, sym, what):
    """The same order on a retry gets the same id (Robinhood dedupes on ref_id)."""
    return str(uuid.uuid5(NS, "%s|%s|%s" % (day, sym, what)))


# ---------------------------------------------------------------- the snapshot

def norm_orders(doc):
    rows = _data(doc) or []
    if isinstance(rows, dict):
        rows = rows.get("orders") or rows.get("results") or []
    out = []
    for o in rows:
        sym = str(o.get("symbol") or o.get("ticker") or "").upper()
        if not sym:
            continue
        execs = o.get("executions") or []
        filled = _f(o.get("filled_quantity") or o.get("cumulative_quantity"))
        if filled is None:
            filled = sum(_f(e.get("quantity")) or 0 for e in execs)
        avg = _f(o.get("average_price") or o.get("average_fill_price"))
        if avg is None and execs and filled:
            avg = sum((_f(e.get("quantity")) or 0) * (_f(e.get("price")) or 0) for e in execs) / filled
        typ = str(o.get("type") or "").lower()
        trig = str(o.get("trigger") or "").lower()
        if trig == "stop" and typ in ("market", "limit"):
            typ = "stop_" + typ
        out.append({"id": o.get("id") or o.get("order_id"), "symbol": sym, "side": str(o.get("side") or "").lower(),
                    "type": typ, "state": str(o.get("state") or o.get("status") or "").lower(),
                    "quantity": _f(o.get("quantity")) or 0.0, "filled": filled or 0.0, "avg_price": avg,
                    "stop_price": _f(o.get("stop_price")), "limit_price": _f(o.get("price") or o.get("limit_price")),
                    "created_at": o.get("created_at"), "ref_id": o.get("ref_id")})
    return out


def norm_positions(doc):
    rows = _data(doc) or []
    if isinstance(rows, dict):
        rows = rows.get("positions") or rows.get("results") or []
    out = {}
    for p in rows:
        sym = str(p.get("symbol") or p.get("ticker") or "").upper()
        q = _f(p.get("quantity"))
        if sym and q:
            out[sym] = {"qty": q, "available": _f(p.get("shares_available_for_sells")) or 0.0,
                        "avg": _f(p.get("average_buy_price"))}
    return out


def norm_quotes(doc):
    d = _data(doc) or {}
    rows = d.get("quotes") or d.get("results") or [] if isinstance(d, dict) else d
    out = {}
    for q in rows:
        q = q.get("quote") or q
        sym = str(q.get("symbol") or "").upper()
        if not sym:
            continue
        last = _f(q.get("last_trade_price") or q.get("last_price") or q.get("price"))
        out[sym] = {"last": last, "ask": _f(q.get("ask_price")) or last, "bid": _f(q.get("bid_price")) or last,
                    "time": q.get("updated_at") or q.get("venue_last_trade_time")}
    return out


def chart_from_bars(sym, bars, now_utc):
    """Robinhood 5-minute bars -> the chart shape entry.evaluate reads."""
    out = []
    for b in bars or []:
        if b.get("interpolated"):
            continue
        t = bot.parse_iso(b["begins_at"])
        c = _f(b.get("close_price"))
        if c is None:
            continue
        out.append({"t": entry.ts_of(t), "o": _f(b.get("open_price")), "h": _f(b.get("high_price")),
                    "l": _f(b.get("low_price")), "c": c, "v": int(b.get("volume") or 0)})
    out.sort(key=lambda b: b["t"])
    return {"symbol": sym, "bars": out, "meta": {"dataGranularity": "5m"}, "prev_close": None,
            "data_time": entry.ts_of(now_utc)}


def norm_bars(doc):
    d = _data(doc) or {}
    rows = d.get("results") or [] if isinstance(d, dict) else d
    out = {}
    for r in rows:
        out.setdefault(str(r.get("symbol") or "").upper(), []).extend(r.get("bars") or [])
    return out


def load_snapshot(path):
    try:
        with open(path, encoding="utf-8") as fh:
            snap = json.load(fh)
    except (OSError, ValueError) as e:
        raise RHError("could not read the snapshot %s: %s" % (path, e))
    pf = _data(snap.get("portfolio")) or {}
    bars = {}
    for part in snap.get("historicals") or []:
        for k, v in norm_bars(part).items():
            bars.setdefault(k, []).extend(v)
    for k, rows in (snap.get("bars") or {}).items():   # compact: [begins_at, o, h, l, c, v]
        bars.setdefault(k.upper(), []).extend(
            {"begins_at": r[0], "open_price": r[1], "high_price": r[2], "low_price": r[3], "close_price": r[4],
             "volume": r[5]} for r in rows)
    quotes = {}
    for part in snap.get("quotes") if isinstance(snap.get("quotes"), list) else [snap.get("quotes") or {}]:
        quotes.update(norm_quotes(part))
    return {"account": snap.get("account"), "total_value": _f(pf.get("total_value")),
            "buying_power": _f((pf.get("buying_power") or {}).get("buying_power")) if isinstance(pf.get("buying_power"), dict)
            else _f(pf.get("buying_power")),
            "positions": norm_positions(snap.get("positions")), "orders": norm_orders(snap.get("orders")),
            "bars": bars, "quotes": quotes, "watch": [str(s).upper() for s in snap.get("watch") or []],
            "now": snap.get("now")}


# ---------------------------------------------------------------- the pass

def merge_cache(snap, path, day):
    """Today's bars: the cache plus the snapshot's, the snapshot winning on the same begins_at."""
    cache = {}
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
        if doc.get("date") == day:
            cache = doc.get("bars") or {}
    except (OSError, ValueError):
        pass
    for sym, rows in snap["bars"].items():
        by = {b["begins_at"]: b for b in cache.get(sym, [])}
        for b in rows:
            by[b["begins_at"]] = b
        cache[sym] = [by[k] for k in sorted(by)]
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path + ".tmp", "w", encoding="utf-8") as fh:
        json.dump({"date": day, "bars": cache}, fh)
    os.replace(path + ".tmp", path)
    snap["bars"] = cache
    return snap


def limits(cfg_bot, total_value):
    c = dict(RH_DEFAULTS)
    c.update(cfg_bot or {})
    if not total_value or total_value <= 0:
        raise RHError("the snapshot has no account value (get_portfolio total_value)")
    max_loss = math.floor(total_value * float(c["max_loss_pct_per_trade"])) / 100.0
    cfg = dict(size.DEFAULTS)
    cfg.update({"budget": total_value, "max_loss_per_trade_usd": max_loss,
                "max_daily_loss_usd": round(max_loss * int(c["max_trades_per_day"]), 2),
                "max_trades_per_day": int(c["max_trades_per_day"]), "max_orders_per_day": 4 * int(c["max_trades_per_day"]) + 4,
                "no_entries_before": c["no_entries_before"], "no_entries_after": c["no_entries_after"],
                "flat_by": c["flat_by"], "cash_buffer_pct": c["cash_buffer_pct"],
                "max_position_pct": c["max_position_pct"], "max_order_usd": total_value, "paper": False})
    return c, cfg


def ledger(orders, day):
    """Per symbol, today's bot orders: filled buys and sells, open orders."""
    led = {}
    for o in orders:
        when = o.get("created_at")
        if not when or entry.to_eastern(bot.parse_iso(when)).date().isoformat() != day:
            continue
        L = led.setdefault(o["symbol"], {"bought": 0.0, "cost": 0.0, "sold": 0.0, "proceeds": 0.0, "open": [],
                                         "buys": 0})
        if o["side"] == "buy":
            L["buys"] += 1
        if o["filled"] and o["avg_price"]:
            if o["side"] == "buy":
                L["bought"] += o["filled"]
                L["cost"] += o["filled"] * o["avg_price"]
            else:
                L["sold"] += o["filled"]
                L["proceeds"] += o["filled"] * o["avg_price"]
        if o["state"] in OPEN_STATES:
            L["open"].append(o)
    return led


def or_stop(chart, now_utc, c):
    """The plan's stop from the opening range, and the range itself."""
    res = entry.evaluate(chart, now_utc, setup="orb", or_minutes=int(c["or_minutes"]), interval="5m")
    orr = res.get("opening_range")
    if not orr:
        return None, res
    sr, sp = exitp.parse_stop(c["stop"])
    raw = (exitp.cents(orr["high"]) + exitp.cents(orr["low"])) / 2 if sr == "or_mid" else exitp.cents(orr["low"])
    return float(exitp.floor_cents(raw)), res


def decide(snap, cfg_bot, now_utc, kill=False):
    """Pure function: the snapshot -> {"actions": [...], "status": {...}, "done": bool}."""
    c, cfg = limits(cfg_bot, snap["total_value"])
    et = entry.to_eastern(now_utc)
    day = et.date()
    today = day.isoformat()
    acts, notes = [], []
    status = {"now_new_york": et.strftime("%Y-%m-%d %H:%M:%S"), "account_value": snap["total_value"],
              "max_loss_per_trade": cfg["max_loss_per_trade_usd"], "notes": notes}
    ok, why = entry.trading_day(day)
    if not ok:
        return {"actions": [], "status": dict(status, state="closed", reason=why), "done": True}
    o, close = entry.session(day)
    if et.time() < o:
        return {"actions": [], "status": dict(status, state="pre-open"), "done": False}
    flat_by, _ = exitp.resolve_flat_by(day, None, cfg, None)
    flat_t = entry.parse_hhmm(flat_by, "flat_by")

    led = ledger(snap["orders"], today)
    pos = snap["positions"]
    quotes = snap["quotes"]
    charts = {s: chart_from_bars(s, b, now_utc) for s, b in snap["bars"].items()}
    trades = sum(1 for L in led.values() if L["bought"] > 0)
    pnl, losses, rows = 0.0, 0, []
    for sym, L in sorted(led.items()):
        held = (pos.get(sym) or {}).get("qty", 0.0) if L["bought"] else 0.0
        last = (quotes.get(sym) or {}).get("last")
        p = L["proceeds"] - L["cost"] + (held * last if held and last else 0.0)
        closed = L["bought"] > 0 and not held and not any(x["side"] == "buy" for x in L["open"])
        if closed and p < -0.005:
            losses += 1
        pnl += p
        rows.append({"symbol": sym, "bought": L["bought"], "sold": L["sold"], "held": held, "pnl": round(p, 2),
                     "closed": closed})
    pnl = round(pnl, 2)
    backstop = -int(c["max_losing_trades"]) * cfg["max_loss_per_trade_usd"]
    status.update(trades=trades, losing_trades=losses, pnl=pnl, positions=rows)

    flatten_why = None
    if kill:
        flatten_why = "kill switch"
    elif et.time() >= flat_t or et.time() >= close:
        flatten_why = "flat by %s" % flat_by
    elif pnl <= backstop + 0.005:
        flatten_why = "today's bot P&L %s is at the backstop %s" % (bot.usd(pnl), bot.usd(backstop))

    # 1. what the bot holds: stops, targets, the flatten
    open_bot = []
    for sym, L in sorted(led.items()):
        for x in L["open"]:
            if x["side"] == "buy" and x["filled"] < x["quantity"]:
                age = (now_utc - bot.parse_iso(x["created_at"])).total_seconds()
                if flatten_why or age >= float(c["entry_timeout_sec"]):
                    acts.append({"action": "cancel", "symbol": sym, "order_id": x["id"],
                                 "why": "entry not filled after %d s" % age if not flatten_why else flatten_why})
        held = (pos.get(sym) or {}).get("qty", 0.0) if L["bought"] else 0.0
        if not held:
            for x in L["open"]:
                if x["side"] == "sell":
                    acts.append({"action": "cancel", "symbol": sym, "order_id": x["id"], "why": "no shares left"})
            continue
        open_bot.append(sym)
        qty = int(held)
        fill = L["cost"] / L["bought"]
        sells = [x for x in L["open"] if x["side"] == "sell"]
        stop_orders = [x for x in sells if x["type"].startswith("stop")]
        stop = stop_orders[0]["stop_price"] if stop_orders else None
        if stop is None and sym in charts:
            stop, _ = or_stop(charts[sym], now_utc, c)
        if stop is None or stop >= fill:
            stop = float(exitp.floor_cents(exitp.cents(fill) * (1 - exitp.D("0.01"))))
            notes.append("%s: no opening-range stop below the fill, using 1%% under it (%.2f)" % (sym, stop))
        tr, tp = exitp.parse_target(c["target"])
        target = round(fill + float(tp) * (fill - stop), 2) if tr == "r" else float(tp)
        last = (quotes.get(sym) or {}).get("last")
        exit_why = flatten_why
        if not exit_why and last is not None and last >= target:
            exit_why = "target %.2f reached (last %.2f)" % (target, last)
        if not exit_why and last is not None and last <= stop and not stop_orders:
            exit_why = "at or under the stop %.2f with no stop order (last %.2f)" % (stop, last)
        if exit_why:
            for x in sells:
                acts.append({"action": "cancel", "symbol": sym, "order_id": x["id"], "why": "before the exit"})
            n = sum(1 for x in snap["orders"] if x["symbol"] == sym and x["side"] == "sell" and x["type"] == "market")
            acts.append({"action": "sell", "symbol": sym, "type": "market", "quantity": str(qty),
                         "time_in_force": "gfd", "ref_id": ref_id(today, sym, "exit-%d" % n), "why": exit_why})
        elif not stop_orders or abs(stop_orders[0]["quantity"] - qty) > 1e-9:
            for x in stop_orders:
                acts.append({"action": "cancel", "symbol": sym, "order_id": x["id"], "why": "replace the stop"})
            acts.append({"action": "sell", "symbol": sym, "type": "stop_market", "quantity": str(qty),
                         "stop_price": "%.2f" % stop, "time_in_force": "gfd",
                         "ref_id": ref_id(today, sym, "stop-%d" % qty), "why": "protective stop, target %.2f" % target})
        status.setdefault("open", []).append({"symbol": sym, "qty": qty, "fill": round(fill, 2), "stop": stop,
                                              "target": target, "last": last})

    if flatten_why:
        done = not open_bot and (kill or et.time() >= flat_t or et.time() >= close)
        return {"actions": acts, "status": dict(status, state="flatten", reason=flatten_why), "done": done}

    # 2. a new entry, at most one a pass
    stop_why = None
    if losses >= int(c["max_losing_trades"]):
        stop_why = "%d losing trades today: no more entries (max_losing_trades %s)" % (losses, c["max_losing_trades"])
    elif trades >= int(c["max_trades_per_day"]):
        stop_why = "%d trades today, the max" % trades
    elif len(open_bot) >= int(c["max_open_positions"]):
        stop_why = "a position is open"
    elif any(x["side"] == "buy" for L in led.values() for x in L["open"]):
        stop_why = "an entry order is still working"
    if stop_why:
        return {"actions": acts, "status": dict(status, state="holding" if open_bot else "no-entries", reason=stop_why),
                "done": False}

    watched = []
    for sym in snap["watch"]:
        if sym in led:
            watched.append({"symbol": sym, "status": "traded today"})
            continue
        if sym in pos:
            watched.append({"symbol": sym, "status": "already held (not the bot's)"})
            continue
        ch = charts.get(sym)
        if not ch or not ch["bars"]:
            watched.append({"symbol": sym, "status": "no bars in the snapshot"})
            continue
        try:
            res = entry.evaluate(ch, now_utc, setup="orb", or_minutes=int(c["or_minutes"]), interval="5m", cfg=cfg)
        except entry.EntryError as e:
            watched.append({"symbol": sym, "status": "error: %s" % e})
            continue
        row = {"symbol": sym, "status": res["status"], "reason": res.get("reason")}
        watched.append(row)
        if res["status"] != "triggered":
            continue
        trig, orr = res["trigger"], res["opening_range"]
        age = res.get("minutes_since") or 0
        if age > float(c["max_trigger_age_min"]):
            row["status"] = "stale trigger (%s, %d min ago)" % (trig["time"], age)
            continue
        q = quotes.get(sym) or {}
        ask = q.get("ask") or q.get("last")
        if not ask:
            row["status"] = "triggered, but no quote"
            continue
        over = 100.0 * (ask / trig["price"] - 1)
        if over > float(c["max_chase_pct"]):
            row["status"] = "triggered, ask %.2f is %.2f%% over the trigger %.2f: not chased" % (ask, over, trig["price"])
            continue
        limit = math.ceil(max(ask, trig["price"]) * 100 - 1e-6) / 100.0
        try:
            cc = exitp.compute(limit, *(exitp.parse_stop(c["stop"]) + exitp.parse_target(c["target"]) +
                                        (orr["high"], orr["low"])))
        except exitp.PlanError as e:
            row["status"] = "no plan: %s" % e
            continue
        plan = {"symbol": sym, "side": "buy", "date": today, "setup": "orb", "entry": float(cc["entry"]),
                "entry_time": trig["time"], "stop": float(cc["stop"]), "target": float(cc["target"]),
                "flat_by": flat_by}
        try:
            s = size.size_trade(plan, cfg, None, limit, None, snap["buying_power"], today)
        except size.SizeError as e:
            row["status"] = "sizing: %s" % e
            continue
        order = size.bracket_order(s)
        if not order:
            row["status"] = "sizing: %s" % s["refusal"]
            continue
        state = {"orders": [{"date": today, "status": "placed", "symbol": x["symbol"], "side": x["side"],
                             "kind": "entry" if x["side"] == "buy" else "exit",
                             "max_loss": cfg["max_loss_per_trade_usd"] if x["side"] == "buy" else None}
                            for x in snap["orders"] if x["filled"] or x["state"] in OPEN_STATES]}
        v = risk.check([order], cfg, {"cash": snap["buying_power"], "positions": []}, {sym: limit}, state, today,
                       now_utc, {sym: now_utc})["results"][0]
        if v["verdict"] != "PASS":
            row["status"] = "trader-risk BLOCK: " + "; ".join(v["reasons"])
            continue
        acts.append({"action": "buy", "symbol": sym, "type": "limit", "quantity": order["qty"],
                     "limit_price": "%.2f" % limit, "time_in_force": "gfd", "ref_id": ref_id(today, sym, "entry"),
                     "stop": s["stop"], "target": s["target"], "max_loss": s["max_loss"],
                     "why": "ORB: the %s bar closed at %.2f over the range high %.2f; %s shares, max loss %s at %.2f"
                            % (trig["bar_start"], trig["price"], orr["high"], order["qty"], bot.usd(s["max_loss"]),
                               s["stop"])})
        row["status"] = "ENTRY"
        break
    status["watch"] = watched
    return {"actions": acts, "status": dict(status, state="watching"), "done": False}


def main(argv=None):
    ap = argparse.ArgumentParser(description="trader-bot, Robinhood mode: one pass, actions as JSON")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("pass")
    p.add_argument("--snapshot", default="trader/rh/snapshot.json")
    p.add_argument("--config", default="trader/config.json")
    p.add_argument("--kill", default="trader/STOP")
    p.add_argument("--cache", default="trader/rh/bars.json", help="today's bars so far; a pass may send only new ones")
    p.add_argument("--now", help="pin the clock, New York time (default: the snapshot's now, else the real clock)")
    a = ap.parse_args(argv)
    if a.cmd != "pass":
        ap.print_help()
        return 2
    try:
        snap = load_snapshot(a.snapshot)
        cfg_bot = (bot.read_json(a.config, {}) or {}).get("robinhood") or {}
        if a.now:
            now = entry.parse_now(a.now)
        elif snap.get("now"):
            now = bot.parse_iso(snap["now"])
        else:
            now = entry.utc_now()
        merge_cache(snap, a.cache, entry.to_eastern(now).date().isoformat())
        out = decide(snap, cfg_bot, now, kill=os.path.exists(a.kill))
    except (RHError, bot.BotError, entry.EntryError, exitp.PlanError, size.SizeError, risk.RiskError) as e:
        print(json.dumps({"error": str(e), "actions": []}))
        return 1
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
