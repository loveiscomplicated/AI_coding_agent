"""
orchestrator/intervention.py — 스켈레톤 생성 및 3회차 분기 단위 테스트

`test_intervention_fast_path.py` 가 fast-path 분류(ENV_ERROR, MAX_ITER 등)를
커버한다면, 이 파일은 LOGIC_ERROR / COLLECTION_ERROR 경로에서 LLM 을
monkeypatch 한 상태의 3회차 skeleton 주입 동작과 `_parse_skeleton_response`
의 경계조건을 검증한다.
"""

from __future__ import annotations

from orchestrator import intervention as iv
from orchestrator.intervention import (
    AnalysisResult,
    _parse_skeleton_response,
    classify_and_analyze,
    generate_skeleton_files,
)
from orchestrator.task import Task


def _task(target_files: list[str] | None = None) -> Task:
    return Task(
        id="t1",
        title="title",
        description="desc",
        acceptance_criteria=["c1"],
        target_files=target_files if target_files is not None else [],
    )


# ── _parse_skeleton_response ──────────────────────────────────────────────────


def test_parse_skeleton_response_multi_file():
    raw = (
        "===FILE: src/foo.py===\n"
        "def foo() -> None:\n"
        '    raise NotImplementedError("TODO: task t1")\n'
        "===END===\n"
        "===FILE: src/bar.py===\n"
        "def bar() -> int:\n"
        '    raise NotImplementedError("TODO: task t1")\n'
        "===END===\n"
    )
    result = _parse_skeleton_response(raw, {"src/foo.py", "src/bar.py"})
    assert set(result.keys()) == {"src/foo.py", "src/bar.py"}
    assert "def foo()" in result["src/foo.py"]
    assert "def bar()" in result["src/bar.py"]
    assert "NotImplementedError" in result["src/foo.py"]


def test_parse_skeleton_response_rejects_out_of_scope_paths():
    raw = (
        "===FILE: src/foo.py===\n"
        "def foo(): pass\n"
        "===END===\n"
        "===FILE: src/evil.py===\n"
        "os.system('rm -rf /')\n"
        "===END===\n"
    )
    result = _parse_skeleton_response(raw, {"src/foo.py"})
    assert set(result.keys()) == {"src/foo.py"}
    assert "evil" not in "".join(result.values())


def test_parse_skeleton_response_returns_empty_on_parse_fail():
    raw = "죄송합니다. 주어진 정보로는 스켈레톤을 생성할 수 없습니다."
    result = _parse_skeleton_response(raw, {"src/foo.py"})
    assert result == {}


def test_parse_skeleton_response_strips_path_whitespace():
    raw = (
        "===FILE:   src/foo.py   ===\n"
        "def foo(): pass\n"
        "===END===\n"
    )
    result = _parse_skeleton_response(raw, {"src/foo.py"})
    assert "src/foo.py" in result


# ── generate_skeleton_files ───────────────────────────────────────────────────


class _FakeResponse:
    def __init__(self, text: str):
        self.content = [{"type": "text", "text": text}]
        self.input_tokens = 10
        self.output_tokens = 20
        self.cached_read_tokens = 0
        self.cached_write_tokens = 0
        self.model = "fake-model"


class _FakeLLM:
    def __init__(self, text: str):
        self._text = text
        self.calls: list = []

    def chat(self, messages):
        self.calls.append(messages)
        return _FakeResponse(self._text)


def test_generate_skeleton_files_returns_empty_when_no_target_files():
    task = _task(target_files=[])
    skeletons, usage, log = generate_skeleton_files(task, "failure ctx")
    assert skeletons == {}
    assert usage == (0, 0, 0, 0)
    assert log == []


def test_generate_skeleton_files_parses_llm_response(monkeypatch):
    task = _task(target_files=["src/foo.py"])
    fake_llm = _FakeLLM(
        "===FILE: src/foo.py===\n"
        '"""foo summary."""\n'
        "def foo() -> None:\n"
        '    raise NotImplementedError("TODO: task t1")\n'
        "===END===\n"
    )

    def fake_resolve(task, role_models):
        return fake_llm

    monkeypatch.setattr(iv, "_resolve_intervention_llm_for_skeleton", fake_resolve)
    skeletons, usage, log = generate_skeleton_files(task, "failure ctx")

    assert "src/foo.py" in skeletons
    assert '"""foo summary."""' in skeletons["src/foo.py"]
    assert "NotImplementedError" in skeletons["src/foo.py"]
    assert usage == (10, 20, 0, 0)
    assert len(log) == 1
    assert len(fake_llm.calls) == 1


def test_generate_skeleton_files_rejects_out_of_scope_paths(monkeypatch):
    task = _task(target_files=["src/foo.py"])
    fake_llm = _FakeLLM(
        "===FILE: src/foo.py===\n"
        "def foo(): pass\n"
        "===END===\n"
        "===FILE: /etc/passwd===\n"
        "evil\n"
        "===END===\n"
    )
    monkeypatch.setattr(
        iv, "_resolve_intervention_llm_for_skeleton", lambda t, r: fake_llm
    )
    skeletons, _, _ = generate_skeleton_files(task, "ctx")
    assert set(skeletons.keys()) == {"src/foo.py"}


def test_generate_skeleton_files_rejects_partial_coverage(monkeypatch):
    task = _task(target_files=["src/foo.py", "src/bar.py"])
    fake_llm = _FakeLLM(
        "===FILE: src/foo.py===\n"
        '"""foo summary."""\n'
        "def foo() -> None:\n"
        '    raise NotImplementedError("TODO: task t1")\n'
        "===END===\n"
    )
    monkeypatch.setattr(
        iv, "_resolve_intervention_llm_for_skeleton", lambda t, r: fake_llm
    )
    skeletons, usage, log = generate_skeleton_files(task, "ctx")
    assert skeletons == {}
    assert usage == (10, 20, 0, 0)
    assert len(log) == 1


def test_generate_skeleton_files_returns_empty_on_llm_exception(monkeypatch):
    task = _task(target_files=["src/foo.py"])

    class _BadLLM:
        def chat(self, messages):
            raise RuntimeError("boom")

    monkeypatch.setattr(
        iv, "_resolve_intervention_llm_for_skeleton", lambda t, r: _BadLLM()
    )
    skeletons, usage, log = generate_skeleton_files(task, "ctx")
    assert skeletons == {}
    assert usage == (0, 0, 0, 0)
    assert log == []


def test_generate_skeleton_files_returns_empty_when_llm_unresolved(monkeypatch):
    task = _task(target_files=["src/foo.py"])
    monkeypatch.setattr(
        iv, "_resolve_intervention_llm_for_skeleton", lambda t, r: None
    )
    skeletons, _, _ = generate_skeleton_files(task, "ctx")
    assert skeletons == {}


# ── 다언어 스텁 (P2) ─────────────────────────────────────────────────────────


def test_stub_line_for_language_python():
    assert iv._stub_line_for_language("python", "t1") == \
        'raise NotImplementedError("TODO: task t1")'


def test_stub_line_for_language_kotlin():
    assert iv._stub_line_for_language("kotlin", "t1") == 'TODO("TODO: task t1")'


def test_stub_line_for_language_typescript():
    assert iv._stub_line_for_language("typescript", "t1") == \
        'throw new Error("TODO: task t1");'


def test_stub_line_for_language_go():
    assert iv._stub_line_for_language("go", "t1") == 'panic("TODO: task t1")'


def test_stub_line_for_language_java():
    assert iv._stub_line_for_language("java", "t1") == \
        'throw new UnsupportedOperationException("TODO: task t1");'


def test_stub_line_for_language_rust():
    assert iv._stub_line_for_language("rust", "t1") == \
        'unimplemented!("TODO: task t1")'


def test_stub_line_for_language_unknown_falls_back_to_python():
    assert iv._stub_line_for_language("brainfuck", "t1") == \
        'raise NotImplementedError("TODO: task t1")'


def test_stub_line_for_language_case_insensitive():
    assert iv._stub_line_for_language("KOTLIN", "t1") == 'TODO("TODO: task t1")'


def test_stub_line_for_target_file_prefers_extension_over_task_language():
    assert iv._stub_line_for_target_file("src/foo.ts", "python", "t1") == \
        'throw new Error("TODO: task t1");'
    assert iv._stub_line_for_target_file("src/foo.py", "typescript", "t1") == \
        'raise NotImplementedError("TODO: task t1")'


def test_skeleton_system_requires_docstrings():
    assert "docstring" in iv._SKELETON_SYSTEM.lower()


def test_generate_skeleton_files_passes_language_specific_stub_to_llm(monkeypatch):
    """Kotlin 태스크일 때 프롬프트에 Kotlin 스텁이 담겨 전달되어야 한다."""
    task = Task(
        id="t-kt",
        title="k",
        description="d",
        acceptance_criteria=["c"],
        target_files=["src/Foo.kt"],
        language="kotlin",
    )
    fake_llm = _FakeLLM(
        "===FILE: src/Foo.kt===\n"
        'fun foo(): Unit = TODO("TODO: task t-kt")\n'
        "===END===\n"
    )
    monkeypatch.setattr(
        iv, "_resolve_intervention_llm_for_skeleton", lambda t, r: fake_llm
    )
    generate_skeleton_files(task, "ctx")

    assert len(fake_llm.calls) == 1
    user_msg = fake_llm.calls[0][0].content
    assert 'TODO("TODO: task t-kt")' in user_msg
    assert "raise NotImplementedError" not in user_msg
    assert "docstring" in user_msg.lower()


def test_generate_skeleton_files_passes_typescript_stub(monkeypatch):
    task = Task(
        id="t-ts",
        title="k",
        description="d",
        acceptance_criteria=["c"],
        target_files=["src/foo.ts"],
        language="typescript",
    )
    fake_llm = _FakeLLM("")
    monkeypatch.setattr(
        iv, "_resolve_intervention_llm_for_skeleton", lambda t, r: fake_llm
    )
    generate_skeleton_files(task, "ctx")

    user_msg = fake_llm.calls[0][0].content
    assert 'throw new Error("TODO: task t-ts");' in user_msg


def test_generate_skeleton_files_passes_per_file_stub_table_for_mixed_targets(monkeypatch):
    task = Task(
        id="t-mixed",
        title="k",
        description="d",
        acceptance_criteria=["c"],
        target_files=["src/foo.ts", "src/bar.py", "src/main.go"],
        language="python",
    )
    fake_llm = _FakeLLM("")
    monkeypatch.setattr(
        iv, "_resolve_intervention_llm_for_skeleton", lambda t, r: fake_llm
    )
    generate_skeleton_files(task, "ctx")

    user_msg = fake_llm.calls[0][0].content
    assert '- src/foo.ts: throw new Error("TODO: task t-mixed");' in user_msg
    assert '- src/bar.py: raise NotImplementedError("TODO: task t-mixed")' in user_msg
    assert '- src/main.go: panic("TODO: task t-mixed")' in user_msg


# ── classify_and_analyze: 3회차 skeleton 주입 분기 ──────────────────────────


def _fake_analyze_factory(should_retry: bool = True):
    """analyze() 대체용 — AnalysisResult 고정 반환."""
    calls = {"count": 0}

    def fake_analyze(task, reason, attempt, previous_hints=None, role_models=None, tier=None):
        calls["count"] += 1
        return AnalysisResult(
            should_retry=should_retry,
            hint="fake hint",
            raw="fake raw",
            token_usage=(5, 7, 0, 0),
            call_log=[{"iteration": 1}],
        )

    return fake_analyze, calls


def test_classify_and_analyze_attempt3_includes_skeletons(monkeypatch):
    task = _task(target_files=["src/foo.py"])
    fake_analyze, _ = _fake_analyze_factory(should_retry=True)
    monkeypatch.setattr(iv, "analyze", fake_analyze)

    gen_calls = {"count": 0}

    def fake_gen(task, failure_context, role_models=None):
        gen_calls["count"] += 1
        return (
            {"src/foo.py": "def foo(): raise NotImplementedError()"},
            (11, 13, 0, 0),
            [{"iteration": 1, "kind": "skeleton"}],
        )

    monkeypatch.setattr(iv, "generate_skeleton_files", fake_gen)

    result = classify_and_analyze(
        task, "[COLLECTION_ERROR] bad syntax", attempt=3,
    )
    assert gen_calls["count"] == 1
    assert "src/foo.py" in result.skeleton_files
    # token_usage 가 analyze + skeleton 합산이어야 함
    assert result.token_usage == (16, 20, 0, 0)
    assert len(result.call_log) == 2


def test_classify_and_analyze_attempt1_has_no_skeletons(monkeypatch):
    task = _task(target_files=["src/foo.py"])
    fake_analyze, _ = _fake_analyze_factory(should_retry=True)
    monkeypatch.setattr(iv, "analyze", fake_analyze)

    gen_calls = {"count": 0}

    def fake_gen(task, failure_context, role_models=None):
        gen_calls["count"] += 1
        return ({"src/foo.py": "x"}, (0, 0, 0, 0), [])

    monkeypatch.setattr(iv, "generate_skeleton_files", fake_gen)

    result = classify_and_analyze(task, "some logic failure", attempt=1)
    assert gen_calls["count"] == 0
    assert result.skeleton_files == {}


def test_classify_and_analyze_attempt2_has_no_skeletons(monkeypatch):
    task = _task(target_files=["src/foo.py"])
    fake_analyze, _ = _fake_analyze_factory(should_retry=True)
    monkeypatch.setattr(iv, "analyze", fake_analyze)
    monkeypatch.setattr(
        iv, "generate_skeleton_files",
        lambda t, c, role_models=None: (_ for _ in ()).throw(AssertionError("불러오면 안 됨")),
    )

    result = classify_and_analyze(task, "some logic failure", attempt=2)
    assert result.skeleton_files == {}


def test_classify_and_analyze_attempt3_no_target_files_no_skeleton(monkeypatch):
    """target_files 가 비어있으면 3회차여도 generate_skeleton_files 를 부르지 않는다."""
    task = _task(target_files=[])
    fake_analyze, _ = _fake_analyze_factory(should_retry=True)
    monkeypatch.setattr(iv, "analyze", fake_analyze)
    monkeypatch.setattr(
        iv, "generate_skeleton_files",
        lambda t, c, role_models=None: (_ for _ in ()).throw(AssertionError("불러오면 안 됨")),
    )

    result = classify_and_analyze(task, "logic failure", attempt=3)
    assert result.skeleton_files == {}


def test_classify_and_analyze_attempt3_give_up_no_skeleton(monkeypatch):
    """analyze() 가 GIVE_UP 반환하면 3회차여도 skeleton 생성하지 않음."""
    task = _task(target_files=["src/foo.py"])
    fake_analyze, _ = _fake_analyze_factory(should_retry=False)
    monkeypatch.setattr(iv, "analyze", fake_analyze)
    monkeypatch.setattr(
        iv, "generate_skeleton_files",
        lambda t, c, role_models=None: (_ for _ in ()).throw(AssertionError("불러오면 안 됨")),
    )

    result = classify_and_analyze(task, "logic failure", attempt=3)
    assert result.should_retry is False
    assert result.skeleton_files == {}


def test_classify_and_analyze_max_iter_no_skeleton_at_attempt3(monkeypatch):
    """MAX_ITER fast-path 는 attempt>=2 에서 GIVE_UP 하므로 skeleton 경로 미도달."""
    task = _task(target_files=["src/foo.py"])

    def never_call(*args, **kwargs):
        raise AssertionError("fast-path 에서는 skeleton 생성되면 안 됨")

    monkeypatch.setattr(iv, "generate_skeleton_files", never_call)

    result = classify_and_analyze(task, "[MAX_ITER] loop", attempt=3)
    assert result.should_retry is False
    assert result.skeleton_files == {}


def test_classify_and_analyze_attempt3_empty_skeletons_keeps_empty(monkeypatch):
    """generate_skeleton_files 가 빈 dict 반환 시 skeleton_files 는 빈 상태 유지."""
    task = _task(target_files=["src/foo.py"])
    fake_analyze, _ = _fake_analyze_factory(should_retry=True)
    monkeypatch.setattr(iv, "analyze", fake_analyze)
    monkeypatch.setattr(
        iv, "generate_skeleton_files",
        lambda t, c, role_models=None: ({}, (0, 0, 0, 0), []),
    )

    result = classify_and_analyze(task, "logic failure", attempt=3)
    assert result.should_retry is True
    assert result.skeleton_files == {}
    # token_usage 는 analyze() 값만 유지
    assert result.token_usage == (5, 7, 0, 0)
