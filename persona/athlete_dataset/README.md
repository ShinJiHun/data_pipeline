# 애슐리트 페르소나 파인튜닝 데이터셋 (A안)

컨텍스트(내 기록)와 시스템 프롬프트는 **LifeMetrics 백엔드가 운영과 똑같이** 만들고,
이 폴더는 질문 목록 관리 → 백엔드 호출 → JSONL 적재 → 학습용 변환만 맡는다.

```
questions_public.txt / questions_admin.txt
        │  (날짜 × 질문 × 모드)
        ▼
generate_dataset.py generate ──POST──▶ LifeMetrics /api/athlete/dataset/sample
        │                                   (컨텍스트 생성 + Claude 답변)
        ▼
data/athlete_dataset/raw_<mode>.jsonl   (원본, 사람이 검수·수정)
        │
generate_dataset.py export
        ▼
data/athlete_dataset/train.jsonl / val.jsonl   (messages 형식 → RunPod QLoRA)
```

`data/` 는 .gitignore 대상이라 결과물(개인 기록 포함)은 커밋되지 않는다.

## 두 가지 모드

| 모드 | 화자 | 컨텍스트 | 톤 |
|---|---|---|---|
| `public` | 방문자 → 개인 비서가 "신지훈"을 소개 | **공개용만** (대회·완주 기록, 운동 스타일, 목표) | 3인칭, 정중, 살짝 자랑 |
| `admin` | 본인 → 내 기록 분석 | 전체 기록 (기존 AthleteContextService) | 2인칭, 분석·직설 |

시스템 프롬프트 초안은 `prompts.md`. 최종본은 백엔드에 둔다(운영 챗과 학습 데이터가 같은 프롬프트를 쓰도록).

## 백엔드에 필요한 API (LifeMetrics 쪽 작업)

```
POST /api/athlete/dataset/sample
Authorization: Bearer <ATHLETE_DATASET_TOKEN>
Content-Type: application/json

{ "mode": "public" | "admin", "date": "2026-11-01", "question": "..." }
```

응답 200:
```json
{
  "mode": "admin",
  "date": "2026-11-01",
  "system": "<그 날짜 기준 컨텍스트가 들어간 시스템 프롬프트 전체>",
  "answer": "<Claude 답변>",
  "model": "claude-..."
}
```

요구사항
- `date` 기준으로 컨텍스트를 만든다(`AthleteContextService.build(userId, date)` — 그 날짜 이후 기록은 넣지 않는다).
- `public` 모드는 **진료·처방·치료, 체중·체지방 등 체성분 수치, 식단 상세, 신체 치수·피팅** 을 컨텍스트 단계에서 제외한다.
  (모델에게 "말하지 마"라고 가르치는 것으로는 부족하다. 넣지 않는 것이 원칙.)
- 인증은 관리자 쿠키 대신 Bearer 토큰(health-sync, dev-requests/worker 와 같은 방식). `AdminWriteFilter.PUBLIC_WRITE_PATHS`
  에 경로를 추가하고 컨트롤러에서 토큰을 검사한다. 토큰이 설정되지 않았으면 404/403.
- Claude 호출 비용이 드는 엔드포인트라 외부에 열지 않는다(토큰 필수, 필요하면 레이트 제한).
- 기록을 DB에 남길 필요는 없다(저장은 이 스크립트가 JSONL 로 한다).

## 사용법

환경변수(.env):
```
LIFEMETRICS_API_BASE=http://localhost:8080      # 백엔드 주소
ATHLETE_DATASET_TOKEN=...                        # 백엔드와 같은 값
```

```bash
# 계획만 확인(호출 안 함)
python3 persona/athlete_dataset/generate_dataset.py generate --mode admin --start 2026-09-01 --end 2026-11-12 --dates 8 --dry-run

# 생성 (이미 만든 날짜×질문 조합은 건너뛴다 → 중단 후 재실행 가능)
python3 persona/athlete_dataset/generate_dataset.py generate --mode admin --start 2026-09-01 --end 2026-11-12 --dates 8
python3 persona/athlete_dataset/generate_dataset.py generate --mode public --start 2026-11-12 --end 2026-11-12 --dates 1

# 검수 후 학습용 변환 (raw 에서 "skip": true 로 표시한 줄은 제외, "answer" 를 직접 고쳐도 됨)
python3 persona/athlete_dataset/generate_dataset.py export --val-ratio 0.1
```

## 데이터 양 가이드
- 모드별 500~1,000쌍이면 첫 QLoRA 시작 가능. admin 은 날짜를 여러 개(질문 수 × 날짜 수), public 은 공개 정보가
  자주 바뀌지 않으니 날짜 1~2개 + 질문 다양화가 효율적이다.
- Claude 답변 중 마음에 안 드는 건 직접 고치면 그게 "내 말투" 데이터가 된다. 양보다 품질.
