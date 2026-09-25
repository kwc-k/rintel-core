# Rintel

**面向 Human 与 AI Agent 的证据驱动软件智能工具。**

[English](README.md)

> **积极开发中。** 本地 MCP、CLI 和 API 接口现在可以使用；我们推荐先从 MCP 入手。图形化 Workbench 仍在开发中，欢迎大家试用并反馈。Compiler、link、runtime、data 和 performance 等高级证据，取决于具体仓库实际采集到了什么。

## 快速开始

先在本机安装 Rintel：

```bash
git clone https://github.com/kwc-k/rintel-core.git
cd rintel-core
./install.sh
```

先通过 Workbench 注册并索引一次本机仓库：

```bash
./rintel serve
```

打开 <http://127.0.0.1:8000>，添加要分析的仓库。然后在 Rintel clone 的目录中，将本地 stdio MCP server 注册到 Codex：

```bash
./rintel doctor
codex mcp add rintel -- "$PWD/rintel" mcp
codex mcp list
```

通过 AI Agent 探索已索引仓库时，推荐先用 MCP。这会在本机启动进程并连接同一个产品 datastore，并非托管的在线 MCP 服务。Workbench 仍可用于初始设置与可视化探索。克隆需要 Git；安装需要 `uv`、Node.js 20+，以及 `npx` 或 pnpm 11.22.0。安装器会通过 `uv` 在项目环境中获取 Python 3.12，并按锁文件构建本地 UI。正常安装不需要 PostgreSQL，默认使用 SQLite。`./rintel doctor --json` 还会报告 MCP 就绪状态、datastore 身份和实际暴露的工具。

## Rintel 用来做什么

Rintel 把软件仓库转化为可持久查询、可追溯证据的系统模型。Human 可以在 Workbench 中检查它；Agent 可以经 MCP 查询同一底层模型，而不必每次都从文件搜索重新拼凑项目。

```text
仓库 → 证据模型 → 结构、符号、拓扑、Flow、设计、
                  Runtime、Build/Test、Git 状态
               ↘ Workbench · MCP Agent · CLI/API
```

从仓库或子系统出发，沿符号和关系追踪路径，再下钻到支持该关系的事实、精确 SourceSpan 或运行记录。Rintel 支持索引和查询 Python、TypeScript/JavaScript、Go、Java、C、C++、Fortran。证据深度依赖语言、构建上下文和已配置 Provider；图上有边，不等于编译器已经证明了调用目标。

Rintel 也将观察到的 AS-IS 与计划中的 TO-BE 架构、Flow 分开。DesignChange、DRC/LVS 类检查、受约束的 Build/Test 和 Git workspace 保持原有权限边界；画出设计关系或合并 Git 分支，都不会自动发布 Canonical truth。

## 证据边界

Rintel 分别保留 **authority、truth、resolution、coverage、execution modality**，不因 Agent 的解释、设计意图或一次运行观察而暗中升级静态结论。

```text
设计意图           ≠ 已观察到的源码事实
候选目标           ≠ EXACT 解析
PARTIAL 覆盖       ≠ COMPLETE 覆盖
Runtime OBSERVED  ≠ Static MUST 或 PRESENT
Git merge         ≠ Canonical CURRENT
```

要断言 `MISSING`，必须先有对应范围内足够的覆盖证明。`UNKNOWN` 不等于 `FALSE`；`NOT_OBSERVED` 只表示某次运行没有观察到。证据不足时，结果应说明未知范围、原因和下一类所需证据，而不是默默猜测。目前部分界面仍直接显示底层 formal fields，面向用户的表达还在改进。

支持材料可能来自 SourceSpan、索引器或编译器观察、object/link 产物、runtime trace、Build/Test receipt 和 Git 状态。具备某种采集机制，**不等于**每个仓库已经拥有该类证据。

## 给 AI Agent 使用

当前 `main` checkout 包含[快速开始](#快速开始)配置的本地 stdio MCP server。先注册并索引仓库，再通过 Agent 查询。如果 UI 和 MCP 启动时使用不同的 `RINTEL_DATA_HOME` 或 `RINTEL_DATABASE_URL`，它们可能指向不同的 datastore；找不到仓库时，请在 MCP 环境中查看 `./rintel doctor`。

默认 MCP surface 以只读为主。显式运行 `./rintel mcp --preset design-execute` 才会暴露额外的、已有且受约束的设计与 workspace 工具；这不授予任意 shell、owner approval 或 Canonical publication 权限。实际可用工具以 MCP `tools/list` 为准。Codex 本地 stdio 路径经过测试；其他 MCP client 的互操作性需要在相应 client 中验证。

可以让 Agent：

> 找一条非平凡链路，给出每一跳的证据和源码；遇到 `UNKNOWN` 或 `PARTIAL` 时解释边界，不要猜目标。

## Human Workbench

本地 UI 与 MCP Agent 使用同一证据模型，涉及仓库浏览、源码与证据检查、拓扑、架构、Flow、DesignChange、Runtime、Build/Test 和 Git 状态。Workbench 仍在开发中，布局和工作流可能变化；欢迎大家试用，并通过[支持渠道](SUPPORT.md)反馈。

## 本地优先与安全默认值

`./rintel serve` 默认只监听 `127.0.0.1`。浏览器不能提交任意 shell 命令；Build/Execution 必须使用服务端定义的 profile。接入外部 AI client 后，项目内容是否由其服务商处理，取决于该 client 自身的配置与数据政策。

默认 SQLite 数据库：macOS 为 `~/Library/Application Support/Rintel/evidence.db`；Linux 为 `$XDG_DATA_HOME/rintel/evidence.db`，未设置该变量时回退到 `~/.local/share/rintel/evidence.db`。可用 `RINTEL_DATA_HOME` 改变本地数据目录；`RINTEL_DATABASE_URL` 是可选 PostgreSQL 高级配置。

重跑 `./install.sh` 可按锁文件更新应用依赖。先停止 Rintel，再运行 `./uninstall.sh`：它只删除项目局部环境及 UI 依赖/构建结果，**不会**删除用户源码仓库、Rintel datastore 或用户缓存。升级或手动删数据前，请先备份 datastore。

## 当前状态

| 范围 | 当前 `main` checkout |
|---|---|
| 仓库索引、搜索、源码、拓扑、证据查询 | 可用 |
| Agent 本地 MCP | 可用；默认以只读为主 |
| Architecture、Flow、DesignChange、DRC/LVS | 在证据与权限边界内可用 |
| Build/Test、Git 协作 | 需配置并获得相应授权 |
| Compiler/link、Runtime 证据 | 取决于 Provider、构建上下文和采集覆盖 |
| Data/performance 结论 | 只有实际测量证据存在时才能给出 |
| 图形化 Workbench | 开发中，欢迎试用和反馈 |
| 可复用任务 Skills、更多 client 集成 | 开发中；不是已发布承诺 |

Rintel 的目标是为理解、设计、执行和验证提供共同底座，而不是给 Agent 再建一套 truth model：证据在底层，行动在上层，不暗中猜测。

## 更多资料

后续方向见[路线图](ROADMAP.md)。实现规格、验收报告与实验保留在 [`docs/`](docs/) 和 [`analysis_tournament/`](analysis_tournament/)，不再堆进 README。项目政策见[贡献指南](CONTRIBUTING.md)、[安全说明](SECURITY.md)、[支持渠道](SUPPORT.md)。

采用 [Apache-2.0 许可证](LICENSE)；另见 [NOTICE](NOTICE) 和[商标说明](TRADEMARKS.md)。
