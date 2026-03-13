---
name: live-trace-triage
description: 在运行中的 run 疑似卡住、tool 连续失败、ida_service 超时或无响应，需要快速判断是 prompt、tool、runtime 还是 ida_service 问题时使用。
user-invocable: true
tags: [trace, triage, runtime, ida-service]
---

# live-trace-triage

## 触发条件

- 运行中的 run 看起来卡住
- tool 连续失败
- ida_service 没响应或疑似超时
- 需要决定继续跑还是先停下来归因

## 使用步骤

1. 先用 `dev_run_watch.py --status` 看 `HEARTBEAT / TURN_SUMMARY / TOOL_BATCH / IDA_STATUS`
2. 再用 `dev_run_watch.py --tail` 看最近事件顺序，确认是否还在推进
3. 若有 `session_id`，再看 `/api/sessions/<id>/summary` 和 `/api/events`
4. 若 `IDA_STATUS.service_alive=false` 或 `timeout_count` 增长，优先看 `service.log`
5. 做归因时只回答四类之一：
   - prompt 规划问题
   - tool 边界或参数问题
   - runtime/watch 调度问题
   - ida_service 执行问题
6. 最后明确建议：
   - 继续观察
   - 优雅停止
   - 先修代码再重跑

## 命令入口

```bash
cd /mnt/d/reverse/agentic_ida_pro
export PYTHONPATH=src

/mnt/d/reverse/agentic_ida_pro/.venv/bin/python -u src/entrypoints/dev_run_watch.py \
  --status <run_id>

/mnt/d/reverse/agentic_ida_pro/.venv/bin/python -u src/entrypoints/dev_run_watch.py \
  --tail <run_id> --lines 20

curl -fsS "http://127.0.0.1:8765/api/sessions/<session_id>/summary"
curl -fsS "http://127.0.0.1:8765/api/events?session_id=<session_id>&limit=20"
```

## 关键判断

- 最近事件一直只有 `llm_request_sent` 而没有 `llm_response_received`：优先怀疑 LLM/API
- `turn_completed` 连续出现但 `effective_mutation_count=0`：优先怀疑 prompt 空转或工具选择错误
- `service_alive=true` 但 `is_executing=true` 持续很久且 `last_event=execute_start`：优先怀疑 ida_service 卡执行
- `run_finished` 但 exit code 非 0：直接回看 `stderr.log`、`run_trace.md`、`evidence.md`
