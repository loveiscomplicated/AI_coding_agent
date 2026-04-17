"""
orchestrator/workspace.py — 태스크 워크스페이스 관리

WorkspaceManager 는 태스크 실행을 위한 격리된 작업 디렉토리를 생성하고
관리한다. 에이전트는 이 workspace 안에서만 파일을 읽고 쓴다.

워크스페이스 구조:
    {repo_path}/.agent-workspace/{task_id}_{timestamp}/
        src/        ← task.target_files 를 repo에서 복사해 옴 (경로 유지)
        tests/      ← TestWriter 에이전트가 여기에 테스트 파일을 생성
        (requirements.txt) ← repo 루트에 있으면 복사 (DockerTestRunner용)

.agent-workspace/ 는 .gitignore 에 등록되어 git 에 노출되지 않는다.

사용 예:
    with WorkspaceManager(task, repo_path="/path/to/repo") as ws:
        print(ws.path)       # /path/to/repo/.agent-workspace/task-001_1234567890
        print(ws.src_dir)    # .../src
        print(ws.tests_dir)  # .../tests
        ws.list_files()      # workspace 안의 모든 파일 목록
    # with 블록 종료 시 성공이면 정리, 실패면 보존 (keep_on_failure=True)
"""

from __future__ import annotations

import ast
import logging
import shutil
import subprocess
import time
from pathlib import Path
from types import TracebackType

from orchestrator.task import Task, TaskStatus

logger = logging.getLogger(__name__)


def strip_src_prefix(rel_path: str) -> str:
    """target_file 경로의 선행 'src/' 한 단계를 제거한다.

    워크스페이스의 ``src_dir`` 자체가 이미 레포의 ``src/`` 코드 루트를
    대표하기 때문에, ``src/foo.py`` 같은 target_file 은
    ``src_dir/foo.py`` 에 놓여야 한다 (``src_dir/src/foo.py`` 가 아니라).

    선행 prefix 가 없는 경로(예: Kotlin 의 ``app/src/main/...``)는 그대로 반환.
    """
    prefix = "src/"
    return rel_path[len(prefix):] if rel_path.startswith(prefix) else rel_path

class WorkspaceManager:
    """
    태스크 실행용 격리 워크스페이스.

    컨텍스트 매니저로 사용하면 종료 시 자동으로 정리 여부를 결정한다:
    - 정상 종료(예외 없음): 항상 정리
    - 예외 발생:
        - keep_on_failure=True  (기본값): 보존 — 디버깅에 사용
        - keep_on_failure=False: 정리
    """

    def __init__(
        self,
        task: Task,
        repo_path: str | Path,
        keep_on_failure: bool = True,
        base_dir: str | Path | None = None,
    ):
        self.task = task
        self.repo_path = Path(repo_path).resolve()
        self.keep_on_failure = keep_on_failure
        self._base_dir = Path(base_dir) if base_dir else self.repo_path / ".agent-workspace"
        self._path: Path | None = None

    # ── 컨텍스트 매니저 ───────────────────────────────────────────────────────

    def __enter__(self) -> "WorkspaceManager":
        self.create()
        return self

    def __exit__(
        self,
        exc_type: type | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        if exc_type is None:
            self.cleanup()
        elif self.keep_on_failure:
            logger.info("실패로 workspace 보존 (디버깅용): %s", self._path)
        else:
            self.cleanup()

    # ── 공개 인터페이스 ───────────────────────────────────────────────────────

    def create(self) -> "WorkspaceManager":
        """
        워크스페이스 디렉토리를 생성하고 target_files 를 복사한다.
        이미 create() 를 호출했으면 no-op.
        """
        if self._path is not None:
            return self

        timestamp = int(time.time())
        self._path = self._base_dir / f"{self.task.id}_{timestamp}"
        self._path.mkdir(parents=True, exist_ok=True)
        self.src_dir.mkdir(exist_ok=True)
        self.tests_dir.mkdir(exist_ok=True)
        (self._path / "context").mkdir(exist_ok=True)

        self._copy_target_files()
        self._copy_requirements()
        self._copy_project_structure()
        self._copy_context_docs()
        self._ensure_python_init()

        logger.info("workspace 생성: %s", self._path)
        return self

    def cleanup(self) -> None:
        """워크스페이스 디렉토리를 삭제한다."""
        if self._path and self._path.exists():
            shutil.rmtree(self._path)
            logger.info("workspace 정리: %s", self._path)
        self._path = None

    def list_files(self) -> list[str]:
        """
        workspace 안의 모든 파일 경로를 workspace 루트 기준 상대 경로로 반환.
        에이전트 실행 후 생성된 파일 목록 확인에 사용.
        """
        if self._path is None:
            return []
        return [
            str(p.relative_to(self._path))
            for p in sorted(self._path.rglob("*"))
            if p.is_file()
        ]

    def list_test_files(self) -> list[str]:
        """tests/ 디렉토리 안의 파일만 반환."""
        return [f for f in self.list_files() if f.startswith("tests/")]

    def list_src_files(self) -> list[str]:
        """src/ 디렉토리 안의 파일만 반환."""
        return [f for f in self.list_files() if f.startswith("src/")]

    # ── 프로퍼티 ─────────────────────────────────────────────────────────────

    @property
    def path(self) -> Path:
        if self._path is None:
            raise RuntimeError("create() 를 먼저 호출하세요.")
        return self._path

    @property
    def src_dir(self) -> Path:
        return self.path / "src"

    @property
    def tests_dir(self) -> Path:
        return self.path / "tests"

    # ── 내부 헬퍼 ─────────────────────────────────────────────────────────────

    def _copy_target_files(self) -> None:
        """
        task.target_files 를 repo_path 기준 상대 경로로 workspace/src/ 에 복사.

        예) target_files = ["src/auth.py", "src/models/user.py"]
            → workspace/src/auth.py            (선행 'src/' 한 단계 제거)
            → workspace/src/models/user.py

            target_files = ["app/src/main/foo.kt"]
            → workspace/src/app/src/main/foo.kt   (선행 'src/' 가 없으면 그대로)

        선행 'src/' 한 단계를 떼어내는 이유: workspace 의 ``src_dir`` 자체가
        이미 레포 ``src/`` 코드 루트를 대표하므로 한 번 더 붙이면 ``src/src/`` 가
        되어 import 경로·프롬프트 모두에서 혼란을 일으킨다.

        repo 에 파일이 아직 없으면 (신규 생성 태스크) **빈 파일을 선주입** 한다.
        이유: Implementer 가 탐색(list/read)으로 시간 낭비하지 않고 즉시
        write/edit 로 접근하도록 유도하기 위함. 빈 파일 그대로 방치되면
        이후 엄격 가드(pipeline) 가 `[TARGET_MISSING]` 으로 차단한다.
        """
        for rel_path in self.task.target_files:
            src = self.repo_path / rel_path
            dest = self.src_dir / strip_src_prefix(rel_path)
            dest.parent.mkdir(parents=True, exist_ok=True)

            if src.exists():
                shutil.copy2(src, dest)
                logger.debug("복사: %s → %s", src, dest)
            else:
                # 신규 파일 → 빈 스켈레톤 생성
                dest.touch()
                logger.info("target_file 스켈레톤 생성(빈 파일): %s", dest)

    def missing_or_empty_target_files(self) -> list[str]:
        """
        task.target_files 중 workspace/src/ 에 존재하지 않거나 빈 파일인 경로를
        repo 기준 상대 경로로 반환한다. Implementer 완료 후 엄격 가드용.

        반환값이 비어 있으면 모든 target_file 이 '실제 내용을 가진' 상태.
        """
        missing: list[str] = []
        for rel_path in self.task.target_files:
            dest = self.src_dir / strip_src_prefix(rel_path)
            if not dest.exists():
                missing.append(rel_path)
                continue
            try:
                if dest.stat().st_size == 0:
                    missing.append(rel_path)
            except OSError:
                missing.append(rel_path)
        return missing

    def _copy_requirements(self) -> None:
        """repo 루트의 requirements.txt 가 있으면 workspace 루트에 복사."""
        req = self.repo_path / "requirements.txt"
        if req.exists():
            shutil.copy2(req, self.path / "requirements.txt")

    def _copy_project_structure(self) -> None:
        """PROJECT_STRUCTURE.md 가 있으면 workspace 루트에 복사한다.

        에이전트가 태스크 시작 시 코드베이스 전체 구조를 즉시 파악할 수 있도록 한다.
        파일이 없으면 조용히 건너뜀 (첫 태스크에서는 아직 생성 전일 수 있음).
        """
        structure_doc = self.repo_path / "PROJECT_STRUCTURE.md"
        if structure_doc.exists():
            shutil.copy2(structure_doc, self.path / "PROJECT_STRUCTURE.md")
            logger.debug("PROJECT_STRUCTURE.md 복사 완료")

    def _ensure_python_init(self) -> None:
        """Python 프로젝트이면 src/__init__.py 를 보장한다.

        target_files 에 .py 파일이 하나라도 있으면 Python 프로젝트로 판단하고
        src/__init__.py 가 없을 경우 빈 파일을 생성한다.
        이 파일이 없으면 `from src.xxx import ...` 패턴이 동작하지 않는다.
        """
        py_exts = {'.py'}
        has_python = any(
            Path(f).suffix.lower() in py_exts
            for f in self.task.target_files
        )
        if not has_python:
            return
        init_file = self.src_dir / "__init__.py"
        if not init_file.exists():
            init_file.touch()
            logger.debug("src/__init__.py 생성 (Python 패키지 인식용)")

    def _copy_context_docs(self) -> None:
        """agent-data/context/ 디렉토리의 문서를 workspace/context/ 에 복사한다.

        tasks.yaml 생성에 쓰인 원본 스펙·요구사항 문서를 에이전트가 참조할 수 있도록 한다.
        에이전트는 프롬프트에 직접 주입되는 게 아니라 파일로 제공되므로,
        필요한 시점에 read_file로 on-demand 참조 → 컨텍스트 낭비 없음.
        """
        context_dir = self.repo_path / "agent-data" / "context"
        if not context_dir.exists():
            return
        dest_dir = self.path / "context"
        dest_dir.mkdir(exist_ok=True)
        for doc in sorted(context_dir.iterdir()):
            if doc.is_file():
                shutil.copy2(doc, dest_dir / doc.name)
        logger.debug("context 문서 복사 완료: %s", [d.name for d in context_dir.iterdir() if d.is_file()])

    def inject_dependency_context(self, dep_tasks: list[Task]) -> None:
        """
        완료된 선행 태스크의 산출물을 workspace에 주입한다.

        1) 선행 태스크의 git 브랜치에서 target_files를 읽어 workspace/src/ 에 복사
           → 후속 태스크가 `from src.xxx import YYY` 로 자연스럽게 import 가능
        2) context/dependency_artifacts.md 에 파일 목록 + 주요 심볼(클래스, 함수 시그니처) 요약
           → 에이전트가 무엇을 import할 수 있는지 즉시 파악 가능
        """
        if not dep_tasks:
            return

        done_deps = [t for t in dep_tasks if t.status == TaskStatus.DONE]
        if not done_deps:
            return

        artifacts_lines: list[str] = [
            "# 선행 태스크 산출물\n",
            "이 파일은 depends_on 으로 연결된 선행 태스크의 완료 산출물을 요약합니다.\n"
            "해당 파일들은 이미 `src/` 디렉토리에 복사되어 있으므로 바로 import 할 수 있습니다.\n",
        ]

        for dep in done_deps:
            branch = dep.branch_name  # agent/{task_id}
            copied_files: list[str] = []
            file_summaries: list[str] = []

            for rel_path in dep.target_files:
                content = self._read_from_branch(branch, rel_path)
                if content is None:
                    continue

                # workspace/src/ 에 복사 (선행 'src/' 한 단계 제거)
                dest = self.src_dir / strip_src_prefix(rel_path)
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(content, encoding="utf-8")
                copied_files.append(rel_path)

                # __init__.py 보장 (중간 패키지)
                self._ensure_init_files(dest)

                # Python 파일이면 심볼 요약 추출
                if rel_path.endswith(".py"):
                    summary = _extract_python_signatures(content, rel_path)
                    if summary:
                        file_summaries.append(summary)

            # Fallback: target_files 경로가 브랜치에 없는 경우
            # 브랜치에서 실제 추가/수정된 소스 파일을 찾아서 주입
            if not copied_files:
                actual_files = self._list_branch_added_files(branch, dep.target_files)
                for rel_path in actual_files:
                    content = self._read_from_branch(branch, rel_path)
                    if content is None:
                        continue
                    dest = self.src_dir / strip_src_prefix(rel_path)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_text(content, encoding="utf-8")
                    copied_files.append(rel_path)
                    self._ensure_init_files(dest)
                    if rel_path.endswith(".py"):
                        summary = _extract_python_signatures(content, rel_path)
                        if summary:
                            file_summaries.append(summary)
                if copied_files:
                    logger.info(
                        "[%s] 선행 태스크 %s: target_files 경로 불일치 → "
                        "브랜치 실제 파일 %d개 fallback 주입",
                        self.task.id, dep.id, len(copied_files),
                    )

            if copied_files:
                artifacts_lines.append(f"\n## {dep.id}: {dep.title}\n")
                artifacts_lines.append(f"**파일**: {', '.join(copied_files)}\n")
                if file_summaries:
                    artifacts_lines.append("\n".join(file_summaries))
                logger.info(
                    "[%s] 선행 태스크 %s 산출물 %d개 파일 주입",
                    self.task.id, dep.id, len(copied_files),
                )

        # context/ 에 요약 문서 작성
        if any(t.target_files for t in done_deps):
            context_dir = self.path / "context"
            context_dir.mkdir(exist_ok=True)
            (context_dir / "dependency_artifacts.md").write_text(
                "\n".join(artifacts_lines), encoding="utf-8",
            )

    def _list_branch_added_files(
        self, branch: str, target_files: list[str],
    ) -> list[str]:
        """브랜치에서 실제로 추가/수정된 소스 파일 목록을 반환한다 (tests/ 제외).

        target_files 경로가 브랜치에 존재하지 않을 때 일반 안전장치로 사용.
        """
        try:
            # 브랜치의 마지막 커밋에서 추가/수정된 파일 확인
            result = subprocess.run(
                ["git", "diff", "--name-only", "--diff-filter=AM",
                 f"{branch}~1", branch],
                capture_output=True, text=True, cwd=self.repo_path, timeout=10,
            )
            if result.returncode != 0:
                return []
        except Exception as e:
            logger.debug("git diff 실패 (%s): %s", branch, e)
            return []

        files = []
        for line in result.stdout.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            # tests/ 는 제외 (테스트 파일은 후속 태스크가 직접 생성)
            if line.startswith("tests/") or line.startswith("test_"):
                continue
            # 소스 파일만 (.py, .kt, .java, .js, .ts 등)
            if any(line.endswith(ext) for ext in
                   (".py", ".kt", ".java", ".js", ".ts", ".go", ".rb")):
                files.append(line)
        return files

    def _read_from_branch(self, branch: str, rel_path: str) -> str | None:
        """git show 로 특정 브랜치의 파일 내용을 읽는다. 실패 시 None."""
        try:
            result = subprocess.run(
                ["git", "show", f"{branch}:{rel_path}"],
                capture_output=True, text=True, cwd=self.repo_path, timeout=10,
            )
            if result.returncode == 0:
                return result.stdout
        except Exception as e:
            logger.debug("git show 실패 (%s:%s): %s", branch, rel_path, e)
        return None

    def _ensure_init_files(self, file_path: Path) -> None:
        """파일 경로의 중간 디렉토리에 __init__.py 가 없으면 생성한다."""
        if not file_path.name.endswith(".py"):
            return
        current = file_path.parent
        while current != self.src_dir and current.is_relative_to(self.src_dir):
            init = current / "__init__.py"
            if not init.exists():
                init.touch()
            current = current.parent


def _extract_python_signatures(source: str, rel_path: str) -> str:
    """Python 소스에서 클래스/함수 시그니처를 추출해 마크다운 요약을 반환한다."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return ""

    lines: list[str] = [f"\n### `{rel_path}`\n```python"]
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ClassDef):
            bases = ", ".join(ast.unparse(b) for b in node.bases) if node.bases else ""
            lines.append(f"class {node.name}({bases}):")
            for item in node.body:
                if isinstance(item, ast.FunctionDef):
                    args = ast.unparse(item.args)
                    ret = f" -> {ast.unparse(item.returns)}" if item.returns else ""
                    lines.append(f"    def {item.name}({args}){ret}: ...")
        elif isinstance(node, ast.FunctionDef):
            args = ast.unparse(node.args)
            ret = f" -> {ast.unparse(node.returns)}" if node.returns else ""
            lines.append(f"def {node.name}({args}){ret}: ...")
    lines.append("```")
    return "\n".join(lines) if len(lines) > 3 else ""  # 시그니처가 없으면 빈 문자열
