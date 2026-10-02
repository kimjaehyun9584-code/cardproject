# 다음 작업 (인수인계 메모)

마지막 업데이트: 2026-10-02, 설계서 v1.9 기준

## 현재 상태
- 합성 데이터 생성기(`cardsim/`)와 검증 리포트 완성. `python -m cardsim.generate --seed 42`
- 공개 통계 보정 결과 (설계서 4.5·4.7·4.8):
  - v1.6: 교육·여행 건당 금액 중위값 30만 → 20만 원
  - v1.8: `UTILITY_INS` 월 결제 횟수 ×0.5, `TELECOM` 중위값 6.5만 → 4.5만 원
  - v1.9: 체크카드 사용액 비중 S1 제외 ×0.95
  - 월 사용액 +8.6%, 결제 건수 +3.4%, 건당 금액 +4.3%, 체크카드 비중 +2.38%p로 모두 허용 범위 안 (시드 42)
- 기본 시드 리포트의 **남은 ⚠️ 하나**: 20대 이용 강도 −0.102 (허용 ±0.10). 시드 42~45에서 −0.03~−0.10 사이로 움직이는 표본 편차지만, 항상 목표보다 낮음

## 바로 할 일 (사용자 확인 필요)
1. 20대 이용 강도: 표본 편차로 두고 넘어갈지, 20대 세그먼트(S1·S2) 결제 횟수를 소폭 올려 중심을 맞출지
   - 사용자와 함께 확정한 값은 유지: S1 대학생 신용카드 보유 확률 8%, 카페(3.2.2), 음식점 간편식(3.2.3), 대학생 편의점 계수(0.45), 대중교통 1,500원 하한, 대학생 월 사용액 70~80만 원(현재 약 71만)
2. 그다음 단계: README 4단계(카드 상품 손익 시뮬레이터 / 혜택 누수 진단)

## 네트워크·API 메모
- 허용됨: www.bok.or.kr, file-cdn.bok.or.kr(`curl --http1.1` 필요), ecos.bok.or.kr, kosis.kr(OpenAPI만), www.data.go.kr, jumin.mois.go.kr
- 안 됨: www.crefia.or.kr(사이트가 연결을 끊음), KOSIS 통계표 화면(sso.kosis.kr 로그인 경유)
- KOSIS OpenAPI는 환경 변수 `KOSIS_API_KEY` 사용 (키 값은 로그·파일·커밋에 남기지 않기). 가끔 연결이 끊겨서 `curl --retry 5 --retry-all-errors` 권장
  - 메타 조회: `statisticsData.do?method=getMeta&type=ITM&...&orgId=435&tblId=DT_435001N_001`
  - 자료 조회: `Param/statisticsParameterData.do?method=getList&...&itmId=ALL&objL1=ALL&prdSe=M&startPrdDe=202501&endPrdDe=202512`

## 작업 방식 (사용자 선호)
- 숫자 가정은 사용자가 직접 검토·확정. 큰 변경은 선택지와 추천을 제시하고 확인받은 뒤 반영
- 수치는 기억이 아니라 출처가 있는 자료로만
