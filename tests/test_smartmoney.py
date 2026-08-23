"""Smart-money clustering and credit assignment."""
from degen.signals.smartmoney import MIN_WINNERS, SmartMoneyBook, WalletStat


def _book(tmp_path):
    return SmartMoneyBook(tmp_path / "sm.json")


def _add(book, wallet, tokens, creators, winners=None):
    s = WalletStat(wallet=wallet)
    s.tokens = list(tokens)
    s.creators = set(creators)
    s.winners = winners if winners is not None else len(tokens)
    s.total_seen = len(tokens)
    s.weighted_credit = 1.0 * s.winners
    s.last_seen = 1e9
    book.wallets[wallet] = s
    return s


def test_a_wallet_below_the_winner_floor_scores_zero(tmp_path):
    b = _book(tmp_path)
    s = _add(b, "W", ["t1", "t2"], ["c1", "c2"])
    assert s.winners < MIN_WINNERS
    assert s.score(1e9) == 0.0


def test_a_single_creator_wallet_is_treated_as_an_insider(tmp_path):
    b = _book(tmp_path)
    s = _add(b, "W", ["t1", "t2", "t3", "t4"], ["only_creator"])
    assert s.is_insider and s.score(1e9) == 0.0


def test_a_diversified_consistent_wallet_scores(tmp_path):
    b = _book(tmp_path)
    s = _add(b, "W", [f"t{i}" for i in range(8)], [f"c{i}" for i in range(8)])
    assert s.score(1e9) > 0


def test_hit_rate_is_shrunk_toward_the_base_rate(tmp_path):
    b = _book(tmp_path)
    lucky = _add(b, "L", ["t1", "t2"], ["c1", "c2"], winners=2)
    steady = _add(b, "S", [f"t{i}" for i in range(40)], [f"c{i}" for i in range(40)], winners=15)
    # 2/2 is a perfect record but on no evidence; it must not outrank 15/40.
    assert lucky.hit_rate < steady.hit_rate


def test_credit_decays_with_age(tmp_path):
    b = _book(tmp_path)
    s = _add(b, "W", [f"t{i}" for i in range(8)], [f"c{i}" for i in range(8)])
    fresh = s.score(s.last_seen)
    stale = s.score(s.last_seen + 60 * 86400)
    assert 0 < stale < fresh / 4


def test_identical_wallets_collapse_into_one_actor(tmp_path):
    b = _book(tmp_path)
    toks = ["a", "b", "c", "d", "e"]
    for i in range(7):
        _add(b, f"bundle{i}", toks, [f"c{j}" for j in range(5)])
    clusters = b.cluster_wallets()
    assert len(set(clusters.values())) == 1, "a bundle must count as one actor"


def test_genuinely_independent_wallets_stay_separate(tmp_path):
    b = _book(tmp_path)
    _add(b, "A", ["t1", "t2", "t3", "t4"], ["c1", "c2", "c3", "c4"])
    _add(b, "B", ["t5", "t6", "t7", "t8"], ["c5", "c6", "c7", "c8"])
    clusters = b.cluster_wallets()
    assert len(set(clusters.values())) == 2


def test_clustering_is_transitive(tmp_path):
    b = _book(tmp_path)
    # A~B and B~C by overlap, so all three are one actor even if A and C
    # were never compared directly.
    _add(b, "A", ["t1", "t2", "t3", "t4", "t5"], ["c1", "c2"])
    _add(b, "B", ["t1", "t2", "t3", "t4", "t5"], ["c1", "c2"])
    _add(b, "C", ["t1", "t2", "t3", "t4", "t5"], ["c1", "c2"])
    clusters = b.cluster_wallets()
    assert len(set(clusters.values())) == 1


def test_book_round_trips_through_disk(tmp_path):
    b = _book(tmp_path)
    _add(b, "W", ["t1", "t2", "t3", "t4"], ["c1", "c2", "c3", "c4"])
    b.scanned.add("t1")
    b.save()
    b2 = SmartMoneyBook(tmp_path / "sm.json")
    assert "W" in b2.wallets and b2.wallets["W"].winners == 4
    assert "t1" in b2.scanned


def test_nan_creator_does_not_break_saving(tmp_path):
    b = _book(tmp_path)
    b.scanned.add("m1")           # so observe() short-circuits without network
    b.observe("m1", 3.0, creator=float("nan"))
    b.save()                       # must not raise on an unsortable creator set
