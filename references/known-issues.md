# Known real-world issues (Reddit + GitHub) — research backing multivac's design

> Researched 2026-07-15 by delegating to `codex exec -c tools.web_search=true` (Reddit-focused).
> This file is the source for `references/gotchas.md` and the Execution Contract in the spec.

I found a recurring pattern across all five CLIs: the hardest failures are not model failures. They are process-contract failures—open stdin handles, TTY-dependent flushing, ambiguous JSON framing, cwd-scoped sessions, interactive permission gates, and shared state between concurrent agents.

This report is current to **2026-07-15**. “Fixed” items are included because wrappers often encounter pinned or auto-installed older versions.

## 1. OpenAI Codex CLI

- **Deterministic hang when called from another agent — `codex exec "prompt"` with inherited non-TTY stdin.** Codex detects the pipe and waits for EOF so it can append stdin to the positional prompt. If Claude Code, PowerShell, a CI runner, or another parent keeps the writer open without sending data, Codex sits at “Reading additional input from stdin…” with 0% CPU and no session. Redirect stdin from `/dev/null` or `NUL`, or explicitly write and close it. This was reproduced specifically through Claude Code’s Bash tool. [Codex issue #20919](https://github.com/openai/codex/issues/20919)

- **`--json` is JSONL, not one JSON document — `codex exec --json`.** Each line is a separate lifecycle event; attempting `json.loads(stdout)` fails or reads only the first event. Parse line-by-line and capture `thread.started.thread_id` plus the terminal event. If only the answer is needed, prefer `-o/--output-last-message`. [Official non-interactive documentation](https://github.com/openai/codex/blob/main/docs/exec.md)

- **Duplicate answer on stdout and stderr — ordinary `codex exec`.** In affected versions, the final assistant message was emitted during event handling and then emitted again as final stdout output. A wrapper combining both streams sees duplicates. Keep stdout and stderr separate and treat stdout or `-o` as the result channel. [Codex issue #12566](https://github.com/openai/codex/issues/12566)

- **Successful command but empty captured output — older `codex exec`.** Some releases executed commands successfully and exited 0 while exposing no stdout/stderr. This was subsequently fixed, but it makes exit status alone insufficient. Require a terminal event or non-empty result artifact and record the CLI version. [Codex issue #9091](https://github.com/openai/codex/issues/9091)

- **Tool output silently loses the useful error tail.** Codex historically truncated command output around 10 KiB/256 lines. If a build emits lots of progress before failing, the model may never see the final error and can claim success. Redirect verbose commands to a file, print a short tail, and ask Codex to inspect the file. [Codex issue #6415](https://github.com/openai/codex/issues/6415), [issue #5913](https://github.com/openai/codex/issues/5913)

- **Resume identity and option placement have been unstable — `codex exec resume`.** Early JSON output did not expose a session/thread ID, making deterministic continuation impossible. Later JSON events added `thread_id`. Options also historically needed to appear before `resume`, and first-run flags should not be assumed to carry over. Capture the ID from structured output and explicitly repeat model, sandbox, approval and output flags. [Codex issue #3817](https://github.com/openai/codex/issues/3817)

- **Non-interactive sessions missing from the interactive resume list.** Codex v0.133 briefly omitted Exec-created sessions unless special inclusion logic was used. `codex exec resume <id>` remained the deterministic route. Never scrape the picker; store the emitted thread ID yourself. [Codex issue #24502](https://github.com/openai/codex/issues/24502)

- **Structured schema and resume could not be combined — `codex exec resume --output-schema`.** As reported in March 2026, resume rejected `--output-schema`, forcing callers to choose between conversation continuity and server-enforced final structure. Workarounds are a fresh session containing a compact prior summary, or client-side validation/retry. [Codex issue #14343](https://github.com/openai/codex/issues/14343)

- **Schema applies more broadly than expected.** In an affected release, `--output-schema` constrained intermediate `agent_message` progress messages, not just the final answer. Agents producing conversational progress then failed schema validation. Suppress progress instructions or validate only the final terminal message client-side. [Codex issue #19816](https://github.com/openai/codex/issues/19816)

- **Headless OAuth login fails on remote machines.** Browser callback login cannot reach a headless server. Current options include device authorization, API-key login, SSH port forwarding, or logging in locally and transferring credentials—although copying `auth.json` is brittle because refresh-token state changes. [Codex issue #3820](https://github.com/openai/codex/issues/3820), [issue #2798](https://github.com/openai/codex/issues/2798)

- **Permission requests can become silent cancellations.** A recent report found MCP calls in `codex exec` were cancelled because stdin was closed and no caller existed to approve them; the apparent workaround was the much broader dangerous bypass flag. Pre-authorize narrowly in configuration where possible and fail on approval events instead of silently retrying. [Codex issue #24135](https://github.com/openai/codex/issues/24135)

- **Sandbox behavior breaks authenticated child tools.** Users have seen `gh auth status` report unauthenticated inside workspace-write because networking or credentials were unavailable. Others found `GH_TOKEN`/`GITHUB_TOKEN` stripped from tool environments. Pass a deliberately allow-listed environment and test auth from inside the exact sandbox profile. [Reddit report](https://www.reddit.com/r/OpenaiCodex/comments/1oq5hv3), [Codex issue #10695](https://github.com/openai/codex/issues/10695)

- **Fan-out can resemble abuse and trigger 429s.** One report involved roughly 25 Codex subagents producing a burst that looked like a DDoS to the backend. Use a bounded worker pool, jittered backoff and one global quota budget—not one retry loop per child. [Codex issue #11083](https://github.com/openai/codex/issues/11083)

## 2. Google Gemini CLI

A crucial current-state change: on **2026-06-18**, Gemini CLI stopped serving the consumer Google AI Free/Pro/Ultra and Code Assist tiers. Google directed those users to Antigravity. Enterprise, Google Cloud and paid Gemini API-key paths remain. A wrapper that assumes yesterday’s cached OAuth entitlement still works can now fail even with valid credentials. [Google announcement](https://github.com/google-gemini/gemini-cli/discussions/27274)

- **Silent SSH/headless startup hang — `gemini -p`.** On Linux over SSH, Gemini could block while accessing a locked or unavailable desktop keychain. A reported workaround is `GEMINI_FORCE_FILE_STORAGE=true`, which avoids keyring storage. [Reddit reproduction](https://www.reddit.com/r/GeminiCLI/comments/1sicvhi)

- **Browser OAuth waits forever on a remote machine.** The CLI opens or expects a localhost browser callback that the remote environment cannot complete. Use an API key, supported device flow, or SSH-forward the callback port. [Gemini CLI issue #1696](https://github.com/google-gemini/gemini-cli/issues/1696)

- **OAuth instructions contaminate machine output.** With `NO_BROWSER`, URL/code prompts were written through the normal output path, breaking scripts that expected only the answer or JSON. The fix was to route authentication diagnostics to stderr. Wrappers should still parse stdout independently and reject non-JSON lines. [Gemini CLI issue #3983](https://github.com/google-gemini/gemini-cli/issues/3983)

- **General diagnostics appeared on stdout.** Log and status messages polluted headless output until logging was centralized and redirected. Use `--output-format json`, separate stderr and pin a known version. [Gemini CLI issue #5602](https://github.com/google-gemini/gemini-cli/issues/5602)

- **JSON has an envelope, not just the answer.** Headless JSON contains a `response` plus statistics for models, tools and files. Code expecting a single string breaks. Validate the envelope and tolerate additive fields. [Gemini CLI headless documentation](https://google-gemini.github.io/gemini-cli/docs/cli/headless.html), [schema issue #8022](https://github.com/google-gemini/gemini-cli/issues/8022)

- **Prompts and stdin are combined.** `gemini -p "instruction"` still reads piped stdin and appends it. This is useful for diffs, but an inherited open pipe can create the same class of orchestration risk as Codex. Explicitly use `/dev/null` when no payload is intended; otherwise write bytes and close stdin. [Gemini CLI reference](https://github.com/google-gemini/gemini-cli/blob/main/docs/cli/cli-reference.md)

- **Untrusted workspace aborts headless execution.** With folder trust enabled, headless mode can raise `FatalUntrustedWorkspaceError`. Untrusted mode also disables project settings, `.env`, MCP servers, custom commands and auto-accept, so a run may behave differently rather than merely lose write access. Pre-establish trust or intentionally use `--skip-trust`/`GEMINI_CLI_TRUST_WORKSPACE=true` only inside an isolated checkout. [Trusted-folder documentation](https://github.com/google-gemini/gemini-cli/blob/main/docs/cli/trusted-folders.md)

- **Approval policy becomes denial in headless mode.** Rules that say “ask the user” cannot prompt without a UI and are converted to deny. Model retries will not fix this. Generate an explicit automation policy with narrow allows and treat permission denial as terminal. [Policy engine documentation](https://github.com/google-gemini/gemini-cli/blob/main/docs/reference/policy-engine.md)

- **Allowed shell tool missing despite `auto_edit`.** Gemini v0.30 contained a hardcoded exclusion that kept `run_shell_command` from being registered in headless mode even where policy allowed it. Fixed by #20639; update rather than weakening the entire permission policy. [Gemini CLI issue #20469](https://github.com/google-gemini/gemini-cli/issues/20469)

- **Capacity errors can turn a short run into a long stall.** Reddit reports show `gemini -p` returning `MODEL_CAPACITY_EXHAUSTED`, retrying Flash for about a minute, or spending tens of minutes before falling back. Put a wrapper-level deadline above provider retry behavior and decide whether model fallback is acceptable before launch. [Reddit report](https://www.reddit.com/r/GeminiAI/comments/1s6wqil), [second report](https://www.reddit.com/r/GeminiFeedback/comments/1s4orv3)

- **Sessions are cwd/project scoped and expire.** Sessions live under a project hash, `--resume` searches the current project, and default retention is 30 days. Renaming, moving or recreating a checkout can make “latest” mean something else. Store the session UUID plus canonical cwd and do not rely on `--resume latest` in concurrent jobs. [Session-management documentation](https://geminicli.com/docs/cli/session-management/)

- **Sandbox does not necessarily inherit the parent environment.** Auth-dependent tools can fail because the sandbox lacks tokens or other shell variables. Explicitly pass a minimal environment; do not source the user’s whole login profile. [Gemini CLI issue #20724](https://github.com/google-gemini/gemini-cli/issues/20724)

## 3. Google Antigravity CLI (`agy`)

Antigravity is young, and public discussion is much thinner than for Codex, Gemini or Claude. Several findings are early 1.0.x regressions.

- **Exit 0 with completely empty output under pipes — `agy -p/--print`, Windows.** The model completed, but non-TTY subprocesses and redirected invocations received zero stdout and stderr. TTY execution worked, suggesting flushing/output handling was coupled to the terminal. Reported workarounds were a PTY wrapper, reverting to Gemini CLI, or instructing the model to write its answer to a file. [Antigravity issue #76](https://github.com/google-antigravity/antigravity-cli/issues/76)

- **Large piped prompt silently truncated.** A hundreds-of-kilobytes prompt was cut without a fatal error; worse, the model then claimed to have inspected repository content that never arrived. Check input byte/token size before spawning, chunk or store large context in files, and verify claimed file access through tool events or artifacts. [Antigravity issue #224](https://github.com/google-antigravity/antigravity-cli/issues/224)

- **Intermittent mid-generation abort when invoked by Claude Code — Windows.** Roughly one in four nested `agy --print` runs reportedly died while atomically renaming a `.tmp` conversation file to `.pb`; antivirus, OneDrive or stale Windows handles returned Access Denied. Delete stale temporary files cautiously, keep the state directory off synchronized storage, and retry once only after verifying no valid result was written. [Antigravity issue #217](https://github.com/google-antigravity/antigravity-cli/issues/217)

- **No clean headless bootstrap.** An early report found Antigravity accepted browser/paste OAuth but ignored `GEMINI_API_KEY`, `GOOGLE_API_KEY`, ADC and preseeded settings. Containers could select file token storage but still could not obtain the initial token non-interactively. Use a persistent pre-authenticated home/profile, or choose another CLI for ephemeral CI until supported noninteractive auth exists. [Antigravity issue #223](https://github.com/google-antigravity/antigravity-cli/issues/223)

- **OAuth not retained on Linux/WSL.** Without a usable libsecret/keyring backend, users were prompted to authenticate on every invocation. Persist and mount the credential store, confirm it survives a second process before deploying, and avoid launching many children that all try to log in. [Antigravity issue #227](https://github.com/google-antigravity/antigravity-cli/issues/227), [Reddit report](https://www.reddit.com/r/GeminiAI/comments/1ti1xiq)

- **First run is inherently interactive.** Theme, rendering, login and workspace trust must be established before reliable `--print` automation. Provision a golden profile in a controlled setup step and test the exact target directory. [Official getting-started guide](https://antigravity.google/docs/cli-getting-started)

- **Resume is tied to absolute cwd.** Antigravity’s recent-conversation cache is keyed by the absolute working directory. Changing checkout paths can lose “continue,” while concurrent jobs sharing a cwd can recover the wrong transcript. Store the direct conversation identifier and isolate each run’s cwd/state. [Official resume documentation](https://antigravity.google/docs/cli/commands/resume)

- **Timeout flags may not cover pre-generation hangs.** Third-party Claude integration testing found `--print-timeout` did not reliably cap authentication/startup stalls, and transcript recovery by cwd was ambiguous under concurrent calls. Always enforce an OS-level wall-clock timeout and kill the complete process group. [Integration changelog](https://app.unpkg.com/antigravity-plugin-cc%400.6.3/files/CHANGELOG.md)

Structured-output discussion for `agy` remains sparse. Early releases were primarily plain-output oriented, so wrappers should not assume Gemini CLI’s JSON schema or event protocol applies merely because Antigravity can front Gemini models.

## 4. Anthropic Claude Code CLI

- **Two different JSON protocols — `json` versus `stream-json`.** `--output-format json` is one envelope containing `result`, `session_id`, usage and cost. `stream-json` is NDJSON, and the last record is the terminal result. With `--json-schema`, the validated payload is in `structured_output`, not `result`. Before v2.1.208, a large piped response could truncate the final stream line. [Official programmatic-use documentation](https://code.claude.com/docs/en/headless)

- **Piped input has a hard 10 MB limit.** Since v2.1.128 Claude exits clearly when stdin exceeds 10 MB. Use a referenced file or reduce the diff rather than slicing bytes blindly. [Official documentation](https://code.claude.com/docs/en/headless)

- **Subprocess invocation could hang when stdin was not explicit.** Claude’s changelog records a fix for `claude -p` hanging when spawned without an explicit stdin configuration. Current wrappers should still use `stdin=DEVNULL` or write-and-close semantics. [Claude Code changelog](https://github.com/anthropics/claude-code/blob/main/CHANGELOG.md)

- **Background work kept `claude -p` alive forever.** Before v2.1.163, a background shell process could prevent exit indefinitely. Current releases terminate ordinary background processes after roughly five seconds; background subagents are waited on, with a default ten-minute ceiling from v2.1.182. Retain your own outer deadline because the model can still initiate expensive work. [Official documentation](https://code.claude.com/docs/en/headless)

- **`-p` sessions are intentionally absent from the picker.** They remain resumable, but only by passing the captured session ID from the original project directory/worktree. Scripts that invoke `claude --resume` and expect to select a headless session appear to have “lost” it. [Session documentation](https://code.claude.com/docs/en/sessions), [issue #42311](https://github.com/anthropics/claude-code/issues/42311)

- **Resume can load incomplete or incorrect history.** Reports include a valid JSONL transcript not being loaded, and snapshot/message-ID collisions causing only the latest portion of history to return. Keep an external summary/checkpoint and verify the first resumed response demonstrates expected context. [Claude Code issue #15837](https://github.com/anthropics/claude-code/issues/15837), [issue #24304](https://github.com/anthropics/claude-code/issues/24304)

- **Resume can destroy prompt-cache efficiency.** Long conversations were reportedly re-cached almost in full on each `--print --resume`, rapidly consuming rate limits. The suspected cause was reordered history around deferred-tool events. Track input/cache token counts per turn and compact or start a summarized fresh session when cache reads collapse. [Claude Code issue #42338](https://github.com/anthropics/claude-code/issues/42338)

- **Allowed-tool patterns have been ignored in some releases.** Patterns such as `Bash(*)` or `Read(*)` still produced “permissions haven’t been granted” in noninteractive runs, while simpler whole-tool allows worked. Pin and test permission syntax against the installed version. Do not fall straight to bypass mode. [Claude Code issue #581](https://github.com/anthropics/claude-code/issues/581)

- **Background agents cannot answer approval prompts.** They can silently fail Edit/Write, and some protected paths under `.claude/` remain guarded even in broad modes. Give subagents explicit tools, assign non-overlapping paths and treat permission-denied tool results as failures. [Claude Code issue #11380](https://github.com/anthropics/claude-code/issues/11380)

- **Nested Claude instances can clobber the parent.** A concrete Reddit reproduction found a nested `claude -p` in the same cwd purged the parent session’s `/tmp/claude-<uid>/<slug>/tasks` directory, causing the parent Bash tool to return an empty result. Nesting is also blocked by `CLAUDECODE`. The reported workaround is an isolated `CLAUDE_CODE_TMPDIR` plus intentionally clearing `CLAUDECODE`. This should only be done in an isolated workspace. [Reddit investigation and workaround](https://www.reddit.com/r/ClaudeAI/comments/1rnsgch/if_your_claude_p_or_the_agent_sdk_scripts_have/)

- **API-key environment variables override subscription OAuth.** An inherited `ANTHROPIC_API_KEY` can silently move usage onto metered API billing. Scrub conflicting auth variables and explicitly select the intended method. [Anthropic support](https://support.claude.com/en/articles/12304248-manage-api-key-environment-variables-in-claude-code)

- **The economics of `claude -p` changed on 2026-06-15.** It no longer simply consumes the ordinary Claude subscription allowance; eligible plans receive a separate Agent SDK monthly credit, after which configured pay-as-you-go billing may apply. Budget using returned `total_cost_usd`, `--max-budget-usd` and `--max-turns`. [Anthropic support](https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan)

- **Interactive and print modes do not expose identical tools.** An LSP report found servers initialized under `--print`, but document-open/change/save events were never sent, making semantic tools ineffective. Test capabilities in `-p`; do not infer them from an interactive session. [Claude Code issue #17063](https://github.com/anthropics/claude-code/issues/17063)

## 5. xAI Grok CLI

Grok Build is the newest CLI here. Reddit/GitHub issue discussion is limited, but xAI’s own changelog documents an unusually dense set of recent headless fixes.

- **Headless returned before subagents/background tasks completed — older `grok -p`.** This produced incomplete output and orphaned work. Fixed in v0.2.58. Require that version or later and still validate terminal completion. [Grok changelog](https://x.ai/build/changelog)

- **Long compaction could hang indefinitely.** Fixed in v0.2.57, with another stalled-summarizer fix in v0.2.60. A wrapper-level deadline remains necessary because compaction may occur late in an otherwise successful run. [Grok changelog](https://x.ai/build/changelog)

- **Resume lost execution policy.** Before v0.2.56, a resumed session did not retain its sandbox profile; related subagent resume logic could fork the wrong conversation or use the wrong cwd. Fixed in v0.2.56. Repeat security-sensitive flags and verify the resumed session’s reported configuration. [Grok changelog](https://x.ai/build/changelog)

- **`/resume` selected the wrong model when names were ambiguous.** Fixed in v0.2.68. Prefer exact session IDs and explicit model flags over interactive matching. [Grok changelog](https://x.ai/build/changelog)

- **Concurrent writers regressed session metadata.** Last-active timestamps and message counts could go backward, making “continue most recent” select the wrong session. Fixed around v0.2.64/65. Never use recency as an identity mechanism when running parallel workers. [Grok changelog](https://x.ai/build/changelog)

- **Auth selection changed based on inherited environment.** OIDC sessions could lose refresh when `XAI_API_KEY` was also present; the wrong method could be selected when both a key and cached token existed. Later releases fixed this and added explicit auth pinning. Sanitize auth variables and set the method in configuration. [Grok changelog](https://x.ai/build/changelog)

- **Configuration flags were silently ignored in headless mode.** `--disable-web-search` was not honored by `grok -p` or ACP until a June fix. This is both a cost and data-boundary problem. Assert the effective configuration from initialization events where possible. [Grok changelog](https://x.ai/build/changelog)

- **Auto-update is active unless disabled.** A long-running automation fleet can otherwise change versions and protocol behavior between jobs. xAI explicitly recommends `--no-auto-update` for scripts and CI. [Official headless documentation](https://docs.x.ai/build/cli/headless-scripting)

- **Output protocols differ.** `json` produces one final JSON object; `streaming-json` produces NDJSON events. Sessions are stored under `~/.grok/sessions`, and `--continue` means most recent in the current directory. Store exact IDs and parse by selected format. [Official headless documentation](https://docs.x.ai/build/cli/headless-scripting)

- **Command output can consume enormous disk space.** v0.2.58 capped active command-output files at 5 GB and truncates them to 64 MB afterward. A wrapper still needs per-job disk quotas and log rotation; 5 GB per child is disastrous under fan-out. [Grok changelog](https://x.ai/build/changelog)

- **Background child processes leaked after headless exit.** Recent releases added cleanup for model-started background tasks and failed agent spawns. Kill the entire process group/container at job teardown, not merely the top-level `grok` PID. [Grok changelog](https://x.ai/build/changelog)

- **Windows/WSL OAuth redirect loop.** A Reddit report shows the CLI returning repeatedly to login while waiting for OAuth completion. The practical automation fallback is `XAI_API_KEY`, though the exact account-side cause was not established. [Reddit report](https://www.reddit.com/r/grok/comments/1u7fchj)

- **Potential repository-upload/data-boundary incident.** A July 2026 wire-capture report alleged Grok Build v0.2.93 uploaded a repository bundle, including git history, to xAI-controlled storage; the author supplied a reproduction repository. This is community evidence rather than a confirmed xAI postmortem, but it is serious enough that regulated wrappers should use disposable checkouts, deny sensitive paths and perform egress inspection. [Reddit investigation](https://www.reddit.com/r/LocalLLaMA/comments/1ut7tis), [reproduction repository](https://github.com/cereblab/grok-build-exfil-repro)

## Cross-CLI orchestration failures

- **Agents overwrite or revert each other in one checkout.** Multiple users report parallel Claude/Codex-style agents editing the same files, undoing one another and creating silent semantic conflicts even when git eventually merges. Give every agent its own git worktree and branch, then serialize test/review/merge. [Reddit discussion](https://www.reddit.com/r/ClaudeAI/comments/1qzduim), [more recent worktree report](https://www.reddit.com/r/ClaudeCode/comments/1uvysm6)

- **Cwd is part of hidden state.** Claude, Gemini, Grok and Antigravity all use the working directory when locating sessions; nested Claude also scopes temporary task files by cwd. Record the canonical cwd with every run, never let the model casually `cd` the long-lived parent process, and launch children with an explicit cwd.

- **Inherited environment selects auth and leaks secrets.** `ANTHROPIC_API_KEY`, `XAI_API_KEY`, `GH_TOKEN`, proxy variables, model settings and agent-recursion markers can change child behavior. Construct an allow-listed environment for each CLI instead of passing `process.env` wholesale.

- **Retry fan-out multiplies cost and load.** If 20 children independently retry a 429 or hang, concurrency can increase exactly when the provider is overloaded. Use one supervisor-controlled semaphore, one aggregate budget and one rate limiter.

- **Transcript recovery is not synchronization.** “Resume last” and cwd-keyed transcript searches are vulnerable to races. A wrapper must capture session IDs from the process that created them and persist them transactionally with the job record.

- **Nested recursion can be accidental.** An agent asked to “consult Codex/Claude/Gemini” may spawn a CLI that reads project instructions telling it to consult another agent, producing exponential fan-out. Set maximum nesting depth, child count, turns and total cost.

## Themes with limited evidence

- I found little reliable evidence of a universal **“prompt must come last”** rule. Flag parsing does vary by release, but the stronger concrete problem is shell quoting and inherited stdin. Passing prompts via a closed stdin pipe or temporary UTF-8 file is safer than building a shell command string.
- Public Antigravity and Grok discussion is still sparse compared with Codex/Claude/Gemini. Their changelogs show rapid protocol churn, so version pinning matters more than extrapolating behavior from the model provider.
- ANSI/spinner contamination was discussed less often than plain diagnostics on stdout. The safer default is still no PTY, explicit color-off where available, separate stderr and structured output.

## Top 10 gotchas a wrapper MUST handle

1. **Own stdin:** use `/dev/null` when empty; otherwise write the complete payload and close it.
2. **Know the framing:** single JSON document versus NDJSON event stream.
3. **Separate stdout and stderr:** never merge them before parsing.
4. **Require a terminal success event/result artifact:** exit code 0 is not sufficient.
5. **Capture exact session IDs and canonical cwd:** never depend on “resume last.”
6. **Repeat model, sandbox, approval and output settings on resume.**
7. **Enforce an external wall-clock timeout and kill the whole process group.**
8. **Use an allow-listed environment:** prevent auth selection, token and recursion-marker leakage.
9. **Give every concurrent agent its own worktree, temp directory and state directory.**
10. **Impose global concurrency, token, turn, cost and disk budgets before fan-out.**
