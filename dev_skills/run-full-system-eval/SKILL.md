---
name: run-full-system-eval
description: 在用户要求跑整系统评测、suite、多 case 回归、比较修改前后结果，或需要按 verdict 决策继续/停下修 bug 时使用。
user-invocable: true
tags: [eval, regression, suite, multi-case]
---

# run-full-system-eval

## 触发条件

- 用户要跑完整评测、回归集、suite 或多 case
- 用户要比较修改前后的整系统结果

## 核心原则

代码只提供单 case 执行原语，多 case 编排由你控制。
你可以在中途检查失败、修 bug、再继续，而不是盲目跑完所有 case。

## 命令入口

```bash
cd /mnt/d/reverse/agentic_ida_pro
export PYTHONPATH=src
PYTHON=/mnt/d/reverse/agentic_ida_pro/.venv/bin/python

# 发现阶段
$PYTHON -u src/entrypoints/eval_runner.py --list-suites
$PYTHON -u src/entrypoints/eval_runner.py --list-cases
$PYTHON -u src/entrypoints/eval_runner.py --case-info <case_id>

# 执行单 case
$PYTHON -u src/entrypoints/eval_runner.py --case <case_id>
# 输出: CASE_DONE case_id=xxx verdict=xxx run_exit_code=xxx run_id=xxx case_dir=xxx

# 查询状态
$PYTHON -u src/entrypoints/eval_runner.py --status <run_dir_or_run_id>

# 停止运行中的 case
$PYTHON -u src/entrypoints/eval_runner.py --stop <run_dir_or_run_id>

# 对已有 run 重新 judge
$PYTHON -u src/entrypoints/eval_runner.py --judge-only <run_dir>

# 保存基线
$PYTHON -u src/entrypoints/eval_runner.py --case <case_id> --save-baseline <label>
```

## 编排流程

### 1. 发现阶段

用 `--list-suites` 确定要跑哪个 suite，用 `--list-cases` 查看所有 case 的 profile、iterations、timeout。

### 2. 逐 case 执行

对 suite 中的每个 `case_id`，调用 `--case <case_id>`，等待 `CASE_DONE` 输出行，读取 verdict。

### 3. verdict 决策

每个 case 完成后根据 verdict 决定下一步：

- `pass`：继续下一个 case
- `fail` / `partial`，但 `run_exit_code=0`：质量问题，记录后继续
- `infra_fail` 或 `run_exit_code!=0`：代码 bug，立即停下修复

### 4. 代码 bug 修复流程

当遇到 `infra_fail` 或 `run_exit_code!=0`：

1. 读 `<case_dir>/verdict.md` 了解失败概况
2. 读 `<case_dir>/.eval_state/stderr.log` 查看 traceback
3. 读 `<case_dir>/.eval_state/watch.log` 查看运行时日志
4. 定位并修复代码 bug
5. 重跑该 case：`--case <case_id>`
6. 确认 verdict 不再是 `infra_fail` 后继续

### 5. 并行策略

- 不同 profile 的 case 可以并行，各自独立 IDA 实例
- 同 profile 的 case 必须串行，共享 IDA 实例

### 6. 结果汇总

所有 case 完成后，汇总各 case 的 verdict，输出 summary 表格。

## 查看顺序

1. `CASE_DONE` 输出行的 verdict 和 `run_exit_code`
2. `<case_dir>/verdict.md`
3. `<case_dir>/evidence.md`
4. `<case_dir>/run_trace.md`
5. `<case_dir>/.eval_state/stderr.log`
6. `<case_dir>/.eval_state/watch.log`
