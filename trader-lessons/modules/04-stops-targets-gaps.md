# Stops, targets and the bar that touches both

Minutes: 6
Example: both

## What a 5-minute bar hides

A bar tells you its open, high, low and close, not the order they happened in.
If one bar's high reaches your target and its low reaches your stop, you
cannot know from the bar which came first.

Every backtest in this pack makes the **conservative call: the stop first**.
A backtest that assumes the target first will look better than real life.

## Gaps

If the price jumps past the stop between two prints (news, a halt, the open),
a stop order fills at the next available price, not at the stop. In the
replays, a bar that *opens* below the stop fills at that open, a loss bigger
than 1R.

## Robinhood specifics

Robinhood has no bracket orders. The bot places a separate stop order as soon
as the buy fills. The target is not an order at all: each 5-minute pass sells
at market if the last price has reached it. So targets can fill a little
better or worse than the exact price.

## Flat by the close

Day trades are sold by 15:55. A trade that hits neither stop nor target is
closed at whatever the price is then: anywhere between -1R and the target.

## Quiz

1. One 5-minute bar reaches both your stop and your target. How do the backtests count it?
   - [x] As a stop: the conservative call
   - [ ] As a target
   - [ ] As half of each
   > Why: the bar does not say which came first, so the replay assumes the worse outcome.
2. Your stop is 20.00. The next bar opens at 19.60. Where does a stop order fill?
   - [ ] 20.00
   - [x] Around 19.60, the next available price
   > Why: a stop becomes a market order once touched; after a gap, the market is below the stop.
3. On Robinhood, what protects the position the moment the buy fills?
   - [x] A separate stop order the bot places right away
   - [ ] A bracket order with stop and target
   > Why: Robinhood has no bracket orders for this, so the stop is its own order and the target is watched each pass.
