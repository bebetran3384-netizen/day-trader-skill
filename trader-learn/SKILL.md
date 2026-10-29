---
name: trader-learn
description: >-
  Learning Modules. Teaches day trading the way this pack does it, in ten
  short lessons that follow the chain from scan to order: the honest odds,
  candles and the opening range, setups, news and catalysts, timing the
  entry, stops, targets and R, position sizing, daily limits, odds and
  expectancy, and orders, brokers, the bot and keeping API keys safe. Each
  lesson has key points, a real worked example (the TSLA reel trade and the
  user's own paper trades), one thing to try with the matching skill, and a
  three-question quiz that is graded with the reason for every answer.
  Tracks progress in trader/state/learn.json. Use when the user says "teach
  me", "learning modules", "lesson", "quiz me", "I'm new to trading",
  "explain R", "what is a stop", "how does position sizing work", "what is an
  opening range", "next lesson", "my progress", or seems unsure what a skill's
  output means.
argument-hint: "[list | lesson N | quiz N | answer N abc | next | progress | reset]"
---

# trader-learn

The Learning Modules teach the method behind the other twelve skills, so
their output makes sense before any money depends on it. Ten lessons, about
ten minutes each, in the order a trade happens. Every lesson ends with
something to try in the real tool and a short quiz. Knowing the rules is not
an edge: most people who day trade lose money, and finishing the lessons
does not change that.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/learn.py" list                  # the lessons and where you are
python3 "${CLAUDE_SKILL_DIR}/learn.py" next                  # the first lesson not passed yet
python3 "${CLAUDE_SKILL_DIR}/learn.py" lesson 6              # or by name: lesson exit
python3 "${CLAUDE_SKILL_DIR}/learn.py" quiz 6
python3 "${CLAUDE_SKILL_DIR}/learn.py" answer 6 bab          # graded, with the reason for each
python3 "${CLAUDE_SKILL_DIR}/learn.py" progress
python3 "${CLAUDE_SKILL_DIR}/learn.py" reset
```

Flags: `--state FILE` (default `trader/state/learn.json`), `--lessons FILE`
(default `lessons.json` in this folder), `--json`, `--now` (ISO time, for
reproducible runs). Exit code 0 when done, 1 when a quiz was answered but not
passed, 2 on bad input.

## The ten lessons

| # | name | lesson | try it with |
| --- | --- | --- | --- |
| 1 | `odds` | What day trading is, and the honest odds | `/trader-alerts` (is the market open) |
| 2 | `bars` | Candles, volume and the opening range | `/trader-entry` |
| 3 | `setups` | Setups: what the scanner looks for | `/trader-scanner` |
| 4 | `news` | News and catalysts | `/trader-news` |
| 5 | `entry` | Timing the entry, and not chasing | `/trader-entry` |
| 6 | `exit` | Stops, targets and R | `/trader-exit` |
| 7 | `sizing` | Position sizing: shares from your max loss | `/trader-budget` |
| 8 | `risk` | Daily limits: the rules that keep you in the game | `/trader-risk` |
| 9 | `odds-math` | Odds, expectancy and why one day proves nothing | `/trader-timemachine`, `/trader-bot` replay |
| 10 | `orders` | Orders, brokers, the bot and keeping keys safe | `/trader-broker`, `/trader-bot` |

A lesson is passed with 2 of 3 right. The lessons live in `lessons.json`, so
they can be edited or extended without touching the code; `learn.py`
refuses a file whose answer key does not match its choices.

## How Claude teaches with it

1. Start where the user is: `learn.py progress`, or `learn.py next` for a
   new learner. A user who asks one question ("what is R?") gets that lesson
   (`lesson exit`), not the whole course.
2. Teach the lesson in your own words, short, from its key points. Use the
   real example. If `trader/` holds the user's own plan, entries or bot log,
   use their numbers too ("your ENPH stop was 0.54 below the entry, so...").
3. Offer the "try it" step with the matching skill, during market hours when
   it needs live bars. Never turn a lesson into a trade: an exercise is a
   look, never an order.
4. Give the quiz one question at a time if the user prefers, then grade it
   with `learn.py answer N abc` so progress is recorded. Explain every wrong
   answer with its reason; do not just print the letter.
5. Not passed: re-teach the missed points a different way, then let them try
   again. Passed: offer the next lesson.
6. Arithmetic questions (R, shares, expectancy): show the working, step by
   step, with round numbers first.

## What it reads and writes

| file | what |
| --- | --- |
| `lessons.json` (this folder) | the ten lessons: key points, example, try it, quiz with answers and reasons |
| `trader/state/learn.json` | per lesson: attempts, best score, passed, when |
| `trader/plan.json`, `trader/state/bot.log` | read by Claude, if present, for the user's own examples |

## Example

```
$ python3 learn.py answer 7 aba
Lesson 7: 2 of 3 right.
  1. a right  R = 0.80. 10 / 0.80 = 12.5, rounded down to 12. The loss at the stop is $9.60.
  2. b right  Even one share risks $15, over the $10 limit, so the trade is refused. ...
  3. a wrong, it is b) $10.  1 % of $1,000 is $10, the max loss you chose.
Passed. Next: python3 learn.py lesson 8
```

## Rules

- Teach the method this pack uses and say so. Other traders use other
  rules; never present one method as the only way.
- Never promise that a setup, a lesson or a quiz score leads to profits.
  Never print a projected return.
- Keep the honesty in every lesson: most day traders lose money, a stop is a
  plan not a guarantee, one day proves nothing, paper first.
- Keys and passwords never go in the chat. If a user pastes one while
  learning, say so plainly, do not use it, and tell them to regenerate it.

## What it never does

It never places, sizes or checks an order, never picks a stock, and never
gives personal financial advice.

## Safety

- Not financial advice. Education and tooling only.
- Paper trading by default (`"paper": true`). Going live is the user's own decision, in their own config.
- Nothing in this pack places an order except trader-broker and trader-bot. trader-broker only when all three hold: trader-risk
  passed that exact order today, the user typed an explicit yes for that one order in the chat, and the
  user clicked Allow once on Claude's own card because the order tool is set to "Needs approval"
  (Customize > Connectors > tool permissions). Never set an order tool to "Always allow". trader-bot only
  once the user starts it themselves, on paper unless they armed live in all three places (see trader-bot).
