"""카드 상품 정의 로드와 검증 (손익 시뮬레이터 설계서 2.2).

상품 파일(config/products/*.json)이 생성 데이터의 업종, 가맹점 비중 파라미터
(config/merchant_shares.json)와 맞는지 확인한다. 오타 하나로 혜택이 조용히 0원이
되지 않도록, 맞지 않으면 바로 오류를 낸다.
"""

import json
from pathlib import Path

from .params import load_params

ROOT = Path(__file__).resolve().parent.parent
PRODUCTS_DIR = ROOT / "config" / "products"
SHARES_PATH = ROOT / "config" / "merchant_shares.json"
PNL_PATH = ROOT / "config" / "pnl_assumptions.json"

CARD_KINDS = {"credit", "check"}
BENEFIT_KINDS = {"rate", "points", "fixed", "per_liter"}
KIND_FIELD = {"rate": "rate", "points": "rate", "fixed": "amount", "per_liter": "per_liter"}
SHARE_LEVEL = {"merchant_share": "txn", "customer_share": "customer"}


class ProductError(ValueError):
    pass


BASIS = {"출처", "추정", "가정"}


def _check_value(key, spec, lo_hi=(0.0, 1.0)):
    v, (lo, hi) = spec["value"], spec["range"]
    if spec.get("basis") not in BASIS:
        raise ProductError(f"{key}: basis는 {sorted(BASIS)} 중 하나")
    if not (lo_hi[0] <= lo <= v <= hi <= lo_hi[1]):
        raise ProductError(f"{key}: value {v}가 range {spec['range']} 안에 있어야 함")


def load_shares(path=None):
    with open(path or SHARES_PATH, encoding="utf-8") as f:
        shares = json.load(f)
    for key, spec in shares["shares"].items():
        if spec["level"] not in SHARE_LEVEL.values():
            raise ProductError(f"{key}: level은 txn 또는 customer")
        _check_value(key, spec)
    _check_value("fuel_price_per_liter", shares["fuel_price_per_liter"], (0.0, float("inf")))
    return shares


CHANNELS = {"online", "offline"}


def _check_match(m, where, categories, shares):
    unknown = set(m) - {"category", "merchant_share", "customer_share", "overseas", "channel"}
    if unknown:
        raise ProductError(f"{where}: 알 수 없는 조건 {sorted(unknown)}")
    if "channel" in m and m["channel"] not in CHANNELS:
        raise ProductError(f"{where}: channel은 online 또는 offline")
    cat = m.get("category")
    if cat != "*" and cat not in categories:
        raise ProductError(f"{where}: 알 수 없는 업종 {cat!r}")
    for field, level in SHARE_LEVEL.items():
        key = m.get(field)
        if key is None:
            continue
        spec = shares["shares"].get(key)
        if spec is None:
            raise ProductError(f"{where}: merchant_shares.json에 없는 비중 {key!r}")
        if spec["category"] != cat:
            raise ProductError(f"{where}: 비중 {key!r}는 {spec['category']} 업종용인데 {cat}에 쓰임")
        if spec["level"] != level:
            raise ProductError(f"{where}: 비중 {key!r}는 level={spec['level']}인데 {field}로 쓰임")


def validate_product(prod, categories, shares):
    pid = prod.get("id", "?")
    for field in ("id", "name", "card_kind", "annual_fee", "performance", "benefits", "pools", "source"):
        if field not in prod:
            raise ProductError(f"{pid}: 필수 항목 {field!r} 없음")
    if prod["card_kind"] not in CARD_KINDS:
        raise ProductError(f"{pid}: card_kind는 credit 또는 check")

    perf = prod["performance"]
    tiers = perf["tiers"]
    if tiers != sorted(tiers):
        raise ProductError(f"{pid}: performance.tiers는 오름차순이어야 함")
    for c in perf.get("exclude_categories", []):
        if c not in categories:
            raise ProductError(f"{pid}: 실적 제외 업종 {c!r}를 알 수 없음")
    for i, m in enumerate(perf.get("exclude_shares", [])):
        _check_match(m, f"{pid}.performance.exclude_shares[{i}]", categories, shares)
    grace = perf.get("grace")
    if grace and grace["tier"] not in tiers:
        raise ProductError(f"{pid}: grace.tier {grace['tier']}가 tiers에 없음")

    valid_tiers = {0, *tiers}
    for name, by_tier in prod["pools"].items():
        if by_tier.get("period", "month") not in ("month", "day"):
            raise ProductError(f"{pid}: 한도 {name!r}의 period는 month 또는 day")
        for t in by_tier:
            if t == "period":
                continue
            if int(t) not in valid_tiers:
                raise ProductError(f"{pid}: 한도 {name!r}의 구간 {t}가 tiers에 없음")

    ids = set()
    for b in prod["benefits"]:
        where = f"{pid}.{b.get('id', '?')}"
        if b.get("id") in ids:
            raise ProductError(f"{where}: 혜택 id 중복")
        ids.add(b.get("id"))
        if b.get("kind") not in BENEFIT_KINDS:
            raise ProductError(f"{where}: kind는 {sorted(BENEFIT_KINDS)} 중 하나")
        if KIND_FIELD[b["kind"]] not in b:
            raise ProductError(f"{where}: kind={b['kind']}에는 {KIND_FIELD[b['kind']]!r} 필요")
        if not b.get("match"):
            raise ProductError(f"{where}: match가 비어 있음")
        for i, m in enumerate(b["match"]):
            _check_match(m, f"{where}.match[{i}]", categories, shares)
        for i, m in enumerate(b.get("exclude", [])):
            _check_match(m, f"{where}.exclude[{i}]", categories, shares)
        if "top_of" in b and not isinstance(b["top_of"], str):
            raise ProductError(f"{where}: top_of는 그룹 이름(문자열)")
        mt = b.get("min_tier")
        if mt is not None and mt not in tiers:
            raise ProductError(f"{where}: min_tier {mt}가 tiers에 없음")
        for p in b.get("pools", []):
            if p not in prod["pools"]:
                raise ProductError(f"{where}: 정의되지 않은 한도 {p!r}")
        hours = b.get("hours")
        if hours is not None and not (len(hours) == 2 and all(0 <= h <= 24 for h in hours)):
            raise ProductError(f"{where}: hours는 [시작, 끝] (0~24)")
        if any(not 0 <= d <= 6 for d in b.get("weekdays", [])):
            raise ProductError(f"{where}: weekdays는 0(월)~6(일)")
    return prod


def load_product(path, params=None, shares=None):
    params = params or load_params()
    shares = shares or load_shares()
    with open(path, encoding="utf-8") as f:
        prod = json.load(f)
    return validate_product(prod, set(params["categories"]), shares)


def load_all_products(directory=None, params=None, shares=None):
    params = params or load_params()
    shares = shares or load_shares()
    return {p.stem: load_product(p, params, shares) for p in sorted(Path(directory or PRODUCTS_DIR).glob("*.json"))}


def load_pnl_assumptions(path=None):
    """손익 요율 (설계서 2.3). 값이 범위 안에 있고 근거 구분이 붙어 있는지 확인한다."""
    with open(path or PNL_PATH, encoding="utf-8") as f:
        pnl = json.load(f)
    for key, spec in pnl["rates"].items():
        _check_value(key, spec, (0.0, float("inf")))
    return pnl
