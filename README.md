# cardproject

카드 상품 설계 & 혜택 시뮬레이션 플랫폼 (진행 중)

- 회사용: 카드 상품의 혜택 구조에 따른 손익 시뮬레이션
- 고객용: 보유 카드의 혜택 누수 진단

실제 고객 데이터 대신 **설계서의 가정으로 만든 합성 고객·거래 데이터**를 사용합니다. 식별정보(이름, 카드번호 등)는 생성하지 않습니다.

## 진행 상황
| 단계 | 내용 | 상태 |
|---|---|---|
| 1 | 합성 데이터 설계서 | ✅ v1.0 확정 ([docs/data_assumptions.md](docs/data_assumptions.md)) |
| 2 | 합성 데이터 생성기 + 검증 리포트 | ✅ 구현 (`cardsim/`) |
| 3 | 공개 통계 보정 | 예정 |
| 4 | 카드 상품 손익 시뮬레이터 / 혜택 누수 진단 | 예정 |

## 합성 데이터 생성

```bash
pip install -r requirements.txt
python -m cardsim.generate --customers 10000 --months 12 --seed 42
```

- `data/synthetic/customers.csv`: 고객 1만 명
- `data/synthetic/transactions.parquet`: 거래 약 1,100만 건 (`--format csv`로 CSV 출력 가능)
- `reports/validation_report.html`: 설계값 대비 생성 결과와 무결성 검사 결과

같은 `--seed`면 항상 같은 데이터가 나옵니다. 1만 명·12개월 기준 약 1~2분 걸립니다.

## 구조
```
config/params.json      설계서의 모든 가정값 (생성기는 이 파일만 읽음)
cardsim/customers.py    고객 생성 (설계서 2장)
cardsim/transactions.py 거래 생성 (설계서 3장)
cardsim/expected.py     설정값으로 계산한 기대값 (설계서 3.4.1)
cardsim/validate.py     검증 항목과 HTML 리포트 (설계서 5장)
cardsim/generate.py     CLI
tests/                  pytest
```

## 테스트
```bash
python -m pytest -q
```

## 한계
수치는 아직 공개 통계로 보정하지 않은 가정값입니다. 시뮬레이션 결과는 상품 구조를 비교하는 상대 지표로만 해석해야 합니다. 자세한 내용은 설계서 6장을 참고하세요.
