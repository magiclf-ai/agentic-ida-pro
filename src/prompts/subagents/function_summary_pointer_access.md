{% set summary_kind = "pointer_access_summary" %}
{% include "subagents/function_summary_agent.md" %}

## 派生任务约束（Pointer Access）
你当前只做“参数指针访问摘要”，不要扩展到完整行为摘要。

### 必做内容
1) 先识别候选变量（优先参数指针，其次别名变量）。
2) 对候选变量调用 `inspect_variable_accesses(function_name, variable_names)`。
3) 若参数指针传入子函数，递归创建同 profile 子任务继续分析。
4) 汇总当前函数 + 子函数的指针访问信息，输出偏移/类型/大小。

### 输出格式（Markdown）
- `summary`：一句话总结当前递归树的指针访问收敛情况。
- `findings` 必须包含以下小节：
  - `Current Function Pointer Accesses`
  - `Recursive Callee Pointer Accesses`
  - `Merged Pointer Access Matrix`

`Merged Pointer Access Matrix` 每行至少给出：
`variable | function | expression | relative_offset | inferred_type | access_size | access_kind | source`

### 禁止事项
1) 禁止直接下结构体定型结论（只做访问摘要）。
2) 禁止跳过子函数传播证据。
3) 禁止缺失偏移/类型/大小三元组后直接提交。
