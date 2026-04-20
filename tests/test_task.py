"""
tests/test_task.py

orchestrator/task.py 단위 테스트.
외부 의존성 없음 — tmp_path fixture로 실제 YAML I/O 검증.

실행:
    pytest tests/test_task.py -v
"""

from __future__ import annotations

import os
import sys

import pytest
import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from orchestrator.task import Task, TaskStatus, load_tasks, save_tasks, LANGUAGE_TEST_FRAMEWORK_MAP


# ── 픽스처 ────────────────────────────────────────────────────────────────────


@pytest.fixture
def minimal_task_dict():
    return {
        "id": "task-001",
        "title": "사용자 인증 구현",
        "description": "JWT 기반 로그인 API를 구현한다.",
        "acceptance_criteria": [
            "올바른 자격증명으로 로그인 시 JWT 반환",
            "잘못된 자격증명으로 401 반환",
        ],
        "target_files": ["src/auth.py"],
        "test_framework": "pytest",
    }


@pytest.fixture
def sample_yaml(tmp_path, minimal_task_dict):
    path = tmp_path / "tasks.yaml"
    path.write_text(
        yaml.dump({"tasks": [minimal_task_dict]}, allow_unicode=True),
        encoding="utf-8",
    )
    return path


# ── Task.from_dict ────────────────────────────────────────────────────────────


class TestTaskFromDict:
    def test_loads_required_fields(self, minimal_task_dict):
        task = Task.from_dict(minimal_task_dict)
        assert task.id == "task-001"
        assert task.title == "사용자 인증 구현"
        assert len(task.acceptance_criteria) == 2
        assert task.target_files == ["src/auth.py"]

    def test_default_status_is_pending(self, minimal_task_dict):
        task = Task.from_dict(minimal_task_dict)
        assert task.status == TaskStatus.PENDING

    def test_default_test_framework_is_pytest(self):
        task = Task.from_dict(
            {
                "id": "x",
                "title": "t",
                "description": "d",
                "acceptance_criteria": ["c"],
            }
        )
        assert task.test_framework == "pytest"

    def test_restores_non_pending_status(self, minimal_task_dict):
        minimal_task_dict["status"] = "implementing"
        task = Task.from_dict(minimal_task_dict)
        assert task.status == TaskStatus.IMPLEMENTING

    def test_restores_retry_count_and_last_error(self, minimal_task_dict):
        minimal_task_dict["retry_count"] = 2
        minimal_task_dict["last_error"] = "AssertionError: ..."
        task = Task.from_dict(minimal_task_dict)
        assert task.retry_count == 2
        assert task.last_error == "AssertionError: ..."


# ── Task 프로퍼티 ─────────────────────────────────────────────────────────────


class TestTaskProperties:
    def test_branch_name(self, minimal_task_dict):
        task = Task.from_dict(minimal_task_dict)
        assert task.branch_name == "agent/task-001"

    def test_is_done_for_done_status(self, minimal_task_dict):
        task = Task.from_dict(minimal_task_dict)
        task.status = TaskStatus.DONE
        assert task.is_done is True

    def test_is_done_for_failed_status(self, minimal_task_dict):
        task = Task.from_dict(minimal_task_dict)
        task.status = TaskStatus.FAILED
        assert task.is_done is True

    def test_is_done_for_superseded_status(self, minimal_task_dict):
        task = Task.from_dict(minimal_task_dict)
        task.status = TaskStatus.SUPERSEDED
        assert task.is_done is True

    def test_superseded_status_serializable(self, minimal_task_dict, tmp_path):
        task = Task.from_dict(minimal_task_dict)
        task.status = TaskStatus.SUPERSEDED
        path = tmp_path / "tasks.yaml"
        save_tasks([task], path)
        loaded = load_tasks(path)
        assert loaded[0].status == TaskStatus.SUPERSEDED

    def test_is_not_done_for_in_progress(self, minimal_task_dict):
        task = Task.from_dict(minimal_task_dict)
        task.status = TaskStatus.IMPLEMENTING
        assert task.is_done is False

    def test_acceptance_criteria_text(self, minimal_task_dict):
        task = Task.from_dict(minimal_task_dict)
        text = task.acceptance_criteria_text()
        assert "1." in text
        assert "2." in text
        assert "JWT" in text


# ── Task.to_dict 왕복 ─────────────────────────────────────────────────────────


class TestTaskRoundTrip:
    def test_to_dict_and_back(self, minimal_task_dict):
        original = Task.from_dict(minimal_task_dict)
        original.status = TaskStatus.REVIEWING
        original.retry_count = 1
        original.last_error = "some error"
        original.pr_url = "https://github.com/owner/repo/pull/42"

        restored = Task.from_dict(original.to_dict())

        assert restored.id == original.id
        assert restored.status == original.status
        assert restored.retry_count == original.retry_count
        assert restored.last_error == original.last_error
        assert restored.pr_url == original.pr_url


# ── load_tasks ────────────────────────────────────────────────────────────────


class TestLoadTasks:
    def test_loads_single_task(self, sample_yaml):
        tasks = load_tasks(sample_yaml)
        assert len(tasks) == 1
        assert tasks[0].id == "task-001"

    def test_loads_multiple_tasks(self, tmp_path, minimal_task_dict):
        second = dict(minimal_task_dict, id="task-002", title="두 번째 태스크")
        path = tmp_path / "tasks.yaml"
        path.write_text(
            yaml.dump({"tasks": [minimal_task_dict, second]}, allow_unicode=True),
            encoding="utf-8",
        )
        tasks = load_tasks(path)
        assert len(tasks) == 2
        assert tasks[1].id == "task-002"

    def test_raises_if_file_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_tasks(tmp_path / "nonexistent.yaml")

    def test_raises_if_tasks_key_missing(self, tmp_path):
        path = tmp_path / "bad.yaml"
        path.write_text("something: []\n", encoding="utf-8")
        with pytest.raises(KeyError, match="tasks"):
            load_tasks(path)

    def test_raises_if_required_field_missing(self, tmp_path):
        path = tmp_path / "bad.yaml"
        path.write_text(
            yaml.dump({"tasks": [{"id": "x", "title": "t"}]}),
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="필수 필드 누락"):
            load_tasks(path)

    def test_raises_if_acceptance_criteria_not_list(self, tmp_path):
        path = tmp_path / "bad.yaml"
        path.write_text(
            yaml.dump({
                "tasks": [{
                    "id": "x",
                    "title": "t",
                    "description": "d",
                    "acceptance_criteria": "string instead of list",
                }]
            }),
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="리스트여야"):
            load_tasks(path)


# ── save_tasks ────────────────────────────────────────────────────────────────


class TestSaveTasks:
    def test_language_field_missing_falls_back_to_python(self, tmp_path):
        """language 필드가 없는 기존 tasks.yaml은 'python'으로 폴백된다."""
        path = tmp_path / "tasks.yaml"
        # language 필드 없이 저장
        path.write_text(
            "tasks:\n"
            "  - id: task-001\n"
            "    title: 구형 태스크\n"
            "    description: language 필드 없음\n"
            "    acceptance_criteria:\n"
            "      - 조건 하나\n",
            encoding="utf-8",
        )
        tasks = load_tasks(path)
        assert tasks[0].language == "python"

    def test_saves_and_reloads(self, tmp_path, minimal_task_dict):
        task = Task.from_dict(minimal_task_dict)
        task.status = TaskStatus.DONE
        task.pr_url = "https://github.com/owner/repo/pull/1"

        path = tmp_path / "output" / "tasks.yaml"  # 중간 디렉토리 없음
        save_tasks([task], path)

        reloaded = load_tasks(path)
        assert len(reloaded) == 1
        assert reloaded[0].status == TaskStatus.DONE
        assert reloaded[0].pr_url == task.pr_url

    def test_creates_parent_dirs(self, tmp_path):
        task = Task.from_dict(
            {
                "id": "x",
                "title": "t",
                "description": "d",
                "acceptance_criteria": ["c"],
            }
        )
        deep_path = tmp_path / "a" / "b" / "c" / "tasks.yaml"
        save_tasks([task], deep_path)
        assert deep_path.exists()


# ── language 필드 ─────────────────────────────────────────────────────────────


class TestLanguageField:
    def test_default_language_is_python(self):
        task = Task.from_dict({
            "id": "x", "title": "t", "description": "d",
            "acceptance_criteria": ["c"],
        })
        assert task.language == "python"

    def test_language_field_preserved(self):
        for lang in ("python", "kotlin", "javascript", "go", "ruby", "c", "cpp"):
            task = Task.from_dict({
                "id": "x", "title": "t", "description": "d",
                "acceptance_criteria": ["c"],
                "language": lang,
            })
            assert task.language == lang, f"language='{lang}' 보존 실패"

    def test_language_round_trips_through_to_dict(self):
        task = Task.from_dict({
            "id": "x", "title": "t", "description": "d",
            "acceptance_criteria": ["c"],
            "language": "kotlin",
        })
        restored = Task.from_dict(task.to_dict())
        assert restored.language == "kotlin"

    def test_language_saved_and_loaded_from_yaml(self, tmp_path):
        task = Task.from_dict({
            "id": "task-kt", "title": "Kotlin 태스크", "description": "d",
            "acceptance_criteria": ["c"],
            "language": "kotlin",
        })
        path = tmp_path / "tasks.yaml"
        save_tasks([task], path)
        loaded = load_tasks(path)
        assert loaded[0].language == "kotlin"


# ── LANGUAGE_TEST_FRAMEWORK_MAP ───────────────────────────────────────────────


class TestLanguageTestFrameworkMap:
    def test_python_maps_to_pytest(self):
        assert LANGUAGE_TEST_FRAMEWORK_MAP["python"] == "pytest"

    def test_go_maps_to_go(self):
        assert LANGUAGE_TEST_FRAMEWORK_MAP["go"] == "go"

    def test_kotlin_maps_to_gradle(self):
        assert LANGUAGE_TEST_FRAMEWORK_MAP["kotlin"] == "gradle"

    def test_javascript_maps_to_jest(self):
        assert LANGUAGE_TEST_FRAMEWORK_MAP["javascript"] == "jest"

    def test_typescript_maps_to_jest(self):
        assert LANGUAGE_TEST_FRAMEWORK_MAP["typescript"] == "jest"

    def test_ruby_maps_to_rspec(self):
        assert LANGUAGE_TEST_FRAMEWORK_MAP["ruby"] == "rspec"

    def test_c_maps_to_c(self):
        assert LANGUAGE_TEST_FRAMEWORK_MAP["c"] == "c"

    def test_cpp_maps_to_cpp(self):
        assert LANGUAGE_TEST_FRAMEWORK_MAP["cpp"] == "cpp"

    def test_all_values_are_nonempty_strings(self):
        for lang, fw in LANGUAGE_TEST_FRAMEWORK_MAP.items():
            assert isinstance(fw, str) and fw, f"언어 '{lang}'의 매핑값이 비어 있음"


# ── complexity 필드 ──────────────────────────────────────────────────────────


class TestComplexityField:
    def test_task_loads_yaml_without_complexity(self, minimal_task_dict):
        """기존 YAML(complexity 필드 부재) 로드 시 task.complexity는 None."""
        task = Task.from_dict(minimal_task_dict)
        assert task.complexity is None

    def test_loads_all_valid_complexity_values(self, minimal_task_dict):
        for value in ("simple", "standard", "complex"):
            data = dict(minimal_task_dict, complexity=value)
            task = Task.from_dict(data)
            assert task.complexity == value, f"complexity='{value}' 보존 실패"

    def test_invalid_complexity_coerces_to_none(self, minimal_task_dict):
        """비정상 값은 None으로 정규화된다 (Literal 보호)."""
        data = dict(minimal_task_dict, complexity="trivial")
        task = Task.from_dict(data)
        assert task.complexity is None

    def test_task_saves_complexity_to_yaml(self, tmp_path, minimal_task_dict):
        data = dict(minimal_task_dict, complexity="complex")
        task = Task.from_dict(data)
        path = tmp_path / "tasks.yaml"
        save_tasks([task], path)

        raw = path.read_text(encoding="utf-8")
        assert "complexity: complex" in raw

        loaded = load_tasks(path)
        assert loaded[0].complexity == "complex"

    def test_omits_complexity_key_when_none(self, tmp_path, minimal_task_dict):
        """complexity=None 태스크는 YAML에 complexity 키를 기록하지 않는다 (하위 호환)."""
        task = Task.from_dict(minimal_task_dict)
        assert task.complexity is None

        path = tmp_path / "tasks.yaml"
        save_tasks([task], path)
        raw = path.read_text(encoding="utf-8")
        assert "complexity" not in raw

        # 재로드해도 None 유지
        reloaded = load_tasks(path)
        assert reloaded[0].complexity is None

    def test_complexity_round_trips_through_to_dict(self, minimal_task_dict):
        data = dict(minimal_task_dict, complexity="simple")
        task = Task.from_dict(data)
        restored = Task.from_dict(task.to_dict())
        assert restored.complexity == "simple"
