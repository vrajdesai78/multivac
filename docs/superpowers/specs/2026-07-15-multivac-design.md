# multivac — design spec

**Status:** approved-pending-review
**Date:** 2026-07-15
**Repo:** `multi-cli-skill` (public)

## Summary

`multivac` lets an **orchestrator CLI — Claude Code or Codex — synchronously invoke and
message the other AI coding CLIs** as delegates: **codex**, **agy** (Antigravity, which
fronts Gemini/Claude/GPT-OSS), **claude**, and **grok**. From inside a host session you can
get a second opinion, delegate a coding task, target a **named or inline-defined subagent**
on the delegate, run a **cross-CLI consensus**, and **send follow-up messages** into a
resumable delegate conversation. Every call rides the target CLI's **existing
subscription/OAuth login** — no per-model API keys.

The core is one dependency-free Python 3 wrapper, `bin/multivac.py` — caller-agnostic, so it
works identically whichever CLI is orchestrating. Each host gets a thin packaging layer that
teaches it *when/how* to call the wrapper: **`SKILL.md`** for Claude Code, and a
**`~/.codex/prompts/multivac.md` slash-prompt + `AGENTS.md` snippet** for Codex. Installs
from a public repo with no `pip`/`npm` step.

Named after Asimov's Multivac — the great computer you bring every question to.

## Goals

1. **Orchestrator-agnostic**: drive delegates from either Claude Code *or* Codex as host
   (when Codex hosts, Claude Code is itself a delegate — the "claude inside codex" case).
2. Synchronous (blocking) invocation of another CLI, returning its clean final answer.
3. **Message** a delegate across turns: `ask --session <label>` sends a follow-up into a
   resumable conversation (first message creates it).
4. **Subagents**: run a delegate as a named agent (`--agent`) or an inline ad-hoc agent
   definition (`--agents`), passed through where supported, emulated where not.
5. Three explicit safety modes: `plan` (read-only, default), `edit`, `full`.
6. Guaranteed subscription auth — never fall back to API keys or non-OAuth paths.
7. Cross-CLI **consensus** (fan the same prompt to N tools in parallel).
8. `doctor` self-check that proves which CLIs are installed and logged in.
9. Trivial public install; stdlib-only; unit-tested core logic.

## Non-goals

- Not an MCP server (that niche is already covered by PAL `clink`, agent-multicli).
- Not an account/quota switcher (see CAAM) — one login per tool is assumed.
- No native `gemini` CLI: Google retired the individual Gemini Code Assist OAuth tier
  (2026-06-18); Gemini is reached **through `agy`** instead. Verified empirically on the
  target machine (`gemini -p` returns "client no longer supported… migrate to Antigravity").

## Prior art (from deep research) and the gap we fill

Closest existing tools: **dispatch** (sparkling-skills; codex + agy, no API keys, resume,
fan-out), **clink** in PAL MCP (subprocess subagents on native auth), **Counselors**
(parallel consensus across installed CLIs). **Gap:** no single tool covers our exact four
(**codex + agy + claude + grok**) on pure subscription auth, driven from **either Claude
Code or Codex as host**, with subagent targeting, in one lightweight skill; grok and
agy-as-Gemini are barely covered. multivac fills exactly that gap.

## Hosts / orchestrators (dual, first-class)

The wrapper is caller-agnostic — it does not care who invokes it. Each host gets a thin
packaging layer that points at the same `bin/multivac.py`:

| Host | How it learns multivac | Invocation |
|---|---|---|
| **Claude Code** | `SKILL.md` (installed to `~/.claude/skills/multivac/` or as a plugin) | Claude runs `multivac.py …` via its Bash tool |
| **Codex** | `~/.codex/prompts/multivac.md` (custom slash-prompt) **+** an `AGENTS.md` snippet users paste into `~/.codex/AGENTS.md` or a project `AGENTS.md` | Codex runs `multivac.py …` via its shell tool |

- **Delegate roster excludes the host by convention.** If Claude Code hosts, it delegates
  to `codex`/`agy`/`grok`; if Codex hosts, it delegates to `claude`/`agy`/`grok`. The host
  is auto-detected from the `MULTIVAC_HOST` env var (set by each packaging layer) so
  `consensus --tools all` and `doctor` skip the host. Self-invocation is allowed but warned.
- Both packaging layers are generated from a **single source of usage guidance** so they
  never drift; the Codex prompt and the SKILL.md share the same command reference.

## The four CLIs (verified on target machine, subscription auth)

| Tool | Headless call | Auth on disk | Structured output | Resume |
|---|---|---|---|---|
| `codex` | `codex exec [--skip-git-repo-check] "P"` | `~/.codex/auth.json` (ChatGPT) | `--json`, `-o <file>` | `codex exec resume <uuid> "P"` |
| `agy` | `agy -p "P"` | `~/.antigravity`, `~/.gemini/antigravity-cli` (Google) | text only | `--conversation <id>` / `-c` (best-effort) |
| `claude` | `claude -p "P"` | macOS keychain OAuth (Claude Max) | `--output-format json` | `--resume <id>` / `-c` |
| `grok` | `grok -p "P"` | `~/.grok/auth.json` (x.ai OAuth) | `--output-format json`, `--json-schema` | `--resume <id>` / `-c` |

### Verified invocation templates (tested end-to-end on target machine)

Answer + session-id extraction differs per tool — the adapter must special-case each:

| Tool | Answer field | Session-id field | Resume command (verified) |
|---|---|---|---|
| `codex` | JSONL `item.completed` where `item.type=="agent_message"` → `.item.text` | JSONL `thread.started.thread_id` | `codex exec resume <id> --skip-git-repo-check -c sandbox_mode="read-only" --json "P"` |
| `claude` | `.result` | `.session_id` | `claude -p --resume <id> --output-format json "P"` |
| `grok` | `.text` | `.sessionId` (camelCase, UUIDv7) | `grok -p --resume <id> --output-format json "P"` |
| `agy` | plain stdout (trim) | — (none exposed) | `agy -p -c "P"` (continue-most-recent; best-effort) |

**Critical quirk:** `codex exec resume` does **not** accept `-s/--sandbox` (only the initial
`codex exec` does). On resume the sandbox is set via `-c sandbox_mode="<mode>"` config
override, or `--dangerously-bypass-approvals-and-sandbox` for `full`. Verified: passing `-s`
to resume errors with `unexpected argument '-s'`.

**Web search (for research delegation).** Optional `--web-search` on `ask`/`consensus` maps
per CLI (verified): codex `-c tools.web_search=true` (**not** `--search`, which is
interactive-only — `codex exec --search` errors); grok is web-enabled by default
(`--disable-web-search` to turn off); claude has built-in web search/fetch; agy fronts
Gemini which has grounded search. Used to delegate "research this on the web / Reddit" tasks
(this design's own research was run through `codex exec -c tools.web_search=true`).

All four smoke-tested end-to-end: a first `ask` (6×7 → "42") and a `resume` (×2 → "84")
each returned correct results on their existing subscription logins. agy `-p` returned clean
stdout non-TTY in v1.1.2 (the documented stdout-drop bug did not reproduce, but the
non-empty guard is retained defensively).

## Architecture

```
multi-cli-skill/
├── README.md                    # install + usage for both hosts
├── SKILL.md                     # Claude Code host: when/how to invoke multivac
├── codex/
│   ├── prompts/multivac.md      # install to ~/.codex/prompts/ (Codex slash-prompt)
│   └── AGENTS.snippet.md        # paste into ~/.codex/AGENTS.md or a project AGENTS.md
├── bin/multivac.py              # the wrapper (Python 3 stdlib only)
├── references/
│   ├── usage.md                 # single source of usage guidance (SKILL.md + codex share)
│   ├── cli-matrix.md            # per-CLI flag table — source of truth
│   └── gotchas.md               # agy stdout/session, timeouts, trusted dirs, auth
├── tests/test_multivac.py       # flag-mapping + session + subagent logic (subprocess mocked)
└── LICENSE
```

Both host layers carry usage guidance (generated from `references/usage.md`) and defer
mechanics to `multivac.py`. The wrapper is the single source of correct behavior so neither
host re-derives flags each call.

## Component: `bin/multivac.py`

One CLI, four subcommands.

### `ask` — one delegate, one turn (or one message into a session)
```
multivac.py ask --tool codex|agy|claude|grok \
  (--prompt "..." | --prompt-file PATH) \
  [--mode plan|edit|full]      # default: plan
  [--agent NAME]               # run delegate as a named subagent (passthrough/emulated)
  [--agents JSON|@FILE]        # inline ad-hoc subagent definition for this call
  [--model NAME] [--cwd DIR] [--timeout SECS] \
  [--session LABEL]            # send into an existing thread (resume), else start+record
  [--json]                     # emit full metadata instead of just the answer
  [--allow-api-keys]           # opt out of env scrubbing (default: scrub)
```
Blocks until the delegate finishes; prints the clean final message (or structured JSON).
Sending a follow-up **message** to a delegate is just `ask --session <label>` again.

### `consensus` — many delegates, same prompt, parallel
```
multivac.py consensus --tools codex,agy,grok|all \
  (--prompt "..." | --prompt-file PATH) [--mode plan|edit|full] [--timeout SECS] [--json]
```
Spawns one child per tool concurrently (thread pool), waits for all, returns each tool's
answer collected under its name. Partial failures are reported per-tool, not fatal.

### `doctor` — readiness
Reports, per tool: installed? logged in (subscription)? any API-key env var set that would
be scrubbed? Exit non-zero if a requested tool is unusable. Used before relying on multivac.

### `sessions` — inspect saved labels
Lists `label → {tool, session_id, created}` from the state file.

## Mode → flag mapping (the safety abstraction)

Default `plan`. Write modes are explicit per call.

| Mode | codex | agy | claude | grok |
|---|---|---|---|---|
| `plan` (read-only) | `-s read-only` | `--mode plan` | `--permission-mode plan` | `--permission-mode plan` |
| `edit` (auto edits) | `-s workspace-write --ask-for-approval never` | `--mode accept-edits` | `--permission-mode acceptEdits` | `--permission-mode acceptEdits` |
| `full` (auto all) | `--dangerously-bypass-approvals-and-sandbox` | `--dangerously-skip-permissions` | `--permission-mode bypassPermissions` | `--permission-mode bypassPermissions` |

Verified from `--help`: claude `--permission-mode` accepts `plan|acceptEdits|bypassPermissions`
(so plan is an explicit read-only mode, not the absence of a flag); grok `--permission-mode`
accepts `plan|acceptEdits|bypassPermissions` too. On `codex exec resume`, `-s` is rejected —
sandbox goes via `-c sandbox_mode="…"`.

**Short-flag collision — internal rule: always build argv with long flags.** `-s` means
`--sandbox` in codex but `--session-id` in grok; `-p` is print in agy/claude/grok but not a
mode anywhere. The wrapper never uses short flags in generated argv, to prevent cross-adapter
mistakes. Exact strings are pinned in `references/cli-matrix.md` and asserted by tests; any
flag not confirmed against the installed CLI version at build time is verified before first
use, not assumed.

### Session-id strategy (differs by CLI — verified)

| CLI | Strategy | Mechanism |
|---|---|---|
| `claude` | **generate client-side** | pass `--session-id <uuid>` on first call, `--resume <uuid>` after — no parsing needed |
| `grok` | **generate client-side** | pass `--session-id <uuid>` (new), `--resume <uuid>` after |
| `codex` | **parse from output** | read `thread.started.thread_id` from `--json`; resume `codex exec resume <id>` |
| `agy` | **best-effort** | `--conversation <id>` resumes by id, but id isn't in stdout; recover it from `--log-file <tmp>` if parseable, else fall back to `-c` |

Generating the UUID up front (claude, grok) is preferred: multivac always knows the label→id
mapping without depending on output parsing.

## Subagents (`--agent` / `--agents`)

Two ways to run a delegate as a specialized subagent instead of a bare prompt:

- `--agent NAME` — run the delegate as a pre-defined named agent (e.g. `security-reviewer`).
- `--agents JSON|@FILE` — define an ad-hoc agent inline for this one call. Canonical shape:
  `{"name","description","prompt"(system prompt),"tools"?}` (matches Claude/Grok's own
  `--agents` JSON so it passes straight through).

Support differs per delegate (verified against installed CLIs):

| Delegate | `--agent NAME` | `--agents JSON` (inline) | Mechanism |
|---|---|---|---|
| `claude` | ✅ passthrough `--agent` | ✅ passthrough `--agents` | native |
| `grok` | ✅ passthrough `--agent` | ✅ passthrough `--agents` | native |
| `agy` | ✅ passthrough `--agent` | ⚠️ no inline flag | named native; inline **emulated** |
| `codex` | ⚠️ no agent flag | ⚠️ no agent flag | both **emulated** |

**Emulation uses native system-prompt flags where they exist, preamble only as last resort:**

| Delegate | Inline-agent system prompt applied via |
|---|---|
| `claude` | `--agents` (native) or `--append-system-prompt` |
| `grok` | `--agents` (native) or `--system-prompt-override` |
| `agy` | prompt preamble (no system-prompt flag) |
| `codex` | prompt preamble; if the agent implies review, route to `codex exec review` |

Preamble form when needed: "You are acting as the `<name>` agent. <system prompt>. Task:
<prompt>." A named `--agent` with no local definition on an emulated delegate is an error
with guidance (define it inline or via the local registry).
Delegates that spawn their **own** internal subagents mid-task (claude, grok, agy) are left
free to do so in `edit`/`full`; multivac never passes `--no-subagents` unless asked.

Optional local agent registry: `--agent NAME` may resolve from `references/agents/<name>.json`
so the same named agent works across all delegates (passed through natively where supported,
emulated where not). This keeps "run the security-reviewer" identical regardless of delegate.

## Execution contract (hardened from real-world failures)

Delegated research (codex, Reddit + GitHub, 2026-07-15 — see `references/known-issues.md`)
found that the hardest failures across all these CLIs are **process-contract failures**, not
model failures. These are *exactly* why a tested wrapper beats hand-rolled per-call Bash.
Every child process launched by multivac obeys this contract:

1. **Own the child's stdin.** Default `stdin=DEVNULL`; when piping a prompt, write the full
   payload and close. *Never* inherit the parent's open stdin — codex, gemini and claude all
   **hang forever** ("Reading additional input from stdin…", 0% CPU) on an inherited open
   pipe, reproduced specifically through Claude Code's Bash tool. (Codex #20919, Gemini
   ref, Claude changelog.)
2. **Never merge stdout and stderr.** Parse the result only from stdout (or `-o` file);
   diagnostics, OAuth prompts and (in some codex builds) a *duplicate* final answer land on
   stderr. (Codex #12566, Gemini #3983/#5602.) Earlier ad-hoc tests used `2>&1` — the wrapper
   must not.
3. **Exit 0 is not success.** Require a terminal event / non-empty result artifact before
   treating a run as done — codex and agy have both returned exit 0 with empty output.
   (Codex #9091, agy #76.)
4. **Know the framing per tool.** codex `--json` and `*-stream-json` are **NDJSON** (parse
   line-by-line); claude `--output-format json` and grok `--output-format json` are a
   **single object**. With `--json-schema`, claude puts the payload in `structured_output`,
   not `result`. Tolerate additive envelope fields.
5. **Enforce an external wall-clock timeout and kill the whole process group.** CLI-level
   timeouts (e.g. agy `--print-timeout`) do **not** cover auth/startup/compaction hangs.
   Spawn children in a new session/process-group and `killpg` on expiry. (agy plugin CHANGELOG,
   grok compaction hangs, gemini `MODEL_CAPACITY_EXHAUSTED` stalls.)
6. **Repeat all settings on resume.** model, sandbox/mode, approval, output-format do **not**
   carry over — re-pass them every resume call (already proven for codex `-c sandbox_mode`;
   grok lost sandbox on resume pre-0.2.56).
7. **Guard input size.** claude rejects piped stdin > 10 MB; agy silently truncated a large
   prompt and then hallucinated having read files. Check byte size before spawning; put large
   context in a file the delegate reads, and don't trust "I read it" without evidence.
8. **Pin/disable auto-update in automation.** grok auto-updates unless `--no-auto-update`;
   protocol churn between jobs is a real failure. Record each tool's version in `doctor`.

## Orchestration safety (nesting, concurrency, isolation)

Because the whole point is one agent CLI driving others, the multi-agent failure modes are
first-class design concerns:

- **Recursion guard.** multivac sets `MULTIVAC_DEPTH` in each child and **refuses** to spawn
  past `--max-depth` (default 2). This stops accidental exponential fan-out when a delegate
  reads an `AGENTS.md`/`GEMINI.md` that itself says "consult another agent." A per-call
  `--max-children` bounds a single `consensus`.
- **tmp/session isolation for nested claude.** A nested `claude -p` in the same cwd can purge
  the parent's `/tmp/claude-<uid>/…/tasks` dir and blank the parent's Bash result (Reddit,
  #issue). When the delegate is `claude`, multivac sets an isolated `CLAUDE_CODE_TMPDIR` and
  only clears the `CLAUDECODE` nesting marker inside an isolated workspace.
- **cwd is hidden state.** claude/gemini/grok/agy all locate sessions by working directory.
  multivac records the **canonical cwd alongside the session id**, passes children an explicit
  `cwd`, and never lets a delegate `cd` the long-lived parent. "Resume last" is never used for
  identity in concurrent jobs.
- **Bounded fan-out + budget.** `consensus` uses one semaphore (default concurrency 3), not
  N unbounded children — ~25 concurrent codex children have tripped backend 429s that look
  like a DDoS (Codex #11083). A shared retry budget with jittered backoff, not per-child loops.
- **Allow-listed environment (superset of key-scrubbing).** Children get a constructed,
  allow-listed env — not `os.environ` wholesale — so auth-selection vars, `GH_TOKEN`, proxy
  vars and recursion markers can't leak or silently switch identity/billing.

## Subscription-auth guarantee

- Build each child's env from an **allow-list**, dropping `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`,
  `XAI_API_KEY`, `GEMINI_API_KEY`, `GOOGLE_API_KEY` (unless `--allow-api-keys`) — an inherited
  key silently moves usage onto **metered API billing** (documented for claude and grok).
- Never use `claude --bare` (forces `ANTHROPIC_API_KEY`/apiKeyHelper) or `codex --oss`.
- `doctor` surfaces any scrubbed key so the user knows subscription auth is in force.
- **Honest cost caveat (not "free"):** "subscription, no API keys" means *no per-model API
  key* — but headless usage still draws on each plan's terms, and these changed in mid-2026:
  `claude -p` now consumes a separate **Agent SDK** monthly credit (then pay-as-you-go) rather
  than the plain subscription allowance; codex/grok/agy have their own headless quotas. multivac
  surfaces `total_cost_usd` where a CLI reports it and supports per-call budget/turn caps.

## Session / resume

- State file: `$MULTIVAC_HOME/sessions.json` (default `./.multivac/sessions.json`,
  `chmod 600`), written transactionally (temp file + atomic rename).
- Shape: `{ "<label>": { "tool", "session_id", "cwd" (canonical), "version", "created" } }`.
- `--session LABEL` on `ask`: if the label exists **for the same tool and cwd**, resume;
  otherwise start fresh and record. Resume re-passes all mode/model/output flags (contract #6).
- ID handling (verified): **generate client-side** for claude/grok (`--session-id <uuid>`);
  **parse** `thread.started.thread_id` for codex; **agy best-effort** — recover the
  conversation id from `--log-file <tmp>` if parseable, else fall back to `-c` with a warning.
  cwd is always stored so a concurrent job can't resume the wrong transcript.

## Output & error handling

- Default output: the delegate's clean final message only (stdout/`-o` channel, per contract
  #2). `--json` adds metadata (tool, mode, model, session_id, cwd, duration, exit code, cost).
- Capture: codex `--json -o <tmp>` (NDJSON events + last-message file); claude/grok
  `--output-format json` (single object → `.result`/`.text`; `structured_output` under a
  schema); agy plain stdout with a **non-empty check** + one PTY retry for its stdout-drop bug.
- Errors: non-zero exit **or** missing terminal event → structured error (tool, exit code,
  stderr tail). "Not logged in" / untrusted-folder / capacity patterns → actionable messages
  (e.g. "run `codex login`", "pass `--trust`/pre-trust the dir").
- Timeouts: external wall-clock, default 180s (codex/claude/grok), 300s (agy). On expiry the
  whole process group is killed. Overridable via `--timeout`.

## Testing (TDD)

- Unit tests (`tests/test_multivac.py`), `subprocess` mocked, no network:
  - mode → correct argv per tool (all 12 combinations), **long-flags only**,
  - env is allow-listed: the five keys + `CLAUDECODE`/proxy vars dropped unless opted in,
  - child is spawned with `stdin=DEVNULL` and in its own process group (contract #1, #5),
  - result parsed from stdout only, never merged with stderr (contract #2),
  - exit 0 with empty output → error, not success (contract #3),
  - NDJSON vs single-object framing parsed correctly per tool (contract #4),
  - session read/write/resume re-passes mode/model flags; matches on tool **and cwd**;
    agy log-file recovery + fallback path,
  - `--mode full` without `--yes`/`MULTIVAC_ALLOW_FULL` refuses,
  - `--max-depth` refusal when `MULTIVAC_DEPTH` is at the limit,
  - input-size guard rejects oversized prompts before spawning,
  - timeout kills the whole process group and returns a structured error.
- One env-gated live smoke test (`MULTIVAC_LIVE=1`): `doctor` + a real `plan` `ask` per
  installed CLI, asserting a known token round-trips and a session resumes.

## Security model

Because the delegates are agentic CLIs that can execute shell commands, edit files, and
reach the network, **multivac is a privilege-delegation tool** — its security is about
containing that delegation. This matters more than usual because the intended distribution
is *install locally and review the source*, so the wrapper must be small, boring, and
auditable. Principles, in priority order:

1. **Read-only by default, with verified enforcement.** Default mode `plan` maps to each
   CLI's strongest read-only setting. Empirically, `codex -s read-only` is **OS-sandbox
   enforced**: a write attempt returns `operation not permitted` and no file is created —
   not honor-system. grok/claude/agy `plan` modes are enforced by each CLI's own permission
   layer (strong, but not a kernel sandbox). This difference is documented, not hidden.
   multivac never silently escalates; `edit`/`full` are explicit per call.
2. **`full` is quarantined behind an explicit ack.** `--mode full` maps to the
   `--dangerously-*` bypass flags. It is never a default and requires the literal `full`
   **plus** an explicit acknowledgement — `--yes` or `MULTIVAC_ALLOW_FULL=1` — or the wrapper
   refuses and explains (an interactive confirm can't work in a headless call, so the gate is
   an explicit flag, not a prompt). It prints a one-line stderr warning naming the tool and
   cwd. Both host layers (`SKILL.md`, Codex prompt) forbid selecting `full` without the
   user's clear, specific intent.
3. **No shell string interpolation.** The wrapper builds argv as a list and never uses
   `shell=True`. The prompt is passed as a single argv element (or via stdin / `--prompt-file`),
   so nothing in the prompt can be reinterpreted as a shell command.
4. **Session labels can't traverse the filesystem.** Labels are JSON object keys inside one
   state file, never path components; still validated to `[A-Za-z0-9._-]{1,64}`.
5. **Subscription-auth scrubbing** (also a security property). The five provider API-key env
   vars (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `XAI_API_KEY`, `GEMINI_API_KEY`,
   `GOOGLE_API_KEY`) are removed from every child unless `--allow-api-keys`, and `--bare` /
   `--oss` are banned — so a call cannot silently switch to a different billed identity.
   Each delegate still reads its **own** on-disk OAuth (`~/.codex/auth.json`, etc.);
   multivac neither reads nor forwards those.
6. **Blast-radius controls.** Per-tool timeouts (child killed on expiry), optional `--cwd`
   to scope the delegate's working root, and `.multivac/` state written `chmod 600`.
7. **Auditable by construction.** One file, Python stdlib only (no third-party code to vet),
   no network calls of its own, no telemetry, no auto-update, and it never `eval`/`exec`s
   model output — it only relays text. A "what this does / does NOT do" block sits at the
   top of the script and README.

8. **The delegate itself may be untrustworthy.** A July 2026 community report alleged a grok
   build uploaded a repo bundle (incl. git history) to vendor storage; unverified, but multivac
   defaults to read-only, recommends disposable checkouts + `--cwd` scoping for sensitive repos,
   and never disables a CLI's own network egress on the user's behalf.

### Residual risk (documented, not solved)
A delegate run in `edit`/`full` on **untrusted input** can be prompt-injected into
destructive or exfiltrating actions — inherent to delegating to an agent that has its own
credentials and filesystem access. Mitigations: read-only default, `--cwd` scoping,
recursion/tmp isolation, and host-layer guidance to keep untrusted-content tasks in `plan`.
multivac reduces blast radius; it cannot make a full-access agent safe on hostile input.

## Other known risks

1. **agy headless fragility** — no session id in `-p`; resume is best-effort via `-c`.
   Non-empty stdout guard retained against the documented (non-reproduced in v1.1.2)
   stdout-drop bug.
2. **CLI flag drift** — these CLIs move fast. `cli-matrix.md` is the single source of truth;
   `doctor` flags a version/flag mismatch before it bites.
3. **Trusted-directory prompts** — some CLIs refuse to act in an untrusted dir headlessly
   (verified: `gemini` did; handled per tool in `cli-matrix.md` via the right trust/skip flag).
4. **Long-running delegates** — bounded by per-tool timeouts and the blocking contract
   (Claude waits; the user can interrupt).
