"""trader-bot Robinhood mode. The reel's TSLA Oct 19 2026 bars, rewritten as
Robinhood get_equity_historicals bars, and snapshots in the connector's own
response shapes (get_portfolio, get_equity_positions, get_equity_orders,
get_equity_quotes). No network."""

import datetime
import json
import os
import unittest

from _load import load, FIX

rh = load("trader-bot", "robinhood")
entry = rh.entry

TSLA = os.path.join(FIX, "trader-entry", "TSLA-2026-10-19-5m.json")
DAY = "2026-10-19"


def ny(text):
    return entry.parse_now(DAY + " " + text)


def iso(utc):
    return utc.strftime("%Y-%m-%dT%H:%M:%S.000000Z")


def rh_bars():
    with open(TSLA) as fh:
        ch = entry.parse_chart(fh.read(), "TSLA")
    return [{"begins_at": iso(entry.utc_of(b["t"])), "open_price": "%.6f" % b["o"], "high_price": "%.6f" % b["h"],
             "low_price": "%.6f" % b["l"], "close_price": "%.6f" % b["c"], "volume": b["v"], "session": "reg"}
            for b in ch["bars"] if b["t"] % 300 == 0]


def quote(sym, last, ask=None):
    return {"quote": {"symbol": sym, "last_trade_price": "%.6f" % last, "bid_price": "%.6f" % (last - 0.01),
                      "ask_price": "%.6f" % (ask or last + 0.01), "state": "active"}}


def order(sym, side, typ, state, qty, filled=0, avg=None, stop=None, price=None, at="10:15", trigger="immediate",
          oid=None):
    return {"id": oid or "%s-%s-%s-%s" % (sym, side, typ, at), "symbol": sym, "side": side, "type": typ,
            "trigger": trigger, "state": state, "quantity": "%.6f" % qty, "cumulative_quantity": "%.6f" % filled,
            "average_price": None if avg is None else "%.6f" % avg, "stop_price": None if stop is None else "%.6f" % stop,
            "price": None if price is None else "%.6f" % price, "created_at": iso(ny(at)), "executions": []}


def spy_bars(chg):
    """SPY's day so far, opening at 600 and drifting to chg % by 10:10."""
    t0 = ny("09:30")
    return [[iso(t0 + datetime.timedelta(minutes=5 * i)), 600.0, 600.5, 599.5, 600.0 * (1 + chg / 100.0 * i / 8), 1000]
            for i in range(9)]


def snap(value=663.75, positions=(), orders=(), last=364.18, ask=None, watch=("TSLA",), now=None, spy=0.3):
    return {"bars": {} if spy is None else {"SPY": spy_bars(spy)},"portfolio": {"data": {"total_value": "%.2f" % value, "cash": "%.2f" % value,
                                   "buying_power": {"buying_power": "%.4f" % value}}},
            "positions": {"data": {"positions": [{"symbol": s, "quantity": "%.6f" % q} for s, q in positions]}},
            "orders": {"data": {"orders": list(orders)}},
            "historicals": [{"data": {"results": [{"symbol": "TSLA", "interval": "5minute", "bars": rh_bars()}]}}],
            "quotes": [{"data": {"results": [quote("TSLA", last, ask)] +
                                 ([] if spy is None else [quote("SPY", 600.0 * (1 + spy / 100.0))])}}],
            "watch": list(watch)}


def run(s, at, cfg=None, kill=False, tmp=None):
    path = os.path.join(tmp, "snap.json")
    with open(path, "w") as fh:
        json.dump(s, fh)
    return rh.decide(rh.load_snapshot(path), cfg or {}, ny(at), kill=kill)


class Base(unittest.TestCase):
    def setUp(self):
        import tempfile, shutil
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)

    def go(self, s, at, **kw):
        return run(s, at, tmp=self.tmp, **kw)


class Entry(Base):
    def test_one_share_of_tsla_at_one_percent_of_663(self):
        out = self.go(snap(), "10:16")
        self.assertEqual(len(out["actions"]), 1)
        a = out["actions"][0]
        # max loss 1 % of 663.75 = 6.63; one share risks 364.19 - 358.32 = 5.87
        self.assertEqual((a["action"], a["symbol"], a["type"], a["quantity"], a["limit_price"]),
                         ("buy", "TSLA", "limit", "1", "364.19"))
        self.assertEqual(a["stop"], 358.32)
        self.assertAlmostEqual(a["target"], 370.06)
        self.assertLessEqual(a["max_loss"], 6.63)
        self.assertEqual(out["status"]["max_loss_per_trade"], 6.63)
        self.assertEqual(a["ref_id"], rh.ref_id(DAY, "TSLA", "entry"))

    def test_no_entry_while_the_range_forms_or_after_a_stale_trigger(self):
        self.assertEqual(self.go(snap(), "09:50")["actions"], [])
        out = self.go(snap(), "10:40")
        self.assertEqual(out["actions"], [])
        self.assertIn("stale", out["status"]["watch"][0]["status"])

    def test_a_run_past_the_trigger_is_not_chased(self):
        out = self.go(snap(last=367.0, ask=367.0), "10:16")
        self.assertEqual(out["actions"], [])
        self.assertIn("not chased", out["status"]["watch"][0]["status"])

    def test_a_stop_too_wide_for_the_account_is_skipped(self):
        out = self.go(snap(value=400), "10:16")    # 1 % = 4.00 < 5.87 a share
        self.assertEqual(out["actions"], [])
        self.assertIn("sizing", out["status"]["watch"][0]["status"])

    def test_never_trades_a_holding_it_did_not_buy_today(self):
        out = self.go(snap(positions=[("TSLA", 2)]), "10:16")
        self.assertEqual(out["actions"], [])
        self.assertEqual(out["status"]["watch"][0]["status"], "already held (not the bot's)")


class MarketFilter(Base):
    """Backtested on 51 symbols x 60 days: longs only while SPY is over its open, entries by 11:30."""

    def test_no_long_entry_while_spy_is_under_its_open(self):
        out = self.go(snap(spy=-0.2), "10:16")
        self.assertEqual(out["actions"], [])
        self.assertIn("SPY", out["status"]["reason"])
        self.assertEqual(out["status"]["market"]["change_pct"], -0.2)

    def test_no_spy_data_means_no_entry(self):
        out = self.go(snap(spy=None), "10:16")
        self.assertEqual(out["actions"], [])
        self.assertIn("cannot be checked", out["status"]["reason"])

    def test_the_filter_can_be_turned_off(self):
        out = self.go(snap(spy=None), "10:16", cfg={"market_filter": None})
        self.assertEqual([a["action"] for a in out["actions"]], ["buy"])

    def test_spy_under_its_open_still_protects_a_position(self):
        buy = order("TSLA", "buy", "limit", "filled", 1, 1, 364.19, price=364.19, at="10:16")
        out = self.go(snap(positions=[("TSLA", 1)], orders=[buy], spy=-0.5), "10:17")
        self.assertEqual([(a["action"], a["type"]) for a in out["actions"]], [("sell", "stop_market")])

    def test_no_entries_after_1130(self):
        self.assertEqual(rh.RH_DEFAULTS["no_entries_after"], "11:30")


class Holding(Base):
    BUY = order("TSLA", "buy", "limit", "filled", 1, 1, 364.19, price=364.19, at="10:16")

    def test_places_the_stop_once_the_buy_fills(self):
        out = self.go(snap(positions=[("TSLA", 1)], orders=[self.BUY], last=364.5), "10:21")
        a = out["actions"]
        self.assertEqual(len(a), 1)
        self.assertEqual((a[0]["action"], a[0]["type"], a[0]["quantity"], a[0]["stop_price"]),
                         ("sell", "stop_market", "1", "358.32"))

    def test_holds_quietly_with_the_stop_in_place(self):
        stop = order("TSLA", "sell", "market", "confirmed", 1, stop=358.32, at="10:17", trigger="stop")
        out = self.go(snap(positions=[("TSLA", 1)], orders=[self.BUY, stop], last=366.0), "10:26")
        self.assertEqual(out["actions"], [])
        self.assertEqual(out["status"]["open"][0]["target"], 370.06)

    def test_sells_at_the_target(self):
        stop = order("TSLA", "sell", "market", "confirmed", 1, stop=358.32, at="10:17", trigger="stop")
        out = self.go(snap(positions=[("TSLA", 1)], orders=[self.BUY, stop], last=370.10), "10:41")
        kinds = [(x["action"], x.get("type")) for x in out["actions"]]
        self.assertEqual(kinds, [("cancel", None), ("sell", "market")])
        self.assertIn("target", out["actions"][1]["why"])

    def test_flattens_at_flat_by_and_is_done_once_flat(self):
        stop = order("TSLA", "sell", "market", "confirmed", 1, stop=358.32, at="10:17", trigger="stop")
        out = self.go(snap(positions=[("TSLA", 1)], orders=[self.BUY, stop], last=366.0), "15:55")
        self.assertEqual([x["action"] for x in out["actions"]], ["cancel", "sell"])
        self.assertFalse(out["done"])
        sold = order("TSLA", "sell", "market", "filled", 1, 1, 366.0, at="15:55")
        out = self.go(snap(orders=[self.BUY, sold], last=366.0), "15:56")
        self.assertEqual(out["actions"], [])
        self.assertTrue(out["done"])

    def test_kill_switch_sells(self):
        out = self.go(snap(positions=[("TSLA", 1)], orders=[self.BUY], last=365.0), "11:00", kill=True)
        self.assertEqual(out["actions"][-1]["type"], "market")
        self.assertTrue(out["done"] is False)

    def test_an_unfilled_entry_is_cancelled_next_pass(self):
        working = order("TSLA", "buy", "limit", "confirmed", 1, price=364.19, at="10:16")
        out = self.go(snap(orders=[working]), "10:21")
        self.assertEqual([x["action"] for x in out["actions"]], ["cancel"])


class DayLimits(Base):
    def loser(self, sym, at, buy=10.0, sell=9.0):
        return [order(sym, "buy", "limit", "filled", 5, 5, buy, at=at, oid=sym + "b"),
                order(sym, "sell", "market", "filled", 5, 5, sell, at=at, trigger="stop", oid=sym + "s")]

    def test_two_losing_trades_stop_the_day(self):
        orders = self.loser("AAA", "09:50", 10.0, 9.9) + self.loser("BBB", "10:00", 10.0, 9.9)
        out = self.go(snap(orders=orders), "10:16")
        self.assertEqual(out["actions"], [])
        self.assertEqual(out["status"]["losing_trades"], 2)
        self.assertIn("losing trades", out["status"]["reason"])

    def test_one_loss_still_trades(self):
        out = self.go(snap(orders=self.loser("AAA", "09:50", 10.0, 9.9)), "10:16")
        self.assertEqual([x["action"] for x in out["actions"]], ["buy"])

    def test_four_trades_is_the_max(self):
        orders = []
        for i, s in enumerate(("AAA", "BBB", "CCC", "DDD")):
            orders += [order(s, "buy", "limit", "filled", 1, 1, 10.0, at="09:5%d" % i, oid=s + "b"),
                       order(s, "sell", "market", "filled", 1, 1, 10.5, at="09:5%d" % i, oid=s + "s")]
        out = self.go(snap(orders=orders), "10:16")
        self.assertEqual(out["actions"], [])
        self.assertIn("4 trades", out["status"]["reason"])

    def test_a_gap_through_the_stop_trips_the_backstop(self):
        buy = order("TSLA", "buy", "limit", "filled", 1, 1, 364.19, at="10:16")
        out = self.go(snap(positions=[("TSLA", 1)], orders=[buy], last=350.0), "10:31")   # -14.19 < -13.26
        self.assertEqual(out["status"]["state"], "flatten")
        self.assertEqual(out["actions"][-1]["type"], "market")

    def test_yesterdays_orders_do_not_count(self):
        old = order("FRO", "buy", "limit", "filled", 2, 2, 55.37, at="10:00")
        old["created_at"] = "2026-10-08T14:16:08.236369Z"
        out = self.go(snap(orders=[old]), "10:16")
        self.assertEqual(out["status"]["trades"], 0)
        self.assertEqual([x["action"] for x in out["actions"]], ["buy"])


class Cache(Base):
    def test_a_pass_can_send_only_the_new_bars(self):
        bars = rh_bars()
        full = snap()
        cut = [b for b in bars if b["begins_at"] < iso(ny("10:10"))]
        rest = [[b["begins_at"], b["open_price"], b["high_price"], b["low_price"], b["close_price"], b["volume"]]
                for b in bars if b["begins_at"] >= iso(ny("10:10"))]
        cache = os.path.join(self.tmp, "bars.json")
        first = dict(full, historicals=[{"data": {"results": [{"symbol": "TSLA", "bars": cut}]}}])
        second = dict(full, historicals=[], bars={"TSLA": rest})
        for i, s in enumerate((first, second)):
            path = os.path.join(self.tmp, "s%d.json" % i)
            with open(path, "w") as fh:
                json.dump(s, fh)
            loaded = rh.merge_cache(rh.load_snapshot(path), cache, DAY)
        out = rh.decide(loaded, {}, ny("10:16"))
        self.assertEqual([x["action"] for x in out["actions"]], ["buy"])

    def test_yesterdays_cache_is_dropped(self):
        cache = os.path.join(self.tmp, "bars.json")
        with open(cache, "w") as fh:
            json.dump({"date": "2026-10-16", "bars": {"OLD": [{"begins_at": "x"}]}}, fh)
        path = os.path.join(self.tmp, "s.json")
        with open(path, "w") as fh:
            json.dump(snap(), fh)
        loaded = rh.merge_cache(rh.load_snapshot(path), cache, DAY)
        self.assertEqual(sorted(loaded["bars"]), ["SPY", "TSLA"])


class Parsing(unittest.TestCase):
    def test_stop_orders_are_read_from_type_and_trigger(self):
        o = rh.norm_orders({"data": {"orders": [order("ERIC", "sell", "market", "cancelled", 46, stop=10.47,
                                                      trigger="stop")]}})[0]
        self.assertEqual((o["type"], o["stop_price"], o["filled"]), ("stop_market", 10.47, 0.0))

    def test_closed_day(self):
        out = rh.decide({"total_value": 663.75, "buying_power": 663.75, "positions": {}, "orders": [], "bars": {},
                         "quotes": {}, "watch": []}, {}, entry.parse_now("2026-10-17 10:00"))
        self.assertTrue(out["done"])


if __name__ == "__main__":
    unittest.main()
