# agent-use usage guide

Start with the [README](../README.en.md) for what problems it solves. This guide covers run artifacts, registries, integration details, adapters and the trust model.

## Terms

- **Provider**: a named execution target.
- **Adapter**: the program that connects that target to agent-use.
- **Registry**: the JSON configuration listing providers and how to run them.
- **Profile**: an ordered list of providers to try.

## Run artifacts

When a command exits 0, progress goes to stderr and JSON goes to stdout. These are selected fields from that JSON:

```json
{
  "state": "staged_for_review",
  "accepted": false,
  "selected_provider": "echo",
  "response": "echo: hello"
}
```

The response also includes `run_directory`. Inspect the saved artifacts:

```bash
ls runs/hello
cat runs/hello/response.txt
cat runs/hello/status.json
```

| Artifact | Contents |
| --- | --- |
| `task.txt` | The submitted task |
| `status.json` | State, selected provider, attempt records, and verification hashes |
| `response.txt` | The draft response, written on success (`echo: hello` here) |
| `attempts/` | Per-attempt workspace; adapters may write files here |

An identical successful request can reuse its directory if its fingerprint and response hash still match. After a failed run or changes to the request, artifacts, dependencies, or runtime environment, choose a fresh output directory. Omitting `output` creates a new system temporary location.

In the `fallback-demo` profile, `fail-demo` deliberately fails; `echo` then succeeds. The `attempts` array records both. Interruption stops the chain; see [failure semantics](DISPATCHER.md#failure-and-fallback-semantics).

## Workspace access

The default `access: "none"` preserves text-only behavior; workspace adapters hard-disable tools. `tool_policy` accepts `disabled`, `guarded`, `unknown`, and `workspace`. Only providers registered as `workspace` can handle `ro`/`rw`; other routes are filtered out, and an empty result is an error. Existing bundled registrations are unchanged.

For a workspace-capable registry, submit `{"version":1,"task":"Review this repository","workdir":"./repo","access":"ro"}` to the JSON entry point, or use `python3 -B scripts/run_agent.py task.txt runs/review --workdir ./repo --access ro`. `workdir` must exist and is resolved to an absolute path. It is required for `ro`/`rw` and forbidden for `none`; workspace access conflicts with `require_no_tools` / `--require-no-tools`. With `none`, `require_no_tools` accepts both `disabled` and `workspace`.

`ro` allows reading files, searching, and listing directories, without editing or running commands. `rw` enables all built-in tools, including writes and Bash, and bypasses confirmations. The adapter sets cwd to `workdir`, never loads MCP (strict MCP configuration and empty setting sources), and uses no `sandbox-exec` wrapper. Results remain `staged_for_review`: `response` is a review conclusion for `ro` or a change description for `rw`. Writes land directly in `workdir`; review the diff. `status.json` records `workdir`/`access`, and both enter the request fingerprint. See [adapter requirements](ADAPTERS.md#workspace-tool-modes).

## CLI integration details

For the `tr` example in the README, `list`, `validate` and `run` all exit 0: `list` shows `my-cli`; `validate` returns `"state": "valid"`; `run` returns `"response": "HELLO"` and `accepted: false`. Validation checks the registry and request without launching the command. It does not test executable availability, authentication, or runtime behavior.

To connect your model, replace `tr`, `a-z`, and `A-Z` with your installed CLI's executable and actual arguments, and set `default_model` to your model identifier. Everything after `--` belongs to that CLI. The wrapper sends task text to stdin and uses nonblank UTF-8 stdout as the response; stderr is discarded. It replaces every occurrence of `{model}` and `{task_file}` in each argument in a single pass (substituted values are not expanded again), so a CLI that takes file input can receive the task file path. No shell expansion occurs, and `options` are not converted into CLI arguments. Configure authentication using the CLI's own mechanism; declare any additional environment access with registry `env_names` or `env_prefixes`. See [the integration guide](ADAPTERS.md#quick-integration-with-a-local-cli).

`{python}` means the current Python interpreter and `{repo}` the agent-use root.

## Where registries live

Keep the registry at any path selected by `--config`, or install it at the user location below. Both entry points select one registry in this order:

1. Explicit `--config PATH`.
2. Nonempty `AGENT_USE_CONFIG` (with `~` expansion).
3. `${XDG_CONFIG_HOME:-~/.config}/agent-use/dispatcher.json`.
4. The bundled `config/dispatcher.json`.

Only a nonempty, absolute `XDG_CONFIG_HOME` is used; otherwise the base is `~/.config`. An absent user registry selects the bundled registry. A selected missing, unreadable, or invalid registry is an error, with no fallback; a broken symlink at the user location is also an error. `XDG_CONFIG_HOME` controls discovery only and is not added to the subprocess environment allowlist.

`list` reports `config_path`, the resolved registry path. The README examples pass `--config` explicitly so that a registry already on your machine does not affect them.

## Write your own adapter

Start with [echo_adapter.py](../examples/echo_adapter.py) when wrapping a CLI is not enough. Read one JSON request from stdin, read the UTF-8 task from `task_file`, and emit exactly one JSON object on stdout. Success requires exit code 0, `protocol_version: 1`, `state: "staged_for_review"`, `accepted: false`, and a nonblank `response`. Failure uses `state: "failed"` and a machine-readable `reason`.

Register an argv `command` and `default_model`, then add the provider to a profile. Declare overridable keys in `options` and additional environment access in `env_names` or `env_prefixes`. Test failure, timeout, interruption, and child-process cleanup as well as success. See [Writing adapters](ADAPTERS.md), [the dispatcher protocol](DISPATCHER.md), and [Reason codes](DISPATCHER.md#reason-codes).

## Private adapters

Keep the public core general-purpose. Put service-specific adapters and your registry in a separate directory or private repository, such as `~/my-adapters/`. This private overlay is a separately selected registry, not an automatic merge with the bundled one. Select it using `--config`, `AGENT_USE_CONFIG`, or the user registry location described above; a symlink at that location also works.

In its command array, use `["{python}", "-B", "{config_dir}/adapter.py"]` to refer to a private adapter. `{config_dir}` is the registry's real containing directory after resolving symlinks. `{python}` and `{repo}` remain available. See [Private adapters in the protocol](DISPATCHER.md#private-adapters).

## Trust model

The dispatcher does not obtain, store, or manage credentials. Adapters own authentication. Keep secrets out of tasks, registry options, responses, and diagnostics. Commands run as argv without a shell. Subprocess environments use an allowlist; the trusted registry declares additional access.

Treat registries and adapters as local executable code. Anyone who can change a registry can cause arbitrary commands to run. Adapters have the current user's operating-system permissions. This is not a sandbox: review code and commands before running them. `tool_policy` is a declaration, not system-level enforcement.

`accepted: false` marks a draft for review; it cannot prevent adapter side effects. Task and response artifacts are local plaintext. All outputs need human or caller review. See [Trust boundaries](DISPATCHER.md#trust-boundaries) for details.

## Requirements

- Python 3.11+; the core and bundled adapters use only the standard library.
- A POSIX system, such as Linux or macOS. The code and tests require POSIX process groups and `fcntl` file locks; native Windows is not supported.
- Git to clone the repository, Bash for the README commands and tests, and the standard `tr` utility for the CLI example.
- The examples run offline without a model account. Downloading the repository for the first time requires network access; if you already have a local copy, skip the clone step.

Run offline tests and shell checks from the repository root with `bash scripts/test.sh`; add `--durations` for the slowest 10 cases. Default module concurrency is `max(1, CPUs // 4)`; `-j N` is capped at `max(1, CPUs // 2)`. The runner attempts nice 10 automatically and isolates each module with a temporary HOME. There are no heavy cases or heavy-test switches.
