<!--
  multivac — paste this block into ~/.codex/AGENTS.md (applies to every session) or into a
  project's own AGENTS.md (applies to that repo only). Fill in <path-to-multi-cli-skill>
  with wherever you cloned https://github.com/<org>/multi-cli-skill on this machine.

  Setting MULTIVAC_HOST=codex on every multivac.py call tells the wrapper that Codex is the
  host, so `consensus --tools all` and `doctor --tools all` expand to the OTHER three CLIs
  (claude, agy, grok) instead of including Codex itself — Codex never needs to delegate to
  Codex.
-->

## multivac — delegate to other AI CLIs

You have access to `multivac`, a wrapper that shells out to other AI coding CLIs on their own
existing subscription/OAuth logins (no API keys). As host, your delegates are:

- `claude` (Claude Code)
- `agy` (Antigravity — fronts Gemini/Claude/GPT-OSS)
- `grok`

Wrapper path: `<path-to-multi-cli-skill>/bin/multivac.py`

Always set `MULTIVAC_HOST=codex` when invoking it:

```
MULTIVAC_HOST=codex python3 <path-to-multi-cli-skill>/bin/multivac.py ask \
  --tool <claude|agy|grok> --prompt "..." --mode plan
```

Subcommands: `ask` (one delegate), `consensus` (fan out to `--tools claude,agy,grok` or
`--tools all`), `doctor` (installed/version/scrubbed-key check, does not verify login),
`sessions` (list resumable conversations). Full flag reference:
`references/usage.md` in the multivac repo.

Modes: `plan` (read-only, default), `edit` (delegate may write files in its own working
directory), `full` (auto-approves all delegate actions; refused unless `--yes` or
`MULTIVAC_ALLOW_FULL=1` is set).

### Hard rules

- **Default mode is `plan` (read-only).** Stay there unless the task genuinely requires the
  delegate to write files.
- **Never choose `--mode full` without the user's explicit, specific intent.** Don't pass
  `--yes` or set `MULTIVAC_ALLOW_FULL=1` on the user's behalf — surface the refusal and ask
  first if `full` genuinely seems needed.
- **Keep untrusted-content tasks in `plan`.** A delegate run in `edit`/`full` has its own
  filesystem and network access and can be prompt-injected by hostile input (a fetched page,
  a third-party file); don't escalate a delegate handling such content.
- **Subscription auth only.** Don't pass `--allow-api-keys` unless the user specifically asks
  for API-key billing.
