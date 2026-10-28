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

CARD_KINDS = {"credit", "check"}
BENEFIT_KINDS = {"rate", "points", "fixed", "per_liter"}
KIND_FIELD = {"rate": "rate", "points": "rate", "fixed": "amount", "per_liter": "per_liter"}
SHARE_LEVEL = {"merchant_share": "txn", "customer_share": "customer"}


class ProductError(ValueError):
    pass


def load_shares(path=None):
    with open(path or SHARES_PATH, encoding="utf-8") as f:
        return json.load(f)


def _check_match(m, where, categories, shares):
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
        for t in by_tier:
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
