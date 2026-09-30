"""생성 결과 검증과 HTML 리포트 (설계서 5장)."""

import html
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .benchmarks import comparison_rows
from .expected import expected_table

SEGMENT_LABELS = {
    "S1_STUDENT": "S1 대학생", "S2_YOUNG_WORKER": "S2 사회초년생", "S3_SINGLE_30S": "S3 30대 1인",
    "S4_FAMILY_KIDS": "S4 양육가구", "S5_MIDDLE_AGE": "S5 중장년", "S6_SELF_EMPLOYED": "S6 자영업",
    "S7_SENIOR": "S7 고령층",
}


@dataclass
class Check:
    name: str
    passed: bool
    detail: str


def _within(actual, expected, rel_tol, se):
    """±rel_tol 이내이거나, 표본오차(se)의 3배 이내면 통과."""
    diff = abs(actual - expected)
    return diff <= rel_tol * expected or diff <= 3 * se


def category_floors(p):
    floors = {}
    for c in p["categories"]:
        spec = p["ticket"][c]
        f = p["mixtures"][spec["mixture"]]["floor"] if "mixture" in spec else spec.get("floor", 0)
        if f:
            floors[c] = f
    return floors


def per_customer_monthly(customers, txns, n_months):
    """고객별 월 평균 결제 횟수·사용액 (취소 거래 제외)."""
    orig = txns[~txns["is_cancelled"]]
    agg = orig.groupby("customer_id")["amount"].agg(["size", "sum"])
    df = customers[["customer_id", "segment"]].set_index("customer_id")
    df["count"] = agg["size"].reindex(df.index, fill_value=0) / n_months
    df["spend"] = agg["sum"].reindex(df.index, fill_value=0) / n_months
    return df


def run_checks(p, customers, txns, n_months):
    checks = []
    exp = expected_table(p)
    pc = per_customer_monthly(customers, txns, n_months)

    # 세그먼트별 월 결제 횟수·사용액이 설계값(3.4.1)과 맞는지.
    # 생성 로직을 검증하는 항목이라, 이미 뽑힌 고객의 활동성 계수 평균은 반영한다
    # (고객 표본이 우연히 활발하게 뽑힌 것까지 로직 오류로 보지 않기 위해).
    activity = customers.groupby("segment")["activity_factor"].mean()
    autopay = {c for c in p["categories"] if p["ticket"][c].get("autopay")}
    for metric, idx, label, tol in (("count", 0, "월 결제 횟수", 0.05), ("spend", 1, "월 사용액", 0.05)):
        bad = []
        for s in p["segments"]:
            v = pc.loc[pc["segment"] == s, metric]
            if len(v) == 0:
                continue
            e = sum(x[idx] * (1.0 if c in autopay else activity[s]) for c, x in exp[s].items())
            se = v.std(ddof=1) / np.sqrt(len(v)) if len(v) > 1 else 0.0
            if not _within(v.mean(), e, tol, se):
                bad.append(f"{s}: {v.mean():,.1f} (설계 {e:,.1f})")
        checks.append(Check(f"세그먼트별 {label} (설계값 ±5% 또는 표본오차 3배 이내, 활동성 계수 표본 평균 반영)", not bad,
                            "; ".join(bad) or "전 세그먼트 통과"))

    orig = txns[~txns["is_cancelled"]]
    cancels = txns[txns["is_cancelled"]]

    checks.append(Check("거래 ID 중복 없음", txns["txn_id"].is_unique, f"{len(txns):,}건"))
    checks.append(Check("금액 > 0", bool((txns["amount"] > 0).all()), f"최소 {txns['amount'].min():,}원"))

    floors = category_floors(p)
    mins = orig.groupby("category")["amount"].min()
    below = [f"{c} {mins[c]:,}원 < {f:,}원" for c, f in floors.items() if c in mins and mins[c] < f]
    checks.append(Check("업종별 최저 결제금액 미만 0건 (3.2)", not below,
                        "; ".join(below) or ", ".join(f"{c} {f:,}원" for c, f in floors.items())))

    bad_inst = int(((txns["payment_type"] == "check") & (txns["installment_months"] > 0)).sum())
    checks.append(Check("체크카드 할부 거래 0건 (2.4)", bad_inst == 0, f"{bad_inst}건"))

    min_amt = p["installment"]["min_amount"]
    small_inst = int(((txns["installment_months"] > 0) & (txns["amount"] < min_amt)).sum())
    checks.append(Check(f"할부는 {min_amt:,}원 이상만 (3.6)", small_inst == 0, f"위반 {small_inst}건"))

    no_credit = set(customers.loc[~customers["has_credit_card"], "customer_id"])
    credit_by_non_holder = int((txns["customer_id"].isin(no_credit) & (txns["payment_type"] == "credit")).sum())
    checks.append(Check("신용카드 미보유 고객의 신용 결제 0건", credit_by_non_holder == 0, f"{credit_by_non_holder}건"))

    matched = cancels.merge(orig, left_on="original_txn_id", right_on="txn_id", suffixes=("", "_o"))
    ok = (len(matched) == len(cancels)
          and bool((matched["amount"] == matched["amount_o"]).all())
          and bool((matched["customer_id"] == matched["customer_id_o"]).all())
          and bool((matched["txn_datetime"] > matched["txn_datetime_o"]).all()))
    checks.append(Check("취소 거래는 원거래와 매칭 (같은 고객·금액, 이후 시점)", ok,
                        f"취소 {len(cancels):,}건 / 원거래 대비 {len(cancels) / max(len(orig), 1):.2%}"))

    return checks


def segment_table(p, customers, txns, n_months):
    """3.4.1과 같은 형식: 세그먼트별 설계값 vs 생성값."""
    exp = expected_table(p)
    pc = per_customer_monthly(customers, txns, n_months)
    orig = txns[~txns["is_cancelled"]].merge(customers[["customer_id", "segment"]], on="customer_id")
    by_cat = orig.groupby(["segment", "category"])["amount"].sum()
    rows = []
    for s in p["segments"]:
        sub = pc[pc["segment"] == s]
        e_cnt = sum(v[0] for v in exp[s].values())
        e_amt = sum(v[1] for v in exp[s].values())
        total = by_cat.loc[s].sum() if s in by_cat.index.get_level_values(0) else 0
        shares = {c: (by_cat.get((s, c), 0) / total if total else 0.0) for c in p["categories"]}
        e_shares = {c: exp[s][c][1] / e_amt for c in p["categories"]}
        rows.append({"segment": s, "customers": len(sub),
                     "count": sub["count"].mean(), "count_exp": e_cnt,
                     "spend": sub["spend"].mean(), "spend_exp": e_amt,
                     "shares": shares, "shares_exp": e_shares})
    return rows


def write_report(path, p, customers, txns, n_months, meta):
    checks = run_checks(p, customers, txns, n_months)
    rows = segment_table(p, customers, txns, n_months)
    esc = html.escape

    check_rows = "".join(
        f"<tr><td>{'✅' if c.passed else '❌'}</td><td>{esc(c.name)}</td><td>{esc(c.detail)}</td></tr>"
        for c in checks)

    head = "".join(f"<th>{esc(SEGMENT_LABELS.get(r['segment'], r['segment']))}</th>" for r in rows)

    def pair(actual, expected, fmt):
        diff = (actual / expected - 1) if expected else 0
        cls = "warn" if abs(diff) > 0.05 else ""
        return f"<td class='{cls}'>{fmt(actual)}<br><span class='sub'>설계 {fmt(expected)} ({diff:+.1%})</span></td>"

    seg_rows = (
        "<tr><th>고객 수</th>" + "".join(f"<td>{r['customers']:,}</td>" for r in rows) + "</tr>"
        + "<tr><th>월 결제 횟수</th>" + "".join(pair(r["count"], r["count_exp"], lambda v: f"{v:,.1f}회") for r in rows) + "</tr>"
        + "<tr><th>월 사용액</th>" + "".join(pair(r["spend"], r["spend_exp"], lambda v: f"{v / 1e4:,.1f}만") for r in rows) + "</tr>"
    )
    share_rows = "".join(
        f"<tr><th>{c}</th>" + "".join(
            f"<td>{r['shares'][c]:.1%}<br><span class='sub'>설계 {r['shares_exp'][c]:.1%}</span></td>" for r in rows) + "</tr>"
        for c in p["categories"])

    orig = txns[~txns["is_cancelled"]]
    summary = {
        "고객 수": f"{len(customers):,}명",
        "거래 수 (취소 포함)": f"{len(txns):,}건",
        "기간": f"{meta['start']} 부터 {n_months}개월",
        "시드": str(meta["seed"]),
        "체크카드 결제 비중 (금액)": f"{orig.loc[orig['payment_type'] == 'check', 'amount'].sum() / orig['amount'].sum():.1%}",
        "할부 비중 (신용 5만 원 이상 거래)": _installment_share(orig, p),
        "해외 결제 비중 (금액)": f"{orig.loc[orig['is_overseas'], 'amount'].sum() / orig['amount'].sum():.1%}",
        "온라인 결제 비중 (금액)": f"{orig.loc[orig['channel'] == 'online', 'amount'].sum() / orig['amount'].sum():.1%}",
    }
    summary_rows = "".join(f"<tr><th>{esc(k)}</th><td>{esc(v)}</td></tr>" for k, v in summary.items())

    def fmt(v, kind):
        return {"won": f"{v:,.0f}원", "count": f"{v:,.1f}건", "pct": f"{v:.1%}"}[kind]

    bench_rows = ""
    for label, gen, tgt, kind in comparison_rows(customers, txns, n_months):
        diff = gen / tgt - 1 if tgt else 0
        cls = "warn" if abs(diff) > 0.15 else ""
        bench_rows += (f"<tr><th>{esc(label)}</th><td>{fmt(gen, kind)}</td><td>{fmt(tgt, kind)}</td>"
                       f"<td class='{cls}'>{diff:+.1%}</td></tr>")

    page = f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>합성 데이터 검증 리포트</title>
<style>
:root {{ --bg:#fff; --fg:#1b1b1f; --muted:#6b6b76; --line:#e3e3e8; --warn:#fff3cd; --head:#f6f6f8; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#141417; --fg:#ececf1; --muted:#9a9aa6; --line:#2c2c33; --warn:#3a3218; --head:#1d1d22; }} }}
body {{ background:var(--bg); color:var(--fg); font-family:-apple-system,"Apple SD Gothic Neo","Noto Sans KR",sans-serif; margin:0; padding:24px 16px; line-height:1.5; }}
main {{ max-width:1100px; margin:0 auto; }}
h1 {{ font-size:1.5rem; margin:0 0 4px; }} h2 {{ font-size:1.15rem; margin:32px 0 8px; }}
p.note {{ color:var(--muted); margin:4px 0 12px; }}
.scroll {{ overflow-x:auto; }}
table {{ border-collapse:collapse; width:100%; font-size:0.9rem; }}
th, td {{ border-bottom:1px solid var(--line); padding:6px 8px; text-align:right; white-space:nowrap; }}
th:first-child, td:first-child {{ text-align:left; }}
thead th {{ background:var(--head); }}
.checks td {{ text-align:left; white-space:normal; }}
.sub {{ color:var(--muted); font-size:0.78rem; }}
td.warn {{ background:var(--warn); }}
</style></head>
<body><main>
<h1>합성 데이터 검증 리포트</h1>
<p class="note">설계서 docs/data_assumptions.md 의 가정값과 생성 결과, 그리고 공개 통계를 비교합니다.</p>

<h2>요약</h2>
<table>{summary_rows}</table>

<h2>공개 통계 비교</h2>
<p class="note">목표값은 data/reference/benchmarks.csv (여신금융협회 2025년 카드승인실적, 행정안전부 2025년 말 주민등록인구)에서 계산합니다. 수치는 보도 기사로 확인한 값이며, 차이가 ±15%를 넘는 칸은 노란색입니다.</p>
<div class="scroll"><table><thead><tr><th>지표</th><th>생성 데이터</th><th>공개 통계</th><th>차이</th></tr></thead><tbody>{bench_rows}</tbody></table></div>

<h2>검증 항목</h2>
<table class="checks"><thead><tr><th></th><th>항목</th><th>결과</th></tr></thead><tbody>{check_rows}</tbody></table>

<h2>세그먼트별 월 평균 (고객 1인 기준, 취소 제외)</h2>
<p class="note">설계값 대비 ±5%를 넘는 칸은 노란색입니다. 고객 수가 적은 세그먼트는 표본 편차로 벗어날 수 있습니다.</p>
<div class="scroll"><table><thead><tr><th></th>{head}</tr></thead><tbody>{seg_rows}</tbody></table></div>

<h2>세그먼트별 업종 사용액 비중</h2>
<div class="scroll"><table><thead><tr><th>업종</th>{head}</tr></thead><tbody>{share_rows}</tbody></table></div>
</main></body></html>
"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(page)
    return checks


def _installment_share(orig, p):
    base = orig[(orig["payment_type"] == "credit") & (orig["amount"] >= p["installment"]["min_amount"])]
    if len(base) == 0:
        return "해당 거래 없음"
    return f"{(base['installment_months'] > 0).mean():.1%}"
