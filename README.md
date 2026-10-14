# agent-use

[中文](README.md) | [English](README.en.md)

用一条命令把任务交给任何能在终端里跑的 AI 模型：某家不可用时按你排好的顺序换下一家，每次的回答和过程都留档待审。装上就能用，不用起服务、不装依赖。

## 解决什么问题

- **每家用法都不一样。** 调用方式、输出格式、出错方式各不相同，脚本里每接一家就得单独写一套。
- **一家挂了活就停。** 限流、额度用完、登录过期，脚本跟着卡住。
- **结果说不清来路。** 拿到一段回答，不知道是哪家、哪个模型给的；失败了也不知道卡在哪一步。

## 装上之后有什么不同

- **一种调用方式接所有家。** 任务用同一个 JSON 请求提交，换模型只改配置，不改脚本。现成的命令行工具加几行配置就能接进来，不用写代码。
- **某家不可用时自动换下一家，活不会卡住。** 顺序由你排；你主动中断时整条停下，不会接着乱试。
- **每次都留底。** 任务原文、回答、每次尝试的记录和校验哈希存在同一个目录里，事后能查清是谁、用什么模型、怎么失败的。
- **结果一律当待审稿。** 每个结果都标着 `accepted: false`：生成出来不等于通过，由你或调用方核对后才算数。

## 相关项目

- [agent-use-adapters](https://github.com/Jerryx333/agent-use-adapters)：已经写好的常见 CLI 适配器，装上就能把任务交给你已登录的那几家。
- [aiw](https://github.com/Jerryx333/aiw)：让 AI 编程助手的主会话把读代码、跑命令、起草这类体力活派给别的模型，它用 agent-use 接入更多模型。

## 你可能会担心的

**安全吗？** agent-use 不读取、不保存账号凭据，登录交给各家 CLI 自己。但注册表等于本机可执行代码：谁能改注册表，谁就能让它执行任意命令；它也不是沙箱。接入前先看一眼适配器和命令。细节见[安全模型](docs/guide.zh-CN.md#安全模型)。

**会不会花钱？** 用的是你接入的那家 CLI 自己的账号和额度；agent-use 本身不收费，也不查价。

**要写代码吗？** 包装现成的命令行工具只要几行配置；有特殊需求时才需要[自己写适配器](docs/guide.zh-CN.md#编写自己的适配器)。

**任务和回答存在哪？** 以明文存在本机的输出目录里。密码、密钥别写进任务。

**支持 Windows 吗？** 不支持原生 Windows。需要 Linux、macOS 等 POSIX 系统和 Python 3.11+。

## 5 分钟上手

需要 Python 3.11+、Git 和 Bash。下面的示例离线运行，不需要模型账号。

```bash
git clone https://github.com/Jerryx333/agent-use.git
cd agent-use
python3 -B scripts/agent_use.py list --config config/dispatcher.json
```

`list` 会列出内置的 `echo`、`fail-demo`、`upper` 三个演示提供方。先用 `echo` 跑一单，它在任务前加 `echo: ` 原样返回，不调用模型：

```bash
printf '%s' '{"version":1,"task":"hello","providers":["echo"],"output":"runs/hello","expect_exact":"echo: hello"}' | python3 -B scripts/agent_use.py run --config config/dispatcher.json
cat runs/hello/response.txt
```

结果里有 `"state": "staged_for_review"` 和 `"accepted": false`。再体验自动换下一家：`fail-demo` 故意失败，随后 `echo` 接手。

```bash
printf '%s' '{"version":1,"task":"hello","profile":"fallback-demo","expect_exact":"echo: hello"}' | python3 -B scripts/agent_use.py run --config config/dispatcher.json
```

想接真模型：已登录常见 CLI 的话，装 [agent-use-adapters](https://github.com/Jerryx333/agent-use-adapters)，30 秒就能跑第一单；用别的命令行工具，见下一节。输出目录里每个文件是什么，见[使用指南](docs/guide.zh-CN.md#运行产物)。

## 接入自己的命令行工具

任何「从标准输入读任务、往标准输出写回答」的命令行工具，都能用 [cli_adapter.py](examples/cli_adapter.py) 直接接入。下面这份注册表把 `tr a-z A-Z` 当成一个「模型」，把 `hello` 变成 `HELLO`：

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

`tool_policy` 可取 `disabled`、`guarded`、`unknown`、`workspace`。workspace 适配器默认硬禁工具（`access=none`）；传 `workdir` 和 `access=ro` 可只开读类工具，`access=rw` 开全部内置工具并跳过确认，仅 `workspace` 路由可用。详见 [workspace 访问](docs/guide.zh-CN.md#workspace-工具访问)。

接你自己的工具时，把 `tr`、`a-z`、`A-Z` 换成它的程序名和参数，把 `default_model` 换成模型名。注册表放哪、包装器怎么传参，见[使用指南](docs/guide.zh-CN.md#接入命令行工具的细节)。

## 常用命令

都在仓库根目录运行；`--config` 省略时按[注册表查找顺序](docs/guide.zh-CN.md#注册表放在哪)自动选。

| 命令 | 用途与示例 |
| --- | --- |
| `list` | 列出提供方和配置组合：`python3 -B scripts/agent_use.py list` |
| `validate` | 只检查注册表和请求，不启动命令：`printf '%s' '{"version":1,"task":"hello","profile":"local"}' \| python3 -B scripts/agent_use.py validate --config runs/dispatcher.json` |
| `run` | 执行一单，失败按顺序换下一家：`printf '%s' '{"version":1,"task":"hello","profile":"local"}' \| python3 -B scripts/agent_use.py run --config runs/dispatcher.json` |

## 更多文档

- [使用指南](docs/guide.zh-CN.md)：运行产物、注册表放在哪、接入细节、编写适配器、私有适配器、安全模型。
- [调度协议](docs/DISPATCHER.md)（英文）：请求和结果字段、失败与回退、reason codes。
- [适配器指南](docs/ADAPTERS.md)（英文）：适配器输入输出、进程与凭据安全、测试方法。

## 参与贡献

在仓库根目录运行离线测试和 shell 检查：`bash scripts/test.sh`。默认按测试模块并发，进程数为 `max(1, CPU 核数 // 4)`，`-j N` 可调且上限为 `max(1, CPU 核数 // 2)`；入口自动尝试降到 nice 10，`--durations` 显示最慢的 10 个用例。所有用例均为轻量离线测试，没有重用例开关。改动走 PR；核心保持厂商无关，具体厂商的适配器放到独立仓库，详见 [CONTRIBUTING.md](CONTRIBUTING.md)。许可证：[Apache-2.0](LICENSE)。
