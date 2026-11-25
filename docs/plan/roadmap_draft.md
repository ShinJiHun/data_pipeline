# 로드맵 초안 (data_pipeline 관점) — 통합 기획서에 합칠 용도

> 작성: 2026-11-25. 이 파일은 **임시 초안**이다. 맥에서 lifemetrics 통합 기획서의
> "앞으로 할 일" 섹션에 옮긴 뒤 여기서는 지워도 된다.
> 항목마다 [pipeline] / [app] 으로 어느 저장소 작업인지 표시했다. 우선순위(P1~P3)는 제안값이니 조정할 것.

## A. 진행 중인 것 마무리

| # | 항목 | 작업 | 제안 |
|---|---|---|---|
| A1 | 식단 AI 일일 분석 (`food_daily`) | [pipeline] `daily_analysis.py` 를 cron 으로 정례화, 식단 기록이 쌓인 뒤 결과 품질 점검 / [app] 결과 화면 | P1 |
| A2 | 애슐리트 파인튜닝 데이터셋 | [app] 답변 max_tokens 잘림 수정 → [pipeline] `generate_dataset.py` 재생성·검수 → RunPod QLoRA | P2 |
| A3 | 식약처 영양DB 품질 | [pipeline] `detect_food_nutrition_errors.py` 검토 목록(CSV) 수동 확인, manual 보정 누적 | P3 |

## B. 데이터 수집 보강

| # | 항목 | 작업 | 제안 |
|---|---|---|---|
| B1 | 실내 라이딩(로라) 수집 | 현재 `main_reader` 는 indoor 활동을 스킵함. 겨울 로라 일정이 시작되니 GPS 없는 활동도 저장(파워·심박·시간)하고 지도·세그먼트 단계만 건너뛰기 | P1 |
| B2 | 헬스커넥트 ↔ 운동 기록 통합 | `hc_exercise_session` 과 PT/헬스장 `exercise_session` 을 날짜 기준으로 묶어 하루 운동량 일원화 | P2 |
| B3 | Strava 동기화 | `strava_sync_checkpoint` 테이블만 있고 비어 있음 — 자동 동기화 구현 여부 결정 | P3 |

## C. 분석·인사이트

| # | 항목 | 작업 | 제안 |
|---|---|---|---|
| C1 | 감량 대시보드 지표 | 체성분 + 섭취 kcal + 운동 소모 kcal 를 일/주 단위로 합친 요약 테이블(배치) → [app] 그래프 | P1 |
| C2 | 주간 리포트 | 라이딩·운동·식단·체성분 주간 요약을 LLM 으로 생성해 저장/알림 | P2 |
| C3 | 장비 정비 알림 | 자전거 부품(`bike_part`) 누적 거리 기준 교체 알림 (체인 측정 `chain_measurement` 활용) | P3 |

## D. 에이전트 (LangGraph)

| # | 항목 | 작업 | 제안 |
|---|---|---|---|
| D1 | 기술 문서 자동 갱신 | `agent/` 폴더 + 별도 venv. 노드: 수집(git log·diff, DB 요약) → 분석 → `docs/data_pipeline.md` 수정안 → 사람 승인 후 커밋 | P2 |
| D2 | 기획서 진행 체크 | 통합 기획서 항목과 최근 커밋을 대조해 "진행됨/정체" 제안 (수정은 사람이) | P3 |
| D3 | 데이터 분석 에이전트 | C2 주간 리포트를 에이전트 서브그래프로 이전, 필요 시 추가 조회 반복 | P3 |

원칙: DB 는 SELECT 전용 계정, 프롬프트에 비밀값·원문 개인정보 금지, 처음엔 주 1회/수동 실행.

## E. 정리·기반 작업

| # | 항목 | 작업 | 제안 |
|---|---|---|---|
| E1 | 하드코딩된 DB 접속 정보 제거 | `lotto_parser/main/lotto_main.py` → `.env` 로 이동, 비밀번호 교체 | P1 |
| E2 | cron 스케줄 문서화 | 서버 crontab 을 `docs/` 에 기록 (비밀값 제외) | P2 |
| E3 | requirements 정리 | 실제 쓰는 패키지를 루트 `requirements.txt` 로 통합, venv 와 맞추기 | P2 |
| E4 | 환경변수·DB 접근 통일 | `.env` 경로 하나로, DB 접근은 `common/database.py` 로 모으기 (점진적) | P3 |
| E5 | 잔여 파일·백업 테이블 정리 | `*.back`, `test.py`, 빈 파일 `To`, `DEVREQ_TEST.md`, `*_bak_*` 테이블 (백업 테이블은 확인 후 수동 삭제) | P3 |
