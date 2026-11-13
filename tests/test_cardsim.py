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


def test_all_products_valid():
    from cardsim.products import load_all_products
    products = load_all_products()
    assert {"shinhan_mr_life", "samsung_taptap_o", "hyundai_zero3_discount",
            "hyundai_zero3_points", "kb_nori2_check"} <= set(products)


def test_product_validation_catches_typos():
    import copy, json
    from cardsim.params import load_params
    from cardsim.products import PRODUCTS_DIR, ProductError, load_shares, validate_product
    params, shares = load_params(), load_shares()
    cats = set(params["categories"])
    base = json.load(open(PRODUCTS_DIR / "kb_nori2_check.json", encoding="utf-8"))
    cases = [
        lambda p: p["benefits"][0]["match"][0].update(category="COFFEE"),          # 없는 업종
        lambda p: p["benefits"][0]["match"][0].update(merchant_share="starbucks"),  # 없는 비중
        lambda p: p["benefits"][1].update(pools=["nope"]),                          # 없는 한도
        lambda p: p["benefits"][1].update(min_tier=250000),                         # 없는 구간
        lambda p: p["benefits"][4]["match"][0].update(merchant_share="telecom_mno3"),  # customer 비중을 txn으로
    ]
    for broken in cases:
        p = copy.deepcopy(base)
        broken(p)
        with pytest.raises(ProductError):
            validate_product(p, cats, shares)


def test_merchant_shares_within_range():
    from cardsim.products import load_shares
    shares = load_shares()
    for key, spec in shares["shares"].items():
        lo, hi = spec["range"]
        assert 0 <= lo <= spec["value"] <= hi <= 1, key


def test_pnl_assumptions_valid():
    from cardsim.products import load_pnl_assumptions
    rates = load_pnl_assumptions()["rates"]
    for key in ("merchant_fee_rate_credit", "merchant_fee_rate_check", "funding_rate_annual",
                "credit_loss_rate", "float_days", "point_cost_per_point"):
        assert key in rates
    assert rates["merchant_fee_rate_check"]["value"] < rates["merchant_fee_rate_credit"]["value"]


def _tiny_case(rows):
    """고객 1명, 신용카드 거래 목록 [(일시, 업종, 금액), ...]으로 시뮬레이터 입력을 만든다."""
    from cardsim.simulate import prepare
    customers = pd.DataFrame({"customer_id": ["C1"], "segment": ["S2_YOUNG_WORKER"], "has_credit_card": [True]})
    txns = pd.DataFrame({
        "txn_id": [f"T{i}" for i in range(len(rows))], "customer_id": "C1",
        "txn_datetime": pd.to_datetime([r[0] for r in rows]), "category": [r[1] for r in rows],
        "amount": [r[2] for r in rows], "channel": "offline", "payment_type": "credit", "is_overseas": False,
        "installment_months": 0, "is_cancelled": False, "excluded_from_performance": False, "original_txn_id": None,
    })
    return prepare(customers, txns, "2025-01", 1)


def _tiny_product(benefits, pools, tiers=(), grace=None):
    return {"id": "x", "name": "x", "card_kind": "credit", "annual_fee": 0, "source": "test",
            "performance": {"tiers": list(tiers), "exclude_categories": [], "grace": grace},
            "benefits": benefits, "pools": pools}


def test_benefit_daily_monthly_limits_and_pool():
    from cardsim.products import load_shares
    from cardsim.simulate import load_behavior, run_product
    # 1월: 같은 날 3건(일 1회라 1건만) + 다른 날 6건(월 5회 제한) → 할인 5건 × 1,000원, 한도 3,000원
    rows = [("2025-01-02 12:00", "CONVENIENCE", 20000)] * 3 + [(f"2025-01-{d:02d} 12:00", "CONVENIENCE", 20000) for d in range(5, 11)]
    cust, t = _tiny_case(rows)
    ben = [{"id": "conv", "kind": "rate", "rate": 0.10, "match": [{"category": "CONVENIENCE"}],
            "max_eligible_per_txn": 10000, "per_day": 1, "per_month": 5, "min_tier": None, "pools": ["p"]}]
    q, _ = run_product(_tiny_product(ben, {"p": {"0": 3000}}), cust, t, load_shares(), load_behavior(), 1, 1, all_on_card=True)
    assert q["disc"].sum() == pytest.approx(3000)
    q, _ = run_product(_tiny_product(ben, {"p": {"0": 100000}}), cust, t, load_shares(), load_behavior(), 1, 1, all_on_card=True)
    assert q["disc"].sum() == pytest.approx(5000)


def test_benefit_fixed_amount_and_previous_month_tier():
    from cardsim.products import load_shares
    from cardsim.simulate import load_behavior, run_product
    rows = [("2025-01-03 10:00", "ETC", 400000),
            ("2025-01-10 10:00", "DELIVERY", 9000), ("2025-01-11 10:00", "DELIVERY", 12000),
            ("2025-02-10 10:00", "DELIVERY", 9000), ("2025-02-11 10:00", "DELIVERY", 12000), ("2025-02-12 10:00", "DELIVERY", 15000)]
    cust, t = _tiny_case(rows)
    ben = [{"id": "dlv", "kind": "fixed", "amount": 1000, "match": [{"category": "DELIVERY"}],
            "min_txn_amount": 10000, "per_month": 1, "min_tier": 300000, "pools": ["p"]}]
    prod = _tiny_product(ben, {"p": {"300000": 1000}}, tiers=[300000])
    q, _ = run_product(prod, cust, t, load_shares(), load_behavior(), 1, 2, all_on_card=True)
    # 1월은 전월실적이 없어 구간 미달, 2월은 1월 실적 42.1만 원으로 30만 원 구간 → 1만 원 이상 첫 건만 1,000원
    assert q["disc"].xs(0, level="m").sum() == 0
    assert q["disc"].xs(1, level="m").sum() == pytest.approx(1000)


def test_transit_aggregated_per_month():
    # 대중교통 3회(1월) + 2회(2월) → 고객·월별 1건씩, 금액 합산, 말일 23시
    rows = [("2025-01-03 08:00", "TRANSIT", 1500), ("2025-01-04 08:00", "TRANSIT", 1500), ("2025-01-20 08:00", "TRANSIT", 1700),
            ("2025-02-03 08:00", "TRANSIT", 1500), ("2025-02-04 08:00", "TRANSIT", 1500), ("2025-01-05 12:00", "CAFE", 5000)]
    _, t = _tiny_case(rows)
    tr = t[t["category"] == "TRANSIT"].sort_values("month")
    assert tr["amount"].tolist() == [4700, 3000]
    assert tr["rides"].tolist() == [3, 2]
    assert tr["txn_datetime"].iloc[0] == pd.Timestamp("2025-01-31 23:00")


def test_discounted_txn_excluded_from_performance():
    from cardsim.products import load_shares
    from cardsim.simulate import load_behavior, run_product
    # 1월 카페 10만 원 할인 + 기타 25만 원. 할인 건을 빼면 실적 25만 원 → 2월 구간 미달
    rows = [("2025-01-05 12:00", "CAFE", 100000), ("2025-01-06 12:00", "ETC", 250000), ("2025-02-05 12:00", "CAFE", 10000)]
    cust, t = _tiny_case(rows)
    ben = [{"id": "cafe", "kind": "rate", "rate": 0.10, "match": [{"category": "CAFE"}], "min_tier": 300000, "pools": []}]
    grace = {"months": 1, "tier": 300000}
    prod = _tiny_product(ben, {}, tiers=[300000], grace=grace)
    q, _ = run_product(prod, cust, t, load_shares(), load_behavior(), 1, 2, all_on_card=True)
    assert q["disc"].xs(1, level="m").sum() == pytest.approx(1000)  # 할인 건도 실적 포함 → 35만 원
    prod["performance"]["exclude_discounted"] = True
    q, _ = run_product(prod, cust, t, load_shares(), load_behavior(), 1, 2, all_on_card=True)
    assert q["disc"].xs(0, level="m").sum() == pytest.approx(10000)  # 1월은 신규 면제
    assert q["disc"].xs(1, level="m").sum() == 0


def test_day_pool_channel_and_exclude():
    from cardsim.products import load_shares
    from cardsim.simulate import load_behavior, run_product
    rows = [("2025-01-02 12:00", "RESTAURANT", 10000)] * 3 + [("2025-01-03 12:00", "RESTAURANT", 10000), ("2025-01-03 13:00", "TRANSIT", 1500)]
    cust, t = _tiny_case(rows)
    ben = [{"id": "r", "kind": "points", "rate": 0.05, "match": [{"category": "*", "channel": "offline"}],
            "exclude": [{"category": "TRANSIT"}], "pools": ["d"]}]
    q, _ = run_product(_tiny_product(ben, {"d": {"period": "day", "0": 1000}}), cust, t, load_shares(), load_behavior(), 1, 1, all_on_card=True)
    assert q["pts"].sum() == pytest.approx(1000 + 500)  # 첫날 1,500 → 1,000, 둘째 날 500, 대중교통 제외
    ben[0]["match"][0]["channel"] = "online"
    q, _ = run_product(_tiny_product(ben, {"d": {"period": "day", "0": 1000}}), cust, t, load_shares(), load_behavior(), 1, 1, all_on_card=True)
    assert q["pts"].sum() == 0


def test_top_of_group_only_largest_area():
    from cardsim.products import load_shares
    from cardsim.simulate import load_behavior, run_product
    rows = [("2025-01-02 12:00", "CAFE", 30000), ("2025-01-03 12:00", "CONVENIENCE", 10000)]
    cust, t = _tiny_case(rows)
    ben = [{"id": "c", "kind": "points", "rate": 0.01, "top_of": "g", "match": [{"category": "CAFE"}], "pools": []},
           {"id": "v", "kind": "points", "rate": 0.01, "top_of": "g", "match": [{"category": "CONVENIENCE"}], "pools": []}]
    q, used = run_product(_tiny_product(ben, {}), cust, t, load_shares(), load_behavior(), 1, 1, all_on_card=True)
    assert q["pts"].sum() == pytest.approx(300)
    assert "v" not in used


def test_advisor_profile_and_leakage():
    from cardsim.advisor import Population, evaluate
    pop = Population(n_customers=800, seed=3)
    g = pop.lookalikes("30s", 1_000_000, n=20)
    assert len(g) == 20 and g["scale"].between(0.5, 2.0).all()
    df, details = evaluate(pop, g, ["shinhan_mr_life", "hyundai_zero3_discount", "kb_nori2_check"])
    assert set(df["id"]) == {"shinhan_mr_life", "hyundai_zero3_discount", "kb_nori2_check"}
    assert (df["실적 미달 누수"] >= 0).all() and (df["한도·횟수 누수"] >= 0).all()
    zero = df.set_index("id").loc["hyundai_zero3_discount"]
    assert zero["실적 미달 누수"] == 0 and zero["한도·횟수 누수"] == 0  # 무실적·무한도
    assert zero["혜택률"] == pytest.approx(0.008, rel=0.01)
    assert (df["월 사용액"] / 1_000_000).between(0.6, 1.4).all()


def test_every_nth_amount_by_txn_and_ride_days():
    from cardsim.products import load_shares
    from cardsim.simulate import load_behavior, run_product
    rows = ([(f"2025-01-{d:02d} 12:00", "ETC", 12000) for d in range(1, 8)]          # 7건 → 3·6번째만
            + [("2025-01-10 12:00", "CAFE", 2000), ("2025-01-11 12:00", "CAFE", 5000), ("2025-01-12 12:00", "CAFE", 15000)]
            + [("2025-01-15 08:00", "TRANSIT", 1500), ("2025-01-15 18:00", "TRANSIT", 1500), ("2025-01-16 08:00", "TRANSIT", 1500)])
    cust, t = _tiny_case(rows)
    sh, bh = load_shares(), load_behavior()
    nth = [{"id": "s", "kind": "fixed", "amount": 1000, "every_nth": 3, "match": [{"category": "ETC"}], "pools": []}]
    q, _ = run_product(_tiny_product(nth, {}), cust, t, sh, bh, 1, 1, all_on_card=True)
    assert q["disc"].sum() == pytest.approx(2000)
    tiered = [{"id": "c", "kind": "fixed", "amount_by_txn": [[3000, 100], [10000, 500]], "min_txn_amount": 3000,
               "match": [{"category": "CAFE"}], "pools": []}]
    q, _ = run_product(_tiny_product(tiered, {}), cust, t, sh, bh, 1, 1, all_on_card=True)
    assert q["disc"].sum() == pytest.approx(600)
    ride = [{"id": "r", "kind": "fixed", "amount": 100, "units": "ride_days", "max_units": 10,
             "match": [{"category": "TRANSIT"}], "pools": []}]
    q, _ = run_product(_tiny_product(ride, {}), cust, t, sh, bh, 1, 1, all_on_card=True)
    assert q["disc"].sum() == pytest.approx(200)  # 이용일 2일


def test_donation_cashback_costs_issuer_but_not_customer():
    from cardsim.advisor import Population, evaluate
    pop = Population(n_customers=600, seed=5)
    g = pop.lookalikes("30s", 800_000, n=15)
    df, _ = evaluate(pop, g, ["tossbank_check_anywhere", "tossbank_check_donation"])
    r = df.set_index("id")
    a, d = r.loc["tossbank_check_anywhere"], r.loc["tossbank_check_donation"]
    assert a["월 혜택"] == pytest.approx(d["월 혜택"]) and a["카드사 공헌이익"] == pytest.approx(d["카드사 공헌이익"])
    assert d["연 순혜택"] == 0 and a["연 순혜택"] > 0
    assert 0.0027 < a["혜택률"] <= 0.003  # 해외 결제는 캐시백 제외
