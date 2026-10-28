"""trader-bot's learner: the replay, the study's rules, the journal, apply. Synthetic
Yahoo-shaped 5-minute charts written to a temp folder. No network."""

import datetime
import json
import os
import shutil
import tempfile
import unittest

from _load import load

learn = load("trader-bot", "learn")
entry = learn.entry

DAY0 = datetime.date(2026, 9, 1)


def sessions(n):
    d, out = DAY0, []
    while len(out) < n:
        if entry.trading_day(d)[0] and entry.session(d)[1].hour == 16:
            out.append(d)
        d += datetime.timedelta(days=1)
    return out


def day_bars(kind, base=100.0):
    """78 bars (o, h, l, c). The range is base..base+1 over the first 15 minutes, then:
    win   the 9:45 bar closes at base+1.5 and the price climbs to base+4
    loss  the 9:45 bar closes at base+1.5 and the price falls to base-1
    late  nothing until the 12:00 bar closes at base+1.5, then base+4
    flat  never closes over the range
    up / down  a market symbol drifting 1 % up or down from its open"""
    b = [(base, base + 1, base, base + 0.5), (base + 0.5, base + 1, base, base + 0.6),
         (base + 0.6, base + 1, base, base + 0.7)]
    if kind in ("up", "down"):
        sign = 1 if kind == "up" else -1
        return [(base, base, base, base * (1 + sign * 0.01 * i / 77.0)) for i in range(78)]
    if kind in ("win", "loss"):
        b.append((base + 0.7, base + 1.6, base + 0.7, base + 1.5))
        p, step = base + 1.5, (0.2 if kind == "win" else -0.2)
        while len(b) < 78:
            q = p + step
            b.append((p, max(p, q), min(p, q), q))
            p = q if (kind == "win" and q < base + 4) or (kind == "loss" and q > base - 1) else p
    elif kind == "late":
        while len(b) < 30:
            b.append((base + 0.5, base + 0.8, base + 0.3, base + 0.5))
        b.append((base + 0.5, base + 1.6, base + 0.5, base + 1.5))
        p = base + 1.5
        while len(b) < 78:
            q = min(p + 0.2, base + 4)
            b.append((p, q, p, q))
            p = q
    else:
        while len(b) < 78:
            b.append((base + 0.5, base + 0.8, base + 0.3, base + 0.5))
    return b


def chart(sym, plan):
    """plan: [(date, kind)] -> Yahoo v8 chart JSON text."""
    ts, o, h, l, c, v = [], [], [], [], [], []
    for d, kind in plan:
        t0 = entry.ts_of(entry.eastern_to_utc(datetime.datetime.combine(d, datetime.time(9, 30))))
        for i, (a, hi, lo, cl) in enumerate(day_bars(kind)):
            ts.append(t0 + 300 * i)
            o.append(a); h.append(hi); l.append(lo); c.append(cl); v.append(1000)
    return json.dumps({"chart": {"result": [{"meta": {"symbol": sym, "dataGranularity": "5m"}, "timestamp": ts,
                                             "indicators": {"quote": [{"open": o, "high": h, "low": l, "close": c,
                                                                       "volume": v}]}}], "error": None}})


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.data = os.path.join(self.tmp, "data")
        os.makedirs(self.data)

    def write(self, sym, plan):
        with open(os.path.join(self.data, sym + ".json"), "w") as fh:
            fh.write(chart(sym, plan))

    def one(self, kind, spy="up", **s):
        d = sessions(1)[0]
        self.write("AAA", [(d, kind)])
        self.write("SPY", [(d, spy)])
        data = learn.load_dir(self.data)
        st = dict(learn.rh.RH_DEFAULTS, **s)
        return learn.trade_day(data["AAA"][d], st, data["SPY"], d)


class Replay(Base):
    def test_a_breakout_that_runs_hits_1r(self):
        t = self.one("win")
        self.assertEqual(t["exit"], "target")
        self.assertAlmostEqual(t["r"], 1.0, delta=0.05)  # less the spread
        self.assertEqual(t["entry_min"], 9 * 60 + 50)

    def test_a_breakout_that_fails_hits_the_midpoint_stop(self):
        t = self.one("loss")
        self.assertEqual(t["exit"], "stop")
        self.assertAlmostEqual(t["r"], -1.0, delta=0.05)

    def test_no_long_while_spy_is_under_its_open(self):
        self.assertIsNone(self.one("win", spy="down"))
        self.assertIsNotNone(self.one("win", spy="down", market_filter=None))

    def test_no_entry_after_the_cutoff(self):
        self.assertIsNone(self.one("late"))
        self.assertEqual(self.one("late", no_entries_after="15:30")["exit"], "target")

    def test_no_breakout_no_trade(self):
        self.assertIsNone(self.one("flat"))

    def test_the_account_must_afford_one_share_at_the_max_loss(self):
        d = sessions(1)[0]
        self.write("AAA", [(d, "loss")])
        self.write("SPY", [(d, "up")])
        data = learn.load_dir(self.data)
        st = dict(learn.rh.RH_DEFAULTS)
        self.assertIsNone(learn.trade_day(data["AAA"][d], st, data["SPY"], d, account=50))   # max loss 0.50 < 1.0
        self.assertIsNotNone(learn.trade_day(data["AAA"][d], st, data["SPY"], d, account=660))


class Study(Base):
    def test_too_few_days_is_an_error(self):
        days = sessions(5)
        self.write("AAA", [(d, "win") for d in days])
        with self.assertRaises(learn.LearnError):
            learn.study(learn.load_dir(self.data), {})

    def test_all_losers_keeps_the_setting_and_claims_no_edge(self):
        days = sessions(24)
        for s in ("AAA", "BBB", "CCC", "DDD", "EEE"):
            self.write(s, [(d, "loss") for d in days])
        self.write("SPY", [(d, "up") for d in days])
        rep = learn.study(learn.load_dir(self.data), {})
        self.assertIsNone(rep["proposal"])
        self.assertFalse(rep["edge"])
        self.assertTrue(rep["verdict"].startswith("keep the current setting"))
        self.assertEqual(rep["current"]["settings"]["no_entries_after"], "11:30")
        self.assertEqual(rep["data"]["symbols"], 5)

    def test_the_learner_may_not_touch_the_risk_limits(self):
        for k in ("max_loss_pct_per_trade", "max_trades_per_day", "max_losing_trades", "flat_by"):
            self.assertNotIn(k, learn.STRATEGY_KEYS)
            for s in learn.candidates():
                self.assertNotIn(k, s)


class Apply(Base):
    def test_apply_writes_strategy_keys_and_keeps_a_backup(self):
        cfg = os.path.join(self.tmp, "config.json")
        with open(cfg, "w") as fh:
            json.dump({"budget": 660, "robinhood": {"max_trades_per_day": 4}}, fh)
        prop = os.path.join(self.tmp, "proposal.json")
        with open(prop, "w") as fh:
            json.dump({"settings": {"or_minutes": 30, "target": "1.5R"}}, fh)
        changed = learn.apply(prop, cfg)
        self.assertEqual(changed["or_minutes"], [15, 30])
        with open(cfg) as fh:
            doc = json.load(fh)
        self.assertEqual(doc["robinhood"], {"max_trades_per_day": 4, "or_minutes": 30, "target": "1.5R"})
        self.assertTrue(os.path.exists(cfg + ".bak"))

    def test_apply_refuses_a_risk_limit(self):
        prop = os.path.join(self.tmp, "proposal.json")
        with open(prop, "w") as fh:
            json.dump({"settings": {"max_loss_pct_per_trade": 5}}, fh)
        with self.assertRaises(learn.LearnError):
            learn.apply(prop, os.path.join(self.tmp, "config.json"))

    def test_apply_without_a_proposal_is_an_error(self):
        with self.assertRaises(learn.LearnError):
            learn.apply(os.path.join(self.tmp, "none.json"), os.path.join(self.tmp, "config.json"))


def order(sym, side, typ, qty, avg, at, stop=None, trigger="immediate", oid=None):
    t = entry.eastern_to_utc(datetime.datetime(2026, 10, 27, *map(int, at.split(":"))))
    return {"id": oid or sym + side + at, "symbol": sym, "side": side, "type": typ, "trigger": trigger,
            "state": "filled", "quantity": "%.6f" % qty, "cumulative_quantity": "%.6f" % qty,
            "average_price": "%.6f" % avg, "stop_price": None if stop is None else "%.6f" % stop,
            "created_at": t.strftime("%Y-%m-%dT%H:%M:%S.000000Z"), "executions": []}


class Journal(Base):
    def snap(self, orders):
        path = os.path.join(self.tmp, "snap.json")
        with open(path, "w") as fh:
            json.dump({"portfolio": {"data": {"total_value": "660"}}, "orders": {"data": {"orders": orders}}}, fh)
        return learn.rh.load_snapshot(path)

    def test_a_stopped_out_trade_is_minus_1r(self):
        s = self.snap([order("MFIC", "buy", "limit", 2, 10.0, "10:16"),
                       order("MFIC", "sell", "market", 2, 9.5, "10:40", stop=9.5, trigger="stop")])
        (t,) = learn.round_trips(s)
        self.assertEqual((t["date"], t["symbol"], t["pnl"], t["r"], t["exit_by"]),
                         ("2026-10-27", "MFIC", -1.0, -1.0, "stop"))

    def test_an_open_position_is_not_a_round_trip(self):
        s = self.snap([order("MFIC", "buy", "limit", 2, 10.0, "10:16")])
        self.assertEqual(learn.round_trips(s), [])

    def test_the_journal_adds_each_trade_once(self):
        s = self.snap([order("MFIC", "buy", "limit", 2, 10.0, "10:16"),
                       order("MFIC", "sell", "market", 2, 10.5, "11:00")])
        path = os.path.join(self.tmp, "j", "journal.jsonl")
        self.assertEqual(len(learn.add_to_journal(path, learn.round_trips(s))), 1)
        self.assertEqual(learn.add_to_journal(path, learn.round_trips(s)), [])
        self.assertEqual(len(learn.read_journal(path)), 1)

    def test_review_wants_20_trades_before_judging(self):
        rows = [{"date": "2026-10-27", "symbol": "X", "pnl": -1.0, "r": -1.0, "exit_by": "stop"}] * 5
        self.assertIn("too few", learn.review(rows)["judgement"])

    def test_review_flags_live_worse_than_the_backtest(self):
        rows = [{"date": "d%d" % i, "symbol": "X", "pnl": -1.0, "r": -1.0 + 0.1 * (i % 3), "exit_by": "stop"}
                for i in range(25)]
        rep = {"current": {"stats": {"mean_r": 0.0}}}
        self.assertIn("worse than the backtest", learn.review(rows, rep)["judgement"])


if __name__ == "__main__":
    unittest.main()
