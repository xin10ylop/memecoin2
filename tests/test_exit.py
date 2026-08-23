"""Exit policy behaviour. These encode the rules we actually want."""
from degen.risk.exit import ExitConfig, Position, evaluate

CFG = ExitConfig()


def _pos(entry=1.0, liq=20_000.0):
    return Position("M", entry, 0.0, 1_000_000, 1.0, liq)


def _drive(path, cfg=CFG, liq=20_000.0, dt=60):
    """Walk a price path, returning the list of (price, frac, reason) sells."""
    pos = _pos(liq=liq)
    sells, t = [], 0
    for px in path:
        t += dt
        d = evaluate(pos, px, liq, t, cfg)
        while d and pos.remaining_frac > 1e-9:
            sells.append((px, d.sell_frac_of_original, d.reason))
            pos.remaining_frac -= d.sell_frac_of_original
            if d.reason in ("ladder", "cost_recovery"):
                pos.rungs_hit += 1
            if d.close:
                break
            d = evaluate(pos, px, liq, t, cfg)
        if pos.remaining_frac <= 1e-9:
            break
    return sells, pos


def test_first_rung_recovers_cost_basis():
    sells, _ = _drive([1.0, 1.7])
    assert sells and sells[0][2] == "cost_recovery"
    # selling 40% at 1.6x returns 64% of stake; enough to de-risk materially
    assert sells[0][1] == CFG.ladder[0][1]


def test_hard_stop_fires_on_a_collapse():
    sells, pos = _drive([1.0, 0.9, 0.5])
    assert sells[-1][2] == "hard_stop"
    assert pos.remaining_frac <= 1e-9


def test_time_stop_closes_a_position_that_never_moved():
    cfg = ExitConfig(time_stop_s=120, time_stop_min_x=1.10)
    sells, _ = _drive([1.0, 1.01, 1.02, 1.0], cfg=cfg)
    assert sells[-1][2] == "time_stop"


def test_time_stop_spares_a_position_that_is_working():
    cfg = ExitConfig(time_stop_s=120, time_stop_min_x=1.10)
    sells, _ = _drive([1.0, 1.3, 1.4], cfg=cfg)
    assert all(s[2] != "time_stop" for s in sells)


def test_trailing_stop_is_not_armed_before_the_first_rung():
    # A 40% wick straight down from entry must not trigger a trail exit,
    # because new tokens do that constantly.
    cfg = ExitConfig(hard_stop_x=0.1)
    sells, _ = _drive([1.0, 1.3, 0.75], cfg=cfg)
    assert all(s[2] != "trail" for s in sells)


def test_trailing_stop_arms_after_a_rung_and_protects_gains():
    sells, _ = _drive([1.0, 1.7, 3.0, 1.5])
    assert any(s[2] == "trail" for s in sells)


def test_liquidity_collapse_exits_immediately():
    pos = _pos(liq=20_000.0)
    d = evaluate(pos, 1.5, 5_000.0, 30, CFG)   # pool down to 25% of entry
    assert d and d.reason == "liquidity_collapse" and d.close


def test_unexitable_pool_exits_regardless_of_price():
    pos = _pos(liq=1_000.0)
    d = evaluate(pos, 5.0, 100.0, 30, CFG)
    assert d and d.reason == "liquidity_collapse"


def test_a_round_tripping_runner_still_banks_a_profit():
    """The failure mode the whole policy exists to prevent."""
    path = [1.0, 1.2, 1.7, 2.6, 4.5, 9.0, 11.0, 6.0, 3.0, 1.0]
    sells, _ = _drive(path)
    realized = sum(frac * px for px, frac, _ in sells)
    assert realized > 2.0, f"round-tripped to {realized:.2f}x of stake"


def test_ladder_never_sells_more_than_the_position():
    sells, pos = _drive([1.0, 1.7, 2.6, 4.2, 9.0, 25.0, 60.0])
    assert sum(f for _, f, _ in sells) <= 1.0 + 1e-9
    assert pos.remaining_frac >= -1e-9


def test_no_decision_on_a_closed_position():
    pos = _pos()
    pos.remaining_frac = 0.0
    assert evaluate(pos, 100.0, 20_000.0, 10, CFG) is None


# ---------------- dump detection ----------------

def test_dump_detector_fires_on_a_coordinated_sell():
    cfg = ExitConfig(hard_stop_x=0.01, time_stop_s=1e9, dump_min_obs=6)
    # Calm drift builds a tight variance estimate, then a violent leg down.
    path = [1.0, 1.01, 1.005, 1.02, 1.015, 1.03, 1.025, 1.04, 1.035, 0.72]
    sells, _ = _drive(path, cfg=cfg)
    assert sells and sells[-1][2] == "dump_detected"


def test_dump_detector_ignores_ordinary_volatility():
    cfg = ExitConfig(hard_stop_x=0.01, time_stop_s=1e9, dump_min_obs=6)
    path = [1.0, 1.3, 0.9, 1.4, 0.95, 1.5, 1.0, 1.6, 1.1, 1.45]
    sells, _ = _drive(path, cfg=cfg)
    assert all(s[2] != "dump_detected" for s in sells)


def test_dump_detector_needs_a_minimum_absolute_move():
    cfg = ExitConfig(hard_stop_x=0.01, time_stop_s=1e9, dump_min_obs=5, dump_min_move=0.5)
    path = [1.0, 1.001, 1.002, 1.001, 1.003, 1.002, 1.004, 0.80]
    sells, _ = _drive(path, cfg=cfg)
    assert all(s[2] != "dump_detected" for s in sells)


def test_dump_detector_can_be_disabled():
    cfg = ExitConfig(hard_stop_x=0.01, time_stop_s=1e9, dump_min_obs=6, dump_detect=False)
    path = [1.0, 1.01, 1.005, 1.02, 1.015, 1.03, 1.025, 1.04, 1.035, 0.72]
    sells, _ = _drive(path, cfg=cfg)
    assert all(s[2] != "dump_detected" for s in sells)


def test_dump_detector_is_silent_before_enough_observations():
    cfg = ExitConfig(hard_stop_x=0.01, time_stop_s=1e9, dump_min_obs=20)
    sells, _ = _drive([1.0, 1.01, 1.02, 0.5], cfg=cfg)
    assert all(s[2] != "dump_detected" for s in sells)


def test_our_own_fill_does_not_feed_the_dump_detector():
    """A laddered sell moves the pool. Re-evaluating at that moved price must
    not enter the control chart, or the exit triggers its own dump signal."""
    cfg = ExitConfig(hard_stop_x=0.01, time_stop_s=1e9, dump_min_obs=5)
    pos = _pos()
    for i, px in enumerate([1.0, 1.01, 1.02, 1.015, 1.03, 1.025, 1.04], start=1):
        evaluate(pos, px, 20_000.0, i * 60, cfg)
    n_before = len(pos.returns)
    evaluate(pos, 0.60, 20_000.0, 500, cfg, record=False)   # our own impact
    assert len(pos.returns) == n_before, "self-impact leaked into the return series"


def test_market_observations_are_still_recorded():
    cfg = ExitConfig(hard_stop_x=0.01, time_stop_s=1e9)
    pos = _pos()
    evaluate(pos, 1.1, 20_000.0, 60, cfg)
    evaluate(pos, 1.2, 20_000.0, 120, cfg)
    assert len(pos.returns) == 2


def test_record_false_still_returns_a_decision():
    cfg = ExitConfig(hard_stop_x=0.55, time_stop_s=1e9)
    pos = _pos()
    d = evaluate(pos, 0.4, 20_000.0, 60, cfg, record=False)
    assert d is not None and d.reason == "hard_stop"


def test_the_trail_widens_as_the_run_extends():
    """Realised volatility rises with the multiple, so the width that survives a
    run's own noise must widen. An earlier version tightened it, which cut the
    tail that supplies the entire edge."""
    cfg = ExitConfig()
    widths = [w for _, w in cfg.trail_schedule]
    assert widths == sorted(widths), "trail must widen, not tighten, as the peak rises"


def test_only_one_ladder_rung_by_default():
    """Above ~2x the census's local Pareto exponent falls below 1, so selling
    the marginal unit is value-destroying. Only the cost-recovery rung remains."""
    cfg = ExitConfig()
    assert len(cfg.ladder) == 1
    assert cfg.ladder[0][0] < 2.0


def test_cost_recovery_still_fires_and_the_rest_rides():
    sells, pos = _drive([1.0, 1.7, 2.5, 4.0, 9.0])
    assert sells[0][2] == "cost_recovery"
    # No further ladder sells; whatever exits later does so via the trail.
    assert sum(1 for _, _, r in sells if r in ("ladder", "cost_recovery")) == 1
