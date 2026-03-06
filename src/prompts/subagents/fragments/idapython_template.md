## IDAPython 模板（提交脚本时优先沿用）
```python
import idc
import idautils

# 可选：按需导入
# import ida_hexrays
# import ida_typeinf

def main():
    # 你的最小动作
    return {"ok": True}

__result__ = main()
```

建议：
- 只导入当前脚本实际需要的模块。
- 关键输出放在 `__result__`，便于稳定回传。
