# Source Code Structure

本项目采用清晰的模块化架构，各目录职责明确：

## 目录说明

### `agent/` - Agent 实现
仅包含具体的 Agent 实现类：
- `idapython_agent.py` - IDAPython 任务执行 Agent
- `struct_recovery_agent.py` - 结构体恢复 Agent
- `reverse_agent_core.py` - 逆向分析主 Agent

### `runtime/` - 运行时核心与管理器
运行时核心、各种管理器和上下文处理：
- `reverse_runtime_core.py` - 通用逆向分析运行时核心
- `subagent_runtime.py` - 子 Agent 运行时
- `policy_manager.py` - 策略消息历史管理
- `prompt_manager.py` - Prompt 模板管理
- `knowledge_manager.py` - 工作知识管理
- `subagent_manager.py` - 子 Agent 生命周期管理
- `tool_registry.py` - 工具注册表
- `context_distiller.py` - 上下文压缩

### `core/` - 核心工具、模型和基础设施
数据模型、工具定义、通用工具和基础设施：
- `models.py` - 数据模型定义
- `tools.py` - LangChain 工具定义
- `utils.py` - 通用工具函数
- `task_board.py` - 任务看板
- `session_logger.py` - 会话日志记录
- `observability.py` - 可观测性中心
- `idapython_kb.py` - IDAPython 知识库

### `clients/` - 外部服务客户端
与外部服务交互的客户端：
- `ida_client.py` - IDA Pro 服务客户端

### `entrypoints/` - 入口点
应用程序入口点和服务：
- `reverse_expert.py` - 逆向专家入口
- `reverse_agent_service.py` - Agent 服务入口
- `observability_api.py` - 可观测性 API
- `observability_stack.py` - 可观测性栈

### `ida_scripts/` - IDA 脚本
在 IDA Pro 中执行的脚本：
- 各种 IDA 操作脚本（创建结构体、设置类型、获取交叉引用等）
- `skills/` - 高级技能脚本

### `ida_service/` - IDA 服务
IDA Pro 服务后端：
- `daemon.py` - 服务守护进程
- `executor.py` - 脚本执行器
- `search_core.py` - 搜索核心

### `prompts/` - Prompt 模板
LLM Prompt 模板：
- `agent/` - Agent 系统 Prompt
- `subagents/` - 子 Agent Prompt
- `distiller/` - 上下文压缩 Prompt
- `fragments/` - Prompt 片段

### `skills/` - 技能定义
高级分析技能：
- `function_analysis/` - 函数分析
- `string_decrypt/` - 字符串解密
- `struct_recovery/` - 结构体恢复

## 导入规范

### Agent 模块
```python
from agent import ReverseAgentCore, IDAPythonTaskAgent
from agent import StructRecoveryAgentCore
```

### Runtime 模块
```python
from runtime import ReverseRuntimeCore, PolicyManager
from runtime import SubAgentRuntime, PromptManager
```

### Core 模块
```python
from core import models, tools, utils
from core import TaskBoard, AgentSessionLogger
```

### Clients 模块
```python
from clients import IDAClient
```

## 设计原则

1. **单一职责** - 每个目录有明确的职责边界
2. **低耦合** - 模块间依赖关系清晰
3. **易扩展** - 添加新 Agent、Client 或工具都很容易
4. **易理解** - 目录结构直观，便于新人快速上手
