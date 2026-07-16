# multivac — usage reference

`multivac` lets a host CLI (Claude Code or Codex) synchronously invoke other AI coding CLIs
— **codex**, **agy** (Antigravity, which fronts Gemini/Claude/GPT-OSS), **claude**, **grok**
— as delegates, on their own existing subscription/OAuth logins. This is the single command
reference; both host packagings (Claude Code's `SKILL.md`, Codex's slash-prompt) point here.

**What multivac does:** builds argv for each delegate CLI, runs it as a child process under
a strict execution contract (owned stdin, split stdout/stderr, its own process group, killed
on timeout), and relays its clean final answer.

**What multivac does NOT do:** it never `eval`/`exec`s model output, makes no network calls
of its own, has no telemetry, and never reads or forwards your OAuth tokens — each delegate
reads its own on-disk login. There is no native `gemini` support (Google retired the
individual Gemini CLI OAuth tier); reach Gemini through `agy`.

Every example below runs from the repo root as `python3 bin/multivac.py ...`. When installed
as a skill, replace that prefix with the path under the skill directory (see `SKILL.md`).

## Subcommands

### `ask` — one call to one delegate

```
python3 bin/multivac.py ask --tool codex --prompt "Review this diff for race conditions"
```

Required: `--tool {codex,agy,claude,grok}` and one of `--prompt "..."` / `--prompt-file path`.

Key flags:

- `--mode {plan,edit,full}` — see [Modes](#modes) below. Default `plan`.
- `--model MODEL` — pass a specific model name through to the delegate.
- `--cwd PATH` — run the delegate rooted at a specific directory instead of the caller's cwd
  (see [Safety notes](#safety-notes) for why this matters on sensitive repos).
- `--timeout SECONDS` — override the per-tool default wall-clock timeout.
- `--agent NAME` / `--agents JSON` — target a subagent. See [Subagents](#subagents-agent---agents).
- `--session LABEL` — send this into a resumable conversation. See [Sessions](#sessions---session).
- `--web-search` — see [Web search](#web-search---web-search).
- `--allow-api-keys` — let the five provider API-key env vars through to the child (off by
  default; see [Safety notes](#safety-notes)).
- `--json` — emit a structured result object (`tool`, `ok`, `answer`, `session_id`, `cwd`,
  `exit_code`, `duration_s`, `cost_usd`, `error`) instead of the plain answer.
- `--max-depth N` — recursion ceiling, default 2. See [Safety notes](#safety-notes).
- `--yes` — explicit acknowledgement required for `--mode full`.
- `--files a.py,b.py` — attach the contents of the listed files to the prompt as read-only
  context, so the delegate gets the exact files (rather than only what it reads from `--cwd`
  on its own). Total attached size is capped at 5 MB. Works on `ask` and `consensus`.

### `consensus` — fan the same prompt out to N delegates

```
python3 bin/multivac.py consensus --tools codex,grok --prompt "What is 6 times 7? Reply with just the number."
```

```
===== codex =====
42
===== grok =====
42
```

`--tools` is a comma list or the literal `all` (which expands to every tool except the
current host, read from `MULTIVAC_HOST`). `--concurrency N` bounds parallel children
(default 3 — deliberately not unbounded, to avoid tripping a provider's abuse/rate-limit
detection).

**`--synthesize`** adds a reconcile step: after the fan-out, one delegate merges all the
answers into a single best answer and flags where they agreed/disagreed (a mixture-of-agents
step — synthesis tends to beat raw side-by-side on tricky logic). `--judge <tool>` names the
synthesizer (default: the first tool in `--tools`). The synthesizer always runs read-only.

```
python3 bin/multivac.py consensus --tools codex,grok,agy --synthesize --prompt "..."
# ... per-tool answers ..., then:
===== synthesis (via codex) =====
<one reconciled answer>
Agreements: ...
Disagreements: ...
```

`consensus` shares every other flag with `ask` (`--mode`, `--model`, `--cwd`, `--timeout`,
`--web-search`, `--files`, `--allow-api-keys`, `--yes`, `--json`, `--max-depth`) except
`--session`: **consensus runs are stateless** — no session is created or resumed, since the
same prompt is fanned out fresh to every tool.

### `doctor` — is each CLI installed, and what would be scrubbed?

```
multivac doctor
```

```
  ✓ codex   codex-cli 0.144.4
  ✓ agy     1.1.3
  ✓ claude  2.1.211 (Claude Code)
  ✓ grok    grok 0.2.101 (5bc4b5dfadcf)
```

Add `--json` for machine-readable output (the same rows as objects with `installed`,
`version`, `scrubbed_keys`, `note`). `doctor` confirms each delegate CLI is installed (with
version) and reports any API-key env vars that would be scrubbed. It does NOT verify login —
a delegate can be installed but not logged in; that surfaces on the first real `ask` as a
"not logged in — run `<tool> login`" error. `--tools` narrows the check to a comma list
(default `all`).

### `sessions` — list recorded resumable sessions

```
python3 bin/multivac.py sessions
```

Dumps `$MULTIVAC_HOME/sessions.json` (default `./.multivac/sessions.json`, written
`chmod 600`): label → `{tool, session_id, cwd, created}`.

## Modes

Three explicit, mutually exclusive modes, mapped to each delegate's own permission system:

- **`plan` (default, read-only).** No file writes, no destructive shell commands. For
  `codex` this is OS-sandbox enforced (`-s read-only`; a write attempt is refused at the
  kernel level, not just by convention). For `agy`/`claude`/`grok` it's enforced by the
  CLI's own permission layer — strong, but not a kernel sandbox. Use this for anything
  read-only: reviews, second opinions, Q&A, analysis of untrusted content.
- **`edit`.** The delegate can edit files in its working directory but does not bypass its
  own approval/sandbox policy for anything riskier.
- **`full`.** Auto-approves *all* delegate actions (maps to each CLI's
  `--dangerously-*`-style bypass flag). **Refused unless explicitly acknowledged**: pass
  `--yes` on the call, or set `MULTIVAC_ALLOW_FULL=1` in the environment. Without one of
  those, multivac exits 1 before spawning anything:

  ```
  $ python3 bin/multivac.py ask --tool codex --prompt "x" --mode full ; echo exit=$?
  ERROR: mode 'full' requires --yes or MULTIVAC_ALLOW_FULL=1 (auto-approves ALL delegate actions)
  exit=1
  ```

  Passing the ack prints a one-line stderr warning naming the tool and cwd before running.
  **Never choose `full` without the user's explicit, specific intent** — an interactive
  confirmation can't work in a headless call, so this flag/env-var pair is the gate.

## Subagents (`--agent` / `--agents`)

Target a named or ad-hoc subagent on the delegate:

```
python3 bin/multivac.py ask --tool claude --agent reviewer --prompt "Review this diff"
```

`--agent NAME` looks up `references/agents/NAME.json` (name restricted to
`[A-Za-z0-9_-]{1,64}`, no path traversal). Example definition
(`references/agents/reviewer.json`):

```json
{"name": "reviewer", "description": "Critical senior code reviewer",
 "prompt": "You are a rigorous senior code reviewer. Identify correctness bugs, security issues, and unhandled edge cases. Be concise and specific."}
```

`--agents '{"name": "...", "prompt": "..."}'` (or `--agents @path/to/file.json`) defines one
inline instead of looking it up. Where the delegate has native system-prompt support
(`claude --append-system-prompt`, `grok --system-prompt-override`) the agent's prompt is
passed natively; where it doesn't (`agy`, `codex`) it's folded into a preamble ahead of the
task in the prompt itself.

## Sessions (`--session`)

`--session LABEL` sends a follow-up message into a resumable conversation. First use with a
label creates the session; later uses with the same label resume it — but only if the label
was created for the **same tool and same cwd**; otherwise multivac starts fresh rather than
resuming the wrong transcript.

```
$ python3 bin/multivac.py ask --tool claude --session t1 --prompt "Remember the number 7. Reply OK."
OK. I'll remember the number 7.
$ python3 bin/multivac.py ask --tool claude --session t1 --prompt "What number did I ask you to remember? Reply with just the number."
7
```

Every resume re-passes mode/model/output flags — delegates do not reliably carry them over
on their own. Session records are written transactionally (temp file + atomic rename) to
`$MULTIVAC_HOME/sessions.json`, `chmod 600`.

## Web search (`--web-search`)

Behavior differs per delegate:

- `codex`: off by default; `--web-search` maps to `-c tools.web_search=true`.
- `grok`: **on by default in the underlying CLI** — multivac passes `--disable-web-search`
  unless you pass `--web-search`, so multivac's off-by-default policy is consistent across
  tools.
- `claude` / `agy`: web search is built into the CLI with no toggle flag; `--web-search` is
  a no-op for these two (nothing to disable or enable at the wrapper level).

## Safety notes

- **Subscription auth only, no API keys.** multivac scrubs the five provider API-key env
  vars (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `XAI_API_KEY`, `GEMINI_API_KEY`,
  `GOOGLE_API_KEY`) from every child unless you pass `--allow-api-keys`, and it never uses
  `claude --bare` or `codex --oss` — so a call cannot silently switch a delegate onto
  metered API billing. Each delegate still reads its own on-disk OAuth
  (`~/.codex/auth.json`, etc.); multivac neither reads nor forwards those tokens. Run
  `doctor` to see which keys were scrubbed on your machine.
- **Recursion guard.** multivac sets `MULTIVAC_DEPTH` on every child and refuses to spawn
  past `--max-depth` (default 2) — this stops accidental exponential fan-out if a delegate
  reads project instructions that themselves say "consult another agent."
- **Disposable checkouts + `--cwd` for sensitive repos.** A delegate run in `edit`/`full`
  mode has its own filesystem and network access; on untrusted input it can in principle be
  prompt-injected into destructive or exfiltrating actions. For anything sensitive, point
  `--cwd` at a disposable checkout rather than your primary working tree, and keep
  untrusted-content tasks in `plan`. multivac reduces blast radius; it does not make a
  full-access agent safe against hostile input.
- **Per-tool timeouts.** Default wall-clock timeouts are 180s for codex/claude/grok and
  300s for agy, overridable with `--timeout`. On expiry the whole child process group is
  killed (not just the top-level PID) — this covers hangs a CLI's own `--timeout`-style
  flag doesn't (auth prompts, compaction, capacity stalls).
- **No shell interpolation.** multivac builds argv as a list and never uses `shell=True`;
  the prompt is passed as a single argv element (or via `--prompt-file`), so nothing in the
  prompt can be reinterpreted as a shell command.
- **5 MB prompt-size limit.** Prompts over 5 MB are rejected before spawning anything
  (`"prompt exceeds 5 MB; put large context in a file"`) — put large context in a file the
  delegate reads instead.
