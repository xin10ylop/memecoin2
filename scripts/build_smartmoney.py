"""Populate the smart-money book from the collected census."""
import sys
sys.path.insert(0, "src")
from degen.signals.smartmoney import SmartMoneyBook, build_from_census
from degen.store.lake import lake
from degen.util.log import setup

setup()
con = lake().con()
rows = con.execute("""
  with f as (select mint, price_usd p0, dev from (
      select *, row_number() over (partition by mint order by observed_at) rn
      from snapshots where price_usd > 0) where rn = 1)
  select f.mint, f.dev, max(s.price_usd)/f.p0 maxx, count(*) n
  from snapshots s join f on s.mint = f.mint
  where s.price_usd > 0 group by f.mint, f.p0, f.dev
  having count(*) >= 4 and max(s.price_usd)/f.p0 < 1e5
""").df().to_dict("records")
print(f"census rows: {len(rows)}  winners(>=2x): {sum(1 for r in rows if r['maxx']>=2)}")
book = SmartMoneyBook()
n = build_from_census(book, rows, limit=int(sys.argv[1]) if len(sys.argv) > 1 else 140)
print(f"scanned {n} tokens; wallets known: {len(book.wallets)}")
top = book.top(15)
print(f"\nwallets clearing the credibility bar (>={3} winners, >=2 distinct creators): {len(book.credited())}")
for s in top:
    print(f"  {s.wallet[:44]:44} winners={s.winners:3d}/{s.total_seen:3d} hit={s.hit_rate:.3f} "
          f"best={s.best_multiple:7.1f}x creators={len(s.creators):3d} score={s.score():.4f}")
