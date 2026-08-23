"""The traction gate - the default, out-of-sample-validated entry rule."""
import pytest

from degen.signals.gate import GateConfig, TractionGate

PASSING = {"holder_count": 40, "liquidity": 8_000.0, "buy_sell_ratio": 0.65, "mcap": 12_000.0}


def test_a_token_meeting_all_four_conditions_passes():
    r = TractionGate().check(PASSING)
    assert r.passed and 0 < r.conviction <= 1.0


@pytest.mark.parametrize("field,bad", [
    ("holder_count", 5), ("liquidity", 900.0),
    ("buy_sell_ratio", 0.30), ("mcap", 1_000.0),
])
def test_failing_any_single_condition_rejects(field, bad):
    r = TractionGate().check({**PASSING, field: bad})
    assert not r.passed and r.conviction == 0.0
    assert any(field.split("_")[0] in x for x in r.failed)


def test_a_missing_value_is_a_failure_not_a_pass():
    for field in ("holder_count", "liquidity", "buy_sell_ratio", "mcap"):
        d = dict(PASSING); d[field] = None
        assert not TractionGate().check(d).passed
    assert not TractionGate().check({}).passed


def test_nan_is_treated_as_missing():
    assert not TractionGate().check({**PASSING, "liquidity": float("nan")}).passed


def test_conviction_rises_with_traction():
    weak = TractionGate().check({"holder_count": 21, "liquidity": 3_100.0,
                                 "buy_sell_ratio": 0.56, "mcap": 5_100.0})
    strong = TractionGate().check({"holder_count": 200, "liquidity": 40_000.0,
                                   "buy_sell_ratio": 0.95, "mcap": 60_000.0})
    assert strong.conviction > weak.conviction


def test_conviction_never_exceeds_one_or_drops_below_the_floor():
    cfg = GateConfig()
    huge = TractionGate().check({"holder_count": 1e6, "liquidity": 1e9,
                                 "buy_sell_ratio": 1.0, "mcap": 1e9})
    bare = TractionGate().check({"holder_count": 20, "liquidity": 3_000.0,
                                 "buy_sell_ratio": 0.55, "mcap": 5_000.0})
    assert huge.conviction <= 1.0
    assert bare.conviction == pytest.approx(cfg.conviction_floor, abs=1e-9)


def test_one_extreme_reading_cannot_carry_the_others():
    lopsided = TractionGate().check({"holder_count": 1e6, "liquidity": 3_000.0,
                                     "buy_sell_ratio": 0.55, "mcap": 5_000.0})
    assert lopsided.conviction < 0.65


def test_signal_interface_returns_none_on_reject():
    g = TractionGate()
    assert g.signal(PASSING) is not None
    assert g.signal({"holder_count": 1}) is None


def test_thresholds_are_configurable():
    strict = TractionGate(GateConfig(min_holders=500))
    assert not strict.check(PASSING).passed


def test_explain_is_readable():
    assert "pass" in TractionGate().check(PASSING).explain()
    assert "fail" in TractionGate().check({"holder_count": 1}).explain()
