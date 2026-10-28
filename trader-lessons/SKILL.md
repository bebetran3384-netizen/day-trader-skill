---
name: trader-lessons
description: >-
  Lessons. Six short learning modules on the trade the bot makes and on
  reading its numbers: the opening-range breakout, thinking in R, position
  sizing from 1 % of the account, stops, targets and gaps, reading a
  backtest without fooling yourself, and why most day traders lose. Each
  module has a worked example on real 5-minute bars (or the reel's TSLA
  trade) and a graded quiz, and your scores are remembered. Use when the
  user says "teach me", "learning modules", "lessons", "explain the
  strategy", "what is R", "how does sizing work", "quiz me", "how do I read
  a backtest", "why did the bot lose", or "my progress".
argument-hint: "[list | 1-6 | progress]"
---

# trader-lessons

Six modules, five to ten minutes each. They teach exactly what the rest of
this pack does, with the pack's own numbers, so that nothing the bot does is
a black box.

**Not financial advice.** These lessons explain how the tools work and what
the data says. They do not tell anyone what to buy, and they say plainly that
most day traders lose money.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/lessons.py" list                         # modules and your best scores
python3 "${CLAUDE_SKILL_DIR}/lessons.py" show 1                       # sections and quiz, no answers
python3 "${CLAUDE_SKILL_DIR}/lessons.py" example 1 --data trader/learn/data   # a real case
python3 "${CLAUDE_SKILL_DIR}/lessons.py" example 3 --account 660      # sizing on your account
python3 "${CLAUDE_SKILL_DIR}/lessons.py" grade 1 --answers 2,2,2,1    # grade and remember
python3 "${CLAUDE_SKILL_DIR}/lessons.py" progress
```

## The modules

| # | module | example on real bars |
| --- | --- | --- |
| 1 | The opening-range breakout | a breakout that reached its target, charted |
| 2 | Thinking in R | a breakout that was stopped out, in R |
| 3 | Position size | the share count, cost and risk on your account |
| 4 | Stops, targets and the bar that touches both | a bar that hit both legs, counted as the stop |
| 5 | Reading a backtest | the plain ORB vs the bot's settings, by half |
| 6 | Why most day traders lose | 2,000 runs of 300 trades: how long losing streaks get |

The examples read the saved 5-minute charts from `trader/learn/data` (the
bot's learner saves them: `trader-bot/learn.py fetch`). Without `--data`,
modules 1 to 4 use the reel's trade (TSLA, October 19 2026) and module 5
asks for the data.

## How Claude teaches a module

1. `lessons.py show N` and present the sections **one at a time**, in your
   own words, short, with the module's numbers. Stop after each section for
   questions before going on.
2. `lessons.py example N` (with `--data` when `trader/learn/data` exists, and
   `--account` set to the user's account value when known) and walk through
   the case: the symbol and day, the range, the trigger, the stop and the
   target, the chart, and the result in R or dollars.
3. The quiz: ask each question with its choices (AskUserQuestion when
   available, one question at a time), collect the answers, then
   `lessons.py grade N --answers a,b,c,d` and go over every miss with the
   `why` line. 3 of 4 right (75 %) passes.
4. Suggest the next module. `lessons.py list` shows where the user is.

Never show the answers before the user has answered. Never invent numbers
that the module or the example did not produce. If the user asks "should I
trade this?", the answer is module 5 and 6: what the data says, the base
rate, and that it is their decision.

## The order

Start at 1 for a new user. Someone who just watched the bot lose money
should do 2, 5 and 6. Someone about to change a setting should do 5 first.
Someone about to go live with real money should do all six.

## Files

| file | what |
| --- | --- |
| `modules/NN-*.md` | the modules: sections, then `## Quiz` |
| `lessons.py` | show, example, grade, progress |
| `trader/lessons/progress.json` | your scores: last, best, passed, date |

A module is plain Markdown: `# Title`, `Minutes:` and `Example:` lines,
`## ` sections, and a `## Quiz` of numbered questions with `- [ ]` choices,
one `- [x]`, and a `> Why:` line. Add a module by adding a file.

## The fine print

- The backtest numbers in modules 5 and 6 come from free, unofficial Yahoo
  bars and simple fills. Real fills are usually worse.
- A quiz score is about understanding the tools, not a license to trade.
- Not financial advice.
