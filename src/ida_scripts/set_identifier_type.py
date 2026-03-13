import re

import idc
import ida_hexrays
import ida_nalt
import ida_typeinf


target_name = __FUNCTION_NAME__
operations = __OPERATIONS__
redecompile = bool(__REDECOMPILE__)


def _parse_data_tinfo_once(c_type_text):
    tif = ida_typeinf.tinfo_t()
    type_text = str(c_type_text or "").strip()
    if not type_text:
        return None, "empty c_type"
    ida_typeinf.parse_decl(
        tif,
        ida_typeinf.get_idati(),
        f"{type_text} __tmp;",
        ida_typeinf.PT_TYP,
    )
    if bool(tif.empty()):
        return None, f"failed to parse type: {type_text}"
    return tif, ""


def _rewrite_struct_tag_type(type_text):
    text = str(type_text or "").strip()
    if not text:
        return ""
    if text.startswith("struct "):
        return ""

    match = re.match(
        r"^(?P<prefix>(?:(?:const|volatile)\s+)*)?(?P<name>[A-Za-z_]\w*)(?P<suffix>\s*(?:\*.*)?)$",
        text,
    )
    if not match:
        return ""

    struct_name = str(match.group("name") or "").strip()
    if not struct_name:
        return ""
    if idc.get_struc_id(struct_name) == idc.BADADDR:
        return ""

    prefix = str(match.group("prefix") or "")
    suffix = str(match.group("suffix") or "")
    return f"{prefix}struct {struct_name}{suffix}".strip()


def _parse_data_tinfo(c_type_text):
    tif, err = _parse_data_tinfo_once(c_type_text)
    if tif is not None:
        return tif, ""

    fallback_text = _rewrite_struct_tag_type(c_type_text)
    if fallback_text and fallback_text != str(c_type_text or "").strip():
        tif_retry, err_retry = _parse_data_tinfo_once(fallback_text)
        if tif_retry is not None:
            return tif_retry, ""
        return None, err_retry
    return None, err


def _get_function_details(func_ea):
    tif = ida_typeinf.tinfo_t()
    ok = bool(ida_nalt.get_tinfo(tif, func_ea))
    if not ok:
        return None, None, "failed to get function tinfo"
    ftd = ida_typeinf.func_type_data_t()
    ok_details = bool(tif.get_func_details(ftd))
    if not ok_details:
        return None, None, "failed to get function signature details"
    return tif, ftd, ""


def _apply_function_details(func_ea, ftd):
    new_tif = ida_typeinf.tinfo_t()
    if not bool(new_tif.create_func(ftd)):
        return False, "failed to create new function type from details"
    flags = int(getattr(ida_typeinf, "TINFO_DEFINITE", 1))
    ok_apply = bool(ida_typeinf.apply_tinfo(func_ea, new_tif, flags))
    if not ok_apply:
        return False, "apply_tinfo failed for function"
    return True, ""


def _resolve_global_ea(op):
    if "address" in op:
        raw = op.get("address")
        try:
            return int(raw), ""
        except Exception:
            try:
                return int(str(raw or "").strip(), 0), ""
            except Exception:
                return idc.BADADDR, f"invalid global address: {raw}"
    symbol_name = str(op.get("name", "") or "").strip()
    if not symbol_name:
        return idc.BADADDR, "global operation requires name or address"
    ea = idc.get_name_ea_simple(symbol_name)
    if ea == idc.BADADDR:
        return idc.BADADDR, f"global symbol not found: {symbol_name}"
    return int(ea), ""


def _normalize(text, case_sensitive):
    value = str(text or "")
    if not case_sensitive:
        value = value.lower()
    return value


if not isinstance(operations, list) or not operations:
    __result__ = {"success": False, "error": "operations is empty", "mutation_effective": False}
else:
    func_ea = idc.get_name_ea_simple(target_name)
    if func_ea == idc.BADADDR:
        __result__ = {"success": False, "error": f"Function not found: {target_name}", "mutation_effective": False}
    else:
        op_reports = []
        mutation_effective = False
        success = True
        error_text = ""

        cfunc = None
        try:
            cfunc = ida_hexrays.decompile(func_ea)
        except Exception:
            cfunc = None

        for idx, raw_op in enumerate(operations):
            if not isinstance(raw_op, dict):
                success = False
                error_text = f"operation[{idx}] is not an object"
                break
            kind = str(raw_op.get("kind", "") or "").strip().lower()
            c_type = str(raw_op.get("c_type", "") or "").strip()
            name = str(raw_op.get("name", "") or "").strip()
            index = int(raw_op.get("index", -1))
            allow_substring = bool(raw_op.get("allow_substring", False))
            case_sensitive = bool(raw_op.get("case_sensitive", True))
            row = {
                "kind": kind,
                "name": name,
                "index": index,
                "c_type": c_type,
                "apply_ok": False,
                "changed": False,
            }

            if kind not in ("parameter", "local", "global", "return"):
                success = False
                error_text = f"operation[{idx}] invalid kind: {kind}"
                break
            if not c_type:
                success = False
                error_text = f"operation[{idx}] empty c_type"
                break

            if kind == "local":
                if cfunc is None:
                    try:
                        cfunc = ida_hexrays.decompile(func_ea)
                    except Exception as e:
                        success = False
                        error_text = f"operation[{idx}] failed to decompile for local update: {e}"
                        break
                if cfunc is None:
                    success = False
                    error_text = f"operation[{idx}] decompile returned empty cfunc"
                    break
                if not name:
                    success = False
                    error_text = f"operation[{idx}] local update requires name"
                    break
                target_tif, err = _parse_data_tinfo(c_type)
                if target_tif is None:
                    success = False
                    error_text = f"operation[{idx}] {err}"
                    break

                lvars = list(cfunc.get_lvars() or [])
                needle = _normalize(name, case_sensitive)
                matched = []
                for lv in lvars:
                    lv_name = str(getattr(lv, "name", ""))
                    hay = _normalize(lv_name, case_sensitive)
                    if allow_substring:
                        if needle and needle in hay:
                            matched.append(lv)
                    elif hay == needle:
                        matched.append(lv)

                if not matched:
                    success = False
                    error_text = f"operation[{idx}] local variable not found: {name}"
                    break

                changed_count = 0
                all_ok = True
                for lv in matched:
                    before_type = ""
                    try:
                        before_type = str(lv.type())
                    except Exception:
                        before_type = ""
                    info = ida_hexrays.lvar_saved_info_t()
                    info.ll = ida_hexrays.lvar_locator_t(lv.location, lv.defea)
                    info.type = target_tif
                    ok = bool(ida_hexrays.modify_user_lvar_info(func_ea, ida_hexrays.MLI_TYPE, info))
                    if not ok:
                        all_ok = False
                    if ok and before_type != str(target_tif):
                        changed_count += 1
                row["apply_ok"] = bool(all_ok)
                row["matched_count"] = len(matched)
                row["changed_count"] = int(changed_count)
                row["changed"] = bool(changed_count > 0)
                if not all_ok:
                    success = False
                    error_text = f"operation[{idx}] failed to apply local type: {name}"
                    break
                mutation_effective = mutation_effective or bool(row["changed"])

            elif kind == "parameter":
                _, ftd, err = _get_function_details(func_ea)
                if ftd is None:
                    success = False
                    error_text = f"operation[{idx}] {err}"
                    break
                param_index = int(index)
                if param_index < 0 and name:
                    if cfunc is None:
                        try:
                            cfunc = ida_hexrays.decompile(func_ea)
                        except Exception:
                            cfunc = None
                    arg_names = []
                    if cfunc is not None:
                        for lv in list(cfunc.get_lvars() or []):
                            try:
                                if bool(lv.is_arg_var()):
                                    arg_names.append(str(getattr(lv, "name", "")))
                            except Exception:
                                pass
                    needle = _normalize(name, case_sensitive)
                    matched_indexes = []
                    for arg_idx, arg_name in enumerate(arg_names):
                        hay = _normalize(arg_name, case_sensitive)
                        if allow_substring:
                            if needle and needle in hay:
                                matched_indexes.append(arg_idx)
                        elif hay == needle:
                            matched_indexes.append(arg_idx)
                    if len(matched_indexes) == 1:
                        param_index = int(matched_indexes[0])
                    elif len(matched_indexes) > 1:
                        success = False
                        error_text = f"operation[{idx}] ambiguous parameter name: {name}"
                        break
                if param_index < 0:
                    success = False
                    error_text = f"operation[{idx}] parameter requires valid index or resolvable name"
                    break
                if param_index >= len(ftd):
                    success = False
                    error_text = f"operation[{idx}] parameter index out of range: {param_index}"
                    break
                target_tif, err = _parse_data_tinfo(c_type)
                if target_tif is None:
                    success = False
                    error_text = f"operation[{idx}] {err}"
                    break
                before_type = str(getattr(ftd[param_index], "type", ""))
                ftd[param_index].type = target_tif
                ok_apply, err_apply = _apply_function_details(func_ea, ftd)
                if not ok_apply:
                    success = False
                    error_text = f"operation[{idx}] {err_apply}"
                    break
                row["index"] = int(param_index)
                row["apply_ok"] = True
                row["changed"] = bool(before_type != str(target_tif))
                mutation_effective = mutation_effective or bool(row["changed"])

            elif kind == "return":
                _, ftd, err = _get_function_details(func_ea)
                if ftd is None:
                    success = False
                    error_text = f"operation[{idx}] {err}"
                    break
                ret_tif, err = _parse_data_tinfo(c_type)
                if ret_tif is None:
                    if str(c_type).strip() != "void":
                        success = False
                        error_text = f"operation[{idx}] {err}"
                        break
                    func_tif = ida_typeinf.tinfo_t()
                    ida_typeinf.parse_decl(
                        func_tif,
                        ida_typeinf.get_idati(),
                        "void __tmp(void);",
                        ida_typeinf.PT_TYP,
                    )
                    if bool(func_tif.empty()):
                        success = False
                        error_text = f"operation[{idx}] failed to parse return type: {c_type}"
                        break
                    tmp_details = ida_typeinf.func_type_data_t()
                    if not bool(func_tif.get_func_details(tmp_details)):
                        success = False
                        error_text = f"operation[{idx}] failed to extract return type details: {c_type}"
                        break
                    ret_tif = tmp_details.rettype
                before_type = str(getattr(ftd, "rettype", ""))
                ftd.rettype = ret_tif
                ok_apply, err_apply = _apply_function_details(func_ea, ftd)
                if not ok_apply:
                    success = False
                    error_text = f"operation[{idx}] {err_apply}"
                    break
                row["apply_ok"] = True
                row["changed"] = bool(before_type != str(ret_tif))
                mutation_effective = mutation_effective or bool(row["changed"])

            elif kind == "global":
                global_ea, err = _resolve_global_ea(raw_op)
                if global_ea == idc.BADADDR:
                    success = False
                    error_text = f"operation[{idx}] {err}"
                    break
                target_tif, err = _parse_data_tinfo(c_type)
                if target_tif is None:
                    success = False
                    error_text = f"operation[{idx}] {err}"
                    break
                before_type = str(idc.get_type(global_ea) or "")
                flags = int(getattr(ida_typeinf, "TINFO_DEFINITE", 1))
                ok_apply = bool(ida_typeinf.apply_tinfo(global_ea, target_tif, flags))
                if not ok_apply:
                    success = False
                    error_text = f"operation[{idx}] failed to apply global type at 0x{int(global_ea):x}"
                    break
                after_type = str(idc.get_type(global_ea) or "")
                row["ea"] = int(global_ea)
                row["apply_ok"] = True
                row["changed"] = bool(before_type != after_type)
                mutation_effective = mutation_effective or bool(row["changed"])

            op_reports.append(row)

        result = {
            "success": bool(success),
            "function": str(target_name),
            "ea": int(func_ea),
            "mutation_effective": bool(success and mutation_effective),
            "operations": op_reports,
        }

        if not success:
            result["error"] = str(error_text or "set_identifier_type failed")
            __result__ = result
        else:
            try:
                ida_hexrays.mark_cfunc_dirty(func_ea, False)
            except Exception:
                pass
            if redecompile:
                try:
                    updated = ida_hexrays.decompile(func_ea)
                    result["decompile_success"] = bool(updated)
                    result["pseudocode"] = str(updated) if updated else ""
                    if not updated:
                        result["error"] = "decompile returned empty cfunc after type update"
                        result["success"] = False
                except Exception as e:
                    result["decompile_success"] = False
                    result["error"] = f"decompile failed after type update: {e}"
                    result["success"] = False
            __result__ = result
