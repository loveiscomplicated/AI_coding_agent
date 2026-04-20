"""
tests/test_verify_cache_hit.py

scripts/verify_cache_hit.py 의 리포트 집계/비교 로직 단위 테스트.

핵심 불변식:
  1. 정렬은 `completed_at` 기준 내림차순 (없으면 mtime fallback).
  2. 수집된 sample_files 는 최신순 + 집계에 실제 포함된 파일만.
  3. Reviewer verdict 분포를 metrics.reviewer_verdict 에서 수집.
  4. compare_analyses 가 role Δ(pp) 와 verdict 분포 Δ 를 정확히 계산.
  5. samples < limit 일 때 호출자가 감지할 수 있도록 samples/limit 반환.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import time
from pathlib import Path

import pytest
import yaml

# scripts/verify_cache_hit.py 는 CLI 스크립트로 작성돼 있어 모듈로 import 하려면
# 직접 경로 지정이 필요하다.
_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

_spec = importlib.util.spec_from_file_location(
    "verify_cache_hit", _REPO / "scripts" / "verify_cache_hit.py",
)
vch = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vch)


# ── Helpers: fake report YAML 생성 ────────────────────────────────────────────


def _write_report(
    dir_: Path,
    task_id: str,
    completed_at: str | None,
    verdict: str,
    reviewer: dict | None = None,
    test_writer: dict | None = None,
    implementer: dict | None = None,
    token_usage: dict | None = None,
) -> Path:
    """한 개의 task-*.yaml 파일을 생성해 경로를 반환한다."""
    if token_usage is None:
        token_usage = {}
        if reviewer is not None:
            token_usage["reviewer"] = reviewer
        if test_writer is not None:
            token_usage["test_writer"] = test_writer
        if implementer is not None:
            token_usage["implementer"] = implementer

    data: dict = {
        "task_id": task_id,
        "title": f"{task_id} title",
        "status": "COMPLETED",
        "metrics": {"reviewer_verdict": verdict},
        "token_usage": token_usage,
    }
    if completed_at is not None:
        data["completed_at"] = completed_at

    # analyze_cache_hit_by_role 가 glob("task-*.yaml") 로 스캔하므로 prefix 강제.
    fname = task_id if task_id.startswith("task-") else f"task-{task_id}"
    path = dir_ / f"{fname}.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=True), encoding="utf-8")
    return path


def _tu(input_: int, cached_read: int, output: int = 0, cached_write: int = 0) -> dict:
    return {
        "input": input_,
        "cached_read": cached_read,
        "output": output,
        "cached_write": cached_write,
    }


# ── 1) 정렬: completed_at 기준 ────────────────────────────────────────────────


class TestCompletedAtSort:
    def test_sorts_by_completed_at_desc_not_mtime(self, tmp_path):
        """mtime 이 오래된 파일이라도 completed_at 이 최신이면 앞에 온다."""
        # 먼저 쓰는 파일이 mtime 상 오래된다 (+ 약간의 sleep 으로 간격 확보)
        p_old_mtime_new_ts = _write_report(
            tmp_path, "task-001",
            completed_at="2026-04-20T10:00:00+00:00",
            verdict="APPROVED",
            reviewer=_tu(100, 900),
        )
        time.sleep(0.02)
        p_new_mtime_old_ts = _write_report(
            tmp_path, "task-002",
            completed_at="2026-04-10T10:00:00+00:00",
            verdict="APPROVED",
            reviewer=_tu(100, 900),
        )
        time.sleep(0.02)
        p_newest = _write_report(
            tmp_path, "task-003",
            completed_at="2026-04-25T10:00:00+00:00",
            verdict="APPROVED",
            reviewer=_tu(100, 900),
        )

        result = vch.analyze_cache_hit_by_role(tmp_path, limit=3)
        # 최신(=2026-04-25) → 중간(=2026-04-20) → 오래된(=2026-04-10)
        assert result["sample_files"] == [
            p_newest.name, p_old_mtime_new_ts.name, p_new_mtime_old_ts.name,
        ]

    def test_falls_back_to_mtime_when_completed_at_missing(self, tmp_path):
        """completed_at 필드가 없으면 mtime 으로 정렬."""
        _write_report(
            tmp_path, "task-A", completed_at=None, verdict="APPROVED",
            reviewer=_tu(100, 900),
        )
        time.sleep(0.02)
        _write_report(
            tmp_path, "task-B", completed_at=None, verdict="APPROVED",
            reviewer=_tu(100, 900),
        )
        time.sleep(0.02)
        _write_report(
            tmp_path, "task-C", completed_at=None, verdict="APPROVED",
            reviewer=_tu(100, 900),
        )
        result = vch.analyze_cache_hit_by_role(tmp_path, limit=3)
        # mtime 내림차순: C → B → A
        assert result["sample_files"] == ["task-C.yaml", "task-B.yaml", "task-A.yaml"]

    def test_parse_completed_at_accepts_z_suffix(self):
        """ISO8601 Z (UTC) 접미사도 파싱되어야 한다."""
        ts_z = vch._parse_completed_at("2026-04-20T10:00:00Z")
        ts_off = vch._parse_completed_at("2026-04-20T10:00:00+00:00")
        assert ts_z is not None and ts_off is not None
        assert abs(ts_z - ts_off) < 1e-6

    def test_parse_completed_at_invalid_returns_none(self):
        assert vch._parse_completed_at("not a timestamp") is None
        assert vch._parse_completed_at("") is None
        assert vch._parse_completed_at(None) is None


# ── 2) samples < limit 검출 ───────────────────────────────────────────────────


class TestSampleShortfall:
    def test_samples_less_than_limit_returned_honestly(self, tmp_path):
        """파일이 limit 보다 적으면 samples=N, limit=20 그대로 반환."""
        _write_report(
            tmp_path, "task-001",
            completed_at="2026-04-20T10:00:00+00:00",
            verdict="APPROVED", reviewer=_tu(100, 900),
        )
        result = vch.analyze_cache_hit_by_role(tmp_path, limit=20)
        assert result["samples"] == 1
        assert result["limit"] == 20
        assert len(result["sample_files"]) == 1

    def test_zero_samples_when_dir_empty(self, tmp_path):
        result = vch.analyze_cache_hit_by_role(tmp_path, limit=20)
        assert result["samples"] == 0
        assert result["totals"] == {}
        assert result["ratios"] == {}
        assert result["sample_files"] == []

    def test_limit_truncates_when_more_files_than_limit(self, tmp_path):
        for i in range(5):
            _write_report(
                tmp_path, f"task-{i:03d}",
                completed_at=f"2026-04-{20 + i:02d}T10:00:00+00:00",
                verdict="APPROVED", reviewer=_tu(100, 900),
            )
        result = vch.analyze_cache_hit_by_role(tmp_path, limit=3)
        assert result["samples"] == 3
        assert result["limit"] == 3
        # 최신 3개 (task-004, task-003, task-002)
        assert result["sample_files"] == ["task-004.yaml", "task-003.yaml", "task-002.yaml"]


# ── 3) verdict 분포 수집 ──────────────────────────────────────────────────────


class TestVerdictCollection:
    def test_collects_reviewer_verdicts_from_metrics(self, tmp_path):
        _write_report(tmp_path, "t1",
                      completed_at="2026-04-20T10:00:00+00:00",
                      verdict="APPROVED", reviewer=_tu(100, 900))
        _write_report(tmp_path, "t2",
                      completed_at="2026-04-21T10:00:00+00:00",
                      verdict="APPROVED", reviewer=_tu(100, 900))
        _write_report(tmp_path, "t3",
                      completed_at="2026-04-22T10:00:00+00:00",
                      verdict="CHANGES_REQUESTED", reviewer=_tu(100, 900))
        _write_report(tmp_path, "t4",
                      completed_at="2026-04-23T10:00:00+00:00",
                      verdict="APPROVED_WITH_SUGGESTIONS", reviewer=_tu(100, 900))

        result = vch.analyze_cache_hit_by_role(tmp_path, limit=20)
        assert result["verdicts"] == {
            "APPROVED": 2,
            "CHANGES_REQUESTED": 1,
            "APPROVED_WITH_SUGGESTIONS": 1,
        }

    def test_empty_verdict_is_bucketed_as_unset(self, tmp_path):
        _write_report(tmp_path, "t1",
                      completed_at="2026-04-20T10:00:00+00:00",
                      verdict="", reviewer=_tu(100, 900))
        result = vch.analyze_cache_hit_by_role(tmp_path, limit=20)
        assert result["verdicts"] == {"(unset)": 1}

    def test_task_verdicts_maps_task_id_to_verdict(self, tmp_path):
        """histogram 과 별개로 task_id 기반 mapping 도 수집돼야 한다 — 이게
        있어야 before/after 가 '같은 태스크에서' 판정이 유지됐는지 검증 가능."""
        _write_report(tmp_path, "001",
                      completed_at="2026-04-20T10:00:00+00:00",
                      verdict="APPROVED", reviewer=_tu(100, 900))
        _write_report(tmp_path, "002",
                      completed_at="2026-04-21T10:00:00+00:00",
                      verdict="CHANGES_REQUESTED", reviewer=_tu(100, 900))
        result = vch.analyze_cache_hit_by_role(tmp_path, limit=20)
        # task_id 는 YAML 의 task_id 필드 그대로 ("001", "002")
        assert result["task_verdicts"] == {
            "001": "APPROVED",
            "002": "CHANGES_REQUESTED",
        }
        # sample_tasks 는 최신순
        assert result["sample_tasks"] == ["002", "001"]

    def test_task_verdicts_falls_back_to_filename_when_task_id_missing(self, tmp_path):
        """YAML 에 task_id 필드가 없으면 파일명 stem 을 키로."""
        data = {
            "title": "x",
            "completed_at": "2026-04-20T10:00:00+00:00",
            "metrics": {"reviewer_verdict": "APPROVED"},
            "token_usage": {"reviewer": _tu(100, 900)},
        }
        (tmp_path / "task-xyz.yaml").write_text(
            yaml.safe_dump(data), encoding="utf-8",
        )
        result = vch.analyze_cache_hit_by_role(tmp_path, limit=20)
        assert result["task_verdicts"] == {"task-xyz": "APPROVED"}


# ── 4) ratio 계산 & 잘못된 데이터 스킵 ────────────────────────────────────────


class TestAggregation:
    def test_ratio_computed_from_sums(self, tmp_path):
        _write_report(tmp_path, "t1",
                      completed_at="2026-04-20T10:00:00+00:00",
                      verdict="APPROVED",
                      reviewer=_tu(100, 900),      # ratio_local = 900/1000 = 0.9
                      test_writer=_tu(200, 200))   # ratio_local = 0.5
        _write_report(tmp_path, "t2",
                      completed_at="2026-04-21T10:00:00+00:00",
                      verdict="APPROVED",
                      reviewer=_tu(300, 100),      # pooled reviewer = 1000/1400 = 0.714
                      test_writer=_tu(200, 400))   # pooled test_writer = 600/1000 = 0.6
        result = vch.analyze_cache_hit_by_role(tmp_path, limit=20)
        assert result["totals"]["reviewer"] == {
            "input": 400, "cached_read": 1000, "output": 0, "cached_write": 0,
        }
        assert abs(result["ratios"]["reviewer"] - 1000 / 1400) < 1e-9
        assert abs(result["ratios"]["test_writer"] - 600 / 1000) < 1e-9

    def test_none_ratio_when_both_zero(self, tmp_path):
        _write_report(tmp_path, "t1",
                      completed_at="2026-04-20T10:00:00+00:00",
                      verdict="APPROVED",
                      reviewer=_tu(0, 0))
        result = vch.analyze_cache_hit_by_role(tmp_path, limit=20)
        assert result["ratios"]["reviewer"] is None

    def test_skips_yaml_without_token_usage(self, tmp_path):
        # token_usage 누락한 파일
        (tmp_path / "task-bad.yaml").write_text(
            yaml.safe_dump({
                "task_id": "task-bad",
                "completed_at": "2026-04-20T10:00:00+00:00",
                "metrics": {"reviewer_verdict": "APPROVED"},
            }), encoding="utf-8",
        )
        _write_report(tmp_path, "task-good",
                      completed_at="2026-04-21T10:00:00+00:00",
                      verdict="APPROVED", reviewer=_tu(100, 900))
        result = vch.analyze_cache_hit_by_role(tmp_path, limit=20)
        assert result["samples"] == 1
        assert result["sample_files"] == ["task-good.yaml"]

    def test_skips_corrupted_yaml(self, tmp_path):
        (tmp_path / "task-corrupt.yaml").write_text(
            "this is :: not valid\n  - yaml\n    :broken", encoding="utf-8",
        )
        _write_report(tmp_path, "task-good",
                      completed_at="2026-04-21T10:00:00+00:00",
                      verdict="APPROVED", reviewer=_tu(100, 900))
        result = vch.analyze_cache_hit_by_role(tmp_path, limit=20)
        assert result["samples"] == 1


# ── 5) compare_analyses ──────────────────────────────────────────────────────


class TestCompareAnalyses:
    def _mk_analysis(self, ratios: dict, verdicts: dict,
                     samples: int = 20, limit: int = 20) -> dict:
        return {
            "samples": samples, "limit": limit,
            "sample_files": [], "totals": {},
            "ratios": ratios, "verdicts": verdicts,
        }

    def test_reviewer_improvement_is_marked(self):
        before = self._mk_analysis(
            ratios={"reviewer": 0.03, "test_writer": 0.8, "implementer": 0.8},
            verdicts={"APPROVED": 15, "CHANGES_REQUESTED": 5},
        )
        after = self._mk_analysis(
            ratios={"reviewer": 0.42, "test_writer": 0.8, "implementer": 0.8},
            verdicts={"APPROVED": 15, "CHANGES_REQUESTED": 5},
        )
        cmp = vch.compare_analyses(before, after)
        rev = next(r for r in cmp["role_rows"] if r["role"] == "reviewer")
        assert rev["status"] == "✅ IMPROVED"
        assert abs(rev["delta_pp"] - 39.0) < 0.01

    def test_other_role_regression_is_flagged(self):
        before = self._mk_analysis(
            ratios={"reviewer": 0.5, "test_writer": 0.80, "implementer": 0.80},
            verdicts={"APPROVED": 20},
        )
        after = self._mk_analysis(
            ratios={"reviewer": 0.5, "test_writer": 0.60, "implementer": 0.80},
            verdicts={"APPROVED": 20},
        )
        cmp = vch.compare_analyses(before, after)
        tw = next(r for r in cmp["role_rows"] if r["role"] == "test_writer")
        assert tw["status"] == "⚠️ REGRESSED"
        impl = next(r for r in cmp["role_rows"] if r["role"] == "implementer")
        assert impl["status"] == "= OK"

    def test_verdict_distribution_delta_computed(self):
        before = self._mk_analysis(
            ratios={},
            verdicts={"APPROVED": 12, "CHANGES_REQUESTED": 6, "ERROR": 2},
        )
        after = self._mk_analysis(
            ratios={},
            verdicts={"APPROVED": 15, "CHANGES_REQUESTED": 4, "APPROVED_WITH_SUGGESTIONS": 1},
        )
        cmp = vch.compare_analyses(before, after)
        by_v = {r["verdict"]: r for r in cmp["verdict_rows"]}
        assert by_v["APPROVED"]["delta"] == 3
        assert by_v["CHANGES_REQUESTED"]["delta"] == -2
        assert by_v["ERROR"]["delta"] == -2
        assert by_v["APPROVED_WITH_SUGGESTIONS"]["delta"] == 1

    def test_missing_role_in_one_side_marked_na(self):
        before = self._mk_analysis(ratios={"reviewer": 0.5}, verdicts={})
        after = self._mk_analysis(
            ratios={"reviewer": 0.5, "implementer": 0.7}, verdicts={},
        )
        cmp = vch.compare_analyses(before, after)
        impl = next(r for r in cmp["role_rows"] if r["role"] == "implementer")
        assert impl["before"] is None
        assert impl["delta_pp"] is None
        assert impl["status"] == "n/a"


class TestTaskLevelVerdictCompare:
    """compare_analyses 가 태스크 단위 verdict 안정성을 정확히 판정하는지.

    체크리스트 F: 'APPROVED 였던 태스크가 여전히 APPROVED 인가?' 를 답하기
    위해 histogram 이 아닌 task_id 기반 diff 가 필요하다. 아래 첫 번째 테스트는
    '히스토그램은 동일한데 per-task flip 이 일어난 경우' 를 확실히 잡아낸다.
    """

    def _mk(self, task_verdicts: dict, verdicts: dict | None = None,
            ratios: dict | None = None) -> dict:
        if verdicts is None:
            verdicts = {}
            for v in task_verdicts.values():
                verdicts[v] = verdicts.get(v, 0) + 1
        return {
            "samples": len(task_verdicts), "limit": len(task_verdicts),
            "sample_files": [], "sample_tasks": list(task_verdicts.keys()),
            "totals": {}, "ratios": ratios or {},
            "verdicts": verdicts, "task_verdicts": task_verdicts,
        }

    def test_stable_tasks_counted_when_verdict_unchanged(self):
        before = self._mk({"task-001": "APPROVED", "task-002": "APPROVED"})
        after = self._mk({"task-001": "APPROVED", "task-002": "APPROVED"})
        cmp = vch.compare_analyses(before, after)
        assert cmp["task_verdict_summary"]["common"] == 2
        assert cmp["task_verdict_summary"]["stable"] == 2
        assert cmp["task_verdict_summary"]["flipped"] == 0
        # 모든 항목 status=STABLE
        assert all(r["status"] == "STABLE" for r in cmp["task_verdict_changes"])

    def test_flipped_detected_when_same_task_different_verdict(self):
        before = self._mk({"task-001": "APPROVED"})
        after = self._mk({"task-001": "CHANGES_REQUESTED"})
        cmp = vch.compare_analyses(before, after)
        assert cmp["task_verdict_summary"]["flipped"] == 1
        flip = cmp["task_verdict_changes"][0]
        assert flip["task_id"] == "task-001"
        assert flip["before"] == "APPROVED"
        assert flip["after"] == "CHANGES_REQUESTED"
        assert flip["status"] == "FLIPPED"

    def test_new_and_dropped_are_classified(self):
        before = self._mk({"task-001": "APPROVED", "task-002": "APPROVED"})
        after = self._mk({"task-002": "APPROVED", "task-003": "APPROVED"})
        cmp = vch.compare_analyses(before, after)
        s = cmp["task_verdict_summary"]
        assert s["stable"] == 1       # task-002
        assert s["dropped"] == 1      # task-001 (before only)
        assert s["new"] == 1          # task-003 (after only)
        by_id = {r["task_id"]: r for r in cmp["task_verdict_changes"]}
        assert by_id["task-001"]["status"] == "DROPPED"
        assert by_id["task-003"]["status"] == "NEW"

    def test_same_histogram_different_task_assignment_detected_as_flip(self):
        """핵심 회귀 가드: 히스토그램은 동일하지만 per-task 판정이 바뀐 경우.

        before: t1=APPROVED, t2=CHANGES_REQUESTED
        after : t1=CHANGES_REQUESTED, t2=APPROVED
        → histogram 은 {APPROVED:1, CHANGES_REQUESTED:1} 로 동일
        → 하지만 두 태스크의 개별 판정이 뒤집힘 = FLIPPED 2건
        """
        before = self._mk({"t1": "APPROVED", "t2": "CHANGES_REQUESTED"})
        after = self._mk({"t1": "CHANGES_REQUESTED", "t2": "APPROVED"})
        # 사전 조건 검증: 히스토그램이 실제로 같은지
        assert before["verdicts"] == after["verdicts"]
        cmp = vch.compare_analyses(before, after)
        # verdict_rows(히스토그램)는 Δ=0 — 이게 compare 에 태스크 단위 비교가
        # 꼭 필요한 이유.
        for row in cmp["verdict_rows"]:
            assert row["delta"] == 0
        # 태스크 단위로는 2건 모두 FLIPPED 로 잡혀야 한다.
        assert cmp["task_verdict_summary"]["flipped"] == 2
        assert cmp["task_verdict_summary"]["stable"] == 0

    def test_cli_compare_output_contains_task_stability_section(self, tmp_path, capsys):
        """CLI 출력에 'Verdict 태스크 단위 안정성' 섹션이 포함되는지."""
        import argparse
        import json as _json
        before = self._mk({"task-001": "APPROVED", "task-002": "CHANGES_REQUESTED"})
        before_path = tmp_path / "before.json"
        before_path.write_text(_json.dumps(before), encoding="utf-8")

        # agent-data/reports 에 task-001 은 여전히 APPROVED, task-002 는 flipped
        _write_report(
            tmp_path, "task-001",
            completed_at="2026-04-25T10:00:00+00:00",
            verdict="APPROVED", reviewer=_tu(100, 900),
        )
        _write_report(
            tmp_path, "task-002",
            completed_at="2026-04-25T11:00:00+00:00",
            verdict="APPROVED", reviewer=_tu(100, 900),
        )

        ns = argparse.Namespace(
            reports_dir=tmp_path, limit=20, min_samples=0,
            snapshot_out=None, compare_to=before_path, json=False,
        )
        rc = vch._run_analyze(ns)
        assert rc == 0
        out = capsys.readouterr().out
        assert "태스크 단위 안정성" in out
        assert "FLIPPED" in out
        # task-002 가 flipped 로 명시돼야 한다
        assert "task-002" in out


# ── 6) CLI: --min-samples / exit 코드 ─────────────────────────────────────────


class TestMinSamplesExitCode:
    """_run_analyze 의 종료 코드: 샘플이 --min-samples 미만이면 3 으로 실패."""

    def _ns(self, **overrides):
        import argparse
        base = argparse.Namespace(
            reports_dir=None, limit=20, min_samples=0,
            snapshot_out=None, compare_to=None, json=False,
        )
        for k, v in overrides.items():
            setattr(base, k, v)
        return base

    def test_exit_0_when_samples_meet_threshold(self, tmp_path, capsys):
        _write_report(tmp_path, "task-001",
                      completed_at="2026-04-20T10:00:00+00:00",
                      verdict="APPROVED", reviewer=_tu(100, 900))
        ns = self._ns(reports_dir=tmp_path, limit=20, min_samples=1)
        rc = vch._run_analyze(ns)
        assert rc == 0

    def test_exit_3_when_below_min_samples(self, tmp_path, capsys):
        _write_report(tmp_path, "task-001",
                      completed_at="2026-04-20T10:00:00+00:00",
                      verdict="APPROVED", reviewer=_tu(100, 900))
        ns = self._ns(reports_dir=tmp_path, limit=20, min_samples=5)
        rc = vch._run_analyze(ns)
        assert rc == 3
        err = capsys.readouterr().err
        assert "min-samples=5" in err and "samples=1" in err

    def test_exit_2_when_zero_samples(self, tmp_path, capsys):
        ns = self._ns(reports_dir=tmp_path, limit=20, min_samples=0)
        rc = vch._run_analyze(ns)
        assert rc == 2
        err = capsys.readouterr().err
        assert "수집된 샘플이 없습니다" in err

    def test_warns_when_samples_less_than_limit(self, tmp_path, capsys):
        _write_report(tmp_path, "task-001",
                      completed_at="2026-04-20T10:00:00+00:00",
                      verdict="APPROVED", reviewer=_tu(100, 900))
        ns = self._ns(reports_dir=tmp_path, limit=20, min_samples=0)
        rc = vch._run_analyze(ns)
        assert rc == 0
        err = capsys.readouterr().err
        assert "limit=20" in err and "1" in err

    def test_snapshot_out_writes_json_file(self, tmp_path, capsys):
        _write_report(tmp_path, "task-001",
                      completed_at="2026-04-20T10:00:00+00:00",
                      verdict="APPROVED", reviewer=_tu(100, 900))
        out = tmp_path / "before.json"
        ns = self._ns(
            reports_dir=tmp_path, limit=20, min_samples=0,
            snapshot_out=out,
        )
        rc = vch._run_analyze(ns)
        assert rc == 0
        assert out.exists()
        import json as _json
        loaded = _json.loads(out.read_text(encoding="utf-8"))
        assert loaded["samples"] == 1
        assert loaded["ratios"]["reviewer"] == 0.9

    def test_compare_to_reads_prior_snapshot(self, tmp_path, capsys):
        import json as _json
        before = {
            "samples": 20, "limit": 20, "sample_files": [],
            "totals": {}, "ratios": {"reviewer": 0.03},
            "verdicts": {"APPROVED": 15, "CHANGES_REQUESTED": 5},
        }
        before_path = tmp_path / "before.json"
        before_path.write_text(_json.dumps(before), encoding="utf-8")

        _write_report(tmp_path, "task-001",
                      completed_at="2026-04-25T10:00:00+00:00",
                      verdict="APPROVED", reviewer=_tu(100, 900))

        ns = self._ns(
            reports_dir=tmp_path, limit=20, min_samples=0,
            compare_to=before_path,
        )
        rc = vch._run_analyze(ns)
        assert rc == 0
        out = capsys.readouterr().out
        # 비교 리포트 헤더와 Δ 표시가 찍혀야 한다
        assert "Before vs After" in out
        assert "reviewer" in out
