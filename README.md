# agent-use

[简体中文](README.zh-CN.md) · [Protocol reference](docs/DISPATCHER.md) · [Adapter guide](docs/ADAPTERS.md)

agent-use is a unified dispatch entry point for AI coding assistants and the people
building workflows around them. Use the same command to hand a task to different
models or command-line tools, try the next one in order if a route fails, and save
`status.json` plus `response.txt` on success for the caller to review. Every result
keeps `accepted: false`: producing a draft never approves it. A **provider** is a
named execution target; an **adapter** is the program that connects that target to
agent-use; a **registry** is the JSON configuration listing providers and how to
run them. A **profile** names an ordered list of providers to try.

## Requirements

- Python 3.11+; the core and bundled adapters use only the standard library.
- A POSIX system, such as Linux or macOS. The code and tests require POSIX process
  groups and `fcntl` file locks; native Windows is not supported.
- Git to clone the repository, Bash for the commands and tests below, and the
  standard `tr` utility for the CLI example.

The examples run offline without a model account. Downloading the repository for
the first time requires network access; if you already have a local copy, skip
the clone step.

## Five-minute quick start

Clone the repository, then run all subsequent commands from its root:

```bash
git clone https://github.com/Jerryx333/agent-use.git
cd agent-use
python3 -B scripts/agent_use.py list --config config/dispatcher.json
```

`list` prints JSON with the providers `echo`, `fail-demo`, and `upper`, their
profiles, and `config_path` (the resolved registry path). The explicit `--config`
keeps this walkthrough independent of any registry already on your machine.

Run a task through the built-in echo adapter, which returns the task with an
`echo: ` prefix rather than calling a model:

```bash
printf '%s' '{"version":1,"task":"hello","providers":["echo"],"output":"runs/hello","expect_exact":"echo: hello"}' | python3 -B scripts/agent_use.py run --config config/dispatcher.json
```

The command exits 0, prints progress to stderr, and returns JSON on stdout.
These are selected fields from that JSON:

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

An identical successful request can reuse its directory if its fingerprint and
response hash still match. After a failed run or changes to the request, artifacts,
dependencies, or runtime environment, choose a fresh output directory. Omitting
`output` creates a new system temporary location.

To see automatic fallback, use the bundled `fallback-demo` profile:

```bash
printf '%s' '{"version":1,"task":"hello","profile":"fallback-demo","expect_exact":"echo: hello"}' | python3 -B scripts/agent_use.py run --config config/dispatcher.json
```

`fail-demo` deliberately fails; `echo` then succeeds. The `attempts` array records
both. Interruption stops the chain; see [failure semantics](docs/DISPATCHER.md#failure-and-fallback-semantics).

## Connect your own model: wrap a CLI

If you already have a command-line tool that accepts task text and prints a
response, use [cli_adapter.py](examples/cli_adapter.py). You do not need to write
the adapter protocol yourself. First try this complete, offline registry. It
wraps `tr a-z A-Z`, turning `hello` into `HELLO`:

```bash
mkdir -p runs
cat > runs/dispatcher.json <<'JSON'
{
  "version": 1,
  "default_profile": "local",
  "providers": {
    "my-cli": {
      "command": [
        "{python}", "-B", "{repo}/examples/cli_adapter.py",
        "--", "tr", "a-z", "A-Z"
      ],
      "default_model": "local-demo",
      "timeout": 60,
      "tool_policy": "unknown"
    }
  },
  "profiles": {
    "local": ["my-cli"]
  }
}
JSON
python3 -B scripts/agent_use.py list --config runs/dispatcher.json
printf '%s' '{"version":1,"task":"hello","profile":"local"}' | python3 -B scripts/agent_use.py validate --config runs/dispatcher.json
printf '%s' '{"version":1,"task":"hello","profile":"local","expect_exact":"HELLO"}' | python3 -B scripts/agent_use.py run --config runs/dispatcher.json
```

All three commands exit 0. `list` shows `my-cli`; `validate` returns
`"state": "valid"`; `run` returns `"response": "HELLO"` and `accepted: false`.
Validation checks the registry and request without launching the command. It does
not test executable availability, authentication, or runtime behavior.

To connect your model, replace `tr`, `a-z`, and `A-Z` with your installed CLI's
executable and actual arguments, and set `default_model` to your model identifier.
Everything after `--` belongs to that CLI. The wrapper sends task text to stdin
and uses nonblank UTF-8 stdout as the response; stderr is discarded. It substitutes
`{model}` and `{task_file}` once per argument, so a CLI that takes file input can
receive the task file path. No shell expansion occurs, and `options` are not
converted into CLI arguments. Configure authentication using the CLI's own
mechanism; declare any additional environment access with registry `env_names`
or `env_prefixes`. See [the integration guide](docs/ADAPTERS.md#quick-integration-with-a-local-cli).

`{python}` means the current Python interpreter and `{repo}` the agent-use root.
Keep the registry at any path selected by `--config`, or install it at the user
location below. Both entry points select one registry in this order:

1. Explicit `--config PATH`.
2. Nonempty `AGENT_USE_CONFIG` (with `~` expansion).
3. `${XDG_CONFIG_HOME:-~/.config}/agent-use/dispatcher.json`.
4. The bundled `config/dispatcher.json`.

Only a nonempty, absolute `XDG_CONFIG_HOME` is used; otherwise the base is
`~/.config`. An absent user registry selects the bundled registry. A selected
missing, unreadable, or invalid registry is an error, with no fallback; a broken
symlink at the user location is also an error. `XDG_CONFIG_HOME` controls discovery
only and is not added to the subprocess environment allowlist.

## Write your own adapter

Start with [echo_adapter.py](examples/echo_adapter.py) when wrapping a CLI is not
enough. Read one JSON request from stdin, read the UTF-8 task from `task_file`,
and emit exactly one JSON object on stdout. Success requires exit code 0,
`protocol_version: 1`, `state: "staged_for_review"`, `accepted: false`, and a
nonblank `response`. Failure uses `state: "failed"` and a machine-readable `reason`.

Register an argv `command` and `default_model`, then add the provider to a profile.
Declare overridable keys in `options` and additional environment access in
`env_names` or `env_prefixes`. Test failure, timeout, interruption, and child-process
cleanup as well as success. See [Writing adapters](docs/ADAPTERS.md),
[the dispatcher protocol](docs/DISPATCHER.md), and [Reason codes](docs/DISPATCHER.md#reason-codes).

## Private adapters

Keep the public core general-purpose. Put service-specific adapters and your
registry in a separate directory or private repository, such as `~/my-adapters/`.
This private overlay is a separately selected registry, not an automatic merge
with the bundled one. Select it using `--config`, `AGENT_USE_CONFIG`, or the user
registry location described above; a symlink at that location also works.

In its command array, use `["{python}", "-B", "{config_dir}/adapter.py"]` to refer
to a private adapter. `{config_dir}` is the registry's real containing directory
after resolving symlinks. `{python}` and `{repo}` remain available. See
[Private adapters in the protocol](docs/DISPATCHER.md#private-adapters).

## Use with aiw

[aiw](https://github.com/Jerryx333/aiw) is a companion project that uses
agent-use to power its text seats. See the aiw README for configuration
instructions.

## Trust model

The dispatcher does not obtain, store, or manage credentials. Adapters own
authentication. Keep secrets out of tasks, registry options, responses, and
diagnostics. Commands run as argv without a shell. Subprocess environments use
an allowlist; the trusted registry declares additional access.

Treat registries and adapters as local executable code. Anyone who can change a
registry can cause arbitrary commands to run. Adapters have the current user's
operating-system permissions. This is not a sandbox: review code and commands
before running them. `tool_policy` is a declaration, not system-level enforcement.

`accepted: false` marks a draft for review; it cannot prevent adapter side effects.
Task and response artifacts are local plaintext. All outputs need human or caller
review. See [Trust boundaries](docs/DISPATCHER.md#trust-boundaries) for details.

## Tests and contributions

Run the offline test suite and shell checks from the repository root:

```bash
bash scripts/test.sh
```

Submit changes through a pull request. Keep contributions provider-neutral and
follow [CONTRIBUTING.md](CONTRIBUTING.md). Licensed under [MIT](LICENSE).
