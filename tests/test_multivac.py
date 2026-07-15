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
