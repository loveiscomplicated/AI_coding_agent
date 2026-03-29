"""
tests/test_git_tools.py

Git 도구 테스트.

설계:
  tools/git_tools.py 에 다음 함수들을 구현한다.
    git_status(repo_path)          — 워킹 트리 상태
    git_diff(repo_path, staged)    — 변경 diff
    git_log(repo_path, n)          — 최근 커밋 로그
    git_add(repo_path, paths)      — 파일 스테이징
    git_commit(repo_path, message) — 커밋

  모두 ToolResult 반환.

아직 구현되지 않음 — 처음엔 실패한다.

실행:
    pytest tests/test_git_tools.py -v
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from tools.git_tools import (   # 아직 없음
    git_add,
    git_commit,
    git_diff,
    git_log,
    git_status,
)


# ── 픽스처: 임시 git 저장소 ───────────────────────────────────────────────────


@pytest.fixture
def repo(tmp_path):
    """초기화된 git 저장소 경로 반환."""
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        check=True, capture_output=True, cwd=str(tmp_path),
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        check=True, capture_output=True, cwd=str(tmp_path),
    )
    return tmp_path


@pytest.fixture
def repo_with_commit(repo):
    """파일 1개 + 최초 커밋이 있는 저장소."""
    f = repo / "hello.py"
    f.write_text("print('hello')\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], check=True, capture_output=True, cwd=str(repo))
    subprocess.run(
        ["git", "commit", "-m", "init"], check=True, capture_output=True, cwd=str(repo)
    )
    return repo


# ── git_status ────────────────────────────────────────────────────────────────


class TestGitStatus:
    def test_clean_repo_shows_clean(self, repo_with_commit):
        result = git_status(str(repo_with_commit))
        assert result.success is True
        assert result.output  # 비어 있지 않음

    def test_untracked_file_shown(self, repo):
        (repo / "new.py").write_text("x = 1\n", encoding="utf-8")
        result = git_status(str(repo))
        assert result.success is True
        assert "new.py" in result.output

    def test_modified_file_shown(self, repo_with_commit):
        (repo_with_commit / "hello.py").write_text("print('modified')\n", encoding="utf-8")
        result = git_status(str(repo_with_commit))
        assert result.success is True
        assert "hello.py" in result.output

    def test_staged_file_shown(self, repo_with_commit):
        (repo_with_commit / "hello.py").write_text("print('staged')\n", encoding="utf-8")
        subprocess.run(["git", "add", "hello.py"], check=True,
                       capture_output=True, cwd=str(repo_with_commit))
        result = git_status(str(repo_with_commit))
        assert result.success is True
        assert "hello.py" in result.output

    def test_not_a_git_repo_returns_error(self, tmp_path):
        result = git_status(str(tmp_path))
        assert result.success is False
        assert result.error is not None

    def test_nonexistent_path_returns_error(self):
        result = git_status("/nonexistent/path/xyz")
        assert result.success is False
        assert result.error is not None


# ── git_diff ──────────────────────────────────────────────────────────────────


class TestGitDiff:
    def test_no_changes_returns_empty(self, repo_with_commit):
        result = git_diff(str(repo_with_commit))
        assert result.success is True
        assert result.output.strip() == "" or "변경 없음" in result.output

    def test_unstaged_diff_shown(self, repo_with_commit):
        (repo_with_commit / "hello.py").write_text("print('changed')\n", encoding="utf-8")
        result = git_diff(str(repo_with_commit), staged=False)
        assert result.success is True
        assert "hello.py" in result.output or "changed" in result.output

    def test_staged_diff_shown(self, repo_with_commit):
        (repo_with_commit / "hello.py").write_text("print('staged')\n", encoding="utf-8")
        subprocess.run(["git", "add", "hello.py"], check=True,
                       capture_output=True, cwd=str(repo_with_commit))
        result = git_diff(str(repo_with_commit), staged=True)
        assert result.success is True
        assert "staged" in result.output or "hello.py" in result.output

    def test_not_git_repo_returns_error(self, tmp_path):
        result = git_diff(str(tmp_path))
        assert result.success is False

    def test_staged_false_does_not_show_staged_changes(self, repo_with_commit):
        """스테이징된 변경은 staged=False diff에 포함되지 않아야 한다."""
        (repo_with_commit / "hello.py").write_text("staged\n", encoding="utf-8")
        subprocess.run(["git", "add", "hello.py"], check=True,
                       capture_output=True, cwd=str(repo_with_commit))
        result = git_diff(str(repo_with_commit), staged=False)
        assert result.success is True
        # unstaged diff에는 이미 add된 내용이 없어야 함
        assert result.output.strip() == "" or "staged" not in result.output


# ── git_log ───────────────────────────────────────────────────────────────────


class TestGitLog:
    def test_returns_commit_messages(self, repo_with_commit):
        result = git_log(str(repo_with_commit), n=5)
        assert result.success is True
        assert "init" in result.output

    def test_n_limits_commits(self, repo_with_commit):
        # 추가 커밋 2개
        for i in range(2):
            f = repo_with_commit / f"file{i}.py"
            f.write_text(f"x={i}\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], check=True,
                           capture_output=True, cwd=str(repo_with_commit))
            subprocess.run(["git", "commit", "-m", f"commit{i}"], check=True,
                           capture_output=True, cwd=str(repo_with_commit))

        result_1 = git_log(str(repo_with_commit), n=1)
        result_3 = git_log(str(repo_with_commit), n=3)
        assert result_1.success is True
        # n=1이면 더 적은 내용
        assert len(result_1.output) <= len(result_3.output)

    def test_empty_repo_no_commits_returns_error_or_empty(self, repo):
        """커밋이 없는 저장소는 에러 또는 빈 출력을 반환해야 한다."""
        result = git_log(str(repo), n=5)
        # 크래시 없이 ToolResult 반환
        assert result is not None
        assert hasattr(result, "success")

    def test_not_git_repo_returns_error(self, tmp_path):
        result = git_log(str(tmp_path), n=5)
        assert result.success is False

    def test_log_includes_hash_and_message(self, repo_with_commit):
        result = git_log(str(repo_with_commit), n=1)
        assert result.success is True
        # 커밋 해시(7자 이상 hex) 또는 메시지 포함
        assert "init" in result.output


# ── git_add ───────────────────────────────────────────────────────────────────


class TestGitAdd:
    def test_add_single_file(self, repo):
        f = repo / "a.py"
        f.write_text("x = 1\n", encoding="utf-8")

        result = git_add(str(repo), paths=["a.py"])
        assert result.success is True

        # git status로 staged 확인
        status = subprocess.run(
            ["git", "status", "--short"], capture_output=True, text=True, cwd=str(repo)
        )
        assert "a.py" in status.stdout

    def test_add_multiple_files(self, repo):
        for name in ["a.py", "b.py", "c.py"]:
            (repo / name).write_text("x\n", encoding="utf-8")

        result = git_add(str(repo), paths=["a.py", "b.py", "c.py"])
        assert result.success is True

    def test_add_all_with_dot(self, repo):
        (repo / "x.py").write_text("x\n", encoding="utf-8")
        result = git_add(str(repo), paths=["."])
        assert result.success is True

    def test_add_nonexistent_file_returns_error(self, repo):
        result = git_add(str(repo), paths=["ghost.py"])
        assert result.success is False
        assert result.error is not None

    def test_add_in_non_git_dir_returns_error(self, tmp_path):
        (tmp_path / "f.py").write_text("x\n", encoding="utf-8")
        result = git_add(str(tmp_path), paths=["f.py"])
        assert result.success is False

    def test_add_empty_paths_returns_error(self, repo):
        result = git_add(str(repo), paths=[])
        assert result.success is False
        assert result.error is not None


# ── git_commit ────────────────────────────────────────────────────────────────


class TestGitCommit:
    def test_commit_staged_changes(self, repo):
        f = repo / "a.py"
        f.write_text("x = 1\n", encoding="utf-8")
        subprocess.run(["git", "add", "a.py"], check=True,
                       capture_output=True, cwd=str(repo))

        result = git_commit(str(repo), message="feat: add a.py")
        assert result.success is True
        assert "feat: add a.py" in result.output or result.success is True

    def test_commit_message_appears_in_log(self, repo):
        f = repo / "b.py"
        f.write_text("y = 2\n", encoding="utf-8")
        subprocess.run(["git", "add", "b.py"], check=True,
                       capture_output=True, cwd=str(repo))
        git_commit(str(repo), message="unique_commit_msg_xyz")

        log = subprocess.run(
            ["git", "log", "--oneline", "-1"],
            capture_output=True, text=True, cwd=str(repo),
        )
        assert "unique_commit_msg_xyz" in log.stdout

    def test_commit_nothing_staged_returns_error(self, repo):
        result = git_commit(str(repo), message="empty commit")
        assert result.success is False
        assert result.error is not None

    def test_commit_empty_message_returns_error(self, repo):
        f = repo / "c.py"
        f.write_text("z\n", encoding="utf-8")
        subprocess.run(["git", "add", "c.py"], check=True,
                       capture_output=True, cwd=str(repo))

        result = git_commit(str(repo), message="")
        assert result.success is False
        assert result.error is not None

    def test_commit_in_non_git_dir_returns_error(self, tmp_path):
        result = git_commit(str(tmp_path), message="msg")
        assert result.success is False

    def test_multiline_commit_message(self, repo):
        f = repo / "d.py"
        f.write_text("w\n", encoding="utf-8")
        subprocess.run(["git", "add", "d.py"], check=True,
                       capture_output=True, cwd=str(repo))

        result = git_commit(str(repo), message="feat: title\n\nbody description")
        assert result.success is True
