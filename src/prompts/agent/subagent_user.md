## Parent Request
{{ user_request }}

## Subagent Task
{{ task }}

## Provided Context
{{ context }}

要求：
- 先给出可执行计划，再按需调用工具。
- 仅输出纯文本证据与结论。
- 任务完成时调用 `submit_subagent_output(summary, findings)`。
