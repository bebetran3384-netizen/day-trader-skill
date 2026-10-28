# Thinking in R

Minutes: 6
Example: r

## One unit of risk

**R** is the amount you lose if the stop is hit. It is set *before* the trade:

    R per share = entry - stop

If you buy at 364.18 with the stop at 358.32, R is 5.86 a share. If you buy
1 share, 1R is $5.86.

## Every result in R

A trade's result in R is the profit divided by the planned risk:

    result in R = (exit - entry) / (entry - stop)

- Stopped out at the stop: **-1R**.
- Exit at a 1R target: **+1R**.
- Sold at 15:55 halfway to the target: **+0.5R**.
- A gap through the stop can be worse than -1R (say -1.3R).

## Why traders count in R

R makes trades comparable. A $3 win on a $5 stock and a $30 win on a $300 stock
can be the same trade in R. It also separates the *strategy* (how many R per
trade on average) from the *sizing* (how many dollars one R is). Module 3 is
the sizing.

## Expectancy

The average result in R over many trades is the **expectancy**:

    expectancy = win rate x average win (R) - loss rate x average loss (R)

With a 1R target and a -1R stop, you need to win more than half the time just
to break even, and more than that once costs are included. A 45 % win rate
with 1R wins and 1R losses is -0.10R a trade: about where the plain ORB landed
in the backtest.

## Quiz

1. Entry 50.00, stop 49.00, exit 51.50. What is the result in R?
   - [ ] +1R
   - [x] +1.5R
   - [ ] +3R
   > Why: (51.50 - 50.00) / (50.00 - 49.00) = 1.5.
2. With a 1R target and a 1R stop, and no costs, what win rate breaks even?
   - [ ] 33 %
   - [x] 50 %
   - [ ] 66 %
   > Why: each win pays 1R and each loss costs 1R, so wins must equal losses.
3. A strategy wins 40 % of the time with 2R wins and 1R losses. What is its expectancy?
   - [ ] -0.2R
   - [x] +0.2R
   - [ ] +0.8R
   > Why: 0.40 x 2 - 0.60 x 1 = 0.80 - 0.60 = +0.20R a trade, before costs.
4. Why can a stopped-out trade lose more than 1R?
   - [x] The price can gap past the stop, and the stop fills at the next price
   - [ ] It cannot, a stop guarantees the price
   > Why: a stop order becomes a market order when touched. A gap or a halt can fill it well below the stop.
