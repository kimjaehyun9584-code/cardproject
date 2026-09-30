# 다음 작업 (인수인계 메모)

마지막 업데이트: 2026-09-30, 설계서 v1.4 기준

## 현재 상태
- 합성 데이터 생성기(`cardsim/`)와 검증 리포트 완성. `python -m cardsim.generate --seed 42`
- 공개 통계 보정 진행 중. 리포트의 공개 통계 비교에서 **남은 ⚠️ 두 개**:
  - 1인당 월 카드 사용액 +19.9% (허용 ±10%)
  - 건당 평균 결제금액(대중교통 제외) +12.9% (허용 ±10%)
- 사용액 초과는 대부분 건당 금액에서 나옴. 결제 건수·연령 구성·이용 강도·체크카드·온라인 비중은 모두 허용 범위 안.

## 바로 할 일: 업종별 건당 금액 보정
1. 환경 변수 `KOSIS_API_KEY`로 KOSIS OpenAPI 호출 (키 값은 절대 로그·파일·커밋에 남기지 않기)
   - 여신금융협회 「월간 국내카드승인실적」: `orgId=435`, `tblId=DT_435001N_001`
   - 먼저 통계표 메타(항목·분류)를 조회해 업종별 승인금액·승인건수가 있는지 확인
   - 엔드포인트 예: `https://kosis.kr/openapi/Param/statisticsParameterData.do?method=getList&apiKey=...&format=json&jsonVD=Y&orgId=435&tblId=DT_435001N_001&prdSe=M&...`
2. 받은 원자료는 `data/reference/raw/`에, 비교에 쓰는 값은 출처와 함께 `data/reference/benchmarks.csv`에 추가
3. 업종별 건당 금액을 생성 데이터와 비교해 **높은 업종만** `config/params.json`의 `ticket` 중위값 조정
   - 사용자와 함께 확정한 가격은 유지: 카페(3.2.2), 음식점 간편식(3.2.3), 대학생 편의점 계수(0.45), 대중교통 1,500원 하한
4. 설계서(`docs/data_assumptions.md`) 4장·3.2·변경 이력 갱신, 리포트 재생성, 테스트 후 커밋·푸시

## 네트워크 메모
- 허용됨: www.bok.or.kr, file-cdn.bok.or.kr(`curl --http1.1` 필요), ecos.bok.or.kr, kosis.kr(OpenAPI만), www.data.go.kr, jumin.mois.go.kr
- 안 됨: www.crefia.or.kr(사이트가 연결을 끊음), KOSIS 통계표 화면(sso.kosis.kr 로그인 경유)

## 작업 방식 (사용자 선호)
- 숫자 가정은 사용자가 직접 검토·확정. 큰 변경은 선택지와 추천을 제시하고 확인받은 뒤 반영
- 수치는 기억이 아니라 출처가 있는 자료로만
