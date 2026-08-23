"""Wash-trade assessment."""
from degen.signals.washtrade import BASE_WASH_SHARE, assess, corrected_volume

ORGANIC = {
    "s5m_numBuys": 30, "s5m_numSells": 12, "s5m_numTraders": 28,
    "s5m_buyVolume": 4_000.0, "s5m_sellVolume": 1_200.0, "holder_count": 40,
}
PAINTED = {
    "s5m_numBuys": 60, "s5m_numSells": 58, "s5m_numTraders": 3,
    "s5m_buyVolume": 40_000.0, "s5m_sellVolume": 39_000.0, "holder_count": 3,
}


def test_organic_activity_scores_low():
    a = assess(ORGANIC)
    assert a.score < 0.25


def test_painted_activity_scores_high():
    a = assess(PAINTED)
    assert a.score > 0.7 and a.reasons


def test_base_discount_applies_even_when_nothing_looks_odd():
    a = assess(ORGANIC)
    assert a.discount < 1.0
    assert abs(a.discount - (1.0 - BASE_WASH_SHARE)) < 0.2


def test_suspicious_tokens_are_discounted_harder():
    assert assess(PAINTED).discount < assess(ORGANIC).discount


def test_corrected_volume_is_below_raw_volume():
    raw = ORGANIC["s5m_buyVolume"] + ORGANIC["s5m_sellVolume"]
    assert 0 < corrected_volume(ORGANIC) < raw


def test_volume_with_almost_no_holders_is_flagged():
    a = assess({"s5m_numBuys": 20, "s5m_numSells": 20, "s5m_numTraders": 2,
                "s5m_buyVolume": 5_000.0, "s5m_sellVolume": 5_000.0, "holder_count": 2})
    assert a.score >= 0.8


def test_missing_fields_do_not_crash():
    a = assess({})
    assert 0.0 <= a.score <= 1.0 and a.discount > 0


def test_trades_per_trader_is_reported():
    a = assess(ORGANIC)
    assert a.trades_per_trader == round(42 / 28, 3)
