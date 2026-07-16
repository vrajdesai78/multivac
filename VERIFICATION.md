# multivac — live verification

End-to-end verification against the real installed CLIs on their existing subscription
logins (no API keys). Run 2026-07-15 from the repo root.

## doctor
```
$ python3 bin/multivac.py doctor
codex  installed  codex-cli 0.144.4       scrubbed_keys: []
agy    installed  1.1.2                   scrubbed_keys: []
claude installed  2.1.211 (Claude Code)   scrubbed_keys: []
grok   installed  grok 0.2.101            scrubbed_keys: []
```

## ask — one read-only call per tool
```
$ python3 bin/multivac.py ask --tool codex  --prompt "Reply with exactly: PONG" --mode plan
PONG
$ python3 bin/multivac.py ask --tool claude --prompt "Reply with exactly: PONG" --mode plan
PONG
$ python3 bin/multivac.py ask --tool grok   --prompt "Reply with exactly: PONG" --mode plan
PONG
$ python3 bin/multivac.py ask --tool agy    --prompt "Reply with exactly: PONG" --mode plan
PONG
```

## resume — send a follow-up message into a session
```
$ python3 bin/multivac.py ask --tool claude --session t1 --prompt "Remember the number 7. Reply OK."
OK. I'll remember the number 7.
$ python3 bin/multivac.py ask --tool claude --session t1 --prompt "What number did I ask you to remember? Reply with just the number."
7
```

## consensus — same prompt, fan out, side by side
```
$ python3 bin/multivac.py consensus --tools codex,grok --prompt "What is 6 times 7? Reply with just the number."
===== codex =====
42
===== grok =====
42
```

## full-mode gate — refuses without explicit ack
```
$ python3 bin/multivac.py ask --tool codex --prompt "x" --mode full ; echo exit=$?
ERROR: mode 'full' requires --yes or MULTIVAC_ALLOW_FULL=1 (auto-approves ALL delegate actions)
exit=1
```

## Notes from verification (bugs caught here, fixed before this record)
- grok/agy took the prompt as the **value** of `-p` (`--single`/`--print`), not a trailing
  positional — fixed in `build_argv`.
- grok hangs on `--output-format json` unless `--no-auto-update` is passed (auto-update
  check blocks); multivac passes it. grok also reassigns a client-supplied `--session-id`,
  so its session id is parsed from output.
- Raw shell calls without `</dev/null` hang on inherited stdin — `run_child` uses
  `stdin=DEVNULL`, which is why the tool does not hang where a naive call would.
