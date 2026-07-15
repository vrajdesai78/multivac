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
import argparse, json, os, re, signal, subprocess, sys, tempfile, time, uuid
from dataclasses import dataclass
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


def build_argv(req: Req, *, session_id=None, new_session_id=None, prompt=None):
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
        argv = ["grok", "--print", "--output-format", "json"]
        if resume:
            argv += ["--resume", session_id]
        elif new_session_id:
            argv += ["--session-id", new_session_id]; planned = new_session_id
        argv += ["--no-auto-update"]
        argv += mode_flags("grok", req.mode)
        if req.model:
            argv += ["--model", req.model]
        argv += _web_flags("grok", req.web_search)

    elif tool == "agy":
        argv = ["agy", "-p"]
        if resume:
            argv += ["-c"]                       # best-effort continue-most-recent
        argv += mode_flags("agy", req.mode)
        if req.model:
            argv += ["--model", req.model]

    argv.append(prompt if prompt is not None else req.prompt)
    return argv, planned


# Env vars always allowed through to children (allow-list, not os.environ wholesale).
_ENV_ALLOW = {
    "PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "LC_ALL", "TERM", "TMPDIR",
    "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME",
    "CODEX_HOME", "GROK_HOME", "MULTIVAC_HOME", "MULTIVAC_ALLOW_FULL",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "NODE_EXTRA_CA_CERTS",
}


def build_env(tool: str, *, allow_api_keys: bool = False, depth: int = 0, base=None) -> dict:
    src = dict(os.environ if base is None else base)
    env = {k: v for k, v in src.items() if k in _ENV_ALLOW}
    if allow_api_keys:
        for k in API_KEYS:
            if k in src:
                env[k] = src[k]
    env["MULTIVAC_DEPTH"] = str(depth + 1)
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
    return (None if timed_out else proc.returncode, out or "", err or "", timed_out)


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


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    print(f"multivac: {args.cmd} (not yet implemented)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
