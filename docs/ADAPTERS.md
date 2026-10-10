# Writing adapters

Adapters are trusted local programs. Start with
[echo_adapter.py](../examples/echo_adapter.py) for a protocol implementation or
[cli_adapter.py](../examples/cli_adapter.py) to wrap an existing command.
See [the protocol reference](DISPATCHER.md) for complete registry, metadata,
environment, and artifact rules.

## Input and output

Read one JSON object from stdin. The dispatcher supplies these fields:

| Field | Meaning |
| --- | --- |
| `protocol_version` | Integer `1` |
| `task_file` | Absolute path to an existing UTF-8 task file |
| `output` | Absolute path to the attempt output directory; create it if needed |
| `model` | Resolved model string |
| `timeout` | Timeout budget in seconds |
| `options` | Object containing declared defaults and permitted overrides |
| `expect_exact` | Expected response string, or `null` |

Emit exactly one JSON object on stdout. Put safe diagnostics on stderr, never
progress messages on stdout. Adapter stdin has a 4 MiB limit; combined stdout
and stderr also have a 4 MiB limit.

A successful adapter exits zero and returns:

```json
{"protocol_version":1,"state":"staged_for_review","accepted":false,"response":"draft text"}
```

`response` must be a nonblank string. The dispatcher checks `expect_exact`, so
the adapter need not duplicate that check. Optional `metadata` must be an object;
only recognized fields described in the protocol reference are retained.

A failure returns:

```json
{"protocol_version":1,"state":"failed","accepted":false,"reason":"example_failure","fatal":false}
```

`protocol_version` must be integer `1`, and `accepted` must be exactly `false`
on either response. A failure object can accompany a nonzero process exit.
`reason` must match `[a-z][a-z0-9_]{0,63}`. Optional `fatal` must be boolean.
Optional `detail` is made printable and truncated to 240 characters, which is
not credential redaction. For `credential_missing`, `missing_credential` can
identify what is missing with a safe label, never its value.

Ordinary failures, invalid responses, and timeouts advance to the next route.
`credential_missing` and `keychain_authorization_pending` are fatal categories;
an adapter can also set `fatal: true`. Fatal failures preserve their reason in
a single-route run but still allow fallback in a configured multi-route chain.
`interrupted` always stops the entire chain. Exhaustion reports
`all_providers_failed`. `staged_for_review` means a draft is available, not that
its contents or the adapter's actions have been approved.

## Process and credential safety

Launch child commands as argv arrays without shell evaluation. Do not interpolate
task text into shell commands. Respect `timeout` and begin cleanup early enough
to terminate and reap your children before the budget expires. Handle interruption
and clean up in a `finally` block on success and failure as well as timeout.
If children start separate sessions or process groups, explicitly clean those up:
the dispatcher's process-group cleanup cannot reach detached descendants.
The dispatcher enforces an outer deadline of the supplied budget plus 8 seconds;
that grace period is for cleanup, not additional task execution.

The registry and adapters have the authority of local executable code. A writable
registry can select arbitrary commands. `accepted: false` cannot prevent side
effects. `tool_policy` is a declaration, not a sandbox or enforcement mechanism.

The subprocess environment uses a default allowlist. Registry `env_names` adds
exact names and `env_prefixes` adds case-sensitive prefixes for that provider.
Prefer narrowly scoped permissions; see the protocol reference for allowed names
and prefix syntax. Adapters own authentication. Never return credentials in
responses, metadata, failure details, or diagnostics, and never put them in tasks
or registry options. Task and response artifacts are local plaintext.

## Quick integration with a local CLI

The shipped `upper` registration uses this command and belongs to `cli-demo`:

```json
["{python}", "-B", "{repo}/examples/cli_adapter.py", "--", "tr", "a-z", "A-Z"]
```

The registry expands `{python}` and `{repo}`. Arguments after `--` belong to the
target command. The wrapper substitutes `{model}` and `{task_file}` literally,
once per argument, without shell evaluation. For example, a local executable
that accepts these flags could use:

```json
["{python}", "-B", "{repo}/examples/cli_adapter.py", "--", "local-cli", "--model", "{model}", "--input", "{task_file}"]
```

Replace the executable and flags with those of your own CLI. The wrapper also
sends task bytes to stdin, uses UTF-8 stdout as the response, and discards stderr.
It does not translate `options` into command arguments. It cleans up its owned
target process group when execution ends.

| CLI outcome | Failure `reason` |
| --- | --- |
| Missing executable | `cli_not_found` (`fatal: true`) |
| Other launch error | `cli_unavailable` |
| Nonzero exit | `cli_exit_nonzero` |
| Empty or whitespace-only stdout | `empty_output` |
| Deadline exceeded | `timeout` |
| Stdout over 4 MiB | `output_too_large` |
| Invalid UTF-8 | `invalid_utf8` |

Other wrapper failures include `invalid_command`, `input_limit`,
`cli_adapter_failed`, and `interrupted` (`fatal: true`). The dispatcher separately
limits the complete adapter protocol output, including JSON overhead.

Register your adapter with a nonempty argv `command`, a `default_model`, and
optional `timeout`, `options`, `tool_policy`, `env_names`, and `env_prefixes`.
Add it to a profile. Option overrides must use keys declared in `options`.
Relative command paths resolve against the caller's working directory.

## Validate and test

From the repository root, validate the shipped CLI route without launching it:

```bash
printf '%s' '{"version":1,"task":"hello","profile":"cli-demo"}' | python3 -B scripts/agent_use.py validate
```

Successful validation exits zero and returns `state: "valid"` with resolved
routes. It checks the registry and request, not executable availability, adapter
responses, authentication, or side effects. For a separate registry, add
`--config registry.json`; `--request request.json` reads a request from a file.

Then run the offline example and check its response:

```bash
printf '%s' '{"version":1,"task":"hello","profile":"cli-demo","expect_exact":"HELLO"}' | python3 -B scripts/agent_use.py run
bash scripts/test.sh
```

For a new adapter, also exercise failure responses, malformed output, timeouts,
interruption, and child cleanup. Use fresh output directories after changing
dependencies or runtime environment; validation alone is not an execution test.
