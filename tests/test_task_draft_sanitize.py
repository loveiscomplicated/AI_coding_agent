"""
tests/test_task_draft_sanitize.py

`_sanitize_task_draft` 보정 로직 테스트.
- target_files 깊은 경로 정리 (기존 동작 회귀 방지)
- description 길이 경고
- description 권장 섹션 누락 경고
"""

from __future__ import annotations

import os

# backend.config는 import 시점에 API 키를 요구하므로 테스트용 더미 값을 세팅한다.
# setdefault는 빈 문자열이 이미 세팅된 경우 no-op이므로 직접 할당한다.
if not os.environ.get("ANTHROPIC_API_KEY"):
    os.environ["ANTHROPIC_API_KEY"] = "test-key"
if not os.environ.get("OPENAI_API_KEY"):
    os.environ["OPENAI_API_KEY"] = "test-key"

from backend.routers.tasks import (  # noqa: E402
    _find_missing_sections,
    _sanitize_task_draft,
)


# ── 기존 보정 동작 회귀 방지 ─────────────────────────────────────────────────

def test_preserves_short_single_level_path() -> None:
    task = {
        "id": "task-001",
        "description": _make_full_description(),
        "target_files": ["user.py", "models/user.py"],
    }
    warnings: list[str] = []
    _sanitize_task_draft(task, warnings)

    assert task["target_files"] == ["user.py", "models/user.py"]
    assert not any("target_files" in w for w in warnings)


def test_strips_src_prefix_without_warning_when_final_unchanged() -> None:
    # src/ 제거 후에도 깊이 1 유지되는 일반 케이스
    task = {
        "id": "task-002",
        "description": _make_full_description(),
        "target_files": ["src/models/user.py"],
    }
    warnings: list[str] = []
    _sanitize_task_draft(task, warnings)

    assert task["target_files"] == ["models/user.py"]
    assert any("target_files" in w for w in warnings)


def test_flattens_deep_package_path() -> None:
    task = {
        "id": "task-003",
        "description": _make_full_description(),
        "target_files": [
            "app/src/main/java/com/example/FakeMapService.kt",
            "app/src/main/java/com/example/Coordinate.kt",
        ],
    }
    warnings: list[str] = []
    _sanitize_task_draft(task, warnings)

    assert task["target_files"] == ["FakeMapService.kt", "Coordinate.kt"]
    assert any("깊은 경로 정리" in w for w in warnings)


def test_deduplicates_after_normalization() -> None:
    task = {
        "id": "task-004",
        "description": _make_full_description(),
        "target_files": [
            "app/src/main/java/a/Foo.kt",
            "app/src/main/java/b/Foo.kt",
        ],
    }
    warnings: list[str] = []
    _sanitize_task_draft(task, warnings)

    assert task["target_files"] == ["Foo.kt"]


# ── description 길이 경고 ────────────────────────────────────────────────────

def test_short_description_emits_warning() -> None:
    task = {
        "id": "task-005",
        "description": "짧은 설명",  # 100자 미만
        "target_files": ["x.py"],
    }
    warnings: list[str] = []
    _sanitize_task_draft(task, warnings)

    assert any("100자 미만" in w and "task-005" in w for w in warnings)


def test_long_description_with_sections_no_length_warning() -> None:
    task = {
        "id": "task-006",
        "description": _make_full_description(),
        "target_files": ["x.py"],
    }
    warnings: list[str] = []
    _sanitize_task_draft(task, warnings)

    assert not any("100자 미만" in w for w in warnings)


# ── 섹션 누락 경고 ───────────────────────────────────────────────────────────

def test_missing_sections_emits_warning() -> None:
    # 길이는 충분하지만 섹션 키워드가 없는 경우
    task = {
        "id": "task-007",
        "description": (
            "메트릭 수집기를 구현한다. Task Report를 YAML 형식으로 저장하고, 로드하고, 집계하는 "
            "기능을 제공한다. " * 3
        ),
        "target_files": ["x.py"],
    }
    warnings: list[str] = []
    _sanitize_task_draft(task, warnings)

    section_warnings = [w for w in warnings if "권장 섹션이 누락" in w]
    assert len(section_warnings) == 1
    assert "task-007" in section_warnings[0]
    # 4개 섹션 모두 누락되었어야 함
    for keyword in ("목적", "기술 요구", "인접", "비고려"):
        assert keyword in section_warnings[0]


def test_partial_sections_reports_only_missing() -> None:
    task = {
        "id": "task-008",
        "description": (
            "### 목적과 배경\n이 태스크는 ... 매우 중요한 목적을 가진다. " * 2
            + "\n\n### 기술 요구사항\n입력은 ... 출력은 ... " * 2
        ),
        "target_files": ["x.py"],
    }
    warnings: list[str] = []
    _sanitize_task_draft(task, warnings)

    section_warnings = [w for w in warnings if "권장 섹션이 누락" in w]
    assert len(section_warnings) == 1
    assert "인접" in section_warnings[0]
    assert "비고려" in section_warnings[0]
    # 이미 포함된 섹션은 누락 목록에 없어야 함
    assert "목적" not in section_warnings[0]
    assert "기술 요구" not in section_warnings[0]


# ── 헤더 기반 매칭 / 한·영 별칭 ──────────────────────────────────────────────

def test_english_headers_accepted() -> None:
    description = (
        "### Purpose and Background\n"
        "Explain why this task exists in the overall system. " * 2
        + "\n\n### Technical Requirements\n"
        "Input is string, output is structured. " * 2
        + "\n\n### Adjacent Context\n"
        "Consumed by task-002 (tests). " * 2
        + "\n\n### Out of Scope\n"
        "Persistence is not handled here. " * 2
    )
    assert _find_missing_sections(description) == []

    task = {"id": "task-en", "description": description, "target_files": ["x.py"]}
    warnings: list[str] = []
    _sanitize_task_draft(task, warnings)
    assert not any("권장 섹션이 누락" in w for w in warnings)


def test_mixed_ko_en_headers_accepted() -> None:
    # 앞 2개는 한국어, 뒤 2개는 영어 헤더
    description = (
        "### 목적과 배경\n첫 섹션 설명입니다. " * 2
        + "\n\n### 기술 요구사항\n두번째 섹션 설명. " * 2
        + "\n\n### Adjacent Context\nDepended on by task-002. " * 2
        + "\n\n### Not in Scope\nAuth is deferred to task-099. " * 2
    )
    assert _find_missing_sections(description) == []


def test_non_goals_alias_matches_out_of_scope_section() -> None:
    description = (
        "### 목적과 배경\n...설명... " * 2
        + "\n\n### 기술 요구사항\n...사양... " * 2
        + "\n\n### 인접 컨텍스트\n...맥락... " * 2
        + "\n\n### Non-Goals\nCaching is out of scope. " * 2
    )
    assert _find_missing_sections(description) == []


def test_keywords_in_body_without_headers_are_not_accepted() -> None:
    # 헤더 없이 본문에만 키워드가 등장하면 섹션으로 인정하지 않는다.
    description = (
        "이 태스크는 목적과 배경이 명확합니다. 기술 요구사항도 좋고 "
        "인접 컨텍스트도 풍부하며 비고려 항목까지 논의했습니다. " * 3
    )
    assert len(description) > 100  # 길이 경고와 무관해야 함을 확인
    missing = _find_missing_sections(description)
    assert set(missing) == {"목적과 배경", "기술 요구사항", "인접 컨텍스트", "비고려 항목"}


def test_alternative_header_levels_and_spacing() -> None:
    # ## 수준 헤더, 선행 공백, 트레일링 #도 Markdown 헤더로 허용되어야 한다
    description = (
        "## 목적과 배경 ##\n본문. " * 2
        + "\n\n#### 기술 요구사항\n본문. " * 2
        + "\n\n   ### 인접 컨텍스트\n본문. " * 2
        + "\n\n# 비고려 항목\n본문. " * 2
    )
    assert _find_missing_sections(description) == []


def test_report_uses_korean_labels_for_missing_sections() -> None:
    # 영어로 2개만 썼을 때 누락 보고는 한국어 라벨로 나와야 읽기 쉽다.
    description = (
        "### Purpose\n본문... " * 2
        + "\n\n### Requirements\n본문... " * 2
    )
    missing = _find_missing_sections(description)
    assert "인접 컨텍스트" in missing
    assert "비고려 항목" in missing
    assert "목적과 배경" not in missing
    assert "기술 요구사항" not in missing


def test_full_four_section_description_no_section_warning() -> None:
    task = {
        "id": "task-009",
        "description": _make_full_description(),
        "target_files": ["x.py"],
    }
    warnings: list[str] = []
    _sanitize_task_draft(task, warnings)

    assert not any("권장 섹션이 누락" in w for w in warnings)


# ── 경고는 실패가 아님 (보정 로직이 task dict를 무효화하지 않음) ────────────

def test_sanitize_does_not_remove_task_on_bad_description() -> None:
    task = {
        "id": "task-010",
        "title": "short",
        "description": "한 줄",
        "target_files": ["ok.py"],
        "acceptance_criteria": ["passes tests"],
    }
    warnings: list[str] = []
    _sanitize_task_draft(task, warnings)

    # description 문제가 있어도 태스크 자체는 보존됨
    assert task["description"] == "한 줄"
    assert task["target_files"] == ["ok.py"]
    assert task["acceptance_criteria"] == ["passes tests"]
    assert warnings  # 경고는 발생해야 함


def test_missing_description_field() -> None:
    # description 키가 아예 없어도 KeyError 없이 경고만 발생
    task = {"id": "task-011", "target_files": ["x.py"]}
    warnings: list[str] = []
    _sanitize_task_draft(task, warnings)

    assert any("100자 미만" in w for w in warnings)
    assert any("권장 섹션이 누락" in w for w in warnings)


# ── 헬퍼 ────────────────────────────────────────────────────────────────────

def _make_full_description() -> str:
    """4개 섹션을 모두 포함하면서 100자 이상인 유효한 description."""
    return (
        "### 목적과 배경\n"
        "이 태스크는 프로젝트 전체의 기반 모듈을 정의한다. 후속 태스크가 이 결과를 소비한다.\n\n"
        "### 기술 요구사항\n"
        "입력은 문자열, 출력은 구조화된 객체. 알고리즘은 결정적이어야 한다.\n\n"
        "### 인접 컨텍스트\n"
        "선행 태스크 없음. 후속 태스크: task-002 (테스트), task-003 (통합).\n\n"
        "### 비고려 항목\n"
        "DB 연동은 이 태스크의 범위가 아님. 파일 기반만 고려한다."
    )
