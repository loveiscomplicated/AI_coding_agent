"""
scripts/verify_dep_injection.py

선행 의존 태스크가 있음에도 `dep_files_injected == 0` 인 태스크 리포트를 찾아낸다.
파이프라인 상 선행 태스크가 존재하면 최소 1개 이상의 파일이 workspace/src 에
주입되어야 한다. 0건이면 dep injection 경로가 고장났다는 신호.

사용법:
    python scripts/verify_dep_injection.py
    python scripts/verify_dep_injection.py --tasks data/tasks.yaml --reports agent-data/reports

리턴 코드:
    0: 버그 없음 (혹은 해당 조건을 만족하는 리포트가 전무)
    1: depends_on 이 비어있지 않은데 dep_files_injected == 0 인 리포트가 있음
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from project_paths import resolve_reports_dir, resolve_tasks_path  # noqa: E402


@dataclass
class BuggyReport:
    task_id: str
    depends_on: list[str]
    report_path: Path

    def format(self) -> str:
        deps = ", ".join(self.depends_on) if self.depends_on else "(none)"
        return f"- {self.task_id}  depends_on=[{deps}]  dep_files_injected=0  ({self.report_path})"


def load_task_dependencies(tasks_path: Path) -> dict[str, list[str]]:
    """tasks.yaml → {task_id: [depends_on...]}"""
    if not tasks_path.exists():
        return {}
    doc = yaml.safe_load(tasks_path.read_text(encoding="utf-8")) or {}
    entries = doc.get("tasks", []) if isinstance(doc, dict) else []
    mapping: dict[str, list[str]] = {}
    for t in entries:
        if not isinstance(t, dict):
            continue
        tid = t.get("id")
        deps = t.get("depends_on") or []
        if isinstance(tid, str):
            mapping[tid] = [str(d) for d in deps if d]
    return mapping


def scan_reports(
    reports_dir: Path,
    deps_by_task: dict[str, list[str]],
) -> tuple[list[BuggyReport], int]:
    """
    reports_dir 내 task-*.yaml 을 모두 읽고, depends_on 이 non-empty 인데
    dep_files_injected == 0 인 리포트를 반환. 두 번째 값은 검사된 리포트 수.
    """
    buggy: list[BuggyReport] = []
    scanned = 0
    if not reports_dir.exists():
        return buggy, scanned

    for path in sorted(reports_dir.glob("task-*.yaml")):
        try:
            doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            continue
        if not isinstance(doc, dict):
            continue
        task_id = doc.get("task_id")
        if not isinstance(task_id, str):
            continue
        scanned += 1
        deps = deps_by_task.get(task_id, [])
        if not deps:
            continue  # depends_on 이 비어있으면 검증 대상 아님
        metrics = doc.get("metrics") or {}
        injected = int(metrics.get("dep_files_injected", 0) or 0)
        if injected == 0:
            buggy.append(BuggyReport(task_id=task_id, depends_on=deps, report_path=path))
    return buggy, scanned


def build_report(buggy: list[BuggyReport], scanned: int) -> str:
    lines = [
        "# verify_dep_injection 결과",
        f"검사한 리포트 수: {scanned}",
        f"dep injection 의심 건수: {len(buggy)}",
        "",
    ]
    if buggy:
        lines.append("## 의심 리포트")
        lines.extend(b.format() for b in buggy)
    else:
        lines.append("의심 리포트 없음 (0건).")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", default="agent-data/tasks.yaml")
    parser.add_argument("--reports", default="agent-data/reports")
    parser.add_argument(
        "--base", default=".", help="프로젝트 루트 (project_paths 해석용)"
    )
    args = parser.parse_args(argv)

    tasks_path = resolve_tasks_path(args.tasks, args.base)
    # 기본 경로 해석이 agent-data/ 를 가리켰지만 거기에 tasks.yaml 이 없고
    # 레거시 data/ 에 있는 경우를 대비한 fallback
    if not tasks_path.exists():
        legacy = Path(args.base) / "data" / "tasks.yaml"
        if legacy.exists():
            tasks_path = legacy
    reports_dir = resolve_reports_dir(args.reports, args.base)

    deps_by_task = load_task_dependencies(tasks_path)
    buggy, scanned = scan_reports(reports_dir, deps_by_task)

    print(build_report(buggy, scanned))
    return 1 if buggy else 0


if __name__ == "__main__":
    raise SystemExit(main())
