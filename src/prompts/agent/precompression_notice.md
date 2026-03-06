## Pre-Compression Reasoning @ iteration {{ iteration }}
- reason: {{ reason }}
- policy_messages: {{ policy_messages }}
- policy_tokens_est: {{ policy_tokens_est }}

你必须先基于当前证据给出中间结论，然后调用 `prune_context_messages` 选择删除/折叠消息。
工具参数必须引用消息ID（Message_xxx）。
禁止折叠 system prompt 与首条 user prompt。

请输出以下结构：
## 与当前分析目标匹配的语义理解蒸馏
### 中间结论
### 是否需要继续获取信息（是/否）
### 最小补充信息集
## LLM 驱动裁切上下文

随后必须调用：
- `prune_context_messages(remove_message_ids='...', fold_message_ids='...', reason='...')`

## 可裁切消息索引
{{ prunable_rows_md }}
