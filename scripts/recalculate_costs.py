"""
scripts/recalculate_costs.py — 기존 task report YAML의 cost_usd 재계산.

_MODEL_PRICING 테이블을 갱신한 후, 디스크에 저장된 task report 들의
cost_usd / cost_estimation_quality 필드를 현재 단가 기준으로 다시 계산해
덮어쓴다. token_usage 와 models_used 필드는 원본을 유지하며, 덮어쓰기
직전 동일 경로에 `.bak` 사본을 남긴다.

사용법 (이 워크스페이스는 uv 기반이라 uv run 필요):
    uv run python scripts/recalculate_costs.py                       # agent-data/reports
    uv run python scripts/recalculate_costs.py --reports-dir DIR     # 경로 지정
    uv run python scripts/recalculate_costs.py --dry-run             # 변경 미리보기
    uv run python scripts/recalculate_costs.py --no-backup           # .bak 생략
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import yaml

# 프로젝트 루트를 sys.path 에 추가해 스크립트 단독 실행 지원
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from orchestrator.report import _calculate_cost_with_quality  # noqa: E402
from reports.task_report import TaskReport  # noqa: E402

logger = logging.getLogger("recalculate_costs")


def _report_to_usage_tuple(
    token_usage: dict[str, dict[str, int]] | None,
) -> dict[str, tuple[int, int, int, int]]:
    """YAML의 {role: {input, output, ...}} 구조를 _calculate_cost 가 기대하는
    tuple 형태로 변환한다."""
    if not token_usage:
        return {}
    result: dict[str, tuple[int, int, int, int]] = {}
    for role, usage in token_usage.items():
        if isinstance(usage, dict):
            result[role] = (
                int(usage.get("input", 0)),
                int(usage.get("output", 0)),
                int(usage.get("cached_read", 0)),
                int(usage.get("cached_write", 0)),
            )
    return result


def recalculate_file(
    path: Path,
    *,
    dry_run: bool = False,
    backup: bool = True,
) -> dict[str, object]:
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)

    report = TaskReport.from_dict(data)
    if not report.models_used:
        return {"path": str(path), "skipped": "no models_used"}

    token_usage_tuples = _report_to_usage_tuple(report.token_usage)
    new_cost, new_quality, missing = _calculate_cost_with_quality(
        token_usage_tuples, report.models_used
    )

    old_cost = data.get("metrics", {}).get("cost_usd")
    old_quality = data.get("metrics", {}).get("cost_estimation_quality", "missing")

    changed = old_cost != new_cost or old_quality != new_quality
    backup_path: Path | None = None
    if changed and not dry_run:
        if backup:
            # 덮어쓰기 직전 .bak 사본을 남겨 롤백 지점을 확보한다.
            backup_path = path.with_suffix(path.suffix + ".bak")
            backup_path.write_bytes(path.read_bytes())
        data.setdefault("metrics", {})["cost_usd"] = new_cost
        data["metrics"]["cost_estimation_quality"] = new_quality
        with path.open("w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False, default_flow_style=False)

    return {
        "path": str(path),
        "changed": changed,
        "old_cost": old_cost,
        "new_cost": new_cost,
        "old_quality": old_quality,
        "new_quality": new_quality,
        "missing_models": missing,
        "backup_path": str(backup_path) if backup_path else None,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--reports-dir", default="agent-data/reports", type=Path)
    ap.add_argument("--dry-run", action="store_true", help="쓰기 없이 결과만 출력")
    ap.add_argument("--no-backup", action="store_true", help=".bak 사본을 남기지 않음")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    if not args.reports_dir.exists():
        logger.error("reports_dir 없음: %s", args.reports_dir)
        return 1

    total = 0
    changed = 0
    for path in sorted(args.reports_dir.glob("task-*.yaml")):
        total += 1
        try:
            result = recalculate_file(
                path, dry_run=args.dry_run, backup=not args.no_backup
            )
        except Exception as e:
            logger.warning("실패 (%s): %s", path, e)
            continue
        if result.get("skipped"):
            logger.info("[skip ] %s (%s)", path.name, result["skipped"])
            continue
        if result["changed"]:
            changed += 1
            logger.info(
                "[%s] %s cost %s→%s quality %s→%s missing=%s backup=%s",
                "dry  " if args.dry_run else "write",
                path.name,
                result["old_cost"],
                result["new_cost"],
                result["old_quality"],
                result["new_quality"],
                result["missing_models"],
                result.get("backup_path") or "-",
            )
        else:
            logger.info("[ok   ] %s (unchanged)", path.name)

    logger.info("처리 완료: 총 %d개 중 %d개 갱신%s", total, changed, " (dry-run)" if args.dry_run else "")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
