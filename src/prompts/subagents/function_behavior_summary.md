{% set profile_name = profile_name | default("function_behavior_summary") %}
{% set summary_kind = "function_behavior_summary" %}
{% include "subagents/function_summary_agent.md" %}

## 派生任务约束（Behavior Summary）
你当前只做“函数行为摘要”，重点是功能、参数、调用关系和安全含义，不要扩展成整库漫游。

### 必做内容
1) 先用 `decompile_function` 建立函数伪代码上下文。
2) 对关键参数、局部变量或状态对象调用 `inspect_variable_accesses(function_name, variable_names)`。
3) 使用 `xref` 补充调用者与被调用者关系，避免只看当前函数局部代码。
4) 若关键参数或关键对象继续传入子函数，递归创建同 profile 子任务并汇总结果。
5) 给出一句话功能总结，并指出关键操作类型。

### 重点分析维度
1) 函数功能
   - 它做什么
   - 它在模块中的角色
2) 参数与返回值
   - 参数名称、用途、是否外部可控
   - 返回值表示成功/失败/状态码/对象指针等什么语义
3) 调用关系
   - 谁调用它
   - 它调用了哪些关键函数
4) 安全注释
   - 是否处理外部输入
   - 是否有长度、边界、状态、认证、资源访问相关风险
   - 若证据不足，只写“未见明确证据”，不要编造漏洞

### 输出格式（Markdown）
- `summary`：一句话概括函数职责和风险级别。
- `findings` 必须包含以下小节：
  - `Function`
  - `Behavior Summary`
  - `Parameters And Return`
  - `Call Relationships`
  - `Security Notes`
  - `Recursive Callee Notes`

推荐条目格式：
- `Function`: `sub_140011230 @ 0x140011230`
- `Behavior Summary`: 校验配置项并决定是否启用远程更新
- `Parameters And Return`
  - `a1`: 配置对象指针，提供更新地址和开关位
  - `a2`: 状态标志，决定是否强制刷新
  - 返回值：`bool`，表示是否允许进入更新流程
- `Call Relationships`
  - 调用者：`main_init`、`reload_config`
  - 被调用者：`parse_config_value`、`check_signature`
- `Security Notes`
  - 外部输入：配置文件内容进入 `a1`
  - 风险：若签名校验失败路径可被绕过，则可能放行未验证更新
  - 当前证据：`decompile_function` 显示失败时仍可在 `force` 标志下继续执行
- `Recursive Callee Notes`
  - `check_signature` 子任务结果显示其返回值直接参与分支判断

### 禁止事项
1) 禁止只复述伪代码，不提炼函数职责。
2) 禁止没有调用关系证据就下“核心函数”结论。
3) 禁止把低置信度猜测写成确定事实。
