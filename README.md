# multivac

`multivac` lets a host AI coding CLI — **Claude Code** or **Codex** — synchronously invoke
other AI coding CLIs as delegates, on their own existing subscription/OAuth logins. No
per-model API keys. Single Python file, stdlib only.

Delegates: **codex**, **agy** (Antigravity, which fronts Gemini/Claude/GPT-OSS), **claude**,
**grok**. Native `gemini` is intentionally not supported — Google retired the individual
Gemini CLI OAuth tier on 2026-06-18; reach Gemini through `agy` instead.

## What multivac does

Builds argv for each delegate CLI, runs it as a child process under a strict execution
contract (owned stdin, split stdout/stderr, its own process group, killed on timeout by
process group, an allow-listed environment), parses its structured or plain-text output, and
relays the clean final answer back to the host.

## What multivac does NOT do

- It never reads or forwards your OAuth tokens — each delegate reads its own on-disk login
  (`~/.codex/auth.json`, `~/.claude/...`, etc.); multivac never touches those files.
- It makes no network calls of its own — every network call is made by the delegate CLI
  itself, on your behalf, using its own login.
- It has no telemetry.
- It never `eval`/`exec`s model output, and never uses `shell=True` — argv is built as a
  list, so nothing in a prompt can be reinterpreted as a shell command.
- It scrubs the five provider API-key env vars (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`,
  `XAI_API_KEY`, `GEMINI_API_KEY`, `GOOGLE_API_KEY`) from every child unless you explicitly
  pass `--allow-api-keys` — so a call cannot silently switch a delegate onto metered API
  billing. Subscription auth only.

See `references/usage.md` for the complete flag reference and safety notes, and
`references/gotchas.md` for the real-world failure modes multivac is built to avoid.

## Prerequisites

- `python3` (3.9+; developed and tested on 3.13).
- Each delegate CLI you plan to use installed **and already logged in** on this machine —
  multivac uses each CLI's own existing subscription session, not an API key:
  - `codex` (OpenAI Codex CLI) — `codex login`
  - `claude` (Claude Code) — `claude login` (or already logged in if you're running this
    from inside Claude Code)
  - `grok` (xAI Grok CLI) — `grok login`
  - `agy` (Google Antigravity CLI) — its own first-run login flow

Run the doctor check first to confirm what's installed and what env vars would be scrubbed:

```
python3 bin/multivac.py doctor
```

`doctor` confirms installation and version; it does **not** verify login. A delegate that's
installed but not logged in surfaces the error on the first real `ask` call
(`"<tool>: not logged in — run <tool> login"`).

## Install

Clone this repo somewhere permanent (both host integrations below reference the clone path
directly — nothing gets copied into a package registry):

```
git clone <this-repo-url> multi-cli-skill
cd multi-cli-skill
python3 bin/multivac.py doctor
```

### Claude Code

Copy or symlink this repo's skill files into your Claude Code skills directory:

```
mkdir -p ~/.claude/skills/multivac
ln -s "$(pwd)/SKILL.md"     ~/.claude/skills/multivac/SKILL.md
ln -s "$(pwd)/bin"          ~/.claude/skills/multivac/bin
ln -s "$(pwd)/references"   ~/.claude/skills/multivac/references
```

(Symlinks keep the skill in sync with the repo; a plain `cp -r` works too if you'd rather
vendor a snapshot.) Claude Code will pick up `SKILL.md`'s frontmatter and offer the skill
whenever a prompt matches its triggers ("ask codex/grok/gemini", "get a second opinion",
"delegate to another CLI", "consensus across models"). `SKILL.md` resolves `bin/multivac.py`
relative to its own install location, so the symlinked layout above works without editing
any paths.

### Codex

Install the slash-prompt and the AGENTS.md snippet:

```
mkdir -p ~/.codex/prompts
cp codex/prompts/multivac.md ~/.codex/prompts/multivac.md
```

Then paste the contents of `codex/AGENTS.snippet.md` into `~/.codex/AGENTS.md` (applies to
every Codex session) or into a specific project's own `AGENTS.md` (applies to that repo
only), filling in `<path-to-multi-cli-skill>` with the absolute path where you cloned this
repo. That snippet also documents `MULTIVAC_HOST=codex`, which Codex must set on every call
so `consensus --tools all` correctly excludes Codex itself (Codex is always the host here,
never a delegate target).

Once installed, `/prompts:multivac <question or task>` in Codex will delegate to
`claude`/`agy`/`grok`, or fan a prompt out across all three.

## Quick start

One delegate, read-only:

```
python3 bin/multivac.py ask --tool codex --prompt "Review this diff for race conditions"
```

Cross-CLI consensus, fanned out in parallel:

```
python3 bin/multivac.py consensus --tools codex,grok --prompt "What is 6 times 7? Reply with just the number."
```

```
===== codex =====
42
===== grok =====
42
```

Follow-up message in a resumable session (first call creates it, later calls with the same
label + tool + cwd resume it):

```
python3 bin/multivac.py ask --tool claude --session t1 --prompt "Remember the number 7. Reply OK."
python3 bin/multivac.py ask --tool claude --session t1 --prompt "What number did I ask you to remember? Reply with just the number."
```

## Modes

Every call is explicit about what a delegate is allowed to do:

- **`plan`** (default) — read-only. No file writes, no destructive commands. Use this for
  reviews, second opinions, and anything touching content you didn't write yourself.
- **`edit`** — the delegate can edit files in its working directory, but doesn't bypass its
  own approval/sandbox policy for anything riskier.
- **`full`** — auto-approves *all* delegate actions. **Refused unless explicitly
  acknowledged**: pass `--yes`, or set `MULTIVAC_ALLOW_FULL=1` in the environment. Without
  one of those, multivac exits 1 before spawning anything — this is the gate that replaces
  the interactive confirmation a headless call can't show:

  ```
  $ python3 bin/multivac.py ask --tool codex --prompt "x" --mode full ; echo exit=$?
  ERROR: mode 'full' requires --yes or MULTIVAC_ALLOW_FULL=1 (auto-approves ALL delegate actions)
  exit=1
  ```

  Never choose `full` on a user's behalf without their explicit, specific intent.

Full flag reference (`--model`, `--cwd`, `--timeout`, `--agent`/`--agents` subagents,
`--web-search`, `--concurrency`, `--json`, `--max-depth`) lives in `references/usage.md`.
Per-CLI argv, output framing, and session-id handling live in `references/cli-matrix.md`.
