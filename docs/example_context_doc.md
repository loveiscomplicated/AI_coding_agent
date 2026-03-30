---
completeness: 78
hint: 각 모듈의 에러 핸들링 정책, 메트릭 집계 시 시간대 기준(UTC vs 로컬), Weekly Report 패턴 감지 로직의 구체적 규칙/임계값, execution_brief 요약 시 Sonnet 프롬프트 설계, 의존성 계산기의 입력 형식(depends_on 필드 스펙), 실제 5단계 시스템과의 통합 인터페이스 확정
---

# 프로젝트 컨텍스트 문서: 멀티 에이전트 시스템 5단계 유틸리티 모듈

## 0. 문서 개요

본 문서는 멀티 에이전트 개발 시스템의 5단계(오케스트레이터 연결)에서 필요한 유틸리티 모듈들을 기술한다. 이 모듈들은 멀티 에이전트 시스템 자체의 첫 실제 프로젝트로서, 시스템이 자기 자신의 부품을 만드는 셀프 호스팅 방식의 검증을 겸한다.

### 프로젝트 선정 배경

멀티 에이전트 시스템의 첫 실제 프로젝트로 SNN 연구 프로젝트가 검토되었으나, GPU 훈련 필요, 결과 해석의 주관성, 하이퍼파라미터 탐색 중심 등의 특성이 TDD 파이프라인과 맞지 않아 보류되었다. 대신 시스템 자체의 유틸리티 모듈을 선택한 이유:

1. **즉시 실용적**: 만들고 나면 바로 시스템에 통합 가능
2. **검증 적합성**: 순수 Python, 명확한 입출력, 테스트 용이
3. **적절한 의존성**: 모듈 간 의존성이 존재하여 의존성 처리 검증 가능
4. **셀프 호스팅 의미**: 시스템이 자기 부품을 만들 수 있다는 강력한 검증

## 1. 프로젝트 목표

### 1.1 핵심 목표

5단계 시스템 운영에 필요한 5개 유틸리티 모듈을 구현한다:

1. **메트릭 수집기** — 파이프라인 실행 데이터 추출/저장/조회
2. **Weekly Report 생성기** — 주간 데이터를 분석 보고서로 변환
3. **PROJECT_STRUCTURE.md 생성기 (StructureUpdater)** — 코드베이스 구조를 자동 문서화
4. **execution_brief 생성기** — 회의 시작 시 주입할 실행 요약 생성
5. **태스크 의존성 계산기** — tasks.yaml의 의존성 그래프를 분석하여 실행 순서 결정

### 1.2 부차 목표 (시스템 검증)

- 멀티 에이전트 TDD 파이프라인의 실제 프로젝트 적용 검증
- 태스크 간 의존성 처리 흐름 검증 (dev 머지 → 다음 태스크 workspace)
- StructureUpdater가 생성한 트리 문서의 컨텍스트 전달 효과 검증
- Task Report 메트릭 수집의 실전 검증

## 2. 모듈 상세 설계

### 2.1 메트릭 수집기 (`metrics/collector.py`)

**역할**: 파이프라인 실행 중 발생하는 정량적 데이터를 구조화하여 저장/조회한다.

**수집 항목**:
```yaml
# data/reports/task-001.yaml
task_id: "001"
title: "메트릭 수집기 구현"
status: COMPLETED
completed_at: "2026-04-07T14:30:00Z"
metrics:
  retry_count: 2
  total_tokens: 12450
  cost_usd: 0.52
  test_count: 7
  test_pass_first_try: false
  reviewer_verdict: APPROVED
  time_elapsed_seconds: 180
  failure_reasons: ["TypeError in line 23", "missing edge case"]
pipeline_result:
  test_output_summary: "7 passed in 1.2s"
  reviewer_feedback: "Clean implementation, good error handling"
  pr_number: 12
  branch: "agent/task-001"
```

**핵심 기능**:
- `save_report(task_id, report_data)` → `data/reports/task-{id}.yaml`로 저장
- `load_report(task_id)` → 단일 리포트 로드
- `load_reports(since=datetime)` → 특정 시점 이후의 모든 리포트 로드
- `get_aggregate_metrics(since, until)` → 기간별 집계 (총 비용, 평균 재시도율, 성공률 등)

**저장 형식**: YAML (기존 tasks.yaml과 일관성 유지)
**저장 위치**: `data/reports/task-{id}.yaml`

### 2.2 Weekly Report 생성기 (`reports/weekly.py`)

**역할**: 메트릭 데이터를 분석하여 주간 마크다운 보고서를 생성한다. 단순 수치 나열이 아닌 패턴 분석과 개선 제안을 포함한다.

**출력 형식**:
```markdown
# Weekly Report — 2026-04-07 ~ 2026-04-13

## 진행 요약
- 완료: 8개 태스크
- 실패 → 수동 개입: 1개 (task-012, 원인: ...)
- 보류 중: 2개

## 주요 성과
- 사용자 인증 모듈 전체 완성 (task-007~011)
- API 엔드포인트 5개 추가

## 이슈 & 패턴
- Implementer 재시도율: 30% (10개 중 3개)
  → acceptance_criteria 모호한 케이스에서 집중 발생
  → 제안: criteria 작성 가이드라인 강화
- Reviewer가 CHANGES_REQUESTED한 건: 2개
  → 둘 다 에러 핸들링 누락

## 비용
| 항목 | 금액 |
|------|------|
| Sonnet (오케스트레이터) | $3.20 |
| Haiku (에이전트) | $1.85 |
| 합계 | $5.05 |
| 태스크당 평균 | $0.63 |

## 추이
- 태스크당 평균 비용: $0.63 (전주 $0.78 대비 19% 감소)
- 재시도율: 30% (전주 40% 대비 개선)

## 다음 주 계획
- task-013~018 예정
- 프론트엔드 컨텍스트 문서 생성 시작
```

**핵심 기능**:
- `generate_weekly_report(week_start, week_end)` → 마크다운 문자열 반환
- `calculate_cost_summary(reports)` → 비용 집계
- `detect_patterns(reports)` → 이슈 & 패턴 섹션 데이터 생성
- `compare_with_previous(current_metrics, previous_report)` → 추이 계산

**패턴 감지 로직**: 재시도율, CHANGES_REQUESTED 빈도, 실패 원인 분류 등에 대한 통계 기반 감지. 구체적 임계값은 실제 데이터 축적 후 조정 (초기값은 합리적 기본값으로 설정).

**이전 주와의 비교**: 직전 Weekly Report를 로드하여 주요 지표의 증감을 계산. 이전 보고서가 없으면 추이 섹션 생략.

**저장 위치**: `data/reports/weekly/2026-W15.md`

### 2.3 PROJECT_STRUCTURE.md 생성기 (`structure/updater.py`)

**역할**: 프로젝트의 Python 코드베이스를 AST 파싱하여 구조 문서를 자동 생성한다. 에이전트와 사람 모두 코드 전체를 읽지 않고도 프로젝트 구조와 인터페이스를 파악할 수 있게 한다.

**파싱 깊이: 레벨 3**
- 파일명
- 클래스/함수명
- 함수 시그니처 (인자, 리턴 타입)
- docstring 첫 줄 요약

**출력 형식**:
```markdown
# Project Structure

## src/
### src/auth/
#### `__init__.py`
- **exports**: `verify_token`, `create_token`, `InvalidTokenError`

#### `token.py`
- `create_token(user_id: str) -> str`
  JWT 토큰 생성. 만료 24h.
- `verify_token(token: str) -> dict`
  토큰 검증. 실패 시 InvalidTokenError.

#### `exceptions.py`
- `class InvalidTokenError(Exception)`
  유효하지 않은 토큰에 대한 예외.

### src/metrics/
#### `collector.py`
- `save_report(task_id: str, report_data: dict) -> Path`
  Task Report를 YAML로 저장.
- `load_report(task_id: str) -> dict`
  단일 Task Report 로드.
- `load_reports(since: datetime) -> list[dict]`
  특정 시점 이후 모든 리포트 로드.

## tests/
### `test_auth.py`
- 7 tests
### `test_metrics.py`
- 5 tests
```

**핵심 기능**:
- `generate_structure(project_root, exclude_dirs)` → 마크다운 문자열 반환
- `parse_python_file(filepath)` → 파일 내 클래스/함수/시그니처/docstring 추출
- `build_tree(project_root)` → 디렉토리 트리 구조 생성
- `format_markdown(tree)` → 트리를 마크다운으로 포맷팅

**제외 대상**: `__pycache__`, `.git`, `node_modules`, `venv`, `.venv` 등 표준 제외 디렉토리. 설정으로 추가 제외 가능.

**테스트 파일 처리**: 테스트 파일은 함수 시그니처까지 파싱하지 않고, 파일명 + 테스트 개수만 표시.

**파이프라인 통합 위치**: TDD 파이프라인의 Reviewer 이후, PR 생성 직전에 StructureUpdater 단계로 실행.

```
TestWriter → Implementer → Reviewer → StructureUpdater → PR
```

### 2.4 execution_brief 생성기 (`reports/execution_brief.py`)

**역할**: 회의 시작 시 Opus에게 주입할 실행 요약 문서를 생성한다. 마지막 회의 이후 ~ 현재까지의 파이프라인 실행 결과를 압축한다.

**생성 시점**: "주간 회의 시작" 버튼 클릭 시, 회의 세션 시작 전에 생성.

**출력 형식**:
```markdown
# Execution Brief
기간: 2026-04-07 ~ 2026-04-13 (마지막 회의 이후)

## 완료된 태스크
- task-007: 사용자 인증 API ✅
- task-008: 세션 관리 ✅
- task-009: 비밀번호 해싱 ✅

## 실패/보류 태스크
- task-010: DB 마이그레이션 ❌
  사유: 스키마 정의가 context_doc에 명시되지 않음
  Implementer 3회 재시도 후 실패

## 핵심 수치
| 지표 | 값 |
|------|-----|
| 완료 | 3/4 |
| 총 비용 | $2.10 |
| 평균 재시도 | 0.7회 |

## 주의 필요 사항
- task-010 실패: DB 스키마 결정 필요
- 재시도율 상승 추세 (전주 대비 +10%)
```

**핵심 기능**:
- `generate_brief(since_datetime)` → 마크다운 문자열 반환
- `classify_tasks(reports)` → 완료/실패/보류 분류
- `extract_attention_items(reports)` → 사람의 주의가 필요한 항목 추출
- `format_brief(classified_tasks, metrics, attention_items)` → 마크다운 포맷팅

**프로젝트 회의 vs 시스템 회의**: 프로젝트 회의에서는 해당 프로젝트의 execution_brief만 주입. 시스템 회의에서는 전체 프로젝트 통합 메트릭 요약을 주입 (이 부분은 시스템 회의 구현 시 확장).

### 2.5 태스크 의존성 계산기 (`orchestrator/dependency.py`)

**역할**: tasks.yaml에 정의된 태스크들의 의존성 관계를 분석하여 올바른 실행 순서를 결정한다.

**입력 형식** (tasks.yaml 확장):
```yaml
tasks:
  - id: "001"
    title: "메트릭 수집기"
    description: "파이프라인 실행 메트릭을 수집/저장/조회하는 모듈"
    depends_on: []
    acceptance_criteria:
      - "Task Report를 YAML로 저장/로드"
      - "기간별 집계 함수 동작"

  - id: "002"
    title: "Weekly Report 생성기"
    description: "메트릭 데이터를 주간 마크다운 보고서로 변환"
    depends_on: ["001"]
    acceptance_criteria:
      - "메트릭 수집기의 데이터를 소비하여 보고서 생성"
      - "비용 집계, 패턴 감지, 추이 비교 포함"

  - id: "003"
    title: "PROJECT_STRUCTURE.md 생성기"
    description: "Python AST 파싱으로 프로젝트 구조 문서 자동 생성"
    depends_on: []
    acceptance_criteria:
      - "레벨 3 파싱 (시그니처 + docstring 첫 줄)"
      - "테스트 파일은 파일명 + 테스트 개수만"

  - id: "004"
    title: "execution_brief 생성기"
    description: "회의 시작 시 주입할 실행 요약 문서 생성"
    depends_on: ["001"]
    acceptance_criteria:
      - "마지막 회의 이후 Task Report 수집/분류"
      - "주의 필요 사항 자동 추출"

  - id: "005"
    title: "태스크 의존성 계산기"
    description: "tasks.yaml의 의존성 그래프를 분석하여 실행 순서 결정"
    depends_on: []
    acceptance_criteria:
      - "위상 정렬로 실행 순서 반환"
      - "순환 의존성 감지 시 에러"
```

**핵심 기능**:
- `resolve_order(tasks)` → 위상 정렬된 태스크 ID 리스트 반환
- `detect_cycles(tasks)` → 순환 의존성 발견 시 관련 태스크 ID와 함께 에러
- `get_dependencies(task_id, tasks)` → 특정 태스크의 직접/간접 의존성 조회
- `get_ready_tasks(tasks, completed_ids)` → 현재 실행 가능한 태스크 목록 (모든 의존성 충족된 것)

**위상 정렬 알고리즘**: Kahn's algorithm 사용. BFS 기반이라 구현이 직관적이고, 순환 감지가 자연스럽게 포함됨 (정렬 결과 크기 ≠ 전체 태스크 수이면 순환 존재).

**`get_ready_tasks`의 활용**: Phase 3 병렬 에이전트에서 "지금 바로 실행 가능한 태스크"를 조회하는 데 사용. 5단계에서는 순차 실행이므로 순서 결정에만 사용하지만, 인터페이스는 병렬 확장을 고려하여 설계.

## 3. 모듈 간 의존성 구조

```
task-001: 메트릭 수집기        (의존 없음)
task-003: 구조 생성기          (의존 없음)
task-005: 의존성 계산기        (의존 없음)
    ↓
task-002: Weekly Report 생성기  (→ task-001 필요)
task-004: execution_brief 생성기 (→ task-001 필요)
```

**실행 순서 (위상 정렬 결과)**:
```
그룹 1 (독립, 어떤 순서든 가능): task-001, task-003, task-005
그룹 2 (그룹 1 완료 후):         task-002, task-004
```

5단계에서는 순차 실행이므로: `001 → 003 → 005 → 002 → 004` (그룹 1 내 순서는 임의).

이 의존성 구조 자체가 task-005 (의존성 계산기)의 테스트 케이스로 사용 가능 — 셀프 레퍼런스.

## 4. 기술 스택 및 제약

| 항목 | 선택 | 이유 |
|------|------|------|
| 언어 | Python 3.12 | 기존 파이프라인과 동일 |
| 데이터 저장 | YAML | 기존 tasks.yaml과 일관성 |
| 테스트 | pytest | 기존 파이프라인 Docker 러너와 동일 |
| AST 파싱 | Python `ast` 모듈 | 표준 라이브러리, 외부 의존성 없음 |
| 그래프 알고리즘 | 직접 구현 (Kahn's) | 단순, 외부 라이브러리 불필요 |
| 마크다운 생성 | 문자열 포맷팅 | 템플릿 엔진 불필요한 수준 |

**외부 의존성 최소화**: PyYAML 외에는 표준 라이브러리만 사용. 에이전트가 Docker 샌드박스에서 쉽게 테스트할 수 있도록.

## 5. 디렉토리 구조

```
AI_coding_agent/           # 기존 프로젝트 루트
├── agents/                # 기존 에이전트 코드
├── orchestrator/
│   ├── task.py            # 기존
│   ├── pipeline.py        # 기존
│   ├── dependency.py      # ★ 신규: 의존성 계산기
│   └── ...
├── metrics/
│   ├── __init__.py
│   └── collector.py       # ★ 신규: 메트릭 수집기
├── reports/
│   ├── __init__.py
│   ├── weekly.py          # ★ 신규: Weekly Report 생성기
│   └── execution_brief.py # ★ 신규: execution_brief 생성기
├── structure/
│   ├── __init__.py
│   └── updater.py         # ★ 신규: 구조 생성기
├── data/
│   ├── tasks.yaml         # 기존
│   └── reports/           # ★ 신규
│       ├── task-001.yaml
│       ├── task-002.yaml
│       └── weekly/
│           └── 2026-W15.md
└── tests/
    ├── test_collector.py  # ★ 신규
    ├── test_weekly.py     # ★ 신규
    ├── test_updater.py    # ★ 신규
    ├── test_brief.py      # ★ 신규
    └── test_dependency.py # ★ 신규
```

## 6. 파이프라인 통합 계획

### 6.1 StructureUpdater 통합

기존 TDD 파이프라인에 단계 추가:

```
TestWriter → Implementer → Reviewer → StructureUpdater → PR
```

StructureUpdater는 읽기 전용 에이전트가 아닌, **코드를 직접 파싱하는 도구 호출**로 구현. Haiku가 아닌 Python 스크립트 실행:

```python
# pipeline.py 내 StructureUpdater 단계
structure_md = generate_structure(workspace_root)
write_file(workspace_root / "PROJECT_STRUCTURE.md", structure_md)
```

### 6.2 메트릭 수집 통합

파이프라인 완료 시 자동 호출:

```python
# pipeline.py 완료 단계
report = {
    "task_id": task.id,
    "title": task.title,
    "status": "COMPLETED",
    "completed_at": datetime.utcnow().isoformat(),
    "metrics": {
        "retry_count": pipeline_state.retry_count,
        "total_tokens": pipeline_state.total_tokens,
        # ...
    }
}
save_report(task.id, report)
```

### 6.3 의존성 계산기 통합

`run.py`에서 파이프라인 실행 전 호출:

```python
# run.py
tasks = load_tasks("data/tasks.yaml")
execution_order = resolve_order(tasks)
for task_id in execution_order:
    run_pipeline(task_id)
    # 완료 후 dev 머지 → 다음 태스크는 dev 기반 workspace
```

## 7. 검증 전략

### 7.1 단위 테스트 (에이전트가 작성)

각 모듈의 핵심 기능에 대한 pytest 테스트. TDD 파이프라인의 TestWriter가 acceptance_criteria 기반으로 작성.

### 7.2 통합 테스트 (에이전트가 작성)

모듈 간 연동 테스트:
- 메트릭 수집기로 저장 → Weekly Report 생성기로 보고서 생성
- 메트릭 수집기로 저장 → execution_brief 생성기로 요약 생성
- 샘플 tasks.yaml → 의존성 계산기 → 올바른 실행 순서

### 7.3 셀프 호스팅 검증 (사람이 판단)

이 프로젝트 자체의 파이프라인 실행 데이터로 모듈을 검증:
- 5개 태스크 실행 후 수집된 메트릭으로 Weekly Report 생성
- 생성된 PROJECT_STRUCTURE.md가 실제 코드와 일치하는지
- 의존성 계산기가 이 프로젝트의 `001→003→005→002→004` 순서를 올바르게 산출하는지

## 8. 시스템 자기 개선과의 연결

이 모듈들은 단순 유틸리티가 아니라, 멀티 에이전트 시스템의 **자기 개선 루프의 기반**이다:

```
레벨 1 (본 프로젝트): 데이터 수집 + 패턴 보고
  메트릭 수집 → Weekly Report → 시스템 회의에서 사람이 판단

레벨 2 (향후): 자동 개선 시도
  패턴 감지 → 프롬프트/criteria 자동 조정

레벨 3 (향후): 메타 최적화
  프롬프트 A vs B 비교 실험 → 자동 채택
```

**핵심 지표: 태스크당 평균 비용**. 시간에 따른 추이를 추적하여 시스템 성숙도를 측정. Weekly Report의 추이 섹션이 이를 자동으로 계산.

## 9. 미결 사항

| 항목 | 현재 상태 | 결정 시점 |
|------|-----------|-----------|
| 메트릭 집계 시간대 기준 | UTC 유력 | 구현 시 |
| Weekly Report 패턴 감지 임계값 | 실제 데이터 축적 후 조정 | 첫 2~3주 운영 후 |
| execution_brief Sonnet 프롬프트 | 기본 요약 프롬프트로 시작 | 5단계-A 회의 인터페이스 확장 시 |
| 에러 핸들링 정책 | 모듈별 구체화 필요 | 각 태스크 구현 시 |
| 시스템 회의용 통합 메트릭 | 프로젝트 회의용 먼저 구현 | 시스템 회의 기능 구현 시 |
| depends_on 필드 스펙 확정 | 문자열 리스트 (task ID) 유력 | 의존성 계산기 구현 시 |

## 10. 탐색 후 폐기된 방향

| 방향 | 폐기 이유 |
|------|-----------|
| SNN 연구 프로젝트를 첫 프로젝트로 | GPU 필요, 결과 해석 주관적, TDD 부적합 |
| PROJECT_STRUCTURE 레벨 4 (전체 docstring) | 문서 비대화, 토큰 낭비 |
| PROJECT_STRUCTURE 레벨 2 (시그니처만) | 함수 역할 파악을 위해 결국 파일을 열어야 함 |
| Task Report JSON 형식 | 기존 tasks.yaml과의 일관성을 위해 YAML 선택 |
| 외부 그래프 라이브러리 (networkx 등) | Kahn's algorithm 직접 구현이 충분, 의존성 최소화 |

