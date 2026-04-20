"""
backend/routers/tasks.py — 태스크 목록 CRUD + 초안 생성 API

GET  /api/tasks            태스크 목록 조회
POST /api/tasks            태스크 목록 전체 저장 (덮어쓰기)
GET  /api/tasks/{id}       단일 태스크 조회
POST /api/tasks/draft      context_doc → Sonnet → 태스크 초안 반환
"""

from __future__ import annotations

import asyncio
import json
import re
import threading
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.config import LLM_PROVIDER, LLM_MODEL_CAPABLE
from llm import LLMConfig, Message, create_client
from orchestrator.task import Task, load_tasks, save_tasks
from orchestrator.task_redesign import create_redesign_llm, redesign_task
from project_paths import resolve_data_dir, resolve_tasks_path
from tools.hotline_tools import get_redesign_model

# ── 초안 생성 잡 저장소 ──────────────────────────────────────────────────────
_draft_jobs: dict[str, dict] = {}
_draft_lock = threading.Lock()

# ── 태스크 재설계 잡 저장소 ───────────────────────────────────────────────────
_redesign_jobs: dict[str, dict] = {}
_redesign_lock = threading.Lock()
_redesign_semaphore = threading.Semaphore(2)  # GLM rate limit 대응: 동시 실행 최대 2개

_DRAFT_SYSTEM_PROMPT = """\
당신은 소프트웨어 개발 태스크를 설계하는 전문가입니다.

프로젝트 컨텍스트 문서를 읽고 구현 태스크 목록을 생성하세요.
생성된 태스크는 별도의 에이전트(TestWriter, Implementer, Reviewer)가 소비합니다.
이 에이전트들은 프로젝트 맥락을 모르므로, description에 모든 필요한 맥락을 담아야 합니다.

[규칙]
- 태스크 하나 = 파일 3개 이하. target_files가 4개 이상 필요하면 반드시 여러 태스크로 분할할 것
- 인터페이스/모델 정의, 구현 로직, 테스트는 가능한 한 별도 태스크로 분리할 것
- acceptance_criteria: 테스트 프레임워크로 직접 검증 가능한 구체적 조건 3~5개
- target_files: 생성 또는 수정할 파일 경로 목록 (반드시 3개 이하)
- depends_on: 먼저 완료되어야 하는 태스크 id 목록 (없으면 빈 배열)
- 컨텍스트 문서에 언급되지 않은 기능을 임의로 추가하지 말 것
- id는 "task-001", "task-002", ... 형식
- task_type: "backend" 또는 "frontend" 중 하나
  - "frontend": HTML/CSS/JS/React/Vue 등 브라우저에서 실행되는 UI 코드. 멀티 에이전트 파이프라인이 실행하지 않으므로 수락 기준을 자동으로 검증할 수 없음. 이 경우에도 태스크를 생성하되, task_type을 "frontend"로 설정할 것.
  - "backend": 서버, CLI, 라이브러리, 테스트, 인프라 등 나머지 모든 것

[description 작성 규칙 — 매우 중요]
각 태스크의 description은 다음 4개 섹션을 반드시 포함해야 합니다.
각 섹션은 "### 섹션명" 형태의 Markdown 헤더로 구분하세요.

1. **### 목적과 배경**
   이 태스크가 왜 필요한지, 프로젝트 전체에서 어떤 역할인지 2-3문장으로 설명.
   "무엇을 만드는지"가 아니라 "왜 만드는지"를 먼저 설명하세요.
   컨텍스트 문서에 관련 결정이 있다면 채택된 핵심만 발췌하세요 (폐기된 대안 제외).
   "컨텍스트 문서에 따르면..." 같은 메타 참조 대신 내용 자체를 직접 쓰세요.

2. **### 기술 요구사항**
   구체적인 구현 스펙. 입출력 형식, 데이터 구조, 인터페이스 제약, 알고리즘.
   acceptance_criteria와 내용이 겹쳐도 괜찮습니다. criteria는 "무엇이 통과해야 하는지"를
   테스트 가능한 체크리스트로, 여기는 "왜 그런 제약이 있는지"를 구현자가 이해할 수 있는
   산문 형태로 쓰세요.

3. **### 인접 컨텍스트**
   - 이 태스크의 결과를 사용하는 후속 태스크: task id + 제목 (역참조)
   - 이 태스크가 의존하는 선행 태스크의 산출물: task id + 산출물 개요 (정참조)
   - 프로젝트에 이미 존재하는 관련 파일이 있다면 경로
   후속 태스크가 없으면 "후속 태스크 없음"으로 명시하세요.

4. **### 비고려 항목**
   이 태스크에서 의도적으로 다루지 않는 것을 명시하세요.
   이것이 없으면 에이전트가 범위 밖 기능까지 구현하려 합니다.

   [근거 제약 — 환각 방지]
   비고려 항목은 다음 세 가지 근거 중 하나에 정확히 대응되는 내용만 적으세요:
   (a) context_doc에 명시된 연기/보류 결정 (예: "v2에서 한다", "다음 스프린트")
   (b) 이미 다른 태스크(선행/후속)에 배정된 책임
   (c) 이 태스크의 target_files·acceptance_criteria가 자명하게 다루지 않는 인접 영역
   근거 없이 만들어낸 제외 문장은 쓰지 마세요 — Reviewer가 그 문장을 근거로
   잘못된 생략을 정당화할 수 있습니다.
   적을 근거가 하나도 없으면 "명시적 비범위 없음."이라고만 쓰고 추측하지 마세요.
   예: "DB 연동은 이 태스크의 범위가 아님 (task-015에서 처리)."  ← (b) 근거
   예: "인증/권한은 context_doc의 '인증은 Phase 2' 결정에 따라 제외."   ← (a) 근거

[description 길이 가이드라인]
- 목표 400~800자(한국어 기준).
- 200자 미만이면 에이전트가 맥락 부족으로 잘못된 방향으로 갈 위험이 큽니다.
- 1000자 초과 시 요약하세요.
- 회의 대화 원문이나 장황한 설명을 복사하지 말고, 결정 사항만 산문으로 발췌하세요.
- 코드 블록을 강제하지 마세요 — 산문이 더 낫습니다.

[language 필드 (필수)]
각 태스크에 `language` 필드를 반드시 포함한다.
- context_doc에서 프로젝트의 주요 프로그래밍 언어를 먼저 파악한다.
- 파악한 언어를 해당 태스크의 `language` 필드에 지정한다.
- 값: "python", "kotlin", "javascript", "typescript", "go", "ruby", "java" 등 소문자
- 같은 프로젝트라도 태스크별로 언어가 다를 수 있다 (예: 백엔드 Go, 프론트엔드 TypeScript).

[복잡도 평가 (complexity)]
각 태스크에 `complexity` 필드를 `simple` / `standard` / `complex` 중 하나로 평가한다.
파이프라인은 이 라벨을 보고 태스크별로 적절한 모델을 자동 선택할 수 있다.

판정은 아래 3단계 절차를 **순서대로** 적용하라. 재현성이 중요하다.

### 1단계 — 하드 규칙 (수치 기준, tier 1차 결정)
각 축(파일 수, 선행 의존 수, 수락 기준 수)을 아래 표로 tier에 매핑하고
**세 축 중 가장 높은 tier** 를 선택한다 (= max).

**중요**: 이 프로젝트는 태스크 설계 규칙상 target_files는 3개 이하(초과 시 태스크 분할),
acceptance_criteria는 3–5개가 권장된다(5 초과 시 경고). 따라서 **이 두 축은 simple/standard
만 구분**하고, **complex tier 판정은 depends_on 축 또는 2단계 승격으로만 달성**된다.
세 축의 임계값을 강제로 맞춰서 가짜 complex 판정이 나오지 않도록 설계된 것이다.

| 축                  | simple | standard     | complex (이 축에서 도달 가능?)    |
|---------------------|--------|--------------|-----------------------------------|
| target_files 수     | 1      | 2 또는 3     | — (≤3 강제 제약)                  |
| depends_on 수       | 0–1    | 2–3          | ≥ 4                               |
| acceptance_criteria | ≤ 3    | 4 또는 5     | — (≤5 권장 제약)                  |

max 규칙: 세 축 중 가장 높은 tier를 선택. 두 축이 complex를 주지 못해도 depends_on 축
하나가 complex면 최종 complex이다.

예:
- files=1, deps=0, criteria=2 → (simple, simple, simple) → **simple**
- files=2, deps=2, criteria=4 → (standard, standard, standard) → **standard**
- files=3, deps=4, criteria=3 → (standard, complex, simple) → **complex**
- files=1, deps=0, criteria=5 → (simple, simple, standard) → **standard**
- files=3, deps=3, criteria=5 → (standard, standard, standard) → **standard** (보조 지표 승격만 남음)

### 2단계 — 보조 규칙 (1단계 결과가 simple/standard일 때만 승격 검토)
아래 보조 지표 중 **2개 이상** 해당하면 1단계 tier를 **한 단계 위로 승격**한다 (cap=complex).
이미 complex라면 승격 없음.

- 외부 라이브러리의 **비표준/내부 API 사용** (예: torch의 custom nn.Module 서브클래스, SQLAlchemy의 dialect-level hook)
- **동시성/락/트랜잭션/원자성** 이 요구됨 (멀티스레드, async lock, DB 트랜잭션)
- **도메인 특수 지식 필요** (암호학·신경과학·통계 모델·네트워크 프로토콜·컴파일러 이론 등)
- **여러 서로 다른 외부 라이브러리** 를 한 태스크에서 동시 통합

판단이 애매한 보조 지표는 세지 마라 — 2개를 확실하게 긍정할 때만 승격한다.

### 3단계 — 기본값
위 단계로도 확정 못 하면 `standard`.

### 예시 판정 (절차 그대로 따라가며)
1. "두 수의 최대공약수 함수 구현" (files=1, deps=0, criteria=2):
   - 1단계 → simple.  2단계 보조 0 → 승격 없음. **simple**
2. "YAML 파서 + 스키마 검증기" (files=2, deps=1, criteria=4):
   - 1단계 축: (standard, simple, standard) → max = standard.
   - 2단계 보조 0~1 → 승격 없음. **standard**
3. "nn.Module 서브클래스 + 커스텀 loss" (files=2, deps=2, criteria=4):
   - 1단계 → standard.  2단계 보조: (비표준 API) + (도메인) = 2 → 승격. **complex**
4. "LSM 모델 클래스 (입력→리퀴드→리드아웃)" (files=3, deps=4, criteria=5):
   - 1단계 → complex.  2단계 cap. **complex**
5. "분산 락 매커니즘 (Redis 기반)" (files=2, deps=2, criteria=5):
   - 1단계 → standard.  2단계 보조: (동시성) + (외부 lib) = 2 → 승격. **complex**

[target_files 규칙]
- 프로젝트의 language 필드에 맞는 파일 확장자와 네이밍 컨벤션을 사용하라.
  - Python: snake_case .py (예: fake_map_service.py)
  - Kotlin/Java: PascalCase .kt/.java (예: FakeMapService.kt)
  - Go: snake_case .go (예: fake_map_service.go)
  - JavaScript/TypeScript: camelCase 또는 PascalCase .js/.ts/.tsx
- target_files 경로: 파일명(flat) 또는 1단계 상대 경로만 허용한다.
  - 좋은 예: "user.py", "models/user.py", "services/auth.py", "FakeMapService.kt"
  - 나쁜 예: "src/models/user.py"  (src/ 접두어 불필요, 자동 제거됨)
  - 나쁜 예: "app/src/main/java/com/example/FakeMapService.kt"  (깊은 경로 금지)

[acceptance_criteria — 언어 중립적으로 작성]
수락 기준은 언어·프레임워크·플랫폼 전용 API를 언급하지 않는 행동 중심 문장으로 작성한다.
- 좋은 예: "distanceTo(other) 메서드가 두 좌표 사이 미터 단위 거리를 반환한다"
- 좋은 예: "빈 입력에 대해 빈 리스트를 반환한다"
- 나쁜 예: "Flow<Float> 타입으로 방위각을 emit한다" (Kotlin 전용 API)
- 나쁜 예: "Python list를 반환한다" (언어 종속 타입 명시)
- 나쁜 예: "SensorManager.getDefaultSensor()를 호출한다" (플랫폼 전용 API)

[분할 예시]
나쁜 예 — 파일 7개를 한 태스크에 (Kotlin 프로젝트):
  task-001: MapService 전체 (Coordinate.kt, Place.kt, Route.kt, RouteStep.kt, MapService.kt, FakeMapService.kt)

좋은 예 — 태스크 3개로 분리:
  task-001: 도메인 모델 정의 (Coordinate.kt, Place.kt, Route.kt)
  task-002: MapService 인터페이스 (MapService.kt, RouteStep.kt) — depends_on: [task-001]
  task-003: 테스트 스텁 구현 (FakeMapService.kt) — depends_on: [task-002]

[수락 기준 자체 검증]
생성한 각 태스크의 acceptance_criteria를 검증한다:
- 수락 기준 간 모순이 없는지 확인한다.
  예: "파라미터 없는 메서드" vs "파라미터로 간격 설정 지원"은 모순이다.
- 모순 발견 시 하나를 선택하고 나머지는 별도 태스크로 분리한다.

[외부 의존성 제한]
하나의 태스크가 import해야 하는 '아직 존재하지 않는 모듈'이 2개를 초과하면 분할한다.

[출력 형식]
다음 JSON만 출력하세요. 마크다운 코드블록, 설명 텍스트 없이 순수 JSON만.
description 필드는 위의 4개 섹션(### 목적과 배경 / ### 기술 요구사항 / ### 인접 컨텍스트 / ### 비고려 항목)을
모두 포함한 긴 문자열이어야 합니다. JSON 문자열 내 개행은 "\\n"으로 이스케이프하세요.

{"tasks": [{"id": "task-001", "title": "...", "description": "### 목적과 배경\\n...\\n\\n### 기술 요구사항\\n...\\n\\n### 인접 컨텍스트\\n...\\n\\n### 비고려 항목\\n...", "acceptance_criteria": ["..."], "target_files": ["Coordinate.kt", "Place.kt"], "depends_on": [], "task_type": "backend", "language": "kotlin", "complexity": "standard"}]}
"""

router = APIRouter()


# ── 요청/응답 스키마 ──────────────────────────────────────────────────────────

class DraftRequest(BaseModel):
    context_doc: str


# ── 엔드포인트 ────────────────────────────────────────────────────────────────

def _normalize_target_path(fpath: str) -> str:
    """target_files 경로 하나를 정규화한다.

    규칙:
      1. 슬래시 없음 → 그대로  (user.py → user.py)
      2. src/ 접두어 먼저 제거  (src/models/user.py → models/user.py)
      3. 슬래시 1개 → 1-level 경로 유지  (models/user.py → models/user.py)
      4. 슬래시 2개 이상 → basename만 추출  (app/src/.../FakeMap.kt → FakeMap.kt)
    """
    if "/" not in fpath:
        return fpath
    if fpath.startswith("src/"):
        fpath = fpath[4:]
    if "/" not in fpath:
        return fpath
    if fpath.count("/") == 1:
        return fpath
    return fpath.rsplit("/", 1)[-1]


_DESCRIPTION_MIN_LENGTH = 100

# 4개 섹션 각각에 대한 한·영 헤더 별칭.
# 매칭은 Markdown 헤더(#으로 시작하는 줄)에서만 수행한다 — 본문에 키워드만 등장하는
# 경우는 섹션으로 인정하지 않는다 (헤더로 구분되지 않으면 에이전트가 파싱하기 어려움).
# 별칭은 소문자·공백 정규화 후 '포함' 매칭한다.
_DESCRIPTION_SECTION_SPEC: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("목적과 배경", ("목적과 배경", "목적/배경", "목적", "배경",
                     "purpose and background", "purpose", "background")),
    ("기술 요구사항", ("기술 요구사항", "기술 요구", "요구사항",
                       "technical requirements", "requirements", "specification")),
    ("인접 컨텍스트", ("인접 컨텍스트", "인접", "컨텍스트",
                       "adjacent context", "related context", "context")),
    ("비고려 항목", ("비고려 항목", "비고려", "비범위",
                     "out of scope", "out-of-scope", "not in scope", "non-goals")),
)

_MARKDOWN_HEADER_PATTERN = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", re.MULTILINE)


def _extract_markdown_headers(description: str) -> list[str]:
    """description의 Markdown 헤더 텍스트를 소문자·공백 정규화하여 반환한다."""
    return [
        re.sub(r"\s+", " ", match.group(1)).strip().lower()
        for match in _MARKDOWN_HEADER_PATTERN.finditer(description)
    ]


def _find_missing_sections(description: str) -> list[str]:
    """4개 섹션 중 헤더로 나타나지 않은 섹션의 한국어 라벨 목록을 반환한다."""
    headers = _extract_markdown_headers(description)
    missing: list[str] = []
    for label, aliases in _DESCRIPTION_SECTION_SPEC:
        found = any(
            alias.lower() in header
            for header in headers
            for alias in aliases
        )
        if not found:
            missing.append(label)
    return missing


def _sanitize_task_draft(task: dict, warnings: list[str]) -> None:
    """LLM이 생성한 태스크 초안의 target_files 깊은 경로를 정리한다.

    자동 보정:
      - 깊은 패키지 경로(app/src/main/...) → 파일명만 추출
    경고만:
      - target_files 보정이 발생한 경우 원래 경로를 warnings에 기록
      - description이 100자 미만
      - description에 권장 섹션(목적/기술 요구/인접/비고려) 누락
    """
    target_files = task.get("target_files") or []
    sanitized_files: list[str] = []
    any_fixed = False

    for fpath in target_files:
        original = fpath
        sanitized = _normalize_target_path(fpath)

        sanitized_files.append(sanitized)
        if sanitized != original:
            any_fixed = True

    if any_fixed:
        original_list = ", ".join(target_files)
        fixed_list = ", ".join(sanitized_files)
        warnings.append(
            f"target_files 깊은 경로 정리: [{original_list}] → [{fixed_list}]"
        )
        task["target_files"] = sanitized_files

    # 중복 제거 (변환 후 같아지는 경우)
    seen: set[str] = set()
    deduped: list[str] = []
    for f in task.get("target_files", []):
        if f not in seen:
            seen.add(f)
            deduped.append(f)
    task["target_files"] = deduped

    # description 품질 경고 (실패는 아님 — LLM이 간결하게 잘 쓸 수도 있으므로)
    task_id = task.get("id", "(id 없음)")
    description = task.get("description", "") or ""
    if len(description) < _DESCRIPTION_MIN_LENGTH:
        warnings.append(
            f"{task_id}: description이 {_DESCRIPTION_MIN_LENGTH}자 미만입니다. "
            "목적과 배경, 기술 요구사항, 인접 컨텍스트, 비고려 항목이 포함되어야 합니다."
        )

    missing = _find_missing_sections(description)
    if missing:
        warnings.append(
            f"{task_id}: description에 권장 섹션이 누락됨 (Markdown 헤더 기준): {missing}"
        )


def _run_draft(job_id: str, context_doc: str) -> None:
    """백그라운드 스레드에서 LLM 초안 생성을 실행한다."""
    try:
        client = create_client(
            LLM_PROVIDER,
            LLMConfig(model=LLM_MODEL_CAPABLE, max_tokens=16000, system_prompt=_DRAFT_SYSTEM_PROMPT),
        )
        llm_response = client.chat([Message(role="user", content=context_doc)])
        raw = ""
        for block in llm_response.content:
            if isinstance(block, dict) and block.get("type") == "text":
                raw = block["text"]
                break
            if hasattr(block, "type") and block.type == "text":
                raw = block.text
                break

        cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip())
        cleaned = re.sub(r"\s*```$", "", cleaned)

        try:
            data: Any = json.loads(cleaned)
        except json.JSONDecodeError as e:
            if llm_response.stop_reason == "max_tokens":
                error = "태스크가 너무 많아 응답이 잘렸습니다. 컨텍스트 문서를 줄이거나 태스크를 분할하세요."
            else:
                error = f"LLM 응답 파싱 실패: {e}\n응답:\n{raw[:300]}"
            with _draft_lock:
                _draft_jobs[job_id]["status"] = "error"
                _draft_jobs[job_id]["error"] = error
            return

        tasks = data.get("tasks", [])
        if not isinstance(tasks, list):
            with _draft_lock:
                _draft_jobs[job_id]["status"] = "error"
                _draft_jobs[job_id]["error"] = "LLM 응답에 'tasks' 배열이 없습니다."
            return

        for task in tasks:
            warnings: list[str] = []
            if len(task.get("target_files") or []) > 3:
                warnings.append(f"target_files {len(task['target_files'])}개 — 3개 이하로 태스크를 분할하세요")
            if len(task.get("acceptance_criteria") or []) > 5:
                warnings.append(f"acceptance_criteria {len(task['acceptance_criteria'])}개 — 5개 이하로 줄이세요")

            # complexity 값 검증 — 비정상 값 제거, 누락 시 경고만 (파이프라인에서 standard로 fallback)
            _cx = task.get("complexity")
            if _cx is not None and _cx not in ("simple", "standard", "complex"):
                warnings.append(f"{task.get('id', '(id 없음)')}: complexity 값 비정상 '{_cx}' — 무시됨")
                task.pop("complexity", None)
            if "complexity" not in task:
                warnings.append(
                    f"{task.get('id', '(id 없음)')}: complexity 누락 — 복잡도 자동 선택 시 standard로 실행됨"
                )

            # ── 후처리: target_files 경로 정규화 + 언어 불일치 보정 ────────
            _sanitize_task_draft(task, warnings)

            if warnings:
                task["warnings"] = warnings

        with _draft_lock:
            _draft_jobs[job_id]["status"] = "done"
            _draft_jobs[job_id]["tasks"] = tasks

    except Exception as e:
        with _draft_lock:
            _draft_jobs[job_id]["status"] = "error"
            _draft_jobs[job_id]["error"] = str(e)


@router.post("/tasks/draft")
def generate_tasks_draft(body: DraftRequest) -> dict:
    """
    context_doc 마크다운을 Sonnet에 전달하여 태스크 초안 생성을 시작한다.
    생성은 백그라운드에서 실행되며 job_id를 즉시 반환한다.
    GET /api/tasks/draft/{job_id} 로 상태 및 결과를 조회한다.
    """
    job_id = str(uuid.uuid4())
    with _draft_lock:
        _draft_jobs[job_id] = {"status": "running", "tasks": None, "error": None}

    threading.Thread(target=_run_draft, args=(job_id, body.context_doc), daemon=True).start()
    return {"job_id": job_id, "status": "running"}


@router.get("/tasks/draft/{job_id}")
def get_draft_status(job_id: str) -> dict:
    """태스크 초안 생성 잡의 상태와 결과를 반환한다."""
    with _draft_lock:
        job = _draft_jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"초안 잡 '{job_id}'를 찾을 수 없습니다.")
    return {"job_id": job_id, **job}


@router.get("/tasks")
def list_tasks(tasks_path: str = "agent-data/tasks.yaml") -> dict:
    """tasks.yaml에서 태스크 목록을 반환한다."""
    path = resolve_tasks_path(tasks_path)
    if not path.exists():
        return {"tasks": []}
    try:
        tasks = load_tasks(path)
    except (KeyError, ValueError) as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {"tasks": [t.to_dict() for t in tasks]}


@router.get("/tasks/{task_id}")
def get_task(task_id: str, tasks_path: str = "agent-data/tasks.yaml") -> dict:
    """특정 ID의 태스크를 반환한다."""
    path = resolve_tasks_path(tasks_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="tasks 파일을 찾을 수 없습니다.")
    try:
        tasks = load_tasks(path)
    except (KeyError, ValueError) as e:
        raise HTTPException(status_code=422, detail=str(e))
    for task in tasks:
        if task.id == task_id:
            return task.to_dict()
    raise HTTPException(status_code=404, detail=f"태스크 '{task_id}'를 찾을 수 없습니다.")


class PatchTaskRequest(BaseModel):
    description: str | None = None
    acceptance_criteria: list[str] | None = None
    tasks_path: str = "agent-data/tasks.yaml"


@router.patch("/tasks/{task_id}")
def patch_task(task_id: str, body: PatchTaskRequest) -> dict:
    """특정 태스크의 description/acceptance_criteria를 부분 업데이트한다."""
    path = resolve_tasks_path(body.tasks_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="tasks 파일을 찾을 수 없습니다.")
    try:
        tasks = load_tasks(path)
    except (KeyError, ValueError) as e:
        raise HTTPException(status_code=422, detail=str(e))
    for task in tasks:
        if task.id == task_id:
            if body.description is not None:
                task.description = body.description
            if body.acceptance_criteria is not None:
                task.acceptance_criteria = body.acceptance_criteria
            save_tasks(tasks, path)
            return task.to_dict()
    raise HTTPException(status_code=404, detail=f"태스크 '{task_id}'를 찾을 수 없습니다.")


class SaveTasksRequest(BaseModel):
    tasks: list[dict]
    tasks_path: str = "agent-data/tasks.yaml"


class RedesignRequest(BaseModel):
    tasks_path: str = "agent-data/tasks.yaml"
    repo_path: str = "."


def _run_redesign(job_id: str, task_id: str, tasks_path: str, repo_path: str) -> None:
    """백그라운드 스레드에서 LLM 태스크 재설계를 실행한다. 세마포어로 동시 실행 수를 제한한다."""
    with _redesign_semaphore:
        with _redesign_lock:
            _redesign_jobs[job_id]["status"] = "running"
        try:
            path = resolve_tasks_path(tasks_path)
            if not path.exists():
                with _redesign_lock:
                    _redesign_jobs[job_id]["status"] = "error"
                    _redesign_jobs[job_id]["error"] = "tasks 파일을 찾을 수 없습니다."
                return

            tasks = load_tasks(path)
            task = next((t for t in tasks if t.id == task_id), None)
            if task is None:
                with _redesign_lock:
                    _redesign_jobs[job_id]["status"] = "error"
                    _redesign_jobs[job_id]["error"] = f"태스크 '{task_id}'를 찾을 수 없습니다."
                return

            # spec.md 등 컨텍스트 문서 읽기
            spec_content = ""
            data_dir = resolve_data_dir(Path(repo_path))
            context_dir = data_dir / "context"
            if context_dir.exists():
                spec_files = list(context_dir.glob("*.md"))
                parts = []
                for sf in sorted(spec_files):
                    try:
                        parts.append(f"### {sf.name}\n{sf.read_text(encoding='utf-8')}")
                    except OSError:
                        pass
                spec_content = "\n\n".join(parts)

            # 오케스트레이터 마크다운 보고서 읽기 (있으면 raw 로그 대신 사용)
            orch_report = ""
            reports_dir = data_dir / "reports"
            orch_report_path = reports_dir / f"{task_id}_orchestrator_report.md"
            if orch_report_path.exists():
                try:
                    orch_report = orch_report_path.read_text(encoding="utf-8")
                except OSError:
                    pass

            redesign_info = get_redesign_model()
            redesign_provider = redesign_info["provider"] or LLM_PROVIDER
            redesign_model_id = redesign_info["model"] or LLM_MODEL_CAPABLE
            llm = create_redesign_llm(redesign_provider, redesign_model_id)
            result = redesign_task(task, tasks, spec_content, llm, orch_report=orch_report)

            with _redesign_lock:
                if result.success:
                    _redesign_jobs[job_id]["status"] = "done"
                    _redesign_jobs[job_id]["action"] = result.action
                    _redesign_jobs[job_id]["explanation"] = result.explanation
                    _redesign_jobs[job_id]["tasks"] = result.tasks
                else:
                    _redesign_jobs[job_id]["status"] = "error"
                    _redesign_jobs[job_id]["error"] = result.error or "재설계 실패"

        except Exception as e:
            with _redesign_lock:
                _redesign_jobs[job_id]["status"] = "error"
                _redesign_jobs[job_id]["error"] = str(e)


@router.post("/tasks/{task_id}/redesign")
def start_task_redesign(task_id: str, body: RedesignRequest) -> dict:
    """
    실패한 태스크를 LLM이 분석하여 재설계 초안을 생성한다.
    spec.md와 tasks.yaml을 컨텍스트로 사용하며, 태스크를 분할하거나 단순화한다.
    생성은 백그라운드에서 실행되며 job_id를 즉시 반환한다.
    GET /api/tasks/redesign/{job_id} 로 결과를 조회한다.
    """
    job_id = str(uuid.uuid4())
    with _redesign_lock:
        _redesign_jobs[job_id] = {
            "status": "queued",
            "action": None,
            "explanation": None,
            "tasks": None,
            "error": None,
        }
    threading.Thread(
        target=_run_redesign,
        args=(job_id, task_id, body.tasks_path, body.repo_path),
        daemon=True,
    ).start()
    return {"job_id": job_id, "status": "running"}


@router.get("/tasks/redesign/{job_id}")
def get_redesign_status(job_id: str) -> dict:
    """태스크 재설계 잡의 상태와 결과를 반환한다."""
    with _redesign_lock:
        job = _redesign_jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"재설계 잡 '{job_id}'를 찾을 수 없습니다.")
    return {"job_id": job_id, **job}


_FIX_DEPS_SYSTEM = """\
You are a task dependency graph expert. Remove the minimum number of depends_on entries to eliminate all circular dependencies.

CRITICAL: Your response must contain ONLY a valid JSON object. No explanation text before or after. No markdown code blocks. No reasoning. Start your response with '{' and end with '}'.

Rules:
- Remove only what is necessary to break cycles
- Prefer removing dependencies that are least logically important based on task titles
- Write a short Korean explanation in the "explanation" field
- Include ALL task IDs in the output

Required JSON format (output this and nothing else):
{"explanation": "수정 이유 요약", "tasks": [{"id": "task-001", "depends_on": [...]}, ...]}
"""


class FixDepsRequest(BaseModel):
    tasks: list[dict]


@router.post("/tasks/fix-dependencies")
def fix_dependencies(body: FixDepsRequest) -> dict:
    """순환 참조를 LLM이 분석하여 자동으로 수정한다."""
    summary = [
        {"id": t.get("id"), "title": t.get("title", ""), "depends_on": t.get("depends_on", [])}
        for t in body.tasks
    ]
    client = create_client(
        LLM_PROVIDER,
        LLMConfig(model=LLM_MODEL_CAPABLE, max_tokens=4096, system_prompt=_FIX_DEPS_SYSTEM),
    )
    prompt = f"다음 태스크 목록의 순환 참조를 수정하세요:\n{json.dumps(summary, ensure_ascii=False, indent=2)}"
    llm_response = client.chat([Message(role="user", content=prompt)])

    raw = ""
    for block in llm_response.content:
        if isinstance(block, dict) and block.get("type") == "text":
            raw = block["text"]
            break
        if hasattr(block, "type") and block.type == "text":
            raw = block.text
            break

    # 응답에서 JSON 객체 추출 (reasoning 텍스트가 앞에 붙는 경우 대응)
    result: Any = None
    # 1순위: 코드블록 내 JSON
    code_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if code_match:
        candidate = code_match.group(1)
    else:
        # 2순위: 첫 번째 '{' ~ 마지막 '}' 사이 추출
        start = raw.find("{")
        end = raw.rfind("}")
        candidate = raw[start : end + 1] if start != -1 and end != -1 else ""

    try:
        result = json.loads(candidate)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=500, detail=f"LLM 응답 파싱 실패: {e}\n응답:\n{raw[:400]}")

    fixed_deps: dict[str, list[str]] = {
        item["id"]: item.get("depends_on", [])
        for item in result.get("tasks", [])
        if "id" in item
    }

    fixed_tasks = [
        {**t, "depends_on": fixed_deps.get(t.get("id", ""), t.get("depends_on", []))}
        for t in body.tasks
    ]
    return {"tasks": fixed_tasks, "explanation": result.get("explanation", "")}


@router.post("/tasks")
def save_tasks_endpoint(body: SaveTasksRequest) -> dict:
    """태스크 목록을 YAML 파일로 저장한다.

    저장 전 유효하지 않은 depends_on 참조(삭제된 태스크 ID)를 자동으로 제거한다.
    """
    try:
        task_objs = [Task.from_dict(t) for t in body.tasks]
    except (KeyError, ValueError) as e:
        raise HTTPException(status_code=422, detail=f"태스크 형식 오류: {e}")

    # 저장될 태스크 ID 집합 확정 후 dangling depends_on 제거
    valid_ids = {t.id for t in task_objs}
    cleaned: list[str] = []
    for task in task_objs:
        before = task.depends_on
        task.depends_on = [d for d in before if d in valid_ids]
        removed = set(before) - set(task.depends_on)
        for r in removed:
            cleaned.append(f"{task.id}.depends_on에서 '{r}' 제거")

    path = resolve_tasks_path(body.tasks_path)
    save_tasks(task_objs, path)
    return {"saved": len(task_objs), "path": str(path), "cleaned_deps": cleaned}
