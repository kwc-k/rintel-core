# Rintel

Rintel 是本机运行的代码证据工作台。首次安装只走这一条路径：

```bash
git clone https://github.com/kwc-k/rintel-core.git
cd rintel-core
./install.sh
./rintel serve
```

打开 `http://127.0.0.1:8000`，在界面中添加本机 Git 仓库；Rintel 会注册并索引它。
安装需要 `uv`、Node.js 20+（附带 `npx`）；从 GitHub clone 还需要 Git。安装器使用固定版本 pnpm 11.22.0，若本机没有匹配版本则通过 `npx` 在项目安装过程获取，不要求全局 npm package。Git 工作区是可选能力；缺少 Git 时仍可索引普通文件夹。`./install.sh` 会逐项给出缺失原因与安装地址。
Python 3.12 由 `uv` 在隔离环境中选择或下载。安装不要求 PostgreSQL，默认使用 SQLite 和 loopback。

`./rintel doctor` 列出必需及可选能力；`./rintel doctor --json` 输出机器可读状态。
重跑 `./install.sh` 可按锁文件升级应用依赖与 UI；启动时会执行 SQLite 的事务式增量 schema 迁移。
升级前建议备份下述 datastore；迁移失败会回滚并拒绝启动，不会重建用户数据库。

停止 Rintel 后运行 `./uninstall.sh`，只移除项目内 Python 环境、UI 依赖和构建结果。源码仓库、用户源码、Rintel 数据库与用户缓存均保留。
macOS 数据库位于 `~/Library/Application Support/Rintel/evidence.db`；Linux 位于 `${XDG_DATA_HOME:-~/.local/share}/rintel/evidence.db`。
可用 `RINTEL_DATA_HOME` 指向另一数据目录。
应用文件是 `.venv/`、`web/node_modules/`、`web/dist/`；依赖缓存由 `uv cache dir` 和 `pnpm store path` 显示，开发测试缓存是 `.pytest_cache/`。索引在 `evidence.db`，Build/Execution/Git 工作状态在同一 Rintel 数据目录的子目录中；E2E 产物按测试 profile 单独清理。用户 repository 始终留在原路径。
如需彻底删除数据，请先备份并手动移除对应的数据目录；卸载脚本不会执行此操作。

默认服务只监听 `127.0.0.1`。Build Recovery 仅接受服务端配置的执行 profile，不接受浏览器提交任意 shell 命令。
PostgreSQL 为高级配置，通过 `RINTEL_DATABASE_URL` 启用；Clang、runtime instrumentation、多 Agent Git 和浏览器 E2E 为可选能力。

## 开发与历史说明

> 将代码库转换为统一的 **Repository Evidence Graph**：
> Human 通过六种 projection 逐层理解、追踪、审计大型代码库；
> Agent 查询同一底层证据图，在有限 token budget 下定位代码、判断影响范围。

```
Repository → Evidence Graph → { Human Views, Agent Queries }
```

本仓库实现 MVP 的 **P0 核心**（spec v0.1 见 `docs/SPEC-v0.1.md`）与
**P1 S1 — PostgreSQL parity**（human workbench SPEC 见 `docs/SPEC-P1-human-workbench.md`，v1.1-fix1）：

1. **Canonical schema** — 单一证据图：`nodes / edges / evidence / snapshots`
   （SQLite + WAL + FTS5）。
2. **Tree-sitter 适配器** — 7 种 REQUIRED 语言：Python、TypeScript/JavaScript、
   Go、Java、C、C++、**Fortran**（原生 module/subroutine/interface/use/call/
   bind(C) 语义，不强行建模为 class→method）。
3. **T0 fixtures + ground truth** — 每种语言一个小型、人工已知正确性的仓库，
   正确性由 harness 实测（precision/recall/location/cross-file/parse）。
4. **Indexer** — 全量 + 增量（只重解析变更文件；canonical identity 稳定；
   解析型关系每个 snapshot 全量重解析，跨文件身份永不过期）。
5. **Correctness harness + CLI** — `ri index / verify / search / dump /
   stats / diff`，原始逐项结果写入 `results/*.jsonl`（不只有 aggregate）。

## 开发者命令

```bash
uv sync                       # 安装依赖（tree-sitter 全家桶）
uv run ri verify fixtures -r --out results   # 全部 T0 fixture 正确性
uv run ri index <repo> --db evidence.db      # repository -> evidence graph
uv run ri search evidence.db PaymentService  # symbol/path/text 检索
uv run ri stats evidence.db                  # 图统计
uv run ri diff evidence.db                   # snapshot diff（spec §40）
uv run ri dump evidence.db --view architecture --out arch.json
uv run pytest tests/ -q                      # 完整测试（含增量一致性）
```

开发模式可分别启动 API 和 Vite；正常安装请使用上方单一入口。
「添加 repository」支持 **服务器本机原生文件夹选择器**（`POST /api/v1/repos/pick-folder`：macOS `osascript` · Linux `zenity` · Windows PowerShell；浏览器 webkitdirectory 拿不到服务端绝对路径，故选择器由后端唤起；无原生环境时手动输入绝对路径）。

当前 7 个 fixture 的验证结果（`ri verify fixtures -r`）：

| fixture       | lang       | nP | nR | eP | eR | loc | xf  | parse |
|---------------|------------|----|----|----|----|-----|-----|-------|
| c_basic       | c          | 1.0| 1.0| 1.0| 1.0| 1.0 | 1.0 | 1.0   |
| cpp_basic     | cpp        | 1.0| 1.0| 1.0| 1.0| 1.0 | 1.0 | 1.0   |
| fortran_basic | fortran    | 1.0| 1.0| 1.0| 1.0| 1.0 | 1.0 | 1.0   |
| go_basic      | go         | 1.0| 1.0| 1.0| 1.0| 1.0 | 1.0 | 1.0   |
| java_basic    | java       | 1.0| 1.0| 1.0| 1.0| 1.0 | 1.0 | 1.0   |
| python_basic  | python     | 1.0| 1.0| 1.0| 1.0| 1.0 | 1.0 | 1.0   |
| ts_basic      | typescript | 1.0| 1.0| 1.0| 1.0| 1.0 | 1.0 | 1.0   |

## 架构

```
src/rintel/
  schema.py       canonical DDL：nodes/edges/evidence/snapshots/files/
                  callsites/imports/includes/pending_edges/run_telemetry + FTS5
  model.py        数据模型：Node / EdgeSpec / Ref / Callsite / ImportBinding …
  identity.py     canonical qname 规则（跨文件身份）
  store.py        Store Protocol（SPEC-P1 §4）：Indexer/Harness 存储无关
  db.py           SQLite 存储层（实现 Store）：snapshot 复制、增量删除、
                  邻居查询、diff、search
  pg/             PostgreSQL 存储层：pgschema.py（SPEC-P1 §6.2 DDL）、
                  pgstore.py（PgStore : Store）、alembic baseline（0001）
  tsutil.py       tree-sitter 语言加载（API 版本兼容）
  adapters/       7 语言适配器（facts，非图；符号引用由 indexer 解析）
  indexer.py      管道：scan → hash → parse → store → resolution pass →
                  synthetic containment → snapshot → telemetry
  harness.py      ground-truth 正确性测量 + results/*.jsonl
  cli.py          index / verify / search / dump / stats / diff
scripts/
  index_to_pg.py         重索引路径：同一 Indexer 管线直写 PostgreSQL（§16 首选）
  migrate_sqlite_pg.py   一次性 SQLite → PostgreSQL 迁移（§16 兜底）
```

### 核心设计原则（spec §46）

- **一个 canonical graph**：Static / Runtime / Git / Test 是同一证据图的不同
  projection，不是四套数据。证据表 `evidence` 为每条非平凡关系携带
  `source / confidence / location / snapshot / timestamp`（spec §9）。
- **Identity**：`node:{kind}:{qname}`；同 (kind, qname) 合并（C 头文件声明
  与 .c 定义合为同一节点，跨文件身份）。C 非 static 符号按全局名；static
  为 `relpath::name`。
- **不制造边**（spec §32）：解析不了的关系写入 `unresolved` 表 + telemetry
  （如 `printf` → stdlib 调用；动态调用；外部 import）。
- **Incremental**（spec §26）：文件 hash + parser/indexer version 记录在
  `files` 表；只重解析变更文件；`callsites / pending_edges / imports /
  includes` 事实表在每个 snapshot 全量重解析（O(facts) 字典查询，不重新
  解析源码）。
- **LOD 就绪**（spec §20）：`neighbors()` BFS 带 node budget；
  `view_export()` 只导出当前 projection。

### 语言支持矩阵（round 1）

| 语言 | 节点 | 关系 |
|------|------|------|
| Python | MODULE/CLASS/METHOD/FUNCTION/VARIABLE/FIELD | CONTAINS/INHERITS/IMPORTS/REFERENCES/CALLS |
| TS/JS | MODULE/CLASS/INTERFACE/ENUM/TYPE/METHOD/FUNCTION/VARIABLE | 同上 + IMPLEMENTS |
| Go | PACKAGE/TYPE/INTERFACE/METHOD/FUNCTION/FIELD | CONTAINS/IMPORTS/CALLS/REFERENCES |
| Java | PACKAGE/CLASS/INTERFACE/ENUM/METHOD/FIELD | CONTAINS/INHERITS/IMPLEMENTS/CALLS/REFERENCES |
| C | FUNCTION/TYPE/ENUM/FIELD（头文件声明与定义合并） | CONTAINS/INCLUDES/CALLS |
| C++ | CLASS/METHOD/FIELD/FUNCTION/TYPE（qualified 定义与声明合并） | 同上 + INHERITS |
| Fortran | MODULE/SUBMODULE/PROGRAM/SUBROUTINE/FUNCTION/INTERFACE/TYPE/FIELD/VARIABLE | CONTAINS/IMPORTS/USES/IMPLEMENTS/CALLS/BINDS_TO/INCLUDES |

Fortran 特殊项（spec §2）均已落地并测试：GENERIC interface（具体过程
IMPLEMENTS 泛型接口）、internal procedures（CONTAINS 嵌套）、USE ONLY 绑定
解析、`SUBMODULE`（schema 允许，fixture 覆盖）、`BIND(C)` → 跨语言
`BINDS_TO`（Fortran 符号 ↔ C 符号按外部名绑定）、`INCLUDE`。

## P1 S1 — PostgreSQL parity（SPEC-P1 §23，完成）

- **Store Protocol**（`store.py`）：Indexer / Harness / CLI 存储无关；
  `Database`（SQLite）与 `PgStore`（PostgreSQL）双实现。
- **PgStore**：单事务 snapshot 构建 + 每 repo advisory lock（§6.1-5）；
  行形状与 SQLite 对齐（`meta_json` 等），canonical identity 与 SQLite
  **逐 id 等价**（S1 门：7 fixture node/edge id 集合完全一致 + 指标精确
  1.000）。
- **schema**：`pgschema.py` 幂等 DDL（evidence + architecture 平面，无独立
  designs 表，v1.1-fix1）；alembic baseline `0001_baseline`；
  `pg_trgm` 存在时建 trigram GIN（embedded 精简版缺 contrib 则跳过，
  正确性不依赖）。
- **脚本**：`scripts/index_to_pg.py`（重索引首选路径）、
  `scripts/migrate_sqlite_pg.py`（SQLite→PG 兜底迁移，等价性有测试）。

```bash
# PG 测试需要 Python 3.12 环境（pgserver 仅 cp312 wheel；嵌入式 PG 16 用于开发测试，
# 产品目标 PG 18，schema 不使用 18 专属特性）
uv venv --python 3.12 .venv312
uv pip install --python .venv312 -e . pytest pgserver
.venv312/bin/python -m pytest tests/ -q        # 70 passed（14 SQLite + 36 PG + 20 API）
uv run pytest tests/ -q                          # 默认 3.14 环境：14 passed + 56 skipped

# 生产/开发 PG（含 pg_trgm）：
docker compose up -d postgres                   # postgres:18
uv run alembic -c src/rintel/pg/alembic.ini upgrade head
uv run python scripts/index_to_pg.py --path <repo> --url $RINTEL_DATABASE_URL
```

## P1 S2 — Understand（SPEC-P1 §23，完成）

API 服务器 + Human UI 第一片：**打开仓库 → 看高层结构 → 下钻到符号 → 证据解释 →
源码跳转 → 多选**（spec §26 步骤 1–5、11–12）。

- **后端**（`src/rintel/server/`，FastAPI，base `/api/v1`）：`repos`（含
  `POST /repos` 创建 + 自动索引 job）、`tree`（懒加载 + q 过滤）、`symbols`、
  `structural-overview`（**R3 read-only Evidence 投影**，绝不自动生成架构）、
  `search`（exact→fts→trigram）、`source`（**R6**：drift 标志 / git show 历史 /
  404 `source_unavailable` 不回退）、`graph/neighborhood`（LOD 预算）、
  `evidence`（provenance 行）、`stats`、`jobs`（JobManager + PG `jobs` 表镜像）。
  统一错误信封 `{"error":{code,message,details}}`；evidence 平面只读。
- **前端**（`web/`，Vue 3 + TS strict + Pinia + Vite + Monaco 懒加载）：
  三栏 + 底部源码 dock；Repository Explorer（懒加载树 + checkbox/Ctrl/Cmd/Shift
  多选，选中态唯一在 workspace store）；Structural Overview（标注
  「Evidence / Code Structure — 基于证据的代码结构，非自动架构判断」）；
  Evidence Inspector（证据 + 依赖 In/Out + 未解析计数）；Source Viewer
  （Monaco 跳转 / drift 警告 / 404 错误态 + 「在树中定位」）；搜索按 match
  类型分组；UI 状态 localStorage 持久化（reload 恢复；S3 裁决：UI 便利状态留本地，服务端
  `workspace_ui_state` 未实现，架构资产必须进 PostgreSQL）。
- **测试**：`tests/test_api_contract.py` 20 用例（真实 PG 链）；
  `web/tests/unit/` 20 用例；`web/tests/e2e/acceptance.spec.ts` 7 门验收旅程
  （Playwright，截图存档 `docs/acceptance/s2-*.png`）。

```bash
# 本地开发（嵌入式 PG16 + FastAPI :8000 + Vite :5173）
.venv312/bin/python scripts/serve_pg.py --port 8000 --index fixtures/python_basic --label pyb
cd web && pnpm install && pnpm dev --host 127.0.0.1 --port 5173 --strictPort
# E2E（后端 :8000 + Vite :5173 在跑；使用系统 Chrome，见 web/playwright.config.ts）
cd web && pnpm exec playwright test
```

**已知限制（S2，均已显式记录）**：SQLite 后端的 jobs 为进程内存态（PG 后端有
`jobs` 表持久化）；聚合关系行无 canonical edge id，点击聚焦源模块（不伪造 id）。
~~Store 级 search 未按 snapshot 过滤~~ → **S5A 已修复**（`?snapshot=` 严格限定到该
快照，见 P1 S5A）；~~真实 PG 18 + `pg_trgm` 冒烟~~ → **S5A 已执行**（
PG18-PROD-SMOKE：postgres:18 + pg_trgm GIN 索引全绿）。

## P1 S3 — Architecture（SPEC-P1 §23，完成）

人类架构工作台第一闭环：**从 Evidence 多选 → 创建 Architecture Component（+N 条映射）→
Human Architecture Canvas → 人工关系 → Architecture Inspector → 映射跳回源码 →
reload 完全恢复**（spec §26 步骤 5–7、11–12）。

- **后端**（`src/rintel/server/api/workspaces.py`，base `/api/v1/workspaces`）：
  workspace CRUD + bootstrap（**创建即自动拥有空 AS-IS model，components=[]，R3+fix1**）；
  components（CRUD + `?subtree=true` 删除，**R5：有子组件/关系 → 409
  `component_has_children`/`component_has_relations`**）；`POST components/batch`
  （**R4：N evidence 实体 → 1 组件 + N 映射，单事务，任一无效整体回滚 422**）；
  relations（人建，同 model，self-loop/重复拒绝）；mappings（single/batch/delete，
  **staleness 是警示不是错误**，entity 解析附在 bootstrap）；layout（乐观锁 409）。
  错误码与 DTO 见 `docs/S3-WEB-CONTRACT.md` §2；SQLite `ARCH_DDL` 与 PG §6.2 镜像
  （复合 FK model 隔离 / partial unique 单 AS-IS / RESTRICT 语义双后端一致）。
- **前端**（`web/`）：中央栏切换 `[Code Structure | Architecture]`；**Human Architecture
  Canvas**（Vue Flow：选择/拖拽/连接/删除/层级 reparent/空态 CTA，节点 = kind 着色 +
  `N files · M symbols`，布局 800ms 防抖落 PG）；`Architecture Inspector`
  （Overview / Mappings / Dependencies / Notes，**Mappings 行点击 → Monaco 精确定位源码**，
  双向 Evidence↔Architecture 链）；MultiSelectBar「创建 Component」→ 批量创建后自动切
  画布并聚焦新组件；架构资产只存 PostgreSQL，UI 便利状态仍在 localStorage（裁决）。
- **测试**：`tests/test_arch_api.py` 15 用例（PG 真链：8 门 + 错误路径）+
  `tests/test_arch_sqlite.py` 7 用例（SQLite parity，默认环境可跑）；
  `web/tests/unit/` 37 用例；Playwright `arch-journey.spec.ts` 完整旅程
  （select→batch→canvas→relation→mapping→Monaco→reload 恢复→删除确认对话框，
  截图 `docs/acceptance/s3-*.png`）+ `acceptance.spec.ts`（S2 回归）。

**已知限制（S3，均已显式记录）**：层级 cycle 防护在 service 层（DB 复合 FK 不跨
SQLite/PG 做闭包检查，§6.1-12）；mapping 端点按 component 作用域（component id 全局
唯一，model 归属由组件决定）；画布 connect 拖拽已禁用 vue-flow autoPanOnConnect
（否则贴近画布边缘时 viewport 自动平移会破坏 drop 命中）；layout 乐观锁令牌未暴露给
前端（PUT 不带 `updated_at`，避免误 409）。S4 已补的 UI 入口：关系删除（边选中 +
Delete 键）、组件改名（Inspector Overview）、映射删除（Mappings 行 ✕）——见下节。

## P1 S4 — Design（SPEC-P1 §23 + 用户裁决 §S4，完成）

设计切片回答：**不改 AS-IS 的情况下提出目标架构，并明确看到“要改什么”以及“改动可能
触及哪些代码”**——`AS-IS → Proposal → TO-BE → DIFF → Structural Impact`。
核心数据语义（裁决冻结）：**Diff(P) = Current(P) − Baseline(P)**，fork 时冻结
`baseline_json`（schema_version=1）+ `base_evidence_snapshot_id`，此后 AS-IS 任何编辑
**不影响既有 diff**（baseline stability 测试锁定）。

- **后端**（`src/rintel/archmodel.py` 纯函数 + `src/rintel/server/api/design.py`）：
  `POST /workspaces/{wid}/models`（fork：单事务深拷贝 components/relations/hierarchy/
  mappings → proposal，组件带 `origin_id` 溯源，AS-IS layout 复制到 TO-BE 起始画布）；
  `GET models/{mid}/diff`（**frozen diff**：added/removed/**modified**/moved components +
  added/removed relations + mapping_changes；modified = kind/name/description 变化，不并入
  moved；relation 按存在性 diff，label-only 不报）；`POST models/{mid}/impact`
  （**Structural Impact**：seeds 规则 = added→proposal mappings / removed→baseline
  mappings / modified+moved→baseline∪proposal mappings；bounded traversal
  CALLS/IMPORTS/INCLUDES/REFERENCES depth≤2 + FILE/DIRECTORY effective-symbol 展开 +
  2000/4000 预算；显式 `snapshot_id`；输出 files/symbols/modules/supporting edges +
  **原因链**）。bootstrap 扩展：models 行带 baseline 元数据 + 顶层 `layouts{}`
  （per-model 布局）。store 双后端新增 `arch_model_baseline`/`arch_fork_model`。
- **前端**（`web/`）：Architecture 画布顶部工具条 `.arch-toolbar`（model 选择 AS-IS/
  Proposals + mode tabs **AS-IS | TO-BE | DIFF** + 「创建 Proposal」/「添加组件」）；
  AS-IS/TO-BE 复用同一套编辑（model 作用域隔离，**TO-BE 改动永不触碰 AS-IS**），
  DIFF 模式只读（画布按 diff 打标：added 绿 / modified `~` / moved `↳`，右栏
  `DiffPanel` 分组列出全部变化，点击行展开 evidence mappings + **Structural Impact**
  面板（标题与原因链文案冻结））；Inspector 组件名可编辑（blur 保存）、Mappings 行可删、
  多选栏新增「映射到组件」。
- **测试**：`tests/test_design_api.py` 13 用例（PG 真链：fork/初始空 diff/**isolation 硬门**/
  diff 全矩阵/baseline stability（fork V1 → AS-IS 演化 V2 → diff 仍相对 V1）/impact seeds 与
  链/persistence）+ `tests/test_design_sqlite.py` 10 用例（SQLite parity + archmodel 纯函数）；
  `web/tests/unit/` 44 用例；Playwright `design-journey.spec.ts` **18 步主链**
  （fork → 空 diff → TO-BE 增组件/加 group/删边重连/拖入 group → DIFF 分组断言 →
  点击 change → mappings 跳 gateway.py → Structural Impact 原因链 → 切回 AS-IS 原样 →
  reload proposal+layout+diff 全恢复 → AS-IS rename → 重开 Proposal diff 仍相对 V1，
  截图 `docs/acceptance/s4-*.png`）。

**S4 E2E 抓到的真产品 bug（已修复）**：vue-flow `NodeDragEvent.nodes` 只含被拖节点，
S3 遗留的 reparent-on-drop 目标查找永远落空（此前无 E2E 覆盖）；drop 命中判定还叠加了
节点拖拽 autoPan 与 4 列网格把节点排到视口外的问题——修复 = 目标查找改用全量节点 +
`autoPanOnNodeDrag=false` + fallback 布局改 2 列（新组件创建后必可见）。

**已知限制（S4）**：DIFF 视图 removed 组件不做 ghost 节点（baseline 无坐标，spec §11
ghost 渲染延期，以 Diff 面板红行呈现）；relation 的 label/kind 修改不产生 diff 行
（v1 按存在性比较）；mapping 变化仅对两侧都存在的组件报告（组件级增删已覆盖其映射）；
~~impact 的 DIRECTORY 映射不做前缀展开（v1 按普通节点遍历）~~ → **S5A 已修复**（seed
按 path 前缀展开为 effective symbol set）；model 删除/重命名/rebase/
promote（§7-25/28）未实现（P1 预留）；~~DEBT-SNAPSHOT-SEARCH~~ → **S5A 已修复**
（search 按 snapshot 过滤，`?snapshot=` 语义真实生效）。

## P1 S5A — Correctness Debt（裁决收紧，完成）

用户裁决把原 S5「Evidence Enhancement」收紧为 **S5A correctness debt + S5B
usability hardening**。S5A = 三项正确性/环境债务，全部落地：

1. **DEBT-SNAPSHOT-SEARCH 修复**：`Store.search`（SQLite `db.py` + PG `pgstore.py`）
   新增 `snapshot_id` 参数，`/api/v1/search` 把 `resolve_snapshot()` 的 sid 传入 store
   （此前 API 解析了 snapshot 却让 store 全快照搜索，去重后历史快照视图被新快照行
   污染——Proposal 记录 `base_evidence_snapshot_id` 后这个洞直接命中历史查看流）。
   回归：SQLite store 级（s1 看不到 s2 新增符号）+ PG API 级（`?snapshot=s1` →
   total=0）+ PG/SQLite parity（确定性 exact 查询，FTS 排序差异不参与比对）。
2. **PG18-PROD-SMOKE 执行**：把 PG16-embedded 上证明的全套 PG 测试在真实
   **PostgreSQL 18.6（docker `postgres:18`）+ pg_trgm** 上重跑：
   **121 用例全绿（0 failure / 0 skip，53.7s）**；同套在 embedded PG16 为
   120 passed + 1 skip（trgm 可用性门）。基础设施：
   `tests/conftest.py` 支持 `RINTEL_TEST_PG_DSN` 指向外部 server（跳过 pgserver）；
   每测试 schema 的 search_path 追加 `public`；`pgschema.create_all` 把扩展显式装入
   `SCHEMA public`（隔离 schema 下 `gin_trgm_ops` 可解析）。**pg_trgm GIN 三索引路径
   首次在真实 PG 上被执行**（embedded PG16 无 contrib，此前一直静默跳过）。
   复跑：`scripts/smoke_pg18.sh`。
3. **DIRECTORY impact 前缀展开**：Structural Impact 的 DIRECTORY seed 经新 store 方法
   `nodes_by_path_prefix`（双实现，SQLite 用 substr 保 case-sensitive 与 PG LIKE 对齐）
   展开为该目录下全部节点（effective symbol set，§14；受 IMPACT_NODE_BUDGET 保护，
   超限置 `truncated` 不静默丢 seed）。原实现只靠图 CONTAINS 展开——CONTAINS 仅递归
   FILE/DIRECTORY 容器，MODULE/CLASS 之下的符号被静默漏掉（复现：`src/checkout` seed
   只回 5/13 符号）；修复后 13/13 符号 + 4 文件 + 4 模块全回，原因链完整。
   前端无改动（Impact DTO 形状不变）。测试：`tests/test_design_api.py`
   `test_impact_directory_seed_expands_to_prefix`（PG 真链）+ `test_core.py`
   prefix 语义 + `test_pg_equivalence.py` parity + `test_pg_schema.py`
   `test_trgm_indexes_when_contrib_available`（embedded 跳过、真 PG18 断言
   pg_trgm 扩展 + 三个 GIN trigram 索引存在）。复跑：`scripts/smoke_pg18.sh`。

## P1 S5B — Real Repository Usability Hardening（完成）

裁决：S5B = 真实仓库走通 Human Architecture Loop 并修 blocker / high-friction /
correctness，不做大功能。主对象：**JusticePlutus**（真实 Python 股票分析 bot，
58 文件 / 1267 符号 / 2072 边，非 deterministic fixture）。

**18 步真实旅程通过**（`web/tests/e2e/s5b-journey.spec.ts`，21.5s；截图
`docs/acceptance/s5b-01..13.png`）：打开 repo → Structural Overview 判断主要区域
（badges：58 文件/1267 符号/2072 边/7 模块/4391 未解析；顶层关系含跨模块
src↔data_provider CALLS 聚合）→ 树钻孔 src/core/pipeline.py →
**StockAnalysisPipeline** CLASS（证据 tree_sitter + 位置）→ 关系聚合行 → 证据
→ Monaco 源码 → 多选建 3 组件（Pipeline Core / Fetchers〔data_provider 目录映射〕
/ Notifier〔notification.py+NotificationService〕）→ 画布连 2 条 DEPENDS_ON →
Arch↔Evidence↔Source 往返 → fork「Plutus Refactor」→ TO-BE 增 Entry Gateway
（main.py 映射）+ Infra group + 拖入 Fetchers → DIFF（added 2 / moved 1）→
**Structural Impact**：Entry Gateway seeds → 6 个真实受影响文件（main.py →
src/core/pipeline.py …）；Fetchers 移动（DIRECTORY seed → **S5A 前缀展开在真实
目录上生效**，>10 个 data_provider/* 文件 + 真实原因链）→ **点击 impact 文件回
源码** → AS-IS 原样 → reload 全部恢复。

**S5B 发现并修复的 observed problems（均带 regression）**：

1. **[correctness] UTF-8 字节偏移 bug（本轮最重要）**：tree-sitter 的
   start_byte/列都是 UTF-8 **字节**索引，adapters 却用 Python str（字符索引）
   切片 → 任何含多字节字符（中文注释/文档串）的真实文件，符号名整体错位：
   `class StockAnalysisPipeline:` 被索引成 `CLASS f __init__(`、docstring 片段
   （「、通知等模块」）变成 VARIABLE、合法符号被坏名占位（jpl 索引 1244→1267
   节点）。**fixtures 全 ASCII，T0 一直 1.000 掩盖了它——只有真实仓库会暴露**。
   修复：parse 字节 + text() 解码切片 + col/range 字节→字符转换（`adapters/base.py`）。
   回归：`test_core.py::test_multibyte_source_offsets`（精确名/qname/行列 + 无
   字符串内容泄漏 + callsite callee 正确）。
2. **[correctness/high-friction] fork 后 Proposal 未立即激活**：fork 201 与
   bootstrap 刷新之间 activeModel 回退 AS-IS → DIFF/TO-BE 点击弹「请先创建或
   选择 Proposal」，且该窗口内「添加组件」会写进 AS-IS（正确性危害）。修复：
   `stores/arch.ts::forkProposal` 乐观插入 proposal 到 bootstrap（models+layouts）
   后再刷新。回归：design-journey E2E（此前每次必现 toast）。
3. **[high-friction] Structural Impact 行不可点击**（裁决步骤 16「按原因链回
   源码」）：impact files/symbols 是纯文本。修复：后端 impact 行补
   `start_line`（symbols+files），前端 `.impact-file`/`.impact-symbol` 可点击 →
   focus evidence + 源码跳转（`StructuralImpact.vue` + `design.py` + arch.ts
   映射）。回归：`test_design_api.py`（start_line 断言）+ S5B E2E 步骤 16。
4. **[E2E 鲁棒性] hydration 瞬态竞态**（S3/S4 reload 后取布局 box 一帧为 null、
   S2 源码链接点击时未重渲染）→ expect.poll + 链接文本等待（产品行为未变）。

**粗粒度遥测**（S5B-TELEMETRY，非性能基准）：repo 打开 0.4–0.6s；第一次理解
（overview）3.4s；钻孔到符号 3.5s；关系证据 0.14s（1 次点击）；源码跳转 0.4s；
3 个组件 3.4s（每组件 ~4 次点击：勾选→多选创建→命名→提交）；2 条关系 1.4s；
Arch 往返 0.5s；fork 0.3s；TO-BE 编辑 2.4s；Diff 0.3s；Impact+链回源码 2.0s；
reload 恢复 1.8s。总活动耗时 ~19.5s，无 blocker，无回归。

**观察记录（backlog，非 blocker）**：Overview 顶层单位行（58）先于关系行，
CALLS 聚合（13）需滚动可见——可优化排序/过滤；未解析 4391（真实第三方调用，
信息噪声）；符号级依赖只能在聚合行/Architecture Inspector 查看（证据 Inspector
无 Dependencies 页签）；impact 原因链含 REFERENCES/IMPORTS 反向段，方向对新
读者需推敲；repo switcher 无删除入口（E2E 轮次积累的 fixture repo 污染列表——
可加清理按钮/只显示最近 N 个）。

## P1 S6 — Fortran Hardening / FAC-HPC Acceptance Preparation（完成）

裁决：fixed-form `.f/.for` 经**保守归一化**接入既有 FortranAdapter 与既有
resolver（不建第二套 parser/resolver，不改 free-form 语义、identity 规则与
no-fabrication 契约）。实现为 `src/rintel/adapters/fortran_fixed_form.py`：
列规则（cols 1-5 label / col 6 续行 / cols 7-72 字段）、注释列 C/c/*/D（转
`!`）、tab 格式、>72 列自适应（仅当文件含非序列号风格的超长代码行才扩展，
每个文件记录 note）、续行纯拼接（标准 fixed-form 语义，跨卡 Hollerith 计数
在拼接后的文本上计算）、数字内空格规整（`0.87 d0`/`8D 00`/`309 .`/数字分组
空格，字符串感知）、Hollerith `Nh...` → 字符串常量、F66 blank-insensitive
标识符（`G A M M A` → `GAMMA`，`GO TO`/`CALL FOO` 不受影响）。**冻结的
S5B UTF-8 契约**：归一化文本保留逐字符原始位置映射（行 + 字符列），证据
location 恒指向原始 `.f/.for` 文件（`tests/test_fixed_form.py` Gate 1-4）。

**FAC 量化结果（`scripts/s6_fac_report.py` 可复现）**：

| 指标 | before（冻结） | after（S6） |
|---|---|---|
| 索引文件 | 114 | 374（+260 `.f`） |
| 节点 | 2817 | 3570（SUBROUTINE 66→438，FUNCTION +94，PROGRAM 0→2） |
| 边 | 7672 | 8606（CALLS 4924→5571，+647） |
| callsites | 25077 | 33212（+8135 全部来自 `.f`） |
| unresolved（全量） | 16489（冻结基线 16506） | 23444（含新增 callsite 中 7207 个未解析） |
| **消费端（c/h/py）unresolved** | — | **−252**（.c −212 / .f90 −31 / .py −9） |
| **C→Fortran CALLS 边** | 0 | **121** |
| fixed-form parse | 0 文件 | **259/260 clean（99.6%）**，0 parse_error |

> 口径修正（FAC-EQ0 复核，`fac_eq0_report.py`）：消费端 like-for-like
> unresolved **−221**（.c −212 / .py −9 / .f90 0；S6 记录的 .f90 −31 为
> 当时 run 的偶发差异，以本次可复现复核为准）。总体 unresolved 数
> before/after 不可直接比较：observation universe 改变（见 FAC-EQ0 节）。

Gate 8 全绿：T0 7 语言全部轴 1.000；新增 fixture `fortran_fixed_basic`
（`fixtures/`）node/edge/location 精确匹配 + 跨语言 C→Fortran 桥 +
INCLUDE 闭包解析 + 故意 unresolved 不伪造；SQLite 133 pass/1 skip
（trgm 门）；**PG18.6 + pg_trgm 133/0 skip**；E2E S2/S3/S4/S5B 批跑
**4/4**；vitest 44/44、vue-tsc 0、build 绿。验收截图
`docs/acceptance/s6-fac-01..05.png`（374 文件 / 3570 符号 / 8606 边概览；
`modqed/uehling.f:2` 证据→源码跳转到原始 tab 格式文件）。

**remaining unresolved 分桶**（after）：`callsite_unresolved` 23444 = 真实外部
目标（C stdlib `free`/`malloc`/`fprintf`/`printf`/`strcmp`/`atoi`，Fortran 内置
`dabs`/`sqrt`/`log` 等 + 数组下标被树语法视为 call 的形状噪声，如
`fse_dat.f` 3366 处）与名字歧义；`edge_unresolved` 165（BINDS_TO/IMPORTS
指向仓库外）；`callsite_no_src` 20。无放宽 global-name 匹配：CALLS 规则分布
`candidate 5097 / global_unique 315 / same_file 159`，与 before 同一套规则；
抽样 global_unique 样本均为真实唯一名匹配；11 个有同名定义的 unresolved
callee 保持未解析（歧义 → 不伪造）。

**FORTRAN-LEGACY-BACKLOG（记录未实现）**：`COMMON`(51 文件)/`DATA`(66)/
`IMPLICIT`(50)/`EXTERNAL`(125)/`INTRINSIC`(121)/`PARAMETER`(133)/
`EQUIVALENCE`(4)/`ENTRY`(1)/`BLOCK DATA`(8) 全部被 grammar 容忍解析但
不产生图语义（文件 note 记录计数）；`ionis/recomb.f` 的 `309 .` 数字风格
留有 1 处 tree_error（文件级 99.6%，其 RECOMB 单元与调用仍提取）；C 侧
`daxpy_()` 式名字修饰调用不在当前 resolver 可表达范围（exact-name 桥已
生效 121 条）；数组下标→call 提取语义经 FAC-EQ0 修正（见下节）。

## P1 FAC-EQ0 — Unresolved Evidence Quality Audit（完成）

> **总入口：`docs/acceptance/p1-rc-evidence-report.md`** —— 合并交付量化 +
> 浏览器走查验证（六观察点 6/6、15 张截图），P1 RC 裁定用唯一文档。
> 证据档案：`docs/acceptance/fac-human-acceptance-evidence.md`；
> runbook：`docs/acceptance/fac-human-acceptance-runbook.md`。

裁决备注：S6 Gate 6 的 before/after 不能做总体直接比较——observation
universe 变了（.f 从不可见变为可见）。修正口径：旧观察域（c/h/py/f90）
like-for-like unresolved **−221**；新观察域 `.f` 新暴露 8,135 callsites
（S6 后口径）——其中 **928 解析 / 7,207 未解析**。新增 FAC-EQ0 门（三项）：

**① unresolved 按"为什么"分桶（索引期记录，API/UI 可见）。**
`src/rintel/eq0.py` + indexer 在每次 unresolved 时打上
`classification`：`true_external`（C stdlib/libm、BLAS-LAPACK、Python
builtin）/ `intrinsic`（Fortran 内建）/ `ambiguous_symbol`（同名多定义）/
`missing_target`（真实观察域外无目标）/ `cross_language`（如 C `foo_`
而仓库内 Fortran `foo` 存在——名字修饰边界，可分类不伪造）/
`call_vs_array_ambiguous`（`A(I)` 无法区分函数调用与数组下标，证据不足
→ 明确标 uncertain）/ `parser_noise` / `other`。分类只是标签，
**不做解析、不生成边**。

**② 低成本可确定的假 callsite 消灭（本地声明规则）。** FortranAdapter
按 scoping unit 收集数组声明（`REAL A(100)` / `DIMENSION C(30)` /
`COMMON /BLK/ D(50)` / free-form `dimension` 限定符，大小写不敏感）：
同一作用域内表达式位置的 `A(I)` 是数组访问（F77 中已声明数组不可被
调用）→ **不登记为 callsite**（文件 note `fortran:subscript_access_skipped=N`
计数）；无声明可判断的表达式调用**不猜** → 保留 callsite 并标
`call_vs_array_ambiguous`（CALL 语句形式一定不是数组，不标）。

**③ UI/语义：`unresolved` ≠ evidence of absence。** Structural Overview
badge 改为 `unresolved / 证据不确定`（tooltip：观察到可能需要解析的引用，
当前证据不足以确定目标——不是"缺失证据"的证明），并显示 top-3 分类
直方图；Evidence Inspector 同步措辞。

**FAC 量化（`scripts/fac_eq0_report.py` 可复现）**：

| 指标 | S6 after | FAC-EQ0 after |
|---|---|---|
| callsites | 33,212 | **26,883**（−6,329 被判定为声明数组访问，不再登记） |
| 边 | 8,606 | 8,581（−25 条 CALLS，其中 136 个先前"解析成功"的数组下标边被移除） |
| `.f` callsites | 8,135 | 1,806（792 解析 / 1,014 未解析） |
| `.f` unresolved | 7,207 | **1,014**（intrinsic 793 / call-array 87 / true_external 99 / missing 35） |
| unresolved 全量（notes） | 23,444 | 17,435 |

消除最多的假 callsite：`modqed/fse_dat.f` **3,365 处**（即此前"数组下标
噪声 ≈3,366"的主力——用户本地声明完全可判：`REAL*8 FSE(120,-3:2,5,5)`
+ `COMMON /re_stor/FSE,errtot` 等）、`atom_data.f` 343、`cmultip.f` 291、
`tripack.f` 288、`dlarfx.f` 220、`wk.f` 209。

**剩余 unresolved 原因分布（全语言，FAC after）**：`true_external` 11,320
（C stdlib/库函数为主）、`missing_target` 4,969、`intrinsic` 793、
`ambiguous_symbol` 71、`call_vs_array_ambiguous` 98（样本：`ABSC` 17 /
`d1mach` 15 / `IDAMAX` 8 等——真实调用或隐式数组，证据不足不猜）；
`parser_noise`/`other` 0；`edge_unresolved` 165、`callsite_no_src` 19。

**门与回归**：新增 `tests/test_eq0.py`（分类器 8 桶单测、fixed/free-form
数组消除、shape 记录、fixture 集成 + 不伪造断言），SQLite 全套
**138 pass / 0 fail / 1 skip**，**PG18.6 + pg_trgm 138 / 0 / 0**，
vitest 44/44、vue-tsc 0、`vite build` 绿，T0 7 语言 Gate 1 仍 1.000。

**EQ0 后新增 backlog**：tree-sitter-fortran 的 `do_loop` 体**未被遍历**
（既有行为，非本次引入）——DO 循环体内的调用/数组访问不可见，属
evidence 完整性缺口，随 FULL-EXPRESSION-COVERAGE backlog；FAC 无
`daxpy_()` 式调用（grep 0 处），cross_language 路径由 fixture 覆盖。

## P2 FLOW0 — Software Circuit MVP（SPEC-P2，Gate 1–10 全绿）

真实函数 → 带端口节点；真实 CALLS → control 走线；Proposed Block 写码 →
**Patch Preview → Apply → 工作树 → 重索引 → 新证据快照 → 绑定 canonical
符号 → Existing**；`Validate Against Code` = 最小软件 LVS（MATCH / STALE /
MISMATCH / UNBOUND）。领域模型 `SoftwareNetlist`
（FlowModel/Block/Port/Net/BlockBinding/FlowLayout），renderer-neutral
（X6 仅为 adapter，`X6 Node JSON ≠ domain model`，§2.2）。

- **入口**：Evidence Inspector「Open as Flow (Software Circuit)」；
  Architecture Inspector「Open Flow (Software Circuit)」；路由 `/flows/:id`。
- **真值边界**（冻结）：自动线只生成 `control`（调用投影），绝不自动声称
  `foo.result → bar.input`；写回失败（parse/symbol-not-found/ambiguous）
  **保持 proposed/modified 且绝不伪造 binding**（§13）。
- **门与回归**：FLOW0 主线 E2E（20 步，§23）**PASS（21.7–22.0s）**；走查脚本
  `web/walkthrough-flow0.mjs` OK（12 张截图）；后端 148/0/1skip（SQLite +
  嵌入 PG16；收集 149，skip=无 pg_trgm）；**PG18.6+pg_trgm 149/0/0**；T0 8 fixtures × 7 axes 全 1.000；
  vitest 45/45、vue-tsc 0、`vite build` 绿；非回归 E2E **S2/S3/S4/S5B/S6 全绿**
  （Fresh DB 协议 + `vite preview`；最终单批 5/5，47.6s）；**P1-DEBT-FIX0 已
  闭合**（A: TO-BE impact seeds 根因 = 选择域混合 → 画布/树选择平面互斥 +
  创建对话框禁止静默降级；B: 合成拖线定性为 harness debt，bounded-retry
  稳定化），S5B **18/18**，Gate 10 收敛全绿 —— 见 report §7.5。
- **文档**：[P2-FLOW0 Evidence Report](docs/acceptance/p2-flow0-evidence-report.md)
  · [Human Acceptance Runbook](docs/acceptance/p2-flow0-human-acceptance-runbook.md)
  · [浏览器走查脚本](web/walkthrough-flow0.mjs) · 截图
  `docs/acceptance/flow0-wt-01..12-*.png`、`s6-01..12-*.png`。
- **FLOW0 后停止**（§25）：不自动扩 P2。

## 已知限制（round 1，均已显式记录，不静默）

- 解析器是 tree-sitter 级，非编译器级（spec §42）。
- 名字解析是启发式（same-file → imported → included → global-unique），
  置信度 0.7–0.95 随规则记录；动态分发 / 指针分析不在范围内。
- Fortran free-form（`.f90/.f95/.f03/.f08`，T0 语言语义不变）+ fixed-form
  `.f/.for`（S6 保守归一化；`recomb.f` 的 `309 .` 数字风格 1 文件
  tree_error，已分桶记录）。
- TS 相对路径导入按文件映射 + 扩展名探测解析；bare specifier 记 external。
- BINDS_TO 等按文件解析的边在“目标文件变更”的增量场景下会丢失并记录
  （源码未变更则不重解析，这是 round-1 的取舍）。
- 大文件（>4MB）与二进制/生成物被跳过并在 telemetry 记录。

## 结果与可复现性

- `results/verify-*.jsonl`：每个 fixture 一行完整原始指标
  （node/edge TP/FP/FN、location、cross-file、parse、callsites、
  unresolved、duration、kind 直方图）——不是 aggregate only（spec §43.7）。
- `run_telemetry` 表：每次 run 的 phase/key/value。
- 测试：`tests/test_core.py` — fixture 正确性（≥0.95 全部轴）、增量一致性
  （节点数 +1、identity 稳定、diff 精确）、增量跨文件调用恢复、search/
  projection、evidence 元数据、unresolved 记录。
