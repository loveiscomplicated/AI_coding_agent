"""
orchestrator/pipeline.py — TDD 파이프라인 상태 머신

TDDPipeline 은 단일 Task 를 받아 다음 순서로 실행한다:

  WRITING_TESTS  → TestWriter (ScopedReactLoop + Haiku)
  IMPLEMENTING   → Implementer (ScopedReactLoop + Haiku)
  RUNNING_TESTS  → DockerTestRunner  [실패 시 IMPLEMENTING 으로 회귀, max 3회]
  REVIEWING      → Reviewer (ScopedReactLoop + Haiku)
  COMMITTING     → 호출자(run.py)가 GitWorkflow 실행

사용 예:
    from orchestrator.pipeline import TDDPipeline, PipelineResult
    from orchestrator.workspace import WorkspaceManager

    pipeline = TDDPipeline(agent_llm=haiku_client, test_runner=DockerTestRunner())

    with WorkspaceManager(task, repo_path) as ws:
        result = pipeline.run(task, ws)

    if result.succeeded:
        print(result.review.verdict)
    else:
        print(result.failure_reason)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from agents.roles import TEST_WRITER, IMPLEMENTER, REVIEWER
from agents.scoped_loop import ScopedReactLoop, ScopedResult
from docker.runner import DockerTestRunner, RunResult
from llm.base import StopReason
from orchestrator.task import Task, TaskStatus
from orchestrator.workspace import WorkspaceManager

logger = logging.getLogger(__name__)

MAX_RETRIES = 2


# ── 결과 데이터 클래스 ────────────────────────────────────────────────────────


@dataclass
class ReviewResult:
    """Reviewer 에이전트 출력 파싱 결과."""

    verdict: str   # "APPROVED" | "CHANGES_REQUESTED"
    summary: str
    details: str
    raw: str       # 원문 (PR body 에 그대로 포함)

    @property
    def approved(self) -> bool:
        return self.verdict == "APPROVED"


@dataclass
class PipelineResult:
    """TDDPipeline.run() 의 최종 반환값."""

    task: Task
    succeeded: bool
    failure_reason: str = ""
    test_result: RunResult | None = None
    review: ReviewResult | None = None
    test_files: list[str] = field(default_factory=list)
    impl_files: list[str] = field(default_factory=list)

    @classmethod
    def failed(cls, task: Task, reason: str) -> "PipelineResult":
        task.status = TaskStatus.FAILED
        task.failure_reason = reason
        return cls(task=task, succeeded=False, failure_reason=reason)


# ── 파이프라인 ────────────────────────────────────────────────────────────────


class TDDPipeline:
    """
    단일 태스크를 TDD 흐름으로 실행하는 파이프라인.

    agent_llm       : TestWriter / Reviewer 용 LLM (Haiku 권장)
    implementer_llm : Implementer 전용 LLM. None이면 agent_llm 사용.
                      복잡한 구현 태스크는 Sonnet을 권장한다.
    test_runner     : DockerTestRunner 인스턴스 (주입 가능, 테스트 시 mock 용이)
    max_retries     : Implementer 재시도 최대 횟수
    """

    def __init__(
        self,
        agent_llm,
        implementer_llm=None,
        test_runner: DockerTestRunner | None = None,
        max_retries: int = MAX_RETRIES,
        max_iterations: int = 15,
        reviewer_max_iterations: int = 5,
    ):
        self.agent_llm = agent_llm
        self.implementer_llm = implementer_llm or agent_llm
        self.test_runner = test_runner or DockerTestRunner()
        self.max_retries = max_retries
        self.max_iterations = max_iterations
        self.reviewer_max_iterations = reviewer_max_iterations

    # ── 공개 인터페이스 ───────────────────────────────────────────────────────

    def run(self, task: Task, workspace: WorkspaceManager) -> PipelineResult:
        """
        태스크 전체 파이프라인을 실행한다.
        workspace 는 이미 create() 된 상태여야 한다.
        """
        logger.info("[%s] 파이프라인 시작", task.id)

        # ── Step 1: 테스트 작성 ───────────────────────────────────────────────
        task.status = TaskStatus.WRITING_TESTS
        test_scoped = self._run_test_writer(task, workspace)
        if not test_scoped.succeeded:
            prefix = "[MAX_ITER] " if _is_max_iter(test_scoped) else ""
            return PipelineResult.failed(task, f"{prefix}TestWriter 실패: {test_scoped.answer}")

        test_files = workspace.list_test_files()
        if not test_files:
            return PipelineResult.failed(task, "TestWriter 가 tests/ 에 파일을 생성하지 않았습니다.")
        logger.info("[%s] 테스트 파일 생성: %s", task.id, test_files)

        # ── Step 2: 구현 + 테스트 실행 (재시도 루프) ─────────────────────────
        docker_result: RunResult | None = None
        for attempt in range(self.max_retries):
            task.status = TaskStatus.IMPLEMENTING
            impl_scoped = self._run_implementer(task, workspace)
            if not impl_scoped.succeeded:
                prefix = "[MAX_ITER] " if _is_max_iter(impl_scoped) else ""
                return PipelineResult.failed(task, f"{prefix}Implementer 실패: {impl_scoped.answer}")

            task.status = TaskStatus.RUNNING_TESTS
            docker_result = self.test_runner.run(workspace.path)
            logger.info(
                "[%s] 테스트 실행 (시도 %d/%d): %s",
                task.id, attempt + 1, self.max_retries, docker_result.summary,
            )

            if docker_result.passed:
                task.retry_count = attempt
                break

            task.retry_count = attempt + 1
            task.last_error = docker_result.stdout
            logger.warning("[%s] 테스트 실패, 재시도 %d회", task.id, task.retry_count)

            if attempt == self.max_retries - 1:
                return PipelineResult.failed(
                    task,
                    f"테스트가 {self.max_retries}회 모두 실패했습니다.\n"
                    f"마지막 오류:\n{docker_result.summary}",
                )

        # ── Step 3: 코드 리뷰 ────────────────────────────────────────────────
        task.status = TaskStatus.REVIEWING
        review_scoped = self._run_reviewer(task, workspace, docker_result)
        review = _parse_review(review_scoped.answer)
        logger.info("[%s] 리뷰 결과: %s — %s", task.id, review.verdict, review.summary)

        task.status = TaskStatus.COMMITTING
        return PipelineResult(
            task=task,
            succeeded=True,
            test_result=docker_result,
            review=review,
            test_files=workspace.list_test_files(),
            impl_files=workspace.list_src_files(),
        )

    # ── 개별 에이전트 실행 ────────────────────────────────────────────────────

    def _run_test_writer(self, task: Task, workspace: WorkspaceManager) -> ScopedResult:
        loop = ScopedReactLoop(
            llm=self.agent_llm,
            role=TEST_WRITER,
            workspace_dir=workspace.path,
            max_iterations=self.max_iterations,
        )
        prompt = _build_test_writer_prompt(task, workspace)
        logger.debug("[%s] TestWriter 시작", task.id)
        return loop.run(prompt)

    def _run_implementer(self, task: Task, workspace: WorkspaceManager) -> ScopedResult:
        loop = ScopedReactLoop(
            llm=self.implementer_llm,
            role=IMPLEMENTER,
            workspace_dir=workspace.path,
            max_iterations=self.max_iterations,
        )
        prompt = _build_implementer_prompt(task, workspace)
        logger.debug("[%s] Implementer 시작 (retry=%d)", task.id, task.retry_count)
        return loop.run(prompt)

    def _run_reviewer(
        self,
        task: Task,
        workspace: WorkspaceManager,
        docker_result: RunResult,
    ) -> ScopedResult:
        loop = ScopedReactLoop(
            llm=self.agent_llm,
            role=REVIEWER,
            workspace_dir=workspace.path,
            max_iterations=self.reviewer_max_iterations,
        )
        prompt = _build_reviewer_prompt(task, workspace, docker_result)
        logger.debug("[%s] Reviewer 시작", task.id)
        return loop.run(prompt)


# ── 프롬프트 빌더 ─────────────────────────────────────────────────────────────


def _context_hint(workspace: WorkspaceManager) -> str:
    """context/ 디렉토리가 있으면 참조 안내 문자열을 반환한다."""
    context_dir = workspace.path / "context"
    if not context_dir.exists():
        return ""
    docs = sorted(f.name for f in context_dir.iterdir() if f.is_file())
    if not docs:
        return ""
    doc_list = ", ".join(f"`context/{d}`" for d in docs)
    return f"\n상세 스펙·아키텍처 문서: {doc_list} — 구현 전에 참조하세요.\n"


def _build_test_writer_prompt(task: Task, workspace: WorkspaceManager) -> str:
    structure_hint = ""
    if (workspace.path / "PROJECT_STRUCTURE.md").exists():
        structure_hint = "\n`PROJECT_STRUCTURE.md` 로 전체 코드베이스 구조를 먼저 파악하세요.\n"

    return f"""## 태스크

**{task.title}**

{task.description}

## 수락 기준

{task.acceptance_criteria_text()}

## 워크스페이스 경로

`{workspace.path}`
{structure_hint}{_context_hint(workspace)}
`src/` 에 있는 기존 코드를 먼저 확인하고,
`tests/` 에 이 태스크를 검증하는 pytest 테스트를 작성하세요.
구현이 없으므로 테스트는 실행 시 실패해야 합니다 (Red 단계).
"""


def _build_implementer_prompt(task: Task, workspace: WorkspaceManager) -> str:
    structure_hint = ""
    if (workspace.path / "PROJECT_STRUCTURE.md").exists():
        structure_hint = "\n`PROJECT_STRUCTURE.md` 로 전체 코드베이스 구조를 먼저 파악하고, 재사용 가능한 모듈이 있는지 확인하세요.\n"

    base = f"""## 태스크

**{task.title}**

{task.description}

## 수락 기준

{task.acceptance_criteria_text()}

## 워크스페이스 경로

`{workspace.path}`
{structure_hint}{_context_hint(workspace)}
`tests/` 에 있는 테스트를 먼저 읽고,
`src/` 에 테스트를 **모두** 통과하는 구현을 작성하세요.
"""
    if task.last_error:
        # 긴 오류 로그는 앞 2000자만 포함 (컨텍스트 절약)
        truncated = task.last_error[:2000]
        if len(task.last_error) > 2000:
            truncated += "\n... (이하 생략)"
        base += f"""
## 이전 시도 실패 로그 (시도 {task.retry_count}회차)

```
{truncated}
```

위 오류를 분석해서 원인을 파악한 뒤 수정하세요.
같은 방식으로 재시도하지 마세요.
"""
    return base


def _build_reviewer_prompt(
    task: Task, workspace: WorkspaceManager, docker_result: RunResult
) -> str:
    return f"""## 검토 요청

**{task.title}**

## 수락 기준

{task.acceptance_criteria_text()}

## 테스트 실행 결과

{docker_result.summary}

## 워크스페이스 경로

`{workspace.path}`

`src/` 와 `tests/` 를 읽고 코드를 검토한 뒤,
지시받은 형식대로 VERDICT 를 반환하세요.
"""


# ── 헬퍼 ─────────────────────────────────────────────────────────────────────


def _is_max_iter(scoped: ScopedResult) -> bool:
    """ScopedResult 가 MAX_ITER 로 종료되었는지 확인한다."""
    return (
        scoped.loop_result is not None
        and scoped.loop_result.stop_reason == StopReason.MAX_ITER
    )


# ── 리뷰 파싱 ─────────────────────────────────────────────────────────────────


def _parse_review(raw: str) -> ReviewResult:
    """
    Reviewer 에이전트 출력에서 VERDICT / SUMMARY / DETAILS 를 추출한다.

    기대 형식:
        VERDICT: APPROVED
        SUMMARY: 전반적으로 잘 구현되었음
        DETAILS:
        ...

    파싱 실패 시 CHANGES_REQUESTED 로 보수적 기본값을 사용한다.
    """
    verdict = "CHANGES_REQUESTED"
    summary = ""
    details_lines: list[str] = []
    in_details = False

    for line in raw.splitlines():
        stripped = line.strip()

        if stripped.upper().startswith("VERDICT:"):
            value = stripped.split(":", 1)[1].strip().upper()
            if value in ("APPROVED", "CHANGES_REQUESTED"):
                verdict = value

        elif stripped.upper().startswith("SUMMARY:"):
            summary = stripped.split(":", 1)[1].strip()

        elif stripped.upper().startswith("DETAILS:"):
            in_details = True
            # DETAILS: 와 같은 줄에 내용이 있을 수도 있음
            inline = stripped.split(":", 1)[1].strip()
            if inline:
                details_lines.append(inline)

        elif in_details:
            details_lines.append(line)

    # VERDICT 를 못 찾으면 텍스트에서 키워드로 추론
    if not any(kw in raw.upper() for kw in ("APPROVED", "CHANGES_REQUESTED")):
        logger.warning("Reviewer 출력에서 VERDICT 를 찾지 못했습니다.")

    return ReviewResult(
        verdict=verdict,
        summary=summary,
        details="\n".join(details_lines).strip(),
        raw=raw,
    )
