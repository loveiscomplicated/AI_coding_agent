# Multi-Agent Development System

> 프로젝트 문서 v2.1 | 2026-04-18 — Gemini 프로바이더 추가, 언어별 task draft/language 필드 정착, 클라이언트 측 TPM·RPM rate limiter, prompt cache 관측성, semantic auto-compaction, Reviewer/Implementer 신뢰성 강화

---

## 1. 프로젝트 개요

### 1.1 목표

AI 에이전트 팀을 활용한 소프트웨어 개발 파이프라인 구축. 사람(나)은 방향 설정과 핵심 의사결정에 집중하고, 구현·테스트·리뷰는 에이전트가 수행한다.

### 1.2 핵심 원칙

- **선별적 인간 감독**: 완전 자동화가 아닌, 사람이 개입할 지점을 명확히 설계
- **TDD 강제**: 모든 에이전트는 테스트 → 구현 → 리뷰 순서를 따름
- **격리된 실행**: 에이전트 하나의 실패가 시스템 전체에 영향을 주지 않음
- **점진적 복잡도**: 단순한 것부터 동작시키고, 검증 후 확장

### 1.3 Non-Goals

- 에이전트의 자율적 프로덕션 배포 (반드시 사람 승인 필요)
- 범용 AI 개발 플랫폼 구축 (내 프로젝트 개발용에 집중)
- 에이전트 간 직접 통신 (모든 조율은 오케스트레이터를 경유)

---

## 2. 전체 아키텍처

### 2.1 시스템 구조

```
나 (사람)
 ↕  회의 / 보고서 검토 / PR 승인
중앙 오케스트레이터 (설정 가능한 LLM)
 ↕  태스크 분배 / 결과 수집 / 컨텍스트 압축
┌──────────────────────────────────┐
│  Agent 1    Agent 2    Agent N   │  ← 설정 가능한 로컬/외부 모델
│  [샌드박스]  [샌드박스]  [샌드박스] │
│  테스트 → 코드 → 리뷰 (순차)     │
└──────────────────────────────────┘
 ↓  PR 생성 + 승인 대기
실제 코드베이스 (Git)
```

### 2.2 각 레이어 역할

| 레이어 | 담당 | 모델 | 비용 특성 |
|--------|------|------|-----------|
| 사람 | 방향 설정, 최종 승인, 예외 처리 | - | 시간 비용만 |
| 오케스트레이터 | 태스크 분해, 컨텍스트 관리, 보고서 생성 | 설정 가능 (기본: claude-opus-4-6) | 중간 비용, 저빈도 |
| 실행 에이전트 | 코드 작성, 테스트 생성, 코드 리뷰 | 설정 가능 (기본: claude-haiku-4-5-20251001) | 저비용, 고빈도 |

모델은 환경 변수(`LLM_PROVIDER`, `LLM_MODEL_FAST`, `LLM_MODEL_CAPABLE`)로 설정한다.
필요하면 `provider_fast`, `provider_capable`, `role_models`로 역할별 프로바이더/모델을 분리할 수 있다.
지원 프로바이더: `claude`, `openai`, `glm`, `ollama`, `gemini`.

### 2.3 에이전트 샌드박스 구조

각 에이전트는 격리된 환경에서 실행된다.

- **파일시스템**: 태스크별 독립 작업 디렉토리, 코드베이스는 읽기 전용 마운트
- **네트워크**: 필요한 API 엔드포인트만 화이트리스트
- **실행 시간**: 태스크별 타임아웃 설정 (무한 루프 방지)
- **리소스**: CPU/메모리 제한으로 다른 에이전트에 영향 차단

### 2.4 TDD 파이프라인 (에이전트 내부 흐름)

```
태스크 수신
 → Agent A: 테스트 코드 작성
 → Agent B: 테스트를 통과하는 구현 코드 작성
 → Agent C: 코드 리뷰 (A, B와 다른 에이전트)
 → 샌드박스에서 테스트 실행
 → 통과 시 PR 생성 / 실패 시 Agent B로 회귀
```

리뷰어를 구현자와 분리하는 것이 핵심이다. 같은 에이전트가 작성하고 리뷰하면 편향이 발생한다.

---

## 2.5 Phase 2 상세 설계 ✅ 구현 완료

### 2.5.1 전체 데이터 흐름

```
agent-data/tasks.yaml (수동 정의 또는 Sonnet 자동 생성 후 승인, legacy data/tasks.yaml 자동 호환)
    │
    ▼ orchestrator/run.py (또는 백엔드 API)
[Task 목록] ──► 의존성 그룹 계산 ──► 진행
    │
    ▼ for each Task:
    │
    ├─► PRE-CHECK: 의존성 산출물 확인
    │       - 선행 태스크 DONE → 스킵 (산출물은 브랜치에 존재, inject 시 git show로 읽음)
    │       - 선행 태스크 미완료 → filesystem 확인 → 없으면 [DEPENDENCY_MISSING] 즉시 실패
    │       - inject_dependency_context: target_files 경로 실패 시 git diff fallback
    │
    ├─► STEP 1: TestWriter (LLM_MODEL_FAST + ScopedReactLoop)
    │       input:  task.description + acceptance_criteria + enriched description (선행 산출물 정보)
    │       output: workspace/tests/ 에 language/test_framework 기반 테스트 파일 작성
    │       tools:  read_file, write_file, list_directory, search_files
    │
    ├─► STEP 2: Implementer (LLM_MODEL_FAST + ScopedReactLoop)
    │       input:  task + 테스트 파일들
    │       output: workspace/src/ 에 구현 파일 작성
    │       tools:  read_file, write_file, edit_file, list_directory, search_files
    │
    ├─► STEP 3: DockerTestRunner
    │       input:  workspace/ 디렉토리 + task.language + task.test_framework
    │       output: RunResult (pass/fail, stdout, summary, failed_tests)
    │       ─────────────────────────────────────────────
    │       Python 이미지: requirements.txt 자동 설치, torch/numpy/scipy/h5py 기본 포함
    │       컴파일/비표준 런타임: setup.sh 또는 언어별 Docker 이미지 사용
    │       FAIL → Implementer 재시도 (MAX_RETRIES=2회, 이전 오류 포함)
    │       FAIL (오케스트레이터 개입) → Sonnet 분석 → RETRY(힌트) or GIVE_UP
    │       PASS → 다음 단계
    │
    ├─► STEP 4: Reviewer (LLM_MODEL_FAST + ScopedReactLoop, 읽기 전용)
    │       input:  task + 테스트 + 구현 + RunResult
    │       output: APPROVED / CHANGES_REQUESTED / ERROR + 피드백
    │       ※ CHANGES_REQUESTED여도 PR은 생성 — 사람이 최종 판단
    │
    └─► STEP 5: GitWorkflow
            - agent/task-{id} 브랜치 생성 (git worktree 기반, 병렬 안전)
            - workspace 결과물 복사 + 커밋
            - no_push=False(기본): gh pr create → base branch로 PR
            - no_push=True: 로컬 브랜치·커밋만 생성, push/PR 건너뜀
            - PR body에 테스트 결과 + 리뷰 피드백 포함
```

### 2.5.2 디렉토리 구조

```
AI_coding_agent/
├── agents/
│   ├── roles.py               # 역할별 RoleConfig (TEST_WRITER, IMPLEMENTER, REVIEWER)
│   ├── scoped_loop.py         # ScopedReactLoop (도구 제한 + workspace 격리)
│   └── prompts/               # 역할별 시스템 프롬프트 마크다운
│       ├── test_writer.md
│       ├── implementer.md
│       └── reviewer.md
├── core/
│   ├── loop.py                # ReactLoop (ReAct 루프 엔진, stop_check/write_deadline + auto-compaction)
│   ├── compactor.py           # 시맨틱 히스토리 압축 (prefix 보존 + middle summary)
│   └── token_log.py           # 역할별 per-call JSONL 토큰 로그 저장
├── llm/
│   ├── __init__.py            # create_client() 팩토리, 프로바이더 등록
│   ├── base.py                # BaseLLMClient, LLMConfig, LLMResponse, Message, StopReason (ABORTED 포함)
│   ├── claude_client.py       # Anthropic API 클라이언트
│   ├── openai_client.py       # OpenAI Chat Completions 클라이언트
│   ├── glm_client.py          # GLM/Zai API 클라이언트 (OpenAI 호환)
│   ├── gemini_client.py       # Google Gemini 클라이언트 (thought_signature/tool bridge 포함)
│   ├── ollama_client.py       # Ollama 로컬 서버 클라이언트
│   └── rate_limiter.py        # 클라이언트 측 TPM/RPM sliding-window limiter + 429 poison
├── orchestrator/
│   ├── task.py                # Task 데이터 모델 + TaskStatus enum + YAML 로드/저장
│   ├── pipeline.py            # TDDPipeline 상태 머신 (의존성 pre-check, enriched description)
│   ├── task_redesign.py       # 태스크 재설계 유틸리티 (실험적)
│   ├── workspace.py           # WorkspaceManager (tmp 생성/정리, 의존성 산출물 주입 + fallback)
│   ├── git_workflow.py        # GitWorkflow (git worktree 기반)
│   ├── merge_agent.py         # MergeAgent (LLM 기반 머지 충돌 자동 해결)
│   ├── report.py              # PipelineResult → TaskReport 변환, 비용/캐시 메트릭 계산
│   ├── dependency.py          # 위상 정렬 기반 실행 순서 결정
│   ├── intervention.py        # 오케스트레이터 개입 (FailureType 분류 + analyze/generate_report/save_report)
│   ├── weekly.py              # 주간 보고서 생성
│   ├── milestone.py           # 마일스톤 보고서 생성
│   └── run.py                 # CLI/API 공용 진입점 (run_pipeline, PauseController — 직접 Discord 폴링 포함)
├── metrics/
│   └── collector.py           # TaskReport 저장/로드/집계 (에이전트가 생성한 독립 모듈)
├── reports/
│   ├── task_report.py         # TaskReport dataclass 단일 소스
│   ├── weekly.py              # 주간 보고서 생성기 (에이전트가 생성한 독립 모듈)
│   └── execution_brief.py     # execution_brief 생성기
├── structure/
│   └── updater.py             # Tree-sitter 다언어 파싱 → PROJECT_STRUCTURE.md 자동 생성
├── hotline/
│   └── notifier.py            # DiscordNotifier (httpx 기반, send/wait_for_reply/listen_for_commands, 429 rate limit, urgent_callback)
├── tools/
│   ├── registry.py            # 도구 등록 및 스키마 빌더
│   ├── file_tools.py          # read_file, write_file, edit_file 등
│   ├── hotline_tools.py       # ask_user 도구 (에이전트 → 사용자 질의)
│   └── ...
├── backend/
│   ├── config.py              # 환경 변수 (LLM_PROVIDER, API 키, Discord 설정)
│   ├── main.py                # FastAPI 앱 진입점
│   └── routers/
│       ├── chat.py            # POST /api/chat/stream, POST /api/chat/complete, GET /api/models
│       ├── tasks.py           # GET/POST /api/tasks, GET/PATCH /api/tasks/{id}, POST/GET /api/tasks/draft[/{job_id}], POST /api/tasks/{id}/redesign, POST /api/tasks/fix-dependencies
│       ├── pipeline.py        # POST /api/pipeline/run, GET /api/pipeline/status/{id}, GET /api/pipeline/stream/{id}, GET /api/pipeline/jobs, POST /api/pipeline/control/{id}
│       ├── reports.py         # POST /api/execution-brief, GET /api/project-structure, POST/GET /api/reports/weekly[/{year}/{week}]
│       ├── dashboard.py       # GET /api/dashboard/summary, /tasks, /milestones[/{filename}]
│       ├── discord_router.py  # GET /api/discord/status, GET /api/discord/guilds, POST /api/discord/test
│       └── utils.py           # GET /api/config, GET/PATCH /api/config/llm, GET/POST /api/utils/context-docs*
├── docker/
│   ├── Dockerfile.test        # python:3.12-slim + pytest
│   ├── docker-entrypoint.sh   # 다중 프레임워크 지원 (pytest/jest/vitest/go/rspec/minitest/python/node)
│   └── runner.py              # DockerTestRunner (RunResult, 언어 분기, 테스트 통과 판정 보정)
├── frontend/
│   └── src/
│       ├── App.tsx
│       ├── components/
│       │   ├── MeetingApp.tsx
│       │   ├── TaskDraftPanel.tsx
│       │   ├── DashboardPage.tsx
│       │   ├── ProjectListPage.tsx
│       │   ├── PipelineModelModal.tsx  # 모델/프로바이더 선택 모달
│       │   ├── SettingsModal.tsx       # 프로바이더/모델 설정 모달
│       │   └── ...
│       └── hooks/
│           ├── useAnthropicStream.ts
│           └── useMeeting.ts
├── scripts/
│   └── test_discord_read.py   # Discord 메시지 읽기 진단 스크립트
└── agent-data/
    └── tasks.yaml             # 태스크 정의 파일
```

### 2.5.3 핵심 결정 사항 (확정)

| 항목 | 결정 | 이유 |
|------|------|------|
| Task 정의 방식 | YAML + `language` 필드 + UI 승인 | 언어별 테스트 러너/프롬프트 결정, 명시적 확인 |
| Reviewer 판정 후 행동 | CHANGES_REQUESTED여도 PR 생성 | 사람이 최종 판단 |
| 테스트 타겟 | 언어별 기본 프레임워크(`python→pytest`, `kotlin/java→gradle`, `js/ts→jest`, `go→go`, `ruby→rspec`, `c/cpp→전용`) | 다중 언어 파이프라인 일관성 |
| 테스트 통과 판정 | exit code + summary 기반 보정 (OK: 접두어 + pytest "N passed" 패턴) | INTERNALERROR 등으로 exit code 비정상이어도 실제 통과 시 성공 처리 |
| 실패한 workspace | 보존 (디버깅용) | 성공 시만 자동 정리 |
| Implementer 재시도 | MAX_RETRIES=2 (TDDPipeline 내부) | 초과 시 오케스트레이터 개입 |
| 오케스트레이터 개입 | FailureType 분류 → ENV_ERROR/UNSUPPORTED_LANGUAGE 즉시 포기, LOGIC_ERROR만 LLM 분석 | 불필요한 LLM 호출 방지 |
| 의존성 pre-check | 선행 태스크 DONE이면 스킵, 미완료만 filesystem 확인 | auto_merge 없이도 정상 동작 |
| 태스크 초안 target_files | `src/` 접두어 제거 → 슬래시 1개면 1-level 경로 보존 → 2개 이상이면 basename 추출 | 패키지 구조 파괴 방지 + 깊은 패키지 경로 정리 |
| 모델 선택 | 환경 변수 + 역할별 override (`provider_fast`, `provider_capable`, `role_models`) | coding/orchestrator/intervention을 독립 튜닝 가능 |
| git push 스킵 | no_push=True 시 로컬 브랜치·커밋만 생성, push/PR 건너뜀 (UI 토글로 제어) | 원격 공개 없이 로컬 검증 가능 |
| LLM 토큰·비용 추적 | 역할별 input/output/cached_read/cached_write + JSONL call log 저장 | 비용·캐시 hit·모델별 사용량 가시화 |
| LLM API 429/TPM 방어 | `llm/rate_limiter.py`의 sliding-window 예약 + 429 poison + 클라이언트별 재시도 | 병렬 에이전트 thundering herd 방지 |
| Prompt cache 관측성 | OpenAI/GLM/Gemini cached token 수집, cache_hit_rate 계산 | prefix 안정성 검증 및 비용 절감 추적 |
| 긴 세션 컨텍스트 관리 | semantic auto-compaction으로 middle history 요약, prefix와 최근 turns 보존 | 긴 ReAct 세션에서 컨텍스트 폭주 방지 |
| 신규 target_file 처리 | workspace 생성 시 빈 스켈레톤 선주입 + Implementer 후 엄격 가드 | 탐색 루프 감소, “성공했지만 파일 없음” 차단 |
| Reviewer 인프라 장애 분리 | 파싱 불가/LLM 실패는 `verdict=ERROR` | 코드 품질 문제와 인프라 실패를 분리 |

### 2.5.4 E2E 검증 결과 (2026-03-30)

실제 태스크 2개(`정수 계산기`, `단어 빈도 분석기`)로 전체 파이프라인 검증 완료.
- task-002에서 첫 시도 실패 → Implementer 재시도(retry_count=1) → 통과 확인
- Reviewer 양쪽 모두 APPROVED 판정
- `tasks.yaml` 체크포인트 자동 저장 확인

---

## 3. Git 브랜치 전략 및 승인 정책

### 3.1 브랜치 구조

```
main                          ← 검증 완료된 코드만
 └── dev                      ← 통합 테스트 통과 후 머지
      └── agent/task-001      ← 에이전트별 독립 브랜치
      └── agent/task-002
      └── agent/task-003
```

- 에이전트는 `agent/task-*` 브랜치에서만 작업 (git worktree 기반, 병렬 안전)
- 샌드박스 테스트 통과 → base branch로 PR 생성
- 승인 기준 충족 시 머지
- `dev` → `main`은 반드시 사람이 승인

### 3.2 승인(Approval) 정책

| 변경 유형 | 승인 방식 | 근거 |
|-----------|-----------|------|
| 핵심 비즈니스 로직 | PR + 사람 직접 리뷰 | 높은 영향도 |
| 유틸리티, 테스트 코드 | 자동 승인 (테스트 통과 시) | 영향 범위 제한적 |
| 인프라, 설정 변경 | PR + 사람 리뷰 | 시스템 안정성 |
| 문서, 주석 | 자동 승인 | 리스크 없음 |

---

## 4. 보고서 체계 및 컨텍스트 관리

### 4.1 보고서 종류

| 보고서 | 주기 | 내용 | 소비자 |
|--------|------|------|--------|
| Task Report | 태스크 완료 시 | 변경 사항, 테스트 결과, 비용, cached token, reviewer verdict, 모델 사용량 | 오케스트레이터 |
| Weekly Report | 1주 | 진행률, 블로커, 비용, 패턴 분석 | 사람 |
| Milestone Report | 파이프라인 완료 시 | 전체 결과 요약, 품질 지표 | 사람 |

### 4.2 컨텍스트 압축 전략

장기 프로젝트에서 오케스트레이터의 컨텍스트 윈도우가 병목이 되는 것을 방지한다.

```
[Task Report 원본들]
  → Sonnet 요약 → [Weekly Report] (주 단위 집계)
    → Milestone Report (파이프라인 완료 시 전체 요약)
```

- **보고서 누적 금지**: Task Report는 Weekly로 압축, 상세는 YAML 파일로 유지
- **활성 컨텍스트 분리**: 현재 진행 중인 태스크만 상세 유지, 완료된 것은 요약으로 대체
- **루프 내부 압축**: ReactLoop는 임계치(기본 30k tokens) 초과 시 prefix(system + 첫 user 태스크)를 보존한 채 중간 구간을 요약해 단일 user 메시지로 치환
- **관측성과 킬스위치**: compaction 이벤트는 call_log에 남고, `DISABLE_COMPACTION=1`로 즉시 비활성화 가능

---

## 5. 회의 인터페이스 설계

### 5.1 목적

LLM과 대화하여 프로젝트 컨텍스트 문서(마크다운)를 생성·갱신하는 인터페이스. 프로젝트 시작 시 초기 회의, 이후 방향 수정이나 마일스톤 리뷰 시 사용한다.

### 5.2 핵심 플로우

```
나 (텍스트 입력 / 파일·이미지 첨부)
  ↓
채팅 UI (백엔드 API 경유)
  ↓ POST /api/chat/stream (SSE)
텍스트 응답 출력 (선택지 버튼 포함 시 자동 렌더링)
  ↓ 내가 종료 선언 또는 수동 갱신 요청
LLM이 마크다운 컨텍스트 문서 생성 (streaming 패널로 실시간 확인)
```

### 5.3 UI 구성

```
┌──────────────────────────────────────────────┐
│  🏗️ PROJECT MEETING  [📄 문서] [↺ 갱신] [종료] │
│                                              │
│  ┌──────────────────────────────────────┐   │
│  │ Opus: 흥미로운 방향이네요. 그 부분에서 │   │
│  │ 제 생각엔 X보다 Y가 더 나을 것 같은데 │   │
│  │ 어떻게 보세요?           [복사]       │   │
│  └──────────────────────────────────────┘   │
│                                              │
│              [나]: 맞아, Y가 맞을 것 같아 [복사]│
│                                              │
│  ──────────────────────────────────────────  │
│  [+] [메시지 입력 (이미지/PDF 첨부 가능)] [→] │
└──────────────────────────────────────────────┘

컨텍스트 문서 패널 (↺ 클릭 또는 📄 클릭 시):
┌──────────────────────────────────────────────┐
│  컨텍스트 문서           ● 생성 중…      [✕] │
│  ──────────────────────────────────────────  │
│  # 프로젝트명                                 │
│  ## 개요 ...                                  │
│  ## 핵심 결정 및 배경 ...                     │
│  ## 미결 사항 ...                             │
│                                    [취소]    │
└──────────────────────────────────────────────┘
```

### 5.4 Opus 역할 및 대화 방식

회의 LLM은 정보를 수집하는 설문자가 아니라 **지적 파트너**로 동작한다.

- 사용자 답변에서 흥미로운 함의나 잠재적 문제를 발견하면 적극적으로 파고든다
- 자신의 의견과 분석을 솔직하게 제시한다 ("제 생각엔…", "이 방향이 더 나을 것 같은데…")
- 아이디어 간 모순이나 트레이드오프가 있으면 함께 탐색한다
- 사용자가 명확히 2~3개 옵션 중 하나를 골라야 할 때만 선택지 버튼을 사용한다
- 사용자가 직접 종료하기 전까지 대화를 계속 이어가며 먼저 종결 선언을 하지 않는다

### 5.5 회의 타입

**프로젝트 회의**: "무엇을 만들 것인가"
- 특정 프로젝트의 기능, 설계, 우선순위 논의
- context_doc + execution_brief + PROJECT_STRUCTURE.md 주입

**시스템 회의**: "어떻게 더 잘 만들 것인가"
- 멀티 에이전트 시스템 자체의 성능, 비용, 프로세스 개선
- 전체 프로젝트 통합 메트릭 + 패턴 분석 주입
- **주기: 매주** (Weekly Report 생성 직후가 적절)

### 5.6 컨텍스트 문서 포맷 (마크다운)

JSON 스키마를 강제하지 않는다. LLM이 프로젝트 성격에 맞는 구조를 자유롭게 결정한다.

```markdown
---
completeness: 75
hint: 샌드박스 구현 방식과 에이전트 모델 선택이 미결
---

# 프로젝트명

## 개요
...

## 핵심 목표
...

## 핵심 결정 및 배경
- 결정 A: ... (배경: ..., 검토했다 폐기한 대안: ...)
- 결정 B: ...

## 기술 스택
...

## 미결 사항
- [ ] ...
- [ ] ...
```

---

## 6. 기술 스택

| 기능 | 기술 | 선택 이유 |
|------|------|-----------|
| 회의 UI | React SPA (Vite) | 프레임워크 오버헤드 없이 빠르게 시작 |
| 스타일링 | Tailwind CSS | 빠른 프로토타이핑 |
| AI (오케스트레이터) | 백엔드 API 경유 (프로바이더 선택 가능) | API 키 서버 측 관리, 프로바이더 교체 용이 |
| AI (실행 에이전트) | claude/openai/glm/ollama 중 선택 | 비용 효율, 고빈도 호출에 적합 |
| 백엔드 | FastAPI + Uvicorn | 가볍고 빠름, 비동기 지원, Python 생태계 통합 |
| 문서 저장 | YAML + 마크다운 파일 (로컬) | 심플하게 시작, 추후 DB 전환 가능 |
| 버전 관리 | Git + GitHub | PR 기반 승인 워크플로우 |
| 핫라인 | Discord Bot | 모바일 알림, 채널 분리, 풍부한 Bot API |
| 음성 입력 (추후) | Web Speech API → Whisper | 핵심 기능 안정화 후 추가 |

---

## 7. 구현 계획

### Phase 1: 회의 인터페이스 ✅ 완료

```
1단계 - 코어 채팅 ✅
  ├── React SPA 세팅 (Vite + Tailwind) ✅
  ├── Anthropic API 연결 (streaming) ✅
  └── 기본 채팅 UI ✅

2단계 - 컨텍스트 문서 생성 ✅
  ├── LLM이 마크다운 컨텍스트 문서 생성 ✅
  │   (JSON 파싱 방식 → 마크다운 직접 생성 방식으로 변경)
  ├── 수동 갱신 버튼 (스트리밍 패널, 취소 가능) ✅
  └── 회의 종료 → 문서 저장 ✅

3단계 - 회의 품질 개선 ✅
  ├── 시스템 프롬프트 재설계 (설문자 → 지적 파트너) ✅
  ├── 이전 회의 로드 → 이어서 회의 ✅
  └── 회의 히스토리 목록 (이름 변경, 삭제) ✅

추가 구현 (설계 당시 미포함)
  ├── 선택지 버튼 (streaming 중 즉시 표시) ✅
  ├── 메시지 복사 / 응답 다시 생성 버튼 ✅
  ├── 파일·이미지 첨부 (+ 버튼, 드래그&드롭) ✅
  └── 컨텍스트 문서 뷰어 (📄 버튼) ✅
```

### Phase 2: 에이전트 실행 환경 ✅ 완료

```
4단계 - 단일 에이전트 파이프라인 ✅ 완료 (2026-03-30)
  ├── Docker 테스트 러너 (격리 pytest 실행) ✅
  ├── Task 모델 + YAML 로드/저장 ✅
  ├── WorkspaceManager (tmp 격리 디렉토리) ✅
  ├── ScopedReactLoop (역할별 도구 제한 + workspace 격리) ✅
  ├── TestWriter / Implementer / Reviewer 에이전트 ✅
  ├── TDDPipeline 상태 머신 (재시도 루프 포함) ✅
  ├── GitWorkflow (브랜치 → 커밋 → PR 생성) ✅
  └── run.py CLI 진입점 + E2E 검증 ✅

5단계 - 오케스트레이터 연결 ✅ 완료 (2026-03-30)
  Step 1: FastAPI 백엔드 + API 프록시 (dangerouslyAllowBrowser 제거) ✅
  Step 2: 파이프라인 확장 (Task Report, 위상 정렬 의존성, 백엔드 API) ✅
    ├── orchestrator/report.py  — TaskReport 저장/로드/집계
    ├── orchestrator/run.py     — resolve_execution_groups() (Kahn's algorithm)
    ├── backend/routers/pipeline.py  — POST /api/pipeline/run (비동기 job)
    └── tests/test_report.py, tests/test_run.py
  Step 3: 태스크 초안 생성 (context_doc → LLM → tasks.yaml + UI) ✅
    ├── POST /api/tasks/draft  — LLM이 JSON 태스크 목록 생성 (비동기 job)
    ├── GET  /api/tasks/draft/{job_id}  — 초안 생성 상태/결과 조회
    ├── frontend/src/components/TaskDraftPanel.tsx  — 편집 + 파이프라인 실행 UI
    └── frontend/src/__tests__/components/TaskDraftPanel.test.tsx
  Step 4: 회의 인터페이스 확장 (회의 타입 분리, execution_brief 주입) ✅
    ├── MeetingApp: meetingType ('project' | 'system') 분리
    ├── POST /api/execution-brief  — Task Report 요약 생성
    └── GET  /api/project-structure — PROJECT_STRUCTURE.md 내용 반환
  Step 5: Discord 핫라인 (알림 + 질의응답 양방향) ✅
    ├── hotline/notifier.py  — DiscordNotifier (httpx 기반, send + wait_for_reply + listen_for_commands)
    │     429 rate limit 자동 재시도 (retry_after 파싱), urgent_callback, catch-all 예외 처리
    ├── tools/hotline_tools.py  — ask_user 도구 (에이전트 → 사용자 질의, LLM 대화 파트너)
    ├── orchestrator/run.py  — 파이프라인 알림 + PauseController
    │     직접 Discord 폴링 (리스너 스레드 백업), stop_check → ReactLoop/ScopedReactLoop 연동
    ├── backend/routers/discord_router.py  — GET /api/discord/status, GET /api/discord/guilds, POST /api/discord/test
    ├── scripts/test_discord_read.py  — Discord 메시지 읽기 진단 스크립트
    └── tests/test_notifier.py
  Step 6: 보고서 체계 (Weekly Report) ✅
    ├── orchestrator/weekly.py  — ISO 주차 집계 + LLM 마크다운 생성
    ├── backend/routers/reports.py  — POST/GET /api/reports/weekly[/{year}/{week}]
    └── tests/test_weekly.py

  첫 실제 프로젝트 — 유틸리티 모듈 5개 (셀프 호스팅 검증) ✅ 완료 (2026-03-31)
  ├── metrics/collector.py       — Task Report 저장/로드/집계 (34 tests APPROVED)
  ├── reports/weekly.py          — 주간 보고서 생성 (39 tests APPROVED)
  ├── structure/updater.py       — Tree-sitter 다언어 파싱 → PROJECT_STRUCTURE.md (52 tests APPROVED)
  │     지원: Python/TS/JS/C/C++/Rust/Go/Java. 새 언어: pip install + _LANG_MAP + _load_parser() + _parse_{lang}() 추가
  ├── reports/execution_brief.py — 회의 시작 시 주입할 실행 요약 (35 tests APPROVED)
  └── orchestrator/dependency.py — 위상 정렬 기반 실행 순서 결정 (27 tests APPROVED)
  상세 설계: docs/project-document-after_Phase_2.md
```

### Phase 3: 멀티 에이전트 + 운영 ✅ 7단계 + 신뢰성 강화 완료

```
6단계 - 병렬 에이전트 ✅ 완료 (2026-03-31)
  ├── git worktree 기반 GitWorkflow 재설계 ✅
  │     main repo HEAD 불변 — 여러 태스크 동시 실행 시 git 상태 충돌 없음
  ├── ThreadPoolExecutor + --parallel N 플래그 ✅
  │     그룹 내 태스크 병렬 실행, 기본값 1 (순차, 하위 호환)
  ├── MergeAgent — LLM 기반 머지 충돌 자동 해결 ✅
  │     그룹 완료 후 dev 자동 머지, 충돌 시 Haiku 1회 호출로 파일별 해결
  └── StructureUpdater 파이프라인 통합 ✅
        그룹 머지 후 PROJECT_STRUCTURE.md 자동 갱신 → 다음 그룹 에이전트에 주입

7단계 - 보고서 및 모니터링 ✅ 완료 (2026-03-31)
  ├── Milestone Report 자동 생성 ✅
  │     orchestrator/milestone.py — 파이프라인 완료 시 LLM이 마크다운 요약 생성
  │     agent-data/reports/milestones/ 에 타임스탬프 파일로 저장
  ├── 대시보드 백엔드 API ✅
  │     backend/routers/dashboard.py
  │     GET /api/dashboard/summary   — 메트릭 집계 (성공률, 재시도, 소요 시간 등)
  │     GET /api/dashboard/tasks     — tasks.yaml + Task Report 조인
  │     GET /api/dashboard/milestones — 마일스톤 보고서 목록
  │     GET /api/dashboard/milestones/{filename} — 보고서 본문 (path traversal 방지)
  └── 대시보드 프론트엔드 UI ✅
        frontend/src/components/DashboardPage.tsx
        메트릭 카드, 태스크 목록 아코디언, 마일스톤 사이드 패널 뷰어
        다크모드 완전 지원

Phase 3 추가 구현 ✅ 완료 (2026-03-31)
  ├── 멀티 프로젝트 관리 UI ✅
  │     frontend/src/components/ProjectListPage.tsx — 프로젝트 카드 그리드
  │     Project = { id, name, rootDir, baseBranch, createdAt }
  ├── 대시보드 파이프라인 제어 ✅
  │     DashboardPage에 ⏸ 멈춤 / ▶ 계속 / ■ 중단 버튼 (실행 중 잡에만 표시)
  │     POST /api/pipeline/control/{job_id} — pause/resume/stop 명령
  │     ▶ 파이프라인 재개 버튼 — pending/failed 태스크 이어서 실행
  ├── auto_merge 토글 ✅
  │     DashboardPage에 토글 버튼 — localStorage('pipeline_auto_merge')로 세션 간 상태 유지
  │     파이프라인 재개 시 auto_merge 값 전달 → 그룹 완료 후 자동 머지 실행
  ├── PipelineModelModal ✅
  │     frontend/src/components/PipelineModelModal.tsx
  │     GET /api/models 로 프로바이더별 모델 목록 조회 (claude/openai/glm/ollama/gemini 동적 열거)
  │     파이프라인 실행 전 fast/capable 모델, 프로바이더, 병렬 에이전트 수 선택
  │     역할별 override(test_writer / implementer / reviewer) 지정 가능
  ├── 오케스트레이터 개입 로직 ✅
  │     orchestrator/intervention.py — analyze() / generate_report() / save_report()
  │     에이전트 실패 시 LLM이 근본 원인 분석 → RETRY(힌트 주입) or GIVE_UP 결정
  │     max_orchestrator_retries(기본 2회) 초과 시 마크다운 실패 보고서 자동 생성
  │     CHANGES_REQUESTED는 LOGIC_ERROR로 분류 (ENV_ERROR 오분류 방지)
  ├── MAX_ITER 감지 및 표면화 ✅
  │     ScopedReactLoop 최대 반복 초과 시 failure_reason에 [MAX_ITER] 프리픽스 태깅
  ├── 크로스 언어 프로젝트 지원 강화 ✅
  │     _DRAFT_SYSTEM_PROMPT: `language` 필수, 언어별 확장자/네이밍 규칙, 4개 섹션 description 강제
  │     _sanitize_task_draft / _normalize_target_path:
  │       src/ 접두어 제거 → 슬래시 1개면 1-level 보존(models/user.py 유지)
  │       → 2개 이상이면 basename 추출 (깊은 패키지 경로 평탄화)
  │     inject_dependency_context fallback: target_files 불일치 시 git diff로 실제 파일 주입
  │     _check_dependency_files: 선행 DONE 태스크 스킵 (auto_merge 없이도 정상 동작)
  │     Task.language + LANGUAGE_TEST_FRAMEWORK_MAP 기반 Docker 이미지 선택
  │     DockerTestRunner: requirements.txt 자동 설치, pytest "N passed" 패턴 인식
  │     Implementer/Reviewer 프롬프트에 target_files 목록 명시 (경로별 파일 생성 위치 안내)
  ├── Catch-up 머지 ✅
  │     auto_merge=ON으로 재개 시 이전에 완료됐지만 아직 머지 안 된 브랜치를 먼저 처리
  ├── Weekly Report UI ✅
  │     DashboardPage에 주간 보고서 섹션 추가
  │     "이번 주 생성" 버튼 → POST /api/reports/weekly → 결과 즉시 뷰어 표시
  └── 운영 품질 개선 ✅
        uvicorn 폴링 로그 억제, Discord TimeoutException 다운그레이드 등

Phase 3 신뢰성 강화 ✅ 완료 (2026-04-18)
  ├── git push 스킵 토글 ✅
  │     RunRequest.no_push: bool — True 시 로컬 브랜치·커밋만 생성, push/PR 건너뜀
  │     TaskDraftPanel UI: 📦 로컬만 / 🚀 push+PR 토글 버튼
  ├── LLM 관측성 강화 ✅
  │     TaskReport: total_tokens / cost_usd / cached_read / cache_hit_rate / token_usage 저장
  │     core/token_log.py: 역할별 per-call JSONL 로그 저장
  │     DashboardPage — 비용 MetricCard 표시, scripts/verify_cache_hit.py로 cache hit 검증 가능
  ├── 멀티 프로바이더 확장 ✅
  │     GeminiClient 추가, backend/config.py에 GEMINI_API_KEY 지원
  │     GET /api/models 및 PipelineModelModal에서 gemini 모델 노출
  ├── LLM API rate limit 방어 ✅
  │     llm/rate_limiter.py: (provider, model)별 TPM/RPM 예약 + 429 poison
  │     openai/glm/gemini 클라이언트에 공통 통합
  │     OpenAI는 Retry-After/에러 메시지 파싱 기반 MAX_RETRIES=6 재시도 유지
  ├── Prompt cache 안정화 ✅
  │     openai/glm 메시지 직렬화 결정성 강화, cached_tokens 수집
  │     Claude/OpenAI/GLM/Gemini cached_read/write 메트릭을 TaskReport로 승격
  ├── Reviewer 신뢰성 개선 ✅
  │     CHANGES_REQUESTED → LOGIC_ERROR, LLM/파싱 실패 → verdict=ERROR 분리
  │     reviewer.md: read_file 결과 해석 규칙 추가
  │     ScopedReactLoop: Reviewer 역할 _readonly_warn_threshold=None (불필요한 경고 제거)
  ├── Implementer/TestWriter 신뢰성 개선 ✅
  │     신규 target_file 빈 스켈레톤 선주입 + Implementer 완료 후 strict missing-file guard
  │     write_deadline 초과 시 WRITE_LOOP 감지 → TestWriter/Implementer 재시도
  │     Python 이미지에 requirements.txt 자동 설치, torch 의존 태스크용 ML 패키지 내장
  └── Semantic auto-compaction ✅
        core/compactor.py + ReactLoop._maybe_compact()
        threshold 30k, prefix 보존, 최근 turns 유지, 2-iter cooldown, env 킬스위치 지원

8단계 - 음성 인터페이스 (선택, 별도)
  ├── STT 입력 (Web Speech API)
  ├── TTS 출력
  └── 음성 회의 모드
```

---

## 8. 미결 사항

| 항목 | 현재 상태 | 결정 시점 |
|------|-----------|-----------|
| 실행 에이전트 모델 선택 | **확정**: 환경 변수로 설정 (기본 fast: Haiku, capable: Opus) | ✅ 결정 완료 |
| 샌드박스 구현 방식 | **확정**: Docker. workspace를 `/tmp`에 생성 후 마운트 | ✅ 결정 완료 |
| Task 정의 방식 | **확정**: YAML (수동 정의 또는 Sonnet 자동 생성 + UI 승인) | ✅ 결정 완료 |
| Reviewer 판정 후 행동 | **확정**: CHANGES_REQUESTED여도 PR 생성. PR body에 피드백 포함 | ✅ 완료 |
| 5단계 오케스트레이터 연결 방식 | **확정**: FastAPI 백엔드 + React 프론트엔드, Discord 핫라인 포함 | ✅ 완료 |
| 에이전트 간 의존성 태스크 처리 | **확정**: DAG 위상 정렬 + 그룹 내 병렬 실행 (ThreadPoolExecutor) | ✅ 완료 |
| 병렬 실행 시 git 충돌 | **확정**: git worktree 기반 GitWorkflow — HEAD 불변, 병렬 안전 | ✅ 완료 |
| 머지 충돌 자동 해결 | **확정**: MergeAgent (LLM 1회 호출/파일, 최대 3회 재시도) | ✅ 완료 |
| 오케스트레이터 개입 | **확정**: LLM 분석 → RETRY/GIVE_UP, 실패 보고서 자동 생성 | ✅ 완료 |
| DB 전환 | JSON/YAML → SQLite or PostgreSQL | 데이터 복잡도 증가 시 |
| Milestone Report 컨텍스트 압축 | Daily Summary 계층 미구현 — Milestone만 있음 | 운영 중 필요 시 |
| CI/CD 통합 | GitHub Actions 유력 | 8단계 또는 별도 |
| 태스크 타입 분기 | frontend 태스크 파이프라인 제외 구현됨 (task_type="frontend") | ✅ 완료 |
| 크로스 언어 target_files | **확정**: 언어별 확장자는 유지하고, `src/` 제거 + 1-level 경로 보존 + 깊은 경로만 basename 정리 | ✅ 완료 |
| 의존성 pre-check | **확정**: 선행 DONE 태스크 스킵, 미완료만 filesystem 확인 | ✅ 완료 |
| 테스트 통과 판정 | **확정**: OK: 접두어 + pytest "N passed" (failed/error 없음) 패턴 보정 | ✅ 완료 |
| 핫라인 확장 | 버튼 인터랙션, /run 명령어, PR 요약, 스크린샷 피드백 | 필요 시 |
| 오케스트레이터 → 사용자 질문 | ask_user 도구로 에이전트가 Discord/stdin 경유 질의 가능 | ✅ 완료 |
| Discord Message Content Intent | **필수**: Developer Portal → Bot → Privileged Gateway Intents에서 활성화. 미활성 시 REST API가 content를 빈 문자열로 반환하여 모든 명령이 무시됨 | ✅ 확인 완료 |
| ReactLoop stop_check | ✅ 매 LLM 호출 전 콜백 체크 → 즉시 ABORTED 반환. PauseController.is_stopped와 연동 | 완료 |
| PauseController 직접 폴링 | ✅ 리스너 스레드 백업으로 직접 Discord API 폴링 (attach_notifier + _poll_discord_for_stop) | 완료 |
| 429 Rate Limit 처리 (Discord) | ✅ listen_for_commands/wait_for_reply에서 retry_after 파싱 후 자동 대기 | 완료 |
| 429 Rate Limit 처리 (LLM API) | ✅ `llm/rate_limiter.py` + openai/glm/gemini 재시도 통합 | 완료 |
| git push 스킵 토글 | ✅ RunRequest.no_push, GitWorkflow.run(no_push), TaskDraftPanel UI 토글 | 완료 |
| LLM 토큰·비용 추적 | ✅ TaskReport.total_tokens / cost_usd / cached_read / cache_hit_rate + JSONL call log | 완료 |
| Reviewer CHANGES_REQUESTED 오분류 | ✅ LOGIC_ERROR로 재분류, Reviewer 프롬프트 read_file 해석 규칙 추가 | 완료 |
| TestWriter WRITE_LOOP | ✅ write_deadline 초과 감지 + 재시도 로직 추가 | 완료 |
| Gemini 프로바이더 | ✅ GeminiClient, GEMINI_API_KEY, 모델 목록/선택 UI 반영 | 완료 |
| Semantic auto-compaction | ✅ ReactLoop가 긴 히스토리를 자동 요약, `DISABLE_COMPACTION=1` 킬스위치 제공 | 완료 |
