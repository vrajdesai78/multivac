import importlib.util, pathlib, sys
spec = importlib.util.spec_from_file_location("multivac", pathlib.Path(__file__).parent.parent / "bin" / "multivac.py")
mv = importlib.util.module_from_spec(spec)
sys.modules["multivac"] = mv  # dataclasses needs the module registered to resolve string annotations
spec.loader.exec_module(mv)

def test_constants_and_parser():
    assert mv.TOOLS == ("codex", "agy", "claude", "grok")
    assert mv.MODES == ("plan", "edit", "full")
    p = mv.build_parser()
    ns = p.parse_args(["ask", "--tool", "codex", "--prompt", "hi"])
    assert ns.cmd == "ask" and ns.tool == "codex" and ns.prompt == "hi"
    assert ns.mode == "plan"  # default

def test_req_defaults():
    r = mv.Req(tool="grok", prompt="x")
    assert r.mode == "plan" and r.max_depth == 2 and r.allow_api_keys is False

def test_mode_flags_first_call():
    assert mv.mode_flags("codex", "plan") == ["-s", "read-only"]
    assert mv.mode_flags("codex", "edit") == ["-s", "workspace-write", "--ask-for-approval", "never"]
    assert mv.mode_flags("codex", "full") == ["--dangerously-bypass-approvals-and-sandbox"]
    assert mv.mode_flags("claude", "plan") == ["--permission-mode", "plan"]
    assert mv.mode_flags("grok", "full") == ["--permission-mode", "bypassPermissions"]
    assert mv.mode_flags("agy", "edit") == ["--mode", "accept-edits"]

def test_codex_resume_uses_config_not_dash_s():
    # codex exec resume rejects -s; sandbox must go via -c config override
    plan = mv.mode_flags("codex", "plan", resume=True)
    assert "-s" not in plan
    assert plan == ["-c", 'sandbox_mode="read-only"']
    assert mv.mode_flags("codex", "full", resume=True) == ["--dangerously-bypass-approvals-and-sandbox"]

def test_no_short_flags_leak_for_non_codex():
    # long flags only for claude/grok/agy generated argv
    for tool in ("claude", "grok", "agy"):
        for mode in mv.MODES:
            assert all(not (f.startswith("-") and not f.startswith("--")) for f in mv.mode_flags(tool, mode))

def test_build_env_scrubs_keys_and_sets_depth():
    base = {"PATH": "/usr/bin", "ANTHROPIC_API_KEY": "sk-x", "OPENAI_API_KEY": "sk-y",
            "HOME": "/home/u", "CLAUDECODE": "1", "MULTIVAC_DEPTH": "0"}
    env = mv.build_env("codex", allow_api_keys=False, depth=0, base=base)
    assert "ANTHROPIC_API_KEY" not in env and "OPENAI_API_KEY" not in env
    assert env["PATH"] == "/usr/bin" and env["HOME"] == "/home/u"
    assert env["MULTIVAC_DEPTH"] == "1"          # incremented for the child

def test_build_env_allow_api_keys_keeps_them():
    base = {"PATH": "/usr/bin", "XAI_API_KEY": "k"}
    env = mv.build_env("grok", allow_api_keys=True, depth=0, base=base)
    assert env["XAI_API_KEY"] == "k"

def test_build_env_isolates_tmp_for_claude():
    base = {"PATH": "/usr/bin", "HOME": "/home/u", "CLAUDECODE": "1"}
    env = mv.build_env("claude", base=base)
    assert "CLAUDE_CODE_TMPDIR" in env and env["CLAUDE_CODE_TMPDIR"]
    assert "CLAUDECODE" not in env            # nesting marker cleared for claude delegate


import sys as _sys

def test_run_child_splits_streams_and_stdin_devnull():
    code, out, err, to = mv.run_child(
        [_sys.executable, "-c", "import sys; print('OUT'); print('ERR', file=sys.stderr); sys.exit(0)"],
        cwd=".", env={"PATH": mv.os.environ["PATH"]}, timeout=30)
    assert code == 0 and to is False
    assert out.strip() == "OUT" and err.strip() == "ERR"   # not merged

def test_run_child_does_not_hang_on_missing_stdin():
    # A child that reads stdin must get EOF immediately (DEVNULL), not hang.
    code, out, err, to = mv.run_child(
        [_sys.executable, "-c", "import sys; d=sys.stdin.read(); print('READ', len(d))"],
        cwd=".", env={"PATH": mv.os.environ["PATH"]}, timeout=10)
    assert to is False and out.strip() == "READ 0"

def test_run_child_times_out_and_kills_group():
    t0 = mv.time.time()
    code, out, err, to = mv.run_child(
        [_sys.executable, "-c", "import time; time.sleep(30)"],
        cwd=".", env={"PATH": mv.os.environ["PATH"]}, timeout=1)
    assert to is True and (mv.time.time() - t0) < 10


import pathlib as _pl
FX = _pl.Path(__file__).parent / "fixtures"

def test_parse_codex_ndjson():
    ans, sid, cost = mv.parse_output("codex", (FX / "codex.jsonl").read_text(), "")
    assert ans == "42" and sid == "019f65bc-e336-7c90-99c5-8ca46a4e603a"

def test_parse_claude_json():
    ans, sid, cost = mv.parse_output("claude", (FX / "claude.json").read_text(), "")
    assert ans == "42" and sid == "1099535c-0ef4-4939-965b-7d0fe1f5455e" and abs(cost - 0.2158) < 1e-6

def test_parse_grok_json():
    ans, sid, cost = mv.parse_output("grok", (FX / "grok.json").read_text(), "")
    assert ans == "42" and sid == "019f65bc-3a9e-74d0-bb8c-43e09abff9a1"

def test_parse_agy_plain_and_empty_guard():
    ans, sid, cost = mv.parse_output("agy", (FX / "agy.txt").read_text(), "")
    assert ans == "42" and sid is None
    import pytest
    with pytest.raises(ValueError):
        mv.parse_output("agy", "   \n", "")          # empty output != success
    with pytest.raises(ValueError):
        mv.parse_output("codex", '{"type":"turn.started"}\n', "")   # no agent_message


def test_session_store_roundtrip(tmp_path):
    st = mv.SessionStore(tmp_path)
    assert st.get("job1", "codex", "/repo") is None
    st.put("job1", "codex", "/repo", "abc-123")
    assert st.get("job1", "codex", "/repo") == "abc-123"
    # wrong tool or cwd -> miss (no cross-transcript resume)
    assert st.get("job1", "claude", "/repo") is None
    assert st.get("job1", "codex", "/other") is None
    # file is chmod 600
    import stat
    mode = (tmp_path / "sessions.json").stat().st_mode
    assert stat.S_IMODE(mode) == 0o600

def test_session_store_put_no_world_readable_tmp_leak(tmp_path):
    import stat
    st = mv.SessionStore(tmp_path)
    st.put("job1", "codex", "/repo", "abc-123")
    st.put("job2", "claude", "/other", "def-456")
    # final file is 0600
    mode = (tmp_path / "sessions.json").stat().st_mode
    assert stat.S_IMODE(mode) == 0o600
    # no leftover mkstemp temp files (the write-then-chmod race window is gone)
    leftovers = list(tmp_path.glob(".sessions-*.tmp"))
    assert leftovers == []

def test_session_store_rejects_bad_label(tmp_path):
    import pytest
    st = mv.SessionStore(tmp_path)
    with pytest.raises(ValueError):
        st.put("../evil", "codex", "/repo", "x")


def test_build_argv_codex_first_call():
    argv, planned = mv.build_argv(mv.Req(tool="codex", prompt="hi", cwd="/repo"), prompt="hi")
    assert argv[:2] == ["codex", "exec"]
    assert "--skip-git-repo-check" in argv and "--json" in argv
    assert "-s" in argv and "read-only" in argv
    assert argv[-1] == "hi"

def test_build_argv_codex_resume():
    argv, _ = mv.build_argv(mv.Req(tool="codex", prompt="more", cwd="/repo"), session_id="TID", prompt="more")
    assert argv[:3] == ["codex", "exec", "resume"] and "TID" in argv
    assert "-s" not in argv and '-c' in argv          # sandbox via config on resume
    assert argv[-1] == "more"

def test_build_argv_claude_generates_session_id():
    r = mv.Req(tool="claude", prompt="hi", cwd="/repo")
    argv, planned = mv.build_argv(r, new_session_id="11111111-1111-4111-8111-111111111111", prompt="hi")
    assert "--print" in argv and ["--output-format", "json"] == argv[argv.index("--output-format"):argv.index("--output-format")+2]
    assert "--session-id" in argv and planned == "11111111-1111-4111-8111-111111111111"
    assert "--permission-mode" in argv and argv[-1] == "hi"

def test_build_argv_grok_first_call():
    argv, planned = mv.build_argv(mv.Req(tool="grok", prompt="x", cwd="/repo"), prompt="x")
    assert "--output-format" in argv and "json" in argv
    assert "--no-auto-update" in argv
    assert "--print" not in argv
    assert "--session-id" not in argv
    assert argv[-1] == "x" and argv[-2] == "--single"
    assert planned is None   # grok's session id is parsed from output, not client-generated

def test_build_argv_grok_resume_uses_resume_flag():
    argv, _ = mv.build_argv(mv.Req(tool="grok", prompt="x", cwd="/repo"), session_id="SID", prompt="x")
    assert "--resume" in argv and "SID" in argv and "-s" not in argv
    assert "-c" not in argv
    assert argv[-1] == "x" and argv[-2] == "--single"

def test_build_argv_agy_prompt_and_websearch():
    argv, _ = mv.build_argv(mv.Req(tool="agy", prompt="p", cwd="/repo", web_search=True), prompt="p")
    assert argv[-2] == "--print" and argv[-1] == "p" and "--mode" in argv
    assert argv.index("--mode") < argv.index("--print")


def test_apply_subagent_native_claude():
    agent = {"name": "reviewer", "prompt": "Be critical."}
    argv, prompt = mv.apply_subagent("claude", ["claude", "--print"], "review x", agent)
    assert "--append-system-prompt" in argv
    assert argv[argv.index("--append-system-prompt") + 1] == "Be critical."
    assert prompt == "review x"

def test_apply_subagent_native_grok():
    agent = {"name": "reviewer", "prompt": "Be critical."}
    argv, prompt = mv.apply_subagent("grok", ["grok", "--print"], "review x", agent)
    assert "--system-prompt-override" in argv

def test_apply_subagent_emulated_codex_preamble():
    agent = {"name": "reviewer", "prompt": "Be critical."}
    argv, prompt = mv.apply_subagent("codex", ["codex", "exec"], "review x", agent)
    assert argv == ["codex", "exec"]                    # no flag added
    assert prompt.startswith("You are acting as the `reviewer` agent.")
    assert "Be critical." in prompt and "review x" in prompt

def test_resolve_agent_named(tmp_path, monkeypatch):
    r = mv.Req(tool="codex", prompt="x", agent="reviewer")
    a = mv.resolve_agent(r)
    assert a["name"] == "reviewer" and "reviewer" in a["prompt"].lower()

def test_resolve_agent_blocks_path_traversal():
    r = mv.Req(tool="codex", prompt="x", agent="../../../etc/passwd")
    try:
        mv.resolve_agent(r)
        assert False, "expected ValueError for path-traversal agent name"
    except ValueError:
        pass

def test_resolve_agent_blocks_slash():
    r = mv.Req(tool="codex", prompt="x", agent="foo/bar")
    try:
        mv.resolve_agent(r)
        assert False, "expected ValueError for slash in agent name"
    except ValueError:
        pass


def _fake_runner_factory(stdout, code=0, stderr="", timed_out=False):
    def r(argv, *, cwd, env, timeout, stdin_data=None):
        return (None if timed_out else code, stdout, stderr, timed_out)
    return r

def test_do_ask_happy_path_claude(tmp_path, monkeypatch):
    monkeypatch.setenv("MULTIVAC_HOME", str(tmp_path))
    req = mv.Req(tool="claude", prompt="hi", cwd=str(tmp_path))
    res = mv.do_ask(req, runner=_fake_runner_factory('{"type":"result","result":"42","session_id":"S1","total_cost_usd":0.01}'))
    assert res.ok and res.answer == "42" and res.session_id == "S1"

def test_do_ask_records_and_resumes_session(tmp_path, monkeypatch):
    monkeypatch.setenv("MULTIVAC_HOME", str(tmp_path))
    r1 = mv.Req(tool="claude", prompt="hi", cwd=str(tmp_path), session="job")
    mv.do_ask(r1, runner=_fake_runner_factory('{"type":"result","result":"a","session_id":"S9"}'))
    st = mv.SessionStore(tmp_path)
    assert st.get("job", "claude", str(tmp_path)) is not None

def test_do_ask_full_requires_ack(tmp_path, monkeypatch):
    monkeypatch.setenv("MULTIVAC_HOME", str(tmp_path)); monkeypatch.delenv("MULTIVAC_ALLOW_FULL", raising=False)
    req = mv.Req(tool="codex", prompt="x", cwd=str(tmp_path), mode="full", yes=False)
    res = mv.do_ask(req, runner=_fake_runner_factory("{}"))
    assert not res.ok and "full" in res.error.lower()

def test_do_ask_depth_guard(tmp_path, monkeypatch):
    monkeypatch.setenv("MULTIVAC_HOME", str(tmp_path)); monkeypatch.setenv("MULTIVAC_DEPTH", "2")
    req = mv.Req(tool="grok", prompt="x", cwd=str(tmp_path), max_depth=2)
    res = mv.do_ask(req, runner=_fake_runner_factory("{}"))
    assert not res.ok and "depth" in res.error.lower()

def test_do_ask_timeout_maps_error(tmp_path, monkeypatch):
    monkeypatch.setenv("MULTIVAC_HOME", str(tmp_path))
    req = mv.Req(tool="codex", prompt="x", cwd=str(tmp_path))
    res = mv.do_ask(req, runner=_fake_runner_factory("", code=None, timed_out=True))
    assert not res.ok and "timed out" in res.error.lower()


def test_run_child_pty_reads_output_written_to_tty():
    code, out, err, to = mv.run_child_pty(
        [_sys.executable, "-c", "import sys; print('TTY' if sys.stdout.isatty() else 'NOTTY')"],
        cwd=".", env={"PATH": mv.os.environ["PATH"]}, timeout=10)
    assert to is False and code == 0
    assert "TTY" in out and "NOTTY" not in out   # pty makes stdout a tty for the child

def test_run_child_pty_times_out_and_kills_group():
    t0 = mv.time.time()
    code, out, err, to = mv.run_child_pty(
        [_sys.executable, "-c", "import time; time.sleep(30)"],
        cwd=".", env={"PATH": mv.os.environ["PATH"]}, timeout=1)
    assert to is True and code is None
    assert (mv.time.time() - t0) < 10


def test_do_ask_agy_retries_via_pty_on_empty_stdout(tmp_path, monkeypatch):
    monkeypatch.setenv("MULTIVAC_HOME", str(tmp_path))
    # Fake "agy" binary that always emits nothing on stdout (simulates the non-TTY stdout-drop bug),
    # placed first on PATH so the real run_child (subprocess, non-tty pipe) finds it and gets empty output.
    fake_bin_dir = tmp_path / "bin"
    fake_bin_dir.mkdir()
    fake_agy = fake_bin_dir / "agy"
    fake_agy.write_text("#!/bin/sh\nexit 0\n")
    fake_agy.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_bin_dir}{mv.os.pathsep}{mv.os.environ['PATH']}")
    monkeypatch.setattr(mv, "run_child_pty", lambda argv, *, cwd, env, timeout: (0, "PONG", "", False))
    req = mv.Req(tool="agy", prompt="hi", cwd=str(tmp_path))
    res = mv.do_ask(req)   # default runner = real run_child -> triggers the agy PTY retry
    assert res.ok and res.answer == "PONG"

def test_do_ask_agy_pty_retry_records_session(tmp_path, monkeypatch):
    monkeypatch.setenv("MULTIVAC_HOME", str(tmp_path))
    fake_bin_dir = tmp_path / "bin"
    fake_bin_dir.mkdir()
    fake_agy = fake_bin_dir / "agy"
    fake_agy.write_text("#!/bin/sh\nexit 0\n")
    fake_agy.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_bin_dir}{mv.os.pathsep}{mv.os.environ['PATH']}")
    monkeypatch.setattr(mv, "run_child_pty", lambda argv, *, cwd, env, timeout: (0, "PONG", "", False))
    req = mv.Req(tool="agy", prompt="hi", cwd=str(tmp_path), session="job")
    res = mv.do_ask(req)
    assert res.ok
    st = mv.SessionStore(tmp_path)
    # agy has no session id of its own; planned is None too, so nothing to record -- just must not crash
    assert st.get("job", "agy", str(tmp_path)) is None

def test_do_ask_agy_pty_retry_still_fails_if_pty_also_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("MULTIVAC_HOME", str(tmp_path))
    fake_bin_dir = tmp_path / "bin"
    fake_bin_dir.mkdir()
    fake_agy = fake_bin_dir / "agy"
    fake_agy.write_text("#!/bin/sh\nexit 0\n")
    fake_agy.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_bin_dir}{mv.os.pathsep}{mv.os.environ['PATH']}")
    monkeypatch.setattr(mv, "run_child_pty", lambda argv, *, cwd, env, timeout: (0, "", "", False))
    req = mv.Req(tool="agy", prompt="hi", cwd=str(tmp_path))
    res = mv.do_ask(req)
    assert not res.ok and "agy" in res.error.lower()

def test_do_ask_agy_no_pty_retry_when_custom_runner_used(tmp_path, monkeypatch):
    # The retry is only for the default real runner; an explicit fake runner (e.g. other tests) must not trigger it.
    monkeypatch.setenv("MULTIVAC_HOME", str(tmp_path))
    monkeypatch.setattr(mv, "run_child_pty", lambda *a, **k: (0, "SHOULD-NOT-BE-USED", "", False))
    req = mv.Req(tool="agy", prompt="hi", cwd=str(tmp_path))
    res = mv.do_ask(req, runner=_fake_runner_factory(""))   # empty stdout, custom runner
    assert not res.ok and "empty stdout" in res.error.lower()


import os as _os, pytest

@pytest.mark.skipif(_os.environ.get("MULTIVAC_LIVE") != "1", reason="live test; set MULTIVAC_LIVE=1")
def test_live_doctor_and_plan_ask():
    rows = mv.do_doctor(list(mv.TOOLS))
    installed = [r["tool"] for r in rows if r["installed"]]
    assert installed, "no delegate CLIs installed"
    for t in installed:
        res = mv.do_ask(mv.Req(tool=t, prompt="Reply with exactly: PONG", cwd=_os.getcwd(), mode="plan"))
        assert res.ok and "PONG" in res.answer.upper(), f"{t}: {res.error or res.answer}"


def test_consensus_runs_all_and_tolerates_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("MULTIVAC_HOME", str(tmp_path))
    def fake_asker(req, **kw):
        if req.tool == "grok":
            return mv.Result(tool="grok", ok=False, error="boom")
        return mv.Result(tool=req.tool, ok=True, answer=f"ans-{req.tool}")
    base = mv.Req(tool="_", prompt="q", cwd=str(tmp_path))
    results = mv.do_consensus(["codex", "grok", "claude"], base, concurrency=2, asker=fake_asker)
    by = {r.tool: r for r in results}
    assert by["codex"].ok and by["claude"].ok and not by["grok"].ok
    assert {r.tool for r in results} == {"codex", "grok", "claude"}

def test_consensus_all_excludes_host(monkeypatch, tmp_path):
    monkeypatch.setenv("MULTIVAC_HOME", str(tmp_path)); monkeypatch.setenv("MULTIVAC_HOST", "claude")
    tools = mv.resolve_consensus_tools("all")
    assert "claude" not in tools and set(tools) == {"codex", "agy", "grok"}


def test_doctor_reports_installed_and_scrubbed(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    fake_which = lambda b: "/usr/bin/" + b if b in ("codex", "claude") else None
    rows = mv.do_doctor(["codex", "grok", "claude"], which=fake_which,
                        version_runner=lambda tool: ("0.1.0" if tool != "grok" else None))
    by = {r["tool"]: r for r in rows}
    assert by["codex"]["installed"] and by["grok"]["installed"] is False
    assert "OPENAI_API_KEY" in by["codex"]["scrubbed_keys"]

def test_sessions_lists(tmp_path, monkeypatch):
    monkeypatch.setenv("MULTIVAC_HOME", str(tmp_path))
    mv.SessionStore(tmp_path).put("j", "codex", str(tmp_path), "TID")
    out = mv.do_sessions()
    assert "j" in out and out["j"]["tool"] == "codex"
