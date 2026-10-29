#!/usr/bin/env python3
"""Pick the bot's symbols from the scanner's hits: priced $5-$150, the 25
most traded (price x volume, the scanner's own measure), one ticker a line."""
import argparse
import json

p = argparse.ArgumentParser()
p.add_argument("--watchlist", default="trader/watchlist.json")
p.add_argument("--out", default="trader/bot_symbols.txt")
p.add_argument("--min-price", type=float, default=5)
p.add_argument("--max-price", type=float, default=150)
p.add_argument("--top", type=int, default=25)
a = p.parse_args()

with open(a.watchlist) as fh:
    hits = json.load(fh).get("hits", [])
ok = [h for h in hits if a.min_price <= (h.get("price") or 0) <= a.max_price]
ok.sort(key=lambda h: (h.get("price") or 0) * (h.get("volume") or 0), reverse=True)
picked = ok[:a.top]
with open(a.out, "w") as fh:
    fh.write("".join(h["symbol"] + "\n" for h in picked))
print("%d hits, %d priced $%g-$%g, picked %d: %s"
      % (len(hits), len(ok), a.min_price, a.max_price, len(picked),
         ", ".join(h["symbol"] for h in picked) or "none"))
