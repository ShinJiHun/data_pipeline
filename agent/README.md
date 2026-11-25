# agent — LangGraph 에이전트

data_pipeline 을 보고 문서·분석을 돕는 에이전트 모음. 로드맵 D 그룹(`docs/plan/roadmap_draft.md`).

| 그래프 | 실행 | 하는 일 |
|---|---|---|
| D1 `graphs/doc_update.py` | `python -m agent.run_doc_update` | git·DB 근거로 `docs/data_pipeline.md` 수정안 작성 → 사람 승인 후 반영 |

```
agent/
├─ config.py          .env 읽기 (모델, DB URL, 출력 경로)
├─ tools/
│  ├─ git_tools.py    git 읽기 전용 (log, ls-files)
│  └─ db_tools.py     DB 읽기 전용 (SELECT 만, 세션 READ ONLY)
├─ graphs/
│  └─ doc_update.py   D1 그래프
└─ run_doc_update.py  D1 실행기 (CLI, 승인 프롬프트)
```

## 설치 (서버, 1회)

기존 `venv` 의 openai/httpx 고정 버전과 충돌하지 않게 **별도 venv** 를 쓴다.

```bash
cd /mnt/200gb/project/data_pipeline
python3 -m venv venv-agent
venv-agent/bin/pip install -r agent/requirements.txt
```

## 환경변수 (`.env`)

```
OPENAI_API_KEY=...
AGENT_MODEL=<OpenAI 모델 이름>
AGENT_DB_URL=mysql+pymysql://agent_ro:<비밀번호>@127.0.0.1:3306/riding_db   # 선택
AGENT_OUTPUT_DIR=/mnt/200gb/logs/agent                                    # 선택(기본값)
```

`AGENT_DB_URL` 은 **SELECT 만 가진 전용 계정**으로 만든다:

```sql
CREATE USER 'agent_ro'@'%' IDENTIFIED BY '...';
GRANT SELECT ON riding_db.* TO 'agent_ro'@'%';
```

## 실행

```bash
# 제안만 만들기 — 저장소는 안 건드리고 AGENT_OUTPUT_DIR/doc_update/ 에 .md/.diff/.summary.txt 저장
venv-agent/bin/python -m agent.run_doc_update --days 7

# diff 보고 y 면 문서 파일 덮어쓰기 (commit/push 는 직접)
venv-agent/bin/python -m agent.run_doc_update --apply --repo /path/to/작업용/clone
```

- `--apply` 는 운영 실행용 체크아웃(`/mnt/200gb/project/data_pipeline`)에 쓰지 말 것. 수정된 파일이 다음 [운영 반영] pull 과 충돌한다.
- cron 은 제안 모드로만 (예: 매주 월 07:00). 비용·품질 보고 주기 조정.

## 다음 확장 아이디어
- 제안을 dev_request 로 등록해 앱 채팅에서 승인 → devreq 워커가 커밋
- D2 기획서 진행 체크, D3 주간 분석 리포트 서브그래프
