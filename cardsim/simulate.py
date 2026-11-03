"""카드 상품 손익 시뮬레이터 (docs/simulator_design.md).

사용 예:
    python -m cardsim.simulate --customers 10000 --seed 42

합성 고객·거래 데이터에 상품(config/products/*.json)을 적용해 고객·월별 공헌이익을 계산하고,
가정값을 범위 양 끝으로 바꾼 민감도 분석과 함께 HTML 리포트를 만든다.
"""

import argparse
import copy
import html
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

from .generate import generate
from .products import load_all_products, load_pnl_assumptions, load_shares

ROOT = Path(__file__).resolve().parent.parent
BEHAVIOR_PATH = ROOT / "config" / "behavior.json"
AUTOPAY = ("TELECOM", "UTILITY_INS")


def load_behavior(path=None):
    with open(path or BEHAVIOR_PATH, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------- 데이터 준비

def prepare(customers, txns, start, seed):
    """취소 거래와 그 원거래를 빼고, 시뮬레이션에 필요한 열과 난수를 붙인다."""
    cancelled = set(txns.loc[txns["is_cancelled"], "original_txn_id"])
    t = txns[~txns["is_cancelled"] & ~txns["txn_id"].isin(cancelled)].copy()
    t = _monthly_transit(t)
    t = t.sort_values(["customer_id", "txn_datetime"]).reset_index(drop=True)
    y0, m0 = (int(x) for x in start.split("-"))
    dtm = t["txn_datetime"]
    t["month"] = ((dtm.dt.year - y0) * 12 + dtm.dt.month - m0).astype(np.int16)
    t["day"] = (dtm.dt.normalize() - pd.Timestamp("1970-01-01")).dt.days.astype(np.int32)
    t["year"] = dtm.dt.year.astype(np.int16)
    t["hour"] = dtm.dt.hour.astype(np.int8)
    t["weekday"] = dtm.dt.day_of_week.astype(np.int8)
    rng = np.random.default_rng([seed, 101])
    t["u_merchant"] = rng.random(len(t))        # 업종 안 가맹점 구간 판정용 (거래별 1개)
    t["u_assign"] = rng.random(len(t))          # 이 카드로 결제할지
    cust = customers.set_index("customer_id")
    rng_c = np.random.default_rng([seed, 102])
    cust["u_customer"] = rng_c.random(len(cust))  # 고객 속성 비중 판정용 (통신사, 후불교통)
    cust["u_chase"] = rng_c.random(len(cust))
    t["cust_idx"] = cust.index.get_indexer(t["customer_id"]).astype(np.int32)
    return cust, t


def _monthly_transit(t):
    """대중교통은 후불교통으로 월 단위 합산 청구된다. 고객·결제수단·월별 1건(말일)으로 묶는다.

    한 고객의 후불교통은 카드 한 장에 연결되므로, 이 카드로 올지도 월 단위로 정해진다.
    """
    is_tr = (t["category"] == "TRANSIT").to_numpy()
    if not is_tr.any():
        return t
    tr = t[is_tr]
    per = tr["txn_datetime"].dt.to_period("M")
    g = tr.groupby([tr["customer_id"], tr["payment_type"], per], sort=False)
    agg = g.agg(txn_id=("txn_id", "first"), amount=("amount", "sum"), rides=("amount", "size"),
                channel=("channel", "first"), is_overseas=("is_overseas", "first"),
                excluded_from_performance=("excluded_from_performance", "first")).reset_index()
    agg = agg.rename(columns={"txn_datetime": "period"})
    agg["txn_datetime"] = agg["period"].dt.to_timestamp(how="end").dt.normalize() + pd.Timedelta(hours=23)
    agg["txn_datetime"] = agg["txn_datetime"].astype(t["txn_datetime"].dtype)
    agg["category"] = "TRANSIT"
    agg["installment_months"] = 0
    agg["is_cancelled"] = False
    agg["original_txn_id"] = None
    agg = agg.drop(columns="period")
    rest = t[~is_tr].copy()
    rest["rides"] = 1
    return pd.concat([rest, agg[rest.columns]], ignore_index=True)


def _share_interval(shares, key):
    spec = shares["shares"][key]
    lo = _share_interval(shares, spec["after"])[1] if "after" in spec else 0.0  # 구간을 이어 붙임
    return lo, lo + spec["value"]


def _match_mask(t, cust_u, m, shares):
    cat = m["category"]
    mask = np.ones(len(t), bool) if cat == "*" else (t["category"].to_numpy() == cat)
    if "merchant_share" in m:
        lo, hi = _share_interval(shares, m["merchant_share"])
        u = t["u_merchant"].to_numpy()
        mask &= (u >= lo) & (u < hi)
    if "customer_share" in m:
        lo, hi = _share_interval(shares, m["customer_share"])
        u = cust_u[t["cust_idx"].to_numpy()]
        mask &= (u >= lo) & (u < hi)
    if m.get("overseas"):
        mask &= t["is_overseas"].to_numpy()
    if "channel" in m:
        mask &= t["channel"].to_numpy() == m["channel"]
    return mask


def _benefit_mask(t, cust_u, b, shares):
    mask = np.zeros(len(t), bool)
    for m in b["match"]:
        mask |= _match_mask(t, cust_u, m, shares)
    if "hours" in b:
        h0, h1 = b["hours"]
        hr = t["hour"].to_numpy()
        mask &= ((hr >= h0) & (hr < h1)) if h0 < h1 else ((hr >= h0) | (hr < h1))
    if "weekdays" in b:
        mask &= np.isin(t["weekday"].to_numpy(), b["weekdays"])
    for m in b.get("exclude", []):
        mask &= ~_match_mask(t, cust_u, m, shares)
    if b.get("lump_only"):
        mask &= t["installment_months"].to_numpy() == 0
    if b.get("min_txn_amount"):
        mask &= t["amount"].to_numpy() >= b["min_txn_amount"]
    return mask


def _tier_value(perf, tiers):
    """전월실적 금액 → 충족한 구간 하한 (없으면 0)."""
    if not tiers:
        return np.zeros_like(perf)
    edges = np.asarray(tiers, dtype=float)
    idx = np.searchsorted(edges, perf, side="right")
    return np.where(idx > 0, edges[np.maximum(idx - 1, 0)], 0.0)


def _pool_limit(by_tier, tier):
    keys = sorted(int(k) for k in by_tier if k != "period")
    lim = 0
    for k in keys:
        if tier >= k:
            lim = by_tier[str(k)]
    return lim


# ---------------------------------------------------------------- 상품 1개 계산

def run_product(prod, cust, t_all, shares, behavior, seed, n_months, all_on_card=False):
    """상품 1개를 가입 가능 고객 전원에게 적용한다. 월 단위로 구간 → 실적 맞추기 → 혜택 → 실적 순서로 계산."""
    kind = prod["card_kind"]
    members = cust.index[cust["has_credit_card"]] if kind == "credit" else cust.index
    mem_mask = cust.index.isin(members)
    t = t_all[(t_all["payment_type"] == kind).to_numpy() & mem_mask[t_all["cust_idx"].to_numpy()]].reset_index(drop=True)
    cust_u = cust["u_customer"].to_numpy()
    n_c = len(cust)

    # 고객별 이 카드 비율 (베타분포)
    k = behavior["share_concentration"]["value"]
    if kind == "credit":
        spec = behavior["main_card_share_credit"]
        mean = cust["segment"].map(spec["value"]).to_numpy() * spec.get("_scale", 1.0)
    else:
        mean = np.full(n_c, behavior["main_card_share_check"]["value"])
    mean = np.clip(mean, 0.02, 0.95)
    share = np.random.default_rng([seed, 201]).beta(mean * k, (1 - mean) * k)

    # 결제 배정: 자동납부 업종은 고객·업종 단위, 나머지는 거래 단위 (대중교통은 이미 고객·월 1건)
    s_txn = share[t["cust_idx"].to_numpy()]
    on = t["u_assign"].to_numpy() < s_txn
    autopay = t["category"].isin(AUTOPAY).to_numpy().copy()
    if all_on_card:  # 테스트용: 모든 결제를 이 카드로
        on[:] = True
    elif autopay.any():
        u_ap = np.random.default_rng([seed, 202]).random((n_c, len(AUTOPAY)))
        cat_i = t.loc[autopay, "category"].map({c: i for i, c in enumerate(AUTOPAY)}).to_numpy()
        on[autopay] = u_ap[t.loc[autopay, "cust_idx"].to_numpy(), cat_i] < s_txn[autopay]

    perf_spec = prod["performance"]
    perf_ex = t["category"].isin(perf_spec.get("exclude_categories", [])).to_numpy().copy()
    for m in perf_spec.get("exclude_shares", []):
        perf_ex |= _match_mask(t, cust_u, m, shares)
    excl_disc = perf_spec.get("exclude_discounted", False)

    tiers = perf_spec["tiers"]
    month = t["month"].to_numpy()
    ci = t["cust_idx"].to_numpy()
    amt = t["amount"].to_numpy().astype(float)
    window, prob = behavior["chase_window"]["value"], behavior["chase_prob"]["value"]
    u_chase = cust["u_chase"].to_numpy()
    can_move = ~perf_ex & ~autopay
    grace = perf_spec.get("grace")
    fuel_price = shares["fuel_price_per_liter"]["value"]

    # 혜택별 대상 거래 (배정과 무관하게 한 번만 계산)
    bens = prod["benefits"]
    bmask = []
    for b in bens:
        mk = _benefit_mask(t, cust_u, b, shares)
        bmask.append(mk)
    simple = [i for i, b in enumerate(bens) if not (b.get("pools") or b.get("per_day") or b.get("per_month")
                                                     or b.get("per_year") or b.get("monthly_eligible_cap"))]
    complex_ = [i for i in range(len(bens)) if i not in simple]

    perf = np.zeros((n_c, n_months))
    tier_cm = np.zeros((n_c, n_months))
    chased = np.zeros((n_c, n_months), bool)
    disc = np.zeros(len(t))
    pts = np.zeros(len(t))
    used_by_benefit = {}
    day, year = t["day"].to_numpy(), t["year"].to_numpy()
    pools = prod["pools"]
    cnt_y_all = {}  # 연간 횟수는 월을 넘어 이어짐

    for mo in range(n_months):
        in_m = month == mo
        # 이번 달 구간 = 전월실적, 신규 회원 면제 기간
        if mo > 0:
            tier_cm[:, mo] = _tier_value(perf[:, mo - 1], tiers)
        if grace and mo < grace["months"]:
            tier_cm[:, mo] = np.maximum(tier_cm[:, mo], grace["tier"])

        # 실적 맞추기: 이번 달 실적이 다음 구간에 조금 못 미치면 다른 카드 결제를 옮김
        sel = in_m & on & ~perf_ex
        p = np.bincount(ci[sel], weights=amt[sel], minlength=n_c)
        if tiers and prob > 0 and not all_on_card:
            edges = np.asarray(tiers, float)
            nxt_idx = np.searchsorted(edges, p, side="right")
            has_next = nxt_idx < len(edges)
            nxt = np.where(has_next, edges[np.minimum(nxt_idx, len(edges) - 1)], np.inf)
            gap = nxt - p
            cand = has_next & (gap > 0) & (gap <= window * nxt) & (u_chase < prob) & mem_mask
            if cand.any():
                pool = np.flatnonzero(in_m & ~on & can_move & cand[ci])
                if len(pool):
                    order = pool[::-1]  # 월말부터 거꾸로
                    df = pd.DataFrame({"i": order, "c": ci[order], "a": amt[order]})
                    df["cum"] = df.groupby("c")["a"].cumsum()
                    df["need"] = gap[df["c"].to_numpy()]
                    moved = df.loc[df["cum"] - df["a"] < df["need"], ["i", "c"]]
                    reached = df.groupby("c")["cum"].max() >= df.groupby("c")["need"].first()
                    ok_c = reached[reached].index
                    moved = moved[moved["c"].isin(ok_c)]
                    on[moved["i"].to_numpy()] = True
                    chased[ok_c.to_numpy(), mo] = True

        # 혜택 (이번 달, 이 카드 결제만)
        cur = in_m & on
        tier_txn = tier_cm[ci, mo]
        for bi in simple:
            b = bens[bi]
            mk = bmask[bi] & cur
            if b.get("min_tier") is not None:
                mk &= tier_txn >= b["min_tier"]
            idx = np.flatnonzero(mk)
            if not len(idx):
                continue
            val = _benefit_value(b, np.minimum(amt[idx], b.get("max_eligible_per_txn", np.inf)), fuel_price)
            (pts if b["kind"] == "points" else disc)[idx] += val
            used_by_benefit[b["id"]] = used_by_benefit.get(b["id"], 0.0) + float(val.sum())
        if complex_:
            rows, bids = [], []
            for bi in complex_:
                b = bens[bi]
                mk = bmask[bi] & cur
                if b.get("min_tier") is not None:
                    mk &= tier_txn >= b["min_tier"]
                idx = np.flatnonzero(mk)
                rows.append(idx)
                bids.append(np.full(len(idx), bi))
            rows, bids = np.concatenate(rows), np.concatenate(bids)
            order = np.lexsort((bids, rows))  # t는 고객·시간순 정렬
            cur_c, cur_day = None, None
            pool_used, pool_day, cnt_m, cnt_d, cap_used = {}, {}, {}, {}, {}
            for r, bi in zip(rows[order].tolist(), bids[order].tolist()):
                c = ci[r]
                b = bens[bi]
                if c != cur_c:
                    cur_c, pool_used, cnt_m, cap_used = c, {}, {}, {}
                if (c, day[r]) != cur_day:
                    cur_day, cnt_d, pool_day = (c, day[r]), {}, {}
                ykey = (c, year[r], bi)
                if b.get("per_day") and cnt_d.get(bi, 0) >= b["per_day"]:
                    continue
                if b.get("per_month") and cnt_m.get(bi, 0) >= b["per_month"]:
                    continue
                if b.get("per_year") and cnt_y_all.get(ykey, 0) >= b["per_year"]:
                    continue
                base = min(amt[r], b.get("max_eligible_per_txn", float("inf")))
                if b.get("monthly_eligible_cap"):
                    base = min(base, b["monthly_eligible_cap"] - cap_used.get(bi, 0.0))
                    if base <= 0:
                        continue
                val = float(_benefit_value(b, np.array([base]), fuel_price)[0])
                tier = tier_cm[c, mo]
                for pn in b.get("pools", []):
                    used = pool_day if pools[pn].get("period") == "day" else pool_used
                    val = min(val, _pool_limit(pools[pn], tier) - used.get(pn, 0.0))
                if val <= 0:
                    continue
                for pn in b.get("pools", []):
                    used = pool_day if pools[pn].get("period") == "day" else pool_used
                    used[pn] = used.get(pn, 0.0) + val
                cnt_d[bi] = cnt_d.get(bi, 0) + 1
                cnt_m[bi] = cnt_m.get(bi, 0) + 1
                cnt_y_all[ykey] = cnt_y_all.get(ykey, 0) + 1
                if b.get("monthly_eligible_cap"):
                    cap_used[bi] = cap_used.get(bi, 0.0) + base
                if b["kind"] == "points":
                    pts[r] += val
                else:
                    disc[r] += val
                used_by_benefit[b["id"]] = used_by_benefit.get(b["id"], 0.0) + val

        # 이번 달 실적 (다음 달 구간 결정). 할인받은 결제를 빼는 상품도 있음
        sel = in_m & on & ~perf_ex
        if excl_disc:
            sel &= disc == 0
        perf[:, mo] = np.bincount(ci[sel], weights=amt[sel], minlength=n_c)

    # ---- 고객·월별 기초 수량 (요율을 곱하기 전)
    t_on = np.flatnonzero(on)
    inst = t["installment_months"].to_numpy()[t_on]
    a_on = amt[t_on]
    q = pd.DataFrame({
        "c": ci[t_on], "m": month[t_on], "amount": a_on, "count": 1.0,
        "lump": np.where(inst == 0, a_on, 0.0),
        "inst_bal": np.where(inst > 0, a_on * (inst + 1) / 2 / 12, 0.0),  # 할부 원금 × 평균 잔액 기간(년)
        "disc": disc[t_on], "pts": pts[t_on],
    }).groupby(["c", "m"]).sum()
    full = pd.MultiIndex.from_product([np.flatnonzero(mem_mask), range(n_months)], names=["c", "m"])
    q = q.reindex(full, fill_value=0.0)
    q["tier"] = tier_cm[q.index.get_level_values(0), q.index.get_level_values(1)]
    q["chased"] = chased[q.index.get_level_values(0), q.index.get_level_values(1)]
    q["segment"] = cust["segment"].to_numpy()[q.index.get_level_values(0)]
    return q, used_by_benefit


def _benefit_value(b, base, fuel_price):
    if b["kind"] in ("rate", "points"):
        return base * b["rate"]
    if b["kind"] == "fixed":
        return np.full_like(base, float(b["amount"]))
    if b["kind"] == "per_liter":
        return base / fuel_price * b["per_liter"]
    raise ValueError(b["kind"])


# ---------------------------------------------------------------- 손익

LINES = ["가맹점수수료", "연회비", "할부수수료", "혜택", "조달", "대손", "승인처리", "모집", "공헌이익"]


def pnl(q, prod, rates):
    r = {k: v["value"] for k, v in rates.items()}
    credit = prod["card_kind"] == "credit"
    fee = r["merchant_fee_rate_credit"] if credit else r["merchant_fee_rate_check"]
    acq = r["acquisition_cost_credit"] if credit else r["acquisition_cost_check"]
    out = pd.DataFrame(index=q.index)
    out["가맹점수수료"] = q["amount"] * fee
    out["연회비"] = prod["annual_fee"] / 12
    out["할부수수료"] = q["inst_bal"] * (1 - r["installment_free_share"]) * r["installment_rate_annual"] if credit else 0.0
    out["혜택"] = q["disc"] + q["pts"] * r["point_cost_per_point"]
    out["조달"] = (q["lump"] * r["float_days"] / 365 + q["inst_bal"]) * r["funding_rate_annual"] if credit else 0.0
    out["대손"] = q["amount"] * r["credit_loss_rate"] if credit else 0.0
    out["승인처리"] = q["count"] * r["processing_cost_per_txn"]
    out["모집"] = acq / r["acquisition_amortization_months"]
    out["공헌이익"] = (out["가맹점수수료"] + out["연회비"] + out["할부수수료"]
                   - out["혜택"] - out["조달"] - out["대손"] - out["승인처리"] - out["모집"])
    return out


def summarize(q, prod, rates):
    p = pnl(q, prod, rates)
    per_cm = p.mean()  # 회원·월 평균
    by_cust = p["공헌이익"].groupby(level=0).sum()
    ben = (q["disc"] + q["pts"]).groupby(level=0).sum()
    top = ben.sort_values(ascending=False)
    top10 = top.iloc[: max(1, len(top) // 10)].sum() / top.sum() if top.sum() > 0 else 0.0
    seg = p.groupby(q["segment"])["공헌이익"].mean()
    tiers = q.loc[q.index.get_level_values(1) > 0, "tier"].value_counts(normalize=True).sort_index()
    return {
        "members": int(q.index.get_level_values(0).nunique()),
        "spend": float(q["amount"].mean()),
        "lines": per_cm.to_dict(),
        "benefit_rate": float((q["disc"].sum() + q["pts"].sum()) / q["amount"].sum()) if q["amount"].sum() else 0.0,
        "neg_share": float((by_cust < 0).mean()),
        "top10_benefit_share": float(top10),
        "by_segment": seg.to_dict(),
        "tier_dist": tiers.to_dict(),
        "chase_rate": float(q["chased"].mean()),
    }


# ---------------------------------------------------------------- 실행·민감도

def simulate(cust, t, products, shares, rates, behavior, seed, n_months):
    res, qs = {}, {}
    for pid, prod in products.items():
        q, used = run_product(prod, cust, t, shares, behavior, seed, n_months)
        qs[pid] = q
        s = summarize(q, prod, rates)
        s["benefit_by_rule"] = used
        res[pid] = s
    return res, qs


def _contrib(qs, products, rates):
    return {pid: float(pnl(q, products[pid], rates)["공헌이익"].mean()) for pid, q in qs.items()}


def sensitivity(qs, cust, t, products, shares, rates, behavior, seed, n_months, sample_customers):
    """가정값 하나씩 범위 양 끝으로 바꿔 회원·월 공헌이익 변화를 본다."""
    rows = []
    # 1) 요율: 기초 수량은 그대로, 요율만 바꿔 다시 계산
    base = _contrib(qs, products, rates)
    for key, spec in rates.items():
        lo_hi = []
        for v in spec["range"]:
            r2 = copy.deepcopy(rates)
            r2[key]["value"] = v
            lo_hi.append(_contrib(qs, products, r2))
        rows.append(("요율", key, spec["basis"], spec["range"], lo_hi))

    # 2) 비중·행동: 표본 고객으로 다시 돌림
    keep = cust.index[:sample_customers]
    cs = cust.loc[keep]
    ts = t[t["customer_id"].isin(keep)].copy()
    ts["cust_idx"] = cs.index.get_indexer(ts["customer_id"]).astype(np.int32)
    _, qb = simulate(cs, ts, products, shares, rates, behavior, seed, n_months)
    base_s = _contrib(qb, products, rates)

    def rerun(sh, bh):
        _, qx = simulate(cs, ts, products, sh, rates, bh, seed, n_months)
        c = _contrib(qx, products, rates)
        return {pid: base[pid] + (c[pid] - base_s[pid]) for pid in c}

    used_keys = {m.get(f) for p in products.values() for b in p["benefits"] for m in b["match"]
                 for f in ("merchant_share", "customer_share")} - {None}
    for key in sorted(used_keys):
        spec = shares["shares"][key]
        lo_hi = []
        for v in spec["range"]:
            sh = copy.deepcopy(shares)
            sh["shares"][key]["value"] = v
            lo_hi.append(rerun(sh, behavior))
        rows.append(("가맹점 비중", key, spec["basis"], spec["range"], lo_hi))
    fp = shares["fuel_price_per_liter"]
    lo_hi = []
    for v in fp["range"]:
        sh = copy.deepcopy(shares)
        sh["fuel_price_per_liter"]["value"] = v
        lo_hi.append(rerun(sh, behavior))
    rows.append(("가맹점 비중", "fuel_price_per_liter", fp["basis"], fp["range"], lo_hi))

    beh_cases = [
        ("main_card_share_credit", "×", behavior["main_card_share_credit"]["range_scale"],
         lambda b, v: b["main_card_share_credit"].__setitem__("_scale", v)),
        ("main_card_share_check", "", behavior["main_card_share_check"]["range"],
         lambda b, v: b["main_card_share_check"].__setitem__("value", v)),
        ("chase_window", "", behavior["chase_window"]["range"], lambda b, v: b["chase_window"].__setitem__("value", v)),
        ("chase_prob", "", behavior["chase_prob"]["range"], lambda b, v: b["chase_prob"].__setitem__("value", v)),
    ]
    for key, _, rng_, setter in beh_cases:
        lo_hi = []
        for v in rng_:
            bh = copy.deepcopy(behavior)
            setter(bh, v)
            lo_hi.append(rerun(shares, bh))
        rows.append(("행동", key, behavior[key]["basis"], rng_, lo_hi))
    return base, rows


def ranking_flips(base, rows, subset=None):
    ids = [p for p in base if subset is None or p in subset]
    order = sorted(ids, key=base.get, reverse=True)
    flips = []
    for grp, key, basis, rng_, lo_hi in rows:
        for v, res in zip(rng_, lo_hi):
            o = sorted(ids, key=res.get, reverse=True)
            if o != order:
                flips.append((key, v, o))
    return order, flips


# ---------------------------------------------------------------- 리포트

def display_name(prod):
    """상품명 옆에 신용·체크 구분을 붙인다."""
    kind = "신용카드" if prod["card_kind"] == "credit" else "체크카드"
    return f"{prod['name']} [{kind}]"


def write_report(path, products, res, base, rows, meta):
    esc = html.escape
    names = {pid: display_name(p) for pid, p in products.items()}
    pids = sorted(res, key=lambda k: base[k], reverse=True)
    order, flips = ranking_flips(base, rows)

    def won(v):
        return f"{v:,.0f}"

    head = "".join(f"<th>{esc(names[p])}</th>" for p in pids)
    summ = ("<tr><th>가입 가능 고객 (명)</th>" + "".join(f"<td>{res[p]['members']:,}</td>" for p in pids) + "</tr>"
            + "<tr><th>회원 1인 월 이 카드 사용액</th>" + "".join(f"<td>{won(res[p]['spend'])}</td>" for p in pids) + "</tr>")
    for line in LINES:
        cls = " class='total'" if line == "공헌이익" else ""
        sign = "" if line in ("가맹점수수료", "연회비", "할부수수료", "공헌이익") else "−"
        summ += f"<tr{cls}><th>{sign}{line}</th>" + "".join(f"<td>{won(res[p]['lines'][line])}</td>" for p in pids) + "</tr>"
    summ += ("<tr><th>혜택률 (혜택 ÷ 사용액)</th>" + "".join(f"<td>{res[p]['benefit_rate']:.2%}</td>" for p in pids) + "</tr>"
             + "<tr><th>연간 공헌이익이 음수인 회원</th>" + "".join(f"<td>{res[p]['neg_share']:.1%}</td>" for p in pids) + "</tr>"
             + "<tr><th>혜택 상위 10% 회원이 받는 혜택 비중</th>" + "".join(f"<td>{res[p]['top10_benefit_share']:.1%}</td>" for p in pids) + "</tr>"
             + "<tr><th>실적 맞추기 발생 (회원·월)</th>" + "".join(f"<td>{res[p]['chase_rate']:.1%}</td>" for p in pids) + "</tr>")

    segs = sorted({s for p in pids for s in res[p]["by_segment"]})
    seg_rows = "".join(f"<tr><th>{esc(s)}</th>" + "".join(
        f"<td>{won(res[p]['by_segment'][s]) if s in res[p]['by_segment'] else '-'}</td>" for p in pids) + "</tr>" for s in segs)

    tier_rows = ""
    for p in pids:
        d = res[p]["tier_dist"]
        if not products[p]["performance"]["tiers"]:
            txt = "무실적 상품 (전월실적 조건 없음)"
        else:
            txt = ", ".join(f"{'구간 미달' if k == 0 else f'{k/10000:,.0f}만 원 이상'} {v:.0%}" for k, v in d.items())
        tier_rows += f"<tr><th>{esc(names[p])}</th><td class='l'>{esc(txt)}</td></tr>"

    ben_rows = ""
    for p in pids:
        tot = sum(res[p]["benefit_by_rule"].values()) or 1
        items = sorted(res[p]["benefit_by_rule"].items(), key=lambda x: -x[1])
        txt = ", ".join(f"{bid} {v / tot:.0%}" for bid, v in items if v > 0)
        unm = "; ".join(u["name"] for u in products[p].get("unmodeled_benefits", [])) or "없음"
        ben_rows += f"<tr><th>{esc(names[p])}</th><td class='l'>{esc(txt)}</td><td class='l'>{esc(unm)}</td></tr>"

    credit_ids = {p for p in pids if products[p]["card_kind"] == "credit"}
    order_credit, flips_credit = ranking_flips(base, rows, credit_ids)

    def verdict(label, od, fl):
        if not fl:
            return f"{label}: 모든 가정값 범위에서 순위 유지 (견고)."
        top = sorted({k for k, _, o in fl if o[0] != od[0]})
        top_txt = (f" 1위({products[od[0]]['name']})는 " +
                   ("모든 범위에서 유지." if not top else "다음 값에서 바뀜 → " + ", ".join(top) + "."))
        return f"{label}: 다음 가정값에 따라 순위가 바뀜 → " + ", ".join(sorted({k for k, _, _ in fl})) + "." + top_txt
    robust = (verdict(f"전체 {len(pids)}종 순위", order, flips) + " " +
              verdict(f"신용카드 {len(credit_ids)}종끼리의 순위", order_credit, flips_credit))
    sens = []
    for grp, key, basis, rng_, lo_hi in rows:
        swing = max(abs(lo_hi[i][p] - base[p]) for i in range(2) for p in pids)
        sens.append((swing, grp, key, basis, rng_, lo_hi))
    sens.sort(key=lambda x: -x[0])
    sens_rows = ""
    for swing, grp, key, basis, rng_, lo_hi in sens:
        cells = "".join(f"<td>{lo_hi[0][p] - base[p]:+,.0f}<br>{lo_hi[1][p] - base[p]:+,.0f}</td>" for p in pids)
        flip_all = any(k == key for k, _, _ in flips)
        flip_cr = any(k == key for k, _, _ in flips_credit)
        tag = "⚠️ 신용끼리 순위 바뀜" if flip_cr else ("⚠️ 신용·체크 순위 바뀜" if flip_all else "유지")
        sens_rows += (f"<tr><th>{esc(key)}<br><span class='sub'>{esc(grp)} · {esc(basis)} · 범위 {esc(str(rng_))}</span></th>"
                      f"{cells}<td>{tag}</td></tr>")


    page = f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>카드 손익 시뮬레이션</title>
<style>
:root {{ --bg:#fff; --fg:#1b1b1f; --muted:#6b6b76; --line:#e3e3e8; --warn:#fff3cd; --head:#f6f6f8; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#141417; --fg:#ececf1; --muted:#9a9aa6; --line:#2c2c33; --warn:#3a3218; --head:#1d1d22; }} }}
body {{ background:var(--bg); color:var(--fg); font-family:-apple-system,"Apple SD Gothic Neo","Noto Sans KR",sans-serif; margin:0; padding:24px 16px; line-height:1.5; }}
main {{ max-width:1100px; margin:0 auto; }}
h1 {{ font-size:1.5rem; margin:0 0 4px; }} h2 {{ font-size:1.15rem; margin:32px 0 8px; }}
p.note {{ color:var(--muted); margin:4px 0 12px; }}
.scroll {{ overflow-x:auto; }}
table {{ border-collapse:collapse; width:100%; font-size:0.88rem; }}
th, td {{ border-bottom:1px solid var(--line); padding:6px 8px; text-align:right; white-space:nowrap; }}
th:first-child {{ text-align:left; }} td.l {{ text-align:left; white-space:normal; }}
thead th {{ background:var(--head); }}
tr.total th, tr.total td {{ font-weight:700; }}
.sub {{ color:var(--muted); font-size:0.75rem; font-weight:400; }}
.callout {{ background:var(--warn); padding:10px 12px; border-radius:6px; }}
</style></head>
<body><main>
<h1>카드 상품 손익 시뮬레이션</h1>
<p class="note">합성 고객 {meta['customers']:,}명 · {meta['months']}개월 ({meta['start']}부터) · 시드 {meta['seed']}. 설계서 docs/simulator_design.md.
금액은 <b>회원 1인 월평균(원)</b>입니다. <b>상품 구조를 비교하는 상대 지표</b>이며 실제 손익 예측이 아닙니다.
가입 가능 고객 전원이 가입한다고 보고(세그먼트별 가입률 동일) 계산했습니다.</p>
<p class="callout">{esc(robust)}</p>

<h2>상품별 손익 (공헌이익 높은 순)</h2>
<div class="scroll"><table><thead><tr><th></th>{head}</tr></thead><tbody>{summ}</tbody></table></div>

<h2>세그먼트별 회원 1인 월 공헌이익</h2>
<div class="scroll"><table><thead><tr><th></th>{head}</tr></thead><tbody>{seg_rows}</tbody></table></div>

<h2>민감도: 가정값 하나를 범위 양 끝으로 바꿨을 때 공헌이익 변화</h2>
<p class="note">위 칸은 범위 하한, 아래 칸은 상한일 때의 변화(원, 회원·월)입니다. 영향이 큰 순서입니다. 가맹점 비중·행동 가정은 앞쪽 {meta['sample']:,}명으로 다시 계산한 변화량입니다.</p>
<div class="scroll"><table><thead><tr><th>가정값</th>{head}<th>순위</th></tr></thead><tbody>{sens_rows}</tbody></table></div>

<h2>전월실적 구간 분포 (회원·월, 첫 달 제외)</h2>
<div class="scroll"><table><tbody>{tier_rows}</tbody></table></div>

<h2>혜택 구성과 제외한 혜택</h2>
<div class="scroll"><table><thead><tr><th>상품</th><th>혜택별 비중</th><th>계산에서 제외한 혜택 (설계서 6장 결정)</th></tr></thead><tbody>{ben_rows}</tbody></table></div>
</main></body></html>
"""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(page, encoding="utf-8")


def main(argv=None):
    ap = argparse.ArgumentParser(description="카드 상품 손익 시뮬레이션")
    ap.add_argument("--customers", type=int, default=10000)
    ap.add_argument("--months", type=int, default=12)
    ap.add_argument("--start", default="2025-01")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--sample", type=int, default=2000, help="민감도 재계산에 쓰는 고객 수")
    ap.add_argument("--report", default="reports/simulation_report.html")
    args = ap.parse_args(argv)

    t0 = time.time()
    _, customers, txns = generate(args.customers, args.months, args.start, args.seed)
    cust, t = prepare(customers, txns, args.start, args.seed)
    products, shares = load_all_products(), load_shares()
    rates, behavior = load_pnl_assumptions()["rates"], load_behavior()
    print(f"데이터 준비: 고객 {len(cust):,}명, 거래 {len(t):,}건 ({time.time() - t0:.0f}초)")

    res, qs = simulate(cust, t, products, shares, rates, behavior, args.seed, args.months)
    print(f"시뮬레이션 완료 ({time.time() - t0:.0f}초)")
    base, rows = sensitivity(qs, cust, t, products, shares, rates, behavior, args.seed, args.months,
                             min(args.sample, len(cust)))
    print(f"민감도 분석 완료 ({time.time() - t0:.0f}초)")
    write_report(args.report, products, res, base, rows,
                 {"customers": len(cust), "months": args.months, "start": args.start, "seed": args.seed,
                  "sample": min(args.sample, len(cust))})
    for pid in sorted(res, key=lambda k: -base[k]):
        r = res[pid]
        print(f"  {display_name(products[pid]):<40} 공헌이익 {r['lines']['공헌이익']:>8,.0f}원/월  혜택률 {r['benefit_rate']:.2%}  "
              f"음수 회원 {r['neg_share']:.0%}")
    print(f"리포트: {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
