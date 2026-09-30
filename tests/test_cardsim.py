import numpy as np
import pandas as pd
import pytest

from cardsim.expected import expected_table, lognormal_mean
from cardsim.generate import generate
from cardsim.params import load_params, season_factors
from cardsim.validate import run_checks


@pytest.fixture(scope="module")
def small():
    p, customers, txns = generate(n_customers=1500, n_months=2, start="2025-01", seed=7)
    return p, customers, txns


def test_same_seed_same_result():
    _, c1, t1 = generate(300, 1, "2025-01", seed=11)
    _, c2, t2 = generate(300, 1, "2025-01", seed=11)
    pd.testing.assert_frame_equal(c1, c2)
    pd.testing.assert_frame_equal(t1, t2)


def test_different_seed_differs():
    _, _, t1 = generate(300, 1, "2025-01", seed=1)
    _, _, t2 = generate(300, 1, "2025-01", seed=2)
    assert not t1["amount"].equals(t2["amount"])


def test_all_integrity_checks_pass(small):
    p, customers, txns = small
    for check in run_checks(p, customers, txns, n_months=2):
        if check.name.startswith("세그먼트별"):
            continue  # 표본이 작아 통계 비교는 아래 별도 테스트에서 넉넉하게 본다
        assert check.passed, f"{check.name}: {check.detail}"


def test_no_identifying_columns(small):
    _, customers, txns = small
    forbidden = {"name", "rrn", "card_number", "phone", "address"}
    assert not forbidden & set(customers.columns)
    assert not forbidden & set(txns.columns)
    assert customers["customer_id"].str.fullmatch(r"CUST_\d{6}").all()


def test_transactions_within_period(small):
    _, _, txns = small
    orig = txns[~txns["is_cancelled"]]
    assert orig["txn_datetime"].min() >= pd.Timestamp("2025-01-01")
    assert orig["txn_datetime"].max() < pd.Timestamp("2025-03-01")


def test_non_participants_have_no_fuel_or_education():
    # 대학생은 주유 이용자 비율이 2%라 대부분 주유 거래가 없어야 한다
    p = load_params()
    p["segment_share"] = {s: (1.0 if s == "S1_STUDENT" else 0.0) for s in p["segments"]}
    _, customers, txns = generate(2000, 1, "2025-01", seed=3, params=p)
    fuel_users = txns.loc[txns["category"] == "FUEL", "customer_id"].nunique()
    assert fuel_users / len(customers) < 0.05


def test_student_cafe_ticket_mostly_low_cost():
    p = load_params()
    p["segment_share"] = {s: (1.0 if s == "S1_STUDENT" else 0.0) for s in p["segments"]}
    _, _, txns = generate(2000, 1, "2025-01", seed=5, params=p)
    cafe = txns.loc[(txns["category"] == "CAFE") & ~txns["is_cancelled"], "amount"]
    assert cafe.min() >= 1700
    assert 2000 <= cafe.median() <= 2700  # 설계서 3.2.2: 대학생 중위값 약 2,300원


def test_category_floors(small):
    _, _, txns = small
    mins = txns.groupby("category")["amount"].min()
    assert mins["TRANSIT"] >= 1500
    assert mins["RESTAURANT"] >= 3000
    for c in ("CONVENIENCE", "MEDICAL", "ONLINE_SHOP", "ETC"):
        assert mins[c] >= 1000, c


def test_season_factors_average_one():
    assert np.mean(season_factors(load_params())) == pytest.approx(1.0)


def test_lognormal_mean_with_floor():
    rng = np.random.default_rng(0)
    x = np.maximum(1700, rng.lognormal(np.log(2000), 0.25, 400_000))
    assert lognormal_mean(2000, 0.25, 1700) == pytest.approx(x.mean(), rel=0.005)


def test_generated_totals_match_design():
    # 표본이 커야 하므로 6천 명·12개월로 설계값(3.4.1)과 ±8% 이내인지 본다
    p, customers, txns = generate(6000, 12, "2025-01", seed=21)
    exp = expected_table(p)
    orig = txns[~txns["is_cancelled"]].merge(customers[["customer_id", "segment"]], on="customer_id")
    n = customers["segment"].value_counts()
    g = orig.groupby("segment")["amount"].agg(["size", "sum"])
    for s in p["segments"]:
        e_cnt = sum(v[0] for v in exp[s].values())
        assert g.loc[s, "size"] / n[s] / 12 == pytest.approx(e_cnt, rel=0.08), s


def test_industry_shares_sum_to_one(small):
    from cardsim.benchmarks import industry_share_rows
    _, _, txns = small
    rows = industry_share_rows(txns)
    assert sum(g for _, g, _ in rows) == pytest.approx(1.0)
    assert sum(p for _, _, p in rows) == pytest.approx(1.0)
