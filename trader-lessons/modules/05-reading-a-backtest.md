# Reading a backtest without fooling yourself

Minutes: 10
Example: backtest

## The numbers that matter

- **Trades (n):** under about 100, almost any result can be luck.
- **Expectancy (mean R):** the average trade, after costs.
- **Standard error:** how far the mean could move by chance. A mean less than
  about 2 standard errors from zero is not reliably different from zero.
- **Win rate:** only meaningful next to the size of wins and losses.

## Costs

Even with no commission there is a spread: you buy at the ask and sell at the
bid. The replays charge 0.03 % on each side. On a tight stop that is a real
slice of R: on a 0.5 % stop, 0.06 % round trip is 0.12R a trade.

## Overfitting

Try enough settings and one will look great on the past by luck. The defense
is to test on days the setting was not chosen on:

- **Split the days into blocks** and require the result to hold in most of
  them, not one lucky stretch.
- **Walk forward:** pick the best setting using only earlier days, then score
  that pick on the next block. If picking-the-best does not beat doing nothing
  on unseen days, the "improvement" was hindsight.

## What the data said for this bot

On 51 stocks over 60 days (August to October 2026):

- The plain ORB (15-minute range, midpoint stop, 1R, entries all day):
  about **-0.09R a trade** over 1,561 trades, negative in both halves.
- With the SPY filter and the 11:30 cutoff: about **-0.02R**, losing less,
  not winning.
- Some settings looked strongly positive in one half and strongly negative in
  the other. That is the signature of luck, not an edge.

The bot's learner (`trader-bot/learn.py study`) runs these checks again on
fresh data and only proposes a change that survives the walk-forward test.

## Quiz

1. A setting averages +0.30R over 12 trades. What can you conclude?
   - [ ] It has an edge
   - [x] Almost nothing: 12 trades is far too few
   > Why: with results spread over roughly -1R to +1R, 12 trades leaves a huge range for luck.
2. A setting is +0.20R on the first half of the days and -0.25R on the second. What is the most likely story?
   - [ ] It works, the second half was unlucky
   - [x] Luck: an edge should not flip sign between halves
   > Why: a real edge shows up, at least weakly, across different stretches of time.
3. What does a walk-forward test check?
   - [x] Whether choosing the best setting on past days would have helped on the days after
   - [ ] Whether the best setting in hindsight is positive
   > Why: it tests the process of choosing, using only information available before each block.
4. Mean -0.02R, standard error 0.03R. Is the strategy losing?
   - [ ] Yes, clearly
   - [x] It is not distinguishable from break-even: the mean is within one standard error of 0
   > Why: -0.02 is less than one standard error from zero; the data cannot tell a small loss from zero.
