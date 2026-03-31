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
10. [컨텍스트 문서 (data/context/)](#10-컨텍스트-문서-datacontext)
11. [대시보드](#11-대시보드)
12. [Discord 핫라인 & 에이전트 질문](#12-discord-핫라인--에이전트-질문)
13. [비용 최적화](#13-비용-최적화)
14. [API 레퍼런스](#14-api-레퍼런스)
15. [트러블슈팅](#15-트러블슈팅)

---

## 1. 시스템 개요

```
사람 (회의 UI 또는 CLI)
     ↕
백엔드 (FastAPI)
     ↕
파이프라인 오케스트레이터
     ├── TestWriter (Haiku) → 테스트 작성
     │        └── ask_user ──→ Discord 대화 → 사람이 확정 → 에이전트 계속
     ├── Implementer (Sonnet) → 구현
     │        └── ask_user ──→ Discord 대화 → 사람이 확정 → 에이전트 계속
     ├── Docker 테스트 러너 → pytest 격리 실행
     ├── Reviewer (Haiku) → 코드 리뷰
     └── GitWorkflow → 브랜치 · 커밋 · PR 생성
```

에이전트는 구현 중 모호한 사항을 발견하면 `context/` 문서를 먼저 확인하고, 그래도 불명확하면 `ask_user` 도구를 통해 사용자와 직접 대화한다.

**핵심 흐름**: `tasks.yaml` 작성 → 파이프라인 실행 → 에이전트가 TDD로 코드 작성 → PR 생성 → 사람이 검토 후 머지.

**멀티 프로젝트**: 이 도구 자체는 `AI_coding_agent/`에 있지만, 파이프라인은 `--repo` 플래그로 어떤 프로젝트에도 적용할 수 있다. 리포트와 tasks.yaml은 대상 프로젝트 디렉토리에 저장된다.

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
VITE_ANTHROPIC_API_KEY=sk-ant-...        # 회의 UI에서 Opus 직접 호출용
VITE_API_BASE_URL=http://localhost:8000  # 백엔드 주소 (기본값)
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
| `--no-pr` | PR 생성 없이 로컬 실행 | false |
| `--parallel`, `-p` | 그룹 내 병렬 실행 수 | `1` |
| `--reports-dir` | Task Report 저장 디렉토리 | `{repo}/data/reports` |
| `--verbose`, `-v` | DEBUG 로그 출력 | false |

### 7.3 사용 예시

```bash
# 전체 실행, 확인 없이
python -m orchestrator.run -t data/tasks.yaml -y

# 다른 프로젝트에 적용
python -m orchestrator.run \
  --tasks /path/to/my-project/tasks.yaml \
  --repo /path/to/my-project

# 특정 태스크 하나만 재실행
python -m orchestrator.run -t data/tasks.yaml --id task-003

# 그룹 내 최대 3개 병렬 실행
python -m orchestrator.run -t data/tasks.yaml -p 3

# PR 없이 로컬 테스트만
python -m orchestrator.run -t data/tasks.yaml --no-pr
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
      - 실패 시 최대 2회 Implementer 재시도, 오케스트레이터 최대 1회 개입
      - 성공 시 git worktree에서 브랜치 생성 → 커밋 → PR
   b. 그룹 완료 후 dev에 자동 머지 (MergeAgent로 충돌 자동 해결)
   c. PROJECT_STRUCTURE.md 자동 갱신
6. 파이프라인 종료 후 Milestone Report 생성
   ({repo}/data/reports/milestones/)
```

### 7.5 실행 결과 확인

- **tasks.yaml**: 각 태스크의 `status`, `pr_url`, `retry_count` 자동 업데이트
- **`{repo}/data/reports/`**: 태스크별 `task-{id}.yaml` 리포트 저장
- **`{repo}/data/reports/milestones/`**: 파이프라인 완료 후 마크다운 요약 보고서

---

## 8. 파이프라인 실행 (UI)

### 8.1 실행 방법

1. `http://localhost:5173` 접속
2. 사이드바에서 `+` → **프로젝트 회의** 시작
3. Opus와 대화 후 **태스크 초안 생성** 버튼 클릭
4. 생성된 태스크를 편집
5. 헤더의 경로 입력 후 **저장 & 파이프라인 시작** 클릭

### 8.2 경로 설정

태스크 초안 화면 헤더에 세 개의 입력창이 있다:

| 입력 | 설명 | 예시 |
|------|------|------|
| **프로젝트 루트** | 에이전트가 코드를 짜는 대상 레포의 절대 경로 | `/path/to/my-project` |
| **브랜치** | PR base branch | `main` |
| **에이전트** | 그룹 내 병렬 실행 수 | `1` |

- tasks.yaml은 `{프로젝트 루트}/data/tasks.yaml`에 자동 저장된다.
- 리포트는 `{프로젝트 루트}/data/reports/`에 자동 저장된다.
- 폴더 아이콘 버튼으로 macOS Finder에서 경로를 직접 선택할 수 있다.

### 8.3 실시간 진행 로그

파이프라인 실행 중에는 터미널 없이 UI에서 실시간으로 진행 상황을 볼 수 있다:

```
🚀 파이프라인 시작 — 9개 태스크
▶ [task-001] MapService 인터페이스 정의
  · TestWriter → Implementer → Docker → Reviewer…
  ✓ 테스트 통과: 12 passed in 4.3s
  · 리뷰: APPROVED — 인터페이스 설계가 명확합니다
  · 브랜치 → 커밋 → 푸시 → PR 생성 중…
✅ [task-001] MapService 인터페이스 정의 완료 (87.2s)  PR → https://...
▶ [task-002] 경로 탐색 알고리즘 구현
  ...
```

완료되면 **결과 확인 →** 버튼이 나타난다.

### 8.4 페이지 새로고침 후 재연결

파이프라인 실행 중 페이지를 새로고침하거나 다른 화면으로 이동해도 자동으로 복원된다.

- 앱 로드 시 백엔드에서 실행 중인 잡을 확인
- 사이드바에 초록 펄스 배지 표시 → 클릭하면 로그 뷰 복원

백엔드가 재시작된 경우 파이프라인 자체가 중단되므로 재연결이 불가능하다. 이 경우 대시보드에서 완료된 태스크의 리포트를 확인한다.

### 8.5 API 직접 호출

```bash
# 파이프라인 시작
curl -X POST http://localhost:8000/api/pipeline/run \
  -H "Content-Type: application/json" \
  -d '{
    "tasks_path": "/path/to/project/tasks.yaml",
    "repo_path": "/path/to/project"
  }'
# → {"job_id": "abc123...", "status": "running"}

# 상태 조회
curl http://localhost:8000/api/pipeline/status/{job_id}

# 실시간 SSE 스트림 구독
curl -N http://localhost:8000/api/pipeline/stream/{job_id}
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
  - divide(a: float, b: float) -> float: a를 b로 나눈다.
    b==0이면 ZeroDivisionError를 발생시킨다.

  외부 라이브러리 없이 표준 라이브러리만 사용한다.
```

**나쁜 예:**
```yaml
description: "계산기 모듈 만들어"
```

---

## 10. 컨텍스트 문서 (data/context/)

에이전트는 `tasks.yaml`의 `description`과 `acceptance_criteria`만으로는 파악하기 어려운 아키텍처 결정, 제약 사항, 비즈니스 맥락을 컨텍스트 문서에서 on-demand로 참조한다.

### 10.1 개념

```
회의에서 논의한 내용
     ↓ (회의 UI → 컨텍스트 문서 생성)
data/context/spec.md          ← 원본 스펙·설계 문서
     ↓ (태스크 초안 생성)
data/tasks.yaml               ← 에이전트 실행 단위
     ↓ (파이프라인 실행)
workspace/context/spec.md     ← 에이전트 워크스페이스에 자동 복사
```

에이전트는 프롬프트에 문서 전체가 주입되는 게 아니라, 필요할 때 `read_file`로 참조한다. 컨텍스트 비용 증가 없이 풍부한 배경 정보를 제공할 수 있다.

### 10.2 문서 저장 위치

대상 프로젝트의 `data/context/` 디렉토리에 마크다운(또는 텍스트) 파일을 저장한다.

```
/path/to/my-project/
└── data/
    ├── context/
    │   ├── architecture.md   ← 아키텍처 설계 결정
    │   ├── api-spec.md       ← 외부 API 제약사항
    │   └── business-rules.md ← 비즈니스 로직 규칙
    └── tasks.yaml
```

파일 이름이나 개수에 제한은 없다. 디렉토리 안의 모든 파일이 각 태스크 워크스페이스에 자동 복사된다.

### 10.3 에이전트가 참조하는 시점

에이전트 시스템 프롬프트에 다음 원칙이 명시되어 있다:

> 구현 중 요구사항이 불명확할 때:
> 1. `context/` 디렉토리의 스펙 문서를 `read_file`로 확인한다.
> 2. 그래도 불명확하면 `ask_user`로 사용자에게 직접 질문한다.

### 10.4 컨텍스트 문서 작성 팁

에이전트가 유용하게 참조할 수 있는 내용:

- **아키텍처 결정**: 왜 이 패턴을 선택했는지 (예: Repository 패턴, 특정 라이브러리 선택 이유)
- **외부 API 제약**: 호출 형식, 인증 방식, 에러 처리 관례
- **비즈니스 규칙**: 예외 vs 반환값 선택 정책, 권한 정책, 데이터 형식 규칙
- **공통 컨벤션**: 네이밍 규칙, 코드 스타일, 파일 구조 원칙

`tasks.yaml`의 `description`에는 "무엇을" 구현할지, `context/` 문서에는 "왜" 그리고 "어떤 제약 하에" 구현하는지를 담는 것이 효과적이다.

---

## 11. 대시보드

### 11.1 접속

사이드바에서 **대시보드** 탭(격자 아이콘) 클릭 → 프로젝트 목록 화면이 표시된다.

### 11.2 프로젝트 관리

프로젝트 목록 화면에서 등록된 프로젝트 카드를 클릭하면 해당 프로젝트의 상세 대시보드로 진입한다.

**새 프로젝트 추가**

`+ 새 프로젝트` 버튼 → 모달에서 프로젝트 루트 디렉토리 경로와 브랜치를 입력한다.
- 폴더 아이콘으로 Finder에서 경로 선택 가능
- 프로젝트 이름은 루트 디렉토리 폴더명으로 자동 설정됨

**백엔드 자동 탐색**

`새로고침` 버튼을 누르면 백엔드에서 실행된 파이프라인 잡 목록을 가져와 아직 등록되지 않은 프로젝트를 자동으로 추가한다.

**프로젝트 카드 상태**

각 카드에는 최근 파이프라인 실행 상태(`running` / `done` / `error`)가 표시된다.

### 11.3 프로젝트 상세 대시보드

프로젝트 카드를 클릭하면 상세 화면으로 진입하며, 상단 **← 프로젝트 목록** 버튼으로 돌아올 수 있다.

**파이프라인 제어 (헤더)**

| 상태 | 표시 |
|------|------|
| 실행 중 | ⏸ 멈춤 / ■ 중단 버튼 활성화, 초록 펄스 배지 |
| 일시정지 | ▶ 계속 / ■ 중단 버튼 활성화 |
| 미실행 | **▶ 파이프라인 재개** 버튼 — pending/failed 태스크 이어서 실행 |

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

**태스크 목록 (중단) — 아코디언**

각 태스크 행을 클릭하면 세부 내용이 펼쳐진다.

| 항목 | 설명 |
|------|------|
| 요약 행 | ID, 타이틀, 상태 배지, 리뷰 판정, 소요 시간, PR 링크 |
| 설명 | `description` 전문 |
| 수락 기준 | `acceptance_criteria` 항목별 목록 |
| 의존성 | `depends_on` ID 목록 |
| 실패 원인 | `failure_reason` (실패 태스크만 표시, 빨간색) |
| 리포트 상세 | 테스트 수, 재시도 수, 완료 시각 |

**마일스톤 보고서 (하단)**

파이프라인 실행 완료 시 자동 생성된 보고서 목록. 클릭하면 우측에 내용이 표시된다.

---

## 12. Discord 핫라인 & 에이전트 질문

Discord를 통해 두 가지 상호작용이 가능하다:

| 기능 | 발생 시점 | 방향 |
|------|-----------|------|
| **파이프라인 알림** | 태스크 시작·완료·실패 등 이벤트 발생 시 | 봇 → 사람 |
| **에이전트 질문** | 에이전트가 구현 중 모호한 사항 발견 시 | 봇 → 사람 → 봇 (대화) |

### 12.1 설정

1. [Discord Developer Portal](https://discord.com/developers/applications)에서 봇 생성
2. Bot → Token 복사 → `.env`의 `DISCORD_BOT_TOKEN`에 입력
3. 알림 받을 채널의 ID 복사 → `DISCORD_CHANNEL_ID`에 입력
4. 봇을 해당 채널이 있는 서버에 초대 (Message, Send Messages 권한 필요)

### 12.2 파이프라인 알림 종류

| 알림 | 발생 시점 |
|------|-----------|
| `📋 파이프라인 시작` | 파이프라인 시작 시 |
| `🚀 [task-xxx] 시작` | 각 태스크 실행 시작 시 |
| `✅ [task-xxx] 완료!` | PR 생성 성공 시 (PR URL 포함) |
| `❌ [task-xxx] 실패` | 파이프라인 실패 시 (원인 포함) |
| `🏁 파이프라인 완료` | 전체 완료 시 (성공/실패 수 포함) |

### 12.3 태스크 실패 시 힌트 입력

태스크 실패 알림 메시지에 Discord 채널에 답장하면, 그 내용이 다음 Implementer 재시도에 힌트로 전달된다.

"건너뜀" 또는 "skip" 입력 시 힌트 없이 다음으로 넘어간다.

### 12.4 에이전트 질문 & 오케스트레이터 대화

에이전트가 `context/` 문서로도 해결할 수 없는 모호한 사항을 만나면 `ask_user` 도구를 호출해 Discord로 질문을 보낸다.

**대화 흐름**

```
봇:  ❓ 에이전트 질문

     로그인 실패 시 예외를 던져야 하나요, None을 반환해야 하나요?

     자유롭게 대화하세요. 결정이 되면 `확정`,
     에이전트에게 맡기려면 `알아서 해`를 입력해주세요.

나:  예외가 맞는 것 같은데 호출부에서 매번 try-catch 해야 하면 불편하지 않나요?

봇:  (오케스트레이터) 두 가지 트레이드오프가 있어요:
     - 예외: 실패를 무시하기 어렵고 명시적 / 호출부에 try-catch 강제
     - None: 호출부가 가볍지만 실패를 무시하기 쉬워 버그 원인이 될 수 있음
     이 서비스가 내부 API라면 예외가 더 안전합니다.

나:  내부 API야. 예외로 가자.

봇:  확정을 입력하면 에이전트에게 전달하겠습니다.

나:  확정

봇:  ✅ 확정 완료

     에이전트에게 전달할 답변:
     >>> 로그인 실패 시 AuthenticationException을 던진다.
         내부 API이므로 호출부에서 명시적으로 처리하도록 예외 방식을 채택한다.
```

**종료 키워드**

| 키워드 | 효과 |
|--------|------|
| `확정` / `결정` / `done` | 대화 내용을 요약해 에이전트에 전달하고 계속 진행 |
| `알아서 해` / `skip` / `패스` | 에이전트가 최선의 판단으로 스스로 결정하고 계속 진행 |

**주의사항**
- 에이전트는 답변을 받을 때까지 해당 도구 호출에서 대기한다. 파이프라인 스레드는 이 동안 블로킹된다.
- Discord가 설정되지 않은 경우 터미널 stdin으로 폴백된다.

### 12.5 설정 확인

```bash
curl http://localhost:8000/api/discord/status

# 테스트 메시지 전송
curl -X POST http://localhost:8000/api/discord/test
```

---

## 13. 비용 최적화

파이프라인은 내부적으로 여러 비용 절감 메커니즘을 적용하고 있다.

### 13.1 컨텍스트 슬라이딩 윈도우

에이전트 ReAct 루프는 매 iteration마다 대화 히스토리 전체를 LLM에 전송한다. 슬라이딩 윈도우를 적용해 초기 태스크 메시지와 최근 6턴만 유지하고 이전 히스토리는 자동으로 드롭한다.

```
슬라이딩 윈도우 없을 때:
  iteration 1:  3,000 tokens
  iteration 10: 30,000 tokens  (10배)
  iteration 20: 60,000 tokens  (20배)

슬라이딩 윈도우 적용 후:
  iteration 1~6: 점진적 증가
  iteration 7~:  ~18,000 tokens 고정 (초기 메시지 + 최근 6턴)
```

### 13.2 도구 결과 트런케이션

`read_file` 등의 도구 결과가 4,000자를 초과하면 자동으로 잘린다. 수만 줄짜리 파일을 전부 컨텍스트에 올리는 것을 방지한다.

### 13.3 시스템 프롬프트 캐싱

Anthropic의 prompt caching을 활용해 에이전트 시스템 프롬프트를 5분간 캐시한다. 동일 에이전트가 반복 호출될 때 시스템 프롬프트 입력 비용을 ~90% 절감한다.

캐시 동작 여부는 Anthropic Console의 Usage 탭 또는 API 사용량 CSV에서 `cache_read` 컬럼 값으로 확인할 수 있다.

### 13.4 파라미터 기본값

| 파라미터 | 기본값 | 설명 |
|----------|--------|------|
| `max_iterations` | 15 | 에이전트 ReAct 루프 최대 반복 수 |
| `max_retries` | 2 | Implementer 테스트 실패 시 재시도 횟수 |
| `max_orchestrator_retries` | 1 | 오케스트레이터 개입 재시도 횟수 |

CLI에서 `--parallel` 수를 높이면 병렬 실행 태스크 수가 늘어나 비용도 선형으로 증가한다.

### 13.5 지출 한도 설정 권장

Anthropic Console → Settings → Billing → Spend limits 에서 월 지출 한도를 설정할 것을 강력히 권장한다. 에이전트가 예상보다 많은 retry를 반복하거나 대량의 태스크를 실행하면 비용이 급격히 증가할 수 있다.

---

## 14. API 레퍼런스

백엔드가 실행 중일 때 `http://localhost:8000/docs`에서 Swagger UI로 전체 API를 확인할 수 있다.

### 주요 엔드포인트

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/api/health` | 서버 상태 확인 |
| POST | `/api/pipeline/run` | 파이프라인 실행 (비동기 job 반환) |
| GET | `/api/pipeline/status/{job_id}` | 파이프라인 실행 상태 조회 |
| GET | `/api/pipeline/stream/{job_id}` | **SSE 실시간 이벤트 스트림** |
| GET | `/api/pipeline/jobs` | 전체 job 목록 |
| GET | `/api/tasks` | tasks.yaml 목록 조회 |
| POST | `/api/tasks/draft` | Sonnet으로 태스크 초안 생성 |
| GET | `/api/dashboard/summary` | 메트릭 요약 (`?reports_dir=&tasks_path=`) |
| GET | `/api/dashboard/tasks` | 태스크 + 리포트 조인 목록 |
| GET | `/api/dashboard/milestones` | 마일스톤 보고서 목록 |
| GET | `/api/dashboard/milestones/{filename}` | 마일스톤 보고서 내용 |
| POST | `/api/execution-brief` | 시스템 회의용 실행 요약 생성 |
| POST | `/api/reports/weekly` | 주간 보고서 생성 |
| GET | `/api/discord/status` | Discord 연결 상태 |
| POST | `/api/discord/test` | Discord 테스트 메시지 전송 |

### pipeline/run 요청 예시

```json
{
  "tasks_path": "/path/to/project/data/tasks.yaml",
  "repo_path": "/path/to/project",
  "base_branch": "main",
  "task_id": null,
  "no_pr": false,
  "reports_dir": null
}
```

- `tasks_path` 기본값은 `{repo_path}/data/tasks.yaml`
- `reports_dir`가 `null`이면 `{repo_path}/data/reports`에 자동 저장된다.
- `base_branch` 기본값은 `main`

### SSE 이벤트 형식

`/api/pipeline/stream/{job_id}` 스트림에서 수신되는 이벤트 타입:

| type | 설명 | 주요 필드 |
|------|------|-----------|
| `pipeline_start` | 파이프라인 시작 | `total` |
| `task_start` | 태스크 시작 | `task_id`, `title` |
| `step` | 단계별 진행 | `task_id`, `step`, `message` |
| `task_done` | 태스크 성공 완료 | `task_id`, `title`, `elapsed`, `pr_url` |
| `task_fail` | 태스크 실패 | `task_id`, `title`, `reason`, `elapsed` |
| `task_skip` | 의존성 실패로 건너뜀 | `task_id`, `title`, `reason` |
| `paused` | 일시정지 | `next_task_id` |
| `resumed` | 재개 | `task_id` |
| `pipeline_aborted` | 사용자 중단 | `message` |
| `pipeline_done` | 전체 완료 | `success`, `fail` |
| `end` | 스트림 종료 sentinel | `status` |

---

## 15. 트러블슈팅

### "백엔드 서버에 연결할 수 없습니다"

```bash
uvicorn backend.main:app --reload --port 8000
lsof -i :8000   # 포트 충돌 확인
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

### git worktree 오류

```bash
git worktree prune          # 고아 워크트리 정리
git branch -D agent/task-xxx  # 브랜치 강제 삭제 후 재실행
```

### 태스크 실패 후 재실행

`--id` 옵션 사용 시 해당 태스크가 자동으로 pending으로 강제된다:

```bash
python -m orchestrator.run -t /path/to/project/tasks.yaml --id task-003 --repo /path/to/project
```

또는 `tasks.yaml`에서 해당 태스크의 `status`를 `pending`으로 직접 수정.

### Implementer 무한 재시도

`last_error` 내용을 확인하고 `description`이나 `acceptance_criteria`를 더 명확하게 수정한 뒤 재실행한다.

### 태스크 초안 생성이 중간에 잘림

컨텍스트 문서가 너무 길거나 태스크가 너무 많은 경우다. 컨텍스트 문서를 줄이거나 태스크를 나눠서 두 번 생성한다.

### 파이프라인 실행 중 페이지를 새로고침했을 때

사이드바에 초록 펄스 배지가 표시된다. 클릭하면 실행 중인 파이프라인의 로그 뷰로 자동 복원된다.

---

## 부록: 디렉토리 구조

```
AI_coding_agent/              ← 파이프라인 도구 (이 레포)
├── agents/
│   ├── roles.py              # 에이전트 역할 설정 (TestWriter, Implementer, Reviewer)
│   ├── scoped_loop.py        # ScopedReactLoop — 도구 제한 + workspace 격리
│   └── prompts/              # 역할별 시스템 프롬프트 (.md)
├── backend/
│   ├── main.py               # FastAPI 앱 진입점
│   └── routers/              # API 라우터
├── core/
│   └── loop.py               # ReactLoop — 슬라이딩 윈도우 + 도구 결과 트런케이션 적용
├── docker/
│   ├── Dockerfile.test       # pytest 격리 실행 이미지
│   └── runner.py             # DockerTestRunner
├── docs/                     # 프로젝트 문서
├── frontend/                 # React + Vite 프론트엔드
│   └── src/components/
│       ├── DashboardPage.tsx     # 프로젝트별 메트릭 대시보드
│       ├── PipelineLogView.tsx   # 실시간 파이프라인 로그 (SSE)
│       └── TaskDraftPanel.tsx    # 태스크 편집 + 파이프라인 실행
├── hotline/
│   └── notifier.py           # Discord 알림 클라이언트
├── llm/
│   └── claude_client.py      # Claude API 클라이언트 (프롬프트 캐싱 적용)
├── orchestrator/
│   ├── run.py                # CLI 진입점 + on_progress 이벤트 발행
│   ├── pipeline.py           # TDD 파이프라인 상태 머신
│   ├── workspace.py          # 태스크별 격리 작업 디렉토리 (/tmp) + context 복사
│   ├── git_workflow.py       # git worktree 기반 브랜치/커밋/PR
│   ├── merge_agent.py        # LLM 기반 머지 충돌 자동 해결
│   ├── milestone.py          # 마일스톤 보고서 생성
│   ├── report.py             # Task Report 저장/로드
│   └── task.py               # Task 데이터 모델 + YAML 로드/저장
├── tools/
│   ├── registry.py           # 도구 등록 & 스키마 관리
│   ├── hotline_tools.py      # ask_user 도구 — Discord 멀티턴 대화
│   └── ...                   # 파일·코드·git 도구
└── .env                      # API 키 (git 추적 제외)

/path/to/my-project/          ← 대상 프로젝트 (별도 레포)
├── data/
│   ├── context/              # ★ 스펙·아키텍처 문서 (에이전트가 참조)
│   │   ├── architecture.md
│   │   └── api-spec.md
│   ├── tasks.yaml            # 파이프라인 태스크 정의
│   └── reports/              # Task Report + 마일스톤 보고서 자동 저장
│       └── milestones/
└── PROJECT_STRUCTURE.md      # 코드베이스 구조 요약 (파이프라인이 자동 갱신)
```
