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

import logging
import shutil
import time
from pathlib import Path
from types import TracebackType

from orchestrator.task import Task

logger = logging.getLogger(__name__)

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
    ):
        self.task = task
        self.repo_path = Path(repo_path).resolve()
        self.keep_on_failure = keep_on_failure
        self._base_dir = self.repo_path / ".agent-workspace"
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
            → workspace/src/auth.py
            → workspace/src/models/user.py
        """
        for rel_path in self.task.target_files:
            src = self.repo_path / rel_path
            if not src.exists():
                logger.warning("target_file 없음 (건너뜀): %s", src)
                continue

            # workspace/src/ 아래에 동일한 상대 경로로 저장
            dest = self.src_dir / rel_path
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            logger.debug("복사: %s → %s", src, dest)

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
        """data/context/ 디렉토리의 문서를 workspace/context/ 에 복사한다.

        tasks.yaml 생성에 쓰인 원본 스펙·요구사항 문서를 에이전트가 참조할 수 있도록 한다.
        에이전트는 프롬프트에 직접 주입되는 게 아니라 파일로 제공되므로,
        필요한 시점에 read_file로 on-demand 참조 → 컨텍스트 낭비 없음.
        """
        context_dir = self.repo_path / "data" / "context"
        if not context_dir.exists():
            return
        dest_dir = self.path / "context"
        dest_dir.mkdir(exist_ok=True)
        for doc in sorted(context_dir.iterdir()):
            if doc.is_file():
                shutil.copy2(doc, dest_dir / doc.name)
        logger.debug("context 문서 복사 완료: %s", [d.name for d in context_dir.iterdir() if d.is_file()])
