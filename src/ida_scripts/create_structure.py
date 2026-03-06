import re
import traceback
import idaapi
import idc
import idautils
import ida_typeinf


struct_name = __STRUCT_NAME__
c_decl = __C_DECL__
fields = __FIELDS__


def _member_decl(size):
    size_value = int(size or 0)
    if size_value == 1:
        return "uint8_t"
    if size_value == 2:
        return "uint16_t"
    if size_value == 4:
        return "uint32_t"
    if size_value == 8:
        return "uint64_t"
    return f"uint8_t[{max(size_value, 1)}]"


def _render_struct_c(sid, name):
    size = int(idc.get_struc_size(sid) or 0)
    members = []
    try:
        for item in idautils.StructMembers(sid):
            off = int(item[0]) if len(item) >= 1 else 0
            mem_name = str(item[1]) if len(item) >= 2 else f"field_{off:x}"
            mem_size = int(item[2]) if len(item) >= 3 else int(idc.get_member_size(sid, off) or 0)
            members.append(
                {
                    "offset": off,
                    "name": mem_name,
                    "size": mem_size,
                }
            )
    except Exception:
        pass
    members.sort(key=lambda row: (int(row.get("offset", 0)), str(row.get("name", ""))))

    lines = [f"struct {name} {{"]
    for row in members:
        off = int(row.get("offset", 0))
        mem_name = str(row.get("name", "") or f"field_{off:x}")
        mem_size = int(row.get("size", 0) or 0)
        lines.append(f"    /*0x{off:x}*/ {_member_decl(mem_size)} {mem_name};")
    lines.append(f"}}; // size=0x{size:x} ({size})")
    return "\n".join(lines)


def _struct_signature(sid):
    rows = []
    try:
        for item in idautils.StructMembers(sid):
            off = int(item[0]) if len(item) >= 1 else 0
            mem_name = str(item[1]) if len(item) >= 2 else ""
            mem_size = int(item[2]) if len(item) >= 3 else int(idc.get_member_size(sid, off) or 0)
            rows.append((off, mem_name, mem_size))
    except Exception:
        pass
    rows.sort(key=lambda x: (int(x[0]), str(x[1])))
    return rows


def _normalize_field_name(raw_name, offset):
    text = str(raw_name or "").strip()
    if not text:
        text = f"field_{int(offset):x}"
    text = re.sub(r"[^A-Za-z0-9_]", "_", text)
    if not text:
        text = f"field_{int(offset):x}"
    if text[0].isdigit():
        text = f"f_{text}"
    return text


def _build_c_decl_from_fields(name, rows):
    normalized = []
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        try:
            offset = int(raw.get("offset", 0))
            size = int(raw.get("size", 0) or 0)
        except Exception:
            continue
        if offset < 0 or size <= 0:
            continue
        normalized.append(
            {
                "name": _normalize_field_name(raw.get("name", ""), offset),
                "offset": offset,
                "size": size,
            }
        )
    if not normalized:
        return ""

    normalized.sort(key=lambda x: int(x["offset"]))
    lines = [f"struct {name} {{"]
    cursor = 0
    pad_index = 0
    used_names = set()

    for field in normalized:
        offset = int(field["offset"])
        size = int(field["size"])
        if offset < cursor:
            return ""
        if offset > cursor:
            pad_size = offset - cursor
            pad_name = f"_pad_{pad_index:x}"
            pad_index += 1
            lines.append(f"    uint8_t {pad_name}[{pad_size}];")
            cursor = offset

        name_candidate = str(field["name"])
        final_name = name_candidate
        suffix = 0
        while final_name in used_names:
            suffix += 1
            final_name = f"{name_candidate}_{suffix}"
        used_names.add(final_name)

        if size in {1, 2, 4, 8}:
            lines.append(f"    {_member_decl(size)} {final_name};")
        else:
            lines.append(f"    uint8_t {final_name}[{size}];")
        cursor += size

    lines.append("};")
    return "\n".join(lines)


def _decl_struct_name(text):
    match = re.search(r"\bstruct\s+([A-Za-z_]\w*)\s*\{", str(text or ""))
    if not match:
        return ""
    return str(match.group(1) or "")


def _get_idati():
    try:
        return ida_typeinf.get_idati()
    except Exception:
        return idaapi.cvar.idati


def _parse_struct_decl_to_tinfo(decl_text):
    tif = ida_typeinf.tinfo_t()
    ida_typeinf.parse_decl(
        tif,
        _get_idati(),
        str(decl_text or ""),
        ida_typeinf.PT_TYP,
    )
    if bool(tif.empty()):
        return None, "C declaration parse failed"
    return tif, ""


def _set_named_struct_type(tif, name):
    flags = 0
    flags |= int(getattr(ida_typeinf, "NTF_REPLACE", 0))
    flags |= int(getattr(ida_typeinf, "NTF_CHKSYNC", 0))

    rc = tif.set_named_type(_get_idati(), str(name), int(flags))
    try:
        rc_int = int(rc)
    except Exception:
        rc_int = -1

    terr_ok = int(getattr(ida_typeinf, "TERR_OK", 0))
    if rc_int == terr_ok:
        return True, rc_int, ""

    err_text = f"set_named_type failed (rc={rc_int})"
    try:
        resolved = str(ida_typeinf.tinfo_errstr(rc_int) or "").strip()
        if resolved:
            err_text = f"{err_text}: {resolved}"
    except Exception:
        pass
    return False, rc_int, err_text


def _run():
    final_name = str(struct_name or "").strip()
    final_c_decl = str(c_decl or "").strip()
    if (not final_c_decl) and isinstance(fields, list):
        final_c_decl = _build_c_decl_from_fields(final_name, fields)

    if not final_name:
        return {
            "success": False,
            "struct_name": str(struct_name),
            "error": "empty struct_name",
            "mutation_effective": False,
        }
    if not final_c_decl:
        return {
            "success": False,
            "struct_name": final_name,
            "error": "missing c_decl (or invalid fields fallback)",
            "mutation_effective": False,
        }

    decl_name = _decl_struct_name(final_c_decl)
    if decl_name and decl_name != final_name:
        return {
            "success": False,
            "struct_name": final_name,
            "error": f"struct name mismatch: expected '{final_name}', got '{decl_name}' in c_decl",
            "mutation_effective": False,
        }

    before_sid = idc.get_struc_id(final_name)
    struct_existed = before_sid != idc.BADADDR
    before_sig = _struct_signature(before_sid) if struct_existed else []

    tif, parse_error = _parse_struct_decl_to_tinfo(final_c_decl)
    if tif is None:
        return {
            "success": False,
            "struct_name": final_name,
            "struct_existed": bool(struct_existed),
            "error": str(parse_error or "C declaration parse failed"),
            "mutation_effective": False,
            "input_c_decl": str(final_c_decl),
        }

    save_ok, save_rc, save_error = _set_named_struct_type(tif, final_name)

    after_sid = idc.get_struc_id(final_name)
    struct_visible = after_sid != idc.BADADDR
    created_struct = bool((not struct_existed) and struct_visible)
    after_sig = _struct_signature(after_sid) if struct_visible else []
    mutation_effective = bool(created_struct or (before_sig != after_sig))

    result = {
        "success": bool(save_ok),
        "struct_name": final_name,
        "sid": int(after_sid) if struct_visible else -1,
        "struct_existed": bool(struct_existed),
        "created_struct": bool(created_struct),
        "requested_field_count": len(fields) if isinstance(fields, list) else 0,
        "mutation_effective": bool(mutation_effective),
        "applied_with": "tinfo_t.set_named_type",
        "set_named_type_rc": int(save_rc),
        "input_c_decl": str(final_c_decl),
    }

    if save_ok:
        if struct_visible:
            try:
                result["c_declaration"] = _render_struct_c(after_sid, final_name)
            except Exception:
                result["c_declaration"] = str(final_c_decl)
        else:
            result["success"] = False
            result["error"] = "type saved but IDB struct was not synchronized"
            result["mutation_effective"] = False
            result["c_declaration"] = str(final_c_decl)
    else:
        result["error"] = str(save_error or "set_named_type failed")

    return result


try:
    __result__ = _run()
except Exception as e:
    __result__ = {
        "success": False,
        "struct_name": str(struct_name),
        "error": f"create_structure exception: {e}",
        "traceback": traceback.format_exc(),
        "mutation_effective": False,
    }
