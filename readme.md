# Agentic IDA Pro

一个面向 **IDA Pro 9.3** 的 LLM 主导逆向分析平台，聚焦“关键函数分析 + 结构体恢复 + 证据链闭环”。

---

## 1. 项目定位

本项目不是“脚本硬编码控制器”，而是 **LLM 驱动的 tool-call loop**：

- 规划由 LLM 完成
- 执行由工具完成
- 状态由任务板与知识库驱动
- 输出为纯文本/Markdown（避免强格式约束）

核心场景：

- 入口与热点函数定位（`search` / `xref`）
- 关键函数伪代码证据采集（`decompile_function` / `inspect_variable_accesses`）
- 结构体创建与迭代（`create_structure`）
- 类型应用与重反编译验证（`set_identifier_type`）
- 任务闭环与证据链输出（`create_task` / `set_task_status` / `submit_output`）

---

## 2. 核心特性

- LLM 主导单循环：观察 -> 规划 -> 调工具 -> 取证 -> 更新任务/知识 -> 再决策
- 结构体恢复强约束：只允许通过 `create_structure` 建模
- 强制验证闭环：结构体创建后必须类型应用并重反编译
- 任务板原生支持：`todo/in_progress/blocked/done` 可追踪
- 会话可观测性：SQLite 持久化 turn/message/tool/event
- 运行验收报告：自动生成 before/after 结构体 diff 与 acceptance summary

---

## 3. 架构概览

```text
User Request
   |
   v
reverse_expert.py
   |
   v
StructRecoveryAgentCore (LLM policy loop)
   |                    \
   |                     \--> TaskBoard / WorkingKnowledge / ContextDistiller
   v
Tool Layer (search/xref/decompile/create_structure/set_identifier_type/...)
   |
   v
IDAClient (HTTP)
   |
   v
ida_service.daemon (IDA 进程内串行执行 IDAPython)
   |
   v
IDB / Hex-Rays / IDA APIs
```

---

## 4. 仓库结构

```text
.
├── src/
│   ├── agent/                     # Agent 核心（LLM loop、任务板、日志、提示词加载）
│   ├── ida_service/               # IDA HTTP 服务（在 IDA 进程内运行）
│   ├── ida_scripts/               # 可复用 IDAPython 模板
│   ├── prompts/                   # 系统提示词与子 Agent 提示词
│   ├── entrypoints/               # 启动入口、服务入口、验收入口
│   └── skills/                    # 技能定义（struct_recovery/function_analysis/string_decrypt）
├── frontend/observability/        # 可观测性前端 (Vue)
├── logs/                          # 会话日志、报告产物
└── test_binaries/                 # 示例二进制/IDB（含 suite_v2 复杂样例）
```

---

## 5. 环境要求

### 5.1 必需

- Python 3.10+（建议）
- IDA Pro 9.3（仅考虑 9.3）
- Hex-Rays 可用（用于反编译）
- 可访问的 OpenAI 兼容 API 服务

### 5.2 可选

- Node.js 18+（可观测性前端）
- WSL + Windows 双端协作（推荐）

---

## 6. 安装

```bash
cd /mnt/d/reverse/agentic_ida_pro
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

---

## 7. LLM 配置

项目强约束模型为 `gpt-5.2`（`reverse_expert.py` 与 `struct_recovery_agent.py` / `reverse_agent_core.py` 均会校验）。

```bash
export OPENAI_API_KEY='your-api-key-1'
export OPENAI_BASE_URL='http://192.168.72.1:8317/v1'
export OPENAI_MODEL='gpt-5.2'
```

---

## 8. 运行方式（发布版推荐）

### 8.1 一体化入口（推荐）

```bash
cd /mnt/d/reverse/agentic_ida_pro
OPENAI_API_KEY='your-api-key-1' \
OPENAI_BASE_URL='http://192.168.72.1:8317/v1' \
OPENAI_MODEL='gpt-5.2' \
PYTHONPATH=src .venv/bin/python reverse_agent.py \
    --input-path /abs/path/to/target.i64 \
    --request "分析关键函数并恢复结构体定义，给出证据链" \
    --agent-core dispatcher \
    --max-iterations 40
```

该入口会：

- 子进程启动 `ida_service.daemon`
- 调用 `/db/open` 打开目标二进制或 IDB
- 启动并等待 `reverse_expert.py`
- 结束时调用 `/db/close`（默认保存）并回收 service 进程

### 8.2 目录批量分析（异步并发）

```bash
cd /mnt/d/reverse/agentic_ida_pro
OPENAI_API_KEY='your-api-key-1' \
OPENAI_BASE_URL='http://192.168.72.1:8317/v1' \
OPENAI_MODEL='gpt-5.2' \
PYTHONPATH=src .venv/bin/python reverse_agent.py \
    --input-path /abs/path/to/samples \
    --recursive \
    --file-pattern '*.i64' \
    --file-pattern '*.exe' \
    --concurrency 3 \
    --ida-port 5000 \
    --request "批量分析并恢复关键结构体，输出证据链" \
    --agent-core dispatcher \
    --max-iterations 40
```

说明：

- 建议统一使用 `--input-path`，当该路径是目录时自动进入批量模式
- `--input-dir` 仍可用，但仅作为 `--input-path` 的兼容别名
- 批量模式下端口为动态分配（从 `--ida-port` 起寻找可用端口），避免并发冲突
- 建议端口区间预留充足，避免与本机其他服务冲突

### 8.3 仅启动 IDA Service（可选）

```bash
cd /mnt/d/reverse/agentic_ida_pro
bash src/entrypoints/run_ida_service.sh
```

若设置 `IDA_DEFAULT_INPUT_PATH`，service 启动时会自动打开；未设置时可后续调用 `/db/open`。

---

## 9. 关键脚本说明

### 9.1 `src/entrypoints/reverse_expert.py`

职责：

- 执行完整 Agent 循环
- 运行前备份 IDB（`/db/backup`）
- 运行前后抓取结构体快照并做 diff
- 输出 acceptance 报告与结构体定义产物

关键参数：

- `--request`：任务描述（必填）
- `--ida-url`：IDA service 地址，默认 `http://127.0.0.1:5000`
- `--max-iterations`：循环上限，默认 24
- `--agent-core`：Agent 入口（`struct_recovery` 或 `dispatcher`，默认 `struct_recovery`）
- `--idapython-kb-dir`：IDAPython 自修复知识库（可选）
- `--report-dir`：报告目录（默认 `logs/agent_reports`）

### 9.2 `reverse_agent.py`（根目录入口）

职责：

- 根路径统一入口，调用 `src/entrypoints/reverse_agent_service.py`
- 对外保持简洁启动命令，不暴露内部脚本层级

### 9.3 `src/entrypoints/reverse_agent_service.py`（内部实现）

职责：

- 统一入口：拉起 `ida_service` 子进程并等待健康检查
- 调用 `open_database`/`close_database` 动态开关 IDB
- 在当前进程直接调用 `reverse_expert` 能力（不再脚本子进程嵌套）

常用参数：

- `--input-path`：统一输入路径；文件=单目标模式，目录=批量模式
- `--input-dir`：兼容别名（建议改用 `--input-path`）
- `--concurrency`：批量并发 worker 数（每个 worker 启动一个独立 ida_service）
- `--file-pattern`：批量模式 glob 过滤（可重复）
- `--request`：逆向任务描述（必填）
- `--ida-host/--ida-port`：service 绑定地址
- `--no-save-on-exit`：退出时关闭数据库不保存

### 9.4 `src/entrypoints/run_ida_service.sh`（纯 Linux 方案）

如果只想单独启动 service，可直接：

```bash
export IDA_DEFAULT_INPUT_PATH=/abs/path/to/target.i64
bash src/entrypoints/run_ida_service.sh
```

---

## 10. Agent 工作流（结构体恢复主线）

标准闭环：

1. `search` / `xref` 缩小函数范围  
2. `decompile_function` 获取伪代码  
3. `inspect_variable_accesses` 获取变量访问与偏移证据  
4. `create_structure(name, c_decl)` 创建或更新结构体  
5. `set_identifier_type` 应用类型并重反编译  
6. 更新任务状态  
7. 重复直到任务板闭环，再 `submit_output`

---

## 11. 主要工具

- 检索：`search`, `xref`
- 证据采集：`decompile_function`, `inspect_variable_accesses`, `expand_call_path`
- 建模：`create_structure`
- 类型应用：`set_identifier_type`
- 深度补证：`run_idapython_task`
- 任务管理：`create_task`, `set_task_status`, `get_task_board`
- 最终提交：`submit_output`

---

## 12. 输出产物与日志

### 12.1 报告目录

默认输出到：

```text
logs/agent_reports/<session_id>_<timestamp>/
```

典型文件：

- `agent_final_output.txt`
- `before_structs.json`
- `after_structs.json`
- `struct_diff_summary.json`
- `struct_definitions_new_or_changed.txt`
- `acceptance_summary.md`
- `acceptance_summary.json`
- `idb_backup.json`
- `ida_snapshot_before.json`
- `ida_snapshot_after.json`

### 12.2 会话可观测性数据库

```text
logs/agent_sessions/agent_observability.sqlite3
```

记录：

- sessions
- turns
- messages
- turn_tools
- session_events

---

## 13. 验收机制（reverse_expert）

脚本会在结束时执行自动验收，典型失败条件：

- 未产生 `tool_call`
- 未产生有效 mutation
- before/after 结构体无变化
- 运行中断或异常

这能避免“只分析不落地”的空跑结果。

---

## 14. 可观测性 UI（可选）

一键启动前后端：

```bash
./start_observability.sh
```

默认：

- 前端：`http://127.0.0.1:5173`
- 后端：`http://127.0.0.1:8765`

前端目录：`frontend/observability`  
后端入口：`src/entrypoints/logs.py`

---

## 15. IDA Service API

核心端点：

- `GET /health`
- `POST /db/open`
- `POST /db/close`
- `GET /db/info`
- `POST /db/backup`
- `POST /execute`
- `GET /functions`
- `POST /decompile`
- `POST /search`
- `POST /xrefs`

快速烟测：

```bash
curl -fsS http://127.0.0.1:5000/health
curl -fsS http://127.0.0.1:5000/db/info
```

---

## 16. 示例：结构体恢复任务（基于提供日志）

请求：

```text
分析关键函数并恢复结构体定义，给出证据链
```

日志结论：

- 任务板 `total=4, done=4, blocked=0`
- 成功恢复并应用验证 `struct container` (0x28)
- 成功恢复并应用验证 `struct node` (0x38)
- 成功恢复并应用验证 `struct field_desc` (0x38)
- 在 `main/sub_1780/sub_15F0/sub_16C0` 中重反编译后字段可读性收敛
- 最终 `submit_output` 成功

---

## 17. 常见问题（FAQ）

### Q1: `Unsupported model` 错误

原因：模型不是 `gpt-5.2`。  
处理：设置 `OPENAI_MODEL=gpt-5.2`。

### Q2: `Missing OPENAI_API_KEY`

原因：未配置 API Key。  
处理：导出 `OPENAI_API_KEY` 环境变量。

### Q3: IDA service 返回 `Database not opened`

原因：当前未打开任何 IDB。  
处理：调用 `POST /db/open`，或在一体化入口中通过 `--input-path` 自动打开。

### Q4: 如何切换到另一个 IDB

处理：调用 `POST /db/open` 并传新路径，服务会默认保存关闭当前数据库后再打开新数据库。

### Q5: 打开目标前为何会删除 `.id0/.id1/.id2/.nam/.til`

处理：这是默认清理逻辑。每次 `open` 前会清理目标目录下这些未打包数据库碎片文件，避免旧文件干扰当前分析数据库。

### Q6: 前端启动失败（npm）

处理：

```bash
cd frontend/observability
npm install
npm run dev -- --host 0.0.0.0 --port 5173
```

---

## 18. 开发原则（项目约束）

- Agent 以 LLM 为主导，Python 侧保持轻控制逻辑
- 输入输出以纯文本为主，不依赖 pydantic/json 强结构输出
- 结构体恢复仅使用 `create_structure` 做落地建模
- IDAPython 兼容目标固定为 IDA Pro 9.3

---

## 19. 复杂逆向样例（suite_v2）

新增复杂数据流样例目录：

```text
test_binaries/suite_v2
├── src/              # 6 个 C/C++ 样例源码
├── bin/              # 编译产物（dbg + strip）
├── Makefile
└── README.md
```

构建（禁优化，保留跨函数逻辑）：

```bash
cd test_binaries/suite_v2
make all
```

每个样例都会产出：

- `*_dbg`（带符号）
- `*_strip`（strip 后）

---


抛砖引玉，在这个 AI 时代，希望找些同路人一起探索 !



<img src="images/qq.jpg" alt="alt text" width="200">
