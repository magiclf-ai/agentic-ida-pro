"""
创建结构体模板

根据收集的字段信息创建结构体定义。
"""
import idc

def create_struct(name, fields):
    """
    创建结构体
    
    Args:
        name: 结构体名称
        fields: 字段列表，每个字段包含 name, offset, size, type
    
    Returns:
        结构体 ID
    """
    try:
        sid = idc.get_struc_id(name)
        if sid == idc.BADADDR:
            sid = idc.add_struc(-1, name, 0)
            if sid == idc.BADADDR:
                return {"error": f"Failed to create structure: {name}"}

        for field in fields:
            field_name = field['name']
            offset = field['offset']
            size = field.get('size', 4)
            field_type = field.get('type', 'FF_DWORD')

            result = idc.add_struc_member(
                sid,
                field_name,
                offset,
                getattr(idc, field_type, idc.FF_DATA),
                -1,
                size
            )
            if result != 0:
                print(f"[WARNING] Failed to add member {field_name} at offset {offset}")

        return {
            "success": True,
            "name": name,
            "sid": sid,
            "field_count": len(fields)
        }
    except Exception as e:
        return {"error": str(e)}

# 使用示例
# fields = [
#     {"name": "field1", "offset": 0, "size": 4, "type": "FF_DWORD"},
#     {"name": "field2", "offset": 4, "size": 8, "type": "FF_QWORD"},
# ]
# result = create_struct("my_struct", fields)
# __result__ = result
