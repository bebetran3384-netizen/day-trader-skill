"""trader-lessons: every module parses, quizzes are well formed, grading remembers,
and the examples work offline (the reel's trade) and on saved charts."""

import json
import os
import shutil
import tempfile
import unittest

from _load import load
import test_learn

lessons = load("trader-lessons", "lessons")


class Modules(unittest.TestCase):
    def test_six_modules_each_with_sections_a_quiz_and_an_example(self):
        mods = lessons.modules()
        self.assertEqual(len(mods), 6)
        for m in mods:
            self.assertGreaterEqual(len(m["sections"]), 3, m["title"])
            self.assertGreaterEqual(len(m["quiz"]), 3, m["title"])
            self.assertIn(m["example"], ("orb", "r", "size", "both", "backtest", "streaks"), m["title"])
            self.assertTrue(m["minutes"])
            for q in m["quiz"]:
                self.assertTrue(1 <= q["answer"] <= len(q["choices"]), q["question"])
                self.assertTrue(q["why"], q["question"])

    def test_show_hides_the_answers(self):
        text = json.dumps(lessons.public(lessons.get(1)))
        self.assertNotIn('"answer"', text)
        self.assertNotIn('"why"', text)

    def test_a_bad_module_number_is_an_error(self):
        for n in ("0", "7", "x"):
            with self.assertRaises(lessons.LessonError):
                lessons.get(n)

    def test_a_question_without_a_right_answer_is_rejected(self):
        with self.assertRaises(lessons.LessonError):
            lessons.parse_module("# T\n\n## A\n\nx\n\n## Quiz\n\n1. Q?\n   - [ ] a\n   - [ ] b\n")


class Grading(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.path = os.path.join(self.tmp, "p", "progress.json")

    def test_all_right_passes_and_is_remembered(self):
        m = lessons.get(2)
        res = lessons.grade(m, [q["answer"] for q in m["quiz"]], self.path)
        self.assertEqual((res["score"], res["passed"]), (len(m["quiz"]), True))
        self.assertEqual(lessons.read_progress(self.path)["2"]["best"], len(m["quiz"]))

    def test_a_miss_comes_back_with_the_why_and_a_later_worse_score_keeps_the_best(self):
        m = lessons.get(1)
        good = [q["answer"] for q in m["quiz"]]
        lessons.grade(m, good, self.path)
        bad = [1 if a != 1 else 2 for a in good]
        res = lessons.grade(m, bad, self.path)
        self.assertEqual(res["score"], 0)
        self.assertTrue(all(r["why"] for r in res["results"]))
        p = lessons.read_progress(self.path)["1"]
        self.assertEqual((p["score"], p["best"], p["passed"]), (0, len(good), True))

    def test_the_wrong_number_of_answers_is_an_error(self):
        with self.assertRaises(lessons.LessonError):
            lessons.grade(lessons.get(1), [1], self.path)


class Examples(test_learn.Base):
    def test_offline_examples_use_the_reels_trade(self):
        ex = lessons.example(1)
        self.assertEqual(ex["source"], "the reel's trade")
        self.assertEqual(ex["case"]["symbol"], "TSLA")
        self.assertEqual(ex["r_per_share"], 5.86)

    def test_sizing_on_660(self):
        ex = lessons.example(3, account=660)
        self.assertEqual((ex["max_loss"], ex["shares"], ex["risk"]), (6.6, 1, 5.86))
        self.assertEqual(lessons.example(3, account=500)["skipped"], True)   # 5.00 max loss < 5.86

    def test_examples_on_saved_charts(self):
        days = test_learn.sessions(4)
        self.write("AAA", [(days[0], "flat"), (days[1], "loss"), (days[2], "win"), (days[3], "win")])
        self.write("SPY", [(d, "up") for d in days])
        ex = lessons.example(1, self.data)
        self.assertEqual((ex["source"], ex["case"]["exit"], ex["case"]["date"]),
                         ("saved charts", "target", days[2].isoformat()))
        self.assertIn("range high", ex["chart"])
        self.assertEqual(lessons.example(2, self.data)["case"]["exit"], "stop")
        bt = lessons.example(5, self.data)
        self.assertEqual([s["n"] for s in bt["settings"]], [3, 3])

    def test_module_5_needs_data(self):
        with self.assertRaises(lessons.LessonError):
            lessons.example(5)

    def test_losing_streaks_are_long_at_45_percent(self):
        ex = lessons.example(6)
        self.assertGreaterEqual(ex["median_longest_losing_streak"], 7)
        self.assertGreater(ex["share_with_7_or_more"], 0.5)


if __name__ == "__main__":
    unittest.main()
