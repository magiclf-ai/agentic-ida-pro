## Tool 说明边界（强制）
1) `@tool` 函数 docstring 只描述定义层信息：功能、`Args`、`Returns`。
2) tool 的使用建议、适用场景、推荐场景、使用方式、示例一律写在 system prompt。
3) 发生冲突时：参数与返回语义以 docstring 为准；调用策略以当前 prompt 为准。

