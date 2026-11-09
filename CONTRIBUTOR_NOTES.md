# Contributor notes

Read the relevant section when changing test fakes, LLM requests, or remediating
CodeQL findings. These notes supplement [AGENTS.md](AGENTS.md); validation and
review follow-through remain governed by [CI.md](CI.md).

## Test fakes and typing

Do not inline a lambda that builds an ad-hoc `type(...)` object or nests another
lambda in `monkeypatch.setattr` / `patch`. Extract a named helper instead:

```python
def _build_harness() -> Any:
    return type("H", (), {"resolve_env_variables": lambda _self: None})()


monkeypatch.setattr(startup, "_build_harness", _build_harness)
```

Trivial lambdas such as `lambda **_kw: None` or `lambda: sentinel` may stay inline.

`make typecheck` covers product packages, not `tests/`. It does not establish
that new tests are type-clean; run mypy on the test path directly when it matters.

## LLM requests and tool schemas

- Tool schemas must be normalized in the provider adapter and covered by
  registry contract tests. Loose JSON Schema checks can pass schemas that fail
  at the LLM API, where all available tools are sent together.
- LLM clients are cached per role and serve concurrent gateway turns. Capture
  request-local facts when building a request and consult those facts on retry;
  another in-flight request may already have changed a shared flag.
- For example, a prompt-cache fallback must check whether the failing request
  carried cache markers, not whether the client still has markers enabled.
  Reproduce this race deterministically by having the fake dependency mutate
  shared state before raising; threads are not required.

## CodeQL and code quality

Preserve behavior when addressing an alert. Do not replace useful work with a
no-op or suppress a finding instead of fixing its cause.

| Finding | Safe remediation |
| --- | --- |
| `py/cyclic-import` | Move shared symbols to a leaf module. Function-local and `TYPE_CHECKING` imports still count as cycle edges. |
| `py/uninitialized-local-variable` / unreachable code | CodeQL does not model `NoReturn` helpers such as `pytest.skip`, `pytest.fail`, `sys.exit`, or `typer.Exit`. Bind values on every visible path; prefer `next(iterable, None)` and an explicit guard over `StopIteration` control flow. Extract a `_raise()` helper for a bare-`raise` reachability alert. |
| `py/ineffectual-statement` on a Protocol | Follow the root docstring-only rule. A clean scan or mypy result is not proof of compliance: `pass` and `raise NotImplementedError` bodies are not caught. |
| `py/ineffectual-statement` on `await` | Keep the await: reaping a cancelled task may be what closes its client. Bind the awaited result in a small helper rather than deleting the await. |
| `py/implicit-string-concatenation-in-list` | Extract long text into a constant and reference it from the list/tuple. Do not silence the alert with explicit `+`; implicit concatenation in a parenthesized assignment is fine. Shared constants still follow the root ownership rule. |
| `py/import-and-import-from` | Follow the file's existing import style, including inside functions; do not mix module imports and `from` imports for the same module. |
| Catching `BaseException` | Catch `Exception` for ordinary failures; do not swallow `KeyboardInterrupt` / `SystemExit` or retain `noqa: BLE001` to hide the issue. |
| Unused global variable | Cross-module imports may not be credited as uses. Keep related copy in a genuinely used structure, or co-locate a private constant with its sole reader. Do not add no-op self-references or `# noqa`; shared constants still belong under `config/constants/`. |
| `py/stack-trace-exposure` | Apply the root external-error rule at the sink/response boundary. Log details server-side; never send exception text, arguments, tracebacks, or provider/model/field internals to HTTP or chat gateway users. |

## Human-owned prompt changes

Humans should edit one skill card or prompt section per commit through a web
editor and review the resulting diff themselves. Agents may propose exact
current/replacement text, but must leave protected files untouched except for
the sole literal-replacement exception in [AGENTS.md](AGENTS.md#human-owned-files-mandatory).
