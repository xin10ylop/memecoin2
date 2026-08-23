"""AMM math. If this is wrong every backtest number is wrong."""
import math

import pytest

from degen.sim.amm import Pool, cp_in_for_out, cp_out, max_size_for_impact, round_trip_cost


def test_constant_product_preserves_k_without_fee():
    x, y = 100.0, 1000.0
    out = cp_out(x, y, 10.0, fee=0.0)
    assert math.isclose((x + 10.0) * (y - out), x * y, rel_tol=1e-9)


def test_fee_reduces_output():
    assert cp_out(100, 1000, 10, 0.01) < cp_out(100, 1000, 10, 0.0)


def test_in_for_out_is_inverse_of_out():
    x, y, fee = 50.0, 5000.0, 0.0025
    want = 100.0
    need = cp_in_for_out(x, y, want, fee)
    assert math.isclose(cp_out(x, y, need, fee), want, rel_tol=1e-6)


def test_in_for_out_is_infinite_beyond_reserve():
    assert cp_in_for_out(10, 100, 100, 0.0) == float("inf")


def test_slippage_grows_with_size():
    p = Pool(sol_reserve=100.0, token_reserve=1e9, fee=0.0025)
    slips = [p.buy(s).slippage_frac for s in (0.1, 1.0, 5.0, 20.0)]
    assert slips == sorted(slips)
    assert all(s > 0 for s in slips)


def test_trade_exceeding_pool_cap_is_refused():
    p = Pool(sol_reserve=10.0, token_reserve=1e9)
    f = p.buy(9.0, max_pool_frac=0.3)
    assert not f.ok and f.reason == "size_exceeds_pool_cap"


def test_buy_then_sell_loses_at_least_the_fees():
    p = Pool(sol_reserve=100.0, token_reserve=1e9, fee=0.01)
    cost = round_trip_cost(p, 1.0)
    assert cost >= 2 * 0.01 * 0.9      # roughly both fee legs
    assert cost < 0.10                 # but not catastrophic at this size


def test_round_trip_cost_rises_as_pool_thins():
    thick = round_trip_cost(Pool(1000.0, 1e9, fee=0.0025), 5.0)
    thin = round_trip_cost(Pool(10.0, 1e9, fee=0.0025), 5.0)
    assert thin > thick


def test_apply_moves_the_pool_against_you():
    p = Pool(100.0, 1e9, fee=0.0)
    f = p.buy(10.0)
    after = p.apply(f, "buy")
    assert after.mid > p.mid            # we pushed price up
    assert after.token_reserve < p.token_reserve


def test_max_size_for_impact_hits_the_target():
    p = Pool(200.0, 1e9, fee=0.003)
    size = max_size_for_impact(p, 0.02, "buy")
    assert size > 0
    assert p.buy(size).slippage_frac == pytest.approx(0.02, abs=2e-3)


def test_pumpfun_curve_starts_at_documented_price():
    p = Pool.from_pumpfun(0.0)
    assert p.sol_reserve == 30.0
    assert p.token_reserve == 1_073_000_000.0
    assert p.kind == "bonding"


def test_pumpfun_price_rises_with_sol_raised():
    a, b = Pool.from_pumpfun(0.0), Pool.from_pumpfun(40.0)
    assert b.mid > a.mid


def test_from_liquidity_usd_round_trips_price():
    p = Pool.from_liquidity_usd(20_000, 0.00004, 100.0)
    # mid is SOL per token; price_usd / sol_usd is the same quantity
    assert p.mid == pytest.approx(0.00004 / 100.0, rel=1e-9)


def test_empty_pool_refuses_trades():
    assert not Pool(0.0, 0.0).buy(1.0).ok
    assert not Pool(10.0, 0.0).sell(1.0).ok
