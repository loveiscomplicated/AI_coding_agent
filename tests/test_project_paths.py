from __future__ import annotations

from project_paths import resolve_data_dir, resolve_reports_dir, resolve_tasks_path


def test_resolve_data_dir_prefers_agent_data(tmp_path):
    (tmp_path / "agent-data").mkdir()
    (tmp_path / "data").mkdir()
    assert resolve_data_dir(tmp_path) == tmp_path / "agent-data"


def test_resolve_data_dir_falls_back_to_legacy_data(tmp_path):
    (tmp_path / "data").mkdir()
    assert resolve_data_dir(tmp_path) == tmp_path / "data"


def test_resolve_tasks_path_uses_existing_legacy_default(tmp_path):
    legacy = tmp_path / "data"
    legacy.mkdir()
    (legacy / "tasks.yaml").write_text("tasks: []\n", encoding="utf-8")
    assert resolve_tasks_path("agent-data/tasks.yaml", base=tmp_path) == legacy / "tasks.yaml"


def test_resolve_tasks_path_keeps_custom_path(tmp_path):
    assert resolve_tasks_path("foo/tasks.yaml", base=tmp_path).as_posix() == "foo/tasks.yaml"


def test_resolve_reports_dir_uses_existing_legacy_default(tmp_path):
    legacy = tmp_path / "data" / "reports"
    legacy.mkdir(parents=True)
    assert resolve_reports_dir("agent-data/reports", base=tmp_path) == legacy
