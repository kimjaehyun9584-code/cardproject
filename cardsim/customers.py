"""고객 테이블 생성 (설계서 2장)."""

import numpy as np
import pandas as pd


def _pick(rng, dist, size):
    keys = list(dist)
    probs = np.array([dist[k] for k in keys], dtype=float)
    return rng.choice(keys, size=size, p=probs / probs.sum())


def _beta_around(rng, mean, concentration, size):
    return rng.beta(mean * concentration, (1 - mean) * concentration, size=size)


def generate_customers(p, n_customers, rng):
    segs = p["segments"]
    shares = np.array([p["segment_share"][s] for s in segs])
    seg_idx = rng.choice(len(segs), size=n_customers, p=shares)
    seg_idx.sort()  # 세그먼트별로 모아 두면 이후 처리와 결과 확인이 쉽다

    kappa = p["propensity_beta_concentration"]
    sigma = p["individual_variation"]["activity_sigma"]
    cols = {k: np.empty(n_customers, dtype=object) for k in ("age_band", "household", "income_band")}
    online = np.empty(n_customers)
    install = np.empty(n_customers)
    overseas = np.empty(n_customers)
    has_credit = np.empty(n_customers, dtype=bool)
    check_ratio = np.empty(n_customers)

    for i, s in enumerate(segs):
        mask = seg_idx == i
        k = int(mask.sum())
        if k == 0:
            continue
        prof = p["segment_profile"][s]
        for col in cols:
            cols[col][mask] = _pick(rng, prof[col], k)
        online[mask] = _beta_around(rng, prof["online_ratio"], kappa, k)
        install[mask] = _beta_around(rng, prof["installment_propensity"], kappa, k)
        overseas[mask] = _beta_around(rng, prof["overseas_propensity"], kappa, k)
        credit = rng.random(k) < p["card_type"]["has_credit_prob"][s]
        has_credit[mask] = credit
        ratio = _beta_around(rng, p["card_type"]["check_card_ratio"][s], kappa, k)
        check_ratio[mask] = np.where(credit, ratio, 1.0)

    # 활동성 계수: 평균 1.0인 로그정규분포 (2.3)
    activity = rng.lognormal(mean=-sigma**2 / 2, sigma=sigma, size=n_customers)

    return pd.DataFrame({
        "customer_id": [f"CUST_{i + 1:06d}" for i in range(n_customers)],
        "segment": np.array(segs)[seg_idx],
        **cols,
        "activity_factor": activity.round(4),
        "online_ratio": online.round(4),
        "installment_propensity": install.round(4),
        "overseas_propensity": overseas.round(4),
        "has_credit_card": has_credit,
        "check_card_ratio": check_ratio.round(4),
    })
