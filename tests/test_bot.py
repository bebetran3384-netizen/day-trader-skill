"""trader-bot tests. Offline: the reel's TSLA Oct 19 2026 five-minute bars
(tests/fixtures/trader-entry/TSLA-2026-10-19-5m.json), the simulated broker,
and a fake Alpaca opener. Nothing here touches the network."""

import copy
import datetime
import io
import json
import os
import shutil
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stderr, redirect_stdout

from _load import load, FIX, ROOT

bot = load("trader-bot", "bot")
entry = bot.entry

TSLA = os.path.join(FIX, "trader-entry", "TSLA-2026-10-19-5m.json")
CONFIG = os.path.join(ROOT, "trader-budget", "config.example.json")
DAY = "2026-10-19"


def ny(text):
    return entry.parse_now(text)


def tsla_doc():
    with open(TSLA) as fh:
        return json.load(fh)


def chart_of(doc, sym="TSLA"):
    return entry.parse_chart(json.dumps(doc), sym)


def cfg(**over):
    with open(CONFIG) as fh:
        c = json.load(fh)
    c.update(over)
    return c


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.lines = []

    def make(self, config=None, symbols=("TSLA",), broker=None, charts=None, doc=None):
        path = os.path.join(self.tmp, "config.json")
        with open(path, "w") as fh:
            json.dump(config or cfg(), fh)
        c = bot.load_config(path)
        ch = chart_of(doc or tsla_doc())
        b = broker or bot.SimBroker(c["budget"])
        return bot.Bot(c, b, list(symbols), "dry-run", charts=charts or (lambda s, d: ch),
                       state_dir=os.path.join(self.tmp, "state"), kill_path=os.path.join(self.tmp, "STOP"),
                       emit=self.lines.append, sleep=lambda s: None)

    def walk(self, b, start, end, step=20):
        t, stop = ny(DAY + " " + start), ny(DAY + " " + end)
        out = None
        while t <= stop:
            out = b.tick(t)
            if out["done"]:
                break
            t += datetime.timedelta(seconds=step)
        return out

    def broker_log(self):
        with open(os.path.join(self.tmp, "state", "broker.json")) as fh:
            return json.load(fh)["orders"]


class TheReelTrade(Base):
    def test_enters_the_readme_trade_at_10_15(self):
        b = self.make()
        self.walk(b, "09:30", "10:20")
        self.assertEqual(len(b.st["entered"]), 1)
        e = b.st["entered"][0]
        self.assertEqual((e["symbol"], e["qty"], e["entry"], e["stop"], e["target"], e["max_loss"]),
                         ("TSLA", 8, 364.18, 358.32, 370.04, 46.88))
        self.assertEqual(e["at"], "2026-10-19T14:15:00Z")   # 10:15 New York, the trigger bar's close
        rec = self.broker_log()[0]
        self.assertEqual((rec["status"], rec["kind"], rec["side"], rec["qty"], rec["placed_by"]),
                         ("placed", "entry", "buy", "8", "trader-bot"))
        self.assertTrue(rec["client_order_id"].startswith("tbot-20261019-TSLA-"))

    def test_the_order_passes_trader_risk_and_is_logged_for_it(self):
        b = self.make()
        self.walk(b, "09:30", "10:20")
        # trader-risk now sees the placed entry: the same buy again is a duplicate
        state = {"orders": self.broker_log()}
        order = {"symbol": "TSLA", "side": "buy", "qty": "8", "type": "market", "time_in_force": "day",
                 "order_class": "bracket", "take_profit_limit_price": "370.04", "stop_loss_stop_price": "358.32",
                 "plan": {"entry": 364.18, "stop": 358.32, "target": 370.04, "max_loss": 46.88}}
        res = bot.risk.check([order], b.cfg, None, {"TSLA": 364.18}, state, DAY, ny(DAY + " 10:21"))
        self.assertEqual(res["results"][0]["verdict"], "BLOCK")
        self.assertTrue(any("duplicate" in r for r in res["results"][0]["reasons"]), res["results"][0]["reasons"])

    def test_one_entry_per_symbol_a_day(self):
        b = self.make()
        self.walk(b, "09:30", "10:45")
        self.assertEqual(len(b.st["entered"]), 1)
        self.assertEqual(len([r for r in self.broker_log() if r["kind"] == "entry"]), 1)

    def test_flattens_by_flat_by_and_is_done(self):
        b = self.make()
        self.walk(b, "09:30", "10:20")
        out = b.tick(ny(DAY + " 15:55"))
        self.assertTrue(out["done"])
        self.assertTrue(b.st["flattened"])
        self.assertEqual(b.broker.pos, {})
        sells = [f for f in b.broker.fill_log if f["side"] == "sell"]
        self.assertEqual(len(sells), 1)
        self.assertEqual(sells[0]["qty"], 8)
        self.assertEqual(self.broker_log()[-1]["kind"], "exit")

    def test_state_survives_a_restart(self):
        b = self.make()
        self.walk(b, "09:30", "10:20")
        b2 = self.make(broker=b.broker)
        b2.tick(ny(DAY + " 10:21"))
        self.assertEqual(len(b2.st["entered"]), 1)
        self.assertEqual(b2.st["symbols"]["TSLA"]["status"], "entered")

    def test_no_entry_before_the_range_is_set(self):
        b = self.make()
        self.walk(b, "09:30", "10:09")
        self.assertEqual(b.st["entered"], [])
        self.assertEqual(b.st["symbols"]["TSLA"]["status"], "watching")


class Guardrails(Base):
    def test_kill_switch_stops_entries_and_flattens(self):
        b = self.make()
        self.walk(b, "09:30", "10:20")
        open(b.kill_path, "w").close()
        out = b.tick(ny(DAY + " 10:22"))
        self.assertTrue(out["done"])
        self.assertIn("kill switch", b.st["halted"])
        self.assertEqual(b.broker.pos, {})

    def test_kill_switch_before_any_trade_means_no_trade(self):
        b = self.make()
        open(b.kill_path, "w").close()
        out = self.walk(b, "09:45", "10:30")
        self.assertTrue(out["done"])
        self.assertEqual(b.st["entered"], [])

    def test_daily_loss_halts_and_flattens(self):
        b = self.make(config=cfg(max_daily_loss_usd=50))
        self.walk(b, "09:30", "10:20")
        real = b.broker.observe

        def observe(sym, chart, now):
            real(sym, chart, now)
            b.broker.prices[sym] = 357.00   # a gap through the 358.32 stop: 8 x (357 - 364.18) = -57.44
        b.broker.observe = observe
        self.assertEqual(len(b.st["entered"]), 1)
        out = b.tick(ny(DAY + " 10:20:30"))
        self.assertTrue(out["done"], out)
        self.assertIn("daily loss", b.st["halted"])
        self.assertEqual(b.broker.pos, {})

    def test_never_trades_or_closes_a_holding_it_did_not_open(self):
        sim = bot.SimBroker(5000)
        sim.pos["TSLA"] = {"qty": 3, "price": 300.0, "stop": 0, "target": 9999, "since": 0}
        sim.pos["AAPL"] = {"qty": 10, "price": 200.0, "stop": 0, "target": 9999, "since": 0}
        b = self.make(broker=sim)
        self.walk(b, "09:30", "10:30")
        self.assertEqual(b.st["entered"], [])
        self.assertEqual(b.st["symbols"]["TSLA"]["status"], "skipped")
        b.tick(ny(DAY + " 15:56"))
        self.assertEqual(sorted(sim.pos), ["AAPL", "TSLA"])   # untouched at the flatten

    def test_stale_trigger_is_not_chased(self):
        b = self.make()
        b.tick(ny(DAY + " 10:40"))          # first look 25 minutes after the 10:15 trigger
        self.assertEqual(b.st["entered"], [])
        self.assertIn("too late", b.st["symbols"]["TSLA"]["reason"])

    def test_a_price_run_past_the_trigger_is_not_chased(self):
        b = self.make()
        b.broker.set_clock(ny(DAY + " 10:15"))
        real = b.broker.observe

        def observe(sym, chart, now):
            real(sym, chart, now)
            b.broker.prices[sym] = 368.00   # 1.05 % over 364.18
        b.broker.observe = observe
        b.tick(ny(DAY + " 10:15"))
        self.assertEqual(b.st["entered"], [])
        self.assertIn("max_chase_pct", b.st["symbols"]["TSLA"]["reason"])

    def test_a_small_run_up_is_planned_from_the_fill_price(self):
        b = self.make()
        real = b.broker.observe

        def observe(sym, chart, now):
            real(sym, chart, now)
            b.broker.prices[sym] = 365.00
        b.broker.observe = observe
        b.tick(ny(DAY + " 10:15"))
        e = b.st["entered"][0]
        self.assertEqual((e["entry"], e["stop"]), (365.00, 358.32))
        self.assertAlmostEqual(e["target"], 371.68)        # still 1R from the real entry
        self.assertLessEqual(e["max_loss"], 50)

    def test_trader_risk_blocks_are_respected(self):
        b = self.make(config=cfg(blocked=[]), symbols=("TSLA",))
        b.cfg["max_trades_per_day"] = 0
        self.walk(b, "09:30", "10:20")
        self.assertEqual(b.st["entered"], [])

    def test_a_stop_too_far_for_the_max_loss_is_skipped(self):
        b = self.make(config=cfg(max_loss_per_trade_usd=5))
        self.walk(b, "09:30", "10:16")
        self.assertEqual(b.st["entered"], [])
        self.assertEqual(b.st["symbols"]["TSLA"]["status"], "skipped")
        self.assertIn("max loss", b.st["symbols"]["TSLA"]["reason"])

    def test_market_closed_day_is_done_at_once(self):
        b = self.make()
        out = b.tick(ny("2026-10-17 10:15"))   # a Saturday
        self.assertTrue(out["done"])


class LiveArming(unittest.TestCase):
    def test_paper_is_the_default(self):
        self.assertEqual(bot.mode_of({"paper": True}, False, False, {}), "paper")
        self.assertEqual(bot.mode_of({}, False, False, {}), "paper")

    def test_live_needs_config_flag_and_env(self):
        env = {bot.LIVE_ENV: bot.LIVE_WORD}
        self.assertEqual(bot.mode_of({"paper": False}, True, False, env), "live")
        with self.assertRaises(bot.BotError):
            bot.mode_of({"paper": False}, True, False, {})                 # no env word
        with self.assertRaises(bot.BotError):
            bot.mode_of({"paper": False}, True, False, {bot.LIVE_ENV: "yes"})
        with self.assertRaises(bot.BotError):
            bot.mode_of({"paper": False}, False, False, env)               # no --live
        with self.assertRaises(bot.BotError):
            bot.mode_of({"paper": True}, True, False, env)                 # config says paper

    def test_dry_run_never_goes_live(self):
        self.assertEqual(bot.mode_of({"paper": False}, False, True, {}), "dry-run")
        with self.assertRaises(bot.BotError):
            bot.mode_of({"paper": False}, True, True, {bot.LIVE_ENV: bot.LIVE_WORD})

    def test_config_refuses_shorts_margin_and_no_stop(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        path = os.path.join(tmp, "c.json")
        for over in ({"allow_short": True}, {"allow_margin": True}, {"require_stop": False}, {"budget": None}):
            with open(path, "w") as fh:
                json.dump(cfg(**over), fh)
            with self.assertRaises(bot.BotError, msg=str(over)):
                bot.load_config(path)


class FakeResp(object):
    def __init__(self, body):
        self.body = json.dumps(body).encode() if body is not None else b""

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeAlpaca(object):
    """Records requests; answers from a script of (method, path-prefix) -> body or exception."""

    def __init__(self, script):
        self.script = script
        self.calls = []

    def __call__(self, req, timeout=None):
        path = req.full_url.split(".markets", 1)[1]
        self.calls.append((req.get_method(), req.full_url, req.data and json.loads(req.data.decode()),
                           dict(req.header_items())))
        for (m, prefix), ans in self.script:
            if m == req.get_method() and path.startswith(prefix):
                if isinstance(ans, Exception):
                    raise ans
                return FakeResp(ans)
        raise AssertionError("unexpected %s %s" % (req.get_method(), path))


class AlpacaRest(Base):
    ACCOUNT = {"cash": "5000", "equity": "5000", "status": "ACTIVE", "trading_blocked": False,
               "account_blocked": False, "account_number": "PA123"}

    def test_paper_url_by_default_and_live_only_when_asked(self):
        self.assertEqual(bot.Alpaca("k", "s").base, bot.PAPER_URL)
        self.assertEqual(bot.Alpaca("k", "s", live=True).base, bot.LIVE_URL)

    def test_bracket_body(self):
        order = {"symbol": "TSLA", "side": "buy", "qty": "8", "type": "market", "time_in_force": "day",
                 "order_class": "bracket", "take_profit_limit_price": "370.04", "stop_loss_stop_price": "358.32",
                 "why": "x", "plan": {}}
        body = bot.alpaca_body(order, "cid")
        self.assertEqual(body, {"symbol": "TSLA", "qty": "8", "side": "buy", "type": "market",
                                "time_in_force": "day", "client_order_id": "cid", "order_class": "bracket",
                                "take_profit": {"limit_price": "370.04"}, "stop_loss": {"stop_price": "358.32"}})

    def script(self, post):
        return [(("GET", "/v2/positions"), []), (("GET", "/v2/account/activities"), []),
                (("GET", "/v2/account"), self.ACCOUNT),
                (("GET", "/v2/stocks/TSLA/trades/latest"), {"trade": {"p": 364.18, "t": "2026-10-19T14:15:00.1234567Z"}}),
                (("POST", "/v2/orders"), post),
                (("GET", "/v2/orders:by_client_order_id"), {"id": "abc", "status": "accepted"}),
                (("GET", "/v2/orders"), [])]

    def test_places_the_bracket_on_the_paper_api_with_keys_in_headers(self):
        fake = FakeAlpaca(self.script({"id": "abc", "status": "accepted", "client_order_id": "x"}))
        b = self.make(broker=bot.Alpaca("KEY", "SECRET", opener=fake))
        b.tick(ny(DAY + " 10:15"))
        posts = [c for c in fake.calls if c[0] == "POST"]
        self.assertEqual(len(posts), 1)
        method, url, body, headers = posts[0]
        self.assertTrue(url.startswith(bot.PAPER_URL))
        self.assertEqual((body["qty"], body["stop_loss"], body["take_profit"]),
                         ("8", {"stop_price": "358.32"}, {"limit_price": "370.04"}))
        self.assertEqual(headers.get("Apca-api-key-id"), "KEY")
        self.assertEqual(b.st["entered"][0]["order_id"], "abc")

    def test_a_lost_reply_is_resolved_by_client_order_id(self):
        fake = FakeAlpaca(self.script(urllib.error.URLError("timed out")))
        b = self.make(broker=bot.Alpaca("k", "s", opener=fake))
        b.tick(ny(DAY + " 10:15"))
        self.assertEqual(b.st["entered"][0]["order_id"], "abc")    # found, not sent twice
        self.assertEqual(len([c for c in fake.calls if c[0] == "POST"]), 1)

    def test_an_unknown_outcome_halts_the_bot(self):
        script = self.script(urllib.error.URLError("timed out"))
        script = [s for s in script if s[0][1] != "/v2/orders:by_client_order_id"]
        script.insert(0, (("GET", "/v2/orders:by_client_order_id"),
                          urllib.error.HTTPError("u", 404, "nf", {}, io.BytesIO(b"{}"))))
        fake = FakeAlpaca(script)
        b = self.make(broker=bot.Alpaca("k", "s", opener=fake))
        b.tick(ny(DAY + " 10:15"))
        self.assertEqual(b.st["entered"], [])
        self.assertIn("unknown", b.st["halted"])

    def test_a_refused_order_is_not_retried(self):
        err = urllib.error.HTTPError("u", 403, "forbidden", {}, io.BytesIO(b'{"message": "insufficient buying power"}'))
        fake = FakeAlpaca(self.script(err))
        b = self.make(broker=bot.Alpaca("k", "s", opener=fake))
        b.tick(ny(DAY + " 10:15"))
        b.tick(ny(DAY + " 10:17"))
        self.assertEqual(len([c for c in fake.calls if c[0] == "POST"]), 1)
        self.assertIn("insufficient buying power", b.st["symbols"]["TSLA"]["reason"])


class Symbols(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)

    def args(self, **kw):
        return type("A", (), dict({"symbols": None, "watchlist": None}, **kw))()

    def test_reads_the_scanner_watchlist_and_the_v1_shortlist(self):
        c = {"bot": dict(bot.BOT_DEFAULTS)}
        w = os.path.join(self.tmp, "w.json")
        with open(w, "w") as fh:
            json.dump({"hits": [{"symbol": "tsla"}, {"symbol": "NVDA"}]}, fh)
        self.assertEqual(bot.symbols_from(self.args(watchlist=w), c)[0], ["TSLA", "NVDA"])
        with open(w, "w") as fh:
            json.dump({"passed": [{"ticker": "AAPL"}, {"ticker": "KO"}], "failed": [{"ticker": "X"}]}, fh)
        self.assertEqual(bot.symbols_from(self.args(watchlist=w), c)[0], ["AAPL", "KO"])

    def test_flag_beats_config_and_the_cap_holds(self):
        c = {"bot": dict(bot.BOT_DEFAULTS, symbols=["AMD"], max_symbols=2)}
        self.assertEqual(bot.symbols_from(self.args(symbols="a,b,c"), c)[0], ["A", "B"])
        self.assertEqual(bot.symbols_from(self.args(), c)[0], ["AMD"])


class Cli(unittest.TestCase):
    def test_replay_the_reel_day(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        os.makedirs(os.path.join(tmp, "charts"))
        shutil.copy(TSLA, os.path.join(tmp, "charts", "TSLA.json"))
        shutil.copy(CONFIG, os.path.join(tmp, "config.json"))
        cwd = os.getcwd()
        os.chdir(tmp)
        self.addCleanup(os.chdir, cwd)
        out = io.StringIO()
        with redirect_stdout(out):
            code = bot.main(["replay", "--offline", "charts", "--day", DAY, "--symbols", "TSLA",
                             "--config", "config.json"])
        text = out.getvalue()
        self.assertEqual(code, 0, text)
        self.assertIn("BUY 8 at ~364.18, stop 358.32, target 370.04, max loss $46.88", text)
        self.assertIn("flat by 15:55", text)
        self.assertIn("Not financial advice", text)

    def test_live_without_the_env_word_refuses(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        path = os.path.join(tmp, "c.json")
        with open(path, "w") as fh:
            json.dump(cfg(paper=False, bot={"symbols": ["TSLA"]}), fh)
        saved = os.environ.pop(bot.LIVE_ENV, None)
        self.addCleanup(lambda: saved is not None and os.environ.__setitem__(bot.LIVE_ENV, saved))
        err = io.StringIO()
        with redirect_stderr(err):
            code = bot.main(["once", "--live", "--config", path, "--state-dir", tmp])
        self.assertEqual(code, 1)
        self.assertIn(bot.LIVE_ENV, err.getvalue())

    def test_stop_and_resume(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        kill = os.path.join(tmp, "STOP")
        with redirect_stdout(io.StringIO()):
            bot.main(["stop", "--kill", kill])
            self.assertTrue(os.path.exists(kill))
            bot.main(["resume", "--kill", kill])
        self.assertFalse(os.path.exists(kill))


class Skill(unittest.TestCase):
    def test_skill_md_says_how_live_is_armed(self):
        with open(os.path.join(ROOT, "trader-bot", "SKILL.md"), encoding="utf-8") as fh:
            text = " ".join(fh.read().split())
        for must in ("name: trader-bot", "TRADER_BOT_LIVE=real-money", "--live", "\"paper\": false",
                     "trader/STOP", "--dry-run", "replay"):
            self.assertIn(must, text)
        self.assertIn("not financial advice", text.lower())


if __name__ == "__main__":
    unittest.main()
