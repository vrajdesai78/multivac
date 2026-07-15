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
