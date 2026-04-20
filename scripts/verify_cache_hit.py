"""
scripts/verify_cache_hit.py — prompt caching 적중률 검증/측정 스크립트.

세 가지 모드를 지원한다:

A. 리포트 집계 모드 (--reports-dir)
   agent-data/reports/task-*.yaml 을 최근 N개 스캔해 역할별 cache hit ratio 와
   Reviewer verdict 분포를 계산. 정렬 기준은 YAML 의 `completed_at` 필드 (ISO8601);
   값이 없으면 파일 mtime 으로 fallback.

   수집된 샘플이 `--limit` 보다 적으면 stderr 에 명시적 경고를 출력한다.
   `--min-samples N` 으로 지정하면 이보다 적을 때 exit 3 으로 실패한다.

   사용법:
       python scripts/verify_cache_hit.py --reports-dir agent-data/reports --limit 20
       python scripts/verify_cache_hit.py --reports-dir agent-data/reports --limit 20 \\
           --snapshot-out /tmp/before.json
       python scripts/verify_cache_hit.py --reports-dir agent-data/reports --limit 20 \\
           --compare-to /tmp/before.json
       python scripts/verify_cache_hit.py --reports-dir agent-data/reports --limit 20 \\
           --min-samples 20

B. 비교 모드 (--compare-to)
   A 모드 출력을 --snapshot-out 으로 저장한 뒤, 후속 실행 시 --compare-to 로
   로드해 역할별 ratio Δ(percentage points)와 Reviewer verdict 분포 변화를
   나란히 보여준다.

C. Live-call 검증 모드 (--provider/--model)
   OpenAI 또는 GLM 에 동일한 system_prompt + user 메시지로 2회 연속 호출하고,
   2번째 호출의 `cached_tokens` 값을 출력한다.

   사용법:
       python scripts/verify_cache_hit.py --provider openai --model gpt-4.1-mini
       python scripts/verify_cache_hit.py --provider glm --model glm-4.5-air

전제 (live-call 모드):
    OPENAI_API_KEY 또는 ZAI_API_KEY 가 .env 에 설정되어 있어야 한다.

해석 (live-call 모드):
    OpenAI/GLM 의 prompt caching 은 best-effort 다. 같은 prefix 라도 호출마다
    다른 replica 로 라우팅되면 cached_read 가 0 이 될 수 있다. 이 스크립트는
    2회 호출 중 어느 한 번이라도 cached_read 가 input 의 30% 또는 1024 토큰
    중 큰 값을 넘으면 prefix 가 캐시 가능한 형태로 전송되고 있다고 판정한다.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from llm.base import LLMConfig, Message  # noqa: E402


# ── 리포트 집계 모드 ──────────────────────────────────────────────────────────


_TOKEN_FIELDS = ("input", "cached_read", "output", "cached_write")


def _parse_completed_at(value) -> float | None:
    """`completed_at` 문자열을 epoch float 로 파싱. 실패하면 None."""
    if not value:
        return None
    try:
        raw = str(value).strip()
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        return datetime.fromisoformat(raw).timestamp()
    except (ValueError, TypeError):
        return None


def _load_report(path: Path) -> tuple[float, dict] | None:
    """YAML 리포트를 (sort_key, data) 로 로드. sort_key 는 completed_at → mtime."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (yaml.YAMLError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    ts = _parse_completed_at(data.get("completed_at"))
    if ts is None:
        try:
            ts = path.stat().st_mtime
        except OSError:
            return None
    return (ts, data)


def _task_key(data: dict, fname: str) -> str:
    """리포트의 task_id 를 키로, 없으면 파일명 stem 으로 fallback."""
    if isinstance(data, dict):
        tid = str(data.get("task_id") or "").strip()
        if tid:
            return tid
    # fname 예: "task-001.yaml" → "task-001"
    return fname[:-5] if fname.endswith(".yaml") else fname


def analyze_cache_hit_by_role(
    reports_dir: Path, limit: int = 20
) -> dict:
    """
    최근 `limit` 개 task-*.yaml 을 `completed_at` 내림차순(ISO8601 기준, 실패 시
    mtime) 으로 정렬해서 상위 `limit` 개를 집계한다.

    반환:
      {
        "samples": <집계된 파일 수>,
        "limit":   <요청한 limit>,
        "sample_files":  [filename, ...],        # 최신순
        "sample_tasks":  [task_id, ...],         # 최신순 (sample_files 와 1:1)
        "totals":  {role: {"input": int, "cached_read": int,
                           "output": int, "cached_write": int}},
        "ratios":  {role: float | None},         # None: 분모 0
        "verdicts": {"APPROVED": n, ...},        # 히스토그램 (빠른 개요)
        "task_verdicts": {task_id: verdict},     # task-level (stability 검증)
      }

    샘플 수가 `limit` 보다 적어도 반환한다. 호출자가 `samples < limit` 을 보고
    경고·실패를 결정한다.
    """
    loaded: list[tuple[float, dict, str]] = []
    for f in reports_dir.glob("task-*.yaml"):
        r = _load_report(f)
        if r is not None:
            loaded.append((r[0], r[1], f.name))
    loaded.sort(key=lambda x: x[0], reverse=True)
    loaded = loaded[:limit]

    totals: dict[str, dict[str, int]] = {}
    verdicts: dict[str, int] = {}
    task_verdicts: dict[str, str] = {}
    sample_files: list[str] = []
    sample_tasks: list[str] = []
    samples = 0

    for _, data, fname in loaded:
        tu = data.get("token_usage") if isinstance(data, dict) else None
        if not isinstance(tu, dict):
            continue
        samples += 1
        sample_files.append(fname)
        task_id = _task_key(data, fname)
        sample_tasks.append(task_id)

        for role, v in tu.items():
            if not isinstance(v, dict):
                continue
            agg = totals.setdefault(role, {k: 0 for k in _TOKEN_FIELDS})
            for key in _TOKEN_FIELDS:
                agg[key] += int(v.get(key, 0) or 0)

        metrics = data.get("metrics") if isinstance(data, dict) else None
        verdict = ""
        if isinstance(metrics, dict):
            verdict = str(metrics.get("reviewer_verdict") or "").strip()
        if not verdict:
            verdict = "(unset)"
        verdicts[verdict] = verdicts.get(verdict, 0) + 1
        task_verdicts[task_id] = verdict

    ratios: dict[str, float | None] = {}
    for role, agg in totals.items():
        denom = agg["input"] + agg["cached_read"]
        ratios[role] = (agg["cached_read"] / denom) if denom > 0 else None

    return {
        "samples": samples,
        "limit": limit,
        "sample_files": sample_files,
        "sample_tasks": sample_tasks,
        "totals": totals,
        "ratios": ratios,
        "verdicts": verdicts,
        "task_verdicts": task_verdicts,
    }


# ── 출력: 단일 측정 ────────────────────────────────────────────────────────────


def _status(ratio: float | None) -> str:
    if ratio is None:
        return "n/a"
    if ratio < 0.10:
        return "⚠️ LOW"
    if ratio < 0.30:
        return "🟡 MID"
    return "✅ OK"


def _fmt_ratio(ratio: float | None) -> str:
    return "n/a" if ratio is None else f"{ratio * 100:.1f}%"


def print_report(analysis: dict) -> None:
    """마크다운 테이블 형식으로 단일 측정 리포트를 표준출력에 기록."""
    samples = analysis["samples"]
    limit = analysis["limit"]
    totals = analysis["totals"]
    ratios = analysis["ratios"]
    verdicts = analysis.get("verdicts") or {}

    if samples == 0:
        print(
            "최근 리포트 없음 (agent-data/reports 에 task-*.yaml 이 없거나 "
            "모두 파싱 실패 / token_usage 부재)."
        )
        return

    print(f"## Cache Hit Ratio by Role (samples={samples}, limit={limit})")
    print()
    print("| Role | Input | Cached Read | Hit Ratio | Status |")
    print("|------|------:|------------:|----------:|--------|")
    for role in sorted(totals.keys()):
        agg = totals[role]
        ratio = ratios.get(role)
        print(
            f"| {role} | {agg['input']} | {agg['cached_read']} | "
            f"{_fmt_ratio(ratio)} | {_status(ratio)} |"
        )

    low = [r for r, v in ratios.items() if v is not None and v < 0.30]
    if low:
        print()
        print(f"Roles with < 30% ratio: {', '.join(sorted(low))}")

    if verdicts:
        print()
        print("## Reviewer Verdict 분포")
        print()
        print("| Verdict | Count |")
        print("|---------|------:|")
        for v in sorted(verdicts):
            print(f"| {v} | {verdicts[v]} |")


# ── 출력: 비교 ────────────────────────────────────────────────────────────────


def _role_status_delta(role: str, before: float | None, after: float | None) -> str:
    """역할별 Δ 판정. Reviewer 는 개선 강조, 다른 역할은 하락 경고."""
    if before is None or after is None:
        return "n/a"
    delta_pp = (after - before) * 100
    if role == "reviewer":
        if delta_pp > 5:
            return "✅ IMPROVED"
        if delta_pp < -5:
            return "⚠️ REGRESSED"
        return "= SAME"
    # 다른 역할: 하락만 경고 (회귀 가드)
    if delta_pp < -5:
        return "⚠️ REGRESSED"
    return "= OK"


def _task_verdict_status(before: str | None, after: str | None) -> str:
    """태스크 단위 verdict 상태 레이블.
    - STABLE   : 양쪽 모두 존재, verdict 동일 (→ 같은 판정 유지)
    - FLIPPED  : 양쪽 모두 존재, verdict 다름 (→ 판정이 바뀜)
    - NEW      : after 에만 존재 (새로 실행된 태스크)
    - DROPPED  : before 에만 존재 (샘플 window 에서 밀려남)
    """
    if before is None and after is None:
        return "NONE"
    if before is None:
        return "NEW"
    if after is None:
        return "DROPPED"
    return "STABLE" if before == after else "FLIPPED"


def compare_analyses(before: dict, after: dict) -> dict:
    """
    두 분석 결과의 비교 결과:
      - role 별 ratio Δ(pp)
      - verdict 히스토그램 Δ (빠른 요약)
      - **태스크 단위 verdict 안정성**: task_id 별 STABLE/FLIPPED/NEW/DROPPED
        → 체크리스트 F (APPROVED/CHANGES_REQUESTED 유지 여부) 충족용
    """
    roles = sorted(set(before.get("ratios", {})) | set(after.get("ratios", {})))
    role_rows: list[dict] = []
    for role in roles:
        b = before.get("ratios", {}).get(role)
        a = after.get("ratios", {}).get(role)
        delta_pp = None if (a is None or b is None) else (a - b) * 100
        role_rows.append({
            "role": role,
            "before": b,
            "after": a,
            "delta_pp": delta_pp,
            "status": _role_status_delta(role, b, a),
        })

    vset = sorted(set(before.get("verdicts", {})) | set(after.get("verdicts", {})))
    verdict_rows: list[dict] = []
    for v in vset:
        bc = int(before.get("verdicts", {}).get(v, 0))
        ac = int(after.get("verdicts", {}).get(v, 0))
        verdict_rows.append({"verdict": v, "before": bc, "after": ac, "delta": ac - bc})

    # 태스크 단위 verdict diff
    before_tv = before.get("task_verdicts") or {}
    after_tv = after.get("task_verdicts") or {}
    all_task_ids = sorted(set(before_tv) | set(after_tv))
    task_verdict_changes: list[dict] = []
    for tid in all_task_ids:
        bv = before_tv.get(tid)
        av = after_tv.get(tid)
        status = _task_verdict_status(bv, av)
        if status == "NONE":
            continue
        task_verdict_changes.append({
            "task_id": tid, "before": bv, "after": av, "status": status,
        })

    summary = {"common": 0, "stable": 0, "flipped": 0, "new": 0, "dropped": 0}
    for r in task_verdict_changes:
        s = r["status"].lower()
        if s in ("stable", "flipped"):
            summary["common"] += 1
        summary[s] = summary.get(s, 0) + 1

    return {
        "samples_before": before.get("samples", 0),
        "samples_after": after.get("samples", 0),
        "limit_before": before.get("limit", 0),
        "limit_after": after.get("limit", 0),
        "role_rows": role_rows,
        "verdict_rows": verdict_rows,
        "task_verdict_changes": task_verdict_changes,
        "task_verdict_summary": summary,
    }


def print_comparison_report(cmp: dict) -> None:
    sb, sa = cmp["samples_before"], cmp["samples_after"]
    lb, la = cmp["limit_before"], cmp["limit_after"]
    print(
        f"## Cache Hit Ratio — Before vs After "
        f"(before samples={sb}/limit={lb}, after samples={sa}/limit={la})"
    )
    if sb != sa:
        print()
        print(
            f"> ⚠️ 샘플 수가 다릅니다 (before={sb}, after={sa}). "
            f"동일 기준 비교가 아닐 수 있으니 해석에 주의."
        )
    print()
    print("| Role | Before | After | Δ (pp) | Status |")
    print("|------|-------:|------:|-------:|--------|")
    for row in cmp["role_rows"]:
        dpp = row["delta_pp"]
        dpp_str = "n/a" if dpp is None else f"{dpp:+.1f}"
        print(
            f"| {row['role']} | {_fmt_ratio(row['before'])} | "
            f"{_fmt_ratio(row['after'])} | {dpp_str} | {row['status']} |"
        )

    regressed = [r["role"] for r in cmp["role_rows"] if "REGRESSED" in r["status"]]
    if regressed:
        print()
        print(f"⚠️ Regressed roles: {', '.join(regressed)}")

    if cmp["verdict_rows"]:
        print()
        print("## Reviewer Verdict 분포 — Before vs After")
        print()
        print("| Verdict | Before | After | Δ |")
        print("|---------|-------:|------:|---:|")
        for row in cmp["verdict_rows"]:
            d = row["delta"]
            print(
                f"| {row['verdict']} | {row['before']} | "
                f"{row['after']} | {d:+d} |"
            )

    # 태스크 단위 verdict 안정성 — 히스토그램이 같아도 per-task flip 을 잡는다
    changes = cmp.get("task_verdict_changes") or []
    summary = cmp.get("task_verdict_summary") or {}
    if changes:
        common = summary.get("common", 0)
        stable = summary.get("stable", 0)
        flipped = summary.get("flipped", 0)
        new_ct = summary.get("new", 0)
        dropped = summary.get("dropped", 0)
        stability = f"{stable}/{common}" if common else "n/a"
        print()
        print(
            f"## Reviewer Verdict 태스크 단위 안정성 "
            f"(공통 태스크={common}, 안정={stability})"
        )
        print()
        print(f"- STABLE  (verdict 유지)     : {stable}")
        print(f"- FLIPPED (verdict 변경)     : {flipped}")
        print(f"- NEW     (after 에만 존재)  : {new_ct}")
        print(f"- DROPPED (before 에만 존재) : {dropped}")

        if flipped:
            flipped_rows = [r for r in changes if r["status"] == "FLIPPED"]
            print()
            print("### Flipped verdicts (same task, different verdict)")
            print()
            print("| Task | Before | After |")
            print("|------|--------|-------|")
            for r in flipped_rows:
                print(f"| {r['task_id']} | {r['before']} | {r['after']} |")
            print()
            print(
                "⚠️ 위 태스크들은 Reviewer 판정이 바뀌었습니다. "
                "동일 입력에 대한 판정 안정성을 깨뜨리는 변화인지 검토하세요."
            )


# ── Live-call 검증 모드 ───────────────────────────────────────────────────────


_PADDED_SYSTEM = (
    "당신은 로컬 파일 시스템에서 작동하는 코딩 에이전트입니다. "
    "다음 원칙을 철저히 따르세요:\n\n"
    + "\n".join(
        f"- 원칙 {i}: 항상 명확한 도구 호출을 우선하고, 불필요한 추측을 피하고, "
        "결과를 요약할 때는 간결한 한국어로 사용자에게 전달합니다. "
        "도구 실행 결과가 나오기 전에는 결론을 내리지 말고, "
        "에러 발생 시 원인 분석 후 다른 방식으로 재시도하세요."
        for i in range(1, 60)
    )
)

_USER_MSG = (
    "간단한 덧셈 함수 `add(a, b)`를 Python으로 작성하고 설명해주세요. "
    "한두 줄 정도의 답으로 충분합니다."
)


def _make_client(provider: str, model: str):
    cfg = LLMConfig(
        model=model, temperature=0.0, max_tokens=256,
        system_prompt=_PADDED_SYSTEM,
    )
    if provider == "openai":
        from llm.openai_client import OpenaiClient
        return OpenaiClient(cfg)
    if provider == "glm":
        from llm.glm_client import GlmClient
        return GlmClient(cfg)
    raise ValueError(f"지원하지 않는 provider: {provider} (openai | glm)")


def _one_call(client, label: str) -> tuple[int, int]:
    t0 = time.perf_counter()
    resp = client.chat([Message(role="user", content=_USER_MSG)])
    elapsed = (time.perf_counter() - t0) * 1000
    print(
        f"[{label}] input={resp.input_tokens:>6}  "
        f"output={resp.output_tokens:>4}  "
        f"cached_read={resp.cached_read_tokens:>6}  "
        f"({elapsed:.0f}ms)"
    )
    return resp.input_tokens, resp.cached_read_tokens


def _run_live(provider: str, model: str) -> int:
    print(f"▶ provider={provider}  model={model}")
    print(
        f"  system_prompt 길이: {len(_PADDED_SYSTEM)} chars "
        f"(~{len(_PADDED_SYSTEM) // 4} tokens 추정)"
    )
    print()

    client = _make_client(provider, model)
    in1, cached1 = _one_call(client, "1회차")
    in2, cached2 = _one_call(client, "2회차")

    print()
    print("─" * 60)
    best = max(cached1, cached2)
    input_ref = max(in1, in2, 1)
    threshold = max(1024, int(input_ref * 0.3))
    if best >= threshold:
        ratio = best / input_ref * 100
        print(
            f"✅ 캐시 가능: 2회 호출 중 최대 cached={best} "
            f"({ratio:.1f}% of input). prefix 가 안정적으로 캐시되고 있음."
        )
        return 0
    print(
        f"⚠️ 캐시 미적중 또는 불충분: max(cached1={cached1}, cached2={cached2})"
        f" < threshold({threshold}).\n"
        "  가능한 원인:\n"
        "    1. prefix 가 1024 토큰보다 짧음 (OpenAI 임계값)\n"
        "    2. system_prompt 또는 첫 user 메시지에 가변 요소 포함 "
        "(timestamp/UUID/random)\n"
        "    3. dict 키 순서/JSON 직렬화 결과가 호출마다 다름\n"
        "    4. provider 가 prompt caching 을 지원하지 않거나 "
        "해당 모델에서 비활성화됨"
    )
    return 1


# ── CLI ──────────────────────────────────────────────────────────────────────


def _emit(analysis: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(analysis, indent=2, ensure_ascii=False))
    else:
        print_report(analysis)


def _run_analyze(args: argparse.Namespace) -> int:
    analysis = analyze_cache_hit_by_role(args.reports_dir, args.limit)
    samples = analysis["samples"]
    limit = analysis["limit"]

    if args.compare_to is not None:
        try:
            before = json.loads(args.compare_to.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            print(f"⚠️ --compare-to 파일 로드 실패: {e}", file=sys.stderr)
            return 4
        cmp = compare_analyses(before, analysis)
        if args.json:
            print(json.dumps({"before": before, "after": analysis, "compare": cmp},
                             indent=2, ensure_ascii=False))
        else:
            print_comparison_report(cmp)
    else:
        _emit(analysis, args.json)

    if args.snapshot_out is not None:
        args.snapshot_out.write_text(
            json.dumps(analysis, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"\n📝 snapshot 저장: {args.snapshot_out}", file=sys.stderr)

    if samples == 0:
        print(
            "⚠️ 수집된 샘플이 없습니다. agent-data/reports 에 task-*.yaml 이 "
            "존재하는지, token_usage 필드가 기록됐는지 확인하세요.",
            file=sys.stderr,
        )
        return 2

    if samples < limit:
        print(
            f"⚠️ 요청 limit={limit} 에 비해 수집된 샘플은 {samples} 개입니다. "
            f"'최근 {limit}개' 기준 비교가 성립하지 않을 수 있습니다.",
            file=sys.stderr,
        )

    if args.min_samples > 0 and samples < args.min_samples:
        print(
            f"❌ --min-samples={args.min_samples} 에 미달 (samples={samples}). "
            f"충분한 실태스크를 실행한 후 다시 시도하세요.",
            file=sys.stderr,
        )
        return 3

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--reports-dir", type=Path, default=None,
        help="agent-data/reports 디렉터리 (지정 시 리포트 집계 모드)",
    )
    parser.add_argument(
        "--limit", type=int, default=20,
        help="집계에 사용할 최근 리포트 개수 (default: 20)",
    )
    parser.add_argument(
        "--min-samples", type=int, default=0,
        help="수집 샘플이 이 값보다 적으면 exit 3 (default: 0 = 실패 없음)",
    )
    parser.add_argument(
        "--snapshot-out", type=Path, default=None,
        help="분석 결과를 JSON 으로 저장할 파일 (before/after 비교용)",
    )
    parser.add_argument(
        "--compare-to", type=Path, default=None,
        help="이전 snapshot JSON 과 비교 (role Δ + verdict 분포 변화)",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="리포트를 JSON 형식으로 stdout 에 출력",
    )
    parser.add_argument(
        "--provider", choices=["openai", "glm"],
        help="live-call 모드 provider (openai | glm)",
    )
    parser.add_argument(
        "--model",
        help="live-call 모드 모델 이름 (예: gpt-4.1-mini, glm-4.5-air)",
    )
    args = parser.parse_args()

    if args.reports_dir is not None:
        if not args.reports_dir.is_dir():
            parser.error(
                f"--reports-dir 경로가 디렉터리가 아님: {args.reports_dir}"
            )
        return _run_analyze(args)

    if not args.provider or not args.model:
        parser.error(
            "--reports-dir 를 지정하거나, 또는 (--provider --model) 을 함께 지정하세요."
        )
    return _run_live(args.provider, args.model)


if __name__ == "__main__":
    raise SystemExit(main())
