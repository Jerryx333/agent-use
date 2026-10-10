# agent-use

[English](README.md) · [完整协议](docs/DISPATCHER.md) · [适配器指南](docs/ADAPTERS.md)

agent-use 是给 AI 编程助手及其工作流开发者用的统一调度入口：用同一条命令把任务交给
不同的模型或命令行工具，某一路失败时按顺序尝试下一家，成功后统一保存 `status.json`
和 `response.txt`，交给调用方复核。每个结果都保持 `accepted: false`，生成草稿不代表
批准。**provider（提供方）**是一个有名字的执行目标；**adapter（适配器）**是把目标
接入 agent-use 的程序；**registry（注册表）**是列出提供方及其运行方式的 JSON 配置；
**profile（配置组合）**是一组按尝试顺序排列的提供方。

## 需要什么

- Python 3.11+；核心和内置适配器只用标准库。
- Linux、macOS 等 POSIX 系统。代码和测试依赖 POSIX 进程组及 `fcntl` 文件锁，
  不支持原生 Windows。
- 克隆仓库需要 Git；下面的命令和测试使用 Bash，CLI 示例还需要标准工具 `tr`。

示例可以离线运行，无需模型账户。首次下载仓库需要网络；已有本地副本时跳过克隆步骤。

## 5 分钟上手

克隆仓库，之后所有命令都在仓库根目录运行：

```bash
git clone https://github.com/Sean529/agent-use.git
cd agent-use
python3 -B scripts/agent_use.py list --config config/dispatcher.json
```

`list` 输出 JSON，列出 `echo`、`fail-demo`、`upper` 三个提供方、配置组合，
以及 `config_path`（解析后的注册表路径）。显式指定 `--config`，可以避免本机已有的
注册表影响这次练习。

先用内置 echo 适配器跑一个任务。它在任务前加上 `echo: ` 并原样返回，不调用模型：

```bash
printf '%s' '{"version":1,"task":"hello","providers":["echo"],"output":"runs/hello","expect_exact":"echo: hello"}' | python3 -B scripts/agent_use.py run --config config/dispatcher.json
```

命令退出码为 0，进度写到 stderr，JSON 结果写到 stdout。下面是结果中的部分字段：

```json
{
  "state": "staged_for_review",
  "accepted": false,
  "selected_provider": "echo",
  "response": "echo: hello"
}
```

结果还包含 `run_directory`。查看保存的产物：

```bash
ls runs/hello
cat runs/hello/response.txt
cat runs/hello/status.json
```

| 产物 | 内容 |
| --- | --- |
| `task.txt` | 提交的任务 |
| `status.json` | 状态、选中的提供方、尝试记录和校验哈希 |
| `response.txt` | 成功时写入的回复草稿，本例为 `echo: hello` |
| `attempts/` | 各次尝试的工作目录，适配器可以在这里写文件 |

相同的成功请求在请求指纹和回复哈希仍匹配时可以复用目录。运行失败，或请求、产物、
依赖、运行环境变化后，请使用新的输出目录。省略 `output` 时，会创建新的系统临时目录。

用内置的 `fallback-demo` 配置组合体验自动换下一家：

```bash
printf '%s' '{"version":1,"task":"hello","profile":"fallback-demo","expect_exact":"echo: hello"}' | python3 -B scripts/agent_use.py run --config config/dispatcher.json
```

`fail-demo` 会故意失败，随后 `echo` 成功；`attempts` 数组记录两次尝试。
中断会停止整条链，详见[失败与回退语义](docs/DISPATCHER.md#failure-and-fallback-semantics)。

## 接入自己的模型：包装已有 CLI

如果已有能接收任务文本、输出回复的命令行工具，可以直接使用
[cli_adapter.py](examples/cli_adapter.py)，不用自己实现适配器协议。
先试下面这份完整的离线注册表：它包装 `tr a-z A-Z`，把 `hello` 转成 `HELLO`。

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

三个命令的退出码都为 0。`list` 列出 `my-cli`，`validate` 返回
`"state": "valid"`，`run` 返回 `"response": "HELLO"` 和 `accepted: false`。
校验只检查注册表和请求，不启动命令，也不检查程序是否可用、认证是否成功或实际运行行为。

接入模型时，把 `tr`、`a-z`、`A-Z` 换成已安装 CLI 的可执行程序和真实参数，
把 `default_model` 换成模型标识。`--` 后的所有参数都属于目标 CLI。
包装器把任务文本送入 stdin，把非空白的 UTF-8 stdout 作为回复，丢弃 stderr。
每个参数中的 `{model}` 和 `{task_file}` 都只替换一遍，因此接受文件输入的 CLI
可以拿到任务文件路径。不进行 shell 展开，也不会把 `options` 自动转成 CLI 参数。
认证使用 CLI 自己的机制；需要额外环境变量时，在注册表的 `env_names` 或
`env_prefixes` 中声明。详见[CLI 接入指南](docs/ADAPTERS.md#quick-integration-with-a-local-cli)。

`{python}` 是当前 Python 解释器，`{repo}` 是 agent-use 仓库根目录。
注册表可放在任意位置并通过 `--config` 指定，也可放到下面的用户配置路径。
两个入口都按以下顺序选取一份注册表：

1. 显式 `--config PATH`。
2. 非空 `AGENT_USE_CONFIG`（展开 `~`）。
3. `${XDG_CONFIG_HOME:-~/.config}/agent-use/dispatcher.json`。
4. 仓库自带的 `config/dispatcher.json`。

仅采用非空且为绝对路径的 `XDG_CONFIG_HOME`，否则以 `~/.config` 为基目录。
用户注册表不存在时使用仓库默认。选中的注册表缺失、不可读或无效就报错，不回退；
用户位置的软链断开时也报错。`XDG_CONFIG_HOME` 仅控制配置查找，不加入子进程环境白名单。

## 编写自己的适配器

包装 CLI 不够用时，从 [echo_adapter.py](examples/echo_adapter.py) 开始。
从 stdin 读取一个 JSON 请求，通过 `task_file` 读取 UTF-8 任务，向 stdout
只输出一个 JSON 对象。成功时退出码必须为 0，并返回 `protocol_version: 1`、
`state: "staged_for_review"`、`accepted: false` 和非空白 `response`。
失败时返回 `state: "failed"` 和机器可读的 `reason`。

在注册表中声明 argv 形式的 `command` 和 `default_model`，再把提供方加入配置组合。
可覆盖的键在 `options` 中声明，额外环境变量权限在 `env_names` 或 `env_prefixes`
中声明。除了成功路径，还要测试失败、超时、中断和子进程清理。
详见[编写适配器](docs/ADAPTERS.md)、[调度器协议](docs/DISPATCHER.md)
及 [Reason codes](docs/DISPATCHER.md#reason-codes)。

## 私有适配器

公开核心保持通用。针对具体服务的适配器和注册表放在独立目录或私有仓库，例如
`~/my-adapters/`。这个私有 overlay 是单独选中的注册表，不会自动与内置注册表合并。
通过 `--config`、`AGENT_USE_CONFIG` 或前述用户注册表路径选择它；用户位置也可以放软链。

在命令数组中，用 `["{python}", "-B", "{config_dir}/adapter.py"]` 引用私有适配器。
`{config_dir}` 是注册表解析软链后的真实所在目录，`{python}` 和 `{repo}` 仍然可用。
详见[协议中的私有适配器说明](docs/DISPATCHER.md#private-adapters)。

## 与 ai-harness 一起用

[ai-harness](https://github.com/Sean529/ai-harness) 是一个使用 agent-use 驱动其文本座位的配套项目。
配置方法见 ai-harness 的 README。

## 安全模型（Trust model）

调度器不获取、保存或管理凭据，认证由适配器负责。任务、注册表选项、回复和诊断中
不要放秘密。命令通过 argv 执行，不经过 shell。子进程环境使用白名单，额外权限由
可信注册表声明。

把注册表和适配器当作本地可执行代码：能修改注册表，就能让它执行任意命令。
适配器以当前用户的系统权限运行。这不是沙箱，运行前请审查代码和命令。
`tool_policy` 是声明，不是系统级强制限制。

`accepted: false` 只标记草稿待审，不能阻止适配器产生副作用。任务和回复产物以
本地明文保存。所有输出都需要人工或调用方复核。详见[信任边界](docs/DISPATCHER.md#trust-boundaries)。

## 测试与贡献

在仓库根目录运行离线测试和 shell 检查：

```bash
bash scripts/test.sh
```

改动通过 PR 提交。贡献应保持厂商无关，并遵守 [CONTRIBUTING.md](CONTRIBUTING.md)。
许可证为 [MIT](LICENSE)。
