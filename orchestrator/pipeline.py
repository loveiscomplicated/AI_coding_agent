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

import ast
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from agents.roles import TEST_WRITER, IMPLEMENTER, REVIEWER
from agents.scoped_loop import ScopedReactLoop, ScopedResult
from docker.runner import DockerTestRunner, RunResult, _detect_runtime
from llm.base import Message, StopReason
from orchestrator.task import Task, TaskStatus
from orchestrator.workspace import WorkspaceManager
from tools.hotline_tools import register_workspace_context_dir, unregister_workspace_context_dir

logger = logging.getLogger(__name__)

MAX_RETRIES = 2
MAX_REVIEW_RETRIES = 1   # Reviewer CHANGES_REQUESTED → Implementer 재시도 최대 횟수


class _StopRequested(BaseException):
    """
    사용자 즉시 중단 요청 시 on_iteration 훅에서 raise되는 sentinel.

    BaseException 을 상속하여 `except Exception` 블록에 잡히지 않고
    ScopedReactLoop → TDDPipeline.run() 까지 깔끔하게 전파된다.
    """


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
        max_review_retries: int = MAX_REVIEW_RETRIES,
        max_iterations: int = 15,
        reviewer_max_iterations: int = 5,
    ):
        self.agent_llm = agent_llm
        self.implementer_llm = implementer_llm or agent_llm
        self.test_runner = test_runner or DockerTestRunner()
        self.max_retries = max_retries
        self.max_review_retries = max_review_retries
        self.max_iterations = max_iterations
        self.reviewer_max_iterations = reviewer_max_iterations

    # ── 공개 인터페이스 ───────────────────────────────────────────────────────

    def run(
        self,
        task: Task,
        workspace: WorkspaceManager,
        on_progress=None,
        pause_ctrl=None,
    ) -> PipelineResult:
        """
        태스크 전체 파이프라인을 실행한다.
        workspace 는 이미 create() 된 상태여야 한다.

        on_progress : Callable[[dict], None] | None
            단계별 진행 상황을 전달받을 콜백. run.py 의 emit 을 래핑해서 넣는다.
            None 이면 아무것도 하지 않는다.
        pause_ctrl : PauseController | None
            중단 요청 감지용. is_stopped 가 True 이면 다음 LLM 응답 직후에
            _StopRequested 를 raise 해 파이프라인을 즉시 종료한다.
        """
        _p = on_progress or (lambda e: None)

        def _agent_p(e: dict) -> None:
            # 중단 요청이 있으면 on_iteration 콜백에서 _StopRequested 를 raise
            if pause_ctrl is not None and pause_ctrl.is_stopped:
                raise _StopRequested("사용자 즉시 중단 요청")
            merged = {**e, "task_id": task.id} if "task_id" not in e else e
            _p(merged)

        logger.info("[%s] 파이프라인 시작", task.id)

        # hotline decisions.md가 ask_user 확정 후 workspace에도 동기화되도록 등록
        register_workspace_context_dir(task.id, workspace.tests_dir.parent / "context")
        try:
            return self._run_pipeline(task, workspace, _p, _agent_p)
        finally:
            unregister_workspace_context_dir(task.id)

    def _run_pipeline(self, task, workspace, _p, _agent_p) -> "PipelineResult":
        """run()의 실제 파이프라인 로직. workspace 등록/해제는 run()이 담당한다."""
        try:
            return self.__run_pipeline_inner(task, workspace, _p, _agent_p)
        except _StopRequested:
            logger.info("[%s] 즉시 중단 요청 — 파이프라인 종료", task.id)
            return PipelineResult.failed(task, "[ABORTED] 사용자 즉시 중단 요청")

    def __run_pipeline_inner(self, task, workspace, _p, _agent_p) -> "PipelineResult":
        """_run_pipeline() 의 실제 구현. _StopRequested 는 _run_pipeline() 이 잡는다."""
        # ── Step 1: 테스트 작성 ───────────────────────────────────────────────
        task.status = TaskStatus.WRITING_TESTS
        _p({"type": "step", "step": "test_writing", "message": "TestWriter: 테스트 작성 중…"})
        test_scoped = self._run_test_writer(task, workspace, on_progress=_agent_p)
        if not test_scoped.succeeded:
            prefix = "[MAX_ITER] " if _is_max_iter(test_scoped) else ""
            return PipelineResult.failed(task, f"{prefix}TestWriter 실패: {test_scoped.answer}")

        test_files = workspace.list_test_files()
        if not test_files:
            # 모델이 write_file을 호출하지 않고 종료한 경우 — 즉시 1회 재시도
            logger.warning("[%s] TestWriter 파일 미생성 — 재시도", task.id)
            _p({"type": "step", "step": "test_writing_retry", "message": "TestWriter: 파일 미생성 — 재시도 중…"})
            test_scoped = self._run_test_writer(task, workspace, retry=True, on_progress=_agent_p)
            if not test_scoped.succeeded:
                prefix = "[MAX_ITER] " if _is_max_iter(test_scoped) else ""
                return PipelineResult.failed(task, f"{prefix}TestWriter 실패: {test_scoped.answer}")
            test_files = workspace.list_test_files()
            if not test_files:
                return PipelineResult.failed(task, "TestWriter 가 tests/ 에 파일을 생성하지 않았습니다.")
        _p({"type": "step", "step": "test_written",
            "message": f"테스트 파일 생성: {', '.join(test_files)}"})
        logger.info("[%s] 테스트 파일 생성: %s", task.id, test_files)

        # ── Step 1.5: 테스트 품질 게이트 (P2 정적 검증 + P3 커버리지) ────────
        static_issues = _validate_tests_static(test_files, workspace)
        missing_criteria = _check_criteria_coverage(task, test_files, workspace, self.agent_llm)
        if static_issues or missing_criteria:
            logger.warning(
                "[%s] 테스트 품질 게이트 — 정적 이슈: %s | 미커버 기준: %s",
                task.id, static_issues, missing_criteria,
            )
            issues_summary = "; ".join(
                (static_issues or []) + [f"미커버: {c}" for c in (missing_criteria or [])]
            )
            _p({"type": "step", "step": "quality_gate",
                "message": f"품질 게이트 재시도 — {issues_summary[:120]}"})
            test_scoped = self._run_test_writer(
                task, workspace,
                static_issues=static_issues,
                missing_criteria=missing_criteria,
                on_progress=_agent_p,
            )
            if not test_scoped.succeeded:
                prefix = "[MAX_ITER] " if _is_max_iter(test_scoped) else ""
                return PipelineResult.failed(
                    task, f"{prefix}TestWriter 품질 보완 실패: {test_scoped.answer}"
                )
            test_files = workspace.list_test_files()
            if not test_files:
                return PipelineResult.failed(task, "TestWriter 품질 게이트 후 파일 미생성")
            _p({"type": "step", "step": "quality_gate_ok",
                "message": f"품질 게이트 통과 — {', '.join(test_files)}"})
            logger.info("[%s] 품질 게이트 통과 후 테스트 파일: %s", task.id, test_files)

        # ── Step 2–3: 구현 → 테스트 → 리뷰 (리뷰 피드백 반영 재시도 포함) ────
        docker_result: RunResult | None = None
        review: ReviewResult | None = None
        reviewer_feedback: str = ""

        for review_attempt in range(self.max_review_retries + 1):
            # 리뷰 재시도 시 이전 테스트 에러 초기화 (새 구현 시도가 기준)
            if review_attempt > 0:
                task.last_error = ""
                task.retry_count = 0
                _p({"type": "step", "step": "review_retry",
                    "message": f"Reviewer 피드백 반영 — 재구현 시작 (리뷰 재시도 {review_attempt}/{self.max_review_retries})"})

            # ── Step 2: 구현 + 테스트 실행 ───────────────────────────────────
            docker_result = None
            for attempt in range(self.max_retries):
                task.status = TaskStatus.IMPLEMENTING
                label = (
                    f"Implementer: 재구현 중… (Reviewer 피드백 반영, 시도 {attempt + 1}/{self.max_retries})"
                    if reviewer_feedback
                    else f"Implementer: 구현 중… (시도 {attempt + 1}/{self.max_retries})"
                )
                _p({"type": "step", "step": "implementing", "message": label})
                impl_scoped = self._run_implementer(
                    task, workspace, reviewer_feedback=reviewer_feedback,
                    on_progress=_agent_p,
                )
                if not impl_scoped.succeeded:
                    prefix = "[MAX_ITER] " if _is_max_iter(impl_scoped) else ""
                    return PipelineResult.failed(task, f"{prefix}Implementer 실패: {impl_scoped.answer}")

                task.status = TaskStatus.RUNNING_TESTS
                _p({"type": "step", "step": "docker_running", "message": "Docker 테스트 실행 중…"})
                docker_result = self.test_runner.run(workspace.path, task.target_files)
                logger.info(
                    "[%s] 테스트 실행 (시도 %d/%d): %s",
                    task.id, attempt + 1, self.max_retries, docker_result.summary,
                )

                if docker_result.passed:
                    task.retry_count = attempt
                    _p({"type": "step", "step": "docker_pass",
                        "message": f"테스트 통과 — {docker_result.summary}"})
                    break

                task.retry_count = attempt + 1
                task.last_error = docker_result.stdout
                logger.warning("[%s] 테스트 실패, 재시도 %d회", task.id, task.retry_count)
                _p({"type": "step", "step": "docker_fail",
                    "message": f"테스트 실패 — {docker_result.summary}"
                               + (f" (재시도 {task.retry_count}/{self.max_retries})"
                                  if task.retry_count < self.max_retries else "")})

                if attempt == self.max_retries - 1:
                    return PipelineResult.failed(
                        task,
                        f"테스트가 {self.max_retries}회 모두 실패했습니다.\n"
                        f"마지막 오류:\n{docker_result.summary}",
                    )

            # ── Step 3: 코드 리뷰 ─────────────────────────────────────────────
            task.status = TaskStatus.REVIEWING
            _p({"type": "step", "step": "reviewing", "message": "Reviewer: 코드 검토 중…"})
            review_scoped = self._run_reviewer(task, workspace, docker_result, on_progress=_agent_p)
            review = _parse_review(review_scoped.answer)
            logger.info("[%s] 리뷰 결과: %s — %s", task.id, review.verdict, review.summary)

            if review.approved:
                _p({"type": "step", "step": "review_approved",
                    "message": f"Reviewer APPROVED — {review.summary}"})
                break

            _p({"type": "step", "step": "review_rejected",
                "message": f"Reviewer CHANGES_REQUESTED — {review.summary}"})

            if review_attempt < self.max_review_retries:
                # CHANGES_REQUESTED → 피드백을 Implementer에 전달하고 재시도
                reviewer_feedback = review.details or review.summary
                logger.warning(
                    "[%s] Reviewer CHANGES_REQUESTED (리뷰 시도 %d/%d) — 피드백 반영 재구현\n  %s",
                    task.id, review_attempt + 1, self.max_review_retries,
                    reviewer_feedback[:200],
                )
            # 마지막 review_attempt이면 루프 자연 종료 → 아래 succeeded=False 처리

        task.status = TaskStatus.COMMITTING
        failure_reason = (
            "" if review.approved
            else f"Reviewer CHANGES_REQUESTED: {review.summary}"
        )
        return PipelineResult(
            task=task,
            succeeded=review.approved,
            failure_reason=failure_reason,
            test_result=docker_result,
            review=review,
            test_files=workspace.list_test_files(),
            impl_files=workspace.list_src_files(),
        )

    # ── 개별 에이전트 실행 ────────────────────────────────────────────────────

    def _run_test_writer(
        self,
        task: Task,
        workspace: WorkspaceManager,
        retry: bool = False,
        static_issues: list[str] | None = None,
        missing_criteria: list[str] | None = None,
        on_progress=None,
    ) -> ScopedResult:
        loop = ScopedReactLoop(
            llm=self.agent_llm,
            role=TEST_WRITER,
            workspace_dir=workspace.path,
            max_iterations=self.max_iterations,
            on_progress=on_progress,
        )
        prompt = _build_test_writer_prompt(
            task, workspace,
            retry=retry,
            static_issues=static_issues,
            missing_criteria=missing_criteria,
        )
        logger.debug(
            "[%s] TestWriter 시작 (retry=%s, static_issues=%d, missing=%d)",
            task.id, retry,
            len(static_issues or []),
            len(missing_criteria or []),
        )
        return loop.run(prompt)

    def _run_implementer(
        self,
        task: Task,
        workspace: WorkspaceManager,
        reviewer_feedback: str = "",
        on_progress=None,
    ) -> ScopedResult:
        loop = ScopedReactLoop(
            llm=self.implementer_llm,
            role=IMPLEMENTER,
            workspace_dir=workspace.path,
            max_iterations=self.max_iterations,
            on_progress=on_progress,
        )
        prompt = _build_implementer_prompt(task, workspace, reviewer_feedback=reviewer_feedback)
        logger.debug(
            "[%s] Implementer 시작 (retry=%d, reviewer_feedback=%s)",
            task.id, task.retry_count, bool(reviewer_feedback),
        )
        return loop.run(prompt)

    def _run_reviewer(
        self,
        task: Task,
        workspace: WorkspaceManager,
        docker_result: RunResult,
        on_progress=None,
    ) -> ScopedResult:
        loop = ScopedReactLoop(
            llm=self.agent_llm,
            role=REVIEWER,
            workspace_dir=workspace.path,
            max_iterations=self.reviewer_max_iterations,
            on_progress=on_progress,
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


def _test_lang_rules(task: Task) -> str:
    """
    target_files 기반으로 TestWriter에 주입할 언어·import·출력 규약 섹션을 생성한다.
    """
    runtime = _detect_runtime(task.target_files)
    impl_files = [
        f for f in task.target_files
        if not Path(f).name.startswith("test_") and not Path(f).stem.endswith(".test")
    ]

    if runtime == "node":
        import_examples = "\n".join(
            f"  const ... = require('../src/{Path(f).name}');  // {f}"
            for f in impl_files
        )
        return f"""## 테스트 작성 규칙

**언어**: JavaScript (Node.js) — target_files에 .js 파일이 있으므로

**테스트 파일 위치**: `tests/` 디렉토리에 `test_*.js` 이름으로 생성

**import 경로** — target_files 파일명에서 직접 유도, 절대 다른 경로를 만들지 마세요:
{import_examples}

**출력 규약** — 테스트 파일 마지막에 반드시 아래 형식으로 출력하고 process.exit() 호출:
```javascript
// 성공 시
console.log(`OK: ${{passed}} passed, 0 failed`);
process.exit(0);

// 실패 시
console.log(`FAIL: ${{passed}} passed, ${{failed}} failed`);
failures.forEach(f => console.log(`- ${{f}}`));
process.exit(1);
```

**구조 패턴**:
```javascript
const failures = [];
let passed = 0;

// 각 테스트
try {{
  // ... assertion
  passed++;
}} catch (e) {{
  failures.push(`test_name: ${{e.message}}`);
}}
```
"""
    else:
        import_examples = "\n".join(
            f"  from {Path(f).stem.replace('-', '_')} import ...  # src/{Path(f).name}"
            for f in impl_files
        )
        return f"""## 테스트 작성 규칙

**언어**: Python — target_files에 .py 파일이 있으므로 (또는 HTML/CSS여서 Python으로 검증)

**테스트 파일 위치**: `tests/` 디렉토리에 `test_*.py` 이름으로 생성

**import 경로** — target_files 파일명에서 직접 유도, 절대 다른 경로를 만들지 마세요:
  (PYTHONPATH에 /workspace/src 가 포함되어 있어 src/ 모듈을 직접 import할 수 있습니다)
{import_examples}

**출력 규약** — 테스트 파일 마지막에 반드시 아래 형식으로 출력하고 sys.exit() 호출:
```python
import sys
# 성공 시
print(f"OK: {{passed}} passed, 0 failed")
sys.exit(0)

# 실패 시
print(f"FAIL: {{passed}} passed, {{failed}} failed")
for f in failures:
    print(f"- {{f}}")
sys.exit(1)
```

**구조 패턴**:
```python
import sys
failures = []
passed = 0

# 각 테스트
try:
    # ... assertion
    passed += 1
except Exception as e:
    failures.append(f"test_name: {{e}}")
```
"""


def _build_test_writer_prompt(
    task: Task,
    workspace: WorkspaceManager,
    retry: bool = False,
    static_issues: list[str] | None = None,
    missing_criteria: list[str] | None = None,
) -> str:
    structure_hint = ""
    if (workspace.path / "PROJECT_STRUCTURE.md").exists():
        structure_hint = "\n`PROJECT_STRUCTURE.md` 로 전체 코드베이스 구조를 먼저 파악하세요.\n"

    feedback_blocks: list[str] = []

    if retry:
        feedback_blocks.append(
            "⚠️ **이전 시도에서 tests/ 에 파일을 생성하지 않았습니다.**\n"
            "반드시 `write_file` 도구를 호출하여 `tests/` 디렉토리에 테스트 파일을 작성하세요.\n"
            "설명만 하고 도구를 호출하지 않으면 실패로 처리됩니다."
        )

    if static_issues:
        issue_list = "\n".join(f"  - {i}" for i in static_issues)
        feedback_blocks.append(
            f"⚠️ **정적 검증 실패 — 아래 문제를 반드시 수정하세요:**\n{issue_list}\n"
            "각 테스트 함수에 실제 assertion (`assert`, `assertEqual`, `expect(...)` 등)이 있어야 합니다."
        )

    if missing_criteria:
        crit_list = "\n".join(f"  - {c}" for c in missing_criteria)
        feedback_blocks.append(
            f"⚠️ **수락 기준 미커버 — 아래 항목을 커버하는 테스트를 추가하세요:**\n{crit_list}"
        )

    feedback_section = ""
    if feedback_blocks:
        feedback_section = "\n## 이전 시도 피드백\n\n" + "\n\n".join(feedback_blocks) + "\n"

    lang_rules = _test_lang_rules(task)

    return f"""## 태스크

**{task.title}**

{task.description}

## 수락 기준

{task.acceptance_criteria_text()}

## 워크스페이스 경로

`{workspace.path}`
{structure_hint}{_context_hint(workspace)}{feedback_section}
{lang_rules}
`src/` 에 있는 기존 코드를 먼저 확인하고,
`tests/` 에 위 규칙대로 테스트를 작성하세요.
구현이 없으므로 테스트는 실행 시 실패해야 합니다 (Red 단계).
"""


def _build_implementer_prompt(
    task: Task,
    workspace: WorkspaceManager,
    reviewer_feedback: str = "",
) -> str:
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
    if reviewer_feedback:
        base += f"""
## Reviewer 피드백 (이전 구현에서 지적된 사항)

```
{reviewer_feedback[:2000]}
```

위 피드백을 반드시 반영하세요. 테스트를 통과하는 것뿐 아니라 코드 품질 문제도 함께 수정해야 합니다.
"""

    if task.last_error:
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


# ── 테스트 품질 게이트 ────────────────────────────────────────────────────────


def _validate_tests_static(test_files: list[str], workspace: WorkspaceManager) -> list[str]:
    """
    테스트 파일을 정적 분석해 품질 문제를 반환한다. 빈 리스트 = 이상 없음.

    Python : ast 모듈로 test_ 함수에 assertion 유무 확인
    JS/TS  : 정규식으로 expect() / assert() 유무 확인
    """
    issues: list[str] = []
    for fname in test_files:
        path = workspace.path / fname
        if not path.exists():
            continue
        src = path.read_text(encoding="utf-8", errors="replace")
        suffix = path.suffix.lower()
        if suffix == ".py":
            issues.extend(_validate_python_test(src, fname))
        elif suffix in (".js", ".ts", ".jsx", ".tsx"):
            issues.extend(_validate_js_test(src, fname))
    return issues


def _validate_python_test(src: str, fname: str) -> list[str]:
    issues: list[str] = []
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        return [f"{fname}: 문법 오류 — {e}"]

    _ASSERT_ATTRS = {
        "assertEqual", "assertNotEqual", "assertTrue", "assertFalse",
        "assertIs", "assertIsNot", "assertIsNone", "assertIsNotNone",
        "assertIn", "assertNotIn", "assertRaises", "assertRaisesRegex",
        "assertGreater", "assertGreaterEqual", "assertLess", "assertLessEqual",
        "assertAlmostEqual", "assertRegex", "assertCountEqual",
        "assert_called", "assert_called_once", "assert_called_with",
        "assert_called_once_with", "assert_any_call", "assert_not_called",
    }

    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        if not node.name.startswith("test"):
            continue
        has_assertion = False
        for child in ast.walk(node):
            if isinstance(child, ast.Assert):
                has_assertion = True
                break
            if isinstance(child, ast.Call):
                func = child.func
                if isinstance(func, ast.Name) and func.id in _ASSERT_ATTRS:
                    has_assertion = True
                    break
                if isinstance(func, ast.Attribute) and func.attr in _ASSERT_ATTRS:
                    has_assertion = True
                    break
                # pytest.raises / pytest.approx / pytest.warns
                if (
                    isinstance(func, ast.Attribute)
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "pytest"
                ):
                    has_assertion = True
                    break
        if not has_assertion:
            issues.append(f"{fname}::{node.name} — assertion 없음 (빈 테스트 또는 pass만 있음)")
    return issues


def _validate_js_test(src: str, fname: str) -> list[str]:
    issues: list[str] = []
    # test('name', () => { ... }) 또는 it('name', async () => { ... })
    for m in re.finditer(
        r'(?:test|it)\s*\(\s*["\'](.+?)["\']\s*,\s*(?:async\s*)?\([^)]*\)\s*=>\s*\{',
        src,
    ):
        name = m.group(1)
        # 본문 추출: 여는 { 이후 중첩 괄호를 따라 닫는 } 찾기
        start = m.end() - 1  # points at '{'
        depth = 0
        body = ""
        for i in range(start, len(src)):
            ch = src[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    body = src[start + 1:i]
                    break
        has_assertion = bool(
            re.search(r"expect\s*\(|assert\s*\(|should\.|\.toBe|\.toEqual|\.toThrow|\.toHaveBeenCalled", body)
        )
        if not has_assertion:
            issues.append(f"{fname}::'{name}' — assertion 없음")
    return issues


def _check_criteria_coverage(
    task: Task,
    test_files: list[str],
    workspace: WorkspaceManager,
    llm,
) -> list[str]:
    """
    수락 기준 각 항목이 테스트에서 커버되는지 경량 LLM 으로 확인한다.
    미커버 항목 목록을 반환한다. 빈 리스트 = 모두 커버됨.
    LLM 호출 실패 시 [] 를 반환해 파이프라인을 차단하지 않는다.
    """
    test_parts: list[str] = []
    for fname in test_files:
        path = workspace.path / fname
        if path.exists():
            content = path.read_text(encoding="utf-8", errors="replace")[:3000]
            test_parts.append(f"### {fname}\n```\n{content}\n```")
    if not test_parts:
        return []

    prompt = (
        "다음 수락 기준 각 항목이 아래 테스트 코드에서 커버되는지 확인하세요.\n\n"
        f"## 수락 기준\n\n{task.acceptance_criteria_text()}\n\n"
        f"## 테스트 코드\n\n{''.join(test_parts)}\n\n"
        "## 지시\n\n"
        "각 수락 기준 항목을 검토한 뒤, **커버되지 않은 항목만** 다음 형식으로 출력하세요:\n"
        "MISSING: <항목 설명>\n\n"
        "모든 항목이 커버되면: MISSING: 없음\n"
        "불확실한 경우 커버된 것으로 간주하세요."
    )
    try:
        response = llm.chat([Message(role="user", content=prompt)])
        if isinstance(response.content, str):
            raw = response.content
        else:
            raw = " ".join(
                b.get("text", "") for b in response.content if b.get("type") == "text"
            )
    except Exception:
        logger.warning("[pipeline] criteria coverage 확인 실패 — 건너뜀")
        return []

    missing: list[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped.upper().startswith("MISSING:"):
            item = stripped.split(":", 1)[1].strip()
            if item and item.lower() not in ("없음", "none", "없음.", "n/a"):
                missing.append(item)
    return missing


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
