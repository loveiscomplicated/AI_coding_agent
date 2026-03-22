"""
tools/code_tools.py — AST 파싱 & 코드 분석 도구

get_imports       : 파일의 모든 import 문 추출
get_outline       : 파일의 함수·클래스 구조 요약 (이름, 줄 번호, 인자, docstring)
get_function_src  : 특정 함수·메서드의 소스코드 추출
"""

import ast
import textwrap
from pathlib import Path

from .schemas import ToolResult


# ── 내부 헬퍼 ─────────────────────────────────────────────────────────────────


def _parse(path: str) -> tuple[ast.Module, list[str]]:
    """파일을 읽어 AST와 원본 라인 리스트를 반환합니다."""
    source = Path(path).read_text(encoding="utf-8")
    tree = ast.parse(source, filename=path)
    return tree, source.splitlines()


def _first_docstring(node: ast.AST) -> str:
    """함수·클래스 노드의 첫 docstring을 반환 (없으면 빈 문자열)."""
    if (
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    ):
        # 첫 줄만 반환 (너무 길면 잘라냄)
        first_line = node.body[0].value.value.strip().splitlines()[0]
        return first_line[:80] + ("…" if len(first_line) > 80 else "")
    return ""


def _arg_names(args: ast.arguments) -> list[str]:
    """ast.arguments → 인자 이름 리스트."""
    names = [a.arg for a in args.posonlyargs + args.args]
    if args.vararg:
        names.append(f"*{args.vararg.arg}")
    names += [a.arg for a in args.kwonlyargs]
    if args.kwarg:
        names.append(f"**{args.kwarg.arg}")
    return names


# ── 공개 도구 함수 ────────────────────────────────────────────────────────────


def get_imports(path: str) -> ToolResult:
    """
    파일의 모든 import 문을 줄 번호와 함께 반환합니다.

    Args:
        path: 분석할 Python 파일 경로

    Returns:
        ToolResult.output 예시:
            L1  import os
            L3  from pathlib import Path
    """
    try:
        tree, _ = _parse(path)
    except FileNotFoundError:
        return ToolResult(success=False, output="", error=f"파일 없음: {path}")
    except SyntaxError as e:
        return ToolResult(success=False, output="", error=f"문법 오류: {e}")
    except Exception as e:
        return ToolResult(success=False, output="", error=str(e))

    results: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = ", ".join(alias.name for alias in node.names)
            results.append((node.lineno, f"L{node.lineno:<4} import {names}"))
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            names = ", ".join(alias.name for alias in node.names)
            results.append(
                (node.lineno, f"L{node.lineno:<4} from {module} import {names}")
            )

    if not results:
        return ToolResult(success=True, output="import 문 없음")

    results.sort(key=lambda x: x[0])
    return ToolResult(success=True, output="\n".join(line for _, line in results))


def get_outline(path: str) -> ToolResult:
    """
    파일의 최상위 함수·클래스 구조를 요약합니다.
    클래스 내부 메서드도 들여쓰기로 표시됩니다.

    Args:
        path: 분석할 Python 파일 경로

    Returns:
        ToolResult.output 예시:
            L10  def load_config(path, encoding) — "설정 파일 읽기"
            L25  class Agent
            L30    def __init__(self, llm)
            L45    def run(self, prompt) — "ReAct 루프 실행"
    """
    try:
        tree, _ = _parse(path)
    except FileNotFoundError:
        return ToolResult(success=False, output="", error=f"파일 없음: {path}")
    except SyntaxError as e:
        return ToolResult(success=False, output="", error=f"문법 오류: {e}")
    except Exception as e:
        return ToolResult(success=False, output="", error=str(e))

    lines: list[str] = []

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = ", ".join(_arg_names(node.args))
            doc = f' — "{_first_docstring(node)}"' if _first_docstring(node) else ""
            prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
            lines.append(f"L{node.lineno:<4} {prefix} {node.name}({args}){doc}")

        elif isinstance(node, ast.ClassDef):
            bases = ", ".join(
                ast.unparse(b) if hasattr(ast, "unparse") else b.id  # type: ignore[attr-defined]
                for b in node.bases
            )
            base_str = f"({bases})" if bases else ""
            lines.append(f"L{node.lineno:<4} class {node.name}{base_str}")

            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    args = ", ".join(_arg_names(child.args))
                    doc = (
                        f' — "{_first_docstring(child)}"'
                        if _first_docstring(child)
                        else ""
                    )
                    prefix = (
                        "async def"
                        if isinstance(child, ast.AsyncFunctionDef)
                        else "def"
                    )
                    lines.append(
                        f"L{child.lineno:<4}   {prefix} {child.name}({args}){doc}"
                    )

    if not lines:
        return ToolResult(success=True, output="정의된 함수·클래스 없음")

    return ToolResult(success=True, output="\n".join(lines))


def get_function_src(path: str, function_name: str) -> ToolResult:
    """
    파일에서 특정 함수 또는 메서드의 소스코드를 추출합니다.
    동명의 함수가 여럿이면 첫 번째를 반환합니다.

    Args:
        path: Python 파일 경로
        function_name: 찾을 함수 또는 메서드 이름

    Returns:
        ToolResult.output: 해당 함수의 소스코드 (줄 번호 포함)
    """
    try:
        tree, src_lines = _parse(path)
    except FileNotFoundError:
        return ToolResult(success=False, output="", error=f"파일 없음: {path}")
    except SyntaxError as e:
        return ToolResult(success=False, output="", error=f"문법 오류: {e}")
    except Exception as e:
        return ToolResult(success=False, output="", error=str(e))

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == function_name:
                start = node.lineno - 1       # 0-indexed
                end = node.end_lineno         # type: ignore[attr-defined]
                snippet = src_lines[start:end]
                # 공통 들여쓰기 제거 (메서드인 경우)
                dedented = textwrap.dedent("\n".join(snippet))
                numbered = "\n".join(
                    f"{start + i + 1:>4}: {line}"
                    for i, line in enumerate(dedented.splitlines())
                )
                return ToolResult(success=True, output=numbered)

    return ToolResult(
        success=False,
        output="",
        error=f"'{function_name}' 함수를 찾을 수 없음: {path}",
    )
