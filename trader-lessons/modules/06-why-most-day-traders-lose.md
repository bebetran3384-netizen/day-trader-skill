# Why most day traders lose

Minutes: 8
Example: streaks

## The base rate

Studies of retail day traders (for example, Barber, Lee, Liu and Odean on
Taiwan, and Chague, De-Losso and Giovannetti on Brazilian futures) find that
the large majority lose money, and that only a very small share earn more
than a minimum wage over time. Treat any strategy as guilty until the data
says otherwise.

## Where the money goes

- **Costs:** spreads, slippage and fills worse than the chart.
- **No edge:** most simple setups, once costs are in, average near zero.
- **Behavior:** moving stops, revenge trades after a loss, bigger size after a
  win. A bot removes these, but it also removes judgement.

## Streaks are normal

With a 45 % win rate, losing streaks of 6, 8 or more happen regularly over a
few hundred trades. That is why risk per trade is 1 % and why the bot stops
for the day after 2 losses: not to avoid losses, but to survive them.

## What a sane plan looks like

1. Paper trade or tiny size until you have at least 100 trades of results.
2. Compare live results with the backtest (`learn.py review`). If live is much
   worse, the fills or the picks differ from the replay: stop and look.
3. Change one setting at a time, and only when it holds up on unseen days.
4. Never risk money you need.

Not financial advice.

## Quiz

1. A strategy wins 45 % of its trades. Over 300 trades, is a streak of 7 losses in a row surprising?
   - [ ] Yes, very rare
   - [x] No, streaks like that are common at that win rate
   > Why: with a 55 % chance of losing each trade, long losing runs are expected over hundreds of trades.
2. Live results are -0.4R a trade over 30 trades while the backtest said -0.02R. What should you do?
   - [ ] Double the size to win it back
   - [x] Stop and find out why live differs from the replay
   > Why: a gap that big points at fills, timing or picks the backtest did not model.
3. What do studies of retail day traders mostly find?
   - [x] Most lose money and very few earn a living from it
   - [ ] About half make money
   > Why: large studies of retail day traders find that a large majority lose money over time.
