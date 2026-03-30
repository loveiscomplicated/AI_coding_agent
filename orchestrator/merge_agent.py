"""
orchestrator/merge_agent.py — LLM 기반 머지 충돌 자동 해결

흐름:
  1. git checkout {base_branch}
  2. git merge {branch} --no-edit
  3. 충돌 발생 시 → 충돌 파일 목록 추출
  4. 각 파일을 LLM에 보내 해결된 내용을 반환받아 덮어쓰기
  5. git add + git commit --no-edit

ScopedReactLoop 없이 단순 LLM 호출(1회/파일)로 동작한다.
도구 호출이 필요 없는 단순 텍스트 변환 작업이기 때문이다.

사용 예:
    from llm import LLMConfig, create_client
    from orchestrator.merge_agent import MergeAgent

    haiku = create_client("claude", LLMConfig(model="claude-haiku-4-5"))
    agent = MergeAgent(llm=haiku, repo_path=".")
    result = agent.merge_branch("agent/task-002", base_branch="dev")
    if result.success:
        print(f"머지 완료 — 해결한 충돌 {result.conflicts_resolved}개")
"""

from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from llm.base import BaseLLMClient, Message

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
당신은 Git 머지 충돌을 해결하는 전문가입니다.

충돌 마커가 포함된 파일을 받으면 두 버전을 지능적으로 합쳐서 해결하세요.

마커 의미:
  <<<<<<< HEAD       현재 브랜치(보통 dev)의 내용
  =======            구분선
  >>>>>>> <branch>   머지하려는 브랜치의 내용

해결 원칙:
- conftest.py, __init__.py 등 공유 설정 파일은 양쪽 내용을 모두 포함하세요
- 중복되는 import, fixture, 정의는 한 번만 포함하세요
- 서로 다른 구현이 충돌하면 두 버전의 의도를 파악해 올바르게 합치세요
- 충돌 마커(<<<, ===, >>>)는 완전히 제거하세요
- 해결된 파일 내용만 반환하세요. 설명이나 마크다운 코드블록 없이.
"""


@dataclass
class MergeResult:
    success: bool
    branch: str
    conflicts_resolved: int = 0
    error: str = ""


class MergeAgent:
    """
    git merge 충돌을 LLM으로 자동 해결한다.

    Args:
        llm:       충돌 해결에 쓸 LLM 클라이언트 (Haiku 권장)
        repo_path: 대상 git 저장소 절대 경로
    """

    def __init__(self, llm: BaseLLMClient, repo_path: str | Path):
        self.llm = llm
        self.repo_path = Path(repo_path).resolve()

    # ── 공개 인터페이스 ───────────────────────────────────────────────────────

    def merge_branch(self, branch: str, base_branch: str = "dev") -> MergeResult:
        """
        branch 를 base_branch 에 머지한다. 충돌 발생 시 LLM 으로 자동 해결.

        호출 전 현재 브랜치가 base_branch 여야 한다.

        Returns:
            MergeResult — success=True 면 머지(+푸시 전) 완료.
        """
        logger.info("[MergeAgent] %s → %s 머지 시작", branch, base_branch)

        # 현재 브랜치 확인
        current = self._git(["rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip()
        if current != base_branch:
            return MergeResult(
                success=False,
                branch=branch,
                error=f"현재 브랜치가 {base_branch!r}가 아닙니다 (현재: {current!r}). "
                      f"checkout 후 호출하세요.",
            )

        # git merge 시도
        merge_result = self._git(["merge", branch, "--no-edit"])

        if merge_result.returncode == 0:
            logger.info("[MergeAgent] %s 충돌 없이 머지 완료", branch)
            return MergeResult(success=True, branch=branch, conflicts_resolved=0)

        # 충돌 파일 추출
        conflicted = self._get_conflicted_files()
        if not conflicted:
            self._git(["merge", "--abort"])
            return MergeResult(
                success=False,
                branch=branch,
                error=f"머지 실패 (충돌 외 오류):\n{merge_result.stderr.strip()}",
            )

        logger.info(
            "[MergeAgent] 충돌 %d개 발견: %s",
            len(conflicted),
            [f.name for f in conflicted],
        )

        # 각 파일 LLM 으로 해결
        for file_path in conflicted:
            try:
                self._resolve_file(file_path)
                self._git(["add", str(file_path.relative_to(self.repo_path))])
                logger.info("[MergeAgent] 충돌 해결 완료: %s", file_path.name)
            except Exception as exc:
                self._git(["merge", "--abort"])
                return MergeResult(
                    success=False,
                    branch=branch,
                    error=f"{file_path.name} 충돌 해결 실패: {exc}",
                )

        # 머지 커밋 (에디터 없이)
        commit = subprocess.run(
            ["git", "commit", "--no-edit"],
            capture_output=True,
            text=True,
            cwd=str(self.repo_path),
            env={**os.environ, "GIT_EDITOR": "true"},
        )
        if commit.returncode != 0:
            return MergeResult(
                success=False,
                branch=branch,
                error=f"머지 커밋 실패:\n{commit.stderr.strip()}",
            )

        logger.info(
            "[MergeAgent] %s 머지 완료 — 충돌 %d개 자동 해결",
            branch,
            len(conflicted),
        )
        return MergeResult(
            success=True,
            branch=branch,
            conflicts_resolved=len(conflicted),
        )

    # ── 내부 헬퍼 ─────────────────────────────────────────────────────────────

    def _get_conflicted_files(self) -> list[Path]:
        """충돌(unmerged) 상태인 파일 목록을 반환한다."""
        result = self._git(["diff", "--name-only", "--diff-filter=U"])
        paths = []
        for line in result.stdout.splitlines():
            line = line.strip()
            if line:
                paths.append(self.repo_path / line)
        return paths

    def _resolve_file(self, file_path: Path) -> None:
        """
        충돌 마커가 있는 파일을 LLM 으로 해결하고 덮어쓴다.

        LLM 호출은 1회. 도구 루프 없음.
        시스템 프롬프트는 클라이언트 config 에 없으므로 user 메시지에 인라인으로 포함한다.
        """
        content = file_path.read_text(encoding="utf-8")

        messages = [
            Message(
                role="user",
                content=(
                    f"{_SYSTEM_PROMPT}\n\n---\n\n"
                    f"파일명: {file_path.name}\n\n"
                    f"다음 충돌을 해결하세요:\n\n{content}"
                ),
            )
        ]

        response = self.llm.chat(messages)
        resolved = response.content.strip()

        # LLM 이 코드블록으로 감쌀 경우 제거
        if resolved.startswith("```"):
            lines = resolved.splitlines()
            # 첫 줄(```python 등)과 마지막 줄(```) 제거
            inner = lines[1:]
            if inner and inner[-1].strip() == "```":
                inner = inner[:-1]
            resolved = "\n".join(inner)

        file_path.write_text(resolved, encoding="utf-8")

    def _git(self, args: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git"] + args,
            capture_output=True,
            text=True,
            cwd=str(self.repo_path),
        )
