"""거래 테이블 생성 (설계서 3장)."""

import calendar

import numpy as np
import pandas as pd

from .params import multiplier, participation_categories, season_factors

SECONDS_PER_DAY = 86400


# ---------------------------------------------------------------- 금액 (3.2)

def _lognormal(rng, median, sigma, size):
    return rng.lognormal(mean=np.log(median), sigma=sigma, size=size)


def _round_won(x):
    return np.maximum(10, np.round(x / 10) * 10).astype(np.int64)


def segment_multipliers(p, category):
    return np.array([multiplier(p, s, category) for s in p["segments"]])


def sample_amounts(p, category, seg_rows, rng):
    """업종별 건당 금액. seg_rows는 거래별 세그먼트 인덱스."""
    n = len(seg_rows)
    spec = p["ticket"][category]
    mult = segment_multipliers(p, category)[seg_rows]

    if "mixture" not in spec:
        amounts = _round_won(_lognormal(rng, spec["median"] * mult, spec["sigma"], n))
        return np.maximum(amounts, spec.get("floor", 0))

    mix = p["mixtures"][spec["mixture"]]
    low_share = np.array([mix["low_share"][s] for s in p["segments"]])[seg_rows]
    is_low = rng.random(n) < low_share
    amounts = np.empty(n)
    for tier, mask in (("low", is_low), ("high", ~is_low)):
        t = mix[tier]
        tier_mult = mult[mask] if t["segment_multiplier"] else 1.0
        amounts[mask] = _lognormal(rng, t["median"] * tier_mult, t["sigma"], int(mask.sum()))
    return np.maximum(_round_won(amounts), mix["floor"])


# ---------------------------------------------------------------- 일시 (3.4)

def _hour_weights(profile):
    w = np.zeros(24)
    for start, end, weight in profile:
        w[start:end] = weight
    return w / w.sum()


def _sample_timestamps(p, category, year, month, n, rng):
    """해당 월 안에서 요일·시간대 가중치를 반영한 초 단위 오프셋."""
    days_in_month = calendar.monthrange(year, month)[1]
    weekday = np.array([calendar.weekday(year, month, d + 1) for d in range(days_in_month)])
    w_weekend = p["timing"]["weekend_weight"].get(category, 1.0)
    day_w = np.where(weekday >= 5, w_weekend, 1.0)
    day = rng.choice(days_in_month, size=n, p=day_w / day_w.sum())

    profiles = p["timing"]["hour_profile"]
    hour = rng.choice(24, size=n, p=_hour_weights(profiles.get(category, profiles["_default"])))
    sec = rng.integers(0, 3600, size=n)
    return day * SECONDS_PER_DAY + hour * 3600 + sec


# ---------------------------------------------------------------- 생성 본체

def generate_transactions(p, customers, n_months, start_year, start_month, rng):
    segs, cats = p["segments"], p["categories"]
    seg_of = {s: i for i, s in enumerate(segs)}
    seg_idx = customers["segment"].map(seg_of).to_numpy()
    activity = customers["activity_factor"].to_numpy()
    n_cust = len(customers)

    # 고객 × 업종 고정 요인: 업종 취향(감마, 평균 1)과 이용 여부(†)
    shape = p["individual_variation"]["taste_gamma_shape"]
    taste = rng.gamma(shape, 1.0 / shape, size=(n_cust, len(cats)))
    uses = np.ones((n_cust, len(cats)), dtype=bool)
    for c in participation_categories(p):
        rate = np.array(p["participation"][c])[seg_idx]
        uses[:, cats.index(c)] = rng.random(n_cust) < rate

    freq = np.array([p["monthly_frequency"][c] for c in cats]).T  # (세그먼트, 업종)
    season = season_factors(p)

    parts = {"cust": [], "cat": [], "ts": [], "amount": []}

    def add(cust_rows, cat_i, ts, amount):
        parts["cust"].append(cust_rows)
        parts["cat"].append(np.full(len(cust_rows), cat_i, dtype=np.int16))
        parts["ts"].append(ts)
        parts["amount"].append(amount)

    # 자동납부 업종: 고객별 청구 건수·납부일·기본 금액을 한 번 정하고 매달 반복
    autopay = {}
    for ci, c in enumerate(cats):
        if not p["ticket"][c].get("autopay"):
            continue
        lam = freq[seg_idx, ci] * taste[:, ci] * uses[:, ci]
        n_bills = rng.poisson(lam)
        rows = np.repeat(np.arange(n_cust), n_bills)
        autopay[ci] = (rows,
                       rng.integers(1, 29, size=len(rows)),
                       sample_amounts(p, c, seg_idx[rows], rng))

    month_starts = []
    for m in range(n_months):
        y = start_year + (start_month - 1 + m) // 12
        mo = (start_month - 1 + m) % 12 + 1
        month_start = np.datetime64(f"{y:04d}-{mo:02d}-01T00:00:00")
        month_starts.append(month_start)
        base = month_start.astype("datetime64[s]").astype(np.int64)

        for ci, c in enumerate(cats):
            if ci in autopay:
                rows, bill_day, base_amt = autopay[ci]
                if len(rows) == 0:
                    continue
                ts = base + (bill_day - 1) * SECONDS_PER_DAY + 9 * 3600 + rng.integers(0, 3600, len(rows))
                amt = _round_won(base_amt * rng.lognormal(0, 0.05, len(rows)))
                add(rows, ci, ts, amt)
                continue
            lam = freq[seg_idx, ci] * uses[:, ci] * activity * taste[:, ci] * season[mo - 1]
            counts = rng.poisson(lam)
            rows = np.repeat(np.arange(n_cust), counts)
            if len(rows) == 0:
                continue
            ts = base + _sample_timestamps(p, c, y, mo, len(rows), rng)
            add(rows, ci, ts, sample_amounts(p, c, seg_idx[rows], rng))

    cust = np.concatenate(parts["cust"])
    cat = np.concatenate(parts["cat"])
    ts = np.concatenate(parts["ts"])
    amount = np.concatenate(parts["amount"])
    order = np.lexsort((ts, cust))
    cust, cat, ts, amount = cust[order], cat[order], ts[order], amount[order]
    n = len(cust)

    cat_names = np.array(cats)

    # 채널: 업종 기본 채널, mixed 업종은 고객 온라인 성향으로 결정
    channel_spec = np.array([p["ticket"][c]["channel"] for c in cats])[cat]
    online = customers["online_ratio"].to_numpy()[cust]
    is_online = np.where(channel_spec == "mixed", rng.random(n) < online, channel_spec == "online")

    # 결제 수단 (2.4)
    has_credit = customers["has_credit_card"].to_numpy()[cust]
    check_ratio = customers["check_card_ratio"].to_numpy()[cust]
    is_check = ~has_credit | (rng.random(n) < check_ratio)

    # 할부 (3.6): 신용카드, 기준 금액 이상
    inst = p["installment"]
    w_inst = np.array([inst["category_weight"].get(c, 0.0) for c in cats])[cat]
    prob = np.minimum(1.0, customers["installment_propensity"].to_numpy()[cust] * w_inst)
    takes = (~is_check) & (amount >= inst["min_amount"]) & (rng.random(n) < prob)
    months = np.zeros(n, dtype=np.int16)
    months[takes] = rng.choice(inst["months"], size=int(takes.sum()), p=inst["months_prob"])

    # 해외 결제 (3.6)
    w_ovs = np.array([p["overseas"]["category_weight"].get(c, 0.0) for c in cats])[cat]
    ovs_prob = np.minimum(1.0, customers["overseas_propensity"].to_numpy()[cust] * w_ovs)
    is_overseas = rng.random(n) < ovs_prob

    excluded = np.array([bool(p["ticket"][c].get("excluded_from_performance")) for c in cats])[cat]

    txn_ids = np.array([f"TXN_{i:010d}" for i in range(1, n + 1)])
    df = pd.DataFrame({
        "txn_id": txn_ids,
        "customer_id": customers["customer_id"].to_numpy()[cust],
        "txn_datetime": ts.astype("datetime64[s]"),
        "category": cat_names[cat],
        "amount": amount,
        "channel": np.where(is_online, "online", "offline"),
        "payment_type": np.where(is_check, "check", "credit"),
        "is_overseas": is_overseas,
        "installment_months": months,
        "is_cancelled": False,
        "excluded_from_performance": excluded,
        "original_txn_id": None,
    })

    # 취소 거래 (3.6): 원거래와 같은 금액, 1~7일 뒤
    cfg = p["cancel"]
    eligible = ~np.isin(cat_names[cat], cfg["exclude_categories"])
    picked = np.flatnonzero(eligible & (rng.random(n) < cfg["rate"]))
    if len(picked):
        lo, hi = cfg["delay_days"]
        delay = rng.integers(lo * SECONDS_PER_DAY, (hi + 1) * SECONDS_PER_DAY, size=len(picked))
        cancels = df.iloc[picked].copy()
        cancels["original_txn_id"] = cancels["txn_id"].to_numpy()
        cancels["txn_id"] = [f"TXN_{i:010d}" for i in range(n + 1, n + 1 + len(picked))]
        cancels["txn_datetime"] = cancels["txn_datetime"] + pd.to_timedelta(delay, unit="s")
        cancels["is_cancelled"] = True
        df = pd.concat([df, cancels], ignore_index=True)
        df = df.sort_values(["customer_id", "txn_datetime"], kind="stable", ignore_index=True)

    return df
