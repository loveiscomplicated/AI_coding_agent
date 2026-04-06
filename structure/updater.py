"""
PROJECT_STRUCTURE.md 자동 생성 모듈 (Tree-sitter 기반)

Tree-sitter를 사용해 다양한 언어의 소스 코드를 파싱하여
PROJECT_STRUCTURE.md 파일을 자동 생성한다.

에이전트가 프로젝트마다 어떤 언어를 선택해도 grammar 패키지 추가만으로 확장 가능하다.
지원되지 않는 확장자의 파일은 조용히 스킵한다.

지원 언어:
    Python, TypeScript/TSX, JavaScript/JSX,
    C, C++, Rust, Go, Java

설치:
    pip install tree-sitter \\
        tree-sitter-python tree-sitter-typescript \\
        tree-sitter-c tree-sitter-cpp tree-sitter-rust \\
        tree-sitter-go tree-sitter-java
"""

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── 상수 ──────────────────────────────────────────────────────────────────────

_EXCLUDE_DIRS = frozenset({
    ".git", "__pycache__", "node_modules", ".venv", "venv",
    ".next", "dist", "build", ".cache", "coverage",
    ".agent-workspace", ".pytest_cache", ".mypy_cache",
    "target", ".tox",
})

# 파일 확장자 → 언어 키
_LANG_MAP: dict[str, str] = {
    ".py":  "python",
    ".ts":  "typescript",
    ".tsx": "tsx",
    ".js":  "javascript",
    ".jsx": "javascript",
    ".c":   "c",
    ".h":   "c",
    ".cpp": "cpp",
    ".cc":  "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".hxx": "cpp",
    ".rs":  "rust",
    ".go":  "go",
    ".java":"java",
}

# 언어 키 → 표시 이름
_LANG_LABEL: dict[str, str] = {
    "python":     "Python",
    "typescript": "TypeScript",
    "tsx":        "TSX",
    "javascript": "JavaScript",
    "c":          "C",
    "cpp":        "C++",
    "rust":       "Rust",
    "go":         "Go",
    "java":       "Java",
}


# ── Tree-sitter 언어 로더 ─────────────────────────────────────────────────────

def _load_parser(lang: str):
    """지정된 언어의 (Language, Parser) 쌍을 반환한다. 미설치 시 None."""
    try:
        from tree_sitter import Language, Parser  # noqa: PLC0415
        if lang == "python":
            import tree_sitter_python as m  # noqa: PLC0415
            language = Language(m.language())
        elif lang == "typescript":
            import tree_sitter_typescript as m  # noqa: PLC0415
            language = Language(m.language_typescript())
        elif lang == "tsx":
            import tree_sitter_typescript as m  # noqa: PLC0415
            language = Language(m.language_tsx())
        elif lang in ("javascript", "jsx"):
            try:
                import tree_sitter_javascript as m  # noqa: PLC0415
                language = Language(m.language())
            except ImportError:
                import tree_sitter_typescript as m  # noqa: PLC0415
                language = Language(m.language_tsx())
        elif lang == "c":
            import tree_sitter_c as m  # noqa: PLC0415
            language = Language(m.language())
        elif lang == "cpp":
            import tree_sitter_cpp as m  # noqa: PLC0415
            language = Language(m.language())
        elif lang == "rust":
            import tree_sitter_rust as m  # noqa: PLC0415
            language = Language(m.language())
        elif lang == "go":
            import tree_sitter_go as m  # noqa: PLC0415
            language = Language(m.language())
        elif lang == "java":
            import tree_sitter_java as m  # noqa: PLC0415
            language = Language(m.language())
        else:
            return None, None
        return language, Parser(language)
    except ImportError:
        return None, None


# ── 공통 헬퍼 ─────────────────────────────────────────────────────────────────

def _extract_docstring(body_node) -> Optional[str]:
    """블록 노드의 첫 번째 문자열 리터럴을 docstring으로 추출한다."""
    if body_node is None or body_node.named_child_count == 0:
        return None
    first = body_node.named_children[0]
    if first.type == "expression_statement" and first.named_child_count > 0:
        s = first.named_children[0]
        if s.type == "string" and s.text:
            raw = s.text.decode("utf-8", errors="replace").strip()
            for q in ('"""', "'''", '"', "'"):
                if raw.startswith(q) and raw.endswith(q) and len(raw) > 2 * len(q):
                    raw = raw[len(q):-len(q)]
                    break
            return raw.strip().splitlines()[0].strip() or None
    return None


def _node_text(node, default: str = "") -> str:
    if node is None or node.text is None:
        return default
    return node.text.decode("utf-8", errors="replace")


# ── Python 파서 ───────────────────────────────────────────────────────────────

def _parse_python(tree, source: bytes) -> tuple[list, list]:
    """
    Python 소스 트리에서 최상위 클래스·함수 정보를 추출한다.

    Returns:
        (classes, functions) — 각각 dict 리스트
    """
    root = tree.root_node
    classes: list[dict] = []
    functions: list[dict] = []

    for node in root.named_children:
        if node.type == "class_definition":
            name_node = node.child_by_field_name("name")
            body_node = node.child_by_field_name("body")
            if name_node is None:
                continue

            methods: list[str] = []
            if body_node:
                for child in body_node.named_children:
                    if child.type in ("function_definition", "decorated_definition"):
                        fn = child
                        if child.type == "decorated_definition":
                            fn = child.child_by_field_name("definition") or child
                        if fn.type == "function_definition":
                            m_name = fn.child_by_field_name("name")
                            if m_name:
                                methods.append(_node_text(m_name))

            classes.append({
                "name":      _node_text(name_node),
                "line":      node.start_point[0] + 1,
                "methods":   methods,
                "docstring": _extract_docstring(body_node),
            })

        elif node.type in ("function_definition", "decorated_definition"):
            fn = node
            if node.type == "decorated_definition":
                fn = node.child_by_field_name("definition") or node
            if fn.type != "function_definition":
                continue

            name_node  = fn.child_by_field_name("name")
            params_node = fn.child_by_field_name("parameters")
            ret_node   = fn.child_by_field_name("return_type")
            body_node  = fn.child_by_field_name("body")

            if name_node is None:
                continue

            name = _node_text(name_node)
            params = _node_text(params_node, "()")
            ret_raw = _node_text(ret_node)
            # ret_node 텍스트는 "-> SomeType" 형태
            ret = (" " + ret_raw.strip()) if ret_raw else ""
            signature = f"{name}{params}{ret}"

            functions.append({
                "name":      name,
                "line":      fn.start_point[0] + 1,
                "signature": signature,
                "docstring": _extract_docstring(body_node),
            })

    return classes, functions


# ── TypeScript / JavaScript 파서 ──────────────────────────────────────────────

def _extract_jsdoc(node) -> Optional[str]:
    """노드 바로 앞의 JSDoc 주석(/** ... */)에서 첫 줄 설명을 추출한다."""
    sibling = node.prev_named_sibling
    if sibling is not None and sibling.type == "comment" and sibling.text:
        raw = sibling.text.decode("utf-8", errors="replace").strip()
        if raw.startswith("/**"):
            # /** ... */ 에서 내용 추출
            inner = raw[3:]
            if inner.endswith("*/"):
                inner = inner[:-2]
            # 각 줄의 leading `*` 제거
            for line in inner.splitlines():
                line = line.strip().lstrip("*").strip()
                if line:
                    return line
    return None


def _parse_typescript(tree, source: bytes) -> tuple[list, list]:
    """
    TypeScript/TSX/JavaScript 소스 트리에서 최상위 클래스·함수 정보를 추출한다.

    Returns:
        (classes, functions) — 각각 dict 리스트
    """
    root = tree.root_node
    classes: list[dict] = []
    functions: list[dict] = []

    def _process_node(node):
        """최상위 선언 노드를 처리한다 (export_statement 래퍼 포함)."""
        if node.type == "export_statement":
            decl = node.child_by_field_name("declaration")
            if decl:
                _process_decl(decl, node)
        elif node.type in ("class_declaration", "function_declaration", "lexical_declaration"):
            _process_decl(node, node)

    def _process_decl(decl, outer_node):
        if decl.type == "class_declaration":
            name_node = decl.child_by_field_name("name")
            body_node = decl.child_by_field_name("body")
            if name_node is None:
                return

            methods: list[str] = []
            if body_node:
                for child in body_node.named_children:
                    if child.type == "method_definition":
                        m_name = child.child_by_field_name("name")
                        if m_name:
                            methods.append(_node_text(m_name))

            classes.append({
                "name":      _node_text(name_node),
                "line":      decl.start_point[0] + 1,
                "methods":   methods,
                "docstring": _extract_jsdoc(outer_node),
            })

        elif decl.type == "function_declaration":
            name_node   = decl.child_by_field_name("name")
            params_node = decl.child_by_field_name("parameters")
            ret_node    = decl.child_by_field_name("return_type")
            if name_node is None:
                return

            name   = _node_text(name_node)
            params = _node_text(params_node, "()")
            ret_raw = _node_text(ret_node)
            ret = (" " + ret_raw.lstrip(":").strip()) if ret_raw else ""
            signature = f"{name}{params}{ret}"

            functions.append({
                "name":      name,
                "line":      decl.start_point[0] + 1,
                "signature": signature,
                "docstring": _extract_jsdoc(outer_node),
            })

        elif decl.type == "lexical_declaration":
            # const foo = (...) => ...  형태
            for var_decl in decl.named_children:
                if var_decl.type != "variable_declarator":
                    continue
                name_node = var_decl.child_by_field_name("name")
                value_node = var_decl.child_by_field_name("value")
                if name_node is None or value_node is None:
                    continue
                if value_node.type != "arrow_function":
                    continue

                name = _node_text(name_node)
                params_node = value_node.child_by_field_name("parameters")
                ret_node    = value_node.child_by_field_name("return_type")
                params = _node_text(params_node, "()")
                ret_raw = _node_text(ret_node)
                ret = (" " + ret_raw.lstrip(":").strip()) if ret_raw else ""
                signature = f"{name}{params}{ret}"

                functions.append({
                    "name":      name,
                    "line":      var_decl.start_point[0] + 1,
                    "signature": signature,
                    "docstring": _extract_jsdoc(outer_node),
                })

    for node in root.named_children:
        _process_node(node)

    return classes, functions


# ── C / C++ 파서 ─────────────────────────────────────────────────────────────

def _extract_c_comment(node) -> Optional[str]:
    """노드 바로 앞의 블록/라인 주석 첫 줄을 반환한다."""
    sibling = node.prev_named_sibling
    if sibling is None or sibling.text is None:
        return None
    raw = sibling.text.decode("utf-8", errors="replace").strip()
    if raw.startswith("/*"):
        inner = raw[2:]
        if inner.endswith("*/"):
            inner = inner[:-2]
        for line in inner.splitlines():
            line = line.strip().lstrip("*").strip()
            if line:
                return line
    elif raw.startswith("//"):
        return raw[2:].strip() or None
    return None


def _c_function_name_and_params(fn_node) -> tuple[str, str]:
    """
    C/C++ function_definition 또는 declaration 에서 이름과 파라미터를 추출한다.
    반환: (name, params_text)
    """
    declarator = fn_node.child_by_field_name("declarator")
    if declarator is None:
        return "", ""

    # 포인터 등 중첩 declarator 언래핑
    while declarator and declarator.type not in ("function_declarator",):
        inner = declarator.child_by_field_name("declarator")
        if inner is None:
            break
        declarator = inner

    if declarator is None or declarator.type != "function_declarator":
        return "", ""

    name_node  = declarator.child_by_field_name("declarator")
    params_node = declarator.child_by_field_name("parameters")
    name   = _node_text(name_node)
    params = _node_text(params_node, "()")
    return name, params


def _parse_c(tree, source: bytes) -> tuple[list, list]:
    """C 소스에서 struct와 최상위 함수를 추출한다."""
    root = tree.root_node
    classes: list[dict] = []
    functions: list[dict] = []

    for node in root.named_children:
        if node.type in ("struct_specifier", "union_specifier", "enum_specifier"):
            name_node = node.child_by_field_name("name")
            if name_node is None:
                continue
            classes.append({
                "name":      _node_text(name_node),
                "line":      node.start_point[0] + 1,
                "methods":   [],
                "docstring": _extract_c_comment(node),
            })

        elif node.type == "function_definition":
            name, params = _c_function_name_and_params(node)
            if not name:
                continue
            ret_type_node = node.child_by_field_name("type")
            ret = _node_text(ret_type_node)
            signature = f"{ret} {name}{params}".strip() if ret else f"{name}{params}"
            functions.append({
                "name":      name,
                "line":      node.start_point[0] + 1,
                "signature": signature,
                "docstring": _extract_c_comment(node),
            })

        elif node.type == "declaration":
            # 함수 선언 (프로토타입)
            name, params = _c_function_name_and_params(node)
            if not name:
                continue
            ret_type_node = node.child_by_field_name("type")
            ret = _node_text(ret_type_node)
            signature = f"{ret} {name}{params}".strip() if ret else f"{name}{params}"
            functions.append({
                "name":      name,
                "line":      node.start_point[0] + 1,
                "signature": signature,
                "docstring": _extract_c_comment(node),
            })

    return classes, functions


def _parse_cpp(tree, source: bytes) -> tuple[list, list]:
    """C++ 소스에서 class/struct와 최상위 함수를 추출한다."""
    root = tree.root_node
    classes: list[dict] = []
    functions: list[dict] = []

    for node in root.named_children:
        if node.type in ("class_specifier", "struct_specifier"):
            name_node = node.child_by_field_name("name")
            body_node = node.child_by_field_name("body")
            if name_node is None:
                continue

            methods: list[str] = []
            if body_node:
                for child in body_node.named_children:
                    if child.type == "function_definition":
                        m_name, _ = _c_function_name_and_params(child)
                        if m_name:
                            methods.append(m_name)
                    elif child.type == "declaration":
                        # 멤버 함수 선언
                        m_name, _ = _c_function_name_and_params(child)
                        if m_name:
                            methods.append(m_name)

            classes.append({
                "name":      _node_text(name_node),
                "line":      node.start_point[0] + 1,
                "methods":   methods,
                "docstring": _extract_c_comment(node),
            })

        elif node.type == "function_definition":
            name, params = _c_function_name_and_params(node)
            if not name:
                continue
            ret_type_node = node.child_by_field_name("type")
            ret = _node_text(ret_type_node)
            signature = f"{ret} {name}{params}".strip() if ret else f"{name}{params}"
            functions.append({
                "name":      name,
                "line":      node.start_point[0] + 1,
                "signature": signature,
                "docstring": _extract_c_comment(node),
            })

    return classes, functions


# ── Rust 파서 ─────────────────────────────────────────────────────────────────

def _parse_rust(tree, source: bytes) -> tuple[list, list]:
    """
    Rust 소스에서 struct/enum과 최상위 함수를 추출한다.
    impl 블록의 메서드는 해당 struct에 귀속시킨다.
    """
    root = tree.root_node
    classes: dict[str, dict] = {}   # name → dict
    functions: list[dict] = []

    def _rust_docstring(node) -> Optional[str]:
        """/// 또는 /** */ 주석에서 첫 줄을 추출한다."""
        sib = node.prev_named_sibling
        if sib is None or sib.text is None:
            return None
        raw = sib.text.decode("utf-8", errors="replace").strip()
        if raw.startswith("///"):
            return raw[3:].strip() or None
        if raw.startswith("/**"):
            inner = raw[3:]
            if inner.endswith("*/"):
                inner = inner[:-2]
            for line in inner.splitlines():
                line = line.strip().lstrip("*").strip()
                if line:
                    return line
        return None

    for node in root.named_children:
        if node.type in ("struct_item", "enum_item"):
            name_node = node.child_by_field_name("name")
            if name_node is None:
                continue
            name = _node_text(name_node)
            classes[name] = {
                "name":      name,
                "line":      node.start_point[0] + 1,
                "methods":   [],
                "docstring": _rust_docstring(node),
            }

        elif node.type == "impl_item":
            type_node = node.child_by_field_name("type")
            body_node = node.child_by_field_name("body")
            if type_node is None or body_node is None:
                continue
            impl_type = _node_text(type_node)
            for child in body_node.named_children:
                if child.type == "function_item":
                    m_name = child.child_by_field_name("name")
                    if m_name and impl_type in classes:
                        classes[impl_type]["methods"].append(_node_text(m_name))

        elif node.type == "function_item":
            name_node   = node.child_by_field_name("name")
            params_node = node.child_by_field_name("parameters")
            ret_node    = node.child_by_field_name("return_type")
            if name_node is None:
                continue
            name   = _node_text(name_node)
            params = _node_text(params_node, "()")
            ret_raw = _node_text(ret_node)
            ret = (" -> " + ret_raw.lstrip("->").strip()) if ret_raw else ""
            functions.append({
                "name":      name,
                "line":      node.start_point[0] + 1,
                "signature": f"{name}{params}{ret}",
                "docstring": _rust_docstring(node),
            })

    return list(classes.values()), functions


# ── Go 파서 ───────────────────────────────────────────────────────────────────

def _parse_go(tree, source: bytes) -> tuple[list, list]:
    """
    Go 소스에서 type(struct)와 최상위 함수를 추출한다.
    method_declaration의 receiver 타입으로 struct에 귀속시킨다.
    """
    root = tree.root_node
    classes: dict[str, dict] = {}
    functions: list[dict] = []

    def _go_docstring(node) -> Optional[str]:
        sib = node.prev_named_sibling
        if sib is None or sib.text is None:
            return None
        raw = sib.text.decode("utf-8", errors="replace").strip()
        if raw.startswith("//"):
            return raw[2:].strip() or None
        if raw.startswith("/*"):
            inner = raw[2:]
            if inner.endswith("*/"):
                inner = inner[:-2]
            for line in inner.splitlines():
                line = line.strip().lstrip("*").strip()
                if line:
                    return line
        return None

    def _receiver_type(method_node) -> str:
        """method_declaration의 receiver에서 struct 타입 이름을 추출한다."""
        recv = method_node.child_by_field_name("receiver")
        if recv is None:
            return ""
        # parameter_list 안의 parameter_declaration → type
        for child in recv.named_children:
            if child.type == "parameter_declaration":
                type_node = child.child_by_field_name("type")
                if type_node:
                    # *TypeName → TypeName
                    t = _node_text(type_node).lstrip("*").strip()
                    return t
        return ""

    for node in root.named_children:
        if node.type == "type_declaration":
            for spec in node.named_children:
                if spec.type == "type_spec":
                    name_node  = spec.child_by_field_name("name")
                    type_node  = spec.child_by_field_name("type")
                    if name_node and type_node and type_node.type == "struct_type":
                        name = _node_text(name_node)
                        classes[name] = {
                            "name":      name,
                            "line":      spec.start_point[0] + 1,
                            "methods":   [],
                            "docstring": _go_docstring(node),
                        }

        elif node.type == "method_declaration":
            recv_type = _receiver_type(node)
            m_name = node.child_by_field_name("name")
            if m_name and recv_type in classes:
                classes[recv_type]["methods"].append(_node_text(m_name))

        elif node.type == "function_declaration":
            name_node   = node.child_by_field_name("name")
            params_node = node.child_by_field_name("parameters")
            result_node = node.child_by_field_name("result")
            if name_node is None:
                continue
            name   = _node_text(name_node)
            params = _node_text(params_node, "()")
            ret_raw = _node_text(result_node)
            ret = (" " + ret_raw.strip()) if ret_raw else ""
            functions.append({
                "name":      name,
                "line":      node.start_point[0] + 1,
                "signature": f"{name}{params}{ret}",
                "docstring": _go_docstring(node),
            })

    return list(classes.values()), functions


# ── Java 파서 ─────────────────────────────────────────────────────────────────

def _parse_java(tree, source: bytes) -> tuple[list, list]:
    """Java 소스에서 class/interface와 최상위 메서드를 추출한다."""
    root = tree.root_node
    classes: list[dict] = []
    functions: list[dict] = []

    def _java_docstring(node) -> Optional[str]:
        sib = node.prev_named_sibling
        if sib is None or sib.text is None:
            return None
        raw = sib.text.decode("utf-8", errors="replace").strip()
        if raw.startswith("/**"):
            inner = raw[3:]
            if inner.endswith("*/"):
                inner = inner[:-2]
            for line in inner.splitlines():
                line = line.strip().lstrip("*").strip()
                if line:
                    return line
        elif raw.startswith("//"):
            return raw[2:].strip() or None
        return None

    def _extract_methods(body_node) -> list[str]:
        methods = []
        if body_node is None:
            return methods
        for child in body_node.named_children:
            if child.type == "method_declaration":
                m_name = child.child_by_field_name("name")
                if m_name:
                    methods.append(_node_text(m_name))
            elif child.type == "constructor_declaration":
                m_name = child.child_by_field_name("name")
                if m_name:
                    methods.append(_node_text(m_name))
        return methods

    for node in root.named_children:
        if node.type in ("class_declaration", "interface_declaration", "enum_declaration"):
            name_node = node.child_by_field_name("name")
            body_node = node.child_by_field_name("body")
            if name_node is None:
                continue
            classes.append({
                "name":      _node_text(name_node),
                "line":      node.start_point[0] + 1,
                "methods":   _extract_methods(body_node),
                "docstring": _java_docstring(node),
            })

        elif node.type == "method_declaration":
            # 클래스 밖 최상위 메서드 (드물지만)
            name_node   = node.child_by_field_name("name")
            params_node = node.child_by_field_name("parameters")
            if name_node is None:
                continue
            name   = _node_text(name_node)
            params = _node_text(params_node, "()")
            functions.append({
                "name":      name,
                "line":      node.start_point[0] + 1,
                "signature": f"{name}{params}",
                "docstring": _java_docstring(node),
            })

    return classes, functions


# ── 단일 파일 파싱 ────────────────────────────────────────────────────────────

def parse_file(filepath: Path) -> dict:
    """
    단일 파일을 Tree-sitter로 파싱하여 구조 정보를 반환한다.

    Returns::

        {
            "path":      str,
            "language":  str,   # 표시 이름 (예: "Python")
            "classes":   [...],
            "functions": [...],
        }

    지원되지 않는 확장자이거나 grammar가 없으면 빈 classes/functions를 반환한다.
    """
    ext = filepath.suffix.lower()
    lang_key = _LANG_MAP.get(ext)
    result_base = {
        "path":      str(filepath),
        "language":  _LANG_LABEL.get(lang_key, ext.lstrip(".").upper()) if lang_key else ext.lstrip(".").upper(),
        "classes":   [],
        "functions": [],
    }

    if lang_key is None:
        return result_base

    _, parser = _load_parser(lang_key)
    if parser is None:
        logger.debug("grammar 없음 — 스킵: %s (%s)", filepath, lang_key)
        return result_base

    try:
        source = filepath.read_bytes()
        tree = parser.parse(source)

        if lang_key == "python":
            classes, functions = _parse_python(tree, source)
        elif lang_key in ("typescript", "tsx", "javascript"):
            classes, functions = _parse_typescript(tree, source)
        elif lang_key == "c":
            classes, functions = _parse_c(tree, source)
        elif lang_key == "cpp":
            classes, functions = _parse_cpp(tree, source)
        elif lang_key == "rust":
            classes, functions = _parse_rust(tree, source)
        elif lang_key == "go":
            classes, functions = _parse_go(tree, source)
        elif lang_key == "java":
            classes, functions = _parse_java(tree, source)
        else:
            classes, functions = [], []

        result_base["classes"]   = classes
        result_base["functions"] = functions
    except Exception as exc:
        logger.warning("파싱 실패 (%s): %s", filepath, exc)

    return result_base


# ── 디렉토리 스캔 ─────────────────────────────────────────────────────────────

def scan_directory(root: Path, exclude_dirs: Optional[list] = None) -> list[dict]:
    """
    디렉토리를 재귀 탐색하여 소스 파일의 구조 정보 리스트를 반환한다.

    - 지원 언어(.py/.ts/.rs 등): Tree-sitter로 파싱하여 클래스·함수 정보 포함
    - 미지원 언어(.rb/.png/.db 등): 파일명과 확장자 표시만 포함 (클래스·함수는 빈 리스트)
    - 제외 디렉토리(__pycache__/target/node_modules 등)의 파일은 완전히 제외

    Args:
        root:         스캔할 루트 디렉토리
        exclude_dirs: 추가로 제외할 디렉토리 이름 목록
    """
    exclude = _EXCLUDE_DIRS | set(exclude_dirs or [])
    results: list[dict] = []

    for filepath in sorted(root.rglob("*")):
        if not filepath.is_file():
            continue
        # 제외 디렉토리 필터
        if any(part in exclude for part in filepath.relative_to(root).parts):
            continue

        ext = filepath.suffix.lower()

        try:
            rel_path = str(filepath.relative_to(root))
        except ValueError:
            rel_path = str(filepath)

        if ext in _LANG_MAP:
            # 지원 언어: Tree-sitter 파싱
            info = parse_file(filepath)
            info["path"] = rel_path
        else:
            # 미지원 언어: 파일명만 표시
            lang_label = ext.lstrip(".").upper() if ext else "unknown"
            info = {
                "path":      rel_path,
                "language":  lang_label,
                "classes":   [],
                "functions": [],
            }

        results.append(info)

    logger.debug("scan_directory: %d개 파일 (root=%s)", len(results), root)
    return results


# ── 마크다운 생성 ─────────────────────────────────────────────────────────────

def generate_markdown(modules: list[dict], title: str = "PROJECT_STRUCTURE") -> str:
    """
    parse_file()/scan_directory() 결과 리스트를 마크다운 문자열로 변환한다.

    Args:
        modules: parse_file() 반환값의 리스트
        title:   마크다운 제목 (기본 "PROJECT_STRUCTURE")
    """
    lines = [f"# {title}", ""]

    if not modules:
        lines.append("(소스 파일 없음)")
        lines.append("")
        return "\n".join(lines)

    for info in modules:
        path     = info.get("path", "")
        language = info.get("language", "")
        lang_label = f" `{language}`" if language else ""
        lines.append(f"## {path}{lang_label}")
        lines.append("")

        classes   = info.get("classes", [])
        functions = info.get("functions", [])

        if classes:
            lines.append("### 클래스 / 타입")
            for cls in classes:
                line_label = f" (L{cls['line']})" if cls.get("line") else ""
                lines.append(f"- **{cls['name']}**{line_label}")
                if cls.get("docstring"):
                    lines.append(f"  {cls['docstring']}")
                for method in cls.get("methods", []):
                    lines.append(f"  - `{method}`")
            lines.append("")

        if functions:
            lines.append("### 함수")
            for func in functions:
                sig = func.get("signature") or func["name"]
                line_label = f" (L{func['line']})" if func.get("line") else ""
                lines.append(f"- `{sig}`{line_label}")
                if func.get("docstring"):
                    lines.append(f"  {func['docstring']}")
            lines.append("")

    return "\n".join(lines)


# ── 공개 API ─────────────────────────────────────────────────────────────────

def update(root: str = ".", output: str = "PROJECT_STRUCTURE.md") -> Path:
    """
    타겟 프로젝트의 소스 코드를 스캔하여 PROJECT_STRUCTURE.md 를 생성한다.

    Args:
        root:   스캔할 프로젝트 루트 디렉토리
        output: 출력 파일 경로 (상대 경로면 root 기준)

    Returns:
        생성된 파일의 절대 Path
    """
    root_path   = Path(root).resolve()
    output_path = Path(output)
    if not output_path.is_absolute():
        output_path = root_path / output_path

    modules  = scan_directory(root_path)
    markdown = generate_markdown(modules)

    output_path.write_text(markdown, encoding="utf-8")
    logger.info("PROJECT_STRUCTURE 생성: %d개 파일 → %s", len(modules), output_path)
    return output_path


# ── 하위 호환성 래퍼 ──────────────────────────────────────────────────────────

def parse_module(file_path: Path) -> dict:
    """parse_file()의 하위 호환 래퍼."""
    return parse_file(file_path)
