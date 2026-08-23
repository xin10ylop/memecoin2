"""Leakage tests.

The single failure mode that makes a trading backtest worthless is letting
information from after the decision point into the features. These tests exist
to make that impossible to reintroduce silently.
"""
import numpy as np
import pandas as pd
import pytest

from degen.features.build import build_panel, features_at, label_forward


def _hist(n=20, start_age=10, step=30, price_fn=None):
    price_fn = price_fn or (lambda i: 1e-6 * (1 + 0.05 * i))
    return pd.DataFrame([
        {
            "mint": "M", "symbol": "T", "observed_at": 1_000 + start_age + i * step,
            "age_s": start_age + i * step, "created_at": 1_000,
            "price_usd": price_fn(i), "liquidity": 5_000 + 100 * i,
            "holder_count": 5 + 2 * i, "s5m_numBuys": 3 + i, "s5m_numSells": 1,
            "s5m_numTraders": 2 + i, "s5m_buyVolume": 10.0 + i, "s5m_sellVolume": 2.0,
            "dev_mints": 2, "mcap": 20_000.0, "token_program": "Tokenkeg",
            "mint_auth_disabled": True, "freeze_auth_disabled": True,
        }
        for i in range(n)
    ])


def test_features_ignore_everything_after_the_decision_age():
    h = _hist()
    age = 200.0
    base = features_at(h, age)
    # Rewrite the entire future to absurd values. Features must not move.
    tampered = h.copy()
    fut = tampered["age_s"] > age
    tampered.loc[fut, "price_usd"] *= 1_000_000
    tampered.loc[fut, "liquidity"] = 9e9
    tampered.loc[fut, "holder_count"] = 999_999
    after = features_at(tampered, age)
    assert base is not None and after is not None
    for k, v in base.items():
        if isinstance(v, float) and np.isfinite(v):
            assert after[k] == pytest.approx(v, rel=1e-12), f"feature {k} leaked the future"
        else:
            assert after[k] == v, f"feature {k} leaked the future"


def test_truncating_the_future_does_not_change_features():
    h = _hist()
    age = 200.0
    full = features_at(h, age)
    truncated = features_at(h[h.age_s <= age], age)
    assert full == truncated


def test_labels_use_only_the_future():
    h = _hist()
    age = 200.0
    f = features_at(h, age)
    lab = label_forward(h, age, 3600, f["price_usd"])
    past_max = h[h.age_s <= age]["price_usd"].max()
    future_max = h[(h.age_s > age)]["price_usd"].max()
    assert lab["max_x"] == pytest.approx(future_max / f["price_usd"])
    assert lab["max_x"] != pytest.approx(past_max / f["price_usd"])


def test_label_horizon_is_respected():
    h = _hist(n=40)
    lab_short = label_forward(h, 100, 120, 1e-6)
    lab_long = label_forward(h, 100, 3600, 1e-6)
    assert lab_short["n_future_obs"] < lab_long["n_future_obs"]
    assert lab_short["max_x"] <= lab_long["max_x"]


def test_no_row_is_emitted_when_the_token_was_not_observed_at_the_age():
    # Token first seen at 500s cannot have a 60s decision row.
    h = _hist(start_age=500)
    panel = build_panel(h, ages=(60,), horizon_s=3600)
    assert panel.empty


def test_panel_requires_future_observations():
    h = _hist(n=4, start_age=10, step=30)     # ends at age 100
    panel = build_panel(h, ages=(90,), horizon_s=3600, min_future_obs=3)
    assert panel.empty


def test_obs_lag_reports_staleness_honestly():
    h = _hist(start_age=10, step=100)
    f = features_at(h, 250.0)
    # last observation at or before 250 is age 210, so lag is 40s
    assert f["obs_age"] == 210
    assert f["obs_lag"] == pytest.approx(40.0)


def test_price_from_peak_never_exceeds_one():
    h = _hist(price_fn=lambda i: 1e-6 * (1 + 0.5 * i if i < 5 else 1.0))
    f = features_at(h, 600)
    assert 0 < f["px_from_peak"] <= 1.0 + 1e-12


def test_slopes_have_the_right_sign():
    rising = features_at(_hist(price_fn=lambda i: 1e-6 * (1 + 0.1 * i)), 400)
    falling = features_at(_hist(price_fn=lambda i: 1e-6 / (1 + 0.1 * i)), 400)
    assert rising["px_slope"] > 0 > falling["px_slope"]
    assert rising["hold_slope"] > 0


def test_missing_price_yields_no_features():
    h = _hist()
    h["price_usd"] = 0.0
    assert features_at(h, 200) is None
