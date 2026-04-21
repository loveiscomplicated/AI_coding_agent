---
completeness: 99
hint: Phase 4 품질·자동화 강화 완료 (2026-04-21). Momus critique 시스템, 태스크 complexity 라벨, Quality Gate, APPROVED_WITH_SUGGESTIONS, collect-only 게이트, intervention 자동 분해, 이상치 탐지, 역할별 압축 프리셋, 의존성 그래프 UI 반영.
---

# 프로젝트 컨텍스트 문서: Multi-Agent Development System — 5단계 오케스트레이터 연결

## 0. 문서 개요 및 변경 이력

본 문서는 AI 에이전트 팀을 활용한 소프트웨어 개발 파이프라인(Multi-Agent Development System)의 **5단계(오케스트레이터 연결)** 설계를 기록한다. Phase 1(회의 인터페이스)과 Phase 2(단일 에이전트 TDD 파이프라인)가 각각 독립적으로 완료된 상태에서, 이 둘을 연결하고 운영에 필요한 인프라를 구축하는 단계이다.

대화에서는 다음 사항이 순차적으로 논의·확정되었다:

1. 연결 방식(느슨한 파일 기반 vs 백엔드 도입) → 가벼운 로컬 백엔드(FastAPI) 도입 확정
2. 아이맥 상시 서버 활용 가능성 검토 → 설계는 실행 환경 독립적으로, 아이맥은 후보로 보류
3. 태스크 초안 생성 흐름, 파이프라인 트리거 방식, Task Report 소비 방식 상세 설계
4. Discord 핫라인 아이디어 → 최소 버전을 5단계-A에 포함
5. 프론트엔드 구현은 멀티 에이전트가 아닌 Claude Code + 사람 직접 작업으로 분리
6. 보고서 체계(Task Report, Weekly Report) 및 시스템 자기 개선 루프 설계
7. 회의 타입 분리(프로젝트 회의 vs 시스템 회의) 및 컨텍스트 주입 전략
8. 태스크 간 의존성 처리: dev 머지 기반 + 트리 문서(PROJECT_STRUCTURE.md) 기반 컨텍스트 전달
9. 5단계 세부 구현 순서(Step 1~6) 확정
10. 첫 실제 프로젝트 선정: 시스템 자체의 유틸리티 모듈(셀프 호스팅 검증)

---

## 1. 프로젝트 배경 및 현재 상태

### 1.1 전체 시스템 구조 (기존)

```
나 (사람)
 ↕  회의 / 보고서 검토 / PR 승인
중앙 오케스트레이터 (설정 가능한 LLM, 기본: claude-opus-4-6)
 ↕  태스크 분배 / 결과 수집 / 컨텍스트 압축
┌──────────────────────────────────┐
│  Agent 1    Agent 2    Agent N   │  ← 설정 가능한 LLM (기본: claude-haiku-4-5-20251001)
│  [샌드박스]  [샌드박스]  [샌드박스] │
│  테스트 → 코드 → 리뷰 (순차)     │
└──────────────────────────────────┘
 ↓  PR 생성 + 승인 대기
실제 코드베이스 (Git)
```

지원 프로바이더: `claude`, `openai`, `glm`, `ollama`, `gemini`.
환경 변수: `LLM_PROVIDER`, `LLM_DEFAULT_MODEL`, `LLM_TITLE_MODEL`, `LLM_ROLE_<ROLE>`.

### 1.2 완료된 Phase

**Phase 1 (회의 인터페이스) ✅**: React SPA (Vite + Tailwind), 백엔드 API 프록시 경유 LLM 호출, 마크다운 컨텍스트 문서 생성, 이전 회의 로드, 파일/이미지 첨부, 선택지 버튼 등 완성.

**Phase 2 (단일 에이전트 TDD 파이프라인) ✅**: Docker 테스트 러너, ScopedReactLoop, TestWriter/Implementer/Reviewer 에이전트, TDDPipeline 상태 머신, GitWorkflow(브랜치→커밋→PR), YAML 기반 태스크 관리. E2E 검증 완료(정수 계산기, 단어 빈도 분석기).

### 1.3 현재 문제: 두 시스템의 단절 (해결 완료)

```
Phase 1: 회의 UI (React) → context_doc.md 생성
Phase 2: 파이프라인 (Python CLI) → tasks.yaml 읽어서 실행
                  ↑
             수동으로 연결 (사람이 직접 tasks.yaml 작성)
```

5단계의 핵심 과제는 이 단절을 해소하는 것이었다. **현재 FastAPI 백엔드로 완전 연결 완료.**

---

## 2. 5단계 핵심 설계 결정

### 2.1 연결 방식: FastAPI 로컬 백엔드 도입 ✅ 구현 완료

**검토된 방향:**

| 방향 | 설명 | 판단 |
|------|------|------|
| A: 완전 파일 기반 | context_doc 다운로드 → 터미널에서 스크립트 실행 → 보고서 드래그&드롭 | **폐기** — 수동 워크플로우 문서화에 불과, 실질적 연결 아님 |
| B: 가벼운 로컬 백엔드 | FastAPI로 얇은 서버, 프론트엔드가 이 서버와 통신 | **채택** |
| C: 통합 백엔드 (풀스택) | WebSocket, 실시간 모니터링, 인증 등 완비 | **시기상조** — Phase 3에서 확장 |

**구현된 FastAPI 엔드포인트 (backend/routers/):**

```
chat.py:
  POST /api/chat/stream        LLM 스트리밍 채팅 (SSE)
  POST /api/chat/complete      LLM 단일 응답 (비스트리밍)
  GET  /api/models             사용 가능한 프로바이더별 모델 목록

health.py:
  GET  /api/health             헬스 체크 → {"status": "ok"}

tasks.py:
  GET  /api/tasks              태스크 목록 조회
  POST /api/tasks              태스크 목록 전체 저장 (덮어쓰기)
  GET  /api/tasks/{id}         단일 태스크 조회
  PATCH /api/tasks/{id}        태스크 부분 업데이트 (description, acceptance_criteria)
  POST /api/tasks/draft        태스크 초안 생성 시작 (비동기, job_id 반환)
  GET  /api/tasks/draft/{job_id} 초안 생성 상태/결과 조회
  POST /api/tasks/critique     초안 비판적 검토 시작 (비동기, job_id 반환)
  GET  /api/tasks/critique/{job_id} 검토 상태/결과 조회
  POST /api/tasks/critique/apply   critique 결과 기반 자동 수정 (동기, 병합 결과 반환)
  POST /api/tasks/{id}/redesign   실패 태스크 재설계 초안 생성 (비동기, job_id 반환)
  GET  /api/tasks/redesign/{job_id} 재설계 상태/결과 조회
  POST /api/tasks/fix-dependencies  순환 depends_on 자동 수정

pipeline.py:
  POST /api/pipeline/run           파이프라인 시작 (비동기, job_id 반환)
  GET  /api/pipeline/status/{id}   잡 상태 조회
  GET  /api/pipeline/stream/{id}   SSE 실시간 이벤트 스트림
  GET  /api/pipeline/jobs          모든 잡 목록
  POST /api/pipeline/control/{id}  제어 명령 (pause/resume/stop)

reports.py:
  POST /api/execution-brief        Task Report 요약 생성 (회의 시작 시 주입)
  GET  /api/project-structure      PROJECT_STRUCTURE.md 내용 반환
  POST /api/reports/weekly         주간 보고서 생성
  GET  /api/reports/weekly         주간 보고서 목록
  GET  /api/reports/weekly/{year}/{week}  특정 주 보고서 내용

dashboard.py:
  GET  /api/dashboard/summary      메트릭 집계
  GET  /api/dashboard/tasks        tasks.yaml + Task Report 조인
  GET  /api/dashboard/milestones   마일스톤 보고서 목록
  GET  /api/dashboard/milestones/{filename}  보고서 본문

discord_router.py:
  GET  /api/discord/status         Discord 연결 상태 확인
  GET  /api/discord/guilds         봇 참여 서버 목록 조회
  POST /api/discord/test           테스트 메시지 전송

utils.py:
  GET  /api/config                 현재 백엔드 기본 LLM 설정
  GET  /api/config/llm             런타임 LLM 설정 조회
  PATCH /api/config/llm            런타임 LLM 설정 변경
  GET  /api/utils/context-docs     context 문서 목록 조회
  GET  /api/utils/context-docs/{filename} context 문서 본문 조회
  POST /api/utils/save-context-doc context 문서 저장
  GET  /api/utils/browse           macOS 파일/폴더 선택 다이얼로그
```

### 2.2 실행 환경 독립성

**설계 원칙**: FastAPI 백엔드는 맥북 로컬, 아이맥 상시 서버, 클라우드 VPS 어디서든 동일하게 동작한다.

**아이맥 상시 서버 후보 검토:**

- 2015 Late iMac 21.5" (Intel Broadwell i5-5250U or i7-5775R, 8GB LPDDR3, HDD 1TB)
- 최대 macOS Monterey(12) 지원 — Docker Desktop 경계선, 향후 지원 끊길 가능성
- API 호출 기반 시스템이므로 로컬 연산 부하는 미미 → 하드웨어 자체는 충분
- **주요 우려**: HDD 기반 모델이면 파일 I/O 병목, Docker 호환성 불안정, macOS 보안 패치 종료
- **대안**: venv 기반 격리로 Docker 대체 가능, Colima 경량 런타임 시도 가능
- **결론**: 가능성 열어두되, 실측 확인 필요. 5단계 설계 자체는 환경 무관하게 진행.

### 2.3 프론트엔드 구현의 특수 처리

**결정: 프론트엔드는 멀티 에이전트 시스템이 구현하지 않고, Claude Code + 사람이 직접 구현한다.**

**배경:**
- 프론트엔드는 "좋아 보이는가"가 판단 기준의 상당 부분을 차지 → TDD로 검증 불가
- 레이아웃, 인터랙션, 간격 등 감각적 피드백이 필수 → 사람이 눈으로 보면서 실시간 수정해야 함
- Claude Code의 즉각적 피드백 루프(코드 수정 → 눈으로 확인 → 지시)가 프론트엔드에 최적

**구현**: tasks.yaml의 `task_type: "frontend"` 태스크는 파이프라인이 자동으로 제외한다. 백엔드 태스크(`task_type: "backend"`)만 TDD 파이프라인으로 실행.

### 2.4 태스크 타입 분기 ✅ 구현 완료

tasks.yaml의 `task_type` 필드로 파이프라인 동작을 분기:
- `backend`: TDD 파이프라인 전체 실행
- `frontend`: 파이프라인 제외, 사람이 직접 구현

---

## 3. 태스크 초안 생성 흐름

### 3.1 변환 흐름 ✅ 구현 완료

```
회의에서 context_doc.md 확정
  → "태스크 생성" 요청 (POST /api/tasks/draft)
  → LLM(기본: `LLM_DEFAULT_MODEL` 또는 task draft 전용 런타임 설정)이 context_doc.md를 읽고 tasks.yaml 초안 생성 (비동기)
  → GET /api/tasks/draft/{job_id} 로 결과 폴링
  → UI에서 태스크 목록 확인 / 수정 / 삭제 / 추가
  → 사람 승인
  → POST /api/pipeline/run 으로 파이프라인 실행 시작
```

### 3.2 LLM의 판단 범위

**LLM이 해도 되는 것:**
- 기능을 적절한 크기의 태스크로 분할
- acceptance_criteria를 테스트 가능한 형태로 구체화
- 태스크 간 의존 순서 제안 (depends_on 필드)
- task_type 분류 (backend/frontend)

**LLM이 하면 안 되는 것:**
- 회의에서 결정하지 않은 기능을 임의로 추가
- 기술 스택이나 아키텍처를 변경하는 판단

### 3.3 태스크 크기 가이드라인

**"하나의 태스크는 파일 3개 이하"** — Implementer가 안정적으로 구현할 수 있는 크기를 유지. 이 가이드라인은 초안 생성 프롬프트에 포함된다 (`_DRAFT_SYSTEM_PROMPT` in `backend/routers/tasks.py`).

### 3.5 크로스 언어 프로젝트 지원 (language 기반) ✅ 구현 완료

**문제**: Kotlin/Java/Go/JS 등 다중 언어 프로젝트에서 task draft가 언어 정보 없이 생성되면 Docker 이미지 선택, 테스트 프레임워크, target_files 규약이 모두 흔들린다. 또한 `src/models/user.py` 같은 정상 경로와 `app/src/main/java/...` 같은 깊은 경로를 동일하게 basename 처리하면 패키지 구조가 깨진다.

**해결 (3단계 방어):**

1. **프롬프트 가이드** (`_DRAFT_SYSTEM_PROMPT`):
   - 각 태스크에 `language` 필드를 **반드시 포함**
   - 언어별 target_files 확장자/네이밍 규칙 사용
     - Python: `snake_case.py`
     - Kotlin/Java: `PascalCase.kt/.java`
     - Go: `snake_case.go`
     - JavaScript/TypeScript: `camelCase` 또는 `PascalCase`
   - target_files: 파일명(flat) 또는 1단계 상대 경로만 허용 (`models/user.py`, `services/auth.py`)
   - `src/` 접두어 불필요 (자동 제거됨), 2단계 이상 경로 금지
   - description/acceptance_criteria는 언어 중립적으로 작성 (플랫폼 전용 API 금지)
   - description은 `### 목적과 배경 / 기술 요구사항 / 인접 컨텍스트 / 비고려 항목` 4개 섹션을 Markdown 헤더로 포함
   - task_type "frontend"는 오직 브라우저 UI에만 (Kotlin 프로젝트도 "backend" 유지)

2. **후처리 자동 보정** (`_sanitize_task_draft` + `_normalize_target_path`):
   - `src/` 접두어 제거 → 슬래시 1개이면 1-level 경로 **보존** (`models/user.py` 유지)
   - 슬래시 2개 이상이면 basename만 추출 (`app/src/.../Coordinate.kt` → `Coordinate.kt`)
   - 변환 후 중복 제거, 보정 발생 시 `warnings` 필드에 기록
   - description 100자 미만 또는 섹션 헤더 누락 시 경고
   - UI도 동일한 경로 정규화 규칙으로 동기화

   | 입력 | 결과 | 이유 |
   |------|------|------|
   | `user.py` | `user.py` | 슬래시 없음 → 유지 |
   | `models/user.py` | `models/user.py` | 1-level → 유지 |
   | `src/user.py` | `user.py` | src/ 접두어 제거 |
   | `src/models/user.py` | `models/user.py` | src/ 제거 → 1-level 유지 |
    | `app/src/main/FakeMap.kt` | `FakeMap.kt` | 2+ 슬래시 → basename |

3. **Implementer·Reviewer 프롬프트에 target_files 명시** (`_format_target_files`):
   - 에이전트가 어느 경로에 파일을 생성해야 하는지 `src/{path}` 형식으로 명확히 안내
   - `Task.language` + `LANGUAGE_TEST_FRAMEWORK_MAP`으로 언어별 테스트 프레임워크 자동 결정
   - DockerTestRunner가 `language`에 맞는 이미지를 선택

**실제 사례** (AR 길안내 앱 프로젝트):
```
입력:  app/src/main/java/com/arwalk/data/fake/FakeMapService.kt
보정:  FakeMapService.kt
```

### 3.4 tasks.yaml 확장 형식

```yaml
tasks:
  - id: "001"
    title: "메트릭 수집기"
    task_type: backend          # "backend" 또는 "frontend"
    language: python            # docker runner / 프롬프트 선택 기준
    complexity: standard        # "simple" | "standard" | "complex" | 생략 가능 (기본 standard)
    description: "..."
    depends_on: []              # 의존하는 태스크 ID 리스트
    acceptance_criteria:
      - "..."
    target_files:               # 생성 또는 수정할 파일 경로 (3개 이하)
      - "metrics/collector.py"
    test_framework: pytest      # 생략 시 language 기본값 사용
    status: pending             # pending | writing_tests | implementing | running_tests | reviewing | committing | done | failed | superseded
```

---

## 4. 파이프라인 트리거 및 실행 방식

### 4.1 기본 동작: 비동기 배치 실행 + 핫라인 개입 ✅ 구현 완료

```
tasks.yaml 초안 승인
  → POST /api/pipeline/run → job_id 즉시 반환
  → 백그라운드에서 의존성 순서에 따라 순차/병렬 실행
  → GET /api/pipeline/stream/{job_id} 로 SSE 실시간 이벤트 구독
  → 각 태스크 완료마다 Discord 알림
  → 사람이 Discord/UI에서 "멈춰" → 다음 태스크부터 일시정지
  → 사람이 "계속" → 재개
  → POST /api/pipeline/control/{job_id} {action: "pause"|"resume"|"stop"} 로 UI에서도 제어 가능
```

**RunRequest 파라미터:**
```
tasks_path: str               tasks.yaml 경로
repo_path: str                대상 레포 경로
base_branch: str              PR 베이스 브랜치 (기본: "main")
task_id: str | None           단일 태스크 실행 (None이면 전체)
no_pr: bool                   PR 생성 생략
no_push: bool                 True 시 로컬 브랜치·커밋만 생성, push/PR 건너뜀
verbose: bool                 상세 로깅
reports_dir: str | None       TaskReport 저장 경로 (None → repo_path/agent-data/reports)
logs_dir: str | None          로그 저장 경로 (None → repo_path/agent-data/logs)
max_workers: int              병렬 에이전트 수 (기본: 1, 순차)
max_orchestrator_retries: int 오케스트레이터 자동 재시도 최대 횟수 (기본: 3, 총 시도 = 이 값 + 1)
auto_merge: bool              그룹 완료 후 base_branch에 자동 머지
intervention_auto_split: bool True 시 최종 실패 직전 LLM이 태스크를 2~3개 하위 태스크로 자동 분해
auto_select_by_complexity: bool True 시 Task.complexity 라벨로 역할별 모델 자동 선택 (role_models는 상위 override 유지)
default_role_models: dict | None  역할별 기본 모델 맵
discord_channel_id: str|None  Discord 채널 ID
role_models: dict | None      역할별 모델 오버라이드 (test_writer/implementer/reviewer/orchestrator/merge_agent/intervention)
role_compaction_tuning_enabled: bool    역할별 압축 임계값 튜닝 활성화
role_compaction_tuning_preset: str      압축 프리셋 이름 (기본: BALANCED)
role_compaction_tuning_overrides: dict | None  역할별 프리셋 override (예: {"implementer": "aggressive"})
```

### 4.2 태스크 간 의존성 처리 ✅ 구현 완료

**세 가지 문제와 각각의 해결:**

| 문제 | 해결 방식 |
|------|-----------|
| 실행 순서 | `resolve_execution_groups()` — Kahn's algorithm으로 위상 정렬 |
| 코드 접근 | task-001 완료 → base branch에 머지 → task-002는 최신 base 기반 workspace 생성 |
| 컨텍스트 전달 | PROJECT_STRUCTURE.md (트리 문서) 기반 — StructureUpdater가 그룹 머지 후 자동 갱신 |

**의존성 실패 시 스킵**: 태스크 실패 시 failed_ids에 추가. depends_on ∩ failed_ids가 있는 후속 태스크는 FAILED 처리. 독립 태스크는 계속 실행.

**재개 지원**: `resolve_execution_groups(tasks, all_valid_ids=all_task_ids)` — 완료된 태스크 ID를 유효 ID로 인정하여 depends_on 검증 통과.

**의존성 pre-check (pipeline.py)** ✅:
- 선행 태스크가 DONE이면 파일 존재 확인 스킵 (산출물은 git 브랜치에 존재)
- 미완료 태스크만 filesystem에서 target_files 확인 → 없으면 `[DEPENDENCY_MISSING]` 즉시 실패
- auto_merge 없이도 정상 동작 (inject_dependency_context가 `git show`로 브랜치에서 읽음)

**의존성 산출물 주입 (workspace.py)** ✅:
- 1차: target_files 경로로 `git show {branch}:{path}` 읽기
- 2차 (fallback): target_files 경로 실패 시 `git diff --name-only`로 브랜치에서 실제 추가된 소스 파일을 찾아 주입 (tests/ 제외)
- Python 파일은 심볼 요약(클래스/함수 시그니처) 추출 → `context/dependency_artifacts.md`에 기록

**실제 사례**: AR 길안내 앱에서 task-004의 target_files가 `app/src/.../FakeMapService.kt`이지만 실제 생성 파일은 `FakeMapService.py`. fallback이 `git diff`로 실제 파일을 찾아 workspace에 주입.

### 4.3 컨텍스트 전달: 트리 문서(PROJECT_STRUCTURE.md) ✅ 구현 완료

**최종 결정: 두 가지 방법 모두 사용, 역할 분리**
- **코드 docstring**: 코드 품질 기준. 모든 에이전트 프롬프트에 docstring 규칙 포함.
- **트리 문서 (PROJECT_STRUCTURE.md)**: 컨텍스트 전달의 메인 수단. 에이전트가 태스크 시작 시 첫 번째로 읽는 문서.

**트리 문서 파싱 깊이: 레벨 3**
- 파일명 + 클래스/함수명 + 함수 시그니처(인자, 리턴 타입) + docstring 첫 줄 요약

**불일치 방지: StructureUpdater 파이프라인 통합 (`structure/updater.py`)**

```
TDD 파이프라인:
  TestWriter → Implementer → DockerTest → Reviewer → PR
                                                      ↓
                                              그룹 머지 후
                                              StructureUpdater 실행
                                              → 다음 그룹 에이전트에 주입
```

Tree-sitter 기반 다언어 파서로 실제 코드에서 트리 문서를 자동 생성. 에이전트가 프로젝트마다 어떤 언어/프레임워크를 선택하더라도 grammar 패키지 추가만으로 확장 가능. 코드에서 직접 생성하므로 불일치가 구조적으로 불가능.

**현재 지원 언어**: Python, TypeScript, TSX, JavaScript, JSX, C, C++, Rust, Go, Java

**미지원 언어 fallback**: grammar가 없는 확장자 파일은 파싱 건너뛰지 않고 파일명만 포함된 항목으로 PROJECT_STRUCTURE.md에 등재. 에이전트가 해당 파일의 존재를 인지하고 필요 시 직접 읽을 수 있음.

**자동 제외 디렉토리** (`_EXCLUDE_DIRS`): `.git`, `__pycache__`, `node_modules`, `.venv`, `venv`, `.next`, `dist`, `build`, `.cache`, `coverage`, `.agent-workspace`, `.pytest_cache`, `.mypy_cache`, `target`, `.tox` — 빌드 산출물·캐시 디렉토리만 제외. 바이너리 확장자 기준 필터링 없음(에이전트가 `.png`, `.db` 등 임의 파일을 필요로 할 수 있음).

**grammar 선택적 설치**: `pyproject.toml`에는 Python/TS/JS/C/C++/Rust/Go/Java grammar가 기본 포함. 특정 언어가 불필요한 환경에서는 해당 패키지만 제거해도 나머지 언어 파싱은 정상 동작 (`_load_parser()`가 `ImportError` 시 해당 언어를 fallback 처리).

**새 언어 추가 절차** (`structure/updater.py`):
1. `pip install tree-sitter-{언어}` + `pyproject.toml` 의존성 추가
2. `_LANG_MAP`에 확장자 → 언어 키 매핑 추가 (예: `".rb": "ruby"`)
3. `_LANG_LABEL`에 언어 키 → 표시 이름 추가 (예: `"ruby": "Ruby"`)
4. `_load_parser()`에 `elif lang == "ruby":` 분기 추가
5. `_parse_{언어}()` 함수 작성 (클래스·함수 추출 로직)
6. `parse_file()`의 언어 분기에 연결

---

## 5. Discord 핫라인 ✅ 구현 완료

### 5.1 핵심 동기

**"언제 어디서든 멀티 에이전트 시스템과 연결"** — 단순 편의성이 아니라 시스템과의 관계 자체를 바꾸는 핵심 인터페이스.

```
핫라인 없이: 맥북 앞에 앉아서 → 파이프라인 실행 → 결과 대기 → 확인
핫라인 있으면: 태스크 등록 → 외출 → 폰으로 진행상황 알림 → 
             필요시 답변 → 돌아와서 PR 확인
```

### 5.2 Discord 선택 이유

- Discord Bot API가 잘 되어 있음
- 채널별 프로젝트/태스크 분리 가능
- 모바일에서 알림 + 즉시 답장
- 대화 기록 자연스럽게 보존

### 5.3 구현된 기능 (hotline/notifier.py + tools/hotline_tools.py)

**구현 기술**: httpx 기반 Discord REST API 직접 호출 (discord.py 라이브러리 미사용)

**알림 흐름:**
```
파이프라인 시작 → Discord 채널 자동 생성 (DISCORD_BOT_TOKEN, DISCORD_GUILD_ID 설정 시)
태스크 시작/완료/실패 → Discord 알림
파이프라인 종료 → Discord 알림
```

**제어 명령 (Discord 채널 또는 UI):**
```
멈춰 / pause  → 다음 태스크 전 일시정지
계속 / resume → 일시정지 해제 후 재개
중단 / stop   → 파이프라인 즉시 종료
```

**에이전트 질의응답 (ask_user 도구, IMPLEMENTER 전용 — TEST_WRITER에서는 제거됨):**
```
에이전트가 ask_user(question=...) 호출
  → Discord 채널에 질문 전송
  → 사용자가 자유롭게 대화 (오케스트레이터 LLM이 대화 파트너로 참여)
  → "확정" 입력 → 대화 내용 요약 → 에이전트에게 단일 답변 반환
  → "알아서 해" 입력 → 에이전트가 최선의 판단으로 진행
  → Discord 없으면 stdin 폴백
```

결정 사항은 `agent-data/context/decisions.md`에 자동 기록된다.

### 5.4 환경 변수 설정

```
DISCORD_BOT_TOKEN=...   # Discord Bot 토큰 (없으면 Discord 기능 비활성화)
DISCORD_GUILD_ID=...    # Discord 서버 ID
```

**⚠️ 필수 설정: Message Content Intent**
Discord Developer Portal → Bot → Privileged Gateway Intents → **Message Content Intent** 를 반드시 활성화해야 한다. 비활성 상태에서는 REST API(`GET /channels/{id}/messages`)가 메시지 존재는 반환하지만 `content`가 빈 문자열 `""`로 반환되어, 모든 Discord 명령(중단/멈춰/확정/알아서 해)과 ask_user 질의응답이 작동하지 않는다.

### 5.5 안정성 강화 (2026-04-03)

**PauseController 직접 폴링 (리스너 스레드 백업)**:
```
PauseController.attach_notifier(notifier, after_message_id)
  → is_stopped 프로퍼티 호출 시 리스너 스레드와 별도로 직접 Discord API 폴링
  → 2초 쓰로틀링, "중단"/"stop" 키워드 감지 시 _stopped = True
```

**ReactLoop stop_check 연동**:
```
ReactLoop(stop_check=pause_ctrl.is_stopped)
  → 매 LLM 호출 전 stop_check() 호출
  → True 반환 시 StopReason.ABORTED로 즉시 종료
  → ScopedReactLoop도 stop_check/write_deadline 전달
```

**429 Rate Limit 자동 재시도**:
- listen_for_commands, wait_for_reply 모두에서 429 응답의 `retry_after` 값 파싱
- 기본 1초 대기 대신 Discord가 지정한 시간만큼 정확히 대기

**기타 방어 메커니즘**:
- listen_for_commands catch-all 예외 처리 (스레드 사망 방지)
- 리스너 스레드 사망 감지 및 자동 재시작 (_check_listener_alive)
- urgent_callback으로 skip_check 상태와 무관하게 중단 명령 즉시 처리
- scripts/test_discord_read.py — Discord API 응답 직접 검증용 진단 스크립트

### 5.6 확장 버전 (후순위)

| 기능 | 설명 |
|------|------|
| 태스크 승인/거부 | 디스코드에서 버튼으로 직접 승인 |
| PR 요약 전송 | 변경 파일, 주요 내용 요약을 디스코드에 자동 게시 |
| 스크린샷 피드백 | 프론트엔드 태스크에서 시각적 피드백 |
| 파이프라인 트리거 | `/run task-001 task-002` 명령어로 디스코드에서 실행 |

---

## 6. 보고서 체계 ✅ 구현 완료

### 6.1 Task Report (`reports/task_report.py`)

**생성 시점**: 태스크 완료 시 자동 생성  
**저장 형식**: YAML (`agent-data/reports/task-{id}.yaml`)  
호환성: 기존 워크스페이스에 `agent-data`가 없고 `data`만 있으면 `data/reports/*`를 자동 사용한다.
**소비자**: 오케스트레이터(상세), 사람(핫라인 요약), execution_brief 생성

**TaskReport 구조 (`reports/task_report.py`의 실제 dataclass):**
```yaml
task_id: "001"
title: "..."
status: "COMPLETED"          # "COMPLETED" 또는 "FAILED"
completed_at: "2026-04-01T14:30:00"
retry_count: 2
time_elapsed_seconds: 180.5
test_count: 7
test_pass_first_try: false
reviewer_verdict: "APPROVED"
failure_reasons: ["TypeError in line 23"]
reviewer_feedback: "..."
models_used:
  test_writer: "openai/gpt-4.1-mini"
  implementer: "claude/claude-haiku-4-5-20251001"
  reviewer: "gemini/gemini-2.5-flash"
token_usage:
  implementer: {input: 12000, output: 1800, cached_read: 6400, cached_write: 0}
  reviewer:    {input: 2200,  output: 350,  cached_read: 1024, cached_write: 0}
total_cached_read_tokens: 7424
cache_hit_rate: 0.3551
total_tokens: 15420          # input + output + cached_read 합산
cost_usd: 0.0046             # _MODEL_PRICING 테이블 기반 추정 비용
```

**집계 함수 (aggregate()):**
- total, completed, failed, success_rate, first_try_rate
- avg_elapsed_seconds, total_retries, reviewer_approved
- total_tokens, total_cost_usd (전체 합산)

**비용 계산 (orchestrator/report.py):**
- `_MODEL_PRICING`: 주요 모델의 입력/출력 토큰 단가 테이블 (Claude/OpenAI/GLM 일부 등록)
- `_calculate_cost(model, input_tokens, output_tokens)`: 단가 미등록 모델은 0.0 반환
- GET /api/dashboard/summary에서 total_tokens / total_cost_usd 합산 반환
- DashboardPage 비용 MetricCard로 실시간 확인 가능
- `core/token_log.py`: 역할별 per-call 토큰 로그를 JSONL로 별도 저장

**구조화된 메트릭의 중요성**: 이 데이터가 시스템 자기 개선 루프의 기반이 됨.

### 6.2 Weekly Report (orchestrator/weekly.py)

**생성 주기**: 매주 (POST /api/reports/weekly 호출 시)  
**저장 위치**: `agent-data/reports/weekly/{year}-W{week}.md`  
호환성: `agent-data`가 없고 `data`만 있으면 `data/reports/weekly/*`를 자동 사용한다.

**Daily Summary를 폐기한 이유**: 핫라인으로 태스크별 알림이 이미 오므로, Daily Summary의 단순 나열은 중복. Weekly Report가 데이터 축적 후 의미 있는 분석을 제공하는 최소 주기.

**핵심 섹션:**
1. 진행 요약 (완료/실패/보류)
2. 주요 성과
3. **이슈 & 패턴** — 단순 숫자가 아닌 시스템 개선 포인트 제안
4. **비용** — 모델별 비용, 합계, 태스크당 평균
5. **추이** — 전주 대비 주요 지표 증감
6. 다음 주 계획

### 6.3 Milestone Report (orchestrator/milestone.py)

**생성 시점**: 파이프라인 전체 완료 시 자동 생성  
**저장 위치**: `agent-data/reports/milestones/{timestamp}.md`  
호환성: `agent-data`가 없고 `data`만 있으면 `data/reports/milestones/*`를 자동 사용한다.
**소비자**: 사람, 대시보드

### 6.4 컨텍스트 압축

**보고서 계층 압축**(`Task Report → Weekly → Milestone`)은 그대로 유지한다. 다만 긴 ReAct 세션 자체의 컨텍스트 폭주는 별도 문제이므로, 현재는 `ReactLoop`에 **semantic auto-compaction**이 추가되어 있다.

```
estimate_tokens(messages) > threshold(기본 30k)
  → system + 첫 user 태스크 prefix 보존
  → 중간 구간을 작은 LLM으로 요약
  → 최근 N turns 유지
  → 단일 user summary 메시지로 치환
```

- `core/compactor.py`: drop 구간 계산, tool_use/tool_result pair 무결성 유지
- `core/loop.py`: `_maybe_compact()`, 2-iteration cooldown, call_log 기록
- `DISABLE_COMPACTION=1`: 즉시 비활성화 킬스위치

즉, **문서 계층 압축은 운영 리포트용**, **semantic auto-compaction은 런타임 루프용**으로 역할이 분리되어 있다.

---

## 7. 회의 인터페이스 확장 ✅ 구현 완료

### 7.1 회의 타입 분리

**프로젝트 회의**: "무엇을 만들 것인가"
- 특정 프로젝트의 기능, 설계, 우선순위 논의
- context_doc + execution_brief + PROJECT_STRUCTURE.md 주입
- 프로젝트마다 별도 존재

**시스템 회의**: "어떻게 더 잘 만들 것인가"
- 멀티 에이전트 시스템 자체의 성능, 비용, 프로세스 개선
- 전체 프로젝트 통합 메트릭 + 패턴 분석 주입
- 시스템 하나에 대해 하나
- **주기: 매주** (Weekly Report 생성 직후가 적절)

**UI 반영 (MeetingApp.tsx):**
```
meetingType: 'project' | 'system'
```

### 7.2 회의 시작 시 컨텍스트 주입 전략

**기존 문제**: Phase 1의 "이전 회의 로드"는 대화 히스토리를 통째로 로드. 파이프라인 결과는 대화 밖에서 일어난 일이라 히스토리에 끼워넣을 수 없음.

**해결: 두 가지 회의 모드와 컨텍스트 분리**

```
"이전 회의 이어서" (기존 유지):
  → 대화 히스토리 통째로 로드
  → 단기 연속 작업용 (어제 회의 이어서)

"주간 회의 시작" (신규):
  → 자동 주입 (요약):
    ├── context_doc.md (이전 회의 결정사항)
    ├── execution_brief.md (파이프라인 결과 요약) ← POST /api/execution-brief 로 생성
    └── PROJECT_STRUCTURE.md (현재 코드 구조)  ← GET /api/project-structure
  → 이전 대화 히스토리는 로드하지 않음
  → LLM에게 필요한 건 결정의 결과이지, 결정 과정 전체가 아님
```

**execution_brief 생성 시점**: "주간 회의 시작" 버튼 클릭 시 즉시 생성. 마지막 회의 이후의 Task Report들을 백엔드가 수집 → LLM이 요약 → 회의 시작 전에 시스템 프롬프트에 주입.

### 7.3 주간 루틴 예시

```
월요일: 프로젝트 회의 → tasks.yaml 생성 → 승인 → 파이프라인 시작
화~금: 파이프라인 실행, 핫라인으로 알림/질의응답
금요일: Weekly Report 자동 생성
토/일: 시스템 회의 → 프로세스 개선 결정 → 다음 주 프로젝트 회의에 반영
```

---

## 8. 시스템 자기 개선 루프

### 8.1 단계별 진화

```
레벨 1 (5단계 ✅ 완료): 데이터 수집 + 패턴 보고 → 사람이 판단
  메트릭 수집 → Weekly Report 패턴 분석 → 시스템 회의에서 결정

레벨 2 (향후): 패턴 발견 → 자동 개선 시도
  "재시도율 높은 태스크 공통점 분석 → 다음 criteria 작성 방식 자동 조정"

레벨 3 (향후): 메타 최적화
  "프롬프트 A vs B로 같은 태스크 실행 → 성공률/비용 비교 → 자동 채택"
```

**핵심**: 레벨 1의 데이터 수집이 레벨 2, 3의 기반. 처음부터 구조화된 메트릭을 쌓아두는 것이 중요.

---

## 9. 5단계 스코프 (완료 기준)

### 9.1 5단계-A (핵심) ✅ 완료

| 구성요소 | 내용 | 상태 |
|----------|------|------|
| FastAPI 백엔드 | API 프록시, 태스크/파이프라인 관리 API, 보고서 조회 API | ✅ |
| 태스크 초안 생성 | context_doc → LLM → tasks.yaml, 의존성 자동 계산, UI에서 확인/수정/승인 | ✅ |
| 파이프라인 확장 | base branch 머지 기반 의존성 처리, StructureUpdater, Task Report 자동 생성 | ✅ |
| Discord 핫라인 (최소) | 시작/완료 알림, 질문→Discord→답변→파이프라인 양방향 텍스트 | ✅ |
| 회의 인터페이스 확장 | 회의 타입 분리(프로젝트/시스템), "주간 회의 시작" + execution_brief 주입 | ✅ |
| 보고서 체계 | Task Report(YAML), Weekly Report(마크다운), Milestone Report | ✅ |

### 9.2 5단계-B (확장) ✅ 완료

| 구성요소 | 내용 | 상태 |
|----------|------|------|
| 태스크 타입 분기 | frontend 태스크 파이프라인 제외 (task_type 필드) | ✅ |
| 핫라인 확장 | 버튼 인터랙션, `/run` 명령어, PR 요약, 스크린샷 피드백 | 미구현 (후순위) |
| Monthly Report | Weekly 축적 후 월간 요약 | 미구현 (필요 시) |

---

## 10. 5단계-A 구현 순서 (완료)

```
Step 1: FastAPI 백엔드 + API 프록시 ✅
  → 프론트엔드가 백엔드 경유하도록 전환
  → dangerouslyAllowBrowser 제거
  → 기존 Phase 1이 백엔드 위에서 동작 확인

Step 2: 파이프라인 확장 ✅
  → StructureUpdater 추가
  → base branch 머지 후 다음 태스크 workspace 생성
  → Task Report + 메트릭 자동 저장
  → 파이프라인 실행/상태 API 연결
  → orchestrator/report.py, resolve_execution_groups(), backend/routers/pipeline.py

Step 3: 태스크 초안 생성 ✅
  → context_doc → LLM → tasks.yaml 변환
  → 의존성 자동 계산 (위상 정렬)
  → UI에서 태스크 목록 확인/수정/승인
  → POST /api/tasks/draft (비동기 job), GET /api/tasks/draft/{job_id}

Step 4: 회의 인터페이스 확장 ✅
  → 회의 타입 (프로젝트/시스템) 분리
  → execution_brief 자동 생성 + 주입 (POST /api/execution-brief)
  → PROJECT_STRUCTURE.md 조회 (GET /api/project-structure)

Step 5: Discord 핫라인 ✅
  → hotline/notifier.py (DiscordNotifier)
  → tools/hotline_tools.py (ask_user, LLM 대화 파트너)
  → PauseController (일시정지/재개/중단)
  → POST /api/pipeline/control/{job_id}

Step 6: 보고서 체계 ✅
  → orchestrator/weekly.py — ISO 주차 집계 + LLM 마크다운 생성
  → POST/GET /api/reports/weekly[/{year}/{week}]
  → orchestrator/milestone.py — 파이프라인 완료 시 마일스톤 보고서
```

### 10.1 5단계 구현 주체

**5단계 인프라 자체는 Claude Code + 사람이 직접 구현.** 멀티 에이전트 시스템이 아직 미완성인 상태에서 자기 자신을 만드는 것은 닭과 달걀 문제. 유틸리티 모듈 프로젝트는 시스템 검증용 테스트 프로젝트로 활용.

---

## 11. 첫 실제 프로젝트: 유틸리티 모듈 (셀프 호스팅 검증) ✅ 완료

### 11.1 프로젝트 선정 과정

**검토 후 보류: SNN 연구 프로젝트**
- GPU 훈련 필요 (Docker 샌드박스에서 CUDA 실행 불가)
- 하이퍼파라미터 탐색이 핵심 (코드 작성보다 실험 설계)
- 기존 코드베이스 확장 (새로 만드는 것이 아님)
- 결과 해석에 주관적 판단 필요
- → TDD 파이프라인과 미스매치 → SNN은 시스템 안정화 후 진행

**채택: 시스템 자체의 유틸리티 모듈**
- 순수 Python, 명확한 입출력, 테스트 용이
- 만들고 나면 바로 시스템에 통합
- 모듈 간 의존성이 적당히 존재 → 의존성 처리 검증
- "시스템이 자기 자신의 부품을 만든다" = 강력한 셀프 호스팅 검증

### 11.2 대상 모듈 (5개) ✅ 모두 완료

1. **메트릭 수집기** (`metrics/collector.py`) — Task Report 저장/로드/집계 (34 tests APPROVED)
2. **Weekly Report 생성기** (`reports/weekly.py`) — 주간 마크다운 보고서 + 패턴 분석 (39 tests APPROVED)
3. **PROJECT_STRUCTURE.md 생성기** (`structure/updater.py`) — Tree-sitter 다언어 파싱 → 트리 문서 (52 tests APPROVED)
4. **execution_brief 생성기** (`reports/execution_brief.py`) — 회의 시작 시 주입할 실행 요약 (35 tests APPROVED)
5. **태스크 의존성 계산기** (`orchestrator/dependency.py`) — 위상 정렬 기반 실행 순서 결정 (27 tests APPROVED)

### 11.3 모듈 간 의존성

```
task-001: 메트릭 수집기        (의존 없음)
task-003: 구조 생성기          (의존 없음)
task-005: 의존성 계산기        (의존 없음)
    ↓
task-002: Weekly Report 생성기  (→ task-001)
task-004: execution_brief 생성기 (→ task-001)
```

실행 순서: `001 → 003 → 005 → 002 → 004` (그룹 1 내 순서는 임의)

**셀프 레퍼런스**: 이 의존성 구조 자체가 task-005 (의존성 계산기)의 테스트 케이스로 활용.

---

## 12. 기술 스택 (5단계 추가분)

| 기능 | 기술 | 선택 이유 |
|------|------|-----------|
| 백엔드 서버 | FastAPI + Uvicorn | 가볍고 빠름, Python 생태계 통합 용이, 비동기 지원 |
| API 프록시 | FastAPI → 다양한 LLM API | 프론트엔드에서 API 키 제거, 프로바이더 교체 용이 |
| LLM 추상화 | llm/ 패키지 (claude/openai/glm/ollama) | 단일 인터페이스로 프로바이더 무관하게 동작 |
| 핫라인 | Discord Bot (hotline/notifier.py) | 모바일 알림, 채널 분리, 풍부한 Bot API |
| Task Report 저장 | YAML (metrics/collector.py) | tasks.yaml과 일관성 |
| Weekly Report | 마크다운 | LLM이 직접 소비 가능, 사람도 읽기 쉬움 |
| 코드 파싱 | Tree-sitter (structure/updater.py) | 다언어 지원 — Python/TS/JS/C/C++/Rust/Go/Java 기본 포함, grammar 패키지 추가만으로 확장 가능 |
| 그래프 알고리즘 | Kahn's algorithm (resolve_execution_groups) | 단순, 외부 라이브러리 불필요 |
| 병렬 실행 | ThreadPoolExecutor (orchestrator/run.py) | Python 표준 라이브러리 |
| Git 병렬 안전 | git worktree (orchestrator/git_workflow.py) | 메인 repo HEAD 불변, 여러 태스크 동시 작업 가능 |

---

## 13. 미결 사항 (현재 상태)

| 항목 | 현재 상태 | 결정 시점 |
|------|-----------|-----------|
| FastAPI 엔드포인트 상세 스펙 | ✅ 구현 완료 — chat/tasks/pipeline/reports/dashboard/discord 전 엔드포인트 | 완료 |
| Discord Bot 기술 스택 | ✅ httpx 기반 hotline/notifier.py 구현 완료 (discord.py 미사용) | 완료 |
| Discord Message Content Intent | ✅ 필수 — Developer Portal에서 활성화 필요. 미활성 시 content 빈 문자열 반환 | 확인 완료 |
| PauseController 직접 폴링 | ✅ 리스너 스레드 백업으로 직접 Discord API 폴링 구현 | 완료 |
| ReactLoop stop_check | ✅ 매 LLM 호출 전 콜백 체크 → StopReason.ABORTED로 즉시 종료 | 완료 |
| 429 Rate Limit 처리 (Discord) | ✅ retry_after 파싱 후 자동 대기 (listen_for_commands + wait_for_reply) | 완료 |
| 429 Rate Limit 처리 (LLM API) | ✅ `llm/rate_limiter.py` + openai/glm/gemini 재시도 통합 | 완료 |
| git push 스킵 토글 | ✅ RunRequest.no_push, GitWorkflow.run(no_push), TaskDraftPanel UI 토글 (📦 로컬만 / 🚀 push+PR) | 완료 |
| LLM 토큰·비용 추적 | ✅ PipelineMetrics.token_usage → TaskReport.total_tokens/cost_usd → 대시보드 MetricCard | 완료 |
| Reviewer CHANGES_REQUESTED 오분류 | ✅ LOGIC_ERROR로 재분류; reviewer.md read_file 해석 규칙; ScopedReactLoop _readonly_warn_threshold=None | 완료 |
| TestWriter WRITE_LOOP | ✅ write_deadline 초과 감지 + 재시도 로직 (scoped_loop.py + test_writer.md 지침) | 완료 |
| 회의 타입별 LLM 시스템 프롬프트 차이 | ✅ MeetingApp meetingType으로 분리 구현 | 완료 |
| execution_brief LLM 프롬프트 | ✅ _BRIEF_SYSTEM 프롬프트 구현 완료 (backend/routers/reports.py) | 완료 |
| 태스크 초안 생성 LLM 프롬프트 | ✅ `_DRAFT_SYSTEM_PROMPT`가 `language` 필드, 4개 섹션 description, 언어별 파일 규칙을 강제 | 완료 |
| 크로스 언어 target_files | ✅ 언어별 확장자는 유지, `src/` 제거 후 1-level 경로 보존, 깊은 경로만 basename 정리 | 완료 |
| Gemini 프로바이더 | ✅ GeminiClient, `GEMINI_API_KEY`, 모델 목록/선택 UI 반영 | 완료 |
| semantic auto-compaction | ✅ 긴 ReactLoop 히스토리를 자동 요약, prefix/cache 안정성 유지 | 완료 |
| 의존성 pre-check | ✅ 선행 DONE 태스크 스킵 + inject fallback (git diff) — auto_merge 없이 정상 동작 | 완료 |
| 테스트 통과 판정 보정 | ✅ OK: 접두어 + pytest "N passed" (failed/error 없음) 패턴 인식 | 완료 |
| depends_on 필드 정확한 스펙 | ✅ 문자열 리스트 (task ID) 확정, YAML로 저장 | 완료 |
| 파이프라인 동기/비동기 실행 | ✅ POST /api/pipeline/run → job_id 비동기 실행, SSE 스트리밍 | 완료 |
| 태스크 complexity 라벨 | ✅ Task.complexity (simple/standard/complex) + 3단계 판정 + auto_select_by_complexity | 완료 |
| Momus Critique 시스템 | ✅ POST /api/tasks/critique + GET/{id} + POST /critique/apply, TaskDraftPanel UI 통합 | 완료 |
| Quality Gate | ✅ orchestrator/quality_gate.py — BLOCKING/WARNING 룰, Python AST 검사, TestWriter 재시도 연동 | 완료 |
| APPROVED_WITH_SUGGESTIONS | ✅ 스타일 지적이 PR을 막지 않음, 대시보드 "승인" 카운트 포함 | 완료 |
| collect-only 게이트 | ✅ pytest/jest/gradle 사전 검사, COLLECTION_ERROR/NO_TESTS_COLLECTED 분류 | 완료 |
| intervention 자동 분해 | ✅ RunRequest.intervention_auto_split, TaskStatus.SUPERSEDED, 하위 태스크 tasks.yaml 추가 | 완료 |
| 역할별 압축 프리셋 | ✅ agents/roles.py 프리셋 + RunRequest role_compaction_tuning_* 파라미터 | 완료 |
| 이상치 탐지 | ✅ _detect_outlier_tasks() μ+2σ 기반, dashboard API + UI 하이라이트 | 완료 |
| 의존성 그래프 모달 | ✅ DependencyGraphModal.tsx — DAG 시각화 + 순환 참조 감지 + 편집 | 완료 |
| 태스크 초안 모델 런타임 설정 | ✅ get_task_draft_model() / get_redesign_model() — UI에서 모델 선택 가능 | 완료 |
| 헬스 체크 | ✅ GET /api/health → {"status": "ok"} | 완료 |
| 아이맥 서버 Docker 호환성 | macOS Monterey 경계선, 실측 필요 | 서버 이전 검토 시 |
| Weekly Report 패턴 감지 임계값 | 합리적 기본값으로 시작, 실데이터 후 조정 | 첫 2~3주 운영 후 |
| DB 전환 | JSON/YAML → SQLite or PostgreSQL | 데이터 복잡도 증가 시 |
| CI/CD 통합 | GitHub Actions 유력 | Phase 3 이후 |
| 핫라인 확장 (버튼, /run 명령) | 미구현 — 기본 텍스트 양방향만 | 필요 시 |
| Monthly Report | 미구현 — Weekly 축적 후 추가 | 필요 시 |
| Quality Gate 비-Python 지원 | Python AST만 지원, 비-Python 언어는 파일 존재 여부만 | 필요 시 |

---

## 14. Phase 3 신뢰성 강화 (2026-04-18) ✅ 완료

### 14.1 git push 스킵 토글

원격 저장소 공개 없이 로컬에서만 결과를 확인하고 싶을 때 사용.

```
RunRequest.no_push = True
  → GitWorkflow.run(no_push=True)
  → 로컬 브랜치 생성 + 커밋만 수행
  → git push origin / gh pr create 건너뜀
```

**UI**: TaskDraftPanel 헤더의 `📦 로컬만` / `🚀 push+PR` 토글 버튼.

### 14.2 LLM 토큰·비용·캐시 추적

```
TDDPipeline._accumulate_tokens()
  → PipelineMetrics.token_usage 누적 (역할별 input/output/cached_read/cached_write)
  → TaskReport 저장 시 total_tokens / cost_usd / cache_hit_rate 계산
      orchestrator/report.py _MODEL_PRICING 테이블:
        claude-opus-4-6, claude-haiku-4-5, gpt-4.1, gpt-4.1-mini 등 단가 등록
  → core/token_log.py 가 per-call JSONL 로그 저장
  → GET /api/dashboard/summary 에 total_tokens / total_cost_usd 합산 반환
  → DashboardPage 비용 MetricCard로 가시화
```

### 14.3 LLM API rate limit 방어

병렬 에이전트가 동일 모델에 동시 요청할 때 발생하는 429와 TPM/RPM 초과를 클라이언트 측에서 선제적으로 제어한다.

```python
# llm/rate_limiter.py
bucket = get_bucket(provider, model)
handle = bucket.reserve(estimate_tokens)
...
bucket.reconcile(handle, actual_tokens)

# 429 발생 시
bucket.poison(retry_after)
```

- 60초 sliding window 기반 TPM/RPM 예약
- `(provider, model)`별 독립 버킷
- 429 이후 모든 스레드를 공통 해제 시각까지 지연(poison)
- openai / glm / gemini 클라이언트에 통합

### 14.4 OpenAI/GLM prompt cache 안정화

```python
messages[0] = system
messages[1] = first user task
messages[2..] = assistant/tool turns
  → prefix byte-identical 유지
  → cached_tokens 수집
```

- OpenAI/GLM 메시지 직렬화의 dict key order 고정
- tool_call arguments 를 `sort_keys=True`로 canonicalize
- `scripts/verify_cache_hit.py`로 cache hit 검증 가능

### 14.5 Gemini 프로바이더 추가

```
llm/gemini_client.py
  → google-genai SDK 기반 클라이언트
  → tool schema bridge (OpenAI tools → Gemini function_declarations)
  → Gemini thinking 모델용 thought_signature round-trip 지원
```

환경 변수는 `GEMINI_API_KEY` 또는 `GOOGLE_API_KEY`.

### 14.6 Reviewer 신뢰성 개선

```
orchestrator/intervention.py:
  CHANGES_REQUESTED → LOGIC_ERROR 분류
  (기존 ENV_ERROR 오분류 방지 → GIVE_UP 대신 RETRY 시도)

orchestrator/pipeline.py:
  Reviewer 출력 파싱 실패 / LLM_ERROR → verdict=ERROR
  (코드 반려와 인프라 장애를 분리)

agents/prompts/reviewer.md:
  read_file 결과 해석 규칙 추가
  (툴 응답이 에러인 경우 vs 파일 내용인 경우 구분)

agents/scoped_loop.py:
  Reviewer 역할: _readonly_warn_threshold = None
  (쓰기 도구 없는 역할에서 불필요한 경고 제거)
```

### 14.7 Implementer/TestWriter 신뢰성 개선

```
orchestrator/workspace.py:
  repo에 아직 없는 target_file 은 빈 스켈레톤을 workspace/src/ 에 선주입

orchestrator/pipeline.py:
  Implementer 종료 후 missing_or_empty_target_files() 검사
  → 빈 파일/누락 파일이면 [TARGET_MISSING]으로 즉시 실패

agents/scoped_loop.py:
  write_deadline 설정 — TestWriter가 지정된 시간 내 파일을 쓰지 않으면
  WRITE_LOOP 감지 → 에이전트 재시작 (최대 재시도 횟수 내)

agents/prompts/test_writer.md:
  WRITE_LOOP 발생 시 재시도 지침 추가
```

### 14.8 Docker 실행 안정성

- Python 이미지에서 workspace 루트 `requirements.txt` 자동 설치
- `torch`, `numpy`, `scipy`, `h5py`를 기본 이미지에 포함해 ML 태스크의 초기 실패 감소
- 언어별 기본 프레임워크는 `LANGUAGE_TEST_FRAMEWORK_MAP`으로 자동 선택

### 14.9 Semantic auto-compaction

```
core/loop.py
  context_pruner 우선
  없으면 semantic compaction
  마지막 fallback은 sliding window trim
```

- 임계치 기본값 30k tokens
- system + 첫 user prefix 보존으로 cache 안정성 유지
- 최근 turns는 keep_last_n으로 유지
- 2-iteration cooldown으로 연쇄 compaction 방지
- `DISABLE_COMPACTION=1` 킬스위치 제공

---

## 15. Phase 4 품질·자동화 강화 (2026-04-21) ✅ 완료

### 15.1 태스크 complexity 라벨

각 태스크에 복잡도 라벨을 부여해 모델 자동 선택에 활용한다.

```
Task.complexity: "simple" | "standard" | "complex" | None

3단계 판정 절차 (_DRAFT_SYSTEM_PROMPT 내):
  1단계 — 하드 규칙 (target_files 수 / depends_on 수 / criteria 수의 max tier)
  2단계 — 보조 규칙 (비표준 API, 동시성, 도메인 지식 등 2개+ 해당 시 승격)
  3단계 — 기본값 standard

RunRequest.auto_select_by_complexity = True
  → Task.complexity 라벨로 모델 자동 매핑
  → role_models는 complexity 매핑의 상위 override 유지
  → PipelineModelModal: "복잡도 자동 선택" 토글로 UI에서 제어
```

### 15.2 Momus Critique 시스템

파이프라인 실행 전 태스크 초안의 구조적 결함을 사전에 발견하는 LLM 기반 검토 시스템.

```
POST /api/tasks/critique
  → 백그라운드에서 LLM이 5개 카테고리 검토
  → verdict: APPROVED | NEEDS_REVISION
  → issues: [{task_id, severity(ERROR|WARNING), category, message}]

카테고리:
  scope       — context_doc 범위 이탈 여부
  sizing      — target_files 3개 초과 여부
  testability — acceptance_criteria 검증 가능성
  dependency  — depends_on 정합성 + 순환 참조
  description — 4개 섹션 헤더 누락 + 100자 미만

POST /api/tasks/critique/apply
  → critique.issues + suggestions를 근거로 최소 수정만 적용
  → 수정된 태스크만 updated_tasks에 포함 (변경 없는 태스크 제외)
  → dangling depends_on 자동 제거

UI (TaskDraftPanel):
  "🦉 Momus 검토" 버튼 → critique 실행 → 결과 배너 표시
  태스크별 인라인 issue 표시 (ERROR: 빨강, WARNING: 노랑)
  "제안 적용" 버튼 → critique/apply → 태스크 일괄 업데이트
  적용 완료 후 버튼 비활성화 (태스크 재수정 전까지)
  태스크 수동 변경 시 이전 검토 결과 자동 초기화 + 리셋 알림
  파이프라인 실행 버튼: critique 미실행 시 확인 다이얼로그 표시
```

### 15.3 Quality Gate

TestWriter 종료 직후 1회 실행하여 테스트 파일의 형식적 유효성을 검사한다.

```
orchestrator/quality_gate.py

특성:
  - LLM 호출 없음 → 결정적·고속·단위 테스트 용이
  - Python AST + 파일시스템 기반 순수 함수
  - BLOCKING 룰 실패 → TestWriter 재시도 트리거
  - WARNING 룰 실패 → 진행, TaskReport에 기록

Verdict:
  PASS    — 모든 룰 통과
  WARNING — BLOCKING 전부 통과 + WARNING 룰 일부 실패
  BLOCKED — BLOCKING 룰 하나라도 실패

비-Python 언어: 현재 파일 존재 여부만 검사 (AST 지원 예정)
```

### 15.4 APPROVED_WITH_SUGGESTIONS verdict

```
Reviewer 판정 종류 확장:
  APPROVED                — 결함 없음, PR 생성
  APPROVED_WITH_SUGGESTIONS — 스타일·개선 제안 있음, PR 생성 (CHANGES_REQUESTED 아님)
  CHANGES_REQUESTED       — 기능적 결함, PR 생성 (사람이 최종 판단)
  ERROR                   — 인프라 장애, verdict 파싱 실패

대시보드 "승인" 카운트: APPROVED + APPROVED_WITH_SUGGESTIONS 모두 포함
```

### 15.5 collect-only 게이트

```
pytest / jest / gradle 테스트 수집 사전 검사 (본 실행 전 별도 단계)

COLLECTION_ERROR       — 수집 자체가 실패 (import 오류, 문법 오류)
NO_TESTS_COLLECTED     — 수집은 성공했지만 테스트 함수가 0개

수집 실패 시 즉시 분기:
  → Implementer 재시도 없이 바로 FailureType 분류
  → 오케스트레이터 개입 여부 결정
```

### 15.6 intervention 자동 분해 (auto-split)

```
RunRequest.intervention_auto_split = True
  → 최종 실패 (max_orchestrator_retries 초과) 직전
  → LLM이 태스크를 2~3개 하위 태스크로 분해
  → 원본 태스크: TaskStatus.SUPERSEDED (YAML에 보존)
  → 하위 태스크: tasks.yaml에 추가 (다음 파이프라인 실행 시 픽업)

관련 파일:
  orchestrator/task_redesign.py — split 기능
  orchestrator/workspace.py     — 하위 태스크 스켈레톤 주입
  orchestrator/intervention.py  — 다언어 스켈레톤 파일 생성
```

### 15.7 역할별 압축 프리셋

```
agents/roles.py:
  ROLE_COMPACTION_PRESET_BALANCED (기본)
  ROLE_COMPACTION_PRESET_AGGRESSIVE
  ROLE_COMPACTION_PRESET_DEFAULT
  역할별 압축 임계값 독립 정의

RunRequest:
  role_compaction_tuning_enabled: bool
  role_compaction_tuning_preset: str
  role_compaction_tuning_overrides: dict (예: {"implementer": "aggressive"})
```

### 15.8 이상치 탐지 (대시보드)

```
backend/routers/dashboard.py: _detect_outlier_tasks()
  → 실행 시간 / 재시도 횟수 기준 μ+2σ 초과 태스크 탐지
  → GET /api/dashboard/summary 에 outlier_tasks 목록 포함
  → DashboardPage: 이상치 태스크 UI 하이라이트
```

### 15.9 의존성 그래프 모달

```
frontend/src/components/DependencyGraphModal.tsx
  → 의존성 DAG 시각화 (방향 그래프)
  → 순환 참조 시 빨간 강조
  → TaskDraftPanel: "의존성 그래프" 버튼으로 접근
                    순환 참조 시 "⚠ 의존성 그래프 수정" 버튼
  → 편집 후 "적용"으로 tasks 업데이트
```

---

## 16. 탐색 후 폐기된 방향

| 방향 | 폐기 이유 |
|------|-----------|
| 완전 파일 기반 연결 (백엔드 없이) | 수동 워크플로우 문서화에 불과, Phase 2 CLI와 사용 경험 차이 없음 |
| 풀스택 통합 백엔드 (WebSocket, 인증 등) | 시기상조. 인증, 프로세스 관리, WebSocket 등 한꺼번에 해결해야 하는 것이 너무 많음 |
| 프론트엔드를 멀티 에이전트로 구현 | TDD가 시각적 판단에 부적합, "좋아 보이는가"를 테스트로 검증 불가. Claude Code + 사람이 최적 |
| Daily Summary 보고서 | 핫라인 알림으로 일일 진행상황은 이미 파악 가능. Daily Summary의 단순 나열은 중복. Weekly가 최소 유의미 분석 주기 |
| SNN 프로젝트를 첫 실제 프로젝트로 | GPU 훈련 필요, 결과 해석 주관적, 하이퍼파라미터 중심, TDD 파이프라인과 미스매치 |
| 매 응답마다 JSON 컨텍스트 문서 반환 | 회의 대화 흐름을 방해, 별도 호출로 분리 (기존 설계에서 이미 확정) |
| PROJECT_STRUCTURE 레벨 4 (전체 docstring) | 문서 비대화, 토큰 낭비 |
| PROJECT_STRUCTURE 레벨 2 (시그니처만) | 함수 역할 파악 불충분, 결국 파일을 열어야 함 |
| Python `ast` 모듈로 StructureUpdater 구현 | Python 전용 — 에이전트가 다른 언어를 선택할 경우 파싱 불가. Tree-sitter로 대체 |
| Task Report JSON 형식 | tasks.yaml과의 일관성을 위해 YAML 선택 |
| Anthropic 전용 설계 | 멀티 프로바이더 지원으로 확장 — claude/openai/glm/ollama 모두 동일 인터페이스 |
| dangerouslyAllowBrowser (프론트엔드 직접 API 호출) | 백엔드 API 프록시로 전환하여 API 키 서버 측 관리 |
