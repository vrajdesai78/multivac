# CLI matrix — per-delegate flags, framing, and session handling

Source of truth for what `bin/multivac.py` actually builds and parses. Every argv, field
name, and quirk below is taken directly from `build_argv`/`parse_output`/`SPECS` in
`bin/multivac.py` and confirmed against `VERIFICATION.md`'s end-to-end run. If this file and
the code ever disagree, the code wins — but that should not happen; `tests/test_multivac.py`
asserts the exact argv shapes documented here.

Native `gemini` is **not** supported as a fifth delegate (Google retired the individual
Gemini CLI OAuth tier on 2026-06-18) — reach Gemini through `agy` (Antigravity), which fronts
it.

## codex

| | |
|---|---|
| Headless invocation (first call) | `codex exec --skip-git-repo-check --json <mode flags> [-c tools.web_search=true] [--model M] "<prompt>"` |
| Headless invocation (resume) | `codex exec resume <session_id> --skip-git-repo-check --json <mode_resume flags> [-c tools.web_search=true] [--model M] "<prompt>"` |
| Mode flags — `plan` | `-s read-only` |
| Mode flags — `edit` | `-s workspace-write --ask-for-approval never` |
| Mode flags — `full` | `--dangerously-bypass-approvals-and-sandbox` |
| Mode flags — `plan` (**resume**) | `-c sandbox_mode="read-only"` |
| Mode flags — `edit` (**resume**) | `-c sandbox_mode="workspace-write" -c approval_policy="never"` |
| Mode flags — `full` (**resume**) | `--dangerously-bypass-approvals-and-sandbox` (unchanged) |
| Output framing | **NDJSON** — one JSON lifecycle event per line, not one document |
| Answer field | last `item.completed` event where `item.type == "agent_message"` → `.item.text` |
| Session-id field | `thread.started` event → `.thread_id` |
| Session-id strategy | **parsed from output** — codex assigns the thread id, multivac reads it off the `thread.started` event on the first call and stores it |
| Resume form | `codex exec resume <id> ...` — note `resume <id>` comes immediately after `exec`, *before* the rest of the flags |
| Web search | off by default; `--web-search` → `-c tools.web_search=true` (**not** `--search`, which is interactive-only and errors under `exec`) |

**Quirk (prominent — this bit multivac during build):** `codex exec resume` **rejects** the
`-s`/`--sandbox` flag that the first-call invocation uses (`unexpected argument '-s'`). Resume
must instead set the sandbox via `-c sandbox_mode="<mode>"` (`SPECS["codex"]["mode_resume"]`
in `bin/multivac.py`). `mode_flags(tool, mode, resume=...)` is the single choke point that
picks the right table — never build codex resume argv from the first-call mode table.

## claude

| | |
|---|---|
| Headless invocation (first call) | `claude --print --output-format json --session-id <client-uuid> --permission-mode <mode> [--model M] "<prompt>"` |
| Headless invocation (resume) | `claude --print --output-format json --resume <session_id> --permission-mode <mode> [--model M] "<prompt>"` |
| Mode flags — `plan` | `--permission-mode plan` |
| Mode flags — `edit` | `--permission-mode acceptEdits` |
| Mode flags — `full` | `--permission-mode bypassPermissions` |
| Output framing | single **JSON** document |
| Answer field | `.result` (or `.structured_output`, re-serialized, when `--json-schema` was used) |
| Session-id field | `.session_id` |
| Session-id strategy | **client-generated** — multivac mints a `uuid.uuid4()` before the first call and passes it via `--session-id`; no parsing needed, and the returned `.session_id` should match what was sent |
| Resume form | `claude --resume <id> ...` (mode/model flags repeated every call — claude does not carry them over) |
| Web search | built in, no toggle; `--web-search` is a no-op for claude |

## grok

| | |
|---|---|
| Headless invocation (first call) | `grok --output-format json --no-auto-update <mode flags> [--model M] [--disable-web-search] --single "<prompt>"` |
| Headless invocation (resume) | `grok --output-format json --no-auto-update --resume <session_id> <mode flags> [--model M] [--disable-web-search] --single "<prompt>"` |
| Mode flags — `plan` | `--permission-mode plan` |
| Mode flags — `edit` | `--permission-mode acceptEdits` |
| Mode flags — `full` | `--permission-mode bypassPermissions` |
| Output framing | single **JSON** document |
| Answer field | `.text` |
| Session-id field | `.sessionId` (camelCase) |
| Session-id strategy | **parsed from output** — grok reassigns/ignores a client-supplied session id, so multivac never sends one on the first call and instead reads `.sessionId` back out of the response to store |
| Resume form | `--resume <id>` |
| Web search | **on by default in the underlying CLI** — multivac passes `--disable-web-search` unless `--web-search` is set, so multivac's off-by-default policy stays consistent across all four tools |

**Quirks (prominent):**
- **`--no-auto-update` is load-bearing, not cosmetic.** Grok hangs on
  `--output-format json` unless `--no-auto-update` is also passed — the auto-update check
  blocks headless execution waiting on a prompt that will never come. `build_argv` always
  includes it; do not drop it "to simplify."
- **The prompt is the *value* of `--single`, and must be last** — `grok --single "<prompt>"`,
  not a trailing bare positional. Passing it positionally caused a real bug caught during
  `VERIFICATION.md` and fixed in `build_argv`.

## agy

| | |
|---|---|
| Headless invocation (first call) | `agy <mode flags> [--model M] --print "<prompt>"` |
| Headless invocation (resume) | `agy -c <mode flags> [--model M] --print "<prompt>"` |
| Mode flags — `plan` | `--mode plan` |
| Mode flags — `edit` | `--mode accept-edits` |
| Mode flags — `full` | `--dangerously-skip-permissions` |
| Output framing | **plain text** — trimmed stdout, no JSON at all |
| Answer field | n/a — the entire trimmed stdout is the answer |
| Session-id field | none exposed |
| Session-id strategy | **none / best-effort** — agy exposes no session id in its output, so multivac has nothing to store; `-c` is a "continue most-recently-active conversation in this cwd" flag, not an id-based resume. In practice this means multivac's `--session LABEL` bookkeeping does not persist a real id for agy across calls — resume relies entirely on agy's own recency-in-cwd behavior, keyed by launching every resumed call from the same `--cwd` |
| Resume form | `-c` (continue-most-recent; **not** an id argument) |
| Web search | built in via Gemini grounding, no toggle; `--web-search` is a no-op for agy |

**Quirk (prominent):** the prompt is the *value* of `--print`, and must be last —
`agy --print "<prompt>"`, same shape as grok's `--single`, same bug class caught in
`VERIFICATION.md`. Also: agy can silently drop stdout entirely under a non-TTY pipe on some
platforms (empty stdout, exit 0) — `parse_output` treats empty trimmed stdout as an error, and
`do_ask` retries once through `run_child_pty` (a pseudo-tty wrapper) before giving up. See
`references/gotchas.md` for the full agy stdout-drop writeup.

## Cross-cutting rules (all four tools)

- **Long flags only, everywhere.** `-s` means `--sandbox` in codex but `--session-id` in
  grok; `-p` means print in agy/claude/grok but is not a mode flag anywhere. `build_argv`
  never emits a short flag, specifically to prevent one adapter's shorthand from being
  misread as another's.
- **Mode is always explicit.** There is no "no flag = default permission" path for any tool;
  `plan`/`edit`/`full` each map to a real, tested flag combination in `SPECS[tool]["mode"]`
  (first call) and `SPECS[tool]["mode_resume"]` where a tool needs a different form on
  resume (codex only, today).
- **Resume always re-passes mode/model/output flags.** No delegate is assumed to carry these
  over from the original call.
- **Exit 0 is never sufficient on its own.** `parse_output` requires a real answer
  (`agent_message` for codex, non-empty `.result`/`.text` for claude/grok, non-empty trimmed
  stdout for agy) — see `references/gotchas.md` item 4.
