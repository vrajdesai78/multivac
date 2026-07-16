# multivac

**Ask one AI coding CLI to consult the others — on your existing subscriptions, no API keys.**

`multivac` lets a host AI coding CLI (**Claude Code** or **Codex**) synchronously invoke other
AI coding CLIs as delegates, each on its own existing subscription/OAuth login. Get a second
opinion, delegate a task, or fan a question out to several models and compare. One Python
file, standard library only, no `pip`/`npm` install.

Named after Asimov's Multivac — the great computer you bring every question to.

<p>
<img alt="Python 3.9+" src="https://img.shields.io/badge/python-3.9%2B-blue"> <img alt="stdlib only" src="https://img.shields.io/badge/deps-stdlib%20only-green"> <img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-black">
</p>

## At a glance

| | |
|---|---|
| **Delegates** | `codex` · `agy` (Gemini/Claude/GPT-OSS via Antigravity) · `claude` · `grok` |
| **Commands** | `ask` · `consensus` (parallel fan-out) · `doctor` · `sessions` |
| **Modes** | `plan` (read-only, default) · `edit` · `full` (gated behind an explicit ack) |
| **Auth** | each delegate's own subscription login — **no per-model API keys** |
| **Hosts** | Claude Code (`SKILL.md`) and Codex (prompt + `AGENTS.md`) |

```console
$ multivac consensus --tools codex,grok,agy --prompt "Simplest way to debounce in vanilla JS?"
===== codex =====
Wrap the handler in a setTimeout you clear on each call…
===== grok =====
Use a closure over a timer id; clear-and-reset on every invocation…
===== agy =====
A single-timer debounce: reset the pending timeout each event…
```

> `agy` is how multivac reaches Gemini. Google retired the individual Gemini CLI OAuth tier
> on 2026-06-18, so the native `gemini` CLI is intentionally **not** a delegate — use `agy`.

## Why not just an MCP server or another agent?

multivac never presents or reads anyone's tokens. It shells out to the **official** vendor
CLI, which authenticates itself the way the vendor intends. That keeps every call on the
right side of "subscription tokens are for official clients only" — the line third-party
multi-model clients have to blur (and that some vendors actively block). See
[What multivac does NOT do](#what-multivac-does-not-do).

## Requirements

- **`python3`** 3.9+ (developed on 3.13). No other dependencies.
- Each delegate CLI you want to use, **installed and already logged in**:

  | Delegate | CLI | Log in with |
  |---|---|---|
  | `codex` | OpenAI Codex CLI | `codex login` (ChatGPT Plus/Pro) |
  | `claude` | Claude Code | `claude login` (Claude Pro/Max) |
  | `grok` | xAI Grok CLI | `grok login` (SuperGrok/X) |
  | `agy` | Google Antigravity CLI | its first-run browser login (Google account) |

You don't need all four — multivac uses whichever are installed. Run `multivac doctor` to see.

## Install

```console
git clone https://github.com/<you>/multi-cli-skill.git
cd multi-cli-skill
./install.sh
```

`install.sh` copies the skill to `~/.claude/skills/multivac/`, installs the Codex prompt (if
`codex` is present), and drops a **`multivac`** launcher in `~/.local/bin`. Re-run it any time
to upgrade; `./install.sh --uninstall` removes what it installed.

If `~/.local/bin` isn't on your `PATH`, the installer tells you the line to add. You can also
skip the installer and run `python3 bin/multivac.py …` directly from the clone.

<details>
<summary>Manual install / custom locations</summary>

**Claude Code** — copy or symlink the skill files:

```console
mkdir -p ~/.claude/skills/multivac
ln -s "$(pwd)/SKILL.md" "$(pwd)/bin" "$(pwd)/references" ~/.claude/skills/multivac/
```

**Codex** — install the slash-prompt and paste the AGENTS snippet:

```console
mkdir -p ~/.codex/prompts
cp codex/prompts/multivac.md ~/.codex/prompts/multivac.md
# then paste codex/AGENTS.snippet.md into ~/.codex/AGENTS.md (sets MULTIVAC_HOST=codex)
```

Then `/prompts:multivac <question>` in Codex delegates to `claude`/`agy`/`grok`.

The installer honors `CLAUDE_SKILLS_DIR`, `CODEX_PROMPTS_DIR`, and `MULTIVAC_BIN_DIR` env
vars if you want non-default locations.
</details>

Verify before you rely on it:

```console
multivac doctor
```

`doctor` reports each delegate's install status and version, and which API-key env vars would
be scrubbed. It does **not** verify login — a delegate that's installed but logged out
surfaces on the first real `ask` as `"<tool>: not logged in — run <tool> login"`.

## Usage

```console
# One delegate, read-only (the default mode)
multivac ask --tool codex --prompt "Review this diff for race conditions"

# Delegate a writing task (delegate may edit files in --cwd)
multivac ask --tool grok --mode edit --cwd ./service --prompt "Add input validation to handlers.py"

# Fan the same prompt out in parallel and compare
multivac consensus --tools codex,grok,agy --prompt "Best way to structure this module?"

# Run a delegate as a named or ad-hoc subagent
multivac ask --tool claude --agent reviewer --prompt "Audit auth.py"
multivac ask --tool codex --agents '{"name":"perf","prompt":"You optimize hot paths."}' --prompt "Speed up parse()"

# Multi-turn: send follow-up messages into a resumable session
multivac ask --tool claude --session t1 --prompt "Remember the number 7. Reply OK."
multivac ask --tool claude --session t1 --prompt "What number did I say? Just the number."

# List saved sessions
multivac sessions
```

Common flags: `--mode`, `--model`, `--cwd`, `--timeout`, `--session`, `--agent`/`--agents`,
`--web-search`, `--concurrency` (consensus), `--json`, `--max-depth`. Full reference:
[`references/usage.md`](references/usage.md). Per-CLI argv, output framing, and session-id
handling: [`references/cli-matrix.md`](references/cli-matrix.md).

## Modes

Every call is explicit about what a delegate may do:

- **`plan`** (default) — read-only. No writes, no destructive commands. Use for reviews,
  second opinions, and anything touching content you didn't write.
- **`edit`** — the delegate may edit files in its working directory.
- **`full`** — auto-approves *all* delegate actions. **Refused unless acknowledged** with
  `--yes` or `MULTIVAC_ALLOW_FULL=1`; otherwise multivac exits 1 before spawning anything.
  This flag-based gate replaces the interactive confirmation a headless call can't show.
  Never choose `full` on someone's behalf without their explicit intent.

## Configuration

| Env var | Purpose | Default |
|---|---|---|
| `MULTIVAC_HOME` | Where session state (`sessions.json`, `chmod 600`) is stored | `./.multivac` (current dir) |
| `MULTIVAC_HOST` | The host CLI to exclude from `consensus --tools all` | unset |
| `MULTIVAC_ALLOW_FULL` | `=1` acknowledges `--mode full` (same as `--yes`) | unset |
| `MULTIVAC_DEPTH` | Recursion guard; a call refuses past `--max-depth` (default 2) | `0` |

Session state is written to `.multivac/` in your **current directory** (so sessions are
per-project). Add `.multivac/` to your project's `.gitignore`.

## What multivac does NOT do

- **Never reads or forwards your OAuth tokens.** Each delegate reads its own on-disk login
  (`~/.codex/auth.json`, the Claude keychain, `~/.grok/auth.json`, `~/.antigravity`);
  multivac never touches those files.
- **Makes no network calls of its own** — every request is the delegate CLI's, on your
  behalf, using its own login. No telemetry. No auto-update.
- **Never `eval`/`exec`s model output** and never uses `shell=True` — argv is built as a
  list, so nothing in a prompt can be reinterpreted as a shell command.
- **Scrubs the five provider API-key env vars** (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`,
  `XAI_API_KEY`, `GEMINI_API_KEY`, `GOOGLE_API_KEY`) from every child unless you pass
  `--allow-api-keys` — so a call can't silently move a delegate onto metered API billing.

It's a single, auditable file (`bin/multivac.py`) — read it before you trust it.

## Troubleshooting

- **`<tool>: not logged in`** — run that CLI's login (`codex login`, etc.) and retry.
- **A delegate refuses in an untrusted directory** — some CLIs won't act in a directory they
  don't trust; trust it in that CLI, or run from a trusted path.
- **A call is slow / times out** — multivac enforces a wall-clock timeout per tool (180s;
  300s for `agy`) and kills the whole process group. Raise it with `--timeout <seconds>`.
- **Nothing prints but exit is 0** — multivac treats a delegate that exits 0 with no result
  (or an error envelope) as a failure and reports it; it won't hand back a fake answer.

More real-world failure modes and how multivac handles them:
[`references/gotchas.md`](references/gotchas.md).

## Development

```console
# Run the test suite (stdlib tool; pytest is only needed for tests)
uv run --with pytest python3 -m pytest -q
# or: pip install pytest && python3 -m pytest -q
```

Tests are hermetic (subprocess mocked) except one live smoke test gated behind
`MULTIVAC_LIVE=1`. Design notes and the implementation plan live in `docs/superpowers/`;
end-to-end verification output is in [`VERIFICATION.md`](VERIFICATION.md).

## Contributing

Issues and PRs welcome. Please keep `bin/multivac.py` **stdlib-only** and add a test for any
behavior change. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE) © 2026 Vraj Desai
