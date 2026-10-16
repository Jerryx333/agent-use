# agent-use

[English](README.md) | [中文](README.zh-CN.md)

Hand a task to any AI model you can run in a terminal, with one command. If one is unavailable, it tries the next in the order you chose, and every answer and attempt is saved for review. Install it and start using it, with no service to run or dependencies to install.

## What problems does it solve?

- **Every model works differently.** Invocation, output format and failure modes all differ, so each new model means another custom integration in your scripts.
- **One outage stops the work.** A rate limit, an exhausted quota or an expired login, and your script stalls.
- **You can't tell where a result came from.** You get an answer but not which service or model produced it, and when something fails you can't tell where.

## What changes after installation?

- **One way to call every model.** Submit tasks as the same JSON request; switching models means changing configuration, not scripts. An existing command-line tool connects with a few lines of configuration and no code.
- **If one is unavailable, it tries the next, so work continues.** You choose the order; when you interrupt a run, the whole chain stops instead of trying more.
- **Every run leaves a record.** The task, the answer, each attempt and checksums are saved in one directory, so you can later tell who answered, with which model, and how anything failed.
- **Every result is a draft.** Each result carries `accepted: false`: producing an answer does not approve it. You or the caller check it first.

## Related projects

- [agent-use-adapters](https://github.com/Jerryx333/agent-use-adapters): ready-made adapters for common CLIs, so you can hand tasks to the ones you are already logged in to.
- [aiw](https://github.com/Jerryx333/aiw): lets an AI coding assistant's main session hand off routine work such as reading code, running commands and drafting to other models; it uses agent-use to connect more models.

## Questions you may have

**Is it safe?** agent-use never reads or stores account credentials; each CLI handles its own login. But a registry is local executable code: anyone who can change it can make it run any command, and it is not a sandbox. Review adapters and commands before connecting them. See the [trust model](docs/guide.md#trust-model).

**Does it cost money?** Calls use the account and quota of the CLI you connect. agent-use itself is free and does not look up prices.

**Do I need to write code?** Wrapping an existing command-line tool takes a few lines of configuration. You only [write an adapter](docs/guide.md#write-your-own-adapter) for special needs.

**Where are tasks and answers stored?** As plain text in the output directory on your machine. Keep passwords and keys out of tasks.

**Windows?** Native Windows is not supported. You need a POSIX system such as Linux or macOS, and Python 3.11+.

## Five-minute start

You need Python 3.11+, Git and Bash. These examples run offline without a model account.

```bash
git clone https://github.com/Jerryx333/agent-use.git
cd agent-use
python3 -B scripts/agent_use.py list --config config/dispatcher.json
```

`list` shows the built-in demo providers `echo`, `fail-demo` and `upper`. Run a task through `echo`, which returns the task with an `echo: ` prefix rather than calling a model:

```bash
printf '%s' '{"version":1,"task":"hello","providers":["echo"],"output":"runs/hello","expect_exact":"echo: hello"}' | python3 -B scripts/agent_use.py run --config config/dispatcher.json
cat runs/hello/response.txt
```

The result includes `"state": "staged_for_review"` and `"accepted": false`. Now try automatic fallback: `fail-demo` fails on purpose, then `echo` takes over.

```bash
printf '%s' '{"version":1,"task":"hello","profile":"fallback-demo","expect_exact":"echo: hello"}' | python3 -B scripts/agent_use.py run --config config/dispatcher.json
```

To use real models: if you are logged in to common CLIs, install [agent-use-adapters](https://github.com/Jerryx333/agent-use-adapters) and run your first task in 30 seconds; for other command-line tools, see the next section. For what each file in the output directory holds, see the [guide](docs/guide.md#run-artifacts).

## Connect your own command-line tool

Any command-line tool that reads a task from standard input and writes the answer to standard output can connect through [cli_adapter.py](examples/cli_adapter.py). This registry treats `tr a-z A-Z` as a "model" that turns `hello` into `HELLO`:

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
printf '%s' '{"version":1,"task":"hello","profile":"local","expect_exact":"HELLO"}' | python3 -B scripts/agent_use.py run --config runs/dispatcher.json
```

`tool_policy` accepts `disabled`, `guarded`, `unknown`, or `workspace`. Workspace adapters hard-disable tools by default (`access=none`). Opt in with `workdir` + `access=ro` (read tools only) or `access=rw` (all built-in tools, confirmations bypassed); only `workspace` routes qualify. See [workspace access](docs/guide.md#workspace-access).

For your own tool, replace `tr`, `a-z` and `A-Z` with its program and arguments, and `default_model` with the model name. Where registries live and how the wrapper passes arguments: see the [guide](docs/guide.md#cli-integration-details).

## Common commands

Run them from the repository root. Without `--config`, the registry is chosen by the [lookup order](docs/guide.md#where-registries-live).

| Command | Purpose and example |
| --- | --- |
| `list` | List providers and profiles: `python3 -B scripts/agent_use.py list` |
| `validate` | Check the registry and request without starting commands: `printf '%s' '{"version":1,"task":"hello","profile":"local"}' \| python3 -B scripts/agent_use.py validate --config runs/dispatcher.json` |
| `run` | Run a task, falling back in order: `printf '%s' '{"version":1,"task":"hello","profile":"local"}' \| python3 -B scripts/agent_use.py run --config runs/dispatcher.json` |

## More documentation

- [Usage guide](docs/guide.md): run artifacts, where registries live, integration details, writing adapters, private adapters, trust model.
- [Dispatcher protocol](docs/DISPATCHER.md): request and result fields, failure and fallback, reason codes.
- [Adapter guide](docs/ADAPTERS.md): adapter input and output, process and credential safety, testing.

## Contributing

Run the offline tests and shell checks from the repository root: `bash scripts/test.sh`. Modules run in parallel with `max(1, CPUs // 4)` workers by default; `-j N` is capped at `max(1, CPUs // 2)`. The runner attempts to lower priority to nice 10; `--durations` shows the slowest 10 cases. All cases are lightweight and offline; there is no heavy-test switch. Submit changes through PRs. The core stays vendor-neutral; adapters for specific vendors belong in separate repositories. See [CONTRIBUTING.zh-CN.md](CONTRIBUTING.zh-CN.md). License: [Apache-2.0](LICENSE).
