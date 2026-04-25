"""
cli/plan_runner.py — plan 모드용 planner + executor handoff
"""

from __future__ import annotations

from dataclasses import dataclass, field

from cli.interface import print_task_summary
from cli.pipeline_confirm import ConfirmType, PipelineConfirmManager
from cli.task_converter import ConversionError, PlanLoop
from orchestrator.task import Task


@dataclass
class PlanRunResult:
    task: Task | None
    execution_prompt: str | None
    user_aborted: bool
    warnings: list[str] = field(default_factory=list)


def build_execution_prompt(task: Task, original_request: str) -> str:
    lines: list[str] = [
        "다음 계획에 따라 로컬 코드베이스를 직접 수정하세요.",
        "",
        f"원래 사용자 요청: {original_request}",
        "",
        f"제목: {task.title}",
        f"언어: {task.language}",
        f"타입: {task.task_type}",
        f"대상 파일: {', '.join(task.target_files) or '(명시 없음)'}",
        "",
        "설명:",
        task.description.strip() or "(없음)",
        "",
        "수락 기준:",
    ]
    if task.acceptance_criteria:
        lines.extend(f"- {criterion}" for criterion in task.acceptance_criteria)
    else:
        lines.append("- (없음)")
    lines.extend([
        "",
        "요구사항:",
        "- 위 범위를 우선 기준으로 구현하세요.",
        "- 필요하면 관련 파일을 추가로 읽어도 되지만, 불필요한 변경은 피하세요.",
        "- 구현 후 가능한 검증을 직접 수행하세요.",
    ])
    return "\n".join(lines)


class PlanRunner:
    def __init__(
        self,
        planner: PlanLoop,
        confirm: PipelineConfirmManager,
    ) -> None:
        self.planner = planner
        self.confirm = confirm

    async def run(self, user_input: str) -> PlanRunResult:
        try:
            conversion = await self.planner.plan(user_input)
        except ConversionError as exc:
            return PlanRunResult(
                task=None,
                execution_prompt=None,
                user_aborted=True,
                warnings=[str(exc)],
            )

        if conversion.aborted:
            return PlanRunResult(
                task=None,
                execution_prompt=None,
                user_aborted=True,
                warnings=conversion.warnings,
            )

        task = conversion.task
        assert task is not None
        print_task_summary(task, warnings=conversion.warnings or [])

        if not await self.confirm.async_confirm(
            ConfirmType.TASK_REVIEW,
            "계획된 태스크를 확인하세요.",
            detail=f"대상 파일: {', '.join(task.target_files) or '(없음)'}",
        ):
            return PlanRunResult(
                task=task,
                execution_prompt=None,
                user_aborted=True,
                warnings=conversion.warnings,
            )

        return PlanRunResult(
            task=task,
            execution_prompt=build_execution_prompt(task, user_input),
            user_aborted=False,
            warnings=conversion.warnings,
        )
