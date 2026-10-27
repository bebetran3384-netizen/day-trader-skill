---
name: trader-bot
description: >-
  Trading Bot. Runs the opening-range breakout end to end without asking for
  each order: watches your symbols bar by bar, plans the stop at the range
  midpoint and the target at 1R, sizes to your max loss, runs trader-risk on
  the exact order, places one bracket order on Alpaca and flattens its own
  positions by 3:55 PM. Paper by default; real money only when the config,
  the command line and the environment all say so. Kill switch, daily loss
  halt, never touches holdings it did not open, and replays any past day on
  simulated fills first. Use when the user says "run the bot", "automate
  it", "trade it for me", "auto trade", "start the trading bot", "stop the
  bot", "kill switch", "is the bot running", "replay the bot on yesterday",
  or "go live with the bot".
argument-hint: "[run | once | status | stop | replay --day YYYY-MM-DD]"
---

# trader-bot

The Trading Bot is the one skill in this pack that places orders by itself.
Every other skill on the order path stops and asks for a yes. The bot asks
once instead: when you start it, and once more, in three places, before it
will touch real money. After that it trades the opening-range breakout on its
own until the close, under your limits.

**Not financial advice.** This is software that does arithmetic on free,
unofficial bars. Most people who day trade lose money. A bot loses it faster
and without asking. Run it on paper for weeks before you think about live,
and never with money you cannot afford to lose.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/bot.py" replay --offline trader/charts --day 2026-10-19   # a past day, simulated
python3 "${CLAUDE_SKILL_DIR}/bot.py" run --dry-run          # today, live Yahoo bars, simulated fills
python3 "${CLAUDE_SKILL_DIR}/bot.py" run                    # today, Alpaca PAPER account
python3 "${CLAUDE_SKILL_DIR}/bot.py" once                   # one pass (for a scheduled task or /loop)
python3 "${CLAUDE_SKILL_DIR}/bot.py" status                 # what it is watching, what it holds, P&L
python3 "${CLAUDE_SKILL_DIR}/bot.py" stop                   # kill switch: trader/STOP
python3 "${CLAUDE_SKILL_DIR}/bot.py" resume                 # clear the kill switch
python3 "${CLAUDE_SKILL_DIR}/bot.py" flatten                # close the bot's positions now
```

## What it does, every pass (every 20 seconds)

1. **Kill switch.** If `trader/STOP` exists: no new entries, cancel the bot's
   orders, close the bot's positions, exit.
2. **Flat by.** At `flat_by` (15:55; earlier on a 1:00 p.m. close) it cancels
   the bracket legs and closes what it opened, then exits for the day.
3. **Daily loss.** Realized plus open P&L of today's bot trades. At or under
   minus `max_daily_loss_usd`: halt and flatten. If the fills cannot be read,
   it places nothing that pass.
4. **Scan** (every 60 seconds). For each symbol it is watching, the same
   rule as `/trader-entry`: the first 5-minute bar after the 15-minute range
   that CLOSES above the range high. Then:
   - trigger older than `max_trigger_age_min` (6): skipped, not chased
   - last price more than `max_chase_pct` (0.5 %) over the trigger: skipped
   - plan, as `/trader-exit` would: stop at the range midpoint, target 1R,
     from the higher of the trigger and the last price
   - size, as `/trader-budget` would: whole shares, max loss per trade,
     position cap, max order, cash after the buffer
   - check, as `/trader-risk` does, with today's log: any BLOCK and it skips
   - one bracket order: market buy, stop-loss, take-profit, good for the day

One entry per symbol a day, one entry per pass, at most `max_open_positions`
(1) open at once, at most `max_trades_per_day` (3).

## Paper, dry run, live

| mode | how | orders go to |
| --- | --- | --- |
| dry run | `--dry-run` or `--offline DIR` | nowhere: simulated fills from the bars |
| paper | the default (`"paper": true`) | `paper-api.alpaca.markets` |
| live | all three below | `api.alpaca.markets`, real money |

Live needs **all three**, and any two without the third is an error, never a
fallback:

1. `"paper": false` in `trader/config.json`
2. `--live` on the command line
3. `TRADER_BOT_LIVE=real-money` in the environment

Keys: `ALPACA_API_KEY` and `ALPACA_SECRET_KEY` (or `APCA_API_KEY_ID` and
`APCA_API_SECRET_KEY`) in the environment. Never paste keys into the chat,
never write them in a file in the project. Paper and live keys are different
keys on Alpaca.

Before a user goes live, Claude says plainly: this places real orders with
real money with no further yes; show me at least a few weeks of paper results
first; here is the exact max you can lose today (`max_daily_loss_usd`, plus
slippage past a stop on a gap). Claude never sets `TRADER_BOT_LIVE` itself,
never adds `--live` on its own, and never flips `"paper"` to false without
the user asking for that in so many words.

## The config

`trader/config.json`, the same file every skill reads (start from
`trader-budget/config.example.json`), plus an optional `"bot"` block:

```json
"bot": {
  "symbols": ["TSLA", "NVDA", "AMD"],
  "or_minutes": 15,
  "stop": "or-mid",
  "target": "1R",
  "min_volume_x": null,
  "max_trigger_age_min": 6,
  "max_chase_pct": 0.5,
  "max_open_positions": 1,
  "max_symbols": 25,
  "poll_seconds": 20,
  "scan_seconds": 60
}
```

The bot refuses to start without `budget`, `max_loss_per_trade_usd` and
`max_daily_loss_usd`, with `"require_stop": false`, or with shorts, margin
or options turned on.

## Where the symbols come from

First found wins: `--symbols TSLA,NVDA`, `bot.symbols` in the config,
`--watchlist FILE`, `trader/watchlist.json` (from `/trader-scanner`), then
`trader/shortlist.json`. That last one is the original Trader skill's
fundamental screen: its `"passed"` tickers. So the two packs combine: screen
on the numbers once a quarter in v1, let the bot trade the breakouts in that
list every day in v2.

## It only touches what it opened

It skips any symbol you already hold, and at the flatten, the kill switch or
the loss halt it closes only the symbols it entered today. Long-term
holdings in the same account are never sold. Even so, a separate account for
the bot is safer.

## Files

| file | what |
| --- | --- |
| `trader/config.json` | your limits and the `"bot"` block |
| `trader/state/bot.json` | today: each symbol's status, entries, halt, P&L |
| `trader/state/bot.log` | one line per event, New York time |
| `trader/state/broker.json` | every order it placed, in trader-broker's format |
| `trader/STOP` | the kill switch: exists = stop |

## Running it

- From a terminal: `bot.py run` before 9:30 New York. It sleeps to the open,
  trades, flattens at 15:55 and exits. Run it in `tmux` or `screen`, or as a
  weekday cron job at 9:25 ET.
- From Claude: a scheduled task each weekday at 9:25 AM ET that runs
  `bot.py run`, or `/loop 1m` with `bot.py once`.
- To stop it: `bot.py stop` (or create `trader/STOP`). A running bot halts
  within one pass. Ctrl-C stops the process but leaves open positions with
  their stop-loss and take-profit at the broker: run `bot.py flatten`.

Always replay first: `bot.py replay --offline DIR --day YYYY-MM-DD` runs a
past day through the same code on simulated fills (stop first when one bar
touches both legs, the bar's open on a gap through the stop). DIR holds
`<TICKER>.json` 5-minute Yahoo charts. A replay is a simulation with perfect
fills, not a promise.

## The fine print it repeats

- A stop is a plan, not a guarantee: a gap or a halt can fill past it.
- Yahoo's bars are free and unofficial; they can lag a minute.
- If the process dies mid-day, the bracket legs at Alpaca still protect the
  position, but nothing flattens it at 15:55. A `day` bracket's legs expire
  at the close, so the position could be held overnight. Check `status`.
- Day-trading rules: if your broker still applies the old pattern-day-trader
  rule, set `"pdt_guard": true` and trader-risk counts the bot's day trades.
- Not financial advice. You decide, and you are responsible for every order
  it places in your account.
