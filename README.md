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
| 3 | 공개 통계 보정 | ✅ v1.11 완료 ([설계서 4장](docs/data_assumptions.md), [다음 작업 메모](docs/next_steps.md)) |
| 4 | 카드 상품 손익 시뮬레이터 / 혜택 누수 진단 | 설계 중 ([손익 시뮬레이터 설계서](docs/simulator_design.md)) |

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
cardsim/products.py     카드 상품 정의 로드·검증 (손익 시뮬레이터 설계서 2.2)
cardsim/simulate.py     손익 시뮬레이터·민감도 분석·리포트 (손익 시뮬레이터 설계서)
config/products/        실제 카드 20종 상품 정의 (신용 8, 체크 12)
cardsim/advisor.py      웹앱 계산부: 고객 프로필 → 카드별 순혜택·혜택 누수·공헌이익
app.py                  웹앱 (Streamlit)
scripts/fetch_banksalad.py  뱅크샐러드 상품 조건 원자료 받기
config/merchant_shares.json  업종 내 가맹점 비중 (출처·추정·가정 구분)
config/pnl_assumptions.json 손익 요율 (출처·추정·가정 구분)
config/behavior.json    주력카드 비율·실적 맞추기 등 행동 가정
tests/                  pytest
```

## 손익 시뮬레이션

```bash
python -m cardsim.simulate --customers 10000 --seed 42
```

실제 카드 20종을 합성 고객에게 적용해 회원 1인 월 공헌이익을 계산하고, 가정값마다 범위 양 끝으로 바꾼 민감도 분석과 함께 `reports/simulation_report.html`을 만듭니다 (약 16분, 대부분 민감도 분석). 상품 구조를 비교하는 상대 지표이며 실제 손익 예측이 아닙니다.

## 테스트
```bash
python -m pytest -q
```

## 한계
수치는 공개 통계(여신금융협회 카드승인실적, 행정안전부 주민등록인구, 한국은행 지급수단 이용행태 조사, 통계청 가계동향조사)의 전체 평균과 연령별 이용 강도에 맞춘 가정값입니다. 업종별 건당 금액과 세그먼트별 사용액은 직접 비교할 통계를 확보하지 못했습니다. 시뮬레이션 결과는 상품 구조를 비교하는 상대 지표로만 해석해야 합니다. 자세한 내용은 설계서 6장을 참고하세요.

## 웹앱: 고객 프로필로 카드 추천 + 카드사 손익

```bash
pip install -r requirements.txt
streamlit run app.py
```

연령대와 월 평균 사용액을 넣으면, 비슷한 합성 고객의 1년 결제로 카드 20종을 계산해 보여줍니다.
- **고객 입장**: 연회비를 뺀 연 순혜택 순위, 혜택 누수(전월실적 미달·한도/횟수 때문에 못 받은 혜택)
- **카드사 입장**: 그 고객의 회원 1인 월 공헌이익, 누수가 없었다면의 공헌이익
- 선택 입력: 아파트 관리비 카드 납부, 통신사, 후불교통, 카페·배달·온라인쇼핑 결제 횟수
- 카드별 계산 반영도(✅/⚠️/❌)와 약관 확인일·프로모션 종료일을 표시하고, 주력 혜택이 빠진 ❌ 카드는 기본으로 순위에서 뺍니다
- 첫 실행 때 합성 데이터를 `data/cache/`에 저장합니다 (약 17초·메모리 약 1.4GB, 이후 로딩 0.5초·약 0.7GB)
- 카드 한 장으로 모든 결제를 한다고 본 값입니다. 자세한 가정은 `docs/simulator_design.md` 8장
