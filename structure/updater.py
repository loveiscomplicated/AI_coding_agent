"""
PROJECT_STRUCTURE.md 자동 생성 모듈 (universal-ctags 기반)

universal-ctags를 사용해 Python, TypeScript, Go, Java, Rust, C++ 등
모든 언어의 소스 코드를 파싱하여 PROJECT_STRUCTURE.md 파일을 자동 생성한다.

설치:
    macOS:          brew install universal-ctags
    Ubuntu/Debian:  apt install universal-ctags

ctags가 없거나 실패한 경우 파일 트리 폴백으로 동작한다.
"""

import json
import logging
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── 상수 ──────────────────────────────────────────────────────────────────────

_EXCLUDE_DIRS = [
    ".git", "__pycache__", "node_modules", ".venv", "venv",
    ".next", "dist", "build", ".cache", "coverage",
    ".agent-workspace", ".pytest_cache", ".mypy_cache",
    "target",        # Rust / Java Maven
    ".tox",
]

# 클래스·타입 계열 kind 값 (언어마다 다름)
_CLASS_KINDS = frozenset({
    "class", "struct", "interface", "type", "enum",
    "module", "namespace", "trait", "protocol",
    "record",        # Java 16+
})

# 함수·메서드 계열 kind 값
_FUNCTION_KINDS = frozenset({
    "function", "func", "method", "member",
    "procedure", "subroutine", "f", "fn", "meth",
    "constructor",
})

# 메서드를 클래스에 귀속시킬 때 확인하는 scope 키
# ctags JSON 은 scope 종류마다 다른 키를 씀 (예: "class", "struct", "namespace")
_SCOPE_KEYS = ("class", "struct", "interface", "namespace", "module", "scope")


# ── ctags 실행 ────────────────────────────────────────────────────────────────

def _run_ctags(root: Path, timeout: int = 60) -> list[dict]:
    """
    universal-ctags를 실행하여 태그 목록(list[dict])을 반환한다.

    Raises:
        RuntimeError: ctags 미설치 또는 실행 실패
    """
    exclude_args = [f"--exclude={d}" for d in _EXCLUDE_DIRS]
    cmd = [
        "ctags",
        "-R",
        "--output-format=json",
        "--fields=+{line}{scope}{scopeKind}",
        *exclude_args,
        "-f", "-",       # stdout 으로 출력
        str(root),
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=root,
        )
    except FileNotFoundError:
        raise RuntimeError(
            "ctags 가 설치되지 않았습니다.\n"
            "  macOS:  brew install universal-ctags\n"
            "  Ubuntu: apt install universal-ctags"
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"ctags 실행 타임아웃 ({timeout}초)")

    tags: list[dict] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if obj.get("_type") == "tag":
                tags.append(obj)
        except json.JSONDecodeError:
            continue

    logger.debug("ctags: %d개 태그 수집 (root=%s)", len(tags), root)
    return tags


# ── 태그 → 구조 딕셔너리 ──────────────────────────────────────────────────────

def _tags_to_structure(tags: list[dict], root: Path) -> dict[str, dict]:
    """
    태그 목록을 파일별 구조 딕셔너리로 변환한다.

    Returns::

        {
            "relative/path.py": {
                "language": "Python",
                "classes":   [{"name": str, "line": int, "methods": [str]}],
                "functions": [{"name": str, "line": int}],
            },
            ...
        }
    """
    by_file: dict[str, list[dict]] = defaultdict(list)
    for tag in tags:
        path = tag.get("path", "")
        if path:
            by_file[path].append(tag)

    structure: dict[str, dict] = {}

    for path, file_tags in sorted(by_file.items()):
        try:
            rel = str(Path(path).relative_to(root))
        except ValueError:
            rel = path

        language = file_tags[0].get("language", "") if file_tags else ""
        classes: dict[str, dict] = {}
        functions: list[dict] = []

        for tag in sorted(file_tags, key=lambda t: t.get("line", 0) or 0):
            name = tag.get("name", "")
            kind = (tag.get("kind") or "").lower()
            line = tag.get("line", 0) or 0

            # scope 키 탐색: 어느 클래스/타입 안에 속하는지 확인
            parent_scope = ""
            for key in _SCOPE_KEYS:
                if tag.get(key):
                    parent_scope = tag[key]
                    break

            if kind in _CLASS_KINDS:
                if name not in classes:
                    classes[name] = {"name": name, "line": line, "methods": []}
            elif kind in _FUNCTION_KINDS:
                if parent_scope and parent_scope in classes:
                    classes[parent_scope]["methods"].append(name)
                else:
                    functions.append({"name": name, "line": line})

        if classes or functions:
            structure[rel] = {
                "language": language,
                "classes":  list(classes.values()),
                "functions": functions,
            }

    return structure


# ── 폴백: 파일 트리 ───────────────────────────────────────────────────────────

def _filetree_fallback(root: Path) -> dict[str, dict]:
    """
    ctags 가 없거나 실패했을 때의 폴백.
    파일 목록만 수집하고, 클래스·함수 정보는 비워둔다.
    """
    exclude = set(_EXCLUDE_DIRS)
    structure: dict[str, dict] = {}
    for f in sorted(root.rglob("*")):
        if not f.is_file():
            continue
        parts = f.relative_to(root).parts
        if any(p in exclude for p in parts):
            continue
        rel = str(f.relative_to(root))
        ext = f.suffix.lstrip(".").upper()
        structure[rel] = {"language": ext, "classes": [], "functions": []}
    logger.debug("파일 트리 폴백: %d개 파일", len(structure))
    return structure


# ── 마크다운 생성 ─────────────────────────────────────────────────────────────

def generate_markdown(structure: dict, title: str = "PROJECT_STRUCTURE") -> str:
    """구조 딕셔너리를 마크다운 문자열로 변환한다."""
    lines = [f"# {title}", ""]

    if not structure:
        lines.append("(소스 파일 없음 또는 태그 없음)")
        lines.append("")
        return "\n".join(lines)

    for path, info in structure.items():
        language = info.get("language", "")
        lang_label = f" `{language}`" if language else ""
        lines.append(f"## {path}{lang_label}")
        lines.append("")

        classes   = info.get("classes", [])
        functions = info.get("functions", [])

        if classes:
            lines.append("### 클래스 / 타입")
            for cls in classes:
                lines.append(f"- **{cls['name']}** (L{cls['line']})")
                for method in cls.get("methods", []):
                    lines.append(f"  - `{method}`")
            lines.append("")

        if functions:
            lines.append("### 함수")
            for func in functions:
                lines.append(f"- `{func['name']}` (L{func['line']})")
            lines.append("")

    return "\n".join(lines)


# ── 공개 API ─────────────────────────────────────────────────────────────────

def update(root: str = ".", output: str = "PROJECT_STRUCTURE.md") -> Path:
    """
    타겟 프로젝트의 소스 코드를 스캔하여 PROJECT_STRUCTURE.md 를 생성한다.

    ctags 가 없으면 파일 트리 폴백으로 동작한다.

    Args:
        root:   스캔할 프로젝트 루트 디렉토리
        output: 출력 파일 경로 (상대 경로면 root 기준)

    Returns:
        생성된 파일의 절대 Path
    """
    root_path = Path(root).resolve()
    output_path = Path(output)
    if not output_path.is_absolute():
        output_path = root_path / output_path

    try:
        tags = _run_ctags(root_path)
        structure = _tags_to_structure(tags, root_path)
        logger.info(
            "PROJECT_STRUCTURE 생성 (ctags): %d개 파일, %d개 태그",
            len(structure), len(tags),
        )
    except RuntimeError as e:
        logger.warning("ctags 실패 → 파일 트리 폴백: %s", e)
        structure = _filetree_fallback(root_path)
        logger.info("PROJECT_STRUCTURE 생성 (파일 트리): %d개 파일", len(structure))

    markdown = generate_markdown(structure)
    output_path.write_text(markdown, encoding="utf-8")
    return output_path


# ── 하위 호환성 래퍼 ──────────────────────────────────────────────────────────

def scan_directory(root: Path, exclude_dirs: Optional[list] = None) -> list:  # noqa: ARG001
    """기존 코드와의 하위 호환성을 위한 래퍼. ctags 기반으로 동작한다."""
    try:
        tags = _run_ctags(root)
        structure = _tags_to_structure(tags, root)
    except RuntimeError:
        structure = _filetree_fallback(root)
    return [{"path": path, **info} for path, info in structure.items()]


def parse_module(file_path: Path) -> dict:
    """기존 코드와의 하위 호환성을 위한 래퍼. 단일 파일을 ctags 로 파싱한다."""
    root = file_path.parent
    try:
        tags = _run_ctags(root)
        structure = _tags_to_structure(tags, root)
        rel = file_path.name
        if rel in structure:
            return {"path": str(file_path), **structure[rel]}
    except RuntimeError:
        pass
    return {"path": str(file_path), "language": "", "classes": [], "functions": []}
