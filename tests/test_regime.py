"""Regime classification and its effect on aggression."""
from degen.signals.regime import BANDS, POLICY, RegimeState, classify


def test_bands_are_ordered_and_cover_the_unit_interval():
    edges = [b for b, _ in BANDS]
    assert edges == sorted(edges)
    assert edges[-1] >= 1.0


def test_classification_moves_through_every_band():
    assert classify(0.000) == "dead"
    assert classify(0.020) == "defensive"
    assert classify(0.060) == "normal"
    assert classify(0.500) == "aggressive"


def test_colder_regimes_trade_smaller_and_more_selectively():
    order = ["dead", "defensive", "normal", "aggressive"]
    sizes = [POLICY[b][0] for b in order]
    positions = [POLICY[b][1] for b in order]
    thresholds = [POLICY[b][2] for b in order]
    assert sizes == sorted(sizes)
    assert positions == sorted(positions)
    assert thresholds == sorted(thresholds, reverse=True)


def test_normal_regime_is_the_neutral_setting():
    assert POLICY["normal"] == (1.0, 1.0, 0.0)


def test_custom_bands_override_the_defaults():
    custom = ((0.5, "dead"), (0.6, "defensive"), (0.7, "normal"), (1.0, "aggressive"))
    assert classify(0.4, custom) == "dead"
    assert classify(0.65, custom) == "normal"


def test_describe_is_readable():
    s = RegimeState(band="defensive", grad_proxy=0.02, n_launches=500, n_reached=10, confident=True)
    text = s.describe()
    assert "defensive" in text and "2.00%" in text
