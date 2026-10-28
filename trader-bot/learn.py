#!/usr/bin/env python3
"""The bot's learner for trader-bot. Standard library only.

The bot learns from two places, and it changes its own settings only when a
change holds up on days it was not chosen on:

  1. The market. 60 days of 5-minute bars for the scanner's kind of stocks,
     plus SPY. Every candidate setting (opening range, stop, target, entry
     cutoff, market filter) is replayed bar by bar, no look-ahead, and scored
     in R on four separate blocks of days.
  2. Its own trades. Each evening the day's closed round trips are read back
     from the Robinhood orders and kept in a journal, so the live results can
     be held up against what the backtest promised.

    python3 learn.py fetch --symbols SOFI,PLTR,AMD --out trader/learn/data   # + SPY, 60 days of 5m bars
    python3 learn.py study --data trader/learn/data --write                  # score, maybe propose
    python3 learn.py study --data trader/learn/data --account 660            # skip trades it could not size
    python3 learn.py journal --snapshot trader/rh/snapshot.json              # record today's round trips
    python3 learn.py review                                                  # live vs the backtest
    python3 learn.py apply                                                   # the proposal -> config.json

How a setting earns a change (study):
  - the days are split, in order, into 4 blocks
  - the candidate's mean R beats the current setting's in at least 3 of the
    4 blocks, by at least 0.03R overall, with a worst block no worse
  - at least 100 trades in all, 15 in every block
  - and the learning itself holds up (walk-forward): before each block from
    the 2nd on, the best setting on the earlier blocks alone is picked and
    scored on that block; those picks must beat the current setting on most
    of those blocks and by 0.015R on average
  With 72 candidates some will look good by luck in hindsight: the walk-forward
  test asks whether choosing this way would have helped before the fact.
  An "edge" is claimed only when the mean R is over 0 in 3 of 4 blocks and
  more than 2 standard errors over 0 in all. Losing less is not an edge.

What it may change: or_minutes, stop, target, no_entries_after,
market_filter, market_min_pct. Never the risk limits (max loss per trade,
trades a day, losing trades, flat by): those are yours. apply writes only
when Claude is asked to, and keeps config.json.bak.

Not financial advice. A backtest is a simulation with simple fills, not a
promise.
"""

import argparse
import datetime
import itertools
import json
import math
import os
import shutil
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import robinhood as rh  # noqa: E402

bot, entry = rh.bot, rh.entry

STRATEGY_KEYS = ("or_minutes", "stop", "target", "no_entries_after", "market_filter", "market_min_pct")
GRID = {"or_minutes": [15, 30], "stop": ["or-mid", "or-low"], "target": ["1R", "1.5R", "2R"],
        "no_entries_after": ["11:30", "15:30"]}
MARKET = [(None, 0.0), ("SPY", 0.0), ("SPY", 0.2)]
MARKET_SYMBOLS = {"SPY", "QQQ"}   # the market's yardsticks, never traded in a replay
SLIP = 0.0003         # 0.03 % each side: spread and slippage (Robinhood has no commission)
BLOCKS = 4
MIN_TRADES, MIN_BLOCK, MIN_LIFT = 100, 15, 0.03
OPEN_MIN, FLAT_MIN = 570, 950   # 9:30; the 15:50 bar's close is the 15:55 flatten


class LearnError(Exception):
    pass


# ---------------------------------------------------------------- data

def load_dir(path):
    """<SYM>.json / .json.gz Yahoo 5m charts -> {SYM: {date: [(minute, o, h, l, c, v), ...]}}."""
    if not os.path.isdir(path):
        raise LearnError("no data folder %s (run: learn.py fetch)" % path)
    out = {}
    for f in sorted(os.listdir(path)):
        if not (f.endswith(".json") or f.endswith(".json.gz")):
            continue
        sym = f.split(".")[0].upper()
        try:
            ch = entry.parse_chart(entry.read_text(os.path.join(path, f)), sym)
        except (OSError, ValueError, entry.EntryError) as e:
            raise LearnError("could not read %s: %s" % (f, e))
        days = {}
        for b in ch["bars"]:
            if b["t"] % 300:
                continue
            et = entry.to_eastern(entry.utc_of(b["t"]))
            m = et.hour * 60 + et.minute
            if OPEN_MIN <= m < 960:
                days.setdefault(et.date(), []).append((m, b["o"], b["h"], b["l"], b["c"], b["v"]))
        out[sym] = {d: sorted(v) for d, v in days.items() if len(v) >= 70}
    if not out:
        raise LearnError("no charts in %s" % path)
    return out


def hhmm(text):
    t = entry.parse_hhmm(text, "time")
    return t.hour * 60 + t.minute


def r_target(text):
    t = str(text).strip().upper()
    if not t.endswith("R"):
        raise LearnError("the learner scores R targets only (got %r)" % text)
    return float(t[:-1])


def market_change(mkt_days, day, minute):
    """% change of the market symbol from its open to the close of the bar starting at minute."""
    bars = mkt_days.get(day) if mkt_days else None
    if not bars:
        return None
    for b in bars:
        if b[0] == minute:
            return 100.0 * (b[4] / bars[0][1] - 1)
    return None


# ---------------------------------------------------------------- the replay

def trade_day(bars, s, mkt_days, day, account=None):
    """One symbol-day under settings s -> a trade dict or None. Mirrors robinhood.decide."""
    orm = int(s["or_minutes"])
    orb = [b for b in bars if b[0] < OPEN_MIN + orm]
    if len(orb) < orm // 5:
        return None
    hi, lo = max(b[2] for b in orb), min(b[3] for b in orb)
    if hi <= lo:
        return None
    first, last = hhmm(s.get("no_entries_before", "09:45")), hhmm(s["no_entries_after"])
    for i, b in enumerate(bars):
        if b[0] < OPEN_MIN + orm:
            continue
        if b[0] + 5 > last:
            return None
        if b[4] <= hi:
            continue
        if b[0] + 5 < first:
            return None
        if s.get("market_filter"):
            chg = market_change(mkt_days, day, b[0])
            if chg is None or chg <= float(s.get("market_min_pct") or 0):
                return None
        px = b[4] * (1 + SLIP)
        stop = (hi + lo) / 2 if s["stop"] == "or-mid" else lo
        risk = px - stop
        if risk <= px * 0.0005:
            return None
        if account:
            max_loss = math.floor(account * float(s.get("max_loss_pct_per_trade", 1.0))) / 100.0
            if risk > max_loss or px > account * float(s.get("max_position_pct", 75)) / 100.0:
                return None
        target = px + r_target(s["target"]) * risk
        for m, o, h, l, c, v in bars[i + 1:]:
            if l <= stop:                       # the stop first when a bar touches both
                return {"r": (min(stop, o) * (1 - SLIP) - px) / risk, "exit": "stop", "entry_min": b[0] + 5}
            if h >= target:
                return {"r": (max(target, o) * (1 - SLIP) - px) / risk, "exit": "target", "entry_min": b[0] + 5}
            if m >= FLAT_MIN:
                return {"r": (c * (1 - SLIP) - px) / risk, "exit": "flat", "entry_min": b[0] + 5}
        return {"r": (bars[-1][4] * (1 - SLIP) - px) / risk, "exit": "flat", "entry_min": b[0] + 5}
    return None


def replay(data, s, account=None):
    mkt = data.get(str(s.get("market_filter") or "SPY").upper(), {})
    out = []
    for sym, days in sorted(data.items()):
        if sym in MARKET_SYMBOLS or (s.get("market_filter") and sym == str(s["market_filter"]).upper()):
            continue
        for d, bars in sorted(days.items()):
            t = trade_day(bars, s, mkt, d, account)
            if t:
                t.update(symbol=sym, day=d.isoformat())
                out.append(t)
    return out


def summarize(trades, blocks):
    """Mean R overall and in each block of days."""
    rs = [t["r"] for t in trades]
    per = []
    for blk in blocks:
        b = [t["r"] for t in trades if t["day"] in blk]
        per.append({"n": len(b), "mean_r": round(statistics.mean(b), 4) if b else None})
    n = len(rs)
    return {"n": n, "mean_r": round(statistics.mean(rs), 4) if rs else None,
            "se": round(statistics.pstdev(rs) / math.sqrt(n), 4) if n > 1 else None,
            "win_rate": round(sum(1 for r in rs if r > 0) / n, 3) if n else None,
            "blocks": per}


def label(s):
    if s.get("market_filter"):
        mp = float(s.get("market_min_pct") or 0)
        m = "%s over its open%s" % (s["market_filter"], " by %.1f%%" % mp if mp else "")
    else:
        m = "no market filter"
    return "OR%s, stop %s, target %s, entries to %s, %s" % (s["or_minutes"], s["stop"], s["target"],
                                                            s["no_entries_after"], m)


def candidates():
    keys = list(GRID)
    for vals in itertools.product(*(GRID[k] for k in keys)):
        for mf, mp in MARKET:
            s = dict(zip(keys, vals))
            s.update(market_filter=mf, market_min_pct=mp)
            yield s


def strategy_of(cfg_bot):
    c = dict(rh.RH_DEFAULTS)
    c.update(cfg_bot or {})
    return {k: c.get(k) for k in STRATEGY_KEYS}


def same(a, b):
    return all(str(a.get(k)) == str(b.get(k)) for k in STRATEGY_KEYS if not (k == "market_min_pct" and
                                                                             not a.get("market_filter")))


def has_edge(st):
    means = [b["mean_r"] for b in st["blocks"] if b["mean_r"] is not None]
    return (st["n"] >= MIN_TRADES and st["se"] is not None and st["mean_r"] > 2 * st["se"]
            and sum(1 for m in means if m > 0) >= BLOCKS - 1)


def walk_forward(rows, cur_row, nblocks):
    """The learning itself, tested: before each block from the 2nd on, pick the best setting on the blocks
    before it only, then score that pick on the block. Learning earns a change only if those out-of-sample
    picks beat the current setting on the same blocks."""
    steps, got, base = [], [], []
    for k in range(1, nblocks):
        def past(st):
            bl = st["blocks"][:k]
            n = sum(b["n"] for b in bl)
            if n < MIN_BLOCK * k or any(b["n"] < MIN_BLOCK for b in bl):
                return None
            return sum(b["n"] * b["mean_r"] for b in bl) / n
        scored = [(past(r["stats"]), r) for r in rows]
        scored = [x for x in scored if x[0] is not None]
        if not scored:
            continue
        _, pick = max(scored, key=lambda x: x[0])
        nxt, cur = pick["stats"]["blocks"][k], cur_row["stats"]["blocks"][k]
        if nxt["mean_r"] is None or cur["mean_r"] is None:
            continue
        steps.append({"block": k + 1, "picked": pick["label"], "picked_r": nxt["mean_r"], "current_r": cur["mean_r"]})
        got.append(nxt["mean_r"])
        base.append(cur["mean_r"])
    learned = round(statistics.mean(got), 4) if got else None
    current = round(statistics.mean(base), 4) if base else None
    return {"steps": steps, "learned_r": learned, "current_r": current,
            "learning_helps": bool(steps) and learned > current + MIN_LIFT / 2
            and 2 * sum(1 for a, b in zip(got, base) if a > b) > len(steps)}


def study(data, cfg_bot, account=None):
    days = sorted({d.isoformat() for v in data.values() for d in v})
    if len(days) < 20:
        raise LearnError("only %d days of bars: want at least 20 (60 is better)" % len(days))
    size = int(math.ceil(len(days) / float(BLOCKS)))
    blocks = [set(days[i:i + size]) for i in range(0, len(days), size)]
    base = {k: v for k, v in dict(rh.RH_DEFAULTS, **(cfg_bot or {})).items()}
    cur = strategy_of(cfg_bot)
    rows, cur_row = [], None
    pool = list(candidates())
    if not any(same(cur, s) for s in pool):
        pool.append(cur)
    for s in pool:
        st = summarize(replay(data, dict(base, **s), account), blocks)
        row = {"settings": s, "label": label(s), "stats": st}
        rows.append(row)
        if same(cur, s):
            cur_row = row
    cst = cur_row["stats"]

    def worst(st):
        ms = [b["mean_r"] for b in st["blocks"]]
        return min(m if m is not None else -9 for m in ms)

    passing = []
    for row in rows:
        st = row["stats"]
        if row is cur_row or st["n"] < MIN_TRADES or any(b["n"] < MIN_BLOCK for b in st["blocks"]):
            continue
        wins = sum(1 for a, b in zip(st["blocks"], cst["blocks"])
                   if a["mean_r"] is not None and (b["mean_r"] is None or a["mean_r"] > b["mean_r"]))
        lift = st["mean_r"] - (cst["mean_r"] if cst["mean_r"] is not None else 0)
        row["beats_blocks"], row["lift_r"] = wins, round(lift, 4)
        if wins >= BLOCKS - 1 and lift >= MIN_LIFT and worst(st) >= worst(cst):
            passing.append(row)
    passing.sort(key=lambda r: (worst(r["stats"]), r["stats"]["mean_r"]), reverse=True)
    wf = walk_forward(rows, cur_row, len(blocks))
    best = passing[0] if passing and wf["learning_helps"] else None
    keep = best or cur_row
    rows.sort(key=lambda r: (worst(r["stats"]) if r["stats"]["n"] >= MIN_TRADES else -9, r["stats"]["mean_r"] or -9),
              reverse=True)
    edge = has_edge(keep["stats"])
    if best:
        verdict = "change: %s beats the current setting in %d of %d blocks, %+.3fR a trade" % (
            best["label"], best["beats_blocks"], len(blocks), best["lift_r"])
    elif passing:
        verdict = ("keep the current setting: %s looks better in hindsight, but picking the best setting on past "
                   "days did not beat the current one on the days after (%+.3fR vs %+.3fR)"
                   % (passing[0]["label"], wf["learned_r"], wf["current_r"]))
    else:
        verdict = "keep the current setting: no candidate beat it in %d of %d blocks by %.2fR" % (
            BLOCKS - 1, len(blocks), MIN_LIFT)
    verdict += ". " + ("The kept setting shows an edge: %+.3fR a trade, %.1f standard errors over 0."
                       % (keep["stats"]["mean_r"], keep["stats"]["mean_r"] / keep["stats"]["se"]) if edge else
                       "No edge: the kept setting averages %+.3fR a trade, which is not reliably over 0."
                       % (keep["stats"]["mean_r"] or 0))
    syms = len([k for k in data if k not in MARKET_SYMBOLS])
    return {"data": {"symbols": syms, "days": len(days), "first": days[0], "last": days[-1],
                     "blocks": [[min(b), max(b)] for b in blocks], "account": account, "slippage_pct": SLIP * 100},
            "current": cur_row, "proposal": best, "edge": edge, "verdict": verdict, "walk_forward": wf,
            "top": rows[:10], "candidates": len(rows)}


# ---------------------------------------------------------------- the journal

def round_trips(snap):
    """Closed round trips per day and symbol from the bot's Robinhood orders."""
    days = sorted({entry.to_eastern(bot.parse_iso(o["created_at"])).date().isoformat()
                   for o in snap["orders"] if o.get("created_at")})
    out = []
    for day in days:
        for sym, L in sorted(rh.ledger(snap["orders"], day).items()):
            if not L["bought"] or L["sold"] + 1e-9 < L["bought"]:
                continue
            mine = [o for o in snap["orders"] if o["symbol"] == sym and o.get("created_at")
                    and entry.to_eastern(bot.parse_iso(o["created_at"])).date().isoformat() == day]
            stops = [o["stop_price"] for o in mine if o["side"] == "sell" and o["stop_price"]]
            fill = L["cost"] / L["bought"]
            pnl = L["proceeds"] - L["cost"]
            stop = stops[0] if stops else None
            r = pnl / (L["bought"] * (fill - stop)) if stop is not None and stop < fill else None
            by_stop = any(o["side"] == "sell" and o["filled"] and o["type"].startswith("stop") for o in mine)
            out.append({"date": day, "symbol": sym, "qty": L["bought"], "entry": round(fill, 4),
                        "exit": round(L["proceeds"] / L["sold"], 4), "stop": stop, "pnl": round(pnl, 2),
                        "r": None if r is None else round(r, 3), "exit_by": "stop" if by_stop else "market"})
    return out


def read_journal(path):
    rows = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
    except OSError:
        pass
    return rows


def add_to_journal(path, trips):
    have = {(r["date"], r["symbol"]) for r in read_journal(path)}
    new = [t for t in trips if (t["date"], t["symbol"]) not in have]
    if new:
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            for t in new:
                fh.write(json.dumps(t, sort_keys=True) + "\n")
    return new


def review(rows, report=None):
    rs = [r["r"] for r in rows if r.get("r") is not None]
    out = {"trades": len(rows), "pnl": round(sum(r["pnl"] for r in rows), 2),
           "wins": sum(1 for r in rows if r["pnl"] > 0), "with_r": len(rs),
           "mean_r": round(statistics.mean(rs), 3) if rs else None,
           "by_exit": {k: sum(1 for r in rows if r["exit_by"] == k) for k in ("stop", "market")}}
    exp = ((report or {}).get("proposal") or (report or {}).get("current") or {}).get("stats", {}).get("mean_r")
    out["backtest_mean_r"] = exp
    if len(rs) < 20:
        out["judgement"] = "%d live trades with an R: too few to judge (want 20)" % len(rs)
    elif exp is None:
        out["judgement"] = "no study report to compare with: run learn.py study --write"
    else:
        se = statistics.pstdev(rs) / math.sqrt(len(rs))
        if out["mean_r"] < exp - 2 * se:
            out["judgement"] = ("live is worse than the backtest (%+.3fR vs %+.3fR, more than 2 standard errors): "
                                "fills, timing or the scanner's picks differ from the replay. Stop and look."
                                % (out["mean_r"], exp))
        else:
            out["judgement"] = "live is in line with the backtest (%+.3fR vs %+.3fR)" % (out["mean_r"], exp)
    return out


# ---------------------------------------------------------------- apply

def apply(proposal_path, config_path):
    doc = bot.read_json(proposal_path, None)
    if not doc or not doc.get("settings"):
        raise LearnError("no proposal in %s: study found nothing that beats the current setting" % proposal_path)
    s = doc["settings"]
    bad = [k for k in s if k not in STRATEGY_KEYS]
    if bad:
        raise LearnError("the proposal touches %s: the learner may change only %s" % (", ".join(bad),
                                                                                      ", ".join(STRATEGY_KEYS)))
    cfg = bot.read_json(config_path, {}) or {}
    if os.path.exists(config_path):
        shutil.copyfile(config_path, config_path + ".bak")
    block = cfg.setdefault("robinhood", {})
    before = strategy_of(block)
    block.update(s)
    bot.write_json(config_path, cfg)
    return {k: [before.get(k), s[k]] for k in s if str(before.get(k)) != str(s[k])}


# ---------------------------------------------------------------- the CLI

def fetch(symbols, out):
    os.makedirs(out, exist_ok=True)
    got, failed = [], []
    for sym in sorted(set(s.upper() for s in symbols) | {"SPY"}):
        try:
            text = entry.yahoo_get(entry.chart_path(sym, "60d", "5m"))
            entry.parse_chart(text, sym)
        except entry.EntryError as e:
            failed.append("%s (%s)" % (sym, e))
            continue
        with open(os.path.join(out, sym + ".json"), "w", encoding="utf-8") as fh:
            fh.write(text if isinstance(text, str) else text.decode("utf-8"))
        got.append(sym)
    return got, failed


def print_study(rep):
    d = rep["data"]
    print("%d symbols, %d days (%s to %s), %d candidates, %.2f%% slippage each way"
          % (d["symbols"], d["days"], d["first"], d["last"], rep["candidates"], d["slippage_pct"]))
    print("blocks: " + ", ".join("%s..%s" % tuple(b) for b in d["blocks"]))

    def line(row):
        st = row["stats"]
        bl = " ".join("%+.2f" % b["mean_r"] if b["mean_r"] is not None else "  n/a" for b in st["blocks"])
        return "  %-72s n=%4d  %+.3fR  blocks %s" % (row["label"], st["n"], st["mean_r"] or 0, bl)

    print("\ncurrent:")
    print(line(rep["current"]))
    print("\nbest by the worst block:")
    for row in rep["top"][:5]:
        print(line(row))
    print("\nwalk-forward (best on the past blocks, scored on the next one):")
    for st in rep["walk_forward"]["steps"]:
        print("  block %d: picked %s -> %+.3fR (current %+.3fR)" % (st["block"], st["picked"], st["picked_r"],
                                                                   st["current_r"]))
    print("\n" + rep["verdict"])


def main(argv=None):
    ap = argparse.ArgumentParser(description="trader-bot's learner: backtest the settings, journal the trades")
    sub = ap.add_subparsers(dest="cmd")
    f = sub.add_parser("fetch", help="60 days of 5-minute bars from Yahoo, plus SPY")
    f.add_argument("--symbols", help="comma-separated")
    f.add_argument("--watch", help="a file with one symbol per line (or space separated)")
    f.add_argument("--out", default="trader/learn/data")
    s = sub.add_parser("study", help="score every candidate setting, propose a change only if it holds up")
    s.add_argument("--data", default="trader/learn/data")
    s.add_argument("--config", default="trader/config.json")
    s.add_argument("--account", type=float, help="skip trades this account could not size (1 share over the max loss)")
    s.add_argument("--write", action="store_true", help="save trader/learn/report.json and proposal.json")
    s.add_argument("--json", action="store_true")
    j = sub.add_parser("journal", help="record the closed round trips in a Robinhood snapshot")
    j.add_argument("--snapshot", default="trader/rh/snapshot.json")
    j.add_argument("--journal", default="trader/learn/journal.jsonl")
    r = sub.add_parser("review", help="the live journal against the backtest")
    r.add_argument("--journal", default="trader/learn/journal.jsonl")
    r.add_argument("--report", default="trader/learn/report.json")
    a2 = sub.add_parser("apply", help="write the proposal into config.json's robinhood block")
    a2.add_argument("--proposal", default="trader/learn/proposal.json")
    a2.add_argument("--config", default="trader/config.json")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "fetch":
            syms = [x for x in (a.symbols or "").replace(",", " ").split() if x]
            if a.watch:
                with open(a.watch, encoding="utf-8") as fh:
                    syms += fh.read().replace(",", " ").split()
            if not syms:
                raise LearnError("give --symbols or --watch")
            got, failed = fetch(syms, a.out)
            print("saved %d charts to %s%s" % (len(got), a.out, "; failed: " + ", ".join(failed) if failed else ""))
        elif a.cmd == "study":
            cfg = (bot.read_json(a.config, {}) or {}).get("robinhood") or {}
            rep = study(load_dir(a.data), cfg, a.account)
            if a.write:
                out = os.path.join("trader", "learn")
                os.makedirs(out, exist_ok=True)
                rep["written"] = entry.iso(entry.utc_now())
                bot.write_json(os.path.join(out, "report.json"), rep)
                pp = os.path.join(out, "proposal.json")
                if rep["proposal"]:
                    bot.write_json(pp, {"settings": rep["proposal"]["settings"], "label": rep["proposal"]["label"],
                                        "verdict": rep["verdict"], "data": rep["data"], "written": rep["written"]})
                elif os.path.exists(pp):
                    os.remove(pp)
            if a.json:
                print(json.dumps(rep, indent=2, default=str))
            else:
                print_study(rep)
        elif a.cmd == "journal":
            new = add_to_journal(a.journal, round_trips(rh.load_snapshot(a.snapshot)))
            print(json.dumps({"added": new, "journal": a.journal}, indent=2))
        elif a.cmd == "review":
            print(json.dumps(review(read_journal(a.journal), bot.read_json(a.report, None)), indent=2))
        elif a.cmd == "apply":
            changed = apply(a.proposal, a.config)
            print(json.dumps({"changed": changed, "config": a.config, "backup": a.config + ".bak"}, indent=2))
        else:
            ap.print_help()
            return 2
    except (LearnError, rh.RHError, entry.EntryError, OSError) as e:
        print("learn: %s" % e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
