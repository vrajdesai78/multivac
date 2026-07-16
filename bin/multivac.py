#!/usr/bin/env python3
"""multivac — invoke other AI coding CLIs (codex, agy, claude, grok) on their
existing subscription logins. Single file, stdlib only.

WHAT THIS DOES: builds argv for each delegate CLI, runs it as a child process
under a strict execution contract, and relays its final text.
WHAT THIS DOES NOT DO: it never eval/execs model output, makes no network calls
of its own, has no telemetry, and never reads your OAuth tokens — each delegate
uses its own on-disk login.
"""
from __future__ import annotations
import argparse, json, os, re, shutil, signal, subprocess, sys, tempfile, time, uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path

TOOLS = ("codex", "agy", "claude", "grok")
MODES = ("plan", "edit", "full")
API_KEYS = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "XAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY")
DEFAULT_TIMEOUTS = {"codex": 180, "claude": 180, "grok": 180, "agy": 300}


@dataclass
class Req:
    tool: str
    prompt: str
    mode: str = "plan"
    model: "str | None" = None
    cwd: "str | None" = None
    session: "str | None" = None
    agent: "str | None" = None
    agents: "str | None" = None
    web_search: bool = False
    timeout: "int | None" = None
    allow_api_keys: bool = False
    yes: bool = False
    as_json: bool = False
    max_depth: int = 2


SPECS = {
    "codex": {
        "bin": "codex",
        "framing": "ndjson",
        "answer": None,            # parsed from agent_message items
        "session": None,           # parsed from thread.started
        "mode": {
            "plan": ["-s", "read-only"],
            "edit": ["-s", "workspace-write", "--ask-for-approval", "never"],
            "full": ["--dangerously-bypass-approvals-and-sandbox"],
        },
        "mode_resume": {           # codex exec resume rejects -s
            "plan": ["-c", 'sandbox_mode="read-only"'],
            "edit": ["-c", 'sandbox_mode="workspace-write"', "-c", 'approval_policy="never"'],
            "full": ["--dangerously-bypass-approvals-and-sandbox"],
        },
    },
    "agy": {
        "bin": "agy", "framing": "plain", "answer": None, "session": None,
        "mode": {"plan": ["--mode", "plan"], "edit": ["--mode", "accept-edits"], "full": ["--dangerously-skip-permissions"]},
    },
    "claude": {
        "bin": "claude", "framing": "json", "answer": "result", "session": "session_id",
        "mode": {"plan": ["--permission-mode", "plan"], "edit": ["--permission-mode", "acceptEdits"], "full": ["--permission-mode", "bypassPermissions"]},
    },
    "grok": {
        "bin": "grok", "framing": "json", "answer": "text", "session": "sessionId",
        "mode": {"plan": ["--permission-mode", "plan"], "edit": ["--permission-mode", "acceptEdits"], "full": ["--permission-mode", "bypassPermissions"]},
    },
}


def mode_flags(tool: str, mode: str, *, resume: bool = False) -> list:
    spec = SPECS[tool]
    if resume and "mode_resume" in spec:
        return list(spec["mode_resume"][mode])
    return list(spec["mode"][mode])


def _web_flags(tool, on):
    if not on:
        return ["--disable-web-search"] if tool == "grok" else []
    return {"codex": ["-c", "tools.web_search=true"], "grok": [], "claude": [], "agy": []}[tool]


_AGENT_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# Strip control chars from relayed delegate output: all C0 except tab(09)/newline(0a),
# plus DEL and C1. Prevents ANSI/OSC terminal spoofing (forged attribution, clipboard hijack).
# Strip ANSI/OSC escape sequences and stray control chars from relayed delegate
# output — prevents terminal spoofing (forged consensus attribution, clipboard
# hijack via OSC 52, hidden/overwriting text). Full sequences are removed so no
# visible parameter residue (e.g. "[31m") is left behind; tab and newline survive.
_ANSI_RE = re.compile(
    r"\x1b\[[0-9;?]*[ -/]*[@-~]"          # CSI: ESC [ ... final byte
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"  # OSC: ESC ] ... (BEL or ST)
    r"|\x1b[PX^_][^\x1b]*(?:\x1b\\)?"      # DCS/SOS/PM/APC strings
    r"|\x1b."                              # any other ESC + one char
)
_CTRL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")  # C0 (keep tab/newline), DEL, C1
def _clean(text):
    if not isinstance(text, str):
        return text
    return _CTRL_RE.sub("", _ANSI_RE.sub("", text))


def _agents_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "references" / "agents"


def resolve_agent(req: Req):
    if req.agents:
        raw = req.agents
        if raw.startswith("@"):
            path = raw[1:]
            if os.path.getsize(path) > 1024 * 1024:
                raise ValueError("--agents file too large (>1MB)")
            raw = Path(path).read_text()
        return json.loads(raw)
    if req.agent:
        if not _AGENT_RE.match(req.agent):
            raise ValueError(f"invalid agent name: {req.agent!r} (use inline --agents JSON for custom definitions)")
        f = _agents_dir() / f"{req.agent}.json"
        if f.exists():
            return json.loads(f.read_text())
        raise ValueError(f"unknown agent {req.agent!r}: define references/agents/{req.agent}.json or use --agents with an inline JSON definition")
    return None


def apply_subagent(tool: str, argv: list, prompt: str, agent_def):
    if not agent_def:
        return argv, prompt
    sysprompt = (agent_def.get("prompt") or "").strip()
    name = agent_def.get("name", "agent")
    if tool == "claude" and sysprompt:
        return argv + ["--append-system-prompt", sysprompt], prompt
    if tool == "grok" and sysprompt:
        return argv + ["--system-prompt-override", sysprompt], prompt
    # emulate (agy, codex): fold into prompt preamble
    if sysprompt:
        prompt = f"You are acting as the `{name}` agent. {sysprompt}\n\nTask: {prompt}"
    return argv, prompt


def build_argv(req: Req, *, session_id=None, new_session_id=None, prompt=None, agent_def=None):
    """Return (argv, planned_session_id). Prompt is the final positional arg."""
    tool = req.tool
    resume = session_id is not None
    argv = []
    planned = session_id

    if tool == "codex":
        argv = ["codex", "exec"]
        if resume:
            argv += ["resume", session_id]
        argv += ["--skip-git-repo-check", "--json"]
        argv += mode_flags("codex", req.mode, resume=resume)
        if req.model:
            argv += ["--model", req.model]
        argv += _web_flags("codex", req.web_search)

    elif tool == "claude":
        argv = ["claude", "--print", "--output-format", "json"]
        if resume:
            argv += ["--resume", session_id]
        elif new_session_id:
            argv += ["--session-id", new_session_id]; planned = new_session_id
        argv += mode_flags("claude", req.mode)
        if req.model:
            argv += ["--model", req.model]

    elif tool == "grok":
        argv = ["grok", "--output-format", "json", "--no-auto-update"]
        if resume:
            argv += ["--resume", session_id]
        argv += mode_flags("grok", req.mode)
        if req.model:
            argv += ["--model", req.model]
        argv += _web_flags("grok", req.web_search)

    elif tool == "agy":
        argv = ["agy"]
        if resume:
            argv += ["-c"]                       # best-effort continue-most-recent
        argv += mode_flags("agy", req.mode)
        if req.model:
            argv += ["--model", req.model]

    prompt = prompt if prompt is not None else req.prompt
    argv, prompt = apply_subagent(tool, argv, prompt, agent_def)
    if tool == "grok":
        argv += ["--single", prompt]   # grok: prompt is the value of -p/--single, must be last
    elif tool == "agy":
        argv += ["--print", prompt]    # agy: prompt is the value of -p/--print, must be last
    else:
        argv.append(prompt)
    return argv, planned


# Env vars always allowed through to children (allow-list, not os.environ wholesale).
_ENV_ALLOW = {
    "PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "LC_ALL", "TERM", "TMPDIR",
    "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME",
    "CODEX_HOME", "GROK_HOME", "MULTIVAC_HOME",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "NODE_EXTRA_CA_CERTS",
}


def build_env(tool: str, *, allow_api_keys: bool = False, depth: int = 0, base=None, max_depth=None) -> dict:
    src = dict(os.environ if base is None else base)
    env = {k: v for k, v in src.items() if k in _ENV_ALLOW}
    if allow_api_keys:
        for k in API_KEYS:
            if k in src:
                env[k] = src[k]
    env["MULTIVAC_DEPTH"] = str(depth + 1)
    if max_depth is not None:
        env["MULTIVAC_MAX_DEPTH"] = str(max_depth)
    if tool == "claude":
        # Prevent a nested claude from purging the parent's /tmp/claude-<uid>/tasks.
        env.pop("CLAUDECODE", None)
        env["CLAUDE_CODE_TMPDIR"] = tempfile.mkdtemp(prefix="multivac-claude-")
    return env


def run_child(argv, *, cwd, env, timeout, stdin_data=None):
    """Execution contract: own stdin, split streams, new process group, killpg on timeout."""
    stdin = subprocess.PIPE if stdin_data is not None else subprocess.DEVNULL
    proc = subprocess.Popen(
        argv, cwd=cwd, env=env,
        stdin=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, start_new_session=True,   # new process group -> killpg
    )
    timed_out = False
    try:
        out, err = proc.communicate(input=stdin_data, timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()
        out, err = proc.communicate()
    out, err = out or "", err or ""
    if len(out) > MAX_OUTPUT_BYTES:
        out = out[:MAX_OUTPUT_BYTES] + "\n…[truncated by multivac]"
    if len(err) > MAX_OUTPUT_BYTES:
        err = err[:MAX_OUTPUT_BYTES] + "\n…[truncated by multivac]"
    return (None if timed_out else proc.returncode, out, err, timed_out)


def run_child_pty(argv, *, cwd, env, timeout):
    """Fallback for agy's non-TTY stdout-drop: run under a pseudo-tty."""
    import pty, select, errno
    pid, fd = pty.fork()
    if pid == 0:  # child
        try:
            os.chdir(cwd); os.execvpe(argv[0], argv, env)
        except Exception:
            os._exit(127)
    buf, deadline = [], time.time() + timeout
    total = 0
    timed_out = False
    try:
        while True:
            if time.time() > deadline:
                timed_out = True
                try: os.killpg(os.getpgid(pid), signal.SIGKILL)
                except Exception: pass
                break
            r, _, _ = select.select([fd], [], [], 0.5)
            if fd in r:
                try:
                    data = os.read(fd, 4096)
                except OSError as e:
                    if e.errno == errno.EIO: break
                    raise
                if not data: break
                total += len(data)
                buf.append(data.decode("utf-8", "replace"))
                if total > MAX_OUTPUT_BYTES:
                    try: os.killpg(os.getpgid(pid), signal.SIGKILL)
                    except Exception: pass
                    break
    finally:
        try: os.close(fd)
        except OSError: pass
    try: _, status = os.waitpid(pid, 0); code = os.waitstatus_to_exitcode(status)
    except Exception: code = None
    return (None if timed_out else code, "".join(buf), "", timed_out)


def parse_output(tool: str, stdout: str, stderr: str):
    framing = SPECS[tool]["framing"]
    if framing == "ndjson":                       # codex
        answer, sid = None, None
        for line in stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("type") == "thread.started":
                sid = ev.get("thread_id")
            item = ev.get("item") or {}
            if ev.get("type") == "item.completed" and item.get("type") == "agent_message":
                answer = item.get("text", answer)
        if answer is None:
            raise ValueError("no agent_message in codex output (run did not complete)")
        return answer, sid, None
    if framing == "json":                         # claude / grok
        obj = json.loads(stdout)
        if obj.get("is_error") or (obj.get("subtype") not in (None, "success")):
            raise ValueError(f"{tool}: delegate reported an error: {str(obj.get('result') or obj.get('text') or obj)[:200]}")
        if "structured_output" in obj:            # --json-schema path
            answer = json.dumps(obj["structured_output"])
        else:
            answer = obj.get(SPECS[tool]["answer"])
        if answer is None or (isinstance(answer, str) and not answer.strip()):
            raise ValueError(f"{tool}: empty result field")
        sid = obj.get(SPECS[tool]["session"])
        cost = obj.get("total_cost_usd")
        return answer, sid, cost
    # plain (agy)
    ans = stdout.strip()
    if not ans:
        raise ValueError("agy: empty stdout (non-TTY stdout-drop; retry via PTY)")
    return ans, None, None


_LABEL_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class SessionStore:
    def __init__(self, home: Path):
        self.home = Path(home)
        self.home.mkdir(parents=True, exist_ok=True)
        self.path = self.home / "sessions.json"

    def _load(self) -> dict:
        if self.path.is_symlink():  # refuse to follow a pre-planted symlink (exfil guard)
            return {}
        try:
            return json.loads(self.path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    @staticmethod
    def _check(label: str):
        if not _LABEL_RE.match(label or ""):
            raise ValueError(f"invalid session label: {label!r}")

    def get(self, label, tool, cwd):
        self._check(label)
        rec = self._load().get(label)
        if rec and rec.get("tool") == tool and rec.get("cwd") == os.path.abspath(cwd):
            return rec.get("session_id")
        return None

    def put(self, label, tool, cwd, session_id):
        self._check(label)
        data = self._load()
        data[label] = {"tool": tool, "cwd": os.path.abspath(cwd),
                       "session_id": session_id, "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        fd, tmpname = tempfile.mkstemp(dir=str(self.home), prefix=".sessions-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(json.dumps(data, indent=2))
            os.replace(tmpname, self.path)  # atomic; preserves the 0600 mode from mkstemp
        except BaseException:
            try:
                os.unlink(tmpname)
            except OSError:
                pass
            raise
        os.chmod(self.path, 0o600)

    def all(self):
        return self._load()


@dataclass
class Result:
    tool: str
    ok: bool
    answer: str = ""
    session_id: "str | None" = None
    cwd: "str | None" = None
    exit_code: "int | None" = None
    duration_s: float = 0.0
    cost_usd: "float | None" = None
    error: "str | None" = None


MAX_PROMPT_BYTES = 5 * 1024 * 1024
MAX_OUTPUT_BYTES = 32 * 1024 * 1024


def multivac_home() -> Path:
    return Path(os.environ.get("MULTIVAC_HOME", os.path.join(os.getcwd(), ".multivac")))


def resolve_prompt(req: Req) -> str:
    if getattr(req, "prompt", None):
        return req.prompt
    raise ValueError("no prompt")


def map_error(tool, exit_code, stderr, timed_out) -> str:
    if timed_out:
        return f"{tool}: timed out (killed process group)"
    tail = "\n".join((stderr or "").splitlines()[-8:])
    low = (stderr or "").lower()
    if "log in" in low or "login" in low or "not authenticated" in low or "unauthorized" in low:
        return f"{tool}: not logged in — run `{tool} login` (subscription). Detail:\n{tail}"
    if "trust" in low and ("folder" in low or "directory" in low or "workspace" in low):
        return f"{tool}: refused (untrusted directory). Pre-trust the dir or pass the tool's trust flag.\n{tail}"
    return f"{tool}: exit {exit_code}.\n{tail}"


def do_ask(req: Req, *, runner=run_child) -> Result:
    cwd = os.path.abspath(req.cwd or os.getcwd())
    # gate: full mode
    if req.mode == "full" and not (req.yes or os.environ.get("MULTIVAC_ALLOW_FULL") == "1"):
        return Result(tool=req.tool, ok=False, cwd=cwd,
                      error="mode 'full' requires --yes or MULTIVAC_ALLOW_FULL=1 (auto-approves ALL delegate actions)")
    # gate: recursion depth (pin the ceiling so a child can only LOWER it, never raise it)
    depth = int(os.environ.get("MULTIVAC_DEPTH", "0") or "0")
    HARD_MAX_DEPTH = 8
    env_cap = os.environ.get("MULTIVAC_MAX_DEPTH")
    effective_max = min(req.max_depth, HARD_MAX_DEPTH)
    if env_cap:
        try:
            effective_max = min(effective_max, int(env_cap))
        except ValueError:
            pass
    if depth >= effective_max:
        return Result(tool=req.tool, ok=False, cwd=cwd,
                      error=f"max recursion depth reached (MULTIVAC_DEPTH={depth} >= {effective_max})")
    # gate: prompt size
    prompt = resolve_prompt(req)
    if len(prompt.encode("utf-8")) > MAX_PROMPT_BYTES:
        return Result(tool=req.tool, ok=False, cwd=cwd, error="prompt exceeds 5 MB; put large context in a file")
    if req.mode == "full":
        print(f"multivac: WARNING running {req.tool} in FULL mode (auto-approves all) in {cwd}", file=sys.stderr)

    store = SessionStore(multivac_home())
    session_id = store.get(req.session, req.tool, cwd) if req.session else None
    new_sid = None
    if not session_id and req.tool == "claude":
        new_sid = str(uuid.uuid4())
    agent_def = resolve_agent(req)
    argv, planned = build_argv(req, session_id=session_id, new_session_id=new_sid, prompt=prompt, agent_def=agent_def)

    env = build_env(req.tool, allow_api_keys=req.allow_api_keys, depth=depth, max_depth=effective_max)
    timeout = req.timeout or DEFAULT_TIMEOUTS[req.tool]
    t0 = time.time()
    code, out, err, timed_out = runner(argv, cwd=cwd, env=env, timeout=timeout)
    dur = time.time() - t0

    if timed_out or (code not in (0, None) and not out.strip()):
        return Result(tool=req.tool, ok=False, cwd=cwd, exit_code=code, duration_s=dur,
                      error=map_error(req.tool, code, err, timed_out))
    try:
        answer, sid, cost = parse_output(req.tool, out, err)
    except ValueError as e:
        if req.tool == "agy" and "empty stdout" in str(e) and runner is run_child:
            code, out, err, timed_out = run_child_pty(argv, cwd=cwd, env=env, timeout=timeout)
            try:
                answer, sid, cost = parse_output("agy", out, err)
            except ValueError as e2:
                return Result(tool="agy", ok=False, cwd=cwd, exit_code=code, duration_s=dur, error=f"agy: {e2}")
            sid = sid or planned
            if req.session and sid: store.put(req.session, "agy", cwd, sid)
            return Result(tool="agy", ok=True, answer=answer, session_id=sid, cwd=cwd, exit_code=code, duration_s=dur)
        return Result(tool=req.tool, ok=False, cwd=cwd, exit_code=code, duration_s=dur,
                      error=f"{req.tool}: {e}\n{chr(10).join(err.splitlines()[-6:])}")
    sid = sid or planned
    if req.session and sid:
        store.put(req.session, req.tool, cwd, sid)
    return Result(tool=req.tool, ok=True, answer=answer, session_id=sid, cwd=cwd,
                  exit_code=code, duration_s=dur, cost_usd=cost)


def resolve_consensus_tools(spec: str) -> list:
    if spec == "all":
        host = os.environ.get("MULTIVAC_HOST")
        return [t for t in TOOLS if t != host]
    return [t.strip() for t in spec.split(",") if t.strip() in TOOLS]


def do_consensus(tools, base: Req, *, concurrency=3, asker=do_ask) -> list:
    reqs = [replace(base, tool=t, session=None) for t in tools]   # consensus is stateless
    results = []
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as ex:
        futs = {ex.submit(asker, r): r.tool for r in reqs}
        for fut in futs:
            try:
                results.append(fut.result())
            except Exception as e:               # defensive: never let one tool abort the batch
                results.append(Result(tool=futs[fut], ok=False, error=str(e)))
    order = {t: i for i, t in enumerate(tools)}
    results.sort(key=lambda r: order.get(r.tool, 99))
    return results


def _version_of(tool: str):
    try:
        out = subprocess.run([SPECS[tool]["bin"], "--version"], capture_output=True, text=True, timeout=15,
                             stdin=subprocess.DEVNULL)
        return (out.stdout or out.stderr).strip().splitlines()[0] if out.returncode == 0 else None
    except Exception:
        return None


def do_doctor(tools, *, which=shutil.which, version_runner=None) -> list:
    version_runner = version_runner or _version_of
    rows = []
    scrubbed = [k for k in API_KEYS if k in os.environ]
    for t in tools:
        installed = which(SPECS[t]["bin"]) is not None
        rows.append({
            "tool": t, "installed": installed,
            "version": version_runner(t) if installed else None,
            "scrubbed_keys": scrubbed,
            "note": "" if installed else f"not found — install {t}",
        })
    return rows


def do_sessions() -> dict:
    return SessionStore(multivac_home()).all()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="multivac", description="Invoke other AI coding CLIs on subscription auth.")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_common(sp):
        sp.add_argument("--mode", choices=MODES, default="plan")
        sp.add_argument("--model")
        sp.add_argument("--cwd")
        sp.add_argument("--timeout", type=int)
        sp.add_argument("--web-search", action="store_true")
        sp.add_argument("--allow-api-keys", action="store_true")
        sp.add_argument("--yes", action="store_true")
        sp.add_argument("--json", dest="as_json", action="store_true")
        sp.add_argument("--max-depth", type=int, default=2)

    a = sub.add_parser("ask")
    a.add_argument("--tool", choices=TOOLS, required=True)
    g = a.add_mutually_exclusive_group(required=True)
    g.add_argument("--prompt")
    g.add_argument("--prompt-file")
    a.add_argument("--session")
    a.add_argument("--agent")
    a.add_argument("--agents")
    add_common(a)

    c = sub.add_parser("consensus")
    c.add_argument("--tools", required=True, help="comma list or 'all'")
    gc = c.add_mutually_exclusive_group(required=True)
    gc.add_argument("--prompt")
    gc.add_argument("--prompt-file")
    c.add_argument("--concurrency", type=int, default=3)
    add_common(c)

    sub.add_parser("doctor").add_argument("--tools", default="all")
    sub.add_parser("sessions")
    return p


def _req_from_args(args) -> Req:
    prompt = args.prompt
    if getattr(args, "prompt_file", None):
        if os.path.getsize(args.prompt_file) > MAX_PROMPT_BYTES:
            raise ValueError("prompt-file exceeds 5 MB; put large context in a file the delegate reads")
        prompt = Path(args.prompt_file).read_text()
    return Req(tool=args.tool, prompt=prompt, mode=args.mode, model=args.model, cwd=args.cwd,
               session=getattr(args, "session", None), agent=getattr(args, "agent", None),
               agents=getattr(args, "agents", None), web_search=args.web_search, timeout=args.timeout,
               allow_api_keys=args.allow_api_keys, yes=args.yes, as_json=args.as_json, max_depth=args.max_depth)


def _emit(res: Result, as_json: bool) -> int:
    if as_json:
        print(json.dumps(res.__dict__, indent=2))
    elif res.ok:
        print(_clean(res.answer))
    else:
        print(f"ERROR: {_clean(res.error)}", file=sys.stderr)
    return 0 if res.ok else 1


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.cmd == "ask":
            return _emit(do_ask(_req_from_args(args)), args.as_json)
        if args.cmd == "consensus":
            tools = resolve_consensus_tools(args.tools)
            if not tools:
                print("ERROR: no valid tools", file=sys.stderr); return 1
            base = Req(tool="_", prompt=(Path(args.prompt_file).read_text() if getattr(args, "prompt_file", None) else args.prompt),
                       mode=args.mode, model=args.model, cwd=args.cwd, web_search=args.web_search,
                       timeout=args.timeout, allow_api_keys=args.allow_api_keys, yes=args.yes, max_depth=args.max_depth)
            results = do_consensus(tools, base, concurrency=args.concurrency)
            if args.as_json:
                print(json.dumps([r.__dict__ for r in results], indent=2))
            else:
                for r in results:
                    print(f"\n===== {r.tool} =====")
                    print(_clean(r.answer) if r.ok else f"ERROR: {_clean(r.error)}")
            return 0 if any(r.ok for r in results) else 1
        if args.cmd == "doctor":
            tools = TOOLS if args.tools == "all" else [t for t in args.tools.split(",") if t in TOOLS]
            if not tools:
                print("ERROR: no valid tools", file=sys.stderr); return 1
            rows = do_doctor(list(tools))
            print(json.dumps(rows, indent=2))
            return 0 if all(r["installed"] for r in rows) else 1
        if args.cmd == "sessions":
            print(json.dumps(do_sessions(), indent=2))
            return 0
        print(f"multivac: {args.cmd} not yet implemented", file=sys.stderr)
        return 0
    except (ValueError, FileNotFoundError, OSError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
