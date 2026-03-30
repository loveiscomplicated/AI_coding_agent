"""PROJECT_STRUCTURE.md 생성기 모듈

Python 표준 라이브러리의 ast 모듈로 소스 코드를 파싱하여
PROJECT_STRUCTURE.md 파일을 자동 생성하는 모듈이다.
"""
import ast
from pathlib import Path
from typing import Optional


def parse_module(file_path) -> dict:
    """파이썬 파일을 ast로 파싱하여 구조 정보를 반환한다.
    
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
        "functions": []
    }
    
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            source_code = f.read()
        
        tree = ast.parse(source_code)
    except (SyntaxError, UnicodeDecodeError):
        # 구문 오류가 있는 파일은 빈 리스트 반환
        return result
    
    # 최상위 클래스 추출
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            docstring = ast.get_docstring(node)
            # docstring의 첫 줄만 추출
            if docstring:
                docstring = docstring.split('\n')[0]
            
            class_info = {
                "name": node.name,
                "methods": [],
                "docstring": docstring
            }
            
            # 메서드 추출
            for item in node.body:
                if isinstance(item, ast.FunctionDef):
                    class_info["methods"].append(item.name)
            
            result["classes"].append(class_info)
    
    # 최상위 함수 추출
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            # 함수의 signature 생성
            signature = _get_function_signature(node)
            
            docstring = ast.get_docstring(node)
            # docstring의 첫 줄만 추출
            if docstring:
                docstring = docstring.split('\n')[0]
            
            func_info = {
                "name": node.name,
                "signature": signature,
                "docstring": docstring
            }
            
            result["functions"].append(func_info)
    
    return result


def _get_function_signature(node: ast.FunctionDef) -> str:
    """함수 노드에서 signature 문자열을 생성한다.
    
    Args:
        node: ast.FunctionDef 노드
        
    Returns:
        함수의 signature 문자열 (예: "(x, y, z=10)")
    """
    args = node.args
    arg_strs = []
    
    # 일반 인자
    for arg in args.args:
        arg_strs.append(arg.arg)
    
    # 기본값이 있는 인자
    num_defaults = len(args.defaults)
    if num_defaults > 0:
        # 기본값이 있는 인자들의 시작 인덱스
        start_idx = len(args.args) - num_defaults
        for i, default in enumerate(args.defaults):
            arg_idx = start_idx + i
            arg_strs[arg_idx] = f"{arg_strs[arg_idx]}=..."
    
    # *args
    if args.vararg:
        arg_strs.append(f"*{args.vararg.arg}")
    
    # **kwargs
    if args.kwarg:
        arg_strs.append(f"**{args.kwarg.arg}")
    
    return f"({', '.join(arg_strs)})"


def scan_directory(root, exclude_dirs: Optional[list[str]] = None) -> list[dict]:
    """디렉토리를 재귀 탐색하여 .py 파일들의 parse_module 결과 리스트를 반환한다.
    
    Args:
        root: 탐색할 루트 디렉토리 경로
        exclude_dirs: 제외할 디렉토리 이름 리스트
                     기본값: ["__pycache__", ".git", "venv", ".venv", "node_modules"]
        
    Returns:
        parse_module 결과 딕셔너리의 리스트
    """
    if exclude_dirs is None:
        exclude_dirs = ["__pycache__", ".git", "venv", ".venv", "node_modules"]
    
    root_path = Path(root)
    results = []
    
    def _should_exclude(path: Path) -> bool:
        """경로가 제외 대상인지 확인한다."""
        for part in path.parts:
            if part in exclude_dirs:
                return True
        return False
    
    # 재귀적으로 .py 파일 찾기
    for py_file in root_path.rglob("*.py"):
        if not _should_exclude(py_file):
            module_info = parse_module(py_file)
            results.append(module_info)
    
    return results


def generate_markdown(modules: list[dict], title: str = "PROJECT_STRUCTURE") -> str:
    """모듈 목록을 마크다운 문자열로 변환한다.
    
    Args:
        modules: parse_module 결과 딕셔너리의 리스트
        title: 마크다운 제목 (기본값: "PROJECT_STRUCTURE")
        
    Returns:
        마크다운 형식의 문자열
    """
    lines = [f"# {title}", ""]
    
    if not modules:
        lines.append("(파이썬 파일 없음)")
        return "\n".join(lines)
    
    for module in modules:
        # 파일 경로를 헤더로 표시
        lines.append(f"## {module['path']}")
        lines.append("")
        
        # 클래스 표시
        if module.get("classes"):
            lines.append("### Classes")
            for cls in module["classes"]:
                lines.append(f"- **{cls['name']}**")
                if cls.get("docstring"):
                    lines.append(f"  - {cls['docstring']}")
                if cls.get("methods"):
                    for method in cls["methods"]:
                        lines.append(f"  - `{method}()`")
            lines.append("")
        
        # 함수 표시
        if module.get("functions"):
            lines.append("### Functions")
            for func in module["functions"]:
                sig = func.get("signature", "()")
                lines.append(f"- **{func['name']}{sig}**")
                if func.get("docstring"):
                    lines.append(f"  - {func['docstring']}")
            lines.append("")
    
    return "\n".join(lines)


def update(root: str = ".", output: str = "PROJECT_STRUCTURE.md") -> Path:
    """scan_directory → generate_markdown → 파일 저장 후 경로를 반환한다.
    
    Args:
        root: 탐색할 루트 디렉토리 경로 (기본값: ".")
        output: 출력 파일 경로 (기본값: "PROJECT_STRUCTURE.md")
        
    Returns:
        생성된 파일의 Path 객체
    """
    # 디렉토리 스캔
    modules = scan_directory(root)
    
    # 마크다운 생성
    markdown_content = generate_markdown(modules)
    
    # 파일 저장
    output_path = Path(output)
    
    # output이 상대 경로이면 root 디렉토리 내에 생성
    if not output_path.is_absolute():
        output_path = Path(root) / output
    
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(markdown_content, encoding='utf-8')
    
    return output_path
