# data_pipeline 기술 문서 (현재 상태)

> 기준: 2026-11-25, main `70a4c8c` 시점 코드 + riding_db_dev 테이블 목록.
> 로드맵·우선순위는 여기 쓰지 않고 **통합 기획서(lifemetrics 저장소)** 에서 관리한다.
> 이 문서는 "지금 무엇이 어떻게 돌고 있나"만 적는다 — 추후 에이전트가 코드 기준으로 자동 갱신할 대상.

## 1. 역할

LifeMetrics 서비스의 **데이터 수집·가공 계층**. 서버(`/mnt/200gb/project/data_pipeline`)에서
cron·watcher·FastAPI 로 돌며, 결과를 MariaDB(`riding_db` 중심)에 쌓는다.
화면·API·AI 대화는 lifemetrics(Spring Boot, `localhost:8080`)가 맡는다.

```
[Garmin/Strava 파일, NAS 이미지, 구글 시트, 공공 API, 블로그 …]
        │  data_pipeline (이 저장소)
        ▼
   MariaDB: riding_db / journal_db / lotto_db
        │
        ▼
   lifemetrics (Spring Boot) ──▶ 앱/웹 화면, AI 분석·대화
```

## 2. 모듈별 현황

### 2.1 riding_data_parser — 라이딩 활동
| 구분 | 파일 | 하는 일 |
|---|---|---|
| 수집 | `main/main_reader.py` (`main/run.sh`) | NAS의 FIT/GPX/TCX 파싱 → `activity_core`, `activity_point`, `gear_usage` 저장 |
| 수집 API | `main/parser_api.py` (FastAPI) | `/parse` 업로드 → 저장 + 세그먼트 매칭 |
| AI 분석 | `main/run_analysis.sh` | Spring Boot API 호출로 미분석 활동 AI 분석 (`ai_analysis`) |
| 분석 | `analysis/*` | 난이도·고도·지형(`activity_terrain`), 세그먼트(`segment_effort`) |
| 지도/DEM | `mapUtil/*`, `dem/build_sigungu_master.py` | 한국 DEM 타일, 시군구 경계(`sigungu`, `region`), 활동-지역 매칭(`activity_region`) |
| 날씨 | `services/weather_service.py`, `weather/activity_weather_backfill.py` | `activity_weather`, `activity_weather_point` |
| 장비 | `services/bike_match.py`, `main/match_unassigned_bikes.py`, `main/recompute_bike_totals_batch.py` | 활동↔자전거 매칭, 누적 거리 |
| 퍼머넌트 | `permentsUtil/*`, `randonneurs/*` | 랜도너스 퍼머넌트 코스 크롤링·GPX (`permanent_courses`, `randonneuring_result`) |
| 기타 | `services/gpt_service.py`, `image_service.py`, `polyline_service.py`, `wind_power.py` | OpenAI 요약, 이미지, 폴리라인, 풍향 파워 |

### 2.2 health — 건강·운동·식단
| 구분 | 파일 | 하는 일 |
|---|---|---|
| 감시 | `watcher/main_watcher.py` | NAS `inbody/`, `fitdays/`, `health_check/` raw 폴더 감시 → OCR → DB |
| OCR 파이프라인 | `services/inbody_pipeline_service.py`, `fitdays_pipeline_service.py`, `health_exam_pipeline_service.py` | `user_body_record`, `health_checkup` |
| 분석 | `services/body_analysis_service.py`, `body_lifetime_analysis_service.py`, `llm/*` | LLM(OpenAI/Gemini/Claude 교체 가능) 체성분 분석 |
| 일일 분석 | `scripts/daily_analysis.py` | 운동+식단 통합 분석 (Claude) |
| 재분석 | `scripts/reanalyze_batch.py` | 인바디/FitDays/건강검진 LLM 재분석 |
| 운동 기록 | `scripts/sync_pt_sheet.py` | 구글 시트 PT 일지 → `exercise_session/log/set`, `exercise_item` |
| 운동 보강 | `scripts/fill_exercise_data.py`, `import_exercise(_v2).py` | 종목 메타 LLM 보강, CSV 임포트(초기 1회성) |
| 식단 | `scripts/load_food_nutrition_master.py`, `detect_food_nutrition_errors.py` | 식약처 영양DB 적재(`food_nutrition_master` 31만 행), 1개 기준 값 오류 보정 |
| 건강 체크 | `scripts/health_check_batch.py` | 혈압·심박·SpO2 CLI 입력 |

### 2.3 persona — 페르소나·블로그
- `crawl_blog.py`, `chunk_embed_utils.py`: 티스토리 글 → 청킹 → bge-m3 임베딩 → `journal_db` (RunPod GPU 에서 실행)
- `blog_archive.py`, `blog_to_journal.py`, `blog_books_to_journal.py`, `blog_photo_link.py`: 블로그 글을 휴먼 블로그(`journal_post`)로 이관
- `athlete_dataset/`: 애슐리트 페르소나 파인튜닝 데이터셋 생성 (README 참고)

### 2.4 기타
- `lotto_parser/`: 로또·연금복권 결과 수집 → `lotto_db`, 카카오 알림 (`run_lotto.sh`, Docker 지원)
- `youtube_data/`: 유튜브 좋아요 영상 메타데이터 수집
- `common/`: SQLAlchemy 공통(`database.py`, `base.py`, `mixins.py`)

## 3. 실행 환경
- Python: `/mnt/200gb/project/data_pipeline/venv`
- 환경변수: `/mnt/200gb/apps/.env` 와 저장소 `.env` 를 스크립트마다 다르게 읽음 (git 미추적)
- LLM 설정: `claude_config.py` (저장소 루트, **git 미추적**, `.gitignore`) — `client`, `CLAUDE_MODEL`, `CLAUDE_MODEL_FAST`, `extract_text`
- 로그: `/mnt/200gb/logs/<앱>`
- 데이터: `/mnt/200gb/NAS/riding/`, `/mnt/200gb/NAS/health/`
- cron 스케줄은 서버 crontab 에만 있음 (저장소에 기록 없음)

## 4. 주요 DB 테이블 (riding_db, dev 복사본 기준 행 수)
| 영역 | 테이블 |
|---|---|
| 라이딩 | `activity_core`(186), `activity_point`(약 159만), `activity_image`, `activity_terrain`, `activity_weather(_point)`, `activity_region`, `ai_analysis` |
| 세그먼트 | `segment`(4,744), `segment_effort`(9,720), `segment_point`, `segment_pr`/`segment_leaderboard`(뷰) |
| 장비 | `bike`, `bike_part`, `gear_usage`, `cassette_cogs`, `device_info`, `device_component` |
| 지역 | `sigungu`, `region`, `ddareungi_station` |
| 퍼머넌트 | `permanent_courses`, `permanent_courses_change_log`, `randonneuring_result` |
| 운동 | `exercise_session`(210), `exercise_log`, `exercise_set`, `exercise_item`(520), `exercise_category`, `muscle_group`, `exercise_muscle_map` |
| 체성분·건강 | `user_body_record`, `health_checkup`, `health_treatment(_visit)`, `body_goal`, `weight_loss_target` |
| 식단 | `food_log`, `food_daily_summary`, `food_nutrition_master`, `food_nutrition_correction` |
| 헬스커넥트 | `hc_daily_activity`, `hc_exercise_session`, `hc_sync_log` (lifemetrics 앱이 적재) |
| AI·개발 | `athlete_chat_session/message`, `note_chunk`, `dev_request`, `dev_conversation` |

## 5. 알려진 기술 부채 (현재 상태 기록용)
- DB 접근 방식이 섞여 있음: SQLAlchemy / PyMySQL 직접 / mysql.connector
- `.env` 로드 경로가 제각각 (`/mnt/200gb/apps/.env`, 저장소 `.env`, `find_dotenv()`), 절대경로 `sys.path.insert` 다수
- `lotto_parser/main/lotto_main.py` 의 하드코딩된 DB 접속 정보는 `.env`(`LOTTO_DB_USER/PASSWORD/NAME`)로 옮김(2026-11-25). 옛 비밀번호가 git 이력에 남아 있으므로 비밀번호 교체 필요
- `requirements.txt` 가 루트 + 3개 하위로 나뉘고, 실제 쓰는 패키지(watchdog, fastapi, playwright, bs4, mysql-connector, anthropic, geopandas 등)가 루트에 없음
- 남은 잔여 파일: `lotto_main.py.back`, `lotto_parser/services/test.py`, `riding_data_parser/main/To`(빈 파일), `DEVREQ_TEST.md`, `import_exercise.py`(v2 로 대체)
- 백업 테이블 잔존: `food_log_bak_20261101`, `food_daily_summary_bak_20261101`, `health_checkup_bak_20261024`
- `reanalyze_batch.py --type fitdays` 가 쓰는 `user_fitdays_record` 가 dev DB 에 없음 (운영 확인 필요)
- 테스트 코드 없음
