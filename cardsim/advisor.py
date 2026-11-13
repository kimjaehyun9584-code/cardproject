"""고객 프로필 → 카드별 고객 순혜택·혜택 누수·카드사 공헌이익 (웹앱 계산부).

1. 합성 고객 중 입력한 연령대(·유형)에서 월 사용액이 가장 가까운 고객 N명을 고른다 (닮은 고객).
2. 각자의 결제 금액을 입력한 월 사용액에 맞게 비례 조정한다 (배율 0.5~2배로 제한).
3. 이 고객들이 카드 한 장으로 모든 결제를 한다고 보고 상품마다 1년을 계산한다.
4. 혜택 누수: 같은 결제에 조건을 하나씩 풀어 다시 계산한 차이.
   - 실적 미달: 전월실적 조건이 없었다면 (매달 최고 구간) 더 받았을 혜택
   - 한도·횟수: 한도·횟수·건당 상한도 없었다면 더 받았을 혜택
5. 선택 입력(Profile)으로 결과를 크게 바꾸는 고객 속성을 덮어쓴다:
   아파트 관리비 카드 납부, 통신사, 후불교통 사용, 커피·배달·온라인쇼핑 결제 횟수 배율.
"""

import copy
import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
from pandas.api.types import union_categoricals

from .generate import generate
from .products import load_all_products, load_pnl_assumptions, load_shares
from .simulate import load_behavior, pnl, prepare, run_product

START = "2025-01"
N_MONTHS = 12
AGE_BANDS = {"20s": "20대", "30s": "30대", "40s": "40대", "50s": "50대", "60s+": "60대 이상"}
SEGMENTS = {
    "S1_STUDENT": "대학생", "S2_YOUNG_WORKER": "사회초년생", "S3_SINGLE_30S": "30대 1인 가구",
    "S4_FAMILY_KIDS": "자녀 양육 가구", "S5_MIDDLE_AGE": "중장년", "S6_SELF_EMPLOYED": "자영업자",
    "S7_SENIOR": "시니어",
}
CATEGORY_KO = {
    "GROCERY": "마트·슈퍼", "RESTAURANT": "음식점", "CAFE": "카페", "CONVENIENCE": "편의점",
    "DELIVERY": "배달앱", "ONLINE_SHOP": "온라인쇼핑", "TRANSIT": "대중교통", "FUEL": "주유",
    "TELECOM": "통신", "UTILITY_INS": "공과금·보험", "MEDICAL": "병원·약국", "EDUCATION": "교육",
    "CULTURE_OTT": "영화·OTT", "TRAVEL": "여행", "DEPT_APPAREL": "백화점·의류", "ETC": "기타",
}
LIMIT_KEYS = ("per_day", "per_month", "per_year", "monthly_eligible_cap", "max_eligible_per_txn")
CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "cache"
ADJUSTABLE = {"CAFE": "카페", "DELIVERY": "배달앱", "ONLINE_SHOP": "온라인쇼핑"}
COVERAGE = {"full": "✅ 전부 반영", "partial": "⚠️ 일부 빠짐", "core_missing": "❌ 주력 혜택 빠짐"}


@dataclass(frozen=True)
class Profile:
    """선택 입력. None은 '모름' = 합성 데이터 평균 비중을 그대로 쓴다."""
    apt_fee: bool | None = None       # 아파트 관리비를 카드로 낸다
    telecom_mno3: bool | None = None  # 이동통신 3사(True) / 알뜰폰(False)
    postpaid_transit: bool | None = None  # 후불교통 기능 사용
    category_factor: tuple = field(default_factory=tuple)  # ((업종, 결제 횟수 배율), ...)


def _profile_shares(shares, profile):
    """고객 속성에 맞게 가맹점 비중을 덮어쓴다 (값 1 = 모두 해당, 0 = 해당 없음)."""
    sh = copy.deepcopy(shares)
    s = sh["shares"]
    if profile.apt_fee is False:
        s["utility_mgmt_fee"]["value"] = 0.0
    if profile.telecom_mno3 is not None:
        v = 1.0 if profile.telecom_mno3 else 0.0
        s["telecom_mno3"]["value"] = s["telecom_mno3_liivm"]["value"] = v
    if profile.postpaid_transit is not None:
        s["transit_postpaid_option"]["value"] = 1.0 if profile.postpaid_transit else 0.0
    return sh


class Population:
    """합성 고객·거래 (앱 시작 시 1회 생성)."""

    def __init__(self, n_customers=10000, seed=42, cache=True):
        self.customers, self.txns = _load_or_generate(n_customers, seed, cache)
        self.seed = seed
        spend = self.txns.groupby("customer_id", observed=True)["amount"].sum() / N_MONTHS
        self.customers["monthly_spend"] = self.customers["customer_id"].map(spend).fillna(0.0)
        self.products = load_all_products()
        self.shares = load_shares()
        self.rates = load_pnl_assumptions()["rates"]
        self.behavior = load_behavior()

    def lookalikes(self, age_band, monthly_spend, segment=None, n=100):
        """연령대(·유형)에서 월 사용액이 가장 가까운 고객 n명과 금액 배율."""
        c = self.customers[self.customers["age_band"] == age_band]
        if segment:
            c = c[c["segment"] == segment]
        c = c[c["monthly_spend"] > 0]
        if c.empty:
            raise ValueError("조건에 맞는 합성 고객이 없습니다")
        c = c.iloc[np.argsort(np.abs(np.log(c["monthly_spend"].to_numpy() / monthly_spend)))[:n]].copy()
        c["scale"] = np.clip(monthly_spend / c["monthly_spend"], 0.5, 2.0)
        return c


CAT_COLS = ["customer_id", "category", "channel", "payment_type"]


def _slim(txns):
    """취소 거래를 빼고, 앱에 필요한 열만 작은 자료형으로 남긴다 (메모리 약 1/3)."""
    cancelled = set(txns.loc[txns["is_cancelled"], "original_txn_id"])
    t = txns[~txns["is_cancelled"] & ~txns["txn_id"].isin(cancelled)]
    t = t.drop(columns=["txn_id", "original_txn_id", "is_cancelled"]).reset_index(drop=True)
    for c in CAT_COLS:
        t[c] = t[c].astype("category")
    t["amount"] = t["amount"].astype("int32")
    t["installment_months"] = t["installment_months"].astype("int8")
    return t


BATCH = 2000  # 한 번에 만드는 고객 수: 무료 배포 서버 메모리(1GB대)에서도 첫 실행이 되도록


def _generate_in_batches(n_customers, seed):
    """고객을 BATCH명씩 나눠 생성해 최대 메모리를 줄인다. 배치마다 시드를 달리하고 고객 ID 앞에 배치 번호를 붙인다."""
    custs, parts = [], []
    for i, start in enumerate(range(0, n_customers, BATCH)):
        size = min(BATCH, n_customers - start)
        _, c, t = generate(size, N_MONTHS, START, seed * 1000 + i)
        pre = f"B{i}_"
        c["customer_id"] = pre + c["customer_id"]
        t["customer_id"] = pre + t["customer_id"]
        custs.append(c)
        parts.append(_slim(t))
        del t
    txns = pd.DataFrame({
        col: (union_categoricals([p[col] for p in parts]) if col in CAT_COLS
              else np.concatenate([p[col].to_numpy() for p in parts]))
        for col in parts[0].columns})
    return pd.concat(custs, ignore_index=True), txns


def _load_or_generate(n_customers, seed, cache):
    """합성 데이터를 data/cache/에 parquet로 저장해 두고 다음부터는 읽기만 한다 (16초 → 1초 미만)."""
    stem = CACHE_DIR / f"pop_v3_n{n_customers}_s{seed}"
    cpath, tpath = stem.with_suffix(".customers.parquet"), stem.with_suffix(".txns.parquet")
    if cache and cpath.exists() and tpath.exists():
        return pd.read_parquet(cpath), pd.read_parquet(tpath)
    customers, txns = _generate_in_batches(n_customers, seed)
    if cache:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        customers.to_parquet(cpath)
        txns.to_parquet(tpath)
    return customers, txns


def _scale_counts(t, factors, seed):
    """업종별 결제 횟수를 배율만큼 줄이거나 늘린다. 늘릴 때는 같은 달 안의 다른 날로 복제한다."""
    if not factors:
        return t
    rng = np.random.default_rng([seed, 301])
    parts = [t[~t["category"].isin([c for c, _ in factors])]]
    for cat, f in factors:
        x = t[t["category"] == cat]
        whole, frac = int(f), f - int(f)
        keep = [x] * whole
        if frac > 0:
            keep.append(x[rng.random(len(x)) < frac])
        for i, k in enumerate(keep[1:], start=1):
            k = k.copy()
            shift = pd.to_timedelta(rng.integers(1, 28, len(k)), unit="D")
            moved = k["txn_datetime"] + shift
            same_month = moved.dt.month == k["txn_datetime"].dt.month
            k["txn_datetime"] = moved.where(same_month, moved - pd.Timedelta(days=28))
            k["txn_id"] = k["txn_id"].astype(str) + f"_c{i}"
            keep[i] = k
        parts.extend(keep)
    return pd.concat(parts, ignore_index=True).sort_values(["customer_id", "txn_datetime"]).reset_index(drop=True)


def _profile_txns(pop, group, kind, profile=Profile()):
    """닮은 고객 거래를 금액 조정하고, 모든 결제를 이 카드 종류로 바꾼다."""
    t = pop.txns[pop.txns["customer_id"].isin(group["customer_id"])].copy()
    for c in CAT_COLS:  # 일부 고객만 남긴 뒤에는 일반 문자열로 (범주형 groupby가 전체 범주를 펼치지 않게)
        t[c] = t[c].astype(str)
    t["amount"] = t["amount"].astype("int64")
    t["txn_id"] = "T" + t.index.astype(str)
    t["is_cancelled"] = False
    t["original_txn_id"] = None
    t = _scale_counts(t, profile.category_factor, pop.seed)
    # 업종 배율을 바꿔도 입력한 월 사용액은 그대로: 고객별 합계를 목표에 맞게 다시 맞춘다
    target = (group.set_index("customer_id")["monthly_spend"] * group.set_index("customer_id")["scale"]) * N_MONTHS
    now = t.groupby("customer_id")["amount"].sum()
    t["amount"] = (t["amount"] * t["customer_id"].map(target / now)).round()
    t["payment_type"] = kind
    if kind == "check":
        t["installment_months"] = 0  # 체크카드는 할부 없음
    cust = group.drop(columns=["monthly_spend", "scale"]).copy()
    cust["has_credit_card"] = True
    return prepare(cust, t, START, pop.seed)


def _relaxed(prod, tiers=False, limits=False):
    p = copy.deepcopy(prod)
    if tiers and p["performance"]["tiers"]:
        p["performance"]["grace"] = {"months": N_MONTHS, "tier": max(p["performance"]["tiers"])}
        p["performance"]["exclude_discounted"] = False
    if limits:
        p["pools"] = {}
        for b in p["benefits"]:
            b["pools"] = []
            for k in LIMIT_KEYS:
                b.pop(k, None)
    return p


def _has_limits(prod):
    return bool(prod["pools"]) or any(k in b for b in prod["benefits"] for k in LIMIT_KEYS)


def promo_status(prod, today=None):
    """약관 확인일·프로모션 종료일과 만료 여부."""
    today = today or dt.date.today()
    end = prod.get("promo_end")
    expired = bool(end) and dt.date.fromisoformat(end) < today
    return {"terms_checked": prod.get("terms_checked"), "promo_end": end, "expired": expired}


def evaluate(pop, group, product_ids=None, profile=Profile()):
    """상품별 고객·카드사 지표 (회원 1인 월평균)."""
    rows, details = [], {}
    prepared = {}
    shares = _profile_shares(pop.shares, profile)
    for pid in product_ids or list(pop.products):
        prod = pop.products[pid]
        kind = prod["card_kind"]
        if kind not in prepared:
            prepared[kind] = _profile_txns(pop, group, kind, profile)
        cust, t = prepared[kind]
        args = (cust, t, shares, pop.behavior, pop.seed, N_MONTHS)
        q, used = run_product(prod, *args, all_on_card=True)
        # 조건이 없는 상품은 다시 계산할 필요가 없음 (속도)
        q_tier = run_product(_relaxed(prod, tiers=True), *args, all_on_card=True)[0] if prod["performance"]["tiers"] else q
        q_all = run_product(_relaxed(prod, tiers=True, limits=True), *args, all_on_card=True)[0] if _has_limits(prod) else q_tier
        ben = lambda x: float((x["disc"] + x["pts"]).mean())
        actual, no_tier, no_limit = ben(q), ben(q_tier), ben(q_all)
        lines = pnl(q, prod, pop.rates).mean()
        spend = float(q["amount"].mean())
        months = q.index.get_level_values(1)
        tier_miss = float((q.loc[months > 0, "tier"] == 0).mean()) if prod["performance"]["tiers"] else 0.0
        rows.append({
            "id": pid,
            "카드": prod["name"],
            "종류": "신용카드" if kind == "credit" else "체크카드",
            "연회비": prod["annual_fee"],
            "월 혜택": actual,
            # customer_value: 고객이 실제로 쓰는 몫의 비율 (기부 캐시백은 0: 카드사 비용이지만 고객 현금이 아님)
            "연 순혜택": actual * prod.get("customer_value", 1.0) * 12 - prod["annual_fee"],
            "혜택률": actual / spend if spend else 0.0,
            "실적 미달 누수": max(no_tier - actual, 0.0),
            "한도·횟수 누수": max(no_limit - no_tier, 0.0),
            "실적 미달 달 비율": tier_miss,
            "카드사 공헌이익": float(lines["공헌이익"]),
            "누수 없을 때 공헌이익": float(pnl(q_all, prod, pop.rates)["공헌이익"].mean()),
            "월 사용액": spend,
            "계산 반영도": COVERAGE[prod.get("coverage", "partial")],
            "coverage": prod.get("coverage", "partial"),
            "약관 확인일": promo_status(prod)["terms_checked"],
            "프로모션 종료일": promo_status(prod)["promo_end"],
            "프로모션 만료": promo_status(prod)["expired"],
        })
        tot = sum(used.values()) or 1.0
        details[pid] = {
            "lines": lines.to_dict(),
            "by_rule": {next((b["name"] for b in prod["benefits"] if b["id"] == k), k): v / len(q) for k, v in used.items()},
            "unmodeled": prod.get("unmodeled_benefits", []),
            "source": prod["source"],
            "tiers": prod["performance"]["tiers"],
            "share": {k: v / tot for k, v in used.items()},
            "coverage_note": prod.get("coverage_note", ""),
        }
    df = pd.DataFrame(rows).sort_values("연 순혜택", ascending=False).reset_index(drop=True)
    return df, details


def category_mix(pop, group, profile=Profile()):
    t = _profile_txns(pop, group, "credit", profile)[1]
    mix = t["amount"].groupby(t["category"]).sum() / len(group) / N_MONTHS
    return mix.rename(index=CATEGORY_KO).sort_values(ascending=False)
