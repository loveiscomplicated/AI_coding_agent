"""
orchestrator/quality_gate.py — 테스트 파일 형식적 유효성 판정

Quality Gate 는 TestWriter 종료 직후 **1회만** 실행되어 테스트 파일의
형식적 유효성을 검사한다. 기능적 정확성 판정은 Reviewer 의 책임이다.

룰 severity:
  BLOCKING  — 실패 시 TestWriter 재시도를 트리거
  WARNING   — 실패해도 진행하되 TaskReport 에 기록

Verdict 집계:
  BLOCKED   — BLOCKING 룰이 하나라도 실패
  WARNING   — BLOCKING 전부 통과 + WARNING 룰이 하나라도 실패
  PASS      — 모든 룰 통과

각 check 함수는 파일시스템 + AST 만 사용하는 순수 함수다. LLM 호출을 하지
않아 결정적·고속이며 단위 테스트가 쉽다.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal

from orchestrator.task import Task
from orchestrator.workspace import is_skeleton_unchanged, strip_src_prefix

Severity = Literal["BLOCKING", "WARNING"]
Verdict = Literal["PASS", "WARNING", "BLOCKED"]


# ── 데이터 구조 ────────────────────────────────────────────────────────────────


@dataclass
class QGResult:
    passed: bool
    message: str = ""


@dataclass
class QGRule:
    rule_id: str
    severity: Severity
    description: str
    check: Callable[[Path, Task], QGResult]


@dataclass
class QGVerdict:
    verdict: Verdict
    rule_results: list[tuple[str, QGResult]] = field(default_factory=list)

    def failed_rules(self, severity: Severity | None = None) -> list[tuple[str, QGResult]]:
        """실패한 룰만 반환. severity 필터 가능."""
        out: list[tuple[str, QGResult]] = []
        for rid, r in self.rule_results:
            if r.passed:
                continue
            if severity is not None and _RULE_BY_ID.get(rid) and _RULE_BY_ID[rid].severity != severity:
                continue
            out.append((rid, r))
        return out


# ── 테스트 파일 수집 ───────────────────────────────────────────────────────────


_TEST_FILE_GLOBS: tuple[str, ...] = (
    "test_*.py", "*_test.py",
)


def collect_test_files(tests_dir: Path) -> list[Path]:
    """tests_dir 내 Python 테스트 파일을 수집한다 (중복 제거, 정렬).

    QG 룰의 공통 유틸이다. 비-Python 파일은 현재 QG 검증 범위 밖이다.
    """
    if not tests_dir.exists():
        return []
    seen: dict[str, Path] = {}
    for pattern in _TEST_FILE_GLOBS:
        for f in tests_dir.rglob(pattern):
            if f.is_file():
                seen[str(f)] = f
    return sorted(seen.values())


# ── 룰별 check 함수 ────────────────────────────────────────────────────────────


def _check_syntax(tests_dir: Path, task: Task) -> QGResult:
    """각 Python 테스트 파일이 ast.parse 로 파싱되는지 확인."""
    errors: list[str] = []
    for f in collect_test_files(tests_dir):
        if f.suffix != ".py":
            continue
        try:
            ast.parse(f.read_text(encoding="utf-8"))
        except SyntaxError as e:
            errors.append(f"{f.name}:{e.lineno} {e.msg}")
    if errors:
        return QGResult(passed=False, message="; ".join(errors))
    return QGResult(passed=True)


def _check_has_test_function(tests_dir: Path, task: Task) -> QGResult:
    """적어도 하나의 test_* 함수가 존재하는지 확인."""
    files = collect_test_files(tests_dir)
    if not files:
        return QGResult(
            passed=False,
            message="workspace/tests/ 에 테스트 파일이 없습니다.",
        )
    for f in files:
        if f.suffix != ".py":
            continue
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
                return QGResult(passed=True)
    return QGResult(
        passed=False,
        message="어느 테스트 파일에도 test_* 함수가 없습니다.",
    )


_ASSERT_ATTRS = frozenset({
    "assertEqual", "assertNotEqual", "assertTrue", "assertFalse",
    "assertIs", "assertIsNot", "assertIsNone", "assertIsNotNone",
    "assertIn", "assertNotIn", "assertRaises", "assertRaisesRegex",
    "assertGreater", "assertGreaterEqual", "assertLess", "assertLessEqual",
    "assertAlmostEqual", "assertRegex", "assertCountEqual",
    "assert_called", "assert_called_once", "assert_called_with",
    "assert_called_once_with", "assert_any_call", "assert_not_called",
})


def _function_has_assertion(fn: ast.FunctionDef) -> bool:
    """함수 본문 어딘가에 assert 문 또는 assertXxx / pytest.raises 호출이 있는가."""
    for child in ast.walk(fn):
        if isinstance(child, ast.Assert):
            return True
        if not isinstance(child, ast.Call):
            continue
        func = child.func
        if isinstance(func, ast.Name) and func.id in _ASSERT_ATTRS:
            return True
        if isinstance(func, ast.Attribute):
            if func.attr in _ASSERT_ATTRS:
                return True
            # pytest.raises(...) / pytest.warns(...)
            if (
                isinstance(func.value, ast.Name)
                and func.value.id == "pytest"
                and func.attr in ("raises", "warns")
            ):
                return True
    return False


def _check_has_assertion(tests_dir: Path, task: Task) -> QGResult:
    """각 test_* 함수에 assertion 이 존재하는지 확인."""
    missing: list[str] = []
    for f in collect_test_files(tests_dir):
        if f.suffix != ".py":
            continue
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not (isinstance(node, ast.FunctionDef) and node.name.startswith("test_")):
                continue
            if not _function_has_assertion(node):
                missing.append(f"{f.name}::{node.name}")
    if missing:
        return QGResult(
            passed=False,
            message="assertion 없는 test_* 함수: " + ", ".join(missing),
        )
    return QGResult(passed=True)


def _is_trivial_test_expr(expr: ast.expr) -> bool:
    """표현식이 동적 참조(Name/Call/Attribute/Subscript) 없이 상수만으로 구성돼
    정적으로 진리값이 고정되는가.

    placeholder 판정의 기저 — Call/Name 이 하나라도 끼면 "실제 코드 평가" 로
    보고 False 를 반환한다. 다음 패턴을 flag:
      - 단일 상수: `True`, `1`, `"x"`
      - 자명 compare: `1 == 1`, `"a" == "a"`, `None is None`
      - 정적 평가 가능: `1 + 1 == 2`, `1 in [1, 2]`, `not False`
    """
    if isinstance(expr, ast.Constant):
        return True
    if isinstance(expr, (ast.List, ast.Tuple, ast.Set)):
        return all(_is_trivial_test_expr(e) for e in expr.elts)
    if isinstance(expr, ast.Dict):
        keys_ok = all(
            _is_trivial_test_expr(k) for k in expr.keys if k is not None
        )
        vals_ok = all(_is_trivial_test_expr(v) for v in expr.values)
        return keys_ok and vals_ok
    if isinstance(expr, ast.Compare):
        return (
            _is_trivial_test_expr(expr.left)
            and all(_is_trivial_test_expr(c) for c in expr.comparators)
        )
    if isinstance(expr, ast.BoolOp):
        return all(_is_trivial_test_expr(v) for v in expr.values)
    if isinstance(expr, ast.UnaryOp):
        return _is_trivial_test_expr(expr.operand)
    if isinstance(expr, ast.BinOp):
        return (
            _is_trivial_test_expr(expr.left)
            and _is_trivial_test_expr(expr.right)
        )
    # Name / Call / Attribute / Subscript 등 → 실제 코드 참조
    return False


def _is_placeholder_assert(node: ast.Assert) -> bool:
    """assert 의 test 표현식이 코드 호출 없이 상수만으로 평가되는가.

    다음을 모두 placeholder 로 간주한다:
      - `assert True` / `assert False` / `assert 1` / `assert "x"`
      - `assert 1 == 1`, `assert "a" == "a"`, `assert None is None`
      - `assert 1 + 1 == 2`, `assert 1 in [1, 2]` (정적으로 참이 고정)
    """
    return _is_trivial_test_expr(node.test)


def _function_body_is_pass_only(fn: ast.FunctionDef) -> bool:
    """`def test_x(): pass` 형태."""
    body = fn.body
    # docstring 은 허용
    core = body
    if core and isinstance(core[0], ast.Expr) and isinstance(core[0].value, ast.Constant) and isinstance(core[0].value.value, str):
        core = core[1:]
    return len(core) == 1 and isinstance(core[0], ast.Pass)


def _check_not_placeholder(tests_dir: Path, task: Task) -> QGResult:
    """스켈레톤 그대로, pass 단독, assert True/False 단독 패턴 금지."""
    bad: list[str] = []
    for f in collect_test_files(tests_dir):
        if f.suffix != ".py":
            continue
        src = f.read_text(encoding="utf-8")

        # (a) 스켈레톤 미변경 감지
        if is_skeleton_unchanged(src, task.id):
            bad.append(f"{f.name}: 스켈레톤 그대로 남음")
            continue

        try:
            tree = ast.parse(src)
        except SyntaxError:
            # syntax_valid 룰이 처리
            continue

        for node in ast.walk(tree):
            if not (isinstance(node, ast.FunctionDef) and node.name.startswith("test_")):
                continue

            # (b) 함수 본문이 pass 단독
            if _function_body_is_pass_only(node):
                bad.append(f"{f.name}::{node.name}: pass 단독")
                continue

            # (c) assertion 이 전부 placeholder (assert True/False/상수) 인 경우
            asserts = [c for c in ast.walk(node) if isinstance(c, ast.Assert)]
            if asserts and all(_is_placeholder_assert(a) for a in asserts):
                bad.append(
                    f"{f.name}::{node.name}: assert True/False 만 사용"
                )
    if bad:
        return QGResult(
            passed=False,
            message="; ".join(bad),
        )
    return QGResult(passed=True)


def _target_file_module_names(task: Task) -> set[str]:
    """target_files 에서 파생 가능한 Python 모듈명 집합.

    ['src/auth.py', 'src/models/user.py'] →
      {'auth', 'models', 'models.user'}
    """
    modules: set[str] = set()
    for tf in task.target_files:
        trimmed = strip_src_prefix(tf)
        p = Path(trimmed)
        if p.suffix.lower() != ".py":
            continue
        parts = [part.replace("-", "_") for part in p.with_suffix("").parts]
        if not parts:
            continue
        modules.add(".".join(parts))
        # 상위 패키지도 포함 (e.g. 'models.user' → 'models')
        for i in range(1, len(parts)):
            modules.add(".".join(parts[:i]))
    return modules


def _module_exists_in_src(dotted: str, src_dir: Path) -> bool:
    """dotted 모듈명이 src_dir 내 파일/패키지로 해석되는지."""
    parts = [p for p in dotted.split(".") if p]
    if not parts:
        return False
    # src/foo/bar.py 또는 src/foo/bar/__init__.py
    file_path = src_dir.joinpath(*parts).with_suffix(".py")
    pkg_init = src_dir.joinpath(*parts, "__init__.py")
    return file_path.exists() or pkg_init.exists()


def _check_imports(tests_dir: Path, task: Task) -> QGResult:
    """테스트 파일의 import 가 workspace/src 내 파일로 해석 가능한지 확인.

    - `from src.X import Y` / `import src.X` → src_dir/X.py 또는 패키지 존재 확인
    - `from X import Y` / `import X` 에서 X 가 target_files 에서 유도된 모듈명
      일 때만 검증 (외부 라이브러리는 스킵)
    - 나머지 모듈 (stdlib, 서드파티) 은 스킵 — runtime 해석 불가능
    """
    files = collect_test_files(tests_dir)
    if not files:
        return QGResult(passed=True)  # has_test_function 룰이 처리

    workspace_root = tests_dir.parent
    src_dir = workspace_root / "src"
    if not src_dir.exists():
        return QGResult(passed=True)

    expected = _target_file_module_names(task)
    errors: list[str] = []

    for f in files:
        if f.suffix != ".py":
            continue
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"))
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            candidates: list[tuple[str, int]] = []
            if isinstance(node, ast.ImportFrom):
                if node.module is None or node.level != 0:
                    continue
                candidates.append((node.module, node.lineno))
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    candidates.append((alias.name, node.lineno))
            else:
                continue

            for module, lineno in candidates:
                # src.X 접두사
                if module == "src":
                    continue  # 빈 import
                if module.startswith("src."):
                    rest = module[len("src."):]
                    if not _module_exists_in_src(rest, src_dir):
                        errors.append(
                            f"{f.name}:{lineno} import '{module}' 해결 불가"
                        )
                    continue
                # 직접 import — target_files 에서 파생된 모듈만 검증
                if module in expected:
                    if not _module_exists_in_src(module, src_dir):
                        errors.append(
                            f"{f.name}:{lineno} import '{module}' 파일 없음"
                        )

    if errors:
        return QGResult(passed=False, message="; ".join(errors))
    return QGResult(passed=True)


def _extract_tokens(text: str) -> list[str]:
    """문자열에서 의미있는 토큰을 추출한다.

    - 한글: 2자 이상
    - 영숫자: 3자 이상 (식별자 패턴)
    """
    return re.findall(r"[가-힣]{2,}|[A-Za-z_][A-Za-z_0-9]{2,}", text)


def _check_coverage(tests_dir: Path, task: Task) -> QGResult:
    """acceptance_criteria 각 항목의 의미있는 토큰이 테스트 코드에 등장하는지 확인.

    heuristic 이다 — 엄격한 LLM 판정을 대체하되, false-positive 가 많으면
    WARNING 으로 처리되므로 파이프라인을 차단하지 않는다.
    """
    if not task.acceptance_criteria:
        return QGResult(passed=True)

    combined_src = ""
    for f in collect_test_files(tests_dir):
        if f.suffix != ".py":
            continue
        try:
            combined_src += f.read_text(encoding="utf-8") + "\n"
        except OSError:
            continue
    if not combined_src:
        return QGResult(
            passed=False,
            message="테스트 파일이 없어 커버리지 확인 불가",
        )

    uncovered: list[str] = []
    for criterion in task.acceptance_criteria:
        tokens = _extract_tokens(criterion)
        if not tokens:
            continue
        if not any(tok in combined_src for tok in tokens):
            truncated = criterion.strip().replace("\n", " ")
            if len(truncated) > 80:
                truncated = truncated[:77] + "..."
            uncovered.append(truncated)

    if uncovered:
        return QGResult(
            passed=False,
            message="미커버 수락 기준: " + "; ".join(uncovered),
        )
    return QGResult(passed=True)


# ── 룰 레지스트리 ──────────────────────────────────────────────────────────────


RULES: list[QGRule] = [
    QGRule(
        rule_id="syntax_valid",
        severity="BLOCKING",
        description="테스트 파일 syntax 유효",
        check=_check_syntax,
    ),
    QGRule(
        rule_id="has_test_function",
        severity="BLOCKING",
        description="최소 1개의 test_* 함수",
        check=_check_has_test_function,
    ),
    QGRule(
        rule_id="has_assertion",
        severity="BLOCKING",
        description="각 test_* 함수에 assertion 존재",
        check=_check_has_assertion,
    ),
    QGRule(
        rule_id="not_placeholder",
        severity="BLOCKING",
        description="assert True/False, pass 단독, 스켈레톤 미변경 금지",
        check=_check_not_placeholder,
    ),
    QGRule(
        rule_id="imports_resolvable",
        severity="BLOCKING",
        description="import 문이 해결 가능",
        check=_check_imports,
    ),
    QGRule(
        rule_id="covers_acceptance",
        severity="WARNING",
        description="acceptance_criteria 커버리지",
        check=_check_coverage,
    ),
]


_RULE_BY_ID: dict[str, QGRule] = {r.rule_id: r for r in RULES}


# ── 적용 가능성 ────────────────────────────────────────────────────────────────


def is_python_task(task: Task) -> bool:
    """현재 QG 룰 세트가 적용되는 Python 태스크인지 판정.

    현행 룰은 모두 ``ast.parse`` 기반으로 Python 테스트 파일만 다룬다.
    JS/TS/doc-only/non-Python 태스크는 적용 범위 밖이므로 QG 를 skip 한다
    (언어별 룰 세트가 추가되면 이 함수를 확장하거나 룰 객체에 ``applies_to``
    predicate 를 부여해 분기한다).

    판정 규칙 (모두 참이어야 True):
      1. ``target_files`` 에 ``.py`` 파일이 하나 이상 존재한다. — 중요 ⚠️
         ``Task.language`` 기본값이 ``"python"`` 이므로 language 단독으로는
         Python 여부를 확신할 수 없다. doc-only 태스크(``target_files=[]``) 는
         language 기본값이 그대로 "python" 으로 남아 있어도 실제로는 Python
         구현이 아니므로 QG 가 적용되면 안 된다. ``target_files`` 의 ``.py``
         존재 여부를 1차 판정으로 쓴다.
      2. ``task.language`` 가 ``"python"`` 이거나 미지정(fallback).
         명시적으로 다른 언어로 지정된 경우는 Python 아님.
    """
    target_files = getattr(task, "target_files", None) or []
    has_py_target = any(
        isinstance(f, str) and f.endswith(".py") for f in target_files
    )
    if not has_py_target:
        # .py 파일이 없으면 language 와 무관하게 QG 적용 불가
        # (doc-only 태스크, JS-only 태스크, 빈 target_files 등)
        return False
    language = getattr(task, "language", None)
    # 명시적으로 다른 언어 → Python 아님
    if language and language != "python":
        return False
    return True


# ── 집계 / 진입점 ──────────────────────────────────────────────────────────────


def aggregate(
    rule_results: list[tuple[str, QGResult]],
    rules: list[QGRule] | None = None,
) -> Verdict:
    """rule_results 에서 verdict 를 계산한다.

    외부에서도 테스트 가능하도록 순수 함수로 노출한다.
    """
    rule_map = {r.rule_id: r for r in (rules or RULES)}
    any_blocking_failed = False
    any_warning_failed = False
    for rid, r in rule_results:
        if r.passed:
            continue
        rule = rule_map.get(rid)
        if rule is None:
            # 미등록 룰은 BLOCKING 으로 안전 처리
            any_blocking_failed = True
            continue
        if rule.severity == "BLOCKING":
            any_blocking_failed = True
        elif rule.severity == "WARNING":
            any_warning_failed = True
    if any_blocking_failed:
        return "BLOCKED"
    if any_warning_failed:
        return "WARNING"
    return "PASS"


def run_quality_gate(
    tests_dir: Path,
    task: Task,
    rules: list[QGRule] | None = None,
) -> QGVerdict:
    """tests_dir 에 대해 모든 룰을 실행하고 QGVerdict 를 반환한다.

    Python 태스크가 아니면 (``is_python_task(task) == False``) 적용 범위 밖으로
    보고 빈 rule_results 와 함께 PASS 를 돌려준다 — JS/TS/doc-only 태스크가
    has_test_function 같은 Python 전용 룰 때문에 BLOCKED 로 떨어지는 회귀 방지.
    룰 주입(``rules=...``) 경로는 applicability 체크를 우회한다 (테스트 용).

    check 함수가 예외를 던지면 해당 룰만 실패로 처리하고 파이프라인은
    차단하지 않는다 (``[CHECK_ERROR] ...``).
    """
    if rules is None and not is_python_task(task):
        return QGVerdict(verdict="PASS", rule_results=[])

    effective_rules = rules if rules is not None else RULES
    results: list[tuple[str, QGResult]] = []
    for rule in effective_rules:
        try:
            result = rule.check(tests_dir, task)
        except Exception as e:  # noqa: BLE001 — 어떤 오류든 회복 가능
            result = QGResult(
                passed=False,
                message=f"[CHECK_ERROR] {type(e).__name__}: {e}",
            )
        results.append((rule.rule_id, result))

    verdict = aggregate(results, rules=effective_rules)
    return QGVerdict(verdict=verdict, rule_results=results)


def verdict_to_rule_results_dict(verdict: QGVerdict) -> list[dict]:
    """QGVerdict.rule_results → TaskReport 용 dict 리스트."""
    return [
        {"rule_id": rid, "passed": r.passed, "message": r.message}
        for rid, r in verdict.rule_results
    ]
