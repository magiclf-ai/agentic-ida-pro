## 示例脚本（按需改造，避免整段照搬）
### 1) 读取函数名
```python
import idc
ea = 0x140001000
__result__ = idc.get_func_name(ea)
```

### 2) 查询 xrefs to
```python
import idc
import idautils
target = idc.get_name_ea_simple("sub_140001000")
rows = []
if target != idc.BADADDR:
    for x in idautils.XrefsTo(target):
        rows.append({"from": int(x.frm), "to": int(x.to), "type": int(x.type)})
__result__ = rows
```

### 3) 非破坏结构体成员新增
```python
import idc
sid = idc.get_struc_id("container_t")
if sid == idc.BADADDR:
    sid = idc.add_struc(-1, "container_t", 0)
rc = idc.add_struc_member(sid, "field_20", 0x20, idc.FF_DWORD, -1, 4)
__result__ = {"sid": int(sid), "rc": int(rc)}
```
