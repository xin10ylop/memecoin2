"""Deployer reputation, and its walk-forward discipline."""
from degen.signals.deployer import MIN_HISTORY, DeployerBook, evaluate_walk_forward


def _book(tmp_path, base=0.06):
    return DeployerBook(tmp_path / "dep.json", base_rate=base)


def test_an_unknown_creator_gets_the_prior_not_a_verdict(tmp_path):
    s = _book(tmp_path).score_at("nobody", 1_000.0)
    assert s["dev_score"] == 0.0 and not s["dev_known"]


def test_score_uses_only_launches_before_the_query_time(tmp_path):
    b = _book(tmp_path)
    for t in (10, 20, 30):
        b.observe("D", t, True)
    early = b.score_at("D", 15.0)
    late = b.score_at("D", 100.0)
    assert early["dev_prior_launches"] == 1
    assert late["dev_prior_launches"] == 3


def test_future_success_cannot_raise_a_past_score(tmp_path):
    b = _book(tmp_path)
    b.observe("D", 10, False)
    before = b.score_at("D", 50.0)
    b.observe("D", 100, True)          # a later win
    after = b.score_at("D", 50.0)      # same query time
    assert before == after


def test_a_short_record_is_shrunk_toward_the_base_rate(tmp_path):
    b = _book(tmp_path)
    b.observe("LUCKY", 1, True)
    b.observe("LUCKY", 2, True)
    s = b.score_at("LUCKY", 10.0)
    assert s["dev_success_rate"] < 0.5       # nowhere near the raw 100%
    assert not s["dev_known"]                # and not yet trusted at all


def test_a_long_good_record_outscores_a_short_perfect_one(tmp_path):
    b = _book(tmp_path)
    b.observe("SHORT", 1, True); b.observe("SHORT", 2, True); b.observe("SHORT", 3, True)
    for i in range(40):
        b.observe("LONG", 10 + i, i % 2 == 0)
    assert b.score_at("LONG", 1_000)["dev_score"] > b.score_at("SHORT", 1_000)["dev_score"]


def test_a_serial_failure_scores_negative(tmp_path):
    b = _book(tmp_path)
    for i in range(40):
        b.observe("FACTORY", i, False)
    s = b.score_at("FACTORY", 1_000)
    assert s["dev_known"] and s["dev_score"] < 0


def test_history_requirement_is_enforced(tmp_path):
    b = _book(tmp_path)
    for i in range(MIN_HISTORY - 1):
        b.observe("D", i, True)
    assert not b.score_at("D", 100)["dev_known"]
    b.observe("D", MIN_HISTORY, True)
    assert b.score_at("D", 100)["dev_known"]


def test_book_round_trips(tmp_path):
    b = _book(tmp_path)
    for i in range(5):
        b.observe("D", i, i % 2 == 0)
    b.save()
    b2 = DeployerBook(tmp_path / "dep.json")
    assert b2.score_at("D", 100)["dev_prior_launches"] == 5


def test_non_string_creator_is_ignored(tmp_path):
    b = _book(tmp_path)
    b.observe(None, 1, True)
    b.observe(float("nan"), 2, True)
    assert not b.devs


def test_walk_forward_evaluation_reports_insufficient_data_honestly(tmp_path):
    b = _book(tmp_path)
    res = evaluate_walk_forward(b, [{"dev": "D", "created_at": 1, "success": True}])
    assert "note" in res
