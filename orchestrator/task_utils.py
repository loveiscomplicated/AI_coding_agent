"""
orchestrator/task_utils.py — 태스크 초안 후처리 공용 유틸.

`backend/routers/tasks.py`와 `cli/task_converter.py`가 공통으로 사용한다.
CLI가 backend 레이어에 의존하지 않도록 orchestrator로 끌어올렸다.

주요 함수:
    normalize_target_path(fpath)         — target_files 경로 하나를 flat/1-level로 정규화
    sanitize_task_draft(task, warnings)  — LLM 초안의 target_files 정리 + description 경고
    find_missing_sections(description)   — description의 4섹션 헤더 누락 탐지
    extract_markdown_headers(description) — Markdown 헤더 텍스트 리스트 추출
"""

from __future__ import annotations

import re


DESCRIPTION_MIN_LENGTH = 100

# 4개 섹션 각각에 대한 한·영 헤더 별칭.
# 매칭은 Markdown 헤더(#으로 시작하는 줄)에서만 수행한다 — 본문에 키워드만 등장하는
# 경우는 섹션으로 인정하지 않는다 (헤더로 구분되지 않으면 에이전트가 파싱하기 어려움).
# 별칭은 소문자·공백 정규화 후 '포함' 매칭한다.
DESCRIPTION_SECTION_SPEC: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("목적과 배경", ("목적과 배경", "목적/배경", "목적", "배경",
                     "purpose and background", "purpose", "background")),
    ("기술 요구사항", ("기술 요구사항", "기술 요구", "요구사항",
                       "technical requirements", "requirements", "specification")),
    ("인접 컨텍스트", ("인접 컨텍스트", "인접", "컨텍스트",
                       "adjacent context", "related context", "context")),
    ("비고려 항목", ("비고려 항목", "비고려", "비범위",
                     "out of scope", "out-of-scope", "not in scope", "non-goals")),
)

MARKDOWN_HEADER_PATTERN = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", re.MULTILINE)


def normalize_target_path(fpath: str) -> str:
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


def extract_markdown_headers(description: str) -> list[str]:
    """description의 Markdown 헤더 텍스트를 소문자·공백 정규화하여 반환한다."""
    return [
        re.sub(r"\s+", " ", match.group(1)).strip().lower()
        for match in MARKDOWN_HEADER_PATTERN.finditer(description)
    ]


def find_missing_sections(description: str) -> list[str]:
    """4개 섹션 중 헤더로 나타나지 않은 섹션의 한국어 라벨 목록을 반환한다."""
    headers = extract_markdown_headers(description)
    missing: list[str] = []
    for label, aliases in DESCRIPTION_SECTION_SPEC:
        found = any(
            alias.lower() in header
            for header in headers
            for alias in aliases
        )
        if not found:
            missing.append(label)
    return missing


def sanitize_task_draft(task: dict, warnings: list[str]) -> None:
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
        sanitized = normalize_target_path(fpath)

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
    if len(description) < DESCRIPTION_MIN_LENGTH:
        warnings.append(
            f"{task_id}: description이 {DESCRIPTION_MIN_LENGTH}자 미만입니다. "
            "목적과 배경, 기술 요구사항, 인접 컨텍스트, 비고려 항목이 포함되어야 합니다."
        )

    missing = find_missing_sections(description)
    if missing:
        warnings.append(
            f"{task_id}: description에 권장 섹션이 누락됨 (Markdown 헤더 기준): {missing}"
        )


# ── 레거시 별칭 (기존 호출자 보호) ────────────────────────────────────────────
# backend/routers/tasks.py 및 tests/test_task_draft_sanitize.py가 _접두어 이름을
# 사용했으므로 하위 호환성 유지.
_normalize_target_path = normalize_target_path
_sanitize_task_draft = sanitize_task_draft
_extract_markdown_headers = extract_markdown_headers
_find_missing_sections = find_missing_sections
_DESCRIPTION_MIN_LENGTH = DESCRIPTION_MIN_LENGTH
_DESCRIPTION_SECTION_SPEC = DESCRIPTION_SECTION_SPEC
_MARKDOWN_HEADER_PATTERN = MARKDOWN_HEADER_PATTERN
