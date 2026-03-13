# 2026-03-13 Full Eval Bugfix Plan

## Goal

执行当前仓库的完整评测链路，分析运行日志与裁决结果，修复导致 `infra_fail`、非零退出或关键能力退化的问题。

## Scope

1. 预检 `eval_runner.py`、`dev_run_watch.py`、case registry 与 IDA service 健康状态。
2. 先跑 `preflight`，再跑 `full` suite。
3. 对失败 case 读取 `verdict.md`、`evidence.md`、`run_trace.md`、`.eval_state/*.log`。
4. 直接修改运行时、prompt、entrypoint 或 `ida_service` 代码完成修复。
5. 回归失败 case，确认不再出现相同问题。
6. 产出实施记录到 `reference/docs`。

## Execution Rules

- 遵循 `run-full-system-eval` skill：按 case 串行推进，遇到 `infra_fail` 或 `run_exit_code != 0` 立即停下修复。
- 保持 LLM 主导，不在 Python 中增加复杂控制与文本解析逻辑。
- 临时测试脚本仅放 `/tmp`，用后删除。
- 不回滚仓库中与本次任务无关的现有变更。

## Checklist

- [ ] 记录 suite/case 范围
- [ ] 确认 IDA service 可启动且健康检查通过
- [ ] 跑通 `preflight`
- [ ] 跑通 `full`
- [ ] 修复失败点并回归
- [ ] 输出实施文档
