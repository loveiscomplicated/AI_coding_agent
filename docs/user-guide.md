# User Guide — AI Coding Agent

> 이 문서는 시스템 전체의 설치, 설정, 사용 방법을 설명한다.

---

## 목차

1. [시스템 개요](#1-시스템-개요)
2. [사전 요구사항](#2-사전-요구사항)
3. [초기 설치](#3-초기-설치)
4. [환경 변수 설정](#4-환경-변수-설정)
5. [서버 실행](#5-서버-실행)
6. [회의 인터페이스 사용법](#6-회의-인터페이스-사용법)
7. [파이프라인 실행 (CLI)](#7-파이프라인-실행-cli)
8. [파이프라인 실행 (UI)](#8-파이프라인-실행-ui)
9. [tasks.yaml 작성법](#9-tasksyaml-작성법)
10. [대시보드](#10-대시보드)
11. [Discord 핫라인](#11-discord-핫라인)
12. [API 레퍼런스](#12-api-레퍼런스)
13. [트러블슈팅](#13-트러블슈팅)

---

## 1. 시스템 개요

```
사람 (회의 UI 또는 CLI)
     ↕
백엔드 (FastAPI)
     ↕
파이프라인 오케스트레이터
     ├── TestWriter (Haiku) → 테스트 작성
     ├── Implementer (Sonnet) → 구현
     ├── Docker 테스트 러너 → pytest 격리 실행
     ├── Reviewer (Haiku) → 코드 리뷰
     └── GitWorkflow → 브랜치 · 커밋 · PR 생성
```

**핵심 흐름**: `tasks.yaml` 작성 → 파이프라인 실행 → 에이전트가 TDD로 코드 작성 → PR 생성 → 사람이 검토 후 머지.

---

## 2. 사전 요구사항

| 도구 | 최소 버전 | 확인 명령 |
|------|-----------|-----------|
| Python | 3.12+ | `python --version` |
| Node.js | 18+ | `node --version` |
| Docker | 24+ | `docker --version` |
| Git | 2.38+ | `git --version` |
| GitHub CLI | 2.0+ | `gh --version` |

GitHub CLI 인증이 되어 있어야 한다:

```bash
gh auth login
gh auth status   # → "Logged in to github.com" 확인
```

---

## 3. 초기 설치

### 3.1 저장소 클론

```bash
git clone https://github.com/loveiscomplicated/AI_coding_agent.git
cd AI_coding_agent
```

### 3.2 Python 의존성

```bash
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 3.3 프론트엔드 의존성

```bash
cd frontend
npm install
cd ..
```

### 3.4 Docker 테스트 이미지 빌드

에이전트가 pytest를 격리된 컨테이너에서 실행하기 위한 이미지다. **최초 1회**만 필요하다.

```bash
python -c "from docker.runner import DockerTestRunner; DockerTestRunner().build_image()"
```

빌드가 완료되면 `ai-coding-agent-test-runner` 이미지가 생성된다.

---

## 4. 환경 변수 설정

### 4.1 백엔드 (`/.env`)

```bash
cp .env.example .env
```

`.env` 파일을 열어 실제 값을 입력한다:

```dotenv
ANTHROPIC_API_KEY=sk-ant-...      # 필수: Anthropic API 키
DISCORD_BOT_TOKEN=                # 선택: Discord 핫라인 사용 시
DISCORD_CHANNEL_ID=               # 선택: Discord 채널 ID (숫자)
```

### 4.2 프론트엔드 (`/frontend/.env`)

```bash
cp frontend/.env.example frontend/.env
```

```dotenv
VITE_ANTHROPIC_API_KEY=sk-ant-...   # 회의 UI에서 Opus 직접 호출용
VITE_API_BASE_URL=http://localhost:8000   # 백엔드 주소 (기본값)
```

> **주의**: `VITE_ANTHROPIC_API_KEY`는 브라우저에서 직접 Anthropic API를 호출하는 회의 기능에만 사용된다. 파이프라인 실행은 백엔드의 `ANTHROPIC_API_KEY`를 사용한다.

---

## 5. 서버 실행

두 개의 터미널을 각각 열어 실행한다.

**터미널 1 — 백엔드**

```bash
source .venv/bin/activate
uvicorn backend.main:app --reload --port 8000
```

정상 시작 시 `Application startup complete.` 출력됨.

**터미널 2 — 프론트엔드**

```bash
cd frontend
npm run dev
```

정상 시작 시 `Local: http://localhost:5173` 출력됨.

브라우저에서 `http://localhost:5173` 접속.

---

## 6. 회의 인터페이스 사용법

### 6.1 새 회의 시작

사이드바 상단 `+` 버튼을 클릭하면 회의 타입을 선택할 수 있다.

| 타입 | 언제 사용 |
|------|-----------|
| **프로젝트 회의** | 새 기능, 설계, 방향 논의. Claude Opus가 지적 파트너로 참여 |
| **시스템 회의** | 파이프라인 실행 결과 리뷰. 최근 Task Report 요약이 자동 주입됨 |

### 6.2 컨텍스트 문서 생성

회의 중 `↺` 버튼을 클릭하면 Opus가 대화 내용을 마크다운 문서로 요약 생성한다.

- 생성 중 취소 가능
- 문서는 `📄` 버튼으로 언제든 다시 볼 수 있음
- **회의 종료** 버튼: 최종 문서를 생성하고 저장함

### 6.3 회의 기록 관리

- 사이드바 좌측에 과거 회의 목록이 표시됨
- 각 항목 호버 → `···` 메뉴 → **이름 수정** / **삭제**
- 검색창으로 제목 검색 가능

### 6.4 파일·이미지 첨부

메시지 입력창 왼쪽 `+` 버튼을 클릭하거나, 파일을 입력창에 드래그&드롭.

지원 형식: 이미지(PNG/JPG/GIF), PDF, 텍스트 파일 등.

---

## 7. 파이프라인 실행 (CLI)

### 7.1 기본 실행

```bash
python -m orchestrator.run --tasks data/tasks.yaml --repo .
```

실행 전 태스크 목록과 실행 순서를 보여주고 `y/N`으로 확인을 요청한다.

### 7.2 주요 옵션

| 옵션 | 설명 | 기본값 |
|------|------|--------|
| `--tasks`, `-t` | 태스크 YAML 파일 경로 | 필수 |
| `--repo`, `-r` | 대상 git 저장소 경로 | `.` |
| `--base-branch`, `-b` | PR base branch | `dev` |
| `--id` | 특정 태스크 하나만 실행 | 전체 |
| `--yes`, `-y` | 확인 없이 바로 시작 | false |
| `--no-pr` | PR 생성 없이 로컬만 실행 | false |
| `--parallel`, `-p` | 그룹 내 병렬 실행 수 | `1` |
| `--verbose`, `-v` | DEBUG 로그 출력 | false |

### 7.3 사용 예시

```bash
# 전체 실행, 확인 없이
python -m orchestrator.run -t data/tasks.yaml -y

# 특정 태스크 하나만 재실행
python -m orchestrator.run -t data/tasks.yaml --id task-003

# 그룹 내 최대 3개 병렬 실행
python -m orchestrator.run -t data/tasks.yaml -p 3

# PR 없이 로컬 테스트만
python -m orchestrator.run -t data/tasks.yaml --no-pr

# 다른 저장소의 dev2 브랜치로 PR
python -m orchestrator.run -t data/tasks.yaml --repo ../my-project --base-branch dev2
```

### 7.4 실행 흐름

```
1. tasks.yaml 로드
2. depends_on 기반 실행 그룹 계산 (Kahn's algorithm)
3. 사람 확인 (--yes 로 생략)
4. Docker 이미지 존재 확인 (없으면 자동 빌드)
5. 그룹 순서대로 실행:
   a. 각 그룹 내 태스크 병렬 실행 (--parallel)
      - TestWriter → Implementer → Docker pytest → Reviewer
      - 실패 시 최대 3회 Implementer 재시도
      - 성공 시 git worktree에서 브랜치 생성 → 커밋 → PR
   b. 그룹 완료 후 dev에 자동 머지 (MergeAgent로 충돌 자동 해결)
   c. PROJECT_STRUCTURE.md 자동 갱신
6. 파이프라인 종료 후 Milestone Report 생성 (data/reports/milestones/)
```

### 7.5 실행 결과 확인

- **tasks.yaml**: 각 태스크의 `status`, `pr_url`, `retry_count` 자동 업데이트
- **data/reports/**: 태스크별 `task-{id}.yaml` 리포트 저장
- **data/reports/milestones/**: 파이프라인 완료 후 마크다운 요약 보고서

---

## 8. 파이프라인 실행 (UI)

웹 UI에서도 파이프라인을 실행할 수 있다.

1. `http://localhost:5173` 접속
2. 사이드바에서 **채팅** 탭 선택
3. 회의 중 `+` → **프로젝트 회의** 시작
4. 대화 후 **TagDraftPanel** 에서 태스크 편집 → **파이프라인 실행** 버튼

또는 백엔드 API를 직접 호출:

```bash
curl -X POST http://localhost:8000/api/pipeline/run \
  -H "Content-Type: application/json" \
  -d '{"tasks_path": "data/tasks.yaml", "repo_path": "."}'
```

반환된 `job_id`로 상태를 조회한다:

```bash
curl http://localhost:8000/api/pipeline/status/{job_id}
```

---

## 9. tasks.yaml 작성법

### 9.1 전체 구조

```yaml
tasks:
  - id: task-001
    title: 기능 제목
    description: |
      구현할 내용을 자세히 설명한다.
      어떤 모듈인지, 어떤 함수가 필요한지 명시한다.
    acceptance_criteria:
      - 조건 1 (테스트 가능한 형태로 작성)
      - 조건 2
    target_files:
      - src/my_module/__init__.py
      - src/my_module/main.py
    test_framework: pytest
    depends_on: []
    status: pending
    retry_count: 0
    last_error: ''
    pr_url: ''
    failure_reason: ''
```

### 9.2 필드 설명

| 필드 | 필수 | 설명 |
|------|------|------|
| `id` | ✅ | 고유 식별자. `task-001` 형식 권장 |
| `title` | ✅ | 한 줄 제목 |
| `description` | ✅ | 구현 내용 상세 설명. 에이전트가 이 내용을 바탕으로 코드를 작성함 |
| `acceptance_criteria` | ✅ | 테스트 작성 기준. **검증 가능한 형태**로 작성할수록 품질이 높아짐 |
| `target_files` | ✅ | 에이전트가 생성할 파일 목록. 경로는 workspace 기준 상대 경로 |
| `test_framework` | ✅ | 현재 `pytest`만 지원 |
| `depends_on` | ✅ | 선행 태스크 ID 목록. 빈 리스트면 독립 실행 |
| `status` | - | `pending` / `implementing` / `reviewing` / `done` / `failed` |
| `retry_count` | - | 현재 재시도 횟수 (자동 관리) |
| `last_error` | - | 마지막 실패 오류 (자동 관리) |
| `pr_url` | - | 생성된 PR URL (자동 관리) |

### 9.3 의존성 설정

```yaml
tasks:
  - id: task-001
    depends_on: []          # 독립 실행 (그룹 1)

  - id: task-002
    depends_on: [task-001]  # task-001 완료 후 실행 (그룹 2)

  - id: task-003
    depends_on: []          # 독립 실행 (그룹 1, task-001과 병렬 가능)

  - id: task-004
    depends_on: [task-001, task-003]  # 둘 다 완료 후 실행 (그룹 3)
```

같은 그룹의 태스크는 `--parallel N` 옵션으로 병렬 실행된다.

### 9.4 description 작성 팁

에이전트의 구현 품질은 `description`의 명확성에 크게 좌우된다.

**좋은 예:**
```yaml
description: |
  utils/calculator.py 모듈을 구현한다.

  구현할 함수:
  - add(a: int, b: int) -> int: 두 정수의 합을 반환한다.
  - divide(a: float, b: float) -> float: a를 b로 나눈다. b==0이면 ZeroDivisionError를 발생시킨다.

  외부 라이브러리 없이 표준 라이브러리만 사용한다.
```

**나쁜 예:**
```yaml
description: "계산기 모듈 만들어"
```

---

## 10. 대시보드

### 10.1 접속

사이드바에서 **대시보드** 탭(격자 아이콘) 클릭.

### 10.2 화면 구성

**메트릭 카드 (상단)**

| 카드 | 설명 |
|------|------|
| 총 실행 태스크 | Task Report가 생성된 전체 태스크 수 |
| 성공률 | 완료 / 전체 × 100 |
| 첫 시도 성공률 | 재시도 없이 통과한 태스크 비율 |
| APPROVED | Reviewer가 APPROVED 판정한 수 |
| 평균 소요 시간 | 태스크당 평균 실행 시간 (초) |
| 총 재시도 횟수 | 모든 태스크의 Implementer 재시도 합계 |
| 마일스톤 보고서 | 생성된 마일스톤 보고서 수 |
| 태스크 상태 | status별 카운트 |

**태스크 목록 (중단)**

각 태스크의 현재 상태, 리뷰 판정, 테스트 수, 소요 시간, 재시도 수를 표시한다. PR URL이 있으면 클릭 가능한 링크가 표시된다.

**마일스톤 보고서 (하단)**

파이프라인 실행 완료 시 자동 생성된 보고서 목록. 클릭하면 우측에 내용이 표시된다.

---

## 11. Discord 핫라인

파이프라인 실행 중 실시간 알림을 받고, 태스크 실패 시 Discord에서 힌트를 입력할 수 있다.

### 11.1 설정

1. [Discord Developer Portal](https://discord.com/developers/applications)에서 봇 생성
2. Bot → Token 복사 → `.env`의 `DISCORD_BOT_TOKEN`에 입력
3. 알림 받을 채널의 ID 복사 → `DISCORD_CHANNEL_ID`에 입력
4. 봇을 해당 채널이 있는 서버에 초대 (Message, Send Messages 권한 필요)

### 11.2 알림 종류

| 알림 | 발생 시점 |
|------|-----------|
| `📋 파이프라인 시작` | `run_pipeline()` 호출 시 |
| `🚀 [task-xxx] 시작` | 각 태스크 실행 시작 시 |
| `✅ [task-xxx] 완료!` | PR 생성 성공 시 (PR URL 포함) |
| `❌ [task-xxx] 실패` | 파이프라인 실패 시 (원인 포함) |
| `🏁 파이프라인 완료` | 전체 완료 시 (성공/실패 수 포함) |

### 11.3 힌트 입력

태스크 실패 알림 메시지에 **5분 이내**로 Discord 채널에 답장하면, 그 내용이 다음 Implementer 재시도에 힌트로 전달된다.

"건너뜀" 또는 "skip" 입력 시 힌트 없이 다음으로 넘어간다. 5분 내 응답 없으면 자동으로 건너뜀.

### 11.4 설정 확인

```bash
curl http://localhost:8000/api/discord/status
```

```bash
# 테스트 메시지 전송
curl -X POST http://localhost:8000/api/discord/test
```

---

## 12. API 레퍼런스

백엔드가 실행 중일 때 `http://localhost:8000/docs`에서 Swagger UI로 전체 API를 확인할 수 있다.

### 주요 엔드포인트

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/api/health` | 서버 상태 확인 |
| POST | `/api/pipeline/run` | 파이프라인 실행 (비동기 job 반환) |
| GET | `/api/pipeline/status/{job_id}` | 파이프라인 실행 상태 조회 |
| GET | `/api/pipeline/jobs` | 전체 job 목록 |
| GET | `/api/tasks` | tasks.yaml 목록 조회 |
| POST | `/api/tasks/draft` | Sonnet으로 태스크 초안 생성 |
| GET | `/api/dashboard/summary` | 메트릭 요약 |
| GET | `/api/dashboard/tasks` | 태스크 + 리포트 조인 목록 |
| GET | `/api/dashboard/milestones` | 마일스톤 보고서 목록 |
| GET | `/api/dashboard/milestones/{filename}` | 마일스톤 보고서 내용 |
| POST | `/api/execution-brief` | 시스템 회의용 실행 요약 생성 |
| POST | `/api/reports/weekly` | 주간 보고서 생성 |
| GET | `/api/reports/weekly` | 주간 보고서 목록 |
| GET | `/api/discord/status` | Discord 연결 상태 |
| POST | `/api/discord/test` | Discord 테스트 메시지 전송 |

### pipeline/run 요청 예시

```json
{
  "tasks_path": "data/tasks.yaml",
  "repo_path": ".",
  "base_branch": "dev",
  "task_id": null,
  "no_pr": false,
  "verbose": false
}
```

---

## 13. 트러블슈팅

### "백엔드 서버에 연결할 수 없습니다"

프론트엔드가 백엔드에 연결하지 못하는 경우다.

```bash
# 백엔드 실행 확인
uvicorn backend.main:app --reload --port 8000

# 포트 충돌 확인
lsof -i :8000
```

### "Docker 이미지 없음" 오류

```bash
python -c "from docker.runner import DockerTestRunner; DockerTestRunner().build_image()"
```

Docker Desktop이 실행 중인지 확인할 것.

### "gh auth" 오류

```bash
gh auth login
gh auth status
```

### git worktree 오류 (이미 존재하는 브랜치)

```bash
# 고아 워크트리 정리
git worktree prune

# 브랜치 강제 삭제 후 재실행
git branch -D agent/task-xxx
```

### 태스크 실패 후 재실행

`tasks.yaml`에서 해당 태스크의 `status`를 `pending`으로 변경한 후 다시 실행한다. 또는 `--id` 옵션 사용 시 자동으로 pending으로 강제된다:

```bash
python -m orchestrator.run -t data/tasks.yaml --id task-003
```

### Implementer 무한 재시도

`last_error` 내용을 확인하고 `description`이나 `acceptance_criteria`를 더 명확하게 수정한 뒤 재실행한다.

### MergeAgent 머지 실패

자동 머지에 실패해도 태스크 자체는 `done`이다. PR은 정상 생성되어 있으므로 GitHub에서 수동으로 머지하면 된다.

```bash
# dev 브랜치 상태 확인
git log dev --oneline -10

# 수동 머지
git checkout dev
git merge agent/task-xxx
```

---

## 부록: 디렉토리 구조

```
AI_coding_agent/
├── agents/
│   ├── roles.py               # 에이전트 역할 설정 (TestWriter, Implementer, Reviewer)
│   ├── scoped_loop.py         # ScopedReactLoop — 도구 제한 + workspace 격리
│   └── prompts/               # 역할별 시스템 프롬프트 (.md)
├── backend/
│   ├── main.py                # FastAPI 앱 진입점
│   └── routers/               # API 라우터
├── data/
│   ├── tasks.yaml             # 태스크 정의 (직접 편집)
│   └── reports/               # Task Report + 마일스톤 보고서 자동 저장
│       └── milestones/
├── docker/
│   ├── Dockerfile.test        # pytest 격리 실행 이미지
│   └── runner.py              # DockerTestRunner
├── docs/                      # 프로젝트 문서
├── frontend/                  # React + Vite 프론트엔드
│   └── src/
│       └── components/
├── hotline/
│   └── notifier.py            # Discord 알림 클라이언트
├── llm/                       # LLM 클라이언트 래퍼
├── orchestrator/
│   ├── run.py                 # CLI 진입점
│   ├── pipeline.py            # TDD 파이프라인 상태 머신
│   ├── workspace.py           # 태스크별 격리 작업 디렉토리 관리
│   ├── git_workflow.py        # git worktree 기반 브랜치/커밋/PR
│   ├── merge_agent.py         # LLM 기반 머지 충돌 자동 해결
│   ├── milestone.py           # 마일스톤 보고서 생성
│   ├── report.py              # Task Report 저장/로드
│   └── task.py                # Task 데이터 모델 + YAML 로드/저장
└── .env                       # API 키 (git 추적 제외)
```
