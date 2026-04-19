"""
tests/test_quality_gate.py

orchestrator/quality_gate.py 단위 테스트.

각 룰(check 함수) 의 pass/fail 케이스를 tmp_path 기반으로 검증하고,
verdict 집계 (BLOCKED / WARNING / PASS) 로직을 별도로 확인한다. 순수 함수
설계이므로 LLM/Docker mock 이 필요 없다.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from orchestrator.quality_gate import (
    QGRule, QGResult, QGVerdict,
    RULES,
    aggregate,
    collect_test_files,
    is_python_task,
    run_quality_gate,
    verdict_to_rule_results_dict,
    _check_syntax,
    _check_has_test_function,
    _check_has_assertion,
    _check_not_placeholder,
    _check_imports,
    _check_coverage,
)
from orchestrator.task import Task


# ── 픽스처 ────────────────────────────────────────────────────────────────────


def _task(
    target_files: list[str] | None = None,
    acceptance_criteria: list[str] | None = None,
    task_id: str = "task-qg",
) -> Task:
    # `[] or default` 가 default 를 반환하는 것을 막기 위해 is None 비교
    if acceptance_criteria is None:
        acceptance_criteria = ["로그인 성공 시 True 반환"]
    if target_files is None:
        target_files = ["src/auth.py"]
    return Task(
        id=task_id,
        title="Quality Gate 테스트",
        description="테스트용 태스크",
        acceptance_criteria=acceptance_criteria,
        target_files=target_files,
    )


@pytest.fixture
def tests_dir(tmp_path: Path) -> Path:
    d = tmp_path / "tests"
    d.mkdir()
    # src 디렉토리도 같이 만들어 imports 룰에서 참조 가능하도록
    (tmp_path / "src").mkdir()
    return d


def _write(tests_dir: Path, name: str, content: str) -> Path:
    p = tests_dir / name
    p.write_text(content, encoding="utf-8")
    return p


# ── 개별 룰: syntax_valid ──────────────────────────────────────────────────────


class TestSyntaxValidRule:
    def test_passes_on_valid_python(self, tests_dir: Path):
        _write(tests_dir, "test_ok.py", "def test_a():\n    assert 1 == 1\n")
        result = _check_syntax(tests_dir, _task())
        assert result.passed is True

    def test_fails_on_syntax_error(self, tests_dir: Path):
        _write(tests_dir, "test_bad.py", "def test_a(:\n    pass\n")
        result = _check_syntax(tests_dir, _task())
        assert result.passed is False
        assert "test_bad.py" in result.message

    def test_passes_when_no_test_files(self, tests_dir: Path):
        # has_test_function 룰이 처리할 문제 — syntax 는 트리거 안 함
        result = _check_syntax(tests_dir, _task())
        assert result.passed is True


# ── 개별 룰: has_test_function ─────────────────────────────────────────────────


class TestHasTestFunctionRule:
    def test_passes_when_test_function_present(self, tests_dir: Path):
        _write(tests_dir, "test_auth.py", "def test_login():\n    assert True\n")
        result = _check_has_test_function(tests_dir, _task())
        assert result.passed is True

    def test_fails_when_no_test_files(self, tests_dir: Path):
        result = _check_has_test_function(tests_dir, _task())
        assert result.passed is False
        assert "테스트 파일이 없습니다" in result.message

    def test_fails_when_only_module_level_code(self, tests_dir: Path):
        _write(tests_dir, "test_no_fn.py", "import pytest\n\nx = 1\n")
        result = _check_has_test_function(tests_dir, _task())
        assert result.passed is False
        assert "test_* 함수가 없습니다" in result.message


# ── 개별 룰: has_assertion ─────────────────────────────────────────────────────


class TestHasAssertionRule:
    def test_passes_with_assert_statement(self, tests_dir: Path):
        _write(tests_dir, "test_x.py", "def test_x():\n    assert 1 + 1 == 2\n")
        result = _check_has_assertion(tests_dir, _task())
        assert result.passed is True

    def test_passes_with_unittest_assertion(self, tests_dir: Path):
        _write(tests_dir, "test_x.py",
               "def test_x(self):\n    self.assertEqual(1, 1)\n")
        result = _check_has_assertion(tests_dir, _task())
        assert result.passed is True

    def test_passes_with_pytest_raises(self, tests_dir: Path):
        _write(tests_dir, "test_x.py",
               "import pytest\n"
               "def test_x():\n"
               "    with pytest.raises(ValueError):\n"
               "        raise ValueError('x')\n")
        result = _check_has_assertion(tests_dir, _task())
        assert result.passed is True

    def test_fails_on_empty_body(self, tests_dir: Path):
        _write(tests_dir, "test_x.py", "def test_x():\n    x = 1\n")
        result = _check_has_assertion(tests_dir, _task())
        assert result.passed is False
        assert "test_x.py::test_x" in result.message


# ── 개별 룰: not_placeholder ───────────────────────────────────────────────────


class TestNotPlaceholderRule:
    def test_passes_on_real_test(self, tests_dir: Path):
        _write(tests_dir, "test_x.py",
               "def test_x():\n    assert compute() == 42\n")
        result = _check_not_placeholder(tests_dir, _task())
        assert result.passed is True

    def test_fails_on_pass_only_body(self, tests_dir: Path):
        _write(tests_dir, "test_x.py", "def test_x():\n    pass\n")
        result = _check_not_placeholder(tests_dir, _task())
        assert result.passed is False
        assert "pass 단독" in result.message

    def test_fails_on_assert_true_only(self, tests_dir: Path):
        _write(tests_dir, "test_x.py", "def test_x():\n    assert True\n")
        result = _check_not_placeholder(tests_dir, _task())
        assert result.passed is False
        assert "assert True/False" in result.message

    def test_fails_on_assert_false_only(self, tests_dir: Path):
        _write(tests_dir, "test_x.py", "def test_x():\n    assert False\n")
        result = _check_not_placeholder(tests_dir, _task())
        assert result.passed is False

    def test_fails_on_trivial_self_equality(self, tests_dir: Path):
        # 회귀 가드 (리뷰 피드백 #2): `assert 1 == 1` 같은 trivial compare 를
        # placeholder 로 잡는다. 이전에는 Constant 만 flag 해서 빠져나갔다.
        _write(tests_dir, "test_x.py", "def test_x():\n    assert 1 == 1\n")
        result = _check_not_placeholder(tests_dir, _task())
        assert result.passed is False

    def test_fails_on_trivial_string_equality(self, tests_dir: Path):
        _write(tests_dir, "test_x.py", "def test_x():\n    assert 'a' == 'a'\n")
        result = _check_not_placeholder(tests_dir, _task())
        assert result.passed is False

    def test_fails_on_trivial_binop(self, tests_dir: Path):
        # `1 + 1 == 2` — 모든 노드가 상수 → 정적 평가 가능 → placeholder
        _write(tests_dir, "test_x.py",
               "def test_x():\n    assert 1 + 1 == 2\n")
        result = _check_not_placeholder(tests_dir, _task())
        assert result.passed is False

    def test_fails_on_trivial_membership(self, tests_dir: Path):
        # `1 in [1, 2]` — 모든 노드가 상수 → placeholder
        _write(tests_dir, "test_x.py",
               "def test_x():\n    assert 1 in [1, 2]\n")
        result = _check_not_placeholder(tests_dir, _task())
        assert result.passed is False

    def test_fails_on_trivial_is_none(self, tests_dir: Path):
        _write(tests_dir, "test_x.py",
               "def test_x():\n    assert None is None\n")
        result = _check_not_placeholder(tests_dir, _task())
        assert result.passed is False

    def test_passes_when_call_present_in_assertion(self, tests_dir: Path):
        # Call 이 있으면 placeholder 가 아님 (실제 코드 호출)
        _write(tests_dir, "test_x.py",
               "def test_x():\n"
               "    x = [1, 2, 3]\n"
               "    assert len(x) == 3\n")
        result = _check_not_placeholder(tests_dir, _task())
        assert result.passed is True

    def test_passes_when_name_reference_in_assertion(self, tests_dir: Path):
        # Name 참조만 있어도 정적 평가 불가 → placeholder 아님
        _write(tests_dir, "test_x.py",
               "def test_x():\n"
               "    x = 1\n"
               "    assert x == 1\n")
        result = _check_not_placeholder(tests_dir, _task())
        assert result.passed is True

    def test_fails_on_skeleton_unchanged(self, tests_dir: Path):
        _write(tests_dir, "test_auth.py",
               "import pytest\n\n\n# TODO: tests for task task-qg\n")
        result = _check_not_placeholder(tests_dir, _task(task_id="task-qg"))
        assert result.passed is False
        assert "스켈레톤" in result.message


# ── 개별 룰: imports_resolvable ────────────────────────────────────────────────


class TestImportsResolvableRule:
    def test_passes_when_target_file_module_exists(self, tmp_path: Path):
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "auth.py").write_text("def login(): return True\n")
        _write(tests_dir, "test_auth.py",
               "from auth import login\n\n"
               "def test_login():\n    assert login() is True\n")
        result = _check_imports(tests_dir, _task(target_files=["src/auth.py"]))
        assert result.passed is True

    def test_fails_when_src_module_missing(self, tmp_path: Path):
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        # src/auth.py 가 존재하지 않음
        _write(tests_dir, "test_auth.py",
               "from src.auth import login\n\n"
               "def test_login():\n    assert login() is True\n")
        result = _check_imports(tests_dir, _task(target_files=["src/auth.py"]))
        assert result.passed is False
        assert "src.auth" in result.message

    def test_passes_on_stdlib_imports(self, tmp_path: Path):
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tmp_path / "src").mkdir()
        _write(tests_dir, "test_x.py",
               "import os\nimport pytest\n\n"
               "def test_env():\n    assert os.environ is not None\n")
        result = _check_imports(tests_dir, _task())
        assert result.passed is True

    def test_passes_with_nested_package(self, tmp_path: Path):
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        src_dir = tmp_path / "src"
        (src_dir / "models").mkdir(parents=True)
        (src_dir / "models" / "__init__.py").write_text("")
        (src_dir / "models" / "user.py").write_text("class User: pass\n")
        _write(tests_dir, "test_user.py",
               "from models.user import User\n\n"
               "def test_u():\n    assert User()\n")
        result = _check_imports(tests_dir, _task(
            target_files=["src/models/user.py"],
        ))
        assert result.passed is True


# ── 개별 룰: covers_acceptance (WARNING) ───────────────────────────────────────


class TestCoversAcceptanceRule:
    def test_passes_when_criterion_tokens_in_tests(self, tests_dir: Path):
        _write(tests_dir, "test_login.py",
               "def test_login_success():\n"
               "    assert login('u', 'p') is True\n")
        task = _task(
            acceptance_criteria=["login 성공 시 True 반환"],
        )
        result = _check_coverage(tests_dir, task)
        assert result.passed is True

    def test_fails_when_criterion_never_mentioned(self, tests_dir: Path):
        _write(tests_dir, "test_unrelated.py",
               "def test_unrelated():\n    assert 1 == 1\n")
        task = _task(
            acceptance_criteria=["비밀번호 암호화 저장 동작"],
        )
        result = _check_coverage(tests_dir, task)
        assert result.passed is False
        assert "미커버" in result.message

    def test_passes_when_no_acceptance_criteria(self, tests_dir: Path):
        _write(tests_dir, "test_x.py",
               "def test_x():\n    assert 1 == 1\n")
        task = _task(acceptance_criteria=[])
        result = _check_coverage(tests_dir, task)
        assert result.passed is True

    def test_fails_when_no_test_files(self, tests_dir: Path):
        task = _task(acceptance_criteria=["기능 A 동작"])
        result = _check_coverage(tests_dir, task)
        assert result.passed is False


# ── aggregate / run_quality_gate ───────────────────────────────────────────────


class TestAggregate:
    def test_pass_verdict_when_all_rules_pass(self):
        results = [(r.rule_id, QGResult(passed=True)) for r in RULES]
        assert aggregate(results) == "PASS"

    def test_blocked_verdict_when_any_blocking_rule_fails(self):
        # syntax_valid 는 BLOCKING
        results = [
            ("syntax_valid", QGResult(passed=False, message="broken")),
            ("has_test_function", QGResult(passed=True)),
            ("has_assertion", QGResult(passed=True)),
            ("not_placeholder", QGResult(passed=True)),
            ("imports_resolvable", QGResult(passed=True)),
            ("covers_acceptance", QGResult(passed=True)),
        ]
        assert aggregate(results) == "BLOCKED"

    def test_warning_verdict_when_only_warning_rules_fail(self):
        # covers_acceptance 만 WARNING — 그것만 실패
        results = [
            ("syntax_valid", QGResult(passed=True)),
            ("has_test_function", QGResult(passed=True)),
            ("has_assertion", QGResult(passed=True)),
            ("not_placeholder", QGResult(passed=True)),
            ("imports_resolvable", QGResult(passed=True)),
            ("covers_acceptance", QGResult(passed=False, message="미커버")),
        ]
        assert aggregate(results) == "WARNING"

    def test_blocked_takes_precedence_over_warning(self):
        # BLOCKING 과 WARNING 이 동시에 실패하면 BLOCKED
        results = [
            ("syntax_valid", QGResult(passed=False, message="e")),
            ("covers_acceptance", QGResult(passed=False, message="미커버")),
            ("has_test_function", QGResult(passed=True)),
            ("has_assertion", QGResult(passed=True)),
            ("not_placeholder", QGResult(passed=True)),
            ("imports_resolvable", QGResult(passed=True)),
        ]
        assert aggregate(results) == "BLOCKED"

    def test_unknown_rule_id_treated_as_blocking(self):
        # 방어적: 등록되지 않은 룰 id 는 BLOCKED 로 안전 처리
        results = [("unknown_rule", QGResult(passed=False, message="?"))]
        assert aggregate(results) == "BLOCKED"


# ── run_quality_gate 통합 ──────────────────────────────────────────────────────


class TestRunQualityGate:
    def test_pass_on_real_test_file(self, tmp_path: Path):
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "auth.py").write_text("def login(u, p): return True\n")
        _write(tests_dir, "test_auth.py",
               "from auth import login\n\n"
               "def test_login_success():\n"
               "    assert login('u', 'p') is True\n")
        task = _task(
            target_files=["src/auth.py"],
            acceptance_criteria=["login 성공 시 True 반환"],
        )
        verdict = run_quality_gate(tests_dir, task)
        assert verdict.verdict == "PASS"
        assert all(r.passed for _, r in verdict.rule_results)

    def test_blocked_on_skeleton_unchanged(self, tmp_path: Path):
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tmp_path / "src").mkdir()
        _write(tests_dir, "test_auth.py",
               "import pytest\n\n\n# TODO: tests for task task-009\n")
        task = _task(task_id="task-009", target_files=["src/auth.py"])
        verdict = run_quality_gate(tests_dir, task)
        assert verdict.verdict == "BLOCKED"
        # not_placeholder 가 실패했어야 함
        rule_map = dict(verdict.rule_results)
        assert rule_map["not_placeholder"].passed is False

    def test_warning_on_uncovered_criterion(self, tmp_path: Path):
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "auth.py").write_text("def login(): return True\n")
        _write(tests_dir, "test_auth.py",
               "from auth import login\n\n"
               "def test_login_basic():\n"
               "    assert login() is True\n")
        task = _task(
            target_files=["src/auth.py"],
            acceptance_criteria=[
                "비밀번호 암호화 저장",  # 테스트에서 전혀 언급 안 됨
            ],
        )
        verdict = run_quality_gate(tests_dir, task)
        assert verdict.verdict == "WARNING"
        rule_map = dict(verdict.rule_results)
        assert rule_map["covers_acceptance"].passed is False

    def test_failed_rules_filter(self, tmp_path: Path):
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tmp_path / "src").mkdir()
        # 아무 파일도 없음 → 여러 룰 BLOCKING 실패
        verdict = run_quality_gate(tests_dir, _task())
        assert verdict.verdict == "BLOCKED"
        blocking_failures = verdict.failed_rules("BLOCKING")
        assert any(rid == "has_test_function" for rid, _ in blocking_failures)

    def test_check_exception_does_not_crash(self, tmp_path: Path):
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()

        def _boom(_td, _task):
            raise RuntimeError("boom")

        custom_rules = [
            QGRule(
                rule_id="boom_rule",
                severity="BLOCKING",
                description="exception thrower",
                check=_boom,
            ),
        ]
        verdict = run_quality_gate(tests_dir, _task(), rules=custom_rules)
        assert verdict.verdict == "BLOCKED"
        rule_map = dict(verdict.rule_results)
        assert rule_map["boom_rule"].passed is False
        assert "[CHECK_ERROR]" in rule_map["boom_rule"].message

    def test_verdict_to_rule_results_dict_roundtrip(self, tmp_path: Path):
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tmp_path / "src").mkdir()
        verdict = run_quality_gate(tests_dir, _task())
        dicts = verdict_to_rule_results_dict(verdict)
        assert isinstance(dicts, list)
        assert all({"rule_id", "passed", "message"} <= d.keys() for d in dicts)
        # 룰 id 집합이 RULES 와 동일
        assert {d["rule_id"] for d in dicts} == {r.rule_id for r in RULES}


# ── 파일 수집 유틸 ─────────────────────────────────────────────────────────────


# ── 적용 가능성 (회귀 가드) ────────────────────────────────────────────────────
#
# 리뷰 피드백 #1: Python 전용 룰이 JS/TS/doc-only 태스크를 BLOCKED 로 막는
# 회귀가 있었다. is_python_task() 로 걸러 QG 를 skip 하고 PASS 를 돌려준다.


class TestApplicability:
    def test_is_python_task_true_for_explicit_language(self):
        t = Task(
            id="t", title="x", description="d",
            acceptance_criteria=["x"], target_files=["src/a.py"],
            language="python",
        )
        assert is_python_task(t) is True

    def test_is_python_task_false_for_javascript(self):
        t = Task(
            id="t", title="x", description="d",
            acceptance_criteria=["x"], target_files=["src/app.js"],
            language="javascript",
        )
        assert is_python_task(t) is False

    def test_is_python_task_false_for_doc_only_with_default_language(self):
        # 리뷰 피드백 P1 회귀 가드: Task.language 의 기본값이 "python" 이지만
        # target_files=[] 인 doc-only 태스크는 QG 적용 대상이 아니다.
        # 이전 구현은 language == "python" 검사가 먼저 True 를 반환해서
        # target_files=[] 인 문서 전용 태스크가 BLOCKED 로 떨어졌다.
        t = Task(
            id="t-doc", title="doc", description="README 작성",
            acceptance_criteria=["항목 A 설명"],
            target_files=[],  # 명시적으로 비어있음
            # language 미지정 → 기본값 "python" 이 할당됨
        )
        assert t.language == "python", "fixture 전제: language 기본값은 python"
        assert is_python_task(t) is False, (
            "target_files=[] 는 language 기본값에 상관없이 Python 아님"
        )

    def test_is_python_task_false_for_doc_only_from_dict(self):
        # Task.from_dict 경로도 동일하게 language='python' 기본값이 적용된다.
        t = Task.from_dict({
            "id": "t-doc-yaml",
            "title": "doc",
            "description": "README",
            "acceptance_criteria": ["항목 A"],
            "target_files": [],
            # language 키 없음 → 기본 "python"
        })
        assert t.language == "python"
        assert is_python_task(t) is False

    def test_is_python_task_false_for_only_non_py_target_files(self):
        # target_files 에 비-Python 파일만 있는 경우도 Python 아님
        t = Task(
            id="t-md", title="md", description="d",
            acceptance_criteria=["x"],
            target_files=["docs/README.md", "docs/SPEC.md"],
            # language 기본값 "python" 이지만 .py 파일이 없음
        )
        assert is_python_task(t) is False

    def test_is_python_task_false_for_non_python_target_and_lang(self):
        t = Task(
            id="t", title="markdown", description="문서",
            acceptance_criteria=["x"], target_files=["docs/spec.md"],
            language="markdown",
        )
        assert is_python_task(t) is False

    def test_run_quality_gate_passes_for_js_task(self, tmp_path: Path):
        # JS 태스크 — tests/ 에 test_app.js 만 있음. Python 룰이 BLOCKED 를
        # 내면 회귀. 적용 가능성 체크로 PASS 를 돌려줘야 한다.
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_app.js").write_text(
            "test('ok', () => { expect(1).toBe(1); });\n"
        )
        (tmp_path / "src").mkdir()
        js_task = Task(
            id="task-js", title="JS", description="d",
            acceptance_criteria=["x"],
            target_files=["src/app.js"], language="javascript",
        )
        verdict = run_quality_gate(tests_dir, js_task)
        assert verdict.verdict == "PASS"
        assert verdict.rule_results == []  # 룰이 실행되지 않음

    def test_run_quality_gate_passes_for_doc_only_task(self, tmp_path: Path):
        # target_files=[] + non-python language → QG skip
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        doc_task = Task(
            id="task-doc", title="doc", description="README",
            acceptance_criteria=["항목 A"],
            target_files=[], language="markdown",
        )
        verdict = run_quality_gate(tests_dir, doc_task)
        assert verdict.verdict == "PASS"
        assert verdict.rule_results == []

    def test_run_quality_gate_passes_for_doc_only_default_language(self, tmp_path: Path):
        # 리뷰 피드백 P1 회귀 가드: target_files=[] + default language="python"
        # 이 BLOCKED 로 떨어지지 않아야 한다.
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tmp_path / "src").mkdir()
        doc_task = Task(
            id="task-doc-default", title="doc", description="README",
            acceptance_criteria=["항목 A"],
            target_files=[],
            # language 미지정 → Task 기본값 "python"
        )
        assert doc_task.language == "python", "fixture 전제: language 기본값"
        verdict = run_quality_gate(tests_dir, doc_task)
        assert verdict.verdict == "PASS"
        assert verdict.rule_results == []

    def test_run_quality_gate_passes_for_markdown_only_targets(self, tmp_path: Path):
        # target_files 에 .md 만 있고 language 는 기본값 "python" 인 경우도 skip
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tmp_path / "src").mkdir()
        doc_task = Task(
            id="task-md", title="md", description="문서",
            acceptance_criteria=["x"],
            target_files=["docs/SPEC.md"],
        )
        verdict = run_quality_gate(tests_dir, doc_task)
        assert verdict.verdict == "PASS"
        assert verdict.rule_results == []

    def test_run_quality_gate_custom_rules_bypass_applicability(self, tmp_path: Path):
        # rules=[...] 주입 경로 — 테스트 용 — 은 applicability 를 우회한다
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        js_task = Task(
            id="task-js", title="JS", description="d",
            acceptance_criteria=["x"],
            target_files=["src/app.js"], language="javascript",
        )
        custom_rules = [
            QGRule(
                rule_id="always_fail",
                severity="BLOCKING",
                description="d",
                check=lambda _td, _t: QGResult(passed=False, message="x"),
            ),
        ]
        verdict = run_quality_gate(tests_dir, js_task, rules=custom_rules)
        assert verdict.verdict == "BLOCKED"  # 룰 주입 시 Python 체크 우회


class TestCollectTestFiles:
    def test_collects_test_prefix_and_suffix(self, tmp_path: Path):
        d = tmp_path / "tests"
        d.mkdir()
        (d / "test_a.py").write_text("")
        (d / "b_test.py").write_text("")
        (d / "c.py").write_text("")  # 수집 대상 아님
        found = {p.name for p in collect_test_files(d)}
        assert found == {"test_a.py", "b_test.py"}

    def test_collects_nested_dirs(self, tmp_path: Path):
        d = tmp_path / "tests"
        (d / "sub").mkdir(parents=True)
        (d / "test_a.py").write_text("")
        (d / "sub" / "test_b.py").write_text("")
        found = {p.name for p in collect_test_files(d)}
        assert found == {"test_a.py", "test_b.py"}

    def test_returns_empty_when_no_dir(self, tmp_path: Path):
        assert collect_test_files(tmp_path / "nope") == []
