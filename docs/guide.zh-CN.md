# agent-use 使用指南

先看 [README](../README.md) 了解它解决什么问题；本文讲运行产物、注册表、接入细节、适配器和安全模型。

## 几个名词

- **provider（提供方）**：一个有名字的执行目标。
- **adapter（适配器）**：把目标接入 agent-use 的程序。
- **registry（注册表）**：列出提供方及其运行方式的 JSON 配置。
- **profile（配置组合）**：一组按尝试顺序排列的提供方。

## 运行产物

命令退出码为 0 时，进度写到 stderr，JSON 结果写到 stdout。下面是结果中的部分字段：

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

相同的成功请求在请求指纹和回复哈希仍匹配时可以复用目录。运行失败，或请求、产物、依赖、运行环境变化后，请使用新的输出目录。省略 `output` 时，会创建新的系统临时目录。

`fallback-demo` 配置组合中，`fail-demo` 会故意失败，随后 `echo` 成功；`attempts` 数组记录两次尝试。中断会停止整条链，详见[失败与回退语义](DISPATCHER.md#failure-and-fallback-semantics)。

## 接入命令行工具的细节

README 中的 `tr` 示例，`list`、`validate`、`run` 三个命令的退出码都为 0：`list` 列出 `my-cli`，`validate` 返回 `"state": "valid"`，`run` 返回 `"response": "HELLO"` 和 `accepted: false`。校验只检查注册表和请求，不启动命令，也不检查程序是否可用、认证是否成功或实际运行行为。

接入模型时，把 `tr`、`a-z`、`A-Z` 换成已安装 CLI 的可执行程序和真实参数，把 `default_model` 换成模型标识。`--` 后的所有参数都属于目标 CLI。包装器把任务文本送入 stdin，把非空白的 UTF-8 stdout 作为回复，丢弃 stderr。每个参数中出现的 `{model}` 和 `{task_file}` 都会被替换，且只替换一遍（替换进去的内容不会再展开），因此接受文件输入的 CLI 可以拿到任务文件路径。不进行 shell 展开，也不会把 `options` 自动转成 CLI 参数。认证使用 CLI 自己的机制；需要额外环境变量时，在注册表的 `env_names` 或 `env_prefixes` 中声明。详见[CLI 接入指南](ADAPTERS.md#quick-integration-with-a-local-cli)。

`{python}` 是当前 Python 解释器，`{repo}` 是 agent-use 仓库根目录。

## 注册表放在哪

注册表可放在任意位置并通过 `--config` 指定，也可放到下面的用户配置路径。两个入口都按以下顺序选取一份注册表：

1. 显式 `--config PATH`。
2. 非空 `AGENT_USE_CONFIG`（展开 `~`）。
3. `${XDG_CONFIG_HOME:-~/.config}/agent-use/dispatcher.json`。
4. 仓库自带的 `config/dispatcher.json`。

仅采用非空且为绝对路径的 `XDG_CONFIG_HOME`，否则以 `~/.config` 为基目录。用户注册表不存在时使用仓库默认。选中的注册表缺失、不可读或无效就报错，不回退；用户位置的软链断开时也报错。`XDG_CONFIG_HOME` 仅控制配置查找，不加入子进程环境白名单。

`list` 输出的 `config_path` 是解析后的注册表路径。README 示例显式指定 `--config`，可以避免本机已有的注册表影响练习。

## 编写自己的适配器

包装 CLI 不够用时，从 [echo_adapter.py](../examples/echo_adapter.py) 开始。从 stdin 读取一个 JSON 请求，通过 `task_file` 读取 UTF-8 任务，向 stdout 只输出一个 JSON 对象。成功时退出码必须为 0，并返回 `protocol_version: 1`、`state: "staged_for_review"`、`accepted: false` 和非空白 `response`。失败时返回 `state: "failed"` 和机器可读的 `reason`。

在注册表中声明 argv 形式的 `command` 和 `default_model`，再把提供方加入配置组合。可覆盖的键在 `options` 中声明，额外环境变量权限在 `env_names` 或 `env_prefixes` 中声明。除了成功路径，还要测试失败、超时、中断和子进程清理。详见[编写适配器](ADAPTERS.md)、[调度器协议](DISPATCHER.md)及 [Reason codes](DISPATCHER.md#reason-codes)。

## 私有适配器

公开核心保持通用。针对具体服务的适配器和注册表放在独立目录或私有仓库，例如 `~/my-adapters/`。这个私有 overlay 是单独选中的注册表，不会自动与内置注册表合并。通过 `--config`、`AGENT_USE_CONFIG` 或前述用户注册表路径选择它；用户位置也可以放软链。

在命令数组中，用 `["{python}", "-B", "{config_dir}/adapter.py"]` 引用私有适配器。`{config_dir}` 是注册表解析软链后的真实所在目录，`{python}` 和 `{repo}` 仍然可用。详见[协议中的私有适配器说明](DISPATCHER.md#private-adapters)。

## 安全模型

调度器不获取、保存或管理凭据，认证由适配器负责。任务、注册表选项、回复和诊断中不要放秘密。命令通过 argv 执行，不经过 shell。子进程环境使用白名单，额外权限由可信注册表声明。

把注册表和适配器当作本地可执行代码：能修改注册表，就能让它执行任意命令。适配器以当前用户的系统权限运行。这不是沙箱，运行前请审查代码和命令。`tool_policy` 是声明，不是系统级强制限制。

`accepted: false` 只标记草稿待审，不能阻止适配器产生副作用。任务和回复产物以本地明文保存。所有输出都需要人工或调用方复核。详见[信任边界](DISPATCHER.md#trust-boundaries)。

## 运行环境

- Python 3.11+；核心和内置适配器只用标准库。
- Linux、macOS 等 POSIX 系统。代码和测试依赖 POSIX 进程组及 `fcntl` 文件锁，不支持原生 Windows。
- 克隆仓库需要 Git；README 中的命令和测试使用 Bash，CLI 示例还需要标准工具 `tr`。
- 示例可以离线运行，无需模型账户。首次下载仓库需要网络；已有本地副本时跳过克隆步骤。
