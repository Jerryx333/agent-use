# Dispatcher protocol

## Interfaces

Python 3.11+ and POSIX process groups and advisory locks are required.
`agent_use.py list` prints the provider catalog. `validate` reads and validates a
request without launching adapters. `run` executes it. Use `--config PATH` to
select a registry and `--request PATH` to read a request file (default: stdin).
Input is limited to 4 MiB. Stdout contains JSON; progress goes to stderr.
Successful listing, validation, or staging exits 0; failure exits 1.

A request is an object with:

| Field | Meaning |
| --- | --- |
| `version` | Required integer `1` |
| `task` | Required nonblank string |
| `output` | Optional output directory; otherwise a fresh temporary location |
| `profile` | Registered profile; mutually exclusive with `providers` |
| `providers` | Ordered, nonempty list of names or route objects, without duplicates |
| `models` | Map of selected provider names to model overrides |
| `timeout` | Override each route's timeout, in seconds |
| `expect_exact` | Optional exact response string, checked by the dispatcher |
| `require_no_tools` | Boolean; reject selected routes not declaring `disabled` |

Route objects accept `provider`, `model`, `timeout`, and `options`. Unknown fields
are rejected. Models are nonblank single-line strings of at most 256 characters.
Timeouts are finite numbers in `(0, 86400]`; booleans are not timeouts.

The file interface is `run_agent.py TASK_FILE OUTPUT`, with optional `--config`,
`--profile`, `--order name,name`, repeated `--model provider=model`, repeated
`--option PROVIDER.KEY=VALUE`, `--timeout`, `--expect-exact`, and
`--require-no-tools`. Option overrides must target selected providers and keys
already declared in their registry `options`. Duplicate targets are rejected.
Values are strings, including empty strings; adapters validate their own value
semantics. JSON route options also require declared keys, but retain scalar types.

## Registry

The registry has integer `version: 1`, `providers` and `profiles` objects, and a
registered `default_profile`. Names match `[a-z][a-z0-9_-]{0,63}`.
A profile is an ordered list of provider names or route objects.

Provider fields:

| Field | Meaning |
| --- | --- |
| `command` | Required nonempty argv array; no shell evaluation |
| `default_model` | Required model string |
| `enabled` | Boolean, default `true` |
| `models` | Optional descriptive model list, not an exhaustive restriction |
| `timeout` | Default route timeout, default 60 seconds |
| `options` | Non-sensitive scalar defaults and declared override keys |
| `tool_policy` | `disabled`, `guarded`, or `unknown` (default) |
| `env_prefixes` | Optional array of additional allowed environment prefixes |
| `env_names` | Optional array of additional allowed exact environment names |

Each environment prefix must match `[A-Z][A-Z0-9]{1,62}_`: a trailing
underscore and at least three characters are required. Matching is case-sensitive
`startswith`. Exact names must match `[A-Z_][A-Z0-9_]{0,63}`; names starting
with `LD_` or `DYLD_`, and `PYTHONPATH`, `PYTHONSTARTUP`, `NODE_OPTIONS`,
`BASH_ENV`, and `ENV` are rejected. Omitting either array is equivalent to `[]`.
These permissions apply only to the provider's subprocess. The catalog includes
both declarations.

Option names use the same syntax as provider names; names suggesting secrets
are rejected. Values may be strings, booleans, integers, or finite floats.
Never put credentials in registry options. `{python}` and `{repo}` substitutions
are supported in command arguments. Relative command paths resolve against the
caller's working directory, not the registry directory.

To wrap a local CLI, register `command` as
`["{python}", "-B", "{repo}/examples/cli_adapter.py", "--", "<prog>", "<arg>"]`.
Arguments after `--` support literal, single-pass `{model}` and `{task_file}`
substitution without shell evaluation. Task text goes to stdin; UTF-8 stdout
becomes the response. The offline `cli-demo` profile runs `tr a-z A-Z`.
Nonzero exit, empty output, missing executable, timeout, and stdout over 4 MiB
report `cli_exit_nonzero`, `empty_output`, `cli_not_found` (fatal), `timeout`,
and `output_too_large`, respectively. Whitespace-only output is empty. Stderr
is discarded. Timeout cleanup terminates the owned target process group.

Model precedence is request `models`, route model, then registry default.
Timeout precedence is request timeout, route timeout, then registry default.
Options merge registry defaults and route overrides, then file-CLI overrides.
Disabled providers are skipped in profiles; explicitly requesting one fails
with `provider_disabled`. No enabled routes also produces that failure.

## Adapter protocol

The adapter receives one JSON object on stdin:

```json
{
  "protocol_version": 1,
  "task_file": "task.txt",
  "output": "attempt-output",
  "model": "echo-1",
  "timeout": 60,
  "options": {"prefix": "echo: "},
  "expect_exact": null
}
```

Actual `task_file` and `output` values are absolute paths. The task file exists;
the adapter may create its attempt output directory if needed. The dispatcher
passes a timeout budget and enforces a process deadline of that budget plus
8 seconds. Adapters should put diagnostics on stderr and emit exactly one JSON
object on stdout. Combined stdout and stderr are limited to 4 MiB; adapter stdin
has a separate 4 MiB limit. Timeout or interruption terminates the process group,
with forced termination after a short grace period.

Successful response:

```json
{"protocol_version":1,"state":"staged_for_review","accepted":false,"response":"echo: hello"}
```

The response must be nonblank and match `expect_exact` if provided. The adapter
need not implement that check. `accepted` must be exactly false, and protocol
version must be integer 1. A nonzero process exit cannot report success.

Failure response (may also accompany a nonzero exit):

```json
{"protocol_version":1,"state":"failed","accepted":false,"reason":"example_failure","fatal":false}
```

Reasons match `[a-z][a-z0-9_]{0,63}`. Optional `fatal` must be boolean.
Optional `detail` is normalized to printable text and truncated to 240 characters;
it is not secret detection. For `credential_missing`, a `missing_credential`
label is accepted only if it matches `[A-Za-z][A-Za-z0-9_. /-]{0,127}`.
The scheduler does not retrieve credentials. Do not emit secret values.

Optional success `metadata` must be an object. Recognized fields are model and
transport identifiers (`actual_model`, `selected_model`, `transport`), nonnegative
finite numbers (`credits`, `switch_count`, `model_switch_count`), nonnegative
integer token counts in `usage`, and at most 12 sanitized `model_attempts`.
Other metadata is discarded. Usage keys are `input_tokens`, `output_tokens`,
`thinking_tokens`, `cache_read_tokens`, and `total_tokens`.

## Failure and fallback semantics

Ordinary failures, invalid protocol, empty or mismatched responses, and timeouts
advance to the next route. `credential_missing` and
`keychain_authorization_pending` are fatal categories. An adapter can also
explicitly set `fatal: true`. For a single route, fatal failure preserves its
reason. In a configured multi-route chain, those failures still try the next
route. `interrupted` always stops the entire chain. Exhaustion reports
`all_providers_failed`. Unexpected scheduler failures report `router_failed`.
Raw process exceptions and invalid protocol payloads are not included in status.

## Artifacts and reuse

Run state moves from `running` to `staged_for_review` or `failed`. Every state
keeps `accepted: false`. `status.json` is atomically replaced as attempts advance;
`task.txt`, `response.txt` on success, and an `attempts` directory accompany it.
Attempts record provider, requested model, options, state, elapsed time, and
sanitized failure or metadata fields. JSON `run` adds `run_directory` and the
successful response text. File `run_agent.py` prints status only.

A nonblocking advisory lock protects each output location. Existing successful
runs are reusable only when the request fingerprint and response hash match.
The fingerprint covers task content, resolved routes, expected response,
the invocation working directory (resolved absolute path),
scheduler sources, and the content of existing files appearing as command
arguments. It does not discover imported modules, resolve executables through
PATH for hashing, or hash environment variable values. After changing adapter
dependencies or runtime environment, use a fresh output directory.
Failed, mismatched, or modified runs are never automatically resent.

## Trust boundaries

Commands are trusted local executables run without a shell. The base environment
allowlist is `PATH`, `HOME`, `USER`, `LOGNAME`, `SHELL`, `TMPDIR`, `LANG`, `TERM`,
`PYTHONIOENCODING`, `PYTHONUTF8`, and prefixes `LC_`, `AGENT_USE_`.
Provider `env_prefixes` and `env_names` extend it. This preserves runtime configuration and is not a sandbox. Prefixes can
expose secrets if configured too broadly; adapters remain responsible for their
own authentication and safe diagnostics. The dispatcher never approves drafts,
installs adapters automatically, or enforces an adapter's tool-policy declaration.
