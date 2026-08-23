"""Broker and submission-path behaviour."""
import pytest

from degen.execution.broker import PaperBroker
from degen.execution.sender import (
    JITO_MIN_TIP, JITO_TIP_ACCOUNTS, SENDER_MAX_MIN_TIP, SENDER_SWQOS_MIN_TIP,
    HeliusSender, JitoBundleSender, RpcSender, build_sender, tip_account,
)
from degen.sim.costs import CostModel

CTX = {"liquidity": 20_000.0, "price_usd": 0.00002, "sol_usd": 100.0, "decimals": 6}


def test_paper_buy_debits_sol_and_credits_tokens():
    b = PaperBroker(start_sol=10.0)
    r = b.buy("M", 1.0, 2_000, CTX)
    assert r.ok and r.tokens_delta > 0
    assert b.sol_balance() < 9.0 + 1e-9      # spent 1 SOL plus fees
    assert b.positions["M"] == pytest.approx(r.tokens_delta)


def test_paper_cannot_spend_more_than_it_has():
    b = PaperBroker(start_sol=0.5)
    assert not b.buy("M", 5.0, 2_000, CTX).ok


def test_paper_refuses_to_invent_a_price_without_pool_state():
    b = PaperBroker(start_sol=10.0)
    r = b.buy("M", 1.0, 2_000, {"decimals": 6})
    assert not r.ok and "no pool state" in r.reason


def test_paper_rejects_a_fill_beyond_the_slippage_cap():
    b = PaperBroker(start_sol=100.0)
    # $2k liquidity at $100/SOL is a 10 SOL reserve; 2 SOL is inside the pool
    # cap but moves the price ~20%, far past a 10bp tolerance.
    thin = {"liquidity": 2_000.0, "price_usd": 0.00002, "sol_usd": 100.0, "decimals": 6}
    r = b.buy("M", 2.0, 10, thin)
    assert not r.ok and "slippage" in r.reason


def test_paper_rejects_a_trade_that_would_dominate_the_pool():
    b = PaperBroker(start_sol=100.0)
    thin = {"liquidity": 2_000.0, "price_usd": 0.00002, "sol_usd": 100.0, "decimals": 6}
    r = b.buy("M", 8.0, 50_000, thin)        # 80% of a 10 SOL reserve
    assert not r.ok and "pool" in r.reason


def test_paper_sell_is_capped_at_the_position():
    b = PaperBroker(start_sol=10.0)
    bought = b.buy("M", 1.0, 2_000, CTX)
    r = b.sell("M", bought.tokens_delta * 10, 2_000, CTX)
    assert r.ok and abs(r.tokens_delta) <= bought.tokens_delta + 1e-6


def test_paper_round_trip_loses_only_costs():
    b = PaperBroker(start_sol=10.0, costs=CostModel(venue="pumpswap"))
    bought = b.buy("M", 1.0, 5_000, CTX)
    b.sell("M", bought.tokens_delta, 5_000, CTX)
    assert 9.8 < b.sol_balance() < 10.0      # down by fees and impact, not more


def test_selling_with_no_position_fails_cleanly():
    b = PaperBroker(start_sol=10.0)
    assert not b.sell("NOPE", 1.0, 2_000, CTX).ok


def test_sender_minimum_tips_match_documented_values():
    assert SENDER_MAX_MIN_TIP == 1_000_000        # 0.001 SOL
    assert SENDER_SWQOS_MIN_TIP == 5_000          # 0.000005 SOL
    assert JITO_MIN_TIP == 1_000


def test_swqos_mode_lowers_the_required_tip_and_marks_the_url():
    cheap = HeliusSender(swqos_only=True)
    rich = HeliusSender(swqos_only=False)
    assert cheap.min_tip < rich.min_tip
    assert "swqos_only=true" in cheap.url


def test_unknown_region_falls_back_to_global():
    assert HeliusSender(region="atlantis").region == "global"


def test_tip_accounts_are_sharded():
    assert len(JITO_TIP_ACCOUNTS) >= 8
    assert tip_account() in JITO_TIP_ACCOUNTS


def test_jito_rejects_an_oversized_bundle():
    r = JitoBundleSender().send_bundle(["tx"] * 6)
    assert not r.ok and "bundle" in r.reason


def test_jito_rejects_an_empty_bundle():
    assert not JitoBundleSender().send_bundle([]).ok


def test_build_sender_dispatches_by_name():
    assert isinstance(build_sender("rpc"), RpcSender)
    assert isinstance(build_sender("helius"), HeliusSender)
    assert isinstance(build_sender("jito"), JitoBundleSender)
