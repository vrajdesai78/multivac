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
