"""
scripts/benchmark_thresholds.py — isolated A/B benchmark runner for role compaction presets.

전제:
  - backend 서버가 이미 실행 중이어야 한다.
  - 이 스크립트는 대상 프로젝트 레포를 직접 건드리지 않고, git worktree 를 따로
    만들어 baseline / candidate 를 각각 독립 실행한다.

주요 흐름:
  1. 대상 레포의 tasks.yaml 에서 "지금 실행 가능한" backend 태스크를 n개 고른다.
  2. 태스크마다 dependency closure 만 담은 임시 tasks.yaml 을 생성한다.
  3. baseline(default 30k) / candidate(preset) 를 worktree 에서 각각 실행한다.
  4. reports/logs 를 arm 별로 분리 저장하고 결과를 비교한다.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from orchestrator.report import load_reports  # noqa: E402
from orchestrator.task import Task, TaskStatus, load_tasks, save_tasks  # noqa: E402

DEFAULT_SERVER_URL = "http://localhost:8000"
DEFAULT_TASKS_PATH = "agent-data/tasks.yaml"
DEFAULT_OUTPUT_SUBDIR = Path("agent-data/benchmarks/thresholds")
DEFAULT_POLL_INTERVAL_S = 2.0
DEFAULT_TIMEOUT_S = 60.0 * 60.0
ROLE_COMPACTION_DEFAULT = "default"
ROLE_COMPACTION_PRESETS = {"conservative", "balanced", "aggressive"}


def load_log_metrics(logs_dir: Path | None) -> dict[str, dict[str, int]]:
    """task_id 별 call_log 파생 지표를 집계한다."""
    if logs_dir is None or not logs_dir.exists():
        return {}

    metrics: dict[str, dict[str, int]] = {}
    for path in sorted(logs_dir.glob("*.jsonl")):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            task_id = str(row.get("task_id") or "").strip()
            if not task_id:
                continue
            agg = metrics.setdefault(task_id, {
                "llm_calls": 0,
                "compaction_events": 0,
                "net_tokens_saved": 0,
            })
            if row.get("event") == "compaction":
                agg["compaction_events"] += 1
                agg["net_tokens_saved"] += int(row.get("net_tokens_saved", 0) or 0)
            else:
                agg["llm_calls"] += 1
    return metrics


def _mean(values: list[float]) -> float:
    return round(sum(values) / len(values), 4) if values else 0.0


def compare_threshold_runs(
    baseline_reports_dir: Path,
    candidate_reports_dir: Path,
    *,
    baseline_logs_dir: Path | None = None,
    candidate_logs_dir: Path | None = None,
    task_ids: list[str] | None = None,
) -> dict[str, Any]:
    """두 실행 결과를 공통 task_id 기준으로 비교한다."""
    baseline_reports = {r.task_id: r for r in load_reports(reports_dir=baseline_reports_dir)}
    candidate_reports = {r.task_id: r for r in load_reports(reports_dir=candidate_reports_dir)}
    ids = sorted(set(baseline_reports) & set(candidate_reports))
    if task_ids:
        wanted = set(task_ids)
        ids = [task_id for task_id in ids if task_id in wanted]

    baseline_logs = load_log_metrics(baseline_logs_dir)
    candidate_logs = load_log_metrics(candidate_logs_dir)

    task_rows: list[dict[str, Any]] = []
    baseline_tokens: list[float] = []
    candidate_tokens: list[float] = []
    baseline_cache_hit: list[float] = []
    candidate_cache_hit: list[float] = []
    baseline_elapsed: list[float] = []
    candidate_elapsed: list[float] = []
    outcome_changes = {"improved": 0, "regressed": 0, "stable": 0}

    for task_id in ids:
        before = baseline_reports[task_id]
        after = candidate_reports[task_id]
        before_log = baseline_logs.get(task_id, {})
        after_log = candidate_logs.get(task_id, {})

        if before.status != after.status:
            if before.status == "FAILED" and after.status == "COMPLETED":
                outcome = "improved"
            elif before.status == "COMPLETED" and after.status == "FAILED":
                outcome = "regressed"
            else:
                outcome = "stable"
        else:
            outcome = "stable"
        outcome_changes[outcome] += 1

        baseline_tokens.append(float(before.total_tokens or 0))
        candidate_tokens.append(float(after.total_tokens or 0))
        baseline_cache_hit.append(float(before.cache_hit_rate or 0))
        candidate_cache_hit.append(float(after.cache_hit_rate or 0))
        baseline_elapsed.append(float(before.time_elapsed_seconds or 0))
        candidate_elapsed.append(float(after.time_elapsed_seconds or 0))

        task_rows.append({
            "task_id": task_id,
            "before_status": before.status,
            "after_status": after.status,
            "outcome": outcome,
            "before_total_tokens": before.total_tokens,
            "after_total_tokens": after.total_tokens,
            "delta_total_tokens": after.total_tokens - before.total_tokens,
            "before_cache_hit_rate": before.cache_hit_rate,
            "after_cache_hit_rate": after.cache_hit_rate,
            "delta_cache_hit_rate": round(after.cache_hit_rate - before.cache_hit_rate, 4),
            "before_elapsed_seconds": before.time_elapsed_seconds,
            "after_elapsed_seconds": after.time_elapsed_seconds,
            "delta_elapsed_seconds": round(after.time_elapsed_seconds - before.time_elapsed_seconds, 4),
            "before_compaction_events": before_log.get("compaction_events", 0),
            "after_compaction_events": after_log.get("compaction_events", 0),
            "delta_compaction_events": after_log.get("compaction_events", 0) - before_log.get("compaction_events", 0),
            "before_net_tokens_saved": before_log.get("net_tokens_saved", 0),
            "after_net_tokens_saved": after_log.get("net_tokens_saved", 0),
            "delta_net_tokens_saved": after_log.get("net_tokens_saved", 0) - before_log.get("net_tokens_saved", 0),
        })

    return {
        "common_tasks": ids,
        "baseline_only_tasks": sorted(set(baseline_reports) - set(candidate_reports)),
        "candidate_only_tasks": sorted(set(candidate_reports) - set(baseline_reports)),
        "summary": {
            "samples": len(ids),
            "outcome_changes": outcome_changes,
            "avg_total_tokens": {
                "baseline": _mean(baseline_tokens),
                "candidate": _mean(candidate_tokens),
                "delta": round(_mean(candidate_tokens) - _mean(baseline_tokens), 4),
            },
            "avg_cache_hit_rate": {
                "baseline": _mean(baseline_cache_hit),
                "candidate": _mean(candidate_cache_hit),
                "delta": round(_mean(candidate_cache_hit) - _mean(baseline_cache_hit), 4),
            },
            "avg_elapsed_seconds": {
                "baseline": _mean(baseline_elapsed),
                "candidate": _mean(candidate_elapsed),
                "delta": round(_mean(candidate_elapsed) - _mean(baseline_elapsed), 4),
            },
        },
        "task_rows": task_rows,
    }


def format_report_markdown(comparison: dict[str, Any]) -> str:
    """비교 결과를 Markdown 문자열로 렌더링한다."""
    summary = comparison["summary"]
    lines = [
        f"## Threshold Benchmark (samples={summary['samples']})",
        "",
        "| Metric | Baseline | Candidate | Delta |",
        "|--------|---------:|----------:|------:|",
    ]
    for key, label in (
        ("avg_total_tokens", "Avg total_tokens"),
        ("avg_cache_hit_rate", "Avg cache_hit_rate"),
        ("avg_elapsed_seconds", "Avg elapsed_seconds"),
    ):
        row = summary[key]
        lines.append(f"| {label} | {row['baseline']} | {row['candidate']} | {row['delta']} |")

    oc = summary["outcome_changes"]
    lines.extend([
        "",
        f"Outcome changes: improved={oc['improved']}, regressed={oc['regressed']}, stable={oc['stable']}",
        "",
        "| Task | Outcome | Δ tokens | Δ cache_hit | Δ elapsed(s) | Δ compactions | Δ net_saved |",
        "|------|---------|---------:|------------:|-------------:|--------------:|------------:|",
    ])
    for row in comparison["task_rows"]:
        lines.append(
            f"| {row['task_id']} | {row['outcome']} | {row['delta_total_tokens']} | "
            f"{row['delta_cache_hit_rate']} | {row['delta_elapsed_seconds']} | "
            f"{row['delta_compaction_events']} | {row['delta_net_tokens_saved']} |"
        )
    if comparison["baseline_only_tasks"] or comparison["candidate_only_tasks"]:
        lines.extend([
            "",
            f"baseline_only: {comparison['baseline_only_tasks']}",
            f"candidate_only: {comparison['candidate_only_tasks']}",
        ])
    return "\n".join(lines)


def print_report(comparison: dict[str, Any]) -> None:
    print(format_report_markdown(comparison))


def _validate_preset(value: str) -> str:
    if value == ROLE_COMPACTION_DEFAULT or value in ROLE_COMPACTION_PRESETS:
        return value
    raise ValueError(
        f"Unknown preset: {value!r}. "
        f"Allowed: {[ROLE_COMPACTION_DEFAULT, *sorted(ROLE_COMPACTION_PRESETS)]!r}"
    )


def _task_files_exist(repo_path: Path, task: Task) -> bool:
    return bool(task.target_files) and all((repo_path / rel_path).exists() for rel_path in task.target_files)


def is_task_runnable(task: Task, tasks_by_id: dict[str, Task], repo_path: Path) -> bool:
    """독립 실험에서 바로 돌릴 수 있는 태스크인지 판정한다."""
    if task.task_type == "frontend":
        return False
    if task.status not in (TaskStatus.PENDING, TaskStatus.FAILED):
        return False
    if not task.target_files:
        return False
    for dep_id in task.depends_on:
        dep = tasks_by_id.get(dep_id)
        if dep is None:
            return False
        if dep.status == TaskStatus.DONE:
            continue
        if _task_files_exist(repo_path, dep):
            continue
        return False
    return True


def select_runnable_tasks(
    tasks: list[Task],
    repo_path: Path,
    *,
    limit: int,
    requested_task_ids: list[str] | None = None,
) -> list[Task]:
    """YAML 순서를 보존하며 지금 실행 가능한 태스크를 고른다."""
    tasks_by_id = {task.id: task for task in tasks}
    requested = set(requested_task_ids or [])
    selected: list[Task] = []
    for task in tasks:
        if requested and task.id not in requested:
            continue
        if is_task_runnable(task, tasks_by_id, repo_path):
            selected.append(task)
        if len(selected) >= limit:
            break
    return selected


def dependency_closure(tasks: list[Task], task_id: str) -> list[Task]:
    """task_id 와 그 선행 의존성 closure 를 원래 순서대로 반환한다."""
    tasks_by_id = {task.id: task for task in tasks}
    needed: set[str] = set()

    def visit(curr_id: str) -> None:
        if curr_id in needed:
            return
        task = tasks_by_id.get(curr_id)
        if task is None:
            raise KeyError(f"Unknown task dependency: {curr_id}")
        needed.add(curr_id)
        for dep_id in task.depends_on:
            visit(dep_id)

    visit(task_id)
    return [task for task in tasks if task.id in needed]


def make_subset_tasks(tasks: list[Task], repo_path: Path, target_task_id: str) -> list[Task]:
    """선택 태스크 실험용 subset tasks.yaml 을 생성한다.

    - 타깃 태스크: PENDING 으로 리셋
    - 선행 태스크: 이미 만족된 것으로 간주하고 DONE 으로 고정
    """
    closure = dependency_closure(tasks, target_task_id)
    target_ids = {target_task_id}
    subset: list[Task] = []
    for task in closure:
        if task.id in target_ids:
            subset.append(replace(
                task,
                status=TaskStatus.PENDING,
                retry_count=0,
                last_error="",
                pr_url="",
                failure_reason="",
            ))
            continue
        forced_status = TaskStatus.DONE if (
            task.status == TaskStatus.DONE or _task_files_exist(repo_path, task)
        ) else task.status
        subset.append(replace(
            task,
            status=forced_status,
            retry_count=0,
            last_error="",
            pr_url="",
            failure_reason="",
        ))
    return subset


def _json_request(method: str, url: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} {url}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Failed to reach backend server at {url}: {exc}") from exc


def start_pipeline_run(server_url: str, payload: dict[str, Any]) -> str:
    body = _json_request("POST", f"{server_url.rstrip('/')}/api/pipeline/run", payload)
    job_id = body.get("job_id")
    if not job_id:
        raise RuntimeError(f"pipeline/run did not return job_id: {body}")
    return str(job_id)


def wait_for_job(server_url: str, job_id: str, *, timeout_s: float, poll_interval_s: float) -> dict[str, Any]:
    """백엔드 job 이 종료될 때까지 poll 한다."""
    status_url = f"{server_url.rstrip('/')}/api/pipeline/status/{job_id}"
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        body = _json_request("GET", status_url)
        status = body.get("status")
        if status in ("done", "error"):
            return body
        time.sleep(poll_interval_s)
    raise TimeoutError(f"Timed out waiting for job {job_id} after {timeout_s}s")


def create_detached_worktree(repo_path: Path, worktree_path: Path, base_ref: str) -> None:
    worktree_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "-C", str(repo_path), "worktree", "add", "--detach", str(worktree_path), base_ref],
        check=True,
        capture_output=True,
        text=True,
    )


def remove_worktree(repo_path: Path, worktree_path: Path) -> None:
    subprocess.run(
        ["git", "-C", str(repo_path), "worktree", "remove", "--force", str(worktree_path)],
        check=True,
        capture_output=True,
        text=True,
    )


def run_benchmark_arm(
    *,
    server_url: str,
    source_repo_path: Path,
    base_ref: str,
    output_root: Path,
    label: str,
    preset: str,
    subset_tasks: list[Task],
    target_task: Task,
    keep_worktrees: bool,
    timeout_s: float,
    poll_interval_s: float,
) -> dict[str, Any]:
    """단일 arm 을 isolated worktree 에서 실행한다."""
    _validate_preset(preset)
    with tempfile.TemporaryDirectory(prefix=f"threshold-{label}-{target_task.id}-") as tmpdir:
        worktree_path = Path(tmpdir) / "repo"
        create_detached_worktree(source_repo_path, worktree_path, base_ref)
        try:
            arm_dir = output_root / label
            reports_dir = arm_dir / "reports"
            logs_dir = arm_dir / "logs"
            task_bundle_dir = worktree_path / "agent-data" / "benchmarks" / "thresholds" / label / target_task.id
            task_bundle_dir.mkdir(parents=True, exist_ok=True)
            tasks_path = task_bundle_dir / "tasks.yaml"
            save_tasks(subset_tasks, tasks_path)

            payload: dict[str, Any] = {
                "tasks_path": str(tasks_path),
                "repo_path": str(worktree_path),
                "base_branch": "main",
                "no_pr": True,
                "no_push": True,
                "max_workers": 1,
                "reports_dir": str(reports_dir),
                "logs_dir": str(logs_dir),
            }
            if preset != ROLE_COMPACTION_DEFAULT:
                payload["role_compaction_tuning_enabled"] = True
                payload["role_compaction_tuning_preset"] = preset

            job_id = start_pipeline_run(server_url, payload)
            job_status = wait_for_job(
                server_url,
                job_id,
                timeout_s=timeout_s,
                poll_interval_s=poll_interval_s,
            )
            if job_status.get("status") != "done":
                raise RuntimeError(
                    f"Benchmark arm {label!r} for task {target_task.id!r} failed: "
                    f"{job_status.get('error') or job_status}"
                )
            return {
                "job_id": job_id,
                "status": job_status.get("status", ""),
                "worktree_path": str(worktree_path) if keep_worktrees else "",
                "reports_dir": str(reports_dir),
                "logs_dir": str(logs_dir),
            }
        finally:
            if keep_worktrees:
                kept = output_root / "worktrees" / f"{target_task.id}-{label}"
                kept.parent.mkdir(parents=True, exist_ok=True)
                if kept.exists():
                    shutil.rmtree(kept)
                shutil.copytree(worktree_path, kept)
            remove_worktree(source_repo_path, worktree_path)


def benchmark_live_runs(
    *,
    repo_path: Path,
    tasks_path: Path,
    server_url: str,
    limit: int,
    preset_a: str,
    preset_b: str,
    base_ref: str,
    output_dir: Path | None = None,
    requested_task_ids: list[str] | None = None,
    keep_worktrees: bool = False,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
) -> dict[str, Any]:
    """실제 backend API 를 호출해 isolated A/B benchmark 를 수행한다."""
    tasks = load_tasks(tasks_path)
    selected = select_runnable_tasks(
        tasks,
        repo_path,
        limit=limit,
        requested_task_ids=requested_task_ids,
    )
    if not selected:
        raise RuntimeError("No runnable tasks found for benchmark.")

    now = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_root = output_dir or (repo_path / DEFAULT_OUTPUT_SUBDIR / now)
    output_root.mkdir(parents=True, exist_ok=True)

    runs: list[dict[str, Any]] = []
    for task in selected:
        subset_tasks = make_subset_tasks(tasks, repo_path, task.id)
        run_a = run_benchmark_arm(
            server_url=server_url,
            source_repo_path=repo_path,
            base_ref=base_ref,
            output_root=output_root,
            label="baseline",
            preset=preset_a,
            subset_tasks=subset_tasks,
            target_task=task,
            keep_worktrees=keep_worktrees,
            timeout_s=timeout_s,
            poll_interval_s=poll_interval_s,
        )
        run_b = run_benchmark_arm(
            server_url=server_url,
            source_repo_path=repo_path,
            base_ref=base_ref,
            output_root=output_root,
            label="candidate",
            preset=preset_b,
            subset_tasks=subset_tasks,
            target_task=task,
            keep_worktrees=keep_worktrees,
            timeout_s=timeout_s,
            poll_interval_s=poll_interval_s,
        )
        runs.append({
            "task_id": task.id,
            "title": task.title,
            "baseline_job_id": run_a["job_id"],
            "candidate_job_id": run_b["job_id"],
        })

    comparison = compare_threshold_runs(
        output_root / "baseline" / "reports",
        output_root / "candidate" / "reports",
        baseline_logs_dir=output_root / "baseline" / "logs",
        candidate_logs_dir=output_root / "candidate" / "logs",
        task_ids=[task.id for task in selected],
    )
    comparison["selection"] = {
        "repo_path": str(repo_path),
        "tasks_path": str(tasks_path),
        "server_url": server_url,
        "base_ref": base_ref,
        "preset_a": preset_a,
        "preset_b": preset_b,
        "selected_tasks": [
            {"task_id": task.id, "title": task.title}
            for task in selected
        ],
        "output_root": str(output_root),
        "runs": runs,
    }

    (output_root / "summary.json").write_text(
        json.dumps(comparison, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_root / "summary.md").write_text(
        format_report_markdown(comparison),
        encoding="utf-8",
    )
    return comparison


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Benchmark role compaction presets via isolated backend runs.")
    parser.add_argument("--repo-path")
    parser.add_argument("--tasks-path", default=DEFAULT_TASKS_PATH)
    parser.add_argument("--server-url", default=DEFAULT_SERVER_URL)
    parser.add_argument("--n", type=int, default=3)
    parser.add_argument("--preset-a", default=ROLE_COMPACTION_DEFAULT)
    parser.add_argument("--preset-b", default="aggressive")
    parser.add_argument("--base-ref", default="HEAD")
    parser.add_argument("--output-dir")
    parser.add_argument("--task-id", action="append", dest="task_ids")
    parser.add_argument("--keep-worktrees", action="store_true")
    parser.add_argument("--timeout-s", type=float, default=DEFAULT_TIMEOUT_S)
    parser.add_argument("--poll-interval-s", type=float, default=DEFAULT_POLL_INTERVAL_S)
    parser.add_argument("--baseline-reports-dir")
    parser.add_argument("--candidate-reports-dir")
    parser.add_argument("--baseline-logs-dir")
    parser.add_argument("--candidate-logs-dir")
    parser.add_argument("--json", action="store_true", dest="as_json")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    # compare-only compatibility mode
    if args.baseline_reports_dir and args.candidate_reports_dir:
        comparison = compare_threshold_runs(
            Path(args.baseline_reports_dir),
            Path(args.candidate_reports_dir),
            baseline_logs_dir=Path(args.baseline_logs_dir) if args.baseline_logs_dir else None,
            candidate_logs_dir=Path(args.candidate_logs_dir) if args.candidate_logs_dir else None,
            task_ids=args.task_ids,
        )
    else:
        if not args.repo_path:
            parser.error("--repo-path is required unless compare-only dirs are provided.")
        repo_path = Path(args.repo_path).resolve()
        tasks_path = Path(args.tasks_path)
        if not tasks_path.is_absolute():
            tasks_path = repo_path / tasks_path
        comparison = benchmark_live_runs(
            repo_path=repo_path,
            tasks_path=tasks_path,
            server_url=args.server_url,
            limit=args.n,
            preset_a=_validate_preset(args.preset_a),
            preset_b=_validate_preset(args.preset_b),
            base_ref=args.base_ref,
            output_dir=Path(args.output_dir).resolve() if args.output_dir else None,
            requested_task_ids=args.task_ids,
            keep_worktrees=args.keep_worktrees,
            timeout_s=args.timeout_s,
            poll_interval_s=args.poll_interval_s,
        )

    if args.as_json:
        print(json.dumps(comparison, ensure_ascii=False, indent=2))
    else:
        print_report(comparison)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
