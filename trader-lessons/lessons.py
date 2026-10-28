#!/usr/bin/env python3
"""Lessons for trader-lessons. Standard library only, no network.

Six short modules on the trade the bot makes and on reading its numbers, each
with a worked example on real 5-minute bars and a quiz that is graded and
remembered.

    python3 lessons.py list                         # the modules and your scores
    python3 lessons.py show 1                       # module 1 as JSON: title, sections, quiz (no answers)
    python3 lessons.py example 1 --data trader/learn/data   # a real case from saved Yahoo charts
    python3 lessons.py example 3 --account 660      # sizing on your account
    python3 lessons.py grade 1 --answers 2,2,2,1    # 1-based choices, one per question
    python3 lessons.py progress

Without --data the examples use the reel's trade (TSLA, October 19 2026).
Progress lives in trader/lessons/progress.json. Not financial advice.
"""

import argparse
import datetime
import json
import math
import os
import random
import re
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MODULES = os.path.join(HERE, "modules")
ROOT = os.path.dirname(HERE)
PROGRESS = os.path.join("trader", "lessons", "progress.json")
REEL = {"symbol": "TSLA", "date": "2026-10-19", "or_high": 362.18, "or_low": 354.47, "trigger_time": "10:10",
        "entry": 364.18, "stop": 358.32, "target": 370.04}


class LessonError(Exception):
    pass


def _learn():
    import importlib.util
    path = os.path.join(ROOT, "trader-bot", "learn.py")
    spec = importlib.util.spec_from_file_location("trader_learn", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------- modules

def parse_module(text):
    lines = text.splitlines()
    if not lines or not lines[0].startswith("# "):
        raise LessonError("a module starts with '# Title'")
    mod = {"title": lines[0][2:].strip(), "minutes": None, "example": None, "sections": [], "quiz": []}
    cur, in_quiz, q = None, False, None
    for line in lines[1:]:
        m = re.match(r"^(Minutes|Example):\s*(\S+)", line)
        if m and cur is None:
            mod[m.group(1).lower()] = int(m.group(2)) if m.group(1) == "Minutes" else m.group(2)
            continue
        if line.startswith("## "):
            in_quiz = line[3:].strip().lower() == "quiz"
            cur = None if in_quiz else {"heading": line[3:].strip(), "body": []}
            if cur:
                mod["sections"].append(cur)
            continue
        if in_quiz:
            m = re.match(r"^\d+\.\s+(.*)", line)
            if m:
                q = {"question": m.group(1).strip(), "choices": [], "answer": None, "why": ""}
                mod["quiz"].append(q)
                continue
            m = re.match(r"^\s+- \[( |x)\]\s+(.*)", line)
            if m and q is not None:
                if m.group(1) == "x":
                    q["answer"] = len(q["choices"]) + 1
                q["choices"].append(m.group(2).strip())
                continue
            m = re.match(r"^\s+>\s*Why:\s*(.*)", line)
            if m and q is not None:
                q["why"] = m.group(1).strip()
            continue
        if cur is not None:
            cur["body"].append(line)
    for s in mod["sections"]:
        s["body"] = "\n".join(s["body"]).strip()
    for q in mod["quiz"]:
        if q["answer"] is None or len(q["choices"]) < 2:
            raise LessonError("%s: a quiz question needs 2+ choices and one [x]" % mod["title"])
    return mod


def modules():
    out = []
    for f in sorted(os.listdir(MODULES)):
        if f.endswith(".md"):
            with open(os.path.join(MODULES, f), encoding="utf-8") as fh:
                m = parse_module(fh.read())
            m["id"] = len(out) + 1
            m["file"] = f
            out.append(m)
    return out


def get(n):
    mods = modules()
    try:
        n = int(n)
    except (TypeError, ValueError):
        raise LessonError("a module is a number, 1 to %d" % len(mods))
    if not 1 <= n <= len(mods):
        raise LessonError("a module is a number, 1 to %d" % len(mods))
    return mods[n - 1]


def public(mod):
    """The module without the answers, for showing before the quiz."""
    return {"id": mod["id"], "title": mod["title"], "minutes": mod["minutes"], "sections": mod["sections"],
            "quiz": [{"question": q["question"], "choices": q["choices"]} for q in mod["quiz"]]}


# ---------------------------------------------------------------- progress

def read_progress(path=PROGRESS):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def grade(mod, answers, path=PROGRESS, today=None):
    if len(answers) != len(mod["quiz"]):
        raise LessonError("module %d has %d questions, got %d answers" % (mod["id"], len(mod["quiz"]), len(answers)))
    res = []
    for q, a in zip(mod["quiz"], answers):
        res.append({"question": q["question"], "yours": a, "answer": q["answer"], "right": a == q["answer"],
                    "correct_choice": q["choices"][q["answer"] - 1], "why": q["why"]})
    score = sum(1 for r in res if r["right"])
    prog = read_progress(path)
    prev = prog.get(str(mod["id"])) or {}
    prog[str(mod["id"])] = {"title": mod["title"], "score": score, "total": len(res),
                            "best": max(score, prev.get("best", 0)),
                            "date": (today or datetime.date.today()).isoformat(),
                            "passed": prev.get("passed", False) or score * 4 >= len(res) * 3}
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(prog, fh, indent=2, sort_keys=True)
    return {"module": mod["id"], "score": score, "total": len(res), "passed": prog[str(mod["id"])]["passed"],
            "results": res}


# ---------------------------------------------------------------- examples

def find_orb(data, learn, want=None):
    """A real breakout from the saved charts: want = 'target', 'stop', 'both' (a bar touching stop and target)."""
    s = dict(learn.rh.RH_DEFAULTS, market_filter=None, no_entries_after="15:30")
    for sym, days in sorted(data.items()):
        if sym in learn.MARKET_SYMBOLS:
            continue
        for d, bars in sorted(days.items()):
            t = learn.trade_day(bars, s, {}, d)
            if not t:
                continue
            orb = [b for b in bars if b[0] < 585]
            hi, lo = max(b[2] for b in orb), min(b[3] for b in orb)
            i = next(k for k, b in enumerate(bars) if b[0] + 5 == t["entry_min"])
            px = bars[i][4]
            stop = (hi + lo) / 2
            target = px + (px - stop)
            hit = next((b for b in bars[i + 1:] if b[3] <= stop or b[2] >= target), None)
            both = bool(hit) and hit[3] <= stop and hit[2] >= target
            if want == "both" and not both:
                continue
            if want in ("target", "stop") and t["exit"] != want:
                continue
            return {"symbol": sym, "date": d.isoformat(), "or_high": round(hi, 2), "or_low": round(lo, 2),
                    "trigger_time": "%02d:%02d" % divmod(t["entry_min"], 60), "entry": round(px, 2),
                    "stop": round(stop, 2), "target": round(target, 2), "exit": t["exit"], "r": round(t["r"], 2),
                    "bars": bars}
    return None


def sparkline(case, width=60, height=16):
    bars = case.get("bars")
    if not bars:
        return None
    step = max(1, int(math.ceil(len(bars) / float(width))))
    cols = [bars[k:k + step] for k in range(0, len(bars), step)]
    lo = min(min(b[3] for b in bars), case["stop"])
    hi = max(max(b[2] for b in bars), case["target"])
    span = hi - lo or 1

    def row(p):
        return int(round((hi - p) / span * (height - 1)))

    grid = [[" "] * len(cols) for _ in range(height)]
    for name, p in (("-", case["or_high"]), ("-", case["or_low"]), (".", case["stop"]), ("~", case["target"])):
        for c in range(len(cols)):
            grid[row(p)][c] = name
    for c, grp in enumerate(cols):
        grid[row(grp[-1][4])][c] = "*"
    labels = {row(case["target"]): "target %.2f" % case["target"], row(case["or_high"]): "range high %.2f" % case["or_high"],
              row(case["stop"]): "stop (mid) %.2f" % case["stop"], row(case["or_low"]): "range low %.2f" % case["or_low"]}
    return "\n".join("".join(g) + ("  " + labels[k] if k in labels else "") for k, g in enumerate(grid))


def example(n, data_dir=None, account=660.0, seed=7):
    mod = get(n)
    kind = mod["example"]
    learn = _learn()
    data = learn.load_dir(data_dir) if data_dir else None
    out = {"module": mod["id"], "kind": kind}
    if kind in ("orb", "r", "size", "both"):
        want = {"orb": "target", "r": "stop", "both": "both"}.get(kind)
        case = find_orb(data, learn, want) if data else None
        src = "saved charts" if case else "the reel's trade"
        case = case or dict(REEL)
        rps = case["entry"] - case["stop"]
        out.update(source=src, case={k: v for k, v in case.items() if k != "bars"}, chart=sparkline(case))
        out["r_per_share"] = round(rps, 2)
        if kind == "r" and "r" in case:
            out["result_r"] = case["r"]
        if kind == "size":
            max_loss = math.floor(account) / 100.0 if account else None
            shares = int(max_loss // rps) if rps > 0 else 0
            cap = account * 0.75
            while shares and shares * case["entry"] > cap:
                shares -= 1
            out.update(account=account, max_loss=round(max_loss, 2), shares=shares,
                       cost=round(shares * case["entry"], 2), risk=round(shares * rps, 2),
                       skipped=shares == 0)
    elif kind == "backtest":
        if not data:
            raise LessonError("module 5's example replays saved charts: give --data (learn.py fetch saves them)")
        for name, s in (("plain ORB, entries all day", dict(market_filter=None, no_entries_after="15:30")),
                        ("the bot now (SPY filter, 11:30 cutoff)", {})):
            trades = learn.replay(data, dict(learn.rh.RH_DEFAULTS, **s))
            days = sorted({t["day"] for t in trades})
            half = days[len(days) // 2] if days else ""
            st = []
            for part in ([t for t in trades if t["day"] < half], [t for t in trades if t["day"] >= half]):
                rs = [t["r"] for t in part]
                st.append({"n": len(rs), "mean_r": round(statistics.mean(rs), 3) if rs else None})
            rs = [t["r"] for t in trades]
            out.setdefault("settings", []).append({
                "name": name, "n": len(rs), "mean_r": round(statistics.mean(rs), 3) if rs else None,
                "se": round(statistics.pstdev(rs) / math.sqrt(len(rs)), 3) if len(rs) > 1 else None,
                "win_rate": round(sum(1 for r in rs if r > 0) / float(len(rs)), 3) if rs else None,
                "halves": st})
    elif kind == "streaks":
        rnd = random.Random(seed)
        runs = []
        for _ in range(2000):
            longest = cur = 0
            for _ in range(300):
                cur = cur + 1 if rnd.random() < 0.55 else 0
                longest = max(longest, cur)
            runs.append(longest)
        runs.sort()
        out.update(win_rate=0.45, trades=300, simulations=2000, median_longest_losing_streak=runs[len(runs) // 2],
                   share_with_7_or_more=round(sum(1 for r in runs if r >= 7) / 2000.0, 3),
                   p95_longest=runs[int(0.95 * len(runs))])
    return out


# ---------------------------------------------------------------- the CLI

def main(argv=None):
    ap = argparse.ArgumentParser(description="trader-lessons: modules, examples on real bars, graded quizzes")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("list")
    s = sub.add_parser("show")
    s.add_argument("module")
    e = sub.add_parser("example")
    e.add_argument("module")
    e.add_argument("--data", help="a folder of saved 5-minute Yahoo charts (trader-bot/learn.py fetch)")
    e.add_argument("--account", type=float, default=660.0)
    g = sub.add_parser("grade")
    g.add_argument("module")
    g.add_argument("--answers", required=True, help="1-based choices, comma separated")
    g.add_argument("--progress", default=PROGRESS)
    p = sub.add_parser("progress")
    p.add_argument("--progress", default=PROGRESS)
    a = ap.parse_args(argv)
    try:
        if a.cmd == "list":
            prog = read_progress()
            for m in modules():
                pr = prog.get(str(m["id"]))
                mark = "  best %d/%d%s" % (pr["best"], pr["total"], ", passed" if pr["passed"] else "") if pr else ""
                print("%d. %s (%s min, %d questions)%s" % (m["id"], m["title"], m["minutes"], len(m["quiz"]), mark))
        elif a.cmd == "show":
            print(json.dumps(public(get(a.module)), indent=2))
        elif a.cmd == "example":
            print(json.dumps(example(a.module, a.data, a.account), indent=2))
        elif a.cmd == "grade":
            try:
                answers = [int(x) for x in a.answers.replace(" ", "").split(",") if x]
            except ValueError:
                raise LessonError("--answers is numbers like 2,1,3")
            print(json.dumps(grade(get(a.module), answers, a.progress), indent=2))
        elif a.cmd == "progress":
            print(json.dumps(read_progress(a.progress), indent=2))
        else:
            ap.print_help()
            return 2
    except LessonError as e:
        print("lessons: %s" % e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
