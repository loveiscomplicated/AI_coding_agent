# Multi-Agent Development System

> 프로젝트 문서 v1.3 | 2026-03-31 — Phase 3 7단계 완료

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
중앙 오케스트레이터 (Claude Opus)
 ↕  태스크 분배 / 결과 수집 / 컨텍스트 압축
┌──────────────────────────────────┐
│  Agent 1    Agent 2    Agent N   │  ← 로컬/저비용 모델
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
| 오케스트레이터 | 태스크 분해, 컨텍스트 관리, 보고서 생성 | Claude Sonnet | 중간 비용, 저빈도 |
| 실행 에이전트 | 코드 작성, 테스트 생성, 코드 리뷰 | Claude Haiku | 저비용, 고빈도 |

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
data/tasks.yaml (수동 정의)
    │
    ▼ orchestrator/run.py
[Task 목록] ──► 사람 확인(y/n) ──► 진행
    │
    ▼ for each Task:
    │
    ├─► STEP 1: TestWriter (Haiku + ScopedReactLoop)
    │       input:  task.description + acceptance_criteria
    │       output: workspace/tests/ 에 pytest 테스트 파일 작성
    │       tools:  read_file, write_file, list_directory, search_files
    │
    ├─► STEP 2: Implementer (Haiku + ScopedReactLoop)
    │       input:  task + 테스트 파일들
    │       output: workspace/src/ 에 구현 파일 작성
    │       tools:  read_file, write_file, edit_file, list_directory, search_files
    │
    ├─► STEP 3: DockerTestRunner
    │       input:  workspace/ 디렉토리
    │       output: RunResult (pass/fail, stdout, summary)
    │       ─────────────────────────────────────────────
    │       FAIL → Implementer 재시도 (max 3회, 이전 오류 포함)
    │       PASS → 다음 단계
    │
    ├─► STEP 4: Reviewer (Haiku + ScopedReactLoop, 읽기 전용)
    │       input:  task + 테스트 + 구현 + RunResult
    │       output: APPROVED / CHANGES_REQUESTED + 피드백
    │       ※ CHANGES_REQUESTED여도 PR은 생성 — 사람이 최종 판단
    │
    └─► STEP 5: GitWorkflow
            - agent/task-{id} 브랜치 생성
            - workspace 결과물 복사 + 커밋
            - gh pr create → base branch로 PR
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
├── orchestrator/
│   ├── task.py                # Task 데이터 모델 + TaskStatus enum + YAML 로드/저장
│   ├── pipeline.py            # TDDPipeline 상태 머신
│   ├── workspace.py           # WorkspaceManager (tmp 생성/정리)
│   ├── git_workflow.py        # GitWorkflow + check_prerequisites
│   └── run.py                 # CLI 진입점
├── docker/
│   ├── Dockerfile.test        # python:3.12-slim + pytest
│   ├── docker-entrypoint.sh   # requirements.txt 자동 설치 후 pytest 실행
│   └── runner.py              # DockerTestRunner
└── data/
    └── tasks.yaml             # 태스크 정의 파일 (수동 작성)
```

### 2.5.3 핵심 결정 사항 (확정)

| 항목 | 결정 | 이유 |
|------|------|------|
| Task 정의 방식 | YAML 수동 정의 | Sonnet 파싱 오류 위험 제거, 명시적 확인 |
| Reviewer 판정 후 행동 | CHANGES_REQUESTED여도 PR 생성 | 사람이 최종 판단 |
| 테스트 타겟 | Python 전용 | Phase 3에서 Node.js 추가 |
| 실패한 workspace | 보존 (디버깅용) | 성공 시만 자동 정리 |
| Implementer 재시도 | MAX_RETRIES=3 | 초과 시 FAILED, 사람 개입 |

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

- 에이전트는 `agent/task-*` 브랜치에서만 작업
- 샌드박스 테스트 통과 → `dev`로 PR 생성
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
| Task Report | 태스크 완료 시 | 변경 사항, 테스트 결과, 이슈 | 오케스트레이터 |
| Daily Summary | 1일 | 진행률, 블로커, 다음 계획 | 사람 |
| Milestone Report | 마일스톤 완료 시 | 목표 대비 달성도, 품질 지표 | 사람 |

### 4.2 컨텍스트 압축 전략

장기 프로젝트에서 Opus의 컨텍스트 윈도우가 병목이 되는 것을 방지한다.

```
[Task Report 원본들]
  → Opus 요약 → [Daily Summary] (원본 폐기)
    → Opus 요약 → [Milestone Summary] (일일 요약 압축)
```

- **보고서 누적 금지**: Task Report는 요약 후 압축본만 유지
- **계층적 요약**: Task → Daily → Milestone 순으로 점진적 압축
- **활성 컨텍스트 분리**: 현재 진행 중인 태스크만 상세 유지, 완료된 것은 요약으로 대체
- **컨텍스트 문서 버전 관리**: 회의 때마다 JSON이 갱신되며 version 번호 증가

---

## 5. 회의 인터페이스 설계

### 5.1 목적

Opus와 대화하여 프로젝트 컨텍스트 문서(JSON)를 생성·갱신하는 인터페이스. 프로젝트 시작 시 초기 회의, 이후 방향 수정이나 마일스톤 리뷰 시 사용한다.

### 5.2 핵심 플로우

```
나 (텍스트 입력 / 파일·이미지 첨부)
  ↓
채팅 UI
  ↓ Opus API (streaming)
텍스트 응답 출력 (선택지 버튼 포함 시 자동 렌더링)
  ↓ 내가 종료 선언 또는 수동 갱신 요청
Opus가 마크다운 컨텍스트 문서 생성 (streaming 패널로 실시간 확인)
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

Opus는 정보를 수집하는 설문자가 아니라 **지적 파트너**로 동작한다.

- 사용자 답변에서 흥미로운 함의나 잠재적 문제를 발견하면 적극적으로 파고든다
- 자신의 의견과 분석을 솔직하게 제시한다 ("제 생각엔…", "이 방향이 더 나을 것 같은데…")
- 아이디어 간 모순이나 트레이드오프가 있으면 함께 탐색한다
- 사용자가 명확히 2~3개 옵션 중 하나를 골라야 할 때만 선택지 버튼을 사용한다
- 사용자가 직접 종료하기 전까지 대화를 계속 이어가며 먼저 종결 선언을 하지 않는다

### 5.5 Opus 시스템 프롬프트 설계

```
역할: 프로젝트 기획 파트너

1. 사용자의 아이디어를 함께 탐색하고 발전시켜 나갈 것
2. 흥미로운 함의나 잠재적 문제를 발견하면 적극적으로 파고들 것
3. 자신의 의견과 분석을 솔직하게 제시할 것
4. 아이디어 간 모순이나 트레이드오프가 있으면 함께 탐색할 것
5. 사용자가 명확히 2~3개 옵션 중 하나를 골라야 하는 경우에만
   <choice>선택지</choice> 태그를 사용할 것
6. 사용자가 직접 종료를 요청하거나 회의 종료 버튼을 누를 때까지
   대화를 계속 이어갈 것 (먼저 종결 선언 금지)
```

매 응답마다 JSON을 반환하지 않는다. 컨텍스트 문서 생성은 별도 호출로 처리한다.

### 5.6 컨텍스트 문서 포맷 (마크다운)

JSON 스키마를 강제하지 않는다. Opus가 프로젝트 성격에 맞는 구조를 자유롭게 결정한다. 오케스트레이터도 LLM이므로 마크다운을 그대로 소비할 수 있다.

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

**생성 시점**
- **매 턴**: 생성하지 않음 (전체 히스토리를 Opus에게 직접 전달)
- **↺ 수동 갱신**: Opus가 스트리밍으로 생성, 패널에서 실시간 확인 및 취소 가능
- **회의 종료**: Opus가 최종 문서 생성 후 완료 화면으로 전환

---

## 6. 기술 스택

| 기능 | 기술 | 선택 이유 |
|------|------|-----------|
| 회의 UI | React SPA (Vite) | 프레임워크 오버헤드 없이 빠르게 시작 |
| 스타일링 | Tailwind CSS | 빠른 프로토타이핑 |
| AI (오케스트레이터) | Anthropic API — Opus, streaming | 실시간 타이핑 효과, 고품질 추론 |
| AI (실행 에이전트) | 로컬 모델 or Sonnet/Haiku | 비용 효율, 고빈도 호출에 적합 |
| 문서 저장 | JSON 파일 (로컬) | 심플하게 시작, 추후 DB 전환 가능 |
| 버전 관리 | Git + GitHub | PR 기반 승인 워크플로우 |
| 음성 입력 (추후) | Web Speech API → Whisper | 핵심 기능 안정화 후 추가 |
| 음성 출력 (추후) | Web Speech API → ElevenLabs | 단계적 업그레이드 |

---

## 7. 구현 계획

### Phase 1: 회의 인터페이스 ✅ 완료

```
1단계 - 코어 채팅 ✅
  ├── React SPA 세팅 (Vite + Tailwind) ✅
  ├── Anthropic API 연결 (streaming) ✅
  └── 기본 채팅 UI ✅

2단계 - 컨텍스트 문서 생성 ✅
  ├── Opus가 마크다운 컨텍스트 문서 생성 ✅
  │   (JSON 파싱 방식 → Opus 직접 생성 방식으로 변경)
  ├── 수동 갱신 버튼 (스트리밍 패널, 취소 가능) ✅
  └── 회의 종료 → 문서 저장 ✅

3단계 - 회의 품질 개선 ✅
  ├── Opus 시스템 프롬프트 재설계 (설문자 → 지적 파트너) ✅
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
  Step 3: 태스크 초안 생성 (context_doc → Sonnet → tasks.yaml + UI) ✅
    ├── POST /api/tasks/draft  — Sonnet이 JSON 태스크 목록 생성
    ├── frontend/src/components/TaskDraftPanel.tsx  — 편집 + 파이프라인 실행 UI
    └── frontend/src/__tests__/components/TaskDraftPanel.test.tsx
  Step 4: 회의 인터페이스 확장 (회의 타입 분리, execution_brief 주입) ✅
    ├── MeetingApp: meetingType ('project' | 'system') 분리
    ├── App.tsx: 시스템 회의 시작 시 execution_brief 자동 조회 후 주입
    ├── backend/routers/reports.py  — POST /api/execution-brief
    └── hooks/useAnthropicStream.ts — buildSystemPrompt(meetingType, brief)
  Step 5: Discord 핫라인 (알림 + 질의응답 양방향) ✅
    ├── hotline/notifier.py  — DiscordNotifier (send + wait_for_reply)
    ├── orchestrator/run.py  — 파이프라인 알림 + 실패 시 힌트 수집
    ├── backend/routers/discord_router.py  — GET /api/discord/status, POST /api/discord/test
    └── tests/test_notifier.py
  Step 6: 보고서 체계 (Weekly Report) ✅
    ├── orchestrator/weekly.py  — ISO 주차 집계 + Sonnet 마크다운 생성
    ├── backend/routers/reports.py  — POST/GET /api/reports/weekly
    └── tests/test_weekly.py (30개 테스트)

  첫 실제 프로젝트 — 유틸리티 모듈 5개 (셀프 호스팅 검증) ✅ 완료 (2026-03-31)
  ├── metrics/collector.py       — Task Report 저장/로드/집계 (34 tests APPROVED)
  ├── reports/weekly.py          — 주간 보고서 생성 (39 tests APPROVED)
  ├── structure/updater.py       — Python AST → PROJECT_STRUCTURE.md (31 tests APPROVED)
  ├── reports/execution_brief.py — 회의 시작 시 주입할 실행 요약 (35 tests APPROVED)
  └── orchestrator/dependency.py — 위상 정렬 기반 실행 순서 결정 (27 tests APPROVED)
  상세 설계: docs/project-document-after_Phase_2.md
```

### Phase 3: 멀티 에이전트 + 운영 ✅ 7단계 완료

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
  │     orchestrator/milestone.py — 파이프라인 완료 시 Sonnet이 마크다운 요약 생성
  │     data/reports/milestones/ 에 타임스탬프 파일로 저장
  ├── 대시보드 백엔드 API ✅
  │     backend/routers/dashboard.py
  │     GET /api/dashboard/summary   — 메트릭 집계 (성공률, 재시도, 소요 시간 등)
  │     GET /api/dashboard/tasks     — tasks.yaml + Task Report 조인
  │     GET /api/dashboard/milestones — 마일스톤 보고서 목록
  │     GET /api/dashboard/milestones/{filename} — 보고서 본문 (path traversal 방지)
  └── 대시보드 프론트엔드 UI ✅
        frontend/src/components/DashboardPage.tsx
        메트릭 카드 8개, 태스크 목록 테이블, 마일스톤 사이드 패널 뷰어
        다크모드 완전 지원

8단계 - 음성 인터페이스 (선택, 별도)
  ├── STT 입력 (Web Speech API)
  ├── TTS 출력
  └── 음성 회의 모드
```

---

## 8. 미결 사항

| 항목 | 현재 상태 | 결정 시점 |
|------|-----------|-----------|
| 실행 에이전트 모델 선택 | **확정**: 오케스트레이터 Sonnet, 실행 에이전트 Haiku | ✅ 결정 완료 |
| 샌드박스 구현 방식 | **확정**: Docker. workspace를 `/tmp`에 생성 후 읽기 전용 마운트 | ✅ 결정 완료 |
| Task 정의 방식 | **확정**: YAML 수동 정의. Sonnet 자동 추출은 Phase 3에서 검토 | ✅ 결정 완료 |
| Reviewer 판정 후 행동 | **확정**: CHANGES_REQUESTED여도 PR 생성. PR body에 피드백 포함 | ✅ 결정 완료 |
| Phase 2 테스트 타겟 | **확정**: Python 전용. Node.js 지원은 Phase 3 | ✅ 결정 완료 |
| 5단계 오케스트레이터 연결 방식 | **확정**: FastAPI 백엔드 + React 프론트엔드 분리, Discord 핫라인 포함 | ✅ 완료 |
| 에이전트 간 의존성 태스크 처리 | **확정**: DAG 위상 정렬 + 그룹 내 병렬 실행 (ThreadPoolExecutor) | ✅ 완료 |
| 병렬 실행 시 git 충돌 | **확정**: git worktree 기반 GitWorkflow — HEAD 불변, 병렬 안전 | ✅ 완료 |
| 머지 충돌 자동 해결 | **확정**: MergeAgent (Haiku LLM 1회 호출/파일) | ✅ 완료 |
| DB 전환 | JSON/YAML → SQLite or PostgreSQL | 데이터 복잡도 증가 시 |
| Milestone Report 컨텍스트 압축 | Daily Summary 계층 미구현 — Milestone만 있음 | 운영 중 필요 시 |
| CI/CD 통합 | GitHub Actions 유력 | 8단계 또는 별도 |
