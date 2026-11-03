"""뱅크샐러드 상품 페이지에서 카드 조건을 받아 원자료로 저장하고 요약을 출력한다.

사용 예:
    python scripts/fetch_banksalad.py CARD000004 CARD003051

저장 위치: data/reference/raw/products/banksalad_<상품ID>.json
상품 페이지의 __NEXT_DATA__에 들어 있는 구조화된 상품 정보를 그대로 저장한다.
요약에는 혜택별 설명, 공유 한도(chartBenefitLimits), 유의사항을 보여준다.
"""

import datetime as dt
import html
import json
import re
import subprocess
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "data" / "reference" / "raw" / "products"


def strip(h):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", h or ""))).strip()


def fetch(guid):
    url = f"https://www.banksalad.com/product/cards/{guid}"
    page = subprocess.run(["curl", "-sSL", "-m", "30", url], capture_output=True, text=True).stdout
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', page, re.S)
    if not m:
        raise SystemExit(f"{guid}: __NEXT_DATA__ 없음")
    for q in json.loads(m.group(1))["props"]["pageProps"]["dehydratedState"]["queries"]:
        data = q.get("state", {}).get("data", {})
        if isinstance(data, dict) and "card" in data:
            return url, data["card"]["card"]
    raise SystemExit(f"{guid}: 상품 정보 없음")


def summarize(card):
    titles = {b["id"]: b["title"] for b in card.get("chartBenefits", [])}
    print(f"=== {card['productGuid']} {card['organization']['name']} {card['name']} | {card['cardTypeEnum']} | "
          f"발급중단 {card['isStopped']} | 연회비 {[a['amountKrw0f'] for a in card['annualCosts']]} | "
          f"전월실적 {card.get('minimumPreviousMonthPaymentAmountKrw0f')} 구간 {card.get('prevMonthPaymentAmountKrw0fOptions')}")
    seen = set()
    for b in card.get("chartBenefits", []):
        d = strip(b.get("description"))
        grades = [(g.get("requiredPrevMonthPaymentAmountKrw0f"), g.get("calculatedMaxBenefitAmountKrw0f")) for g in b.get("grades", [])]
        print(f"- [{b['title']}] id={b['id']} {b['summary'].replace(chr(10), ' ')} | 구간별 최대 {grades}")
        if d not in seen:
            seen.add(d)
            print(f"    {d}")
    for lim in card.get("chartBenefitLimits") or []:
        print(f"  한도 {lim.get('requiredPrevMonthPaymentAmountKrw0f')} {[titles.get(i, i) for i in lim['chartBenefitIds']]} {lim['limitAmount']}")
    foot = strip(card.get("footerDescription"))
    if foot:
        print(f"  유의사항: {foot}")


def main(guids):
    OUT.mkdir(parents=True, exist_ok=True)
    for guid in guids:
        url, card = fetch(guid)
        (OUT / f"banksalad_{guid}.json").write_text(json.dumps(
            {"_source": url, "_downloaded": dt.date.today().isoformat(), "card": card}, ensure_ascii=False, indent=1),
            encoding="utf-8")
        summarize(card)


if __name__ == "__main__":
    main(sys.argv[1:])
