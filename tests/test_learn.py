import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from _load import ROOT, load

learn = load("trader-learn", "learn")


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = learn.main(list(argv))
    return code, out.getvalue(), err.getvalue()


class Lessons(unittest.TestCase):
    def setUp(self):
        self.lessons = learn.load_lessons(learn.LESSONS)

    def test_ten_lessons_in_order_with_three_questions(self):
        self.assertEqual([les["id"] for les in self.lessons], list(range(1, 11)))
        self.assertEqual(len({les["slug"] for les in self.lessons}), 10)
        for les in self.lessons:
            self.assertEqual(len(les["quiz"]), 3, les["slug"])
            self.assertGreaterEqual(len(les["points"]), 3, les["slug"])
            self.assertTrue(les["example"] and les["try"], les["slug"])
            for q in les["quiz"]:
                self.assertEqual(len(q["choices"]), 4, q["q"])
                self.assertTrue(q["why"], q["q"])

    def test_every_try_step_names_a_skill_of_the_pack(self):
        skills = {d for d in os.listdir(ROOT) if os.path.isfile(os.path.join(ROOT, d, "SKILL.md"))}
        for les in self.lessons:
            named = [s for s in skills if "/" + s in " ".join(les["try"])]
            self.assertTrue(named, les["slug"])

    def test_the_worked_numbers_add_up(self):
        # the reel: range mid rounded down, R, 1R target, shares and the loss at the stop
        self.assertEqual(int((354.47 + 362.18) / 2 * 100) / 100, 358.32)
        self.assertEqual(round(364.18 - 358.32, 2), 5.86)
        self.assertEqual(round(364.18 + 5.86, 2), 370.04)
        self.assertEqual(int(50 // 5.86), 8)
        self.assertEqual(round(8 * 5.86, 2), 46.88)
        # lesson 7 question 1 and lesson 9 question 1
        self.assertEqual(int(10 // round(40.00 - 39.20, 2)), 12)
        self.assertAlmostEqual(0.55 * 1 - 0.45 * 1, 0.10)

    def test_a_bad_answer_key_is_refused(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        with open(learn.LESSONS) as fh:
            data = json.load(fh)
        data["lessons"][0]["quiz"][0]["answer"] = "e"
        path = os.path.join(tmp, "bad.json")
        with open(path, "w") as fh:
            json.dump(data, fh)
        code, _, err = run("list", "--lessons", path, "--state", os.path.join(tmp, "s.json"))
        self.assertEqual(code, 2)
        self.assertIn("not one of the choices", err)


class Cli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.state = os.path.join(self.tmp, "trader", "state", "learn.json")

    def cli(self, *argv):
        return run(*(list(argv) + ["--state", self.state, "--now", "2026-10-29T12:00:00"]))

    def test_pass_records_and_points_to_the_next_lesson(self):
        code, out, _ = self.cli("answer", "7", "aba")
        self.assertEqual(code, 0)
        self.assertIn("Lesson 7: 2 of 3 right.", out)
        self.assertIn("3. a wrong, it is b) $10.", out)
        self.assertIn("Next: python3 learn.py lesson 8", out)
        with open(self.state) as fh:
            rec = json.load(fh)["lessons"]["7"]
        self.assertEqual((rec["attempts"], rec["best"], rec["passed"]), (1, 2, True))

    def test_a_fail_keeps_a_pass_and_exits_one(self):
        self.cli("answer", "7", "abb")
        code, out, _ = self.cli("answer", "7", "ccc")
        self.assertEqual(code, 1)
        self.assertIn("Not passed yet", out)
        with open(self.state) as fh:
            rec = json.load(fh)["lessons"]["7"]
        self.assertEqual((rec["attempts"], rec["best"], rec["passed"]), (2, 3, True))

    def test_next_and_progress(self):
        self.cli("answer", "1", "bcb")
        code, out, _ = self.cli("next")
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("Lesson 2: Candles, volume and the opening range"))
        _, out, _ = self.cli("progress")
        self.assertIn("1 of 10 lessons passed.", out)
        self.assertIn("[x]  1.", out)

    def test_lesson_by_name_and_quiz_hides_the_answers(self):
        code, out, _ = self.cli("lesson", "exit")
        self.assertEqual(code, 0)
        self.assertIn("Stops, targets and R", out)
        code, out, _ = self.cli("quiz", "6", "--json")
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(len(data["quiz"]), 3)
        self.assertNotIn("answer", json.dumps(data))

    def test_bad_input(self):
        self.assertEqual(self.cli("answer", "7", "ab")[0], 2)
        self.assertEqual(self.cli("answer", "7", "abz")[0], 2)
        self.assertEqual(self.cli("lesson", "99")[0], 2)
        self.assertEqual(self.cli("quiz")[0], 2)

    def test_reset(self):
        self.cli("answer", "1", "bcb")
        self.assertEqual(self.cli("reset")[0], 0)
        _, out, _ = self.cli("progress")
        self.assertIn("0 of 10 lessons passed.", out)


if __name__ == "__main__":
    unittest.main()
