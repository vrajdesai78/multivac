# Contributing to multivac

Thanks for your interest! multivac is deliberately small and auditable — contributions that
keep it that way are the most welcome.

## Ground rules

1. **`bin/multivac.py` stays standard-library only.** No third-party imports in the shipped
   tool. `pytest` is a dev-only dependency for the test suite.
2. **Add a test for every behavior change.** Tests live in `tests/test_multivac.py` and are
   hermetic — subprocesses are mocked, no network. Prefer asserting real behavior over
   asserting mock calls.
3. **Keep the security posture.** Don't introduce `shell=True`, `eval`/`exec` of model
   output, network calls from the wrapper itself, telemetry, or auto-update. The child
   process contract (own stdin, split streams, own process group + wall-clock timeout,
   allow-listed env) is load-bearing — don't weaken it.
4. **Subscription auth only.** Never make a delegate default to an API key or a non-official
   auth path (no `claude --bare`, no `codex --oss`).

## Adding or changing a delegate

Per-tool behavior is driven by the `SPECS` table and `build_argv`/`parse_output` in
`bin/multivac.py`, with the verified flags documented in `references/cli-matrix.md`. If you
add or change a delegate:

- Verify the real headless invocation against the installed CLI (these CLIs change fast and
  unit tests that only check argv fragments **will** miss execution bugs — see the grok/agy
  fixes in the git history). Test an actual `ask` end to end.
- Update `references/cli-matrix.md` to match the code exactly.
- Note any quirk (e.g. grok needs `--no-auto-update` or it hangs on JSON output) in
  `references/gotchas.md`.

## Development

```console
uv run --with pytest python3 -m pytest -q      # run tests
MULTIVAC_LIVE=1 uv run --with pytest python3 -m pytest -q   # include the live smoke test
```

## Pull requests

- Keep changes focused; one concern per PR.
- Describe what you changed and how you verified it (paste real command output for behavior
  changes — `it works` isn't evidence).
- Commit messages: imperative mood, explain the "why" when it isn't obvious.

By contributing, you agree your contributions are licensed under the project's
[MIT license](LICENSE).
