"""
tests/test_complexity.py

orchestrator/complexity.py 단위 테스트.

실행:
    pytest tests/test_complexity.py -v
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from orchestrator.complexity import compute_complexity, normalize_complexity, SIMPLE_THRESHOLDS
from orchestrator.task import Task


# ── compute_complexity ────────────────────────────────────────────────────────


class TestComputeComplexity:
    def _dict(self, **kwargs):
        base = {
            "target_files": ["src/auth.py"],
            "depends_on": [],
            "acceptance_criteria": ["조건 하나"],
            "description": "짧은 설명",
        }
        base.update(kwargs)
        return base

    def test_simple_classification_minimal_task(self):
        task = self._dict()
        assert compute_complexity(task) == "simple"

    def test_non_simple_when_multiple_files(self):
        task = self._dict(target_files=["src/a.py", "src/b.py"])
        assert compute_complexity(task) == "non-simple"

    def test_non_simple_when_has_dependencies(self):
        task = self._dict(depends_on=["task-001"])
        assert compute_complexity(task) == "non-simple"

    def test_non_simple_when_too_many_criteria(self):
        task = self._dict(acceptance_criteria=["a", "b", "c", "d"])
        assert compute_complexity(task) == "non-simple"

    def test_non_simple_when_description_too_long(self):
        long_desc = "x" * (SIMPLE_THRESHOLDS["description_max_chars"] + 1)
        task = self._dict(description=long_desc)
        assert compute_complexity(task) == "non-simple"

    def test_non_simple_when_no_target_files(self):
        task = self._dict(target_files=[])
        assert compute_complexity(task) == "non-simple"

    def test_criteria_at_boundary_is_simple(self):
        task = self._dict(acceptance_criteria=["a", "b", "c"])
        assert compute_complexity(task) == "simple"

    def test_description_at_boundary_is_simple(self):
        task = self._dict(description="x" * SIMPLE_THRESHOLDS["description_max_chars"])
        assert compute_complexity(task) == "simple"

    def test_missing_fields_treated_as_non_simple(self):
        assert compute_complexity({}) == "non-simple"

    def test_dataclass_input_supported(self):
        task = Task(
            id="t", title="T", description="짧은 설명",
            acceptance_criteria=["하나"],
            target_files=["src/x.py"],
        )
        assert compute_complexity(task) == "simple"

    def test_dataclass_non_simple_when_multiple_files(self):
        task = Task(
            id="t", title="T", description="d",
            acceptance_criteria=["a"],
            target_files=["a.py", "b.py"],
        )
        assert compute_complexity(task) == "non-simple"


# ── normalize_complexity ──────────────────────────────────────────────────────


class TestNormalizeComplexity:
    def test_normalize_standard_to_non_simple(self):
        assert normalize_complexity("standard") == "non-simple"

    def test_normalize_complex_to_non_simple(self):
        assert normalize_complexity("complex") == "non-simple"

    def test_normalize_simple_preserved(self):
        assert normalize_complexity("simple") == "simple"

    def test_normalize_non_simple_preserved(self):
        assert normalize_complexity("non-simple") == "non-simple"

    def test_normalize_none_returns_none(self):
        assert normalize_complexity(None) is None

    def test_normalize_unknown_returns_none(self):
        assert normalize_complexity("unknown_value") is None

    def test_normalize_empty_string_returns_none(self):
        assert normalize_complexity("") is None
