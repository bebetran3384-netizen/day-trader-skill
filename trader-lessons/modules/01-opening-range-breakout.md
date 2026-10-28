# The opening-range breakout

Minutes: 8
Example: orb

## The idea

The first minutes after 9:30 New York time are the busiest of the day. Overnight
news, pre-market orders and the first institutional trades all land at once. The
high and the low of those first 15 minutes are the **opening range**.

The opening-range breakout (ORB) bets that when price leaves that range with
conviction, it tends to keep going for a while. It is one of the oldest
intraday setups, and it is the one rule the trader-bot trades.

## The exact rule the bot uses

1. **Range:** the high and low of the three 5-minute bars from 9:30 to 9:45.
2. **Trigger:** the first 5-minute bar that *closes* above the range high.
   A wick above the high is not enough: the bar has to close there.
3. **Fresh only:** the trigger must be at most 6 minutes old, and the price at
   most 0.5 % above it. A breakout already running is not chased.
4. **Stop:** the middle of the range. If price falls back to the middle, the
   breakout failed.
5. **Target:** 1R, as far above the entry as the stop is below it.
6. **Cutoffs:** no new entries after 11:30, and everything is sold by 15:55.
7. **Market filter:** no new buys while SPY is below its own 9:30 open.

## Why each piece is there

- Waiting for the *close* filters out quick wicks that reverse at once.
- The freshness rule stops the bot from buying a move that already happened.
- The midpoint stop is a plain, mechanical definition of "the idea was wrong".
- The 11:30 cutoff and the SPY filter come from the backtest in module 5:
  breakouts late in the day and on down-market days did worse.

## What it is not

The ORB is not a prediction. Most breakouts fail or go nowhere. The only
question that matters is whether the winners pay for the losers *after costs*,
and that is a question for data, not for a story. Module 5 shows the answer
for 51 stocks over 60 days.

## Quiz

1. At 9:52 a stock trades above its opening-range high, but the 9:50 bar closes back inside the range. What does the bot do?
   - [ ] Buys at once, the price broke out
   - [x] Nothing yet: the rule needs a 5-minute bar to close above the range high
   - [ ] Sells short, the breakout failed
   > Why: the trigger is a close above the range high. A wick that falls back is not a breakout.
2. The trigger bar closed at 10:05. At 10:20 the price is still near the trigger. Does the bot buy?
   - [ ] Yes, the price has not run away
   - [x] No: the trigger is more than 6 minutes old
   > Why: the bot only takes fresh triggers (at most 6 minutes old) so it trades the breakout, not what came after.
3. Where is the stop for a range of 20.00 to 21.00?
   - [ ] 20.00, the range low
   - [x] 20.50, the range midpoint
   - [ ] 1 % below the entry
   > Why: the bot's stop is the range midpoint, (20.00 + 21.00) / 2.
4. SPY is 0.3 % below its open at 10:00 and your stock triggers. What happens?
   - [x] No buy: the market filter blocks long entries while SPY is under its open
   - [ ] Buy anyway, the stock is strong
   > Why: in the backtest, breakouts on down-market mornings lost more often, so the filter skips them.
