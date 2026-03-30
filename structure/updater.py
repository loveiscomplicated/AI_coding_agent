"""
PROJECT_STRUCTURE.md 자동 생성 모듈

Python 표준 라이브러리의 ast 모듈로 소스 코드를 파싱하여
PROJECT_STRUCTURE.md 파일을 자동 생성한다.
"""
import ast
from pathlib import Path
from typing import Optional


def parse_module(file_path: Path) -> dict:
    """
    파이썬 파일을 ast로 파싱하여 구조 정보를 반환한다.

    Args:
        file_path: 파싱할 파이썬 파일 경로

    Returns:
        {
            "path": str,
            "classes": [
                {
                    "name": str,
                    "methods": list[str],
                    "docstring": str | None
                },
                ...
            ],
            "functions": [
                {
                    "name": str,
                    "signature": str,
                    "docstring": str | None
                },
                ...
            ]
        }
    """
    result = {
        "path": str(file_path),
        "classes": [],
        "functions": [],
    }

    try:
        source = Path(file_path).read_text(encoding="utf-8")
        tree = ast.parse(source)
    except (SyntaxError, Exception):
        # 구문 오류 또는 읽기 오류 시 빈 구조 반환
        return result

    for node in ast.iter_child_nodes(tree):
        # 최상위 클래스 추출
        if isinstance(node, ast.ClassDef):
            methods = [
                item.name
                for item in ast.iter_child_nodes(node)
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
            ]
            docstring = ast.get_docstring(node)
            result["classes"].append({
                "name": node.name,
                "methods": methods,
                "docstring": docstring,
            })

        # 최상위 함수 추출
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # 시그니처 구성
            args = node.args
            params = []

            # 일반 인자 (기본값 없는 것 먼저)
            num_defaults = len(args.defaults)
            num_args = len(args.args)
            for i, arg in enumerate(args.args):
                default_index = i - (num_args - num_defaults)
                if default_index >= 0:
                    default_node = args.defaults[default_index]
                    try:
                        default_val = ast.unparse(default_node)
                    except Exception:
                        default_val = "..."
                    params.append(f"{arg.arg}={default_val}")
                else:
                    params.append(arg.arg)

            # *args
            if args.vararg:
                params.append(f"*{args.vararg.arg}")

            # keyword-only args
            for i, kwarg in enumerate(args.kwonlyargs):
                if i < len(args.kw_defaults) and args.kw_defaults[i] is not None:
                    try:
                        default_val = ast.unparse(args.kw_defaults[i])
                    except Exception:
                        default_val = "..."
                    params.append(f"{kwarg.arg}={default_val}")
                else:
                    params.append(kwarg.arg)

            # **kwargs
            if args.kwarg:
                params.append(f"**{args.kwarg.arg}")

            signature = f"{node.name}({', '.join(params)})"

            # docstring 첫 줄만 추출
            raw_docstring = ast.get_docstring(node)
            if raw_docstring:
                docstring = raw_docstring.splitlines()[0]
            else:
                docstring = None

            result["functions"].append({
                "name": node.name,
                "signature": signature,
                "docstring": docstring,
            })

    return result


def scan_directory(root: Path, exclude_dirs: Optional[list] = None) -> list:
    """
    디렉토리를 재귀 탐색하여 .py 파일들의 parse_module 결과 리스트를 반환한다.

    Args:
        root: 탐색할 루트 디렉토리
        exclude_dirs: 제외할 디렉토리 목록
                     기본값: ["__pycache__", ".git", "venv", ".venv", "node_modules"]

    Returns:
        parse_module 결과의 리스트
    """
    if exclude_dirs is None:
        exclude_dirs = ["__pycache__", ".git", "venv", ".venv", "node_modules"]

    root = Path(root)
    results = []

    def _walk(directory: Path):
        for item in sorted(directory.iterdir()):
            if item.is_dir():
                # 제외 디렉토리 건너뜀
                if item.name in exclude_dirs:
                    continue
                _walk(item)
            elif item.is_file() and item.suffix == ".py":
                results.append(parse_module(item))

    _walk(root)
    return results


def generate_markdown(modules: list, title: str = "PROJECT_STRUCTURE") -> str:
    """
    모듈 목록을 마크다운 문자열로 변환한다.

    Args:
        modules: parse_module 결과의 리스트
        title: 마크다운 제목 (기본값: "PROJECT_STRUCTURE")

    Returns:
        마크다운 형식의 문자열
    """
    lines = [f"# {title}", ""]

    if not modules:
        lines.append("(파이썬 파일 없음)")
        lines.append("")
        return "\n".join(lines)

    for module in modules:
        path = module.get("path", "")
        classes = module.get("classes", [])
        functions = module.get("functions", [])

        # 파일 경로를 헤더로 표시
        lines.append(f"## {path}")
        lines.append("")

        # 클래스 목록
        if classes:
            lines.append("### 클래스")
            for cls in classes:
                docstring_part = f" — {cls['docstring']}" if cls.get("docstring") else ""
                lines.append(f"- **{cls['name']}**{docstring_part}")
                for method in cls.get("methods", []):
                    lines.append(f"  - `{method}`")
            lines.append("")

        # 함수 목록
        if functions:
            lines.append("### 함수")
            for func in functions:
                docstring_part = f" — {func['docstring']}" if func.get("docstring") else ""
                lines.append(f"- `{func['signature']}`{docstring_part}")
            lines.append("")

    return "\n".join(lines)


def update(root: str = ".", output: str = "PROJECT_STRUCTURE.md") -> Path:
    """
    scan_directory → generate_markdown → 파일 저장 후 경로를 반환한다.

    Args:
        root: 탐색할 루트 디렉토리 (기본값: ".")
        output: 출력 파일 경로 (기본값: "PROJECT_STRUCTURE.md")

    Returns:
        생성된 파일의 Path
    """
    root_path = Path(root)
    output_path = Path(output)

    # output이 상대 경로인 경우 root 디렉토리 기준으로 해석
    if not output_path.is_absolute():
        output_path = root_path / output_path

    modules = scan_directory(root_path)
    markdown = generate_markdown(modules)

    output_path.write_text(markdown, encoding="utf-8")
    return output_path
