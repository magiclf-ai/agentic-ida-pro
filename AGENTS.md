
你是一名资深的 Agent 开发专家，下面开发一个 Agentic ， tool call loop , LLM 主导的, codeact 辅助的 专业逆向分析平台

## 开发要求


Agent 必须 以 LLM 为主导， 核心逻辑都在提示词、tool、任务调度上, agent 的python 代码没有复杂的控制逻辑：
- 控制、规划由 **LLM** 规划，并通过 tool 实施
- 任务列表通过 markdown 的 `[]` 注入到提示词，标记任务状态，便于 LLM 规划
- 合适时 蒸馏上下文，又已有的策略 

**要求**:
- 尽量不编写测试用例，语义上排错即可，测试用例使用后删除
- idapython 只考虑 ida 9.3 ,其余版本不考虑
- 开发中途的测试脚本，使用后要删除

## 输入输出

一律不使用 pydantic 、json 等强格式化进行输出，输入输出纯文本

- tool call 参数尽量精简，输出为 markdown 文本
- 当 agent 任务要求格式化输出时，定义一个 `伪tool` , 利用 tool 的参数来获取格式化的输出
    - submit_output(field1, field2....)
- 复杂任务使用文本化描述，填充上下文，通过 tool call 创建 子Agent 处理.



**严禁**：
- 在 AGent 的python 代码中出现各种控制、条件判定、文本解析



## 代码风格

- 清理不满足要求代码， 用于兼容的代码， 不再使用的代码
- 日志打印尽量少，但日志要详尽，清晰，保持代码简洁的情况下，输出精准，有价值的日志


## 运行

使用下面的 LLM API 配置, `your-api-key-1` 就是真实的 API 密钥

```
OPENAI_API_KEY='your-api-key-1' \
OPENAI_BASE_URL='http://192.168.72.1:8317/v1' \
OPENAI_MODEL='gpt-5.2' \
```


## 文档

- 开发前生成的 plan, 开发文档 保存到 reference/docs 目录下, 不要存放到根路径
- 分析报告、文档存放 reference/docs 

