from __future__ import annotations

from cli.interrupt import _is_standalone_escape, stdin_readers_paused


def test_standalone_escape_returns_true():
    ready_states = iter([([], [], [])])

    def fake_select(_r, _w, _x, _timeout):
        return next(ready_states)

    assert _is_standalone_escape(0, select_fn=fake_select, read_fn=lambda *_: b"") is True


def test_escape_sequence_returns_false_and_drains_bytes():
    ready_states = iter([
        ([0], [], []),
        ([0], [], []),
        ([], [], []),
    ])
    consumed: list[bytes] = []
    chunks = iter([b"[", b"A"])

    def fake_select(_r, _w, _x, _timeout):
        return next(ready_states)

    def fake_read(_fd, _size):
        chunk = next(chunks)
        consumed.append(chunk)
        return chunk

    assert _is_standalone_escape(0, select_fn=fake_select, read_fn=fake_read) is False
    assert consumed == [b"[", b"A"]


def test_stdin_readers_paused_calls_pause_and_resume(monkeypatch):
    calls: list[str] = []

    class _Reader:
        def pause(self):
            calls.append("pause")

        def resume(self):
            calls.append("resume")

    monkeypatch.setattr("cli.interrupt._stdin_readers", [_Reader()])

    with stdin_readers_paused():
        calls.append("body")

    assert calls == ["pause", "body", "resume"]


def test_stdin_readers_paused_resumes_after_exception(monkeypatch):
    calls: list[str] = []

    class _Reader:
        def pause(self):
            calls.append("pause")

        def resume(self):
            calls.append("resume")

    monkeypatch.setattr("cli.interrupt._stdin_readers", [_Reader()])

    try:
        with stdin_readers_paused():
            raise RuntimeError("boom")
    except RuntimeError:
        pass

    assert calls == ["pause", "resume"]
