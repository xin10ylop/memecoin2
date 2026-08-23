"""What a given bankroll can realistically be expected to make per day.

    python scripts/bankroll.py [bankroll_usd]

This script exists in two halves on purpose. The first reproduces the naive
answer, which is wrong by roughly a factor of fifteen; the second is defensible.
The naive version is kept because the specific way it fails is the single
easiest mistake to make when sizing this strategy, and because it is the answer
almost any straightforward Monte Carlo will hand you.

**Why the naive answer is wrong.** It resamples N trades per day independently
from the observed out-of-sample outcomes. Memecoins are all long the same
factor - whether the market is hot right now - so a day's trades behave like a
handful of independent bets rather than hundreds. The law of large numbers
therefore does not smooth the daily total the way independent sampling implies:
it turns a coin-flip business into an apparent certainty. It also resamples a
pool in which five trades out of eighty-one produced all of the profit.

Corrected, the probability of a losing day moves from under 1% to roughly 45%,
and the expected daily figure collapses.
"""
from __future__ import annotations

import sys
from math import erf, sqrt

# Measured on the held-out window; see docs/RESEARCH.md.
MEAN_EDGE_PER_TRADE = 0.133      # mean, and a tail artefact - the median was +1.3%
OBSERVED_TRADES = 81
PROFIT_FROM_TOP5 = 1.05          # top five trades produced 105% of profit
MEAN_HOLD_MIN = 30.4
SLOTS = 6                        # concurrent positions with capital at risk
BASE_FRAC = 0.02                 # of bankroll, at full conviction
CONVICTION = 0.6                 # typical
SOL_USD = 94.0
TX_PER_TRADE = 4                 # one buy, one cost-recovery sell, terminal exits
FEE_PER_TX = {"routine": 0.009, "contested": 0.139, "aggressive": 0.385}
INFRA_MONTHLY = 56.0             # $6 server + ~$50 paid RPC
MIN_VIABLE_POSITION = 47.0       # ~0.5 SOL; below this fixed costs dominate


def main(bankroll: float = 1000.0) -> None:
    pos = bankroll * BASE_FRAC * CONVICTION
    trades_day = SLOTS * 24 * 60 / MEAN_HOLD_MIN
    fees_day = TX_PER_TRADE * FEE_PER_TX["routine"] * trades_day

    print(f"\nbankroll ${bankroll:,.0f}   position ${pos:,.2f}   {SLOTS} slots   "
          f"{MEAN_HOLD_MIN:.0f}min mean hold")
    print(f"mechanical ceiling {trades_day:.0f} trades/day "
          f"(${pos*trades_day:,.0f} turnover; ${SLOTS*pos:,.0f} at risk at once, "
          f"{100*SLOTS*pos/bankroll:.1f}% of bankroll)")
    if pos < MIN_VIABLE_POSITION:
        print(f"\n  WARNING: ${pos:,.2f} is below the ~${MIN_VIABLE_POSITION:.0f} where fixed costs")
        print( "  stop mattering. At this size only the routine fee tier is affordable, which")
        print( "  means losing every contested fill - and contested tokens are disproportionately")
        print( "  the good ones.")

    print("\n--- fee tier affordability ---")
    print(f"{'tier':<12}{'$/trade':>10}{'% of position':>15}{'$/day':>10}{'%bankroll/day':>15}")
    for tier, per_tx in FEE_PER_TX.items():
        per_trade = TX_PER_TRADE * per_tx
        flag = "" if per_trade / pos < 0.02 else "   <- exceeds any measured edge"
        print(f"{tier:<12}{per_trade:>10.2f}{100*per_trade/pos:>14.2f}%"
              f"{per_trade*trades_day:>10.0f}{100*per_trade*trades_day/bankroll:>14.1f}%{flag}")

    print("\n--- THE NAIVE ANSWER (wrong, kept as a warning) ---")
    naive = MEAN_EDGE_PER_TRADE * pos * trades_day - fees_day
    print(f"  {MEAN_EDGE_PER_TRADE*100:.1f}% edge x {trades_day:.0f} trades  ->  "
          f"${naive:+,.0f}/day ({100*naive/bankroll:+.0f}% of bankroll), "
          f"P(losing day) under 1%")
    print(f"  Rejected: the edge is a mean over {OBSERVED_TRADES} trades where five produced "
          f"{100*PROFIT_FROM_TOP5:.0f}% of profit,")
    print( "  and independent resampling ignores that a day's trades share one market factor.")

    print("\n--- SENSITIVITY: daily P&L is linear in the per-trade edge ---")
    print(f"{'edge/trade':>12}{'gross/day':>12}{'net/day':>10}{'%bankroll':>11}{'net/month':>12}{'after infra':>13}")
    for edge in (0.133, 0.08, 0.05, 0.03, 0.02, 0.01, 0.0, -0.01, -0.03):
        gross = edge * pos * trades_day
        net = gross - fees_day
        note = ""
        if edge == MEAN_EDGE_PER_TRADE:
            note = "  <- measured, not believed"
        elif edge == 0.05:
            note = "  <- plausible after decay"
        print(f"{100*edge:>11.1f}%{gross:>12.0f}{net:>10.0f}{100*net/bankroll:>10.1f}%"
              f"{30*net:>12.0f}{30*net-INFRA_MONTHLY:>13.0f}{note}")

    print("\n--- CORRELATION: how many independent bets is a day, really? ---")
    edge, sd_trade = 0.05, 1.2
    mu = edge * pos * trades_day
    print(f"  at a {100*edge:.0f}% edge and {sd_trade:.1f}x trade-level volatility:")
    print(f"{'independent bets/day':>22}{'daily sd':>12}{'P(losing day)':>16}")
    for k in (int(trades_day), 50, 20, 10, 5):
        sd = sd_trade * pos * trades_day / sqrt(k)
        p = 0.5 * (1 - erf((mu / sd) / sqrt(2)))
        tag = "  <- what the naive run assumed" if k == int(trades_day) else (
              "  <- realistic" if k == 5 else "")
        print(f"{k:>22}{sd:>12.0f}{100*p:>15.1f}%{tag}")

    print(f"\n--- infrastructure hurdle ---")
    print(f"  ${INFRA_MONTHLY:.0f}/month = {100*INFRA_MONTHLY/bankroll:.1f}% of this bankroll, "
          f"payable before the first dollar of profit")
    better = INFRA_MONTHLY / 0.01
    print(f"  it falls under 1% of bankroll at about ${better:,.0f}\n")


if __name__ == "__main__":
    main(float(sys.argv[1]) if len(sys.argv) > 1 else 1000.0)
