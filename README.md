# agent-use

A local, provider-neutral agent dispatcher. It runs registered adapters in order,
tries fallbacks when a route fails, and saves drafts for review. Every result has
`accepted: false`; generation never means approval. Two offline example adapters
are included. No model service or account is required to try them.

[Chinese](README.zh-CN.md) · [Protocol reference](docs/DISPATCHER.md) · [Adapter guide](docs/ADAPTERS.md)

## Quick start

Requirements: Python 3.11+, Bash, and a POSIX system (such as Linux or macOS).
Run these commands from the repository root:

```bash
python3 -B scripts/agent_use.py list
printf '%s' '{"version":1,"task":"hello"}' | python3 -B scripts/agent_use.py validate
printf '%s' '{"version":1,"task":"hello","profile":"fallback-demo"}' | python3 -B scripts/agent_use.py run
```

The last command tries `fail-demo`, then returns `echo: hello` from `echo`.
Its JSON includes attempts and the run directory. Without `output`, results are
stored in a new system temporary directory.

The file-based interface writes durable artifacts to a directory you choose:

```bash
mkdir -p runs
printf '%s' hello > runs/task.txt
python3 -B scripts/run_agent.py runs/task.txt runs/hello --expect-exact 'echo: hello'
python3 -B scripts/run_agent.py runs/task.txt runs/custom --option 'echo.prefix=draft: '
```

An identical successful request can reuse its directory. A changed request,
modified response, or failed run requires a new output directory.

```bash
bash scripts/test.sh
SKILLS_DIR="$HOME/.agents/skills" bash scripts/install.sh
```

Installation creates only the `agent-use` skill link, is idempotent, and rejects
conflicting content. `SKILLS_DIR` defaults to the location shown above.

## Write an adapter

Use [examples/echo_adapter.py](examples/echo_adapter.py) as a starting point.
Read one JSON object from stdin, read the UTF-8 task from `task_file`, and emit
one JSON object on stdout. A successful response contains `protocol_version: 1`,
`state: "staged_for_review"`, `accepted: false`, and a nonempty `response` string.
For failure, emit `state: "failed"` with a machine-readable `reason`.

Register an argv `command`, a `default_model`, and optional settings in
`config/dispatcher.json` or a separate registry selected with `--config`.
`{python}` and `{repo}` expand to the current interpreter and repository root.
Declare overridable keys in `options` and any additional environment prefixes
in `env_prefixes` or exact names in `env_names`. Add the provider to a profile to select it.
See [the full protocol](docs/DISPATCHER.md) for validation and failure semantics,
and [Reason codes](docs/DISPATCHER.md#reason-codes) for failure classification.

## Private adapters

Keep your adapters and registry in a separate directory or private repository,
for example `~/my-adapters/`. In that registry, use a command such as
`["{python}", "-B", "{config_dir}/adapter.py"]` to reference your adapter.
`{config_dir}` expands in each command argument to the real directory containing
the registry, after resolving symlinks. `{python}` and `{repo}` remain available.

Select the registry with:

```bash
export AGENT_USE_CONFIG="$HOME/my-adapters/dispatcher.json"
python3 -B scripts/agent_use.py list
```

Alternatively, create a symlink at the default user location:

```bash
mkdir -p "$HOME/.config/agent-use"
ln -s "$HOME/my-adapters/dispatcher.json" "$HOME/.config/agent-use/dispatcher.json"
```

Both entry points use this lookup order: explicit `--config PATH`, nonempty
`AGENT_USE_CONFIG` (with `~` expansion), the user registry at
`${XDG_CONFIG_HOME:-~/.config}/agent-use/dispatcher.json`, then the bundled
`config/dispatcher.json`. Use `XDG_CONFIG_HOME` only when nonempty and absolute;
otherwise ignore it and use `~/.config`. An empty `AGENT_USE_CONFIG` is ignored.
`XDG_CONFIG_HOME` only selects the registry; it is not added to the subprocess
allowlist. A selected missing or unreadable registry is an error, with no fallback;
a broken symlink at the user location is also an error. `agent_use.py list`
includes `config_path`, the resolved absolute registry path, for troubleshooting.

## Trust model

The dispatcher does not obtain, store, or manage credentials. Keep secrets out
of tasks, registry options, and adapter responses. Adapters own authentication.
Commands run as argv without a shell; subprocess environments use an allowlist.
Additional environment access must be declared in the trusted registry.

Treat registries and adapters as local executable code: anyone who can change
a registry can cause arbitrary commands to execute when it is used.
`accepted: false` only marks a result as awaiting review; it cannot prevent
side effects performed by an adapter.

Adapters run with the current user's operating-system permissions. This is not
a sandbox: review adapter code and registry commands before running them.
`tool_policy` is an adapter declaration, not operating-system enforcement.
Task and response artifacts are local plaintext. All outputs remain drafts for
human or caller review. See the protocol reference for environment and metadata
filtering details.

## License

[MIT](LICENSE).
