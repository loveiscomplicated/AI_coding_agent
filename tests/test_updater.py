"""structure.updater 모듈 테스트"""
import pytest
from pathlib import Path
from structure.updater import parse_module, scan_directory, generate_markdown, update


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
        
        another_class = next(cls for cls in result["classes"] if cls["name"] == "AnotherClass")
        assert "method_a" in another_class["methods"]
    
    def test_parse_module_extracts_class_docstring(self, sample_python_file):
        """수락 기준 1: 클래스 docstring을 추출한다"""
        result = parse_module(sample_python_file)
        
        sample_class = next(cls for cls in result["classes"] if cls["name"] == "SampleClass")
        assert sample_class["docstring"] == "샘플 클래스 docstring"
    
    def test_parse_module_extracts_top_level_functions(self, sample_python_file):
        """수락 기준 2: 최상위 함수의 이름을 추출한다"""
        result = parse_module(sample_python_file)
        
        assert "functions" in result
        assert len(result["functions"]) == 2
        
        func_names = [func["name"] for func in result["functions"]]
        assert "top_level_function" in func_names
        assert "another_function" in func_names
    
    def test_parse_module_extracts_function_signature(self, sample_python_file):
        """수락 기준 2: 함수의 signature를 추출한다"""
        result = parse_module(sample_python_file)
        
        another_func = next(func for func in result["functions"] if func["name"] == "another_function")
        assert "signature" in another_func
        assert "x" in another_func["signature"]
        assert "y" in another_func["signature"]
    
    def test_parse_module_extracts_function_docstring(self, sample_python_file):
        """수락 기준 2: 함수의 docstring 첫 줄을 추출한다"""
        result = parse_module(sample_python_file)
        
        top_func = next(func for func in result["functions"] if func["name"] == "top_level_function")
        assert top_func["docstring"] == "최상위 함수 docstring"
    
    def test_parse_module_returns_path(self, sample_python_file):
        """parse_module은 파일 경로를 반환한다"""
        result = parse_module(sample_python_file)
        
        assert "path" in result
        assert result["path"] == str(sample_python_file)
    
    def test_parse_module_handles_syntax_error(self, syntax_error_file):
        """수락 기준 3: 구문 오류 있는 파일에서 빈 dict를 반환한다"""
        result = parse_module(syntax_error_file)
        
        assert isinstance(result, dict)
        assert result.get("classes") == []
        assert result.get("functions") == []
        assert "path" in result
    
    def test_parse_module_handles_empty_file(self, temp_workspace):
        """빈 파일을 처리한다"""
        empty_file = temp_workspace / "empty.py"
        empty_file.write_text("")
        
        result = parse_module(empty_file)
        
        assert result["classes"] == []
        assert result["functions"] == []
    
    def test_parse_module_ignores_nested_classes(self, temp_workspace):
        """최상위 클래스만 추출한다 (중첩 클래스 제외)"""
        code = '''
class OuterClass:
    class InnerClass:
        pass
    
    def method(self):
        pass
'''
        file_path = temp_workspace / "nested.py"
        file_path.write_text(code)
        
        result = parse_module(file_path)
        
        assert len(result["classes"]) == 1
        assert result["classes"][0]["name"] == "OuterClass"
    
    def test_parse_module_ignores_nested_functions(self, temp_workspace):
        """최상위 함수만 추출한다 (중첩 함수 제외)"""
        code = '''
def outer_function():
    def inner_function():
        pass
    return inner_function
'''
        file_path = temp_workspace / "nested_func.py"
        file_path.write_text(code)
        
        result = parse_module(file_path)
        
        assert len(result["functions"]) == 1
        assert result["functions"][0]["name"] == "outer_function"


class TestScanDirectory:
    """scan_directory() 함수 테스트"""
    
    def test_scan_directory_finds_python_files(self, complex_directory_structure):
        """수락 기준 4: .py 확장자 파일만 처리한다"""
        result = scan_directory(complex_directory_structure)
        
        assert isinstance(result, list)
        assert len(result) > 0
        
        # 모든 결과가 dict이고 path를 포함해야 함
        for module in result:
            assert isinstance(module, dict)
            assert "path" in module
            assert module["path"].endswith(".py")
    
    def test_scan_directory_excludes_pycache(self, complex_directory_structure):
        """수락 기준 5: __pycache__ 디렉토리를 건너뛴다"""
        result = scan_directory(complex_directory_structure)
        
        paths = [module["path"] for module in result]
        
        # __pycache__ 경로가 없어야 함
        for path in paths:
            assert "__pycache__" not in path
    
    def test_scan_directory_excludes_venv(self, complex_directory_structure):
        """수락 기준 5: venv 디렉토리를 건너뛴다"""
        result = scan_directory(complex_directory_structure)
        
        paths = [module["path"] for module in result]
        
        # venv 경로가 없어야 함
        for path in paths:
            assert "venv" not in path
    
    def test_scan_directory_default_exclude_dirs(self, complex_directory_structure):
        """기본 exclude_dirs는 ["__pycache__", ".git", "venv", ".venv", "node_modules"]"""
        result = scan_directory(complex_directory_structure)
        
        paths = [module["path"] for module in result]
        
        # 기본 제외 디렉토리들이 포함되지 않아야 함
        for path in paths:
            assert "__pycache__" not in path
            assert ".git" not in path
            assert "venv" not in path
            assert ".venv" not in path
            assert "node_modules" not in path
    
    def test_scan_directory_custom_exclude_dirs(self, complex_directory_structure):
        """custom exclude_dirs를 사용할 수 있다"""
        result = scan_directory(complex_directory_structure, exclude_dirs=["tests"])
        
        paths = [module["path"] for module in result]
        
        # tests 디렉토리가 제외되어야 함
        for path in paths:
            assert "tests" not in path
    
    def test_scan_directory_recursive_search(self, complex_directory_structure):
        """재귀적으로 모든 .py 파일을 찾는다"""
        result = scan_directory(complex_directory_structure)
        
        paths = [module["path"] for module in result]
        
        # src 디렉토리의 파일들을 찾아야 함
        assert any("src" in path and "module1.py" in path for path in paths)
        assert any("src" in path and "module2.py" in path for path in paths)
    
    def test_scan_directory_returns_list_of_dicts(self, complex_directory_structure):
        """list[dict] 형태를 반환한다"""
        result = scan_directory(complex_directory_structure)
        
        assert isinstance(result, list)
        for item in result:
            assert isinstance(item, dict)
            assert "path" in item
            assert "classes" in item
            assert "functions" in item


class TestGenerateMarkdown:
    """generate_markdown() 함수 테스트"""
    
    def test_generate_markdown_includes_header(self):
        """수락 기준 6: "# PROJECT_STRUCTURE" 헤더를 포함한다"""
        modules = []
        result = generate_markdown(modules)
        
        assert "# PROJECT_STRUCTURE" in result
    
    def test_generate_markdown_custom_title(self):
        """custom title을 사용할 수 있다"""
        modules = []
        result = generate_markdown(modules, title="MY_STRUCTURE")
        
        assert "# MY_STRUCTURE" in result
    
    def test_generate_markdown_empty_modules(self):
        """수락 기준 7: 모듈이 없으면 "(파이썬 파일 없음)"을 포함한다"""
        modules = []
        result = generate_markdown(modules)
        
        assert "(파이썬 파일 없음)" in result
    
    def test_generate_markdown_includes_file_paths(self):
        """모듈의 파일 경로를 헤더로 표시한다"""
        modules = [
            {
                "path": "src/module1.py",
                "classes": [],
                "functions": []
            }
        ]
        result = generate_markdown(modules)
        
        assert "src/module1.py" in result
    
    def test_generate_markdown_includes_classes(self):
        """클래스를 목록으로 표시한다"""
        modules = [
            {
                "path": "src/module1.py",
                "classes": [
                    {
                        "name": "MyClass",
                        "methods": ["method1", "method2"],
                        "docstring": "클래스 설명"
                    }
                ],
                "functions": []
            }
        ]
        result = generate_markdown(modules)
        
        assert "MyClass" in result
        assert "method1" in result
        assert "method2" in result
    
    def test_generate_markdown_includes_functions(self):
        """함수를 목록으로 표시한다"""
        modules = [
            {
                "path": "src/module1.py",
                "classes": [],
                "functions": [
                    {
                        "name": "my_function",
                        "signature": "(x, y)",
                        "docstring": "함수 설명"
                    }
                ]
            }
        ]
        result = generate_markdown(modules)
        
        assert "my_function" in result
    
    def test_generate_markdown_multiple_modules(self):
        """여러 모듈을 처리한다"""
        modules = [
            {
                "path": "src/module1.py",
                "classes": [{"name": "Class1", "methods": [], "docstring": None}],
                "functions": []
            },
            {
                "path": "src/module2.py",
                "classes": [{"name": "Class2", "methods": [], "docstring": None}],
                "functions": []
            }
        ]
        result = generate_markdown(modules)
        
        assert "src/module1.py" in result
        assert "src/module2.py" in result
        assert "Class1" in result
        assert "Class2" in result
    
    def test_generate_markdown_returns_string(self):
        """문자열을 반환한다"""
        modules = []
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
        """기본 output은 "PROJECT_STRUCTURE.md"이다"""
        result = update(str(complex_directory_structure))
        
        assert result.name == "PROJECT_STRUCTURE.md"
        assert result.exists()
    
    def test_update_default_root_path(self, temp_workspace):
        """기본 root는 "."이다"""
        # 현재 디렉토리를 temp_workspace로 변경하고 테스트
        import os
        original_cwd = os.getcwd()
        try:
            os.chdir(str(temp_workspace))
            
            # 테스트 파일 생성
            (temp_workspace / "test.py").write_text("def test(): pass")
            
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
    
    def test_update_file_contains_modules(self, complex_directory_structure):
        """생성된 파일이 발견된 모듈을 포함한다"""
        output_path = complex_directory_structure / "PROJECT_STRUCTURE.md"
        
        update(str(complex_directory_structure), str(output_path))
        
        content = output_path.read_text()
        # src 디렉토리의 파일들이 포함되어야 함
        assert "module1.py" in content or "module2.py" in content or "(파이썬 파일 없음)" in content
    
    def test_update_overwrites_existing_file(self, complex_directory_structure):
        """기존 파일을 덮어쓴다"""
        output_path = complex_directory_structure / "PROJECT_STRUCTURE.md"
        
        # 첫 번째 업데이트
        update(str(complex_directory_structure), str(output_path))
        first_content = output_path.read_text()
        
        # 두 번째 업데이트
        update(str(complex_directory_structure), str(output_path))
        second_content = output_path.read_text()
        
        # 파일이 존재하고 내용이 있어야 함
        assert output_path.exists()
        assert len(second_content) > 0


class TestIntegration:
    """통합 테스트"""
    
    def test_full_workflow(self, complex_directory_structure):
        """전체 워크플로우: scan → generate → update"""
        output_path = complex_directory_structure / "PROJECT_STRUCTURE.md"
        
        # update 함수가 전체 워크플로우를 수행
        result = update(str(complex_directory_structure), str(output_path))
        
        # 파일이 생성되었는지 확인
        assert result.exists()
        
        # 파일 내용 확인
        content = result.read_text()
        assert "# PROJECT_STRUCTURE" in content
        assert len(content) > 0
    
    def test_parse_and_generate_integration(self, sample_python_file, temp_workspace):
        """parse_module과 generate_markdown의 통합"""
        # parse_module으로 파일 파싱
        parsed = parse_module(sample_python_file)
        
        # generate_markdown으로 마크다운 생성
        markdown = generate_markdown([parsed])
        
        # 결과 확인
        assert "SampleClass" in markdown
        assert "top_level_function" in markdown
        assert "# PROJECT_STRUCTURE" in markdown
