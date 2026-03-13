# 2026-03-13 Full Eval Bugfix Implementation

## Summary

本轮先修通了评测基础设施，再回归代表性 case。核心结果：

- 修复 `reverse_expert` 在 pre-backup / snapshot 阶段把 `ida_service` 打死导致的 `infra_fail`
- 为短预算 case 增加 LLM 级预算提示与最后一轮强制 finalize 保底
- `preflight_struct_complex_test` 从 `infra_fail` 提升到 `pass`
- `preflight_reverse_complex_test` 从 `partial` 提升到 `pass`
- `attack_complex_test` / `reverse_complex_test` / `struct_complex_test` 均 `pass`

## Root Causes

1. `reverse_expert` 把 pre-backup 当成硬前置，失败直接 `return 2`
2. `ida_service` 的 `/db/backup` 在当前 IDA 运行环境下会导致进程异常断开
3. `take_database_snapshot` 也会触发 daemon 断开，导致 report artifacts 缺失
4. `preflight` 只有 2 轮，但 prompt 没有预算感知，最后一轮仍可能扩任务而不 submit
5. `dev_run_watch` 在存在 `run_id` 但拿不到精确 session 时会回落到历史 session 猜测，容易串旧 run

## Code Changes

1. `src/clients/ida_client.py`
   - 为本地 ida_service 请求固定使用 `requests.Session(trust_env=False)`
   - `backup_database()` 改为基于当前 IDB 路径的本地 `shutil.copy2` 备份，不再走危险的 `/db/backup`

2. `src/entrypoints/reverse_expert.py`
   - pre-backup 改为 warning，不再阻断主流程
   - 去掉 before/after snapshot 的硬依赖
   - 证据采集改为基于当前打开 DB 的稳定反编译结果，而不是 snapshot diff

3. `src/entrypoints/dev_run_watch.py`
   - `ida_service /status` 查询改为无代理直连
   - 当已有 `run_id` 时，仅接受 `watch_run_id` 精确匹配的 session，禁止回退到历史 session 猜测

4. `src/runtime/reverse_runtime_core.py`
   - 新增 iteration budget 提示，在短预算和最后两轮时明确要求收缩任务并优先 submit
   - 新增 forced finalize 保底：最后一轮结束仍未 submit 时，追加一次仅允许 finalize tool 的 LLM 收尾

5. `src/entrypoints/reverse_expert.py`
   - 基于 `pre_recovery_backup` / `post_recovery_backup` 重新采集同一批函数的 before/after 反编译结果
   - 新增 LLM 生成的 `Pseudocode Diff Summary`，输出“修改概述 / 函数级变化 / 剩余缺口”
   - 对比提示收紧为“只做差异概述”，禁止补写结构体定义和代码块

## Test Results

### Preflight

- `preflight_struct_complex_test`
  - 初始：`infra_fail`, `run_exit_code=2`
  - 修复后：`pass`, `run_exit_code=0`
  - run_id: `eval_20260313_091454_921874`

- `preflight_attack_complex_test`
  - 初始：`infra_fail`, `run_exit_code=2`
  - 中间：`fail`, `run_exit_code=0`（预算提示后仍未 finalize）
  - 修复后：`partial`, `run_exit_code=0`
  - run_id: `eval_20260313_091016_349409`

- `preflight_reverse_complex_test`
  - 初始：`infra_fail`, `run_exit_code=2`
  - 中间：`partial`, `run_exit_code=0`
  - 修复后：`pass`, `run_exit_code=0`
  - run_id: `eval_20260313_091236_718363`

### Representative Full Cases

- `attack_complex_test`: `pass`, `run_exit_code=0`
  - run_id: `eval_20260313_091648_837444`

- `reverse_complex_test`: `pass`, `run_exit_code=0`
  - run_id: `eval_20260313_092126_263205`

- `struct_complex_test`: `pass`, `run_exit_code=0`
  - run_id: `eval_20260313_093032_020649`

### IDA API Smoke

基于临时 `/tmp` 脚本验证以下链路全部成功：

- `health_check`
- `open_database`
- `decompile_function`
- `create_structure_detailed` + `struct_comment`
- `set_function_comment`
- `close_database`

期望项全部为 `true`。

## Remaining Risks

1. `preflight_attack_complex_test` 仍是 `partial`
   - 说明 2 轮预算下，attack profile 的最终摘要仍偏保守
   - 但对应正式 case `attack_complex_test` 已 `pass`

2. 本轮没有串行跑完 `full` suite 的 11 个 case
   - 已覆盖 preflight 全量 + 3 个代表性正式 case + ida_service API smoke
   - 若继续扩展，建议下一步优先跑：
     - `struct_c_tagged_union_fsm`
     - `struct_c_multi_phase_builder`
     - `struct_cpp_pimpl_bridge`
     - `reverse_cpp_multi_inherit_cast`

3. `reverse_expert.py` 中旧的 snapshot 相关 helper 仍保留，但当前不再走主路径
   - 后续若要彻底清理，可在确认没有其他入口依赖后删除

4. `complex_test` 相关 case 目前仍使用 `test_binaries/complex_test.i64`
   - 原因：尝试直接切到原始二进制 `test_binaries/complex_test` 时，`ida_service` 在 `db_open` 阶段会异常断开
   - 结论：当前 before/after 对比是“当前 `.i64` 基线的前后变化”，不是“从全新未分析二进制开始”的绝对净新增量

## Addendum: Fresh-Binary Eval Rework

本轮已把上面的第 4 点改掉。当前评测链路不再依赖仓库内历史 `.i64` 作为 `complex_test` 的输入，而是统一执行：

1. 拷贝原始二进制到 case 隔离目录
2. 用本机 IDA 9.3 `idat -B -o ...` 从该拷贝重新生成新的 `.i64`
3. 后续 agent / before-after 对比只打开这份新生成的 `.i64`

### Code Changes

1. `src/entrypoints/eval_runner.py`
   - `complex_test` 与 suite_v2 原始样本都会先复制到 case 私有目录
   - 若输入不是 `.i64/.idb/.i32`，自动在隔离目录里生成 fresh `.i64`
   - `HOME` 指向 case 私有 `idat_home`，并复制最小 `.idapro` 配置，避免历史分析状态和 sandbox 家目录写入问题
   - 生成结果写入 `.eval_state/prepared_input.json` 与 `idat_import.log`

2. `src/evaluation/cases.py`
   - `preflight_*_complex_test`、`attack_complex_test`、`reverse_complex_test`、`struct_complex_test` 的 `input_path` 已切回原始 `test_binaries/complex_test`
   - `preflight_struct_complex_test` 的预算已从 `2` 提高到 `6`，给 fresh binary 下的“取证 -> 建模 -> 应用 -> 验证”最小闭环留出空间

3. `src/entrypoints/reverse_expert.py`
   - 评测 case 的 `case_id` / `case_spec` / `evidence_functions` 现在会直接注入给主请求
   - 让短预算 case 不再只拿到一行泛化 request

4. `src/runtime/reverse_runtime_core.py`
   - `short_budget` / `finalize_window` 规则进一步收紧
   - 已知关键函数时优先 `decompile_function` / `inspect_variable_accesses`
   - 已成功 `create_structure` 后，下一轮必须优先 `set_identifier_type`，禁止无意义重复建同名结构体

5. `src/ida_scripts/create_structure.py`
   - `set_named_type` 成功后会额外尝试 `import_type` 同步到 IDB Structures
   - 同时尝试 `Node` 与 `struct Node` 两类名称解析
   - 对函数指针 typedef、裸 typedef 指针字段等 LLM 常见声明形式增加降级重试

6. `src/ida_scripts/set_identifier_type.py`
   - 当 `Node *` / `Container *` 解析失败时，会自动重试 `struct Node *` / `struct Container *`

### Fresh-Binary Validation

1. `preflight_struct_complex_test`
   - fresh-IDB 生成已确认：`logs/eval_runs/eval_20260313_113140_784827/preflight_struct_complex_test/.eval_state/prepared_input.json`
   - `idat` 导入日志已确认：`logs/eval_runs/eval_20260313_113140_784827/preflight_struct_complex_test/.eval_state/idat_import.log`
   - 最新代表性结果：多次回归后已稳定证明“输入来自 fresh binary”，且 `field_desc` / `Node` / `Container` 结构体可以在不同 run 中落地创建
   - 但 verdict 仍在 `fail/partial` 间波动，主要剩余问题不是 infra，而是 LLM 在最后几轮未稳定完成 `set_identifier_type` + after 重反编译闭环

2. `reverse_cpp_multi_inherit_cast`
   - `pass`, `run_exit_code=0`
   - run_id: `eval_20260313_113416_725978`
   - 证明 suite_v2 原始二进制也能走 fresh-import 链路稳定跑通

### Current Conclusion

1. “测评时拷贝二进制，再重新分析二进制”的基础设施已经落地并验证成功
2. before/after 无变化的问题已被重新归因：
   - 旧原因：历史 `.i64` 污染
   - 新状态：`complex_test` 已不再吃历史 `.i64`
   - 剩余原因：`struct_recovery` 在短预算下仍不稳定完成类型应用闭环
3. 当前最大的剩余问题是质量层而不是 infra：
   - `create_structure` / `set_identifier_type` 工具链比之前更稳
   - 但 `preflight_struct_complex_test` 仍未稳定达到“>=3 个关键函数 after 出现成员访问收敛”的验收标准
