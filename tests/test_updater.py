"""
structure.updater 모듈의 테스트
"""
import pytest
from pathlib import Path
from structure.updater import parse_module, scan_directory, generate_markdown, update, parse_file


class TestParseModule:
    """parse_module() 함수 테스트"""
    
    def test_parse_module_extracts_class_names(self, sample_python_file):
        """수락 기준 1: 클래스 이름과 메서드 이름 목록을 정확히 추출한다"""
        result = parse_module(sample_python_file)
        
        assert "classes" in result
        assert len(result["classes"]) == 2
        
        class_names = [cls["name"] for cls in result["classes"]]
        assert "SampleClass" in class_names
        assert "AnotherClass" in class_names
    
    def test_parse_module_extracts_method_names(self, sample_python_file):
        """수락 기준 1: 메서드 이름 목록을 정확히 추출한다"""
        result = parse_module(sample_python_file)
        
        sample_class = next(cls for cls in result["classes"] if cls["name"] == "SampleClass")
        assert "methods" in sample_class
        assert "method_one" in sample_class["methods"]
        assert "method_two" in sample_class["methods"]
    
    def test_parse_module_includes_class_docstring(self, sample_python_file):
        """클래스의 docstring을 포함한다"""
        result = parse_module(sample_python_file)
        
        sample_class = next(cls for cls in result["classes"] if cls["name"] == "SampleClass")
        assert sample_class["docstring"] == "샘플 클래스 docstring"
    
    def test_parse_module_extracts_top_level_functions(self, sample_python_file):
        """수락 기준 2: 최상위 함수의 이름과 docstring 첫 줄을 추출한다"""
        result = parse_module(sample_python_file)
        
        assert "functions" in result
        assert len(result["functions"]) == 2
        
        func_names = [func["name"] for func in result["functions"]]
        assert "top_level_function" in func_names
        assert "another_function" in func_names
    
    def test_parse_module_extracts_function_docstring_first_line(self, sample_python_file):
        """수락 기준 2: 함수의 docstring 첫 줄을 추출한다"""
        result = parse_module(sample_python_file)
        
        func = next(f for f in result["functions"] if f["name"] == "top_level_function")
        assert func["docstring"] == "최상위 함수 docstring"
    
    def test_parse_module_extracts_function_signature(self, sample_python_file):
        """함수의 signature를 추출한다"""
        result = parse_module(sample_python_file)
        
        func = next(f for f in result["functions"] if f["name"] == "another_function")
        assert "signature" in func
        assert "x" in func["signature"]
        assert "y" in func["signature"]
    
    def test_parse_module_handles_syntax_error(self, syntax_error_file):
        """수락 기준 3: 구문 오류 있는 파일에서 빈 dict를 반환한다"""
        result = parse_module(syntax_error_file)
        
        assert isinstance(result, dict)
        assert result.get("classes") == []
        assert result.get("functions") == []
        assert "path" in result
    
    def test_parse_module_returns_path(self, sample_python_file):
        """반환값에 파일 경로를 포함한다"""
        result = parse_module(sample_python_file)
        
        assert "path" in result
        assert result["path"] == str(sample_python_file)
    
    def test_parse_module_handles_no_docstring(self, temp_workspace):
        """docstring이 없는 경우 None을 반환한다"""
        file_path = temp_workspace / "no_docstring.py"
        file_path.write_text('''
class NoDocClass:
    def method(self):
        pass

def no_doc_func():
    pass
''')
        result = parse_module(file_path)
        
        cls = result["classes"][0]
        assert cls["docstring"] is None
        
        func = result["functions"][0]
        assert func["docstring"] is None
    
    def test_parse_module_only_extracts_top_level(self, temp_workspace):
        """최상위 함수와 클래스만 추출한다"""
        file_path = temp_workspace / "nested.py"
        file_path.write_text('''
class OuterClass:
    def outer_method(self):
        def inner_function():
            pass
        pass

def top_level_func():
    class InnerClass:
        pass
    pass
''')
        result = parse_module(file_path)
        
        # 최상위 클래스는 1개
        assert len(result["classes"]) == 1
        assert result["classes"][0]["name"] == "OuterClass"
        
        # 최상위 함수는 1개
        assert len(result["functions"]) == 1
        assert result["functions"][0]["name"] == "top_level_func"


class TestScanDirectory:
    """scan_directory() 함수 테스트"""
    
    def test_scan_directory_finds_supported_files(self, complex_directory_structure):
        """수락 기준 4: 지원 언어 파일(.py/.ts/.tsx/.js/.jsx)을 처리한다"""
        result = scan_directory(complex_directory_structure)

        assert isinstance(result, list)
        assert len(result) > 0

        supported_exts = {".py", ".ts", ".tsx", ".js", ".jsx"}
        for module in result:
            assert isinstance(module, dict)
            assert "path" in module
            ext = "." + module["path"].rsplit(".", 1)[-1]
            assert ext in supported_exts
    
    def test_scan_directory_excludes_default_dirs(self, complex_directory_structure):
        """수락 기준 5: exclude_dirs에 지정된 디렉토리를 건너뛴다"""
        result = scan_directory(complex_directory_structure)
        
        paths = [module["path"] for module in result]
        
        # __pycache__, venv, .git 디렉토리의 파일은 포함되지 않아야 함
        for path in paths:
            assert "__pycache__" not in path
            assert "venv" not in path
            assert ".git" not in path
    
    def test_scan_directory_with_custom_exclude_dirs(self, complex_directory_structure):
        """커스텀 exclude_dirs를 사용할 수 있다"""
        result = scan_directory(complex_directory_structure, exclude_dirs=["src"])
        
        paths = [module["path"] for module in result]
        
        # src 디렉토리의 파일은 포함되지 않아야 함
        for path in paths:
            assert "src" not in path
    
    def test_scan_directory_recursive_search(self, complex_directory_structure):
        """재귀적으로 하위 디렉토리를 탐색한다"""
        result = scan_directory(complex_directory_structure)
        
        paths = [module["path"] for module in result]
        
        # src/subdir/module3.py가 포함되어야 함
        assert any("subdir" in path and "module3.py" in path for path in paths)
    
    def test_scan_directory_empty_directory(self, empty_directory):
        """파이썬 파일이 없는 디렉토리는 빈 리스트를 반환한다"""
        result = scan_directory(empty_directory)
        
        assert isinstance(result, list)
        assert len(result) == 0
    
    def test_scan_directory_returns_parse_module_results(self, complex_directory_structure):
        """각 파일의 parse_module 결과를 반환한다"""
        result = scan_directory(complex_directory_structure)
        
        for module in result:
            assert "path" in module
            assert "classes" in module
            assert "functions" in module


class TestGenerateMarkdown:
    """generate_markdown() 함수 테스트"""
    
    def test_generate_markdown_includes_header(self):
        """수락 기준 6: 반환값은 "# PROJECT_STRUCTURE" 헤더를 포함한다"""
        modules = [
            {
                "path": "test.py",
                "classes": [],
                "functions": []
            }
        ]
        result = generate_markdown(modules)
        
        assert "# PROJECT_STRUCTURE" in result
    
    def test_generate_markdown_custom_title(self):
        """커스텀 제목을 사용할 수 있다"""
        modules = [
            {
                "path": "test.py",
                "classes": [],
                "functions": []
            }
        ]
        result = generate_markdown(modules, title="MY_STRUCTURE")
        
        assert "# MY_STRUCTURE" in result
    
    def test_generate_markdown_no_modules(self):
        """수락 기준 7: 모듈이 없으면 "(소스 파일 없음)"을 포함한다"""
        modules = []
        result = generate_markdown(modules)

        assert "(소스 파일 없음)" in result
    
    def test_generate_markdown_includes_file_paths(self):
        """파일 경로를 헤더로 표시한다"""
        modules = [
            {
                "path": "src/module1.py",
                "classes": [],
                "functions": []
            },
            {
                "path": "src/module2.py",
                "classes": [],
                "functions": []
            }
        ]
        result = generate_markdown(modules)
        
        assert "src/module1.py" in result
        assert "src/module2.py" in result
    
    def test_generate_markdown_includes_classes(self):
        """클래스를 목록으로 표시한다"""
        modules = [
            {
                "path": "test.py",
                "classes": [
                    {
                        "name": "TestClass",
                        "methods": ["method1", "method2"],
                        "docstring": "Test class"
                    }
                ],
                "functions": []
            }
        ]
        result = generate_markdown(modules)
        
        assert "TestClass" in result
        assert "method1" in result
        assert "method2" in result
    
    def test_generate_markdown_includes_functions(self):
        """함수를 목록으로 표시한다"""
        modules = [
            {
                "path": "test.py",
                "classes": [],
                "functions": [
                    {
                        "name": "test_func",
                        "signature": "test_func(x, y)",
                        "docstring": "Test function"
                    }
                ]
            }
        ]
        result = generate_markdown(modules)
        
        assert "test_func" in result
    
    def test_generate_markdown_returns_string(self):
        """문자열을 반환한다"""
        modules = [
            {
                "path": "test.py",
                "classes": [],
                "functions": []
            }
        ]
        result = generate_markdown(modules)
        
        assert isinstance(result, str)


class TestUpdate:
    """update() 함수 테스트"""
    
    def test_update_creates_file(self, complex_directory_structure):
        """수락 기준 8: 파일을 생성한다"""
        output_path = complex_directory_structure / "PROJECT_STRUCTURE.md"
        
        result = update(str(complex_directory_structure), str(output_path))
        
        assert output_path.exists()
    
    def test_update_returns_path(self, complex_directory_structure):
        """수락 기준 8: 존재하는 Path를 반환한다"""
        output_path = complex_directory_structure / "PROJECT_STRUCTURE.md"
        
        result = update(str(complex_directory_structure), str(output_path))
        
        assert isinstance(result, Path)
        assert result.exists()
    
    def test_update_default_output_path(self, complex_directory_structure):
        """기본 output 경로는 "PROJECT_STRUCTURE.md"이다"""
        result = update(str(complex_directory_structure))
        
        assert result.name == "PROJECT_STRUCTURE.md"
        assert result.exists()
    
    def test_update_default_root_path(self, temp_workspace):
        """기본 root 경로는 "."이다"""
        # 현재 디렉토리에 파이썬 파일 생성
        (temp_workspace / "test.py").write_text("def func(): pass")
        
        # 현재 디렉토리를 temp_workspace로 변경하고 테스트
        import os
        original_cwd = os.getcwd()
        try:
            os.chdir(temp_workspace)
            result = update()
            assert result.exists()
            assert result.name == "PROJECT_STRUCTURE.md"
        finally:
            os.chdir(original_cwd)
    
    def test_update_file_contains_markdown(self, complex_directory_structure):
        """생성된 파일이 마크다운 형식을 포함한다"""
        output_path = complex_directory_structure / "PROJECT_STRUCTURE.md"
        
        update(str(complex_directory_structure), str(output_path))
        
        content = output_path.read_text()
        assert "# PROJECT_STRUCTURE" in content
    
    def test_update_overwrites_existing_file(self, complex_directory_structure):
        """기존 파일을 덮어쓴다"""
        output_path = complex_directory_structure / "PROJECT_STRUCTURE.md"
        
        # 첫 번째 업데이트
        update(str(complex_directory_structure), str(output_path))
        first_content = output_path.read_text()
        
        # 두 번째 업데이트
        update(str(complex_directory_structure), str(output_path))
        second_content = output_path.read_text()
        
        # 내용이 동일해야 함 (또는 업데이트되어야 함)
        assert output_path.exists()
        assert len(second_content) > 0


class TestIntegration:
    """통합 테스트"""
    
    def test_full_workflow(self, complex_directory_structure):
        """전체 워크플로우: scan → generate → update"""
        # scan_directory
        modules = scan_directory(complex_directory_structure)
        assert len(modules) > 0
        
        # generate_markdown
        markdown = generate_markdown(modules)
        assert "# PROJECT_STRUCTURE" in markdown
        
        # update
        output_path = complex_directory_structure / "OUTPUT.md"
        result = update(str(complex_directory_structure), str(output_path))
        
        assert result.exists()
        assert result.read_text() == markdown
    
    def test_parse_module_with_real_structure(self, complex_directory_structure):
        """실제 디렉토리 구조에서 parse_module 테스트"""
        module_file = complex_directory_structure / "src" / "module1.py"
        result = parse_module(module_file)
        
        assert len(result["classes"]) == 1
        assert result["classes"][0]["name"] == "ClassA"
        assert len(result["functions"]) == 1
        assert result["functions"][0]["name"] == "func_a"


class TestParseFileTypeScript:
    """TypeScript 파일 파싱 테스트"""

    def test_parse_ts_class(self, tmp_path):
        """TypeScript 클래스와 메서드를 추출한다"""
        f = tmp_path / "service.ts"
        f.write_text(
            """\
class TokenService {
    createToken(userId: string): string {
        return '';
    }
    verifyToken(token: string): boolean {
        return true;
    }
}
""",
            encoding="utf-8",
        )
        result = parse_file(f)

        assert result["language"] == "TypeScript"
        assert len(result["classes"]) == 1
        assert result["classes"][0]["name"] == "TokenService"
        assert "createToken" in result["classes"][0]["methods"]
        assert "verifyToken" in result["classes"][0]["methods"]

    def test_parse_ts_function(self, tmp_path):
        """TypeScript 최상위 함수를 추출한다"""
        f = tmp_path / "utils.ts"
        f.write_text(
            """\
export function add(a: number, b: number): number {
    return a + b;
}
""",
            encoding="utf-8",
        )
        result = parse_file(f)

        assert len(result["functions"]) == 1
        func = result["functions"][0]
        assert func["name"] == "add"
        assert "a" in func["signature"]
        assert "b" in func["signature"]

    def test_parse_ts_jsdoc(self, tmp_path):
        """TypeScript JSDoc 주석 첫 줄을 docstring으로 추출한다"""
        f = tmp_path / "api.ts"
        f.write_text(
            """\
/** Generates a random key */
export function generateKey(): string {
    return '';
}
""",
            encoding="utf-8",
        )
        result = parse_file(f)

        assert len(result["functions"]) == 1
        assert result["functions"][0]["docstring"] == "Generates a random key"

    def test_parse_ts_arrow_function(self, tmp_path):
        """const 화살표 함수를 추출한다"""
        f = tmp_path / "arrow.ts"
        f.write_text(
            "export const multiply = (x: number, y: number): number => x * y;\n",
            encoding="utf-8",
        )
        result = parse_file(f)

        assert len(result["functions"]) == 1
        assert result["functions"][0]["name"] == "multiply"


class TestScanDirectoryMultiLang:
    """다언어 scan_directory 테스트"""

    def test_includes_typescript(self, tmp_path):
        """.ts 파일을 포함한다"""
        (tmp_path / "index.ts").write_text("export function foo() {}\n", encoding="utf-8")
        (tmp_path / "main.py").write_text("def bar(): pass\n", encoding="utf-8")

        result = scan_directory(tmp_path)
        paths = [m["path"] for m in result]

        assert any(p.endswith(".ts") for p in paths)
        assert any(p.endswith(".py") for p in paths)

    def test_unsupported_language_included_as_file_only(self, tmp_path):
        """미지원 언어(.rb/.lua)는 파일명만 포함되고 클래스/함수는 빈 리스트다"""
        (tmp_path / "app.rb").write_text("def foo; end\n", encoding="utf-8")
        (tmp_path / "script.lua").write_text("function foo() end\n", encoding="utf-8")
        (tmp_path / "main.py").write_text("def ok(): pass\n", encoding="utf-8")

        result = scan_directory(tmp_path)
        paths = [m["path"] for m in result]

        # 미지원 언어도 경로는 포함
        assert any(p.endswith(".rb") for p in paths)
        assert any(p.endswith(".lua") for p in paths)
        # 하지만 클래스/함수 정보는 없음
        rb = next(m for m in result if m["path"].endswith(".rb"))
        assert rb["classes"] == []
        assert rb["functions"] == []

    def test_all_non_excluded_dir_files_included(self, tmp_path):
        """제외 디렉토리 밖의 파일은 확장자에 무관하게 목록에 포함된다"""
        (tmp_path / "image.png").write_bytes(b"\x89PNG")
        (tmp_path / "data.db").write_bytes(b"SQLite")
        (tmp_path / "main.py").write_text("def ok(): pass\n", encoding="utf-8")

        result = scan_directory(tmp_path)
        paths = [m["path"] for m in result]

        assert any(p.endswith(".png") for p in paths)
        assert any(p.endswith(".db") for p in paths)
        assert any(p.endswith(".py") for p in paths)


class TestParseFileC:
    """C 파일 파싱 테스트"""

    def test_parse_c_struct(self, tmp_path):
        """C struct를 클래스로 추출한다"""
        f = tmp_path / "point.c"
        f.write_text("struct Point { int x; int y; };\n", encoding="utf-8")
        result = parse_file(f)
        assert result["language"] == "C"
        assert any(c["name"] == "Point" for c in result["classes"])

    def test_parse_c_function(self, tmp_path):
        """C 최상위 함수를 추출한다"""
        f = tmp_path / "math.c"
        f.write_text("int add(int a, int b) { return a + b; }\n", encoding="utf-8")
        result = parse_file(f)
        assert any(fn["name"] == "add" for fn in result["functions"])
        func = next(fn for fn in result["functions"] if fn["name"] == "add")
        assert "a" in func["signature"]
        assert "b" in func["signature"]

    def test_parse_h_header(self, tmp_path):
        """.h 파일도 C로 파싱한다"""
        f = tmp_path / "api.h"
        f.write_text("void greet(const char* name);\n", encoding="utf-8")
        result = parse_file(f)
        assert result["language"] == "C"


class TestParseFileCpp:
    """C++ 파일 파싱 테스트"""

    def test_parse_cpp_class_with_methods(self, tmp_path):
        """C++ 클래스와 멤버 함수를 추출한다"""
        f = tmp_path / "animal.cpp"
        f.write_text(
            "class Animal {\npublic:\n    void speak() {}\n    int age() { return 0; }\n};\n",
            encoding="utf-8",
        )
        result = parse_file(f)
        assert result["language"] == "C++"
        assert any(c["name"] == "Animal" for c in result["classes"])
        animal = next(c for c in result["classes"] if c["name"] == "Animal")
        assert "speak" in animal["methods"]
        assert "age" in animal["methods"]

    def test_parse_cpp_top_level_function(self, tmp_path):
        """C++ 최상위 함수를 추출한다"""
        f = tmp_path / "utils.cpp"
        f.write_text("int square(int x) { return x * x; }\n", encoding="utf-8")
        result = parse_file(f)
        assert any(fn["name"] == "square" for fn in result["functions"])


class TestParseFileRust:
    """Rust 파일 파싱 테스트"""

    def test_parse_rust_struct(self, tmp_path):
        """Rust struct를 클래스로 추출한다"""
        f = tmp_path / "config.rs"
        f.write_text("pub struct Config { pub name: String }\n", encoding="utf-8")
        result = parse_file(f)
        assert result["language"] == "Rust"
        assert any(c["name"] == "Config" for c in result["classes"])

    def test_parse_rust_impl_methods(self, tmp_path):
        """Rust impl 블록의 메서드를 struct에 귀속시킨다"""
        f = tmp_path / "server.rs"
        f.write_text(
            "pub struct Server {}\nimpl Server {\n    pub fn start(&self) {}\n    pub fn stop(&self) {}\n}\n",
            encoding="utf-8",
        )
        result = parse_file(f)
        server = next(c for c in result["classes"] if c["name"] == "Server")
        assert "start" in server["methods"]
        assert "stop" in server["methods"]

    def test_parse_rust_function(self, tmp_path):
        """Rust 최상위 함수를 추출한다"""
        f = tmp_path / "lib.rs"
        f.write_text("pub fn greet(name: &str) -> String { String::new() }\n", encoding="utf-8")
        result = parse_file(f)
        assert any(fn["name"] == "greet" for fn in result["functions"])
        func = next(fn for fn in result["functions"] if fn["name"] == "greet")
        assert "name" in func["signature"]


class TestParseFileGo:
    """Go 파일 파싱 테스트"""

    def test_parse_go_struct(self, tmp_path):
        """Go struct를 클래스로 추출한다"""
        f = tmp_path / "server.go"
        f.write_text("package main\ntype Server struct { Port int }\n", encoding="utf-8")
        result = parse_file(f)
        assert result["language"] == "Go"
        assert any(c["name"] == "Server" for c in result["classes"])

    def test_parse_go_method_attached_to_struct(self, tmp_path):
        """Go 메서드를 receiver 타입의 struct에 귀속시킨다"""
        f = tmp_path / "server.go"
        f.write_text(
            "package main\ntype Server struct {}\nfunc (s *Server) Start() error { return nil }\n",
            encoding="utf-8",
        )
        result = parse_file(f)
        server = next(c for c in result["classes"] if c["name"] == "Server")
        assert "Start" in server["methods"]

    def test_parse_go_function(self, tmp_path):
        """Go 최상위 함수를 추출한다"""
        f = tmp_path / "math.go"
        f.write_text("package main\nfunc Add(a, b int) int { return a + b }\n", encoding="utf-8")
        result = parse_file(f)
        assert any(fn["name"] == "Add" for fn in result["functions"])


class TestParseFileJava:
    """Java 파일 파싱 테스트"""

    def test_parse_java_class_with_methods(self, tmp_path):
        """Java 클래스와 메서드를 추출한다"""
        f = tmp_path / "Calculator.java"
        f.write_text(
            "public class Calculator {\n    public int add(int a, int b) { return a + b; }\n}\n",
            encoding="utf-8",
        )
        result = parse_file(f)
        assert result["language"] == "Java"
        assert any(c["name"] == "Calculator" for c in result["classes"])
        calc = next(c for c in result["classes"] if c["name"] == "Calculator")
        assert "add" in calc["methods"]

    def test_parse_java_javadoc(self, tmp_path):
        """Java Javadoc 주석 첫 줄을 docstring으로 추출한다"""
        f = tmp_path / "Service.java"
        f.write_text(
            "/** User service class */\npublic class UserService {}\n",
            encoding="utf-8",
        )
        result = parse_file(f)
        assert result["classes"][0]["docstring"] == "User service class"

    def test_parse_java_interface(self, tmp_path):
        """Java interface를 클래스로 추출한다"""
        f = tmp_path / "Repo.java"
        f.write_text("public interface Repository { void save(); }\n", encoding="utf-8")
        result = parse_file(f)
        assert any(c["name"] == "Repository" for c in result["classes"])
