# 다음 작업 (인수인계 메모)

마지막 업데이트: 2026-10-02, 설계서 v1.7 기준

## 현재 상태
- 합성 데이터 생성기(`cardsim/`)와 검증 리포트 완성. `python -m cardsim.generate --seed 42`
- v1.6에서 교육·여행 건당 금액 중위값 30만 → 20만 원 반영 (설계서 4.5). 건당 금액은 +8.7%로 통과
- 리포트의 공개 통계 비교에서 **남은 ⚠️ 두 개**:
  - 1인당 월 카드 사용액 +15.6% (허용 ±10%). 건수 +5.6%, 건당 금액 +8.7%가 같은 방향이라 곱하면 초과
  - 체크카드 결제 비중 +3.0%p (허용 ±3%p). 교육·여행(신용카드 비중 높음)을 낮춘 부작용으로 경계를 근소하게 넘음
- KOSIS 여신금융협회 통계표(`DT_435001N_001`)는 승인금액만 있고 건수가 없어, 업종 대분류 금액 구성비만 리포트에 참고로 표시 (설계서 4.4)

## 바로 할 일 (사용자 확인 필요)
1. 월 사용액 초과 해소: v1.7에서 가계동향조사로 확인한 결과 `UTILITY_INS`(1인당 23.2만 원)와 `TELECOM`(7.9만 원)이 전액 카드 결제를 가정한 상한(16.4만·5.4만 원)보다도 높음. 설계서 4.6 시나리오 중 선택 (예: `UTILITY_INS` 횟수 ×0.5 + `TELECOM` 중위값 4.5만 → 월 사용액 +8.7%)
2. 체크카드 비중: 2.4 `check_card_ratio`를 소폭 낮출지
   - 사용자와 함께 확정한 값은 유지: 카페(3.2.2), 음식점 간편식(3.2.3), 대학생 편의점 계수(0.45), 대중교통 1,500원 하한, 대학생 월 사용액 70~80만 원(현재 약 72만)
3. 결정 후 `config/params.json` 반영, 설계서 갱신, 리포트 재생성, 테스트 후 커밋·푸시

## 네트워크·API 메모
- 허용됨: www.bok.or.kr, file-cdn.bok.or.kr(`curl --http1.1` 필요), ecos.bok.or.kr, kosis.kr(OpenAPI만), www.data.go.kr, jumin.mois.go.kr
- 안 됨: www.crefia.or.kr(사이트가 연결을 끊음), KOSIS 통계표 화면(sso.kosis.kr 로그인 경유)
- KOSIS OpenAPI는 환경 변수 `KOSIS_API_KEY` 사용 (키 값은 로그·파일·커밋에 남기지 않기). 가끔 연결이 끊겨서 `curl --retry 5 --retry-all-errors` 권장
  - 메타 조회: `statisticsData.do?method=getMeta&type=ITM&...&orgId=435&tblId=DT_435001N_001`
  - 자료 조회: `Param/statisticsParameterData.do?method=getList&...&itmId=ALL&objL1=ALL&prdSe=M&startPrdDe=202501&endPrdDe=202512`

## 작업 방식 (사용자 선호)
- 숫자 가정은 사용자가 직접 검토·확정. 큰 변경은 선택지와 추천을 제시하고 확인받은 뒤 반영
- 수치는 기억이 아니라 출처가 있는 자료로만
