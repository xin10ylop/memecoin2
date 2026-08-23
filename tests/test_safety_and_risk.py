"""Safety filters and portfolio risk."""
import pytest

from degen.risk.manager import RiskConfig, RiskManager
from degen.safety.filters import SafetyConfig, check_local

GOOD = {
    "mint": "M", "liquidity": 12_000.0, "holder_count": 40.0, "trades_total": 30.0,
    "s5m_numTraders": 20.0, "buy_sell_ratio": 0.65, "top_holders_pct": 30.0,
    "dev_balance_pct": 2.0, "dev_mints": 3.0, "mint_auth_disabled": True,
    "freeze_auth_disabled": True, "token_program": "Tokenkeg", "has_socials": True,
}


def test_a_healthy_token_passes():
    assert check_local(GOOD).ok


def test_thin_liquidity_is_rejected():
    assert not check_local({**GOOD, "liquidity": 500.0}).ok


def test_live_mint_authority_is_rejected():
    r = check_local({**GOOD, "mint_auth_disabled": False})
    assert not r.ok and any("mint authority" in x for x in r.rejects)


def test_live_freeze_authority_is_rejected():
    assert not check_local({**GOOD, "freeze_auth_disabled": False}).ok


def test_token_factory_creator_is_rejected():
    r = check_local({**GOOD, "dev_mints": 9_000.0})
    assert not r.ok and any("factory" in x for x in r.rejects)


def test_dev_holding_a_large_share_is_rejected():
    assert not check_local({**GOOD, "dev_balance_pct": 40.0}).ok


def test_heavy_net_selling_is_rejected():
    assert not check_local({**GOOD, "buy_sell_ratio": 0.1}).ok


def test_nan_fields_do_not_crash_the_filter():
    import math
    noisy = {k: (math.nan if k not in ("mint", "token_program") else v) for k, v in GOOD.items()}
    r = check_local(noisy)
    assert isinstance(r.ok, bool)      # rejects, but must not raise


def test_token_2022_is_flagged_not_silently_accepted():
    r = check_local({**GOOD, "token_program": "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"})
    assert r.detail.get("requires_extension_check") or not r.ok


def test_missing_data_never_counts_as_a_pass():
    assert not check_local({"mint": "M"}).ok


# ---------------- risk ----------------

def test_size_scales_with_conviction():
    rm = RiskManager(RiskConfig(bankroll_sol=10.0))
    hi, _ = rm.size_for(1.0)
    lo, _ = rm.size_for(0.5)
    assert hi > lo > 0


def test_size_is_capped_by_pool_depth():
    rm = RiskManager(RiskConfig(bankroll_sol=100.0, max_size_sol=10.0))
    big, _ = rm.size_for(1.0, pool_sol_reserve=10_000)
    small, _ = rm.size_for(1.0, pool_sol_reserve=5.0)
    assert small < big


def test_negative_edge_kelly_refuses_to_size():
    rm = RiskManager(RiskConfig(bankroll_sol=10.0))
    size, why = rm.size_for(1.0, win_prob=0.02, win_mult=2.0)
    assert size == 0.0 and "negative edge" in why


def test_positive_edge_kelly_allows_a_size():
    rm = RiskManager(RiskConfig(bankroll_sol=10.0))
    size, _ = rm.size_for(1.0, win_prob=0.45, win_mult=4.0)
    assert size > 0


def test_one_position_per_creator():
    rm = RiskManager()
    rm.on_entry("A", 0.1, creator="dev1")
    ok, why = rm.can_enter("B", creator="dev1")
    assert not ok and "creator" in why


def test_max_open_positions_enforced():
    rm = RiskManager(RiskConfig(max_open_positions=2, min_seconds_between_entries=0))
    rm.on_entry("A", 0.1, creator="d1")
    rm.on_entry("B", 0.1, creator="d2")
    ok, why = rm.can_enter("C", creator="d3")
    assert not ok and "max open" in why


def test_consecutive_losses_halt_trading():
    rm = RiskManager(RiskConfig(max_consecutive_losses=3))
    for i in range(3):
        rm.on_entry(f"T{i}", 0.1, creator=f"d{i}")
        rm.on_exit(f"T{i}", -0.05)
    assert rm.state.halted
    assert not rm.can_enter("X")[0]


def test_daily_loss_limit_halts_trading():
    rm = RiskManager(RiskConfig(bankroll_sol=10.0, daily_loss_limit_frac=0.10, max_consecutive_losses=99))
    rm.on_entry("A", 2.0, creator="d")
    rm.on_exit("A", -1.5)
    assert rm.state.halted and "daily" in rm.state.halt_reason


def test_a_win_resets_the_loss_streak():
    rm = RiskManager(RiskConfig(max_consecutive_losses=5))
    rm.on_entry("A", 0.1, creator="d1"); rm.on_exit("A", -0.05)
    rm.on_entry("B", 0.1, creator="d2"); rm.on_exit("B", +0.20)
    assert rm.state.consecutive_losses == 0


def test_repeated_fill_failures_halt_trading():
    rm = RiskManager(RiskConfig(halt_on_fill_failures=3))
    for _ in range(3):
        rm.on_fill_failure()
    assert rm.state.halted


def test_operator_can_resume_after_a_halt():
    rm = RiskManager(RiskConfig(max_consecutive_losses=1))
    rm.on_entry("A", 0.1, creator="d"); rm.on_exit("A", -0.05)
    assert rm.state.halted
    rm.resume()
    assert not rm.state.halted
    # The independent entry-rate limit may still be in force; what matters is
    # that the block is no longer the halt.
    ok, why = rm.can_enter("Z")
    assert ok or "halted" not in why


# ---------------- capital-at-risk accounting ----------------

def test_a_recovered_position_frees_its_slot():
    rm = RiskManager(RiskConfig(max_open_positions=1, min_seconds_between_entries=0))
    rm.on_entry("A", 1.0, creator="d1")
    assert not rm.can_enter("B", creator="d2")[0]
    rm.on_partial_exit("A", 1.2)          # cost basis returned, running on house money
    assert rm.at_risk_positions() == 0
    assert rm.can_enter("B", creator="d2")[0]


def test_a_partially_recovered_position_still_holds_its_slot():
    rm = RiskManager(RiskConfig(max_open_positions=1, min_seconds_between_entries=0))
    rm.on_entry("A", 1.0, creator="d1")
    rm.on_partial_exit("A", 0.4)
    assert rm.at_risk_positions() == 1
    assert not rm.can_enter("B", creator="d2")[0]


def test_exposure_is_net_of_proceeds_banked():
    rm = RiskManager(RiskConfig(min_seconds_between_entries=0))
    rm.on_entry("A", 1.0, creator="d1")
    rm.on_entry("B", 1.0, creator="d2")
    assert rm.exposure() == pytest.approx(2.0)
    rm.on_partial_exit("A", 0.6)
    assert rm.exposure() == pytest.approx(1.4)


def test_house_money_residuals_are_still_bounded():
    rm = RiskManager(RiskConfig(max_open_positions=2, max_tracked_positions=3,
                                min_seconds_between_entries=0))
    for i in range(3):
        rm.on_entry(f"T{i}", 1.0, creator=f"d{i}")
        rm.on_partial_exit(f"T{i}", 2.0)      # all recovered
    assert rm.at_risk_positions() == 0
    ok, why = rm.can_enter("NEW", creator="dz")
    assert not ok and "tracked" in why
