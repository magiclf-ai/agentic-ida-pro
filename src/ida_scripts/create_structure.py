import re
import traceback
import idaapi
import idc
import idautils
import ida_typeinf


struct_name = __STRUCT_NAME__
c_decl = __C_DECL__
fields = __FIELDS__
struct_comment = __STRUCT_COMMENT__

LLM_STRUCT_BEGIN = "[LLM_STRUCT_NOTE_BEGIN]"
LLM_STRUCT_END = "[LLM_STRUCT_NOTE_END]"


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


def _simplify_decl_for_retry(name, decl_text):
    text = str(decl_text or "").strip()
    if not text:
        return ""

    final_name = str(name or "").strip()
    simplified = text
    if final_name:
        simplified = re.sub(
            rf"^\s*typedef\s+struct\s+{re.escape(final_name)}\s+{re.escape(final_name)}\s*;\s*$",
            "",
            simplified,
            flags=re.MULTILINE,
        )

    fp_aliases = []

    def _drop_fp_typedef(match):
        alias = str(match.group("alias") or "").strip()
        if alias:
            fp_aliases.append(alias)
        return ""

    simplified = re.sub(
        r"^\s*typedef\s+.+?\(\s*\*\s*(?P<alias>[A-Za-z_]\w*)\s*\)\s*\([^;]*\)\s*;\s*$",
        _drop_fp_typedef,
        simplified,
        flags=re.MULTILINE,
    )
    for alias in fp_aliases:
        simplified = re.sub(rf"\b{re.escape(alias)}\b", "void *", simplified)

    builtin_types = {
        "void",
        "char",
        "signed",
        "unsigned",
        "short",
        "long",
        "int",
        "__int64",
        "uint8_t",
        "uint16_t",
        "uint32_t",
        "uint64_t",
        "int8_t",
        "int16_t",
        "int32_t",
        "int64_t",
        "size_t",
        "bool",
        "_BYTE",
        "_WORD",
        "_DWORD",
        "_QWORD",
    }

    def _replace_alias_pointer_field(match):
        indent = str(match.group("indent") or "")
        base = str(match.group("base") or "").strip()
        stars = str(match.group("stars") or "")
        field = str(match.group("field") or "").strip()
        suffix = str(match.group("suffix") or "")
        if not base or base in builtin_types:
            return str(match.group(0) or "")
        return f"{indent}void {stars} {field};{suffix}"

    simplified = re.sub(
        r"^(?P<indent>\s*)(?P<base>[A-Za-z_]\w*)\s*(?P<stars>\*+)\s*(?P<field>[A-Za-z_]\w*)\s*;(?P<suffix>\s*(?://.*)?)$",
        _replace_alias_pointer_field,
        simplified,
        flags=re.MULTILINE,
    )
    return str(simplified or "").strip()


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


def _parse_decl_with_retry(name, decl_text):
    tif, parse_error = _parse_struct_decl_to_tinfo(decl_text)
    if tif is not None:
        return tif, "", str(decl_text or "").strip()

    simplified = _simplify_decl_for_retry(name, decl_text)
    if simplified and simplified != str(decl_text or "").strip():
        tif_retry, parse_error_retry = _parse_struct_decl_to_tinfo(simplified)
        if tif_retry is not None:
            return tif_retry, "", simplified
        return None, str(parse_error_retry or parse_error or "C declaration parse failed"), simplified
    return None, str(parse_error or "C declaration parse failed"), str(decl_text or "").strip()


def _struct_name_candidates(name):
    final_name = str(name or "").strip()
    if not final_name:
        return []
    candidates = [final_name]
    if not final_name.startswith("struct "):
        candidates.append(f"struct {final_name}")
    deduped = []
    for candidate in candidates:
        if candidate not in deduped:
            deduped.append(candidate)
    return deduped


def _resolve_struct_sid(name):
    for candidate in _struct_name_candidates(name):
        sid = idc.get_struc_id(candidate)
        if sid != idc.BADADDR:
            return sid, candidate
    return idc.BADADDR, ""


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


def _import_type_to_idb(name):
    badnode = int(getattr(idaapi, "BADNODE", getattr(idc, "BADADDR", -1)))
    last_tid = -1
    for candidate in _struct_name_candidates(name):
        try:
            imported = idc.import_type(-1, str(candidate))
        except Exception:
            continue
        try:
            imported_int = int(imported)
        except Exception:
            continue
        last_tid = imported_int
        if imported_int != badnode:
            return True, imported_int
    return False, last_tid


def _strip_llm_struct_block(text):
    value = str(text or "")
    pattern = re.compile(
        r"\n?\[LLM_STRUCT_NOTE_BEGIN\].*?\[LLM_STRUCT_NOTE_END\]\n?",
        flags=re.DOTALL,
    )
    stripped = re.sub(pattern, "\n", value)
    return str(stripped or "").strip()


def _merge_struct_comment(existing, llm_comment):
    llm_text = str(llm_comment or "").strip()
    base = _strip_llm_struct_block(existing)
    if not llm_text:
        return base
    block = f"{LLM_STRUCT_BEGIN}\n{llm_text}\n{LLM_STRUCT_END}".strip()
    if not base:
        return block
    return f"{base}\n\n{block}".strip()


def _apply_struct_comment(sid, comment_text):
    target = str(comment_text or "").strip()
    if not target:
        return True, False, "", ""

    before = str(_get_struc_comment_compat(sid) or "")
    merged = _merge_struct_comment(before, target)
    if merged == before:
        return True, False, before, merged

    ok = bool(_set_struc_comment_compat(sid, merged))
    if not ok:
        return False, False, before, merged

    after = str(_get_struc_comment_compat(sid) or "")
    changed = bool(after != before)
    return True, changed, before, after


def _get_struc_comment_compat(sid):
    try:
        return idc.get_struc_cmt(sid, 1)
    except TypeError:
        pass
    except Exception:
        pass
    try:
        return idc.get_struc_cmt(sid)
    except Exception:
        return ""


def _set_struc_comment_compat(sid, comment_text):
    text = str(comment_text or "")
    try:
        return bool(idc.set_struc_cmt(sid, text, 1))
    except TypeError:
        pass
    except Exception:
        pass
    try:
        return bool(idc.set_struc_cmt(sid, text))
    except Exception:
        return False


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

    before_sid, before_sid_name = _resolve_struct_sid(final_name)
    struct_existed = before_sid != idc.BADADDR
    before_sig = _struct_signature(before_sid) if struct_existed else []

    tif, parse_error, effective_c_decl = _parse_decl_with_retry(final_name, final_c_decl)
    if tif is None:
        return {
            "success": False,
            "struct_name": final_name,
            "struct_existed": bool(struct_existed),
            "error": str(parse_error or "C declaration parse failed"),
            "mutation_effective": False,
            "input_c_decl": str(final_c_decl),
            "effective_c_decl": str(effective_c_decl or ""),
        }

    save_ok, save_rc, save_error = _set_named_struct_type(tif, final_name)

    after_sid, after_sid_name = _resolve_struct_sid(final_name)
    struct_visible = after_sid != idc.BADADDR
    import_type_ok = False
    import_type_tid = -1
    if save_ok and (not struct_visible):
        import_type_ok, import_type_tid = _import_type_to_idb(final_name)
        after_sid, after_sid_name = _resolve_struct_sid(final_name)
        struct_visible = after_sid != idc.BADADDR
    created_struct = bool((not struct_existed) and struct_visible)
    after_sig = _struct_signature(after_sid) if struct_visible else []
    mutation_effective = bool(created_struct or (before_sig != after_sig))
    comment_requested = bool(str(struct_comment or "").strip())
    comment_apply_ok = True
    comment_changed = False
    comment_before = ""
    comment_after = ""

    result = {
        "success": bool(save_ok),
        "struct_name": final_name,
        "resolved_struct_name": str(after_sid_name or before_sid_name or final_name),
        "sid": int(after_sid) if struct_visible else -1,
        "struct_existed": bool(struct_existed),
        "created_struct": bool(created_struct),
        "requested_field_count": len(fields) if isinstance(fields, list) else 0,
        "mutation_effective": bool(mutation_effective),
        "applied_with": "tinfo_t.set_named_type",
        "set_named_type_rc": int(save_rc),
        "input_c_decl": str(final_c_decl),
        "effective_c_decl": str(effective_c_decl or final_c_decl),
        "import_type_ok": bool(import_type_ok),
        "import_type_tid": int(import_type_tid),
        "comment_requested": bool(comment_requested),
        "comment_apply_ok": bool(comment_apply_ok),
        "comment_changed": bool(comment_changed),
    }

    if save_ok:
        if struct_visible:
            try:
                result["c_declaration"] = _render_struct_c(after_sid, final_name)
            except Exception:
                result["c_declaration"] = str(final_c_decl)
            if comment_requested:
                comment_apply_ok, comment_changed, comment_before, comment_after = _apply_struct_comment(
                    after_sid,
                    struct_comment,
                )
                result["comment_apply_ok"] = bool(comment_apply_ok)
                result["comment_changed"] = bool(comment_changed)
                result["comment_before"] = str(comment_before or "")
                result["comment_after"] = str(comment_after or "")
                if not comment_apply_ok:
                    result["success"] = False
                    result["error"] = "set_struc_cmt failed"
                    result["mutation_effective"] = bool(mutation_effective)
                    return result
                result["mutation_effective"] = bool(mutation_effective or comment_changed)
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
