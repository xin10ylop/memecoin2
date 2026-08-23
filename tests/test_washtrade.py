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


# ---------------- social disqualifiers ----------------

def test_repurposed_account_signature():
    from degen.signals.social import looks_repurposed
    # Old handle, crypto content only in the last week, long prior silence.
    assert looks_repurposed(account_age_days=900, first_crypto_post_age_days=5, silence_gap_days=200)
    # A genuinely old crypto account is not flagged.
    assert not looks_repurposed(account_age_days=900, first_crypto_post_age_days=800, silence_gap_days=0)
    # A new account is a different problem, not this one.
    assert not looks_repurposed(account_age_days=20, first_crypto_post_age_days=5, silence_gap_days=0)


def test_bot_amplification_flags_a_fresh_account_swarm():
    from degen.signals.social import Mention, bot_amplification
    ms = [Mention(f"buy {i}", author=f"a{i}", author_followers=5,
                  author_created_at=__import__("degen.util.timeutil", fromlist=["now"]).now() - 10*86400)
          for i in range(10)]
    r = bot_amplification(ms)
    assert r["flagged"] and r["fresh_frac"] > 0.5


def test_bot_amplification_flags_duplicate_text():
    from degen.signals.social import Mention, bot_amplification
    ms = [Mention("same exact shill text", author=f"a{i}") for i in range(10)]
    assert bot_amplification(ms)["dup_frac"] > 0.3


def test_bot_amplification_passes_an_organic_set():
    from degen.signals.social import Mention, bot_amplification
    import degen.util.timeutil as T
    ms = [Mention(f"genuinely different comment number {i} about the token", author=f"a{i}",
                  author_followers=4000, author_created_at=T.now() - 900*86400) for i in range(10)]
    assert not bot_amplification(ms)["flagged"]


def test_empty_mention_set_is_not_flagged():
    from degen.signals.social import bot_amplification
    assert not bot_amplification([])["flagged"]
