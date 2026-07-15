# multivac — design spec

**Status:** approved-pending-review
**Date:** 2026-07-15
**Repo:** `multi-cli-skill` (public)

## Summary

`multivac` is a Claude Code skill that lets Claude synchronously invoke other
AI coding CLIs — **codex**, **agy** (Antigravity, which fronts Gemini/Claude/GPT-OSS),
**claude**, and **grok** — from inside a session, to get a second opinion, delegate a
coding task, or run a cross-CLI consensus. Every call rides the target CLI's **existing
subscription/OAuth login** — no per-model API keys.

The skill is one `SKILL.md` (tells Claude *when/how* to invoke) plus one dependency-free
Python 3 wrapper, `bin/multivac.py` (does the *how* reliably: flag mapping, timeouts,
session capture, output parsing, auth safety). It installs from a public repo with no
`pip`/`npm` step.

Named after Asimov's Multivac — the great computer you bring every question to.

## Goals

1. Synchronous (blocking) invocation of another CLI, returning its clean final answer.
2. Three explicit safety modes: `plan` (read-only, default), `edit`, `full`.
3. Guaranteed subscription auth — never fall back to API keys or non-OAuth paths.
4. Cross-CLI **consensus** (fan the same prompt to N tools in parallel).
5. **Resume** a prior delegate conversation across turns via named session labels.
6. `doctor` self-check that proves which CLIs are installed and logged in.
7. Trivial public install; stdlib-only; unit-tested core logic.

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
(**codex + agy + claude + grok**) on pure subscription auth in one lightweight skill; grok
and agy-as-Gemini are barely covered. multivac fills exactly that gap.

## The four CLIs (verified on target machine, subscription auth)

| Tool | Headless call | Auth on disk | Structured output | Resume |
|---|---|---|---|---|
| `codex` | `codex exec [--skip-git-repo-check] "P"` | `~/.codex/auth.json` (ChatGPT) | `--json`, `-o <file>` | `codex exec resume <uuid> "P"` |
| `agy` | `agy -p "P"` | `~/.antigravity`, `~/.gemini/antigravity-cli` (Google) | text only | `--conversation <id>` / `-c` (best-effort) |
| `claude` | `claude -p "P"` | macOS keychain OAuth (Claude Max) | `--output-format json` | `--resume <id>` / `-c` |
| `grok` | `grok -p "P"` | `~/.grok/auth.json` (x.ai OAuth) | `--output-format json`, `--json-schema` | `--resume <id>` / `-c` |

All four smoke-tested returning correct output on their existing logins (CODEX_OK,
CLAUDE_OK, grok OK, agy `models` lists Gemini/Claude/GPT-OSS).

## Architecture

```
multi-cli-skill/
├── README.md                 # install + usage for other users
├── SKILL.md                  # when/how Claude invokes multivac
├── bin/multivac.py           # the wrapper (Python 3 stdlib only)
├── references/
│   ├── cli-matrix.md         # per-CLI flag table — source of truth
│   └── gotchas.md            # agy stdout bug, timeouts, trusted dirs, auth
├── tests/test_multivac.py    # flag-mapping + session logic (subprocess mocked)
└── LICENSE
```

`SKILL.md` carries usage guidance and defers mechanics to `multivac.py`. The wrapper is
the single source of correct behavior so Claude does not re-derive flags each call.

## Component: `bin/multivac.py`

One CLI, four subcommands.

### `ask` — one delegate, one turn
```
multivac.py ask --tool codex|agy|claude|grok \
  (--prompt "..." | --prompt-file PATH) \
  [--mode plan|edit|full]      # default: plan
  [--model NAME] [--cwd DIR] [--timeout SECS] \
  [--session LABEL]            # resume if label exists for this tool, else start+record
  [--json]                     # emit full metadata instead of just the answer
  [--allow-api-keys]           # opt out of env scrubbing (default: scrub)
```
Blocks until the delegate finishes; prints the clean final message (or structured JSON).

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
| `plan` (read-only) | `-s read-only` | `--mode plan` | default headless (no skip flag) | `--permission-mode plan` |
| `edit` (auto edits) | `-s workspace-write --ask-for-approval never` | `--mode accept-edits` | `--permission-mode acceptEdits` | `--permission-mode acceptEdits` |
| `full` (auto all) | `--dangerously-bypass-approvals-and-sandbox` | `--dangerously-skip-permissions` | `--dangerously-skip-permissions` | `--always-approve` |

Exact per-CLI flag strings are pinned in `references/cli-matrix.md` and asserted by tests;
any flag not confirmed against the installed CLI version at build time is verified before
first use, not assumed.

## Subscription-auth guarantee

- Scrub `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `XAI_API_KEY`, `GEMINI_API_KEY`,
  `GOOGLE_API_KEY` from each child's environment unless `--allow-api-keys`.
- Never use `claude --bare` (forces `ANTHROPIC_API_KEY`/apiKeyHelper) or `codex --oss`.
- `doctor` surfaces any scrubbed key so the user knows subscription auth is in force.

## Session / resume

- State file: `$MULTIVAC_HOME/sessions.json` (default `./.multivac/sessions.json`).
- Shape: `{ "<label>": { "tool": "...", "session_id": "...", "created": "<iso>" } }`.
- `--session LABEL` on `ask`: if the label exists **for the same tool**, resume that
  session; otherwise start fresh and record the new id captured from the delegate's output.
- ID capture: codex from `--json` event stream; claude/grok from `--output-format json`
  `.session_id`. **agy is best-effort:** it does not cleanly surface a conversation id in
  headless mode, so agy resume falls back to `-c` (continue-most-recent) with a logged
  warning; a label is still recorded so intent is visible.

## Output & error handling

- Default output: the delegate's clean final message only. `--json` adds metadata
  (tool, mode, model, session_id, duration, exit code).
- Capture: codex `--json -o <tmp>` (read last-message file); claude/grok
  `--output-format json` (parse `.result`); agy plain stdout with a **non-empty check** to
  catch its documented non-TTY stdout-drop bug (retry once via a PTY if empty).
- Errors: non-zero exit → structured error (tool, exit code, stderr tail). "Not logged in"
  patterns → actionable message (e.g. "run `codex login`").
- Timeouts: default 180s (codex/claude/grok), 300s (agy — its own default is 5m). Child is
  killed on timeout; returns a clean timeout error. Overridable via `--timeout`.

## Testing (TDD)

- Unit tests (`tests/test_multivac.py`), `subprocess` mocked, no network:
  - mode → correct argv per tool (all 12 combinations),
  - env scrubbing removes the five keys unless `--allow-api-keys`,
  - session read/write/resume (including agy fallback path),
  - output parsing for each capture strategy,
  - timeout kills child and returns structured error.
- One env-gated live smoke test (`MULTIVAC_LIVE=1`): `doctor` + a real `plan` `ask` per
  installed CLI, asserting a known token round-trips.

## Known risks

1. **agy headless fragility** — `-p` stdout-drop under non-TTY and no clean session id.
   Mitigated by non-empty check + PTY retry and `-c` resume fallback; documented in gotchas.
2. **CLI flag drift** — these CLIs move fast; flags may change across versions.
   `cli-matrix.md` is the single source of truth and `doctor` can flag a mismatch.
3. **Trusted-directory prompts** — some CLIs refuse to act in an untrusted dir headlessly.
   Handled per tool in `cli-matrix.md` (e.g. pass the appropriate trust/skip flag).
4. **Long-running delegates** — mitigated by per-tool timeouts and the blocking contract
   (Claude waits; the user can interrupt).
