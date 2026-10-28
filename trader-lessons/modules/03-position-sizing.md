# Position size: from 1 % to a share count

Minutes: 6
Example: size

## Risk first, shares second

Most beginners pick a number of shares and hope. The bot works backwards:

1. **Max loss per trade:** 1 % of the account, rounded down to the cent.
   On a $660 account that is $6.60.
2. **R per share:** entry minus stop.
3. **Shares:** max loss / R per share, rounded *down* to a whole share.
4. **Caps:** no more than 75 % of the account in one position, and keep a
   10 % cash buffer. If even 1 share risks more than the max loss, the trade
   is skipped.

## A worked case

Account $660, entry 10.20, stop 10.00:

- max loss = $6.60
- R per share = 0.20
- shares = 6.60 / 0.20 = 33
- cost = 33 x 10.20 = $336.60, under the 75 % cap of $495: OK

If the stop were 9.20 (R = 1.00), only 6 shares: the *same* $6 risk, a
smaller position. A wide stop means fewer shares, never more risk.

## The daily limits

On top of the per-trade limit, the bot stops trading for the day after
2 losing trades, takes at most 4 trades a day, and sells everything if the
day's loss reaches 2 x the max loss (a gap through a stop). The worst normal
day is therefore about -2R, about -$13 on $660.

## Why 1 %

At 1 % a trade, ten losses in a row cost about 10 % of the account. At 10 % a
trade, ten losses in a row (which do happen, see module 6) leave about a third
of it. Small risk per trade is what lets you survive long enough to find out
whether a strategy works.

## Quiz

1. Account $1,000, risk 1 %, entry 25.00, stop 24.50. How many shares?
   - [ ] 40
   - [x] 20
   - [ ] 10
   > Why: max loss $10.00, R per share 0.50, 10 / 0.50 = 20 shares.
2. You widen the stop from 0.50 to 1.00 below the entry. What happens to the dollar risk?
   - [x] It stays the same: the share count halves
   - [ ] It doubles
   > Why: shares = max loss / R per share, so a wider stop buys fewer shares for the same dollars at risk.
3. Account $660, a $300 stock with a $8 stop distance. What does the bot do?
   - [ ] Buys 1 share
   - [x] Skips it: 1 share risks $8, over the $6.60 max loss
   > Why: shares round down; 6.60 / 8 is 0 shares, so the trade is skipped.
4. How many losing trades end the bot's day?
   - [ ] 1
   - [x] 2
   - [ ] 4
   > Why: max_losing_trades is 2: after the second loss, no more entries that day.
