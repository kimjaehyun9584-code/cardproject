# 다음 작업 (인수인계 메모)

마지막 업데이트: 2026-11-13, 데이터 설계서 v1.11 · 시뮬레이터 설계서 v0.15 기준

## 현재 상태
- 합성 데이터 생성기(`cardsim/`)와 검증 리포트 완성. `python -m cardsim.generate --seed 42`
- **공개 통계 보정 완료** (설계서 4.5~4.10). 시드 42~45 모두 공개 통계 비교 전 항목과 검증 항목이 허용 범위 안
  - v1.6: 교육·여행 건당 금액 중위값 30만 → 20만 원
  - v1.8: `UTILITY_INS` 월 결제 횟수 ×0.5, `TELECOM` 중위값 6.5만 → 4.5만 원
  - v1.9: 체크카드 사용액 비중 S1 제외 ×0.95
  - v1.10: 20대 소액 결제 증가 (S1 카페 18 → 24회, S2 편의점 12 → 16회)
  - v1.11: S4 양육가구 연령 배분 30대 30%·40대 70%
  - 시드 42: 월 사용액 +8.9%, 결제 건수 +5.0%, 건당 금액 +2.9%, 체크카드 +2.5%p
- 스크립트: `scripts/fetch_banksalad.py CARD000004 ...` → 뱅크샐러드 상품 조건 원자료 저장
- 확인하지 못한 것: 업종별 건당 금액(카드승인실적에 건수 없음), 금액 기준 온라인 비중, 세그먼트별 사용액(연령·소득별 카드 사용액 통계 미확보)

## 바로 할 일: 손익 시뮬레이터 (`docs/simulator_design.md` v0.15)
- 범위 확정: 신용·체크카드 상품, 공헌이익 수준 손익, 주력카드 비율 + 실적 맞추기, 세그먼트별 가입률
- 상품 확정 (2026-10-28): 실제 카드 5종을 `config/products/*.json`으로 정의, `cardsim/products.py`로 검증
1. ✅ 가맹점 비중 16개 + 휘발유 가격 (설계서 2.5, v0.6): 출처 2·추정 13·가정 1(후불교통 선택률). 근거를 끝까지 못 찾는 값만 가정으로 둠 (사용자 확정)
2. ✅ 손익 요율 12개 수집 (설계서 2.3·5장, v0.7): 추정 8·가정 4. `gongsi.crefia.or.kr`·`www.cardsales.or.kr` 허용 시 체크카드 수수료율·대손율 재확인 가능
3. ✅ 행동 가정 (설계서 2.4): 주력카드 비율은 한국은행 조사로 추정, 실적 맞추기·체크카드 비율·가입률은 가정
4. ✅ 엔진·민감도·리포트 구현: `python -m cardsim.simulate --customers 10000 --seed 42` (약 4분) → `reports/simulation_report.html`, 첫 결과는 설계서 7장
5. ✅ 결론을 흔든 값 재확인 (v0.9): 체크카드 수수료율·신판 대손율을 추정으로 교체. 신용카드끼리 순위는 견고, 노리2(체크) vs Mr.Life(신용)는 판단 불가 수준으로 근접
6. ✅ 모집 비용 안분 기간 재검토 (v0.10): 해지율 연 11.6% → 상한 103개월, 기본값 60개월(가정). Mr.Life(신용) +3,141원 vs 노리2(체크) +2,831원으로 근접, 순위 판단 불가
7. ✅ 비교 상품 확대 (v0.11): 신용 4종 추가(My WE:SH, LOCA 365, LOCA LIKIT 1.2, 신한 처음) → 9종. 엔진에 월 단위 순서 계산(할인 건 실적 제외), 대중교통 월 합산 1건, 일 한도, 온·오프라인 조건, 제외 조건, 일시불 조건 추가
   - 체크카드 인기 상위는 뱅크샐러드에 없음 → `www.card-gorilla.com` 허용 시 추가 가능. 마일리지 카드는 마일 가치 자료 필요
8. ✅ 인기 체크카드 4종 추가 (v0.12): 카드고릴라 2025 체크 TOP 10 중 Deep Dream 체크, 네이버페이 머니 하나, SOL트래블 체크, 카카오페이 신한 체크 → 13종
   - MG 더나은: 새마을금고 사이트 프록시 403. 카드고릴라 상세 API(api.card-gorilla.com:8080)는 연결 끊김
   - SOL트래블(해외 수수료 면제)·카카오페이 신한(카카오페이 2%)은 주력 혜택 미반영이라 결과 해석 주의
9. ✅ 고객·카드사 겸용 웹앱 (설계서 8장): `streamlit run app.py`
10. ✅ 케이뱅크 ONE(3종 선택지별)·토스뱅크(오프라인 캐시백) 추가 (v0.13) → 17종. 엔진에 `every_nth`·`amount_by_txn`·`units`(이용일 수) 추가
   - 둘 다 기간 한정 프로모션(케이뱅크 2026.12.31, 토스 2027.1.20까지) → 시즌 바뀌면 상품 파일 갱신 필요
   - ✅ (v0.14) 토스 온라인·어디서나·기부 캐시백 추가 → 20종. 온라인 중 간편결제·웹툰·게임·여행 영역은 데이터 없음으로 미반영
11. ✅ 웹앱 공개 전 보완 (v0.15, 설계서 8장): 계산 반영도·❌ 카드 순위 제외, 선택 입력 4종, 약관 날짜 경고, 디스크 캐시, 안내 문구
12. 다음 후보 (사용자 확인 필요): 배포(Streamlit Community Cloud 등, 저장소 공개 범위 결정), 카드 2장 조합 추천, 마일리지·프리미엄 카드
   - 프로모션 만료 시 갱신: 케이뱅크 ONE 2026-12-31, 토스뱅크 2027-01-20 → 상품 파일의 혜택·`terms_checked`·`promo_end` 수정
- PR: https://github.com/kimjaehyun9584-code/cardproject/pull/1 (이 브랜치에 푸시하면 갱신됨)
- 합성 데이터에서 사용자와 함께 확정한 값은 유지: S1 대학생 신용카드 보유 확률 8%, 카페 가격대(3.2.2), 음식점 간편식(3.2.3), 대학생 편의점 횟수 8회·계수 0.45, 대중교통 1,500원 하한, 대학생 월 사용액 70~80만 원(현재 약 73만)

## 네트워크·API 메모
- KOSIS에서 쓴 표: 카드승인실적(435/DT_435001N_001), 가계동향(101/DT_1L9U105), 소매업태별 판매액(101/DT_1K41003), 주유소 판매가격(318/TX_31802_A000)
- 허용됨: www.bok.or.kr, file-cdn.bok.or.kr(`curl --http1.1` 필요), ecos.bok.or.kr, kosis.kr(OpenAPI만), www.data.go.kr, jumin.mois.go.kr
- 신한카드 IR 자료: `POST /mob/MOBFM12003N/MOBFM12003R05.ajax` {"page":1}로 목록, `/mob/MOBFM12003N/RetrieveFile.ajax?ancGdSeqN=..&hmpBlbBsnCCd=HPC&factBookGubun=N`으로 PDF (`curl --http1.1` 권장)
- 신한카드 상품공시실(`/pconts/html/helpdesk/guide/CONFM90022/CONFM90022R02.html`)에 할부 금리 분포·가맹점 수수료율 구간·조달금리 공시가 있음
- 허용됨 (2026-11-04 추가): www.kbanknow.com (상세는 POST /ib20/act/CRDCRDONE0000100A 응답, 브라우저로 받음), www.tossbank.com (/card/benefits, 간헐적으로 upstream 실패 → 재시도. 탭 전환에는 asset-fe.tossbank.com 필요, 2026-11-13 허용), www.card-gorilla.com (기사·순위 페이지는 정적 HTML이 비어 있어 v.daum.net 기사로 확인)
- 허용됨 (2026-10-02 추가): card.kbcard.com, www.shinhancard.com, www.samsungcard.com, www.hyundaicard.com, www.bccard.com, www.banksalad.com, v.daum.net
  - 뱅크샐러드 상품 페이지(`/product/cards/CARD000004` 형식)의 `__NEXT_DATA__`에 상품 조건이 구조화되어 있음. 목록 페이지는 없어 검색으로 상품 ID를 찾음
- 안 됨: www.crefia.or.kr·gongsi.crefia.or.kr·www.cardsales.or.kr(허용 도메인이지만 상대 서버가 연결을 끊음), file.alphasquare.co.kr·static12.samsungcard.com(정책 403), KOSIS 통계표 화면(sso.kosis.kr 로그인 경유)
- KOSIS OpenAPI는 환경 변수 `KOSIS_API_KEY` 사용 (키 값은 로그·파일·커밋에 남기지 않기). 가끔 연결이 끊겨서 `curl --retry 5 --retry-all-errors` 권장
  - 메타 조회: `statisticsData.do?method=getMeta&type=ITM&...&orgId=435&tblId=DT_435001N_001`
  - 자료 조회: `Param/statisticsParameterData.do?method=getList&...&itmId=ALL&objL1=ALL&prdSe=M&startPrdDe=202501&endPrdDe=202512`

## 작업 방식 (사용자 선호)
- 숫자 가정은 사용자가 직접 검토·확정. 큰 변경은 선택지와 추천을 제시하고 확인받은 뒤 반영
- 수치는 기억이 아니라 출처가 있는 자료로만
