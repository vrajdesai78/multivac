# Gotchas — condensed digest

`references/known-issues.md` is the full research writeup (Reddit + GitHub citations,
researched 2026-07-15). This file is the actionable summary: what can go wrong when shelling
out to another AI CLI, and exactly how `bin/multivac.py` handles it. Read `known-issues.md`
for citations; read this file to know what's already covered and what still needs caller
discipline.

## The Top 10 execution-contract rules

Each rule below is one of the "Top 10 gotchas a wrapper MUST handle" from
`known-issues.md`, mapped to the code that implements it.

1. **Own stdin.** A child that inherits an open, unwritten pipe (e.g. Codex under Claude
   Code's Bash tool) can hang forever waiting for EOF. **Multivac handles this by** always
   passing `stdin=subprocess.DEVNULL` unless `stdin_data` is explicitly supplied
   (`run_child` in `bin/multivac.py`) — no delegate ever inherits the parent's stdin.

2. **Know the framing before parsing.** Codex's `--json` is NDJSON (one lifecycle event per
   line); claude/grok's `--output-format json` is a single JSON document; agy is plain text.
   Treating NDJSON as one `json.loads()` call reads only the first event or throws.
   **Multivac handles this by** keying `SPECS[tool]["framing"]` and branching in
   `parse_output` — line-by-line NDJSON scan for codex, single `json.loads` for claude/grok,
   raw trimmed text for agy.

3. **Never merge stdout and stderr before parsing.** Codex has, in some versions, duplicated
   the final answer onto both streams; OAuth/diagnostic noise from other CLIs has also
   leaked onto stdout. **Multivac handles this by** capturing `stdout`/`stderr` as separate
   pipes in `run_child` and parsing only `stdout` in `parse_output` — stderr is used solely
   for `map_error`'s login/trust-error heuristics, never for the answer.

4. **Exit code 0 is not sufficient.** Some CLI versions have exited 0 with empty captured
   output. **Multivac handles this by** requiring a real terminal artifact regardless of exit
   code: `parse_output` raises `ValueError` if codex never emitted an `agent_message`, if
   claude/grok's answer field is `None`/blank, or if agy's trimmed stdout is empty — each of
   which is treated as a failed `Result`, not a success.

5. **Capture exact session IDs; never rely on "resume last."** Cwd-scoped or
   recency-based session pickers can resume the wrong transcript under concurrent jobs.
   **Multivac handles this by** having `SessionStore` key every record on `(label, tool, cwd)`
   and only returning a stored id when both `tool` and the *canonical absolute* `cwd` match
   the request (`SessionStore.get`) — a label reused in a different directory or against a
   different tool starts fresh instead of silently resuming the wrong conversation. (agy is
   the one exception: it exposes no session id at all, so multivac cannot enforce this for
   agy beyond passing `--cwd` consistently — see the cli-matrix agy row.)

6. **Repeat model/sandbox/approval/output flags on every resume.** No delegate is assumed to
   remember them. **Multivac handles this by** rebuilding the full mode/model/output argv on
   every call in `build_argv`, resume included — `mode_flags(tool, mode, resume=...)` is
   called for both first-call and resume paths, and the codex resume path specifically swaps
   `-s` for `-c sandbox_mode="..."` because `codex exec resume` rejects `-s` outright.

7. **Enforce an external wall-clock timeout and kill the whole process group.** A CLI's own
   `--timeout`-style flag has repeatedly failed to cover login prompts, compaction, or
   capacity stalls. **Multivac handles this by** launching every child with
   `start_new_session=True` (its own process group) and, on `subprocess.TimeoutExpired`,
   calling `os.killpg(os.getpgid(pid), SIGKILL)` — not just `proc.kill()` on the top-level
   PID — in `run_child` (and the PTY equivalent in `run_child_pty`). Default timeouts are
   180s for codex/claude/grok and 300s for agy (`DEFAULT_TIMEOUTS`), overridable with
   `--timeout`.

8. **Use an allow-listed environment.** Inherited `ANTHROPIC_API_KEY`/`XAI_API_KEY`/
   `GH_TOKEN`/proxy vars can silently change auth method, billing, or child tool behavior.
   **Multivac handles this by** building each child's environment from `_ENV_ALLOW` — a
   fixed allow-list, not `os.environ` wholesale — in `build_env`, scrubbing all five
   provider API-key variables unless `--allow-api-keys` is passed, and always stamping
   `MULTIVAC_DEPTH` so nested invocations are visible to the next hop.

9. **Give every concurrent agent its own temp/state directory.** A nested Claude instance in
   the same cwd has been observed purging the parent's `/tmp/claude-<uid>/tasks` directory.
   **Multivac handles this by** popping `CLAUDECODE` and setting a fresh
   `CLAUDE_CODE_TMPDIR = tempfile.mkdtemp(...)` for every claude child in `build_env` — the
   nested process gets an isolated tmpdir instead of colliding with the parent's.

10. **Impose global concurrency/turn/cost/depth budgets before fan-out.** Unbounded parallel
    retries have been reported to look like abuse and trigger provider-side rate limiting.
    **Multivac handles this by** bounding `consensus` fan-out with a `ThreadPoolExecutor` of
    `--concurrency` workers (default 3, not unbounded) in `do_consensus`, and gating
    recursion globally via `MULTIVAC_DEPTH` vs. `--max-depth` (default 2) in `do_ask` — a
    delegate that itself tries to "consult another agent" cannot fan out exponentially.

## Highest-impact per-CLI issues

**codex**
- **Deterministic hang on inherited non-TTY stdin** (issue #20919) — covered by rule 1 above.
- **`--json` is NDJSON, not one document** — covered by rule 2.
- **`codex exec resume` rejects `-s`** — covered by rule 6; see `references/cli-matrix.md`
  for the exact resume flag substitution.
- **Fan-out can look like abuse (~25 subagents → 429s, issue #11083)** — covered by rule 10.

**claude**
- **Nested claude can purge the parent's tmp task directory** (Reddit-reported) — covered by
  rule 9.
- **`-p` sessions don't appear in the interactive picker** but remain resumable by the
  captured session id from the original cwd — this is *expected* behavior for multivac
  (client-generated uuid + cwd match), not a bug to route around.
- **Inherited `ANTHROPIC_API_KEY` silently moves usage onto metered billing** — covered by
  rule 8.
- **Piped stdin has a hard 10 MB limit** in claude itself — multivac's own 5 MB
  `MAX_PROMPT_BYTES` guard in `do_ask` rejects oversized prompts before spawning the child,
  well under that ceiling.

**grok**
- **`--output-format json` hangs without `--no-auto-update`** — the single highest-impact
  grok-specific gotcha; `build_argv` always includes `--no-auto-update` for grok, no flag to
  disable it. See `references/cli-matrix.md`.
- **The prompt must be the value of `--single`, not a trailing positional** — bug caught and
  fixed during `VERIFICATION.md`; `build_argv` appends `["--single", prompt]` as the last
  two argv elements for grok.
- **Auto-update / auth-selection instability across versions** — mitigated by pinning to
  long flags and an allow-listed environment (rule 8); `doctor` surfaces the installed
  version so a caller can correlate odd behavior with a known changelog fix.
- **Community-reported repo-exfiltration risk (v0.2.93, unconfirmed by xAI)** — out of scope
  for multivac to fix directly; mitigated at the policy layer by keeping untrusted-content
  tasks in `plan` mode and pointing `--cwd` at disposable checkouts (see `usage.md`'s Safety
  notes).

**agy**
- **Exit 0 with completely empty stdout under non-TTY pipes** (issue #76) — covered by rule 4
  plus a dedicated fallback: `do_ask` catches the "empty stdout" `ValueError` from
  `parse_output` and retries once through `run_child_pty`, a pseudo-tty wrapper, before
  reporting failure.
- **The prompt must be the value of `--print`, not a trailing positional** — same bug class
  as grok's `--single`, fixed in the same commit.
- **No session id exposed; resume is cwd-scoped recency, not identity** — documented as a
  known limitation in `references/cli-matrix.md` rather than papered over; `--session LABEL`
  bookkeeping is a no-op for agy beyond keeping `--cwd` consistent across calls.
- **Large piped prompts can be silently truncated** (issue #224) — mitigated by the same 5 MB
  `MAX_PROMPT_BYTES` guard used for all four tools.

## Cross-cutting orchestration risks (not solved by any single flag)

- **Cwd is part of hidden state** for every tool that supports resume — `do_ask` always
  resolves and passes a canonical absolute `cwd`, and `SessionStore` keys on it, but a
  caller that lets the model `cd` the *parent* process mid-run still breaks this invariant.
- **Concurrent agents editing the same checkout can silently revert each other's work** — out
  of scope for a single-shot wrapper; the mitigation is operational (one worktree per agent),
  not something `multivac.py` enforces.
- **Untrusted content in the prompt is a prompt-injection surface once a delegate has
  filesystem/network access** — multivac reduces blast radius (mode gating, `--cwd`
  isolation, no shell interpolation) but does not make `edit`/`full` mode safe against
  hostile input; see the Safety notes in `references/usage.md`.

Full citations for every item above live in `references/known-issues.md`.
