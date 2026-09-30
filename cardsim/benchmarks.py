"""공개 통계 벤치마크 (설계서 4장) 로드와 비교 지표 계산.

원자료는 data/reference/benchmarks.csv 에 출처와 함께 기록한다.
여기서는 그 값으로 합성 데이터와 비교할 '1인당' 지표를 만든다.
"""

from pathlib import Path

import pandas as pd

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "data" / "reference" / "benchmarks.csv"
AGE_BANDS = {"20s": "pop_share_20s", "30s": "pop_share_30s", "40s": "pop_share_40s",
             "50s": "pop_share_50s", "60s+": ("pop_share_60s", "pop_share_70plus")}


BOK_TXN = {"20s": "bok_card_txn_20s", "30s": "bok_card_txn_30s", "40s": "bok_card_txn_40s",
           "50s": "bok_card_txn_50s", "60s+": "bok_card_txn_60plus"}


def load_benchmarks(path=None):
    df = pd.read_csv(path or DEFAULT_PATH)
    return dict(zip(df["metric_id"], df["값"])), df


def targets(b):
    """비교용 목표값. 개인카드 기준, 20세 이상 인구 1인당."""
    adult_share = sum(b[k] for k in ("pop_share_20s", "pop_share_30s", "pop_share_40s",
                                     "pop_share_50s", "pop_share_60s", "pop_share_70plus")) / 100
    adults = b["population_total"] * adult_share

    personal_amount_year = sum(b[f"personal_amount_q{q}"] for q in (1, 2, 3, 4)) * 1e12
    # 개인카드 승인건수는 1~3분기만 확보되어, 건수·건당 금액은 1~3분기로 계산한다
    amount_q123 = sum(b[f"personal_amount_q{q}"] for q in (1, 2, 3)) * 1e12
    count_q123 = sum(b[f"personal_count_q{q}"] for q in (1, 2, 3)) * 1e8

    age_share = {}
    for band, keys in AGE_BANDS.items():
        keys = keys if isinstance(keys, tuple) else (keys,)
        age_share[band] = sum(b[k] for k in keys) / 100 / adult_share

    return {
        "adults": adults,
        "monthly_spend_per_adult": personal_amount_year / adults / 12,
        "monthly_count_per_adult": count_q123 / adults / 9,
        "personal_ticket": amount_q123 / count_q123,
        # 체크카드는 사실상 개인카드뿐이라 개인카드 승인금액 대비로 본다 (4분기)
        "check_share": b["check_amount_q4"] / b["personal_amount_q4"],
        "age_share": age_share,
        # 한국은행 조사의 연령별 월 카드 이용건수를 40대 대비 비율로 (설문이라 절대값은 쓰지 않음)
        "age_intensity": {band: b[BOK_TXN[band]] / b[BOK_TXN["40s"]] for band in AGE_BANDS},
        "online_share_count": b["bok_card_txn_online_share"] / 100,
    }


def generated_metrics(customers, txns, n_months):
    orig = txns[~txns["is_cancelled"]]
    n = len(customers)
    no_transit = orig[orig["category"] != "TRANSIT"]
    age = customers["age_band"].value_counts(normalize=True).to_dict()
    # 연령대별 1인당 월 결제 건수 (대중교통 제외)
    per_cust = no_transit.groupby("customer_id").size().reindex(customers["customer_id"], fill_value=0)
    by_age = (per_cust.to_numpy() / n_months)
    age_cnt = pd.Series(by_age, index=customers["age_band"].to_numpy()).groupby(level=0).mean()
    return {
        "monthly_spend_per_adult": orig["amount"].sum() / n / n_months,
        "monthly_count_per_adult": len(orig) / n / n_months,
        "monthly_count_per_adult_ex_transit": len(no_transit) / n / n_months,
        "personal_ticket": orig["amount"].mean(),
        "personal_ticket_ex_transit": no_transit["amount"].mean(),
        "check_share": orig.loc[orig["payment_type"] == "check", "amount"].sum() / orig["amount"].sum(),
        "online_share_count_ex_transit": (no_transit["channel"] == "online").mean(),
        "age_share": {band: age.get(band, 0.0) for band in AGE_BANDS},
        "age_intensity": {band: age_cnt.get(band, 0.0) / age_cnt.get("40s", float("nan")) for band in AGE_BANDS},
    }


# 허용 범위 (설계서 5장). ("rel", x): 상대 오차 ±x, ("pp", x): 비중 차이 ±x (%p), ("abs", x): 절대 차이 ±x.
# 정의가 거의 같고 시뮬레이터 결과에 직접 영향을 주는 지표는 좁게, 설문 기반이거나 정의 차이가 있는 지표는 넓게 잡는다.
TOLERANCE = {
    "spend": ("rel", 0.10),
    "count": ("rel", 0.10),
    "ticket": ("rel", 0.10),
    "check": ("pp", 0.03),
    "online": ("pp", 0.05),
    "age_share": ("pp", 0.02),
    "age_intensity": ("abs", 0.10),
}


def within(actual, target, tol):
    kind, x = tol
    if kind == "rel":
        return abs(actual / target - 1) <= x
    return abs(actual - target) <= x


def comparison_rows(customers, txns, n_months, path=None):
    """(지표, 생성값, 목표값, 표시 형식, 허용 범위 또는 None) 목록. None은 참고용."""
    b, _ = load_benchmarks(path)
    t = targets(b)
    g = generated_metrics(customers, txns, n_months)
    T = TOLERANCE
    rows = [
        ("20세 이상 1인당 월 카드 사용액", g["monthly_spend_per_adult"], t["monthly_spend_per_adult"], "won", T["spend"]),
        ("20세 이상 1인당 월 결제 건수 (참고: 대중교통 포함)", g["monthly_count_per_adult"], t["monthly_count_per_adult"], "count", None),
        ("20세 이상 1인당 월 결제 건수 (대중교통 제외)", g["monthly_count_per_adult_ex_transit"], t["monthly_count_per_adult"], "count", T["count"]),
        ("개인카드 건당 평균 결제금액 (참고: 대중교통 포함)", g["personal_ticket"], t["personal_ticket"], "won", None),
        ("개인카드 건당 평균 결제금액 (대중교통 제외)", g["personal_ticket_ex_transit"], t["personal_ticket"], "won", T["ticket"]),
        ("체크카드 결제 비중 (금액)", g["check_share"], t["check_share"], "pct", T["check"]),
        ("온라인 결제 비중 (건수, 대중교통 제외)", g["online_share_count_ex_transit"], t["online_share_count"], "pct", T["online"]),
    ]
    rows += [(f"연령 구성: {band}", g["age_share"][band], t["age_share"][band], "pct", T["age_share"]) for band in AGE_BANDS]
    rows += [(f"이용 강도 (40대=1): {band}", g["age_intensity"][band], t["age_intensity"][band], "ratio", T["age_intensity"])
             for band in AGE_BANDS if band != "40s"]
    return rows
