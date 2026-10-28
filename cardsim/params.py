"""설계서(docs/data_assumptions.md) 가정값을 담은 config/params.json 로더."""

import json
from pathlib import Path

DEFAULT_PARAMS_PATH = Path(__file__).resolve().parent.parent / "config" / "params.json"


def load_params(path=None):
    with open(path or DEFAULT_PARAMS_PATH, encoding="utf-8") as f:
        params = json.load(f)
    _validate(params)
    return params


def _validate(p):
    segs, cats = p["segments"], p["categories"]
    share = sum(p["segment_share"][s] for s in segs)
    if abs(share - 1.0) > 1e-9:
        raise ValueError(f"segment_share 합계가 1이 아닙니다: {share}")
    for c in cats:
        if c not in p["ticket"]:
            raise ValueError(f"ticket 설정 누락: {c}")
        if len(p["monthly_frequency"][c]) != len(segs):
            raise ValueError(f"monthly_frequency 길이 불일치: {c}")
    for c, rates in p["participation"].items():
        if c.startswith("_"):
            continue
        if len(rates) != len(segs):
            raise ValueError(f"participation 길이 불일치: {c}")
    if len(p["seasonality"]["factors"]) != 12:
        raise ValueError("seasonality.factors는 12개월이어야 합니다")


def participation_categories(p):
    return [c for c in p["participation"] if not c.startswith("_")]


def season_factors(p):
    """연평균이 1.0이 되도록 정규화한 월별 계수 (index 0 = 1월)."""
    f = p["seasonality"]["factors"]
    mean = sum(f) / len(f)
    return [x / mean for x in f]


def multiplier(p, segment, category):
    """3.2.1 세그먼트 계수 (예외 포함). 계수를 쓰지 않는 업종은 1.0."""
    override = p["segment_multiplier_override"].get(segment, {})
    if category in override:
        return override[category]
    if p["ticket"][category].get("segment_multiplier", True) is False:
        return 1.0
    return p["segment_multiplier"][segment]
