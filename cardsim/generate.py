"""합성 고객·거래 데이터 생성 CLI.

사용 예:
    python -m cardsim.generate --customers 10000 --months 12 --seed 42
"""

import argparse
import time
from pathlib import Path

import numpy as np

from .customers import generate_customers
from .params import load_params
from .transactions import generate_transactions
from .validate import write_report


def generate(n_customers, n_months, start, seed, params=None):
    p = params or load_params()
    year, month = (int(x) for x in start.split("-"))
    rng = np.random.default_rng(seed)
    customers = generate_customers(p, n_customers, rng)
    txns = generate_transactions(p, customers, n_months, year, month, rng)
    return p, customers, txns


def main(argv=None):
    ap = argparse.ArgumentParser(description="카드 합성 고객·거래 데이터 생성")
    ap.add_argument("--customers", type=int, default=10000)
    ap.add_argument("--months", type=int, default=12)
    ap.add_argument("--start", default="2025-01", help="시작 연월 (YYYY-MM)")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--params", default=None, help="파라미터 JSON 경로 (기본: config/params.json)")
    ap.add_argument("--out", default="data/synthetic")
    ap.add_argument("--format", choices=["parquet", "csv"], default="parquet",
                    help="거래 테이블 형식. 1만 명·12개월이면 약 1,100만 건이라 parquet 권장")
    ap.add_argument("--report", default="reports/validation_report.html")
    args = ap.parse_args(argv)

    t0 = time.time()
    p, customers, txns = generate(args.customers, args.months, args.start, args.seed,
                                  load_params(args.params) if args.params else None)
    print(f"생성 완료: 고객 {len(customers):,}명, 거래 {len(txns):,}건 ({time.time() - t0:.0f}초)")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    customers.to_csv(out / "customers.csv", index=False)
    if args.format == "parquet":
        txns.to_parquet(out / "transactions.parquet", index=False)
    else:
        txns.to_csv(out / "transactions.csv", index=False)
    print(f"저장: {out}/customers.csv, {out}/transactions.{args.format}")

    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    checks = write_report(args.report, p, customers, txns, args.months,
                          {"start": args.start, "seed": args.seed})
    for c in checks:
        print(f"  {'PASS' if c.passed else 'FAIL'}  {c.name}: {c.detail}")
    print(f"검증 리포트: {args.report}")
    return 0 if all(c.passed for c in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
