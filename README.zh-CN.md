# agent-use

通用的本地 agent 调度器：按注册表运行适配器，失败时尝试备选，并保存待审稿。
结果始终是 `accepted: false`，生成成功不代表批准。附带两个离线示例，无需模型服务或账户。

[English](README.md) · [完整协议](docs/DISPATCHER.md) · [适配器指南](docs/ADAPTERS.md)

## 快速开始

需要 Python 3.11+、Bash 和 POSIX 系统。以下命令在仓库根目录运行：

```bash
python3 -B scripts/agent_use.py list
printf '%s' '{"version":1,"task":"hello"}' | python3 -B scripts/agent_use.py validate
printf '%s' '{"version":1,"task":"hello","profile":"fallback-demo"}' | python3 -B scripts/agent_use.py run
```

最后一条先记录 `fail-demo` 失败，再由 `echo` 返回 `echo: hello`。
JSON 包含尝试记录和产物目录；未指定 `output` 时使用新的系统临时目录。

也可以使用任务文件并指定产物目录：

```bash
mkdir -p runs
printf '%s' hello > runs/task.txt
python3 -B scripts/run_agent.py runs/task.txt runs/hello --expect-exact 'echo: hello'
python3 -B scripts/run_agent.py runs/task.txt runs/custom --option 'echo.prefix=draft: '
bash scripts/test.sh
SKILLS_DIR="$HOME/.agents/skills" bash scripts/install.sh
```

安装仅创建一个 `agent-use` 技能链接，可重复执行，遇到冲突拒绝覆盖。
默认技能目录与示例一致。相同的成功请求可复用已有目录；请求、适配器或产物变化，
以及之前运行失败，都需要使用新目录。

## 编写适配器

参考 `examples/echo_adapter.py`：从 stdin 读取一个 JSON 请求，通过 `task_file`
读取 UTF-8 任务，向 stdout 输出一个 JSON 对象。成功响应包含
`protocol_version: 1`、`state: "staged_for_review"`、`accepted: false` 和非空 `response`。
失败返回 `state: "failed"` 和机器可读的 `reason`。

在注册表中声明 argv 形式的 `command`、`default_model`、可覆盖的 `options`
以及可选 `env_prefixes` 和 `env_names`，再加入 profile。通过 `--config` 可使用独立注册表。
`{python}` 和 `{repo}` 分别展开为当前解释器和仓库根目录。
完整字段、状态与失败语义见 [协议文档](docs/DISPATCHER.md)，
失败分类见 [reason 词表](docs/DISPATCHER.md#reason-codes)。

## 私有适配器

把适配器和注册表放在仓库之外的独立目录或私有仓库，例如 `~/my-adapters/`。
在注册表中使用 `["{python}", "-B", "{config_dir}/adapter.py"]` 引用适配器。
`{config_dir}` 在每个命令参数中展开为注册表解析软链后的真实所在目录；
现有 `{python}` 和 `{repo}` 仍然可用。

通过环境变量指定注册表：

```bash
export AGENT_USE_CONFIG="$HOME/my-adapters/dispatcher.json"
python3 -B scripts/agent_use.py list
```

仅在 `XDG_CONFIG_HOME` 非空且为绝对路径时采用，否则忽略并使用 `~/.config`。

也可以在默认用户位置创建软链：

```bash
mkdir -p "${XDG_CONFIG_HOME:-$HOME/.config}/agent-use"
ln -sfn "$HOME/my-adapters/dispatcher.json" "${XDG_CONFIG_HOME:-$HOME/.config}/agent-use/dispatcher.json"
```

两个入口的查找顺序相同：显式 `--config PATH` → 非空 `AGENT_USE_CONFIG`
（展开 `~`）→ `${XDG_CONFIG_HOME:-~/.config}/agent-use/dispatcher.json` → 仓库自带 `config/dispatcher.json`。
`XDG_CONFIG_HOME` 只用于查找注册表，不加入子进程环境白名单。
`AGENT_USE_CONFIG` 为空时忽略；选中的注册表缺失或不可读就报错，不回退。
默认用户位置是断开的软链时也报错，不回退。
`agent_use.py list` 的 `config_path` 返回解析后的绝对路径，便于排查。

## 信任模型（Trust model）

调度器不获取、保存或管理凭据；认证由适配器负责。任务、配置和输出中不要放秘密。
命令通过 argv 执行，不经过 shell；环境变量默认采用白名单，额外前缀或精确变量名由可信注册表声明。
注册表和适配器等同于本机可执行代码：能修改注册表，就能在它被使用时执行任意命令。
`accepted: false` 只代表结果待审，不能阻止适配器本身的副作用。
适配器以当前用户权限运行，并非沙箱；运行前应审查代码及命令。
`tool_policy` 是适配器声明，不是系统级隔离。任务和回复以本地明文保存，结果始终待审。

## 许可

[MIT](LICENSE)。
