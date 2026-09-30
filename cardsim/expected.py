"""설정값만으로 계산한 기대값 (설계서 3.4.1 표).

생성 결과가 설계 의도대로 나왔는지 검증 리포트에서 비교하는 기준이다.
"""

import math

from .params import multiplier


def _phi(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def lognormal_mean(median, sigma, floor=0.0):
    """E[max(floor, X)], X ~ LogNormal(ln(median), sigma)."""
    mu = math.log(median)
    if floor <= 0:
        return math.exp(mu + sigma**2 / 2)
    z = (math.log(floor) - mu) / sigma
    tail = math.exp(mu + sigma**2 / 2) * (1 - _phi(z - sigma))
    return floor * _phi(z) + tail


def mean_ticket(p, segment, category):
    spec = p["ticket"][category]
    mult = multiplier(p, segment, category)
    if "mixture" not in spec:
        return lognormal_mean(spec["median"] * mult, spec["sigma"])
    mix = p["mixtures"][spec["mixture"]]
    share = mix["low_share"][segment]
    means = {}
    for tier in ("low", "high"):
        t = mix[tier]
        m = t["median"] * (mult if t["segment_multiplier"] else 1.0)
        means[tier] = lognormal_mean(m, t["sigma"], mix["floor"])
    return share * means["low"] + (1 - share) * means["high"]


def expected_table(p):
    """세그먼트별 {업종: (월 평균 결제 횟수, 월 평균 사용액)}."""
    out = {}
    for si, s in enumerate(p["segments"]):
        rows = {}
        for c in p["categories"]:
            freq = p["monthly_frequency"][c][si]
            part = p["participation"][c][si] if c in p["participation"] else 1.0
            count = freq * part
            rows[c] = (count, count * mean_ticket(p, s, c))
        out[s] = rows
    return out
