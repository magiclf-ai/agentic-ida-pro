
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
- 增加 IDA 静态分析能力时，直接修改 ida service 的代码，而不是先 idapython 脚本，然后让 ida client 调用 ida.execute_script

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

执行时使用下面 venv 中的 python
```
/mnt/d/reverse/agentic_ida_pro/.venv/bin/python
```

## IDA Service 运行与验证（标准步骤）

后续联调统一按以下流程，避免环境偏差：

1) 进入项目根目录并激活解释器约束
```bash
cd /mnt/d/reverse/agentic_ida_pro
export PYTHONPATH=src
```

2) 启动 ida_service（示例端口 5000）
```bash
PYTHONPATH=src /mnt/d/reverse/agentic_ida_pro/.venv/bin/python -u -m ida_service.daemon \
  --host 127.0.0.1 --port 5000 --log-dir logs
```

3) 健康检查
```bash
curl -fsS http://127.0.0.1:5000/health
```

4) API smoke test（以 `test_binaries/complex_test.i64` 为例）
- 说明：使用 `create_structure(..., struct_comment=...)` + `set_function_comment(...)` 验证注释链路。
- 说明：测试脚本放 `/tmp`，执行后立即删除。

```bash
cat > /tmp/ida_api_smoke_test.py <<'PY'
from __future__ import annotations
import json, os, sys
from pathlib import Path

ROOT = Path('/mnt/d/reverse/agentic_ida_pro').resolve()
sys.path.insert(0, str(ROOT / 'src'))
from agent.ida_client import IDAClient

target = str((ROOT / 'test_binaries' / 'complex_test.i64').resolve())
client = IDAClient(base_url='http://127.0.0.1:5000', timeout=600)

health = client.health_check()
open_ret = client.open_database(input_path=target, run_auto_analysis=True, save_current=False)
funcs = client.list_functions()
func_name = 'main' if any(str(x.get('name','')) == 'main' for x in funcs) else str(funcs[0]['name'])
pseudo = client.decompile_function(function_name=func_name)

struct_ret = client.create_structure_detailed(
    name='api_smoke_alias_mesh_elem',
    c_decl='typedef struct api_smoke_alias_mesh_elem { uint32_t kind; uint32_t value; uint64_t offset; uint32_t scale; uint32_t _pad14; } api_smoke_alias_mesh_elem;',
    struct_comment='分析成功\\n改动摘要: API smoke test\\n结构体作用: 测试结构体注释链路'
)
func_cmt_ret = client.set_function_comment(
    function_name=func_name,
    analysis_status='分析成功',
    change_summary='API smoke test 写入函数头注释',
    function_summary='验证 set_function_comment 接口链路',
    repeatable=True,
)
close_ret = client.close_database(save=False)

print(json.dumps({
    'ok': True,
    'checks': {
        'health_status': health.get('status'),
        'open_database_success': bool(open_ret.get('success', True)),
        'decompile_non_empty': bool(str(pseudo or '').strip()),
        'create_structure_success': bool(struct_ret.get('success', False)),
        'struct_comment_apply_ok': bool(struct_ret.get('comment_apply_ok', False)),
        'set_function_comment_success': bool(func_cmt_ret.get('success', False)),
        'close_database_success': bool(close_ret.get('success', True)),
    },
    'details': {
        'function': func_name,
        'create_structure_error': struct_ret.get('error', ''),
        'set_function_comment_error': func_cmt_ret.get('error', ''),
    }
}, ensure_ascii=False, indent=2))
PY

PYTHONPATH=src /mnt/d/reverse/agentic_ida_pro/.venv/bin/python /tmp/ida_api_smoke_test.py
/mnt/d/reverse/agentic_ida_pro/.venv/bin/python - <<'PY'
import os
p='/tmp/ida_api_smoke_test.py'
if os.path.exists(p):
    os.remove(p)
print('deleted', p)
PY
```

5) 期望结果
- `health_status=ok`
- `create_structure_success=true`
- `struct_comment_apply_ok=true`
- `set_function_comment_success=true`
- `close_database_success=true`

6) 常见坑
- 必须带 `PYTHONPATH=src`，否则 `-m ida_service.daemon` 可能找不到模块。
- 函数注释接口名称是 `set_function_comment`（不是 `annotate_function_comment`）。
- 临时测试脚本必须删除，不保留在仓库中。


## 文档

- 开发前生成的 plan, 开发文档 保存到 reference/docs 目录下, 不要存放到根路径
- 分析报告、文档存放 reference/docs 
