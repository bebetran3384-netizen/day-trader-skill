#!/usr/bin/env python3
"""Learning modules for trader-learn. Standard library only.

    python3 learn.py list                      # the ten lessons and where you are
    python3 learn.py lesson 6                  # one lesson: key points, a real example, what to try
    python3 learn.py quiz 6                    # its three questions
    python3 learn.py answer 6 bab              # grade your answers, with the reason for each
    python3 learn.py next                      # the first lesson you have not passed
    python3 learn.py progress                  # lessons passed, best scores, attempts
    python3 learn.py reset                     # start over

A lesson is passed with 2 of 3 right. Progress lives in trader/state/learn.json
(--state). The lessons live in lessons.json next to this file (--lessons), so
they can be edited without touching the code. --json prints machine-readable
output for every command.

Exit codes: 0 done, 1 answered but not passed, 2 bad input.
"""

import argparse
import datetime
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LESSONS = os.path.join(HERE, "lessons.json")
STATE = os.path.join("trader", "state", "learn.json")
PASS_MARK = 2
LETTERS = "abcd"


class BadInput(Exception):
    pass


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return default


def write_json(path, data):
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


def load_lessons(path):
    data = read_json(path)
    if not data or not data.get("lessons"):
        raise BadInput("no lessons in %s" % path)
    for les in data["lessons"]:
        for q in les["quiz"]:
            if q["answer"] not in LETTERS[:len(q["choices"])]:
                raise BadInput("lesson %s: answer %r is not one of the choices" % (les["id"], q["answer"]))
    return data["lessons"]


def find(lessons, ref):
    for les in lessons:
        if str(les["id"]) == str(ref) or les["slug"] == str(ref).lower():
            return les
    raise BadInput("no lesson %r: pick 1 to %d (python3 learn.py list)" % (ref, len(lessons)))


def load_state(path):
    st = read_json(path, None) or {}
    st.setdefault("lessons", {})
    return st


def passed(st, les):
    return bool(st["lessons"].get(str(les["id"]), {}).get("passed"))


def grade(les, answers):
    """answers: 'bab' or 'b,a,b' -> (score, [per question dict])."""
    picks = [c for c in answers.lower() if c.isalpha()]
    if len(picks) != len(les["quiz"]):
        raise BadInput("lesson %s has %d questions: give %d letters, like %s"
                       % (les["id"], len(les["quiz"]), len(les["quiz"]), "a" * len(les["quiz"])))
    rows = []
    for i, (q, pick) in enumerate(zip(les["quiz"], picks), 1):
        if pick not in LETTERS[:len(q["choices"])]:
            raise BadInput("question %d: %r is not a choice (a to %s)" % (i, pick, LETTERS[len(q["choices"]) - 1]))
        rows.append({"n": i, "pick": pick, "answer": q["answer"], "right": pick == q["answer"],
                     "correct_choice": q["choices"][LETTERS.index(q["answer"])], "why": q["why"]})
    return sum(r["right"] for r in rows), rows


def record(st, les, score, now):
    rec = st["lessons"].setdefault(str(les["id"]), {"attempts": 0, "best": 0, "passed": False})
    rec["attempts"] += 1
    rec["best"] = max(rec["best"], score)
    rec["last"] = now
    if score >= PASS_MARK and not rec["passed"]:
        rec["passed"] = True
        rec["passed_at"] = now
    return rec


def next_lesson(lessons, st, after=None):
    """The first lesson not passed, looking after lesson `after` first and then from the top."""
    later = [les for les in lessons if after is not None and les["id"] > after]
    for les in later + lessons:
        if not passed(st, les):
            return les
    return None


def show_lesson(les):
    out = ["Lesson %d: %s  (about %d minutes)" % (les["id"], les["title"], les["minutes"]), ""]
    out += ["  - " + p for p in les["points"]]
    out += ["", "Real example: " + les["example"], "", "Try it:"]
    out += ["  " + t for t in les["try"]]
    out += ["", "Then: python3 learn.py quiz %d" % les["id"]]
    return "\n".join(out)


def show_quiz(les):
    out = ["Quiz, lesson %d: %s" % (les["id"], les["title"])]
    for i, q in enumerate(les["quiz"], 1):
        out += ["", "%d. %s" % (i, q["q"])]
        out += ["   %s) %s" % (LETTERS[j], c) for j, c in enumerate(q["choices"])]
    out += ["", "Answer with: python3 learn.py answer %d %s" % (les["id"], "?" * len(les["quiz"]))]
    return "\n".join(out)


def show_progress(lessons, st):
    done = sum(passed(st, les) for les in lessons)
    out = ["%d of %d lessons passed." % (done, len(lessons))]
    for les in lessons:
        rec = st["lessons"].get(str(les["id"]))
        mark = "x" if passed(st, les) else " "
        tail = ("best %d/%d, %d attempt%s" % (rec["best"], len(les["quiz"]), rec["attempts"],
                                              "" if rec["attempts"] == 1 else "s")) if rec else "not started"
        out.append("  [%s] %2d. %-52s %s" % (mark, les["id"], les["title"], tail))
    nxt = next_lesson(lessons, st)
    out.append("")
    out.append("Next: python3 learn.py lesson %d" % nxt["id"] if nxt else
               "All lessons passed. Keep practising on paper: knowing the rules is not the same as an edge.")
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Learning modules for trader-learn. Standard library only.")
    ap.add_argument("command", choices=["list", "lesson", "quiz", "answer", "next", "progress", "reset"])
    ap.add_argument("lesson", nargs="?", help="lesson number or name")
    ap.add_argument("answers", nargs="?", help="your letters, like bab")
    ap.add_argument("--lessons", default=LESSONS)
    ap.add_argument("--state", default=STATE)
    ap.add_argument("--now", help="ISO time, for reproducible runs")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    now = a.now or datetime.datetime.now().replace(microsecond=0).isoformat()

    def emit(text, data):
        print(json.dumps(data, indent=2) if a.json else text)

    try:
        lessons = load_lessons(a.lessons)
        st = load_state(a.state)
        if a.command in ("list", "progress"):
            emit(show_progress(lessons, st), {"lessons": [
                dict({"id": les["id"], "title": les["title"]}, **st["lessons"].get(str(les["id"]), {}))
                for les in lessons]})
            return 0
        if a.command == "reset":
            write_json(a.state, {"lessons": {}})
            emit("Progress cleared.", {"reset": True})
            return 0
        if a.command == "next":
            les = next_lesson(lessons, st)
            if les is None:
                emit(show_progress(lessons, st), {"next": None})
                return 0
            emit(show_lesson(les), les)
            return 0
        if not a.lesson:
            raise BadInput("which lesson? python3 learn.py %s 1" % a.command)
        les = find(lessons, a.lesson)
        if a.command == "lesson":
            emit(show_lesson(les), les)
            return 0
        if a.command == "quiz":
            emit(show_quiz(les), {"id": les["id"], "quiz": [{"q": q["q"], "choices": q["choices"]}
                                                           for q in les["quiz"]]})
            return 0
        if not a.answers:
            raise BadInput("give your letters: python3 learn.py answer %d %s" % (les["id"], "a" * len(les["quiz"])))
        score, rows = grade(les, a.answers)
        rec = record(st, les, score, now)
        write_json(a.state, st)
        out = ["Lesson %d: %d of %d right." % (les["id"], score, len(rows))]
        for r in rows:
            out.append("  %d. %s %s  %s" % (r["n"], r["pick"], "right" if r["right"] else
                                            "wrong, it is %s) %s." % (r["answer"], r["correct_choice"]), r["why"]))
        if score >= PASS_MARK:
            nxt = next_lesson(lessons, st, after=les["id"])
            out.append("Passed." + (" Next: python3 learn.py lesson %d" % nxt["id"] if nxt else
                                    " That was the last one: all lessons passed."))
        else:
            out.append("Not passed yet (%d of %d needed). Re-read: python3 learn.py lesson %d"
                       % (PASS_MARK, len(rows), les["id"]))
        emit("\n".join(out), {"id": les["id"], "score": score, "results": rows, "record": rec})
        return 0 if score >= PASS_MARK else 1
    except BadInput as e:
        print("trader-learn: %s" % e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
