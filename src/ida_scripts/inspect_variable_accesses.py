import idc
import ida_hexrays


target_name = __FUNCTION_NAME__
raw_variables = __VARIABLE_NAMES__


def _safe_text(node):
    try:
        return str(node.dstr())
    except Exception:
        return ""


def _safe_type(node):
    try:
        return str(node.type)
    except Exception:
        return ""


def _safe_size_from_type(tif):
    if tif is None:
        return 0
    try:
        size = int(tif.get_size())
        if size > 0:
            return size
    except Exception:
        pass
    return 0


def _safe_size(node):
    try:
        return _safe_size_from_type(node.type)
    except Exception:
        return 0


def _parse_variable_names(raw_value):
    rows = []
    if isinstance(raw_value, (list, tuple, set)):
        rows = [str(x or "").strip() for x in raw_value]
    else:
        text = str(raw_value or "")
        text = text.replace(",", "\n")
        rows = [part.strip() for part in text.splitlines()]
    out = []
    seen = set()
    for row in rows:
        if not row:
            continue
        if row in seen:
            continue
        seen.add(row)
        out.append(row)
    return out


def _strip_casts(expr):
    cur = expr
    while cur is not None:
        try:
            op = int(getattr(cur, "op", -1))
        except Exception:
            break
        if op == int(getattr(ida_hexrays, "cot_cast", -1000)):
            cur = getattr(cur, "x", None)
            continue
        if op == int(getattr(ida_hexrays, "cot_ref", -1000)):
            cur = getattr(cur, "x", None)
            continue
        break
    return cur


def _get_number_value(expr):
    if expr is None:
        return None
    cur = _strip_casts(expr)
    if cur is None:
        return None
    try:
        op = int(getattr(cur, "op", -1))
    except Exception:
        return None
    if op != int(getattr(ida_hexrays, "cot_num", -1000)):
        return None
    for getter in ("numval", "nvalue"):
        try:
            fn = getattr(cur, getter, None)
            if fn:
                return int(fn())
        except Exception:
            pass
    try:
        return int(getattr(getattr(cur, "n", None), "_value", 0))
    except Exception:
        return None


def _iter_children(expr):
    if expr is None:
        return []
    out = []
    for key in ("x", "y", "z"):
        child = getattr(expr, key, None)
        if child is not None:
            out.append(child)
    try:
        for arg in getattr(expr, "a", []):
            out.append(arg)
    except Exception:
        pass
    return out


def _build_local_maps(cfunc):
    idx_to_name = {}
    arg_names = set()
    try:
        for idx, lv in enumerate(cfunc.get_lvars()):
            name = str(getattr(lv, "name", "") or "").strip()
            if not name:
                continue
            idx_to_name[int(idx)] = name
            try:
                if bool(lv.is_arg_var()):
                    arg_names.add(name)
            except Exception:
                pass
    except Exception:
        pass
    return idx_to_name, arg_names


def _expr_var_name(expr, idx_to_name):
    if expr is None:
        return ""
    cur = _strip_casts(expr)
    if cur is None:
        return ""
    try:
        op = int(getattr(cur, "op", -1))
    except Exception:
        return ""
    if op != int(getattr(ida_hexrays, "cot_var", -1000)):
        return ""
    try:
        idx = int(getattr(getattr(cur, "v", None), "idx", -1))
    except Exception:
        idx = -1
    if idx in idx_to_name:
        return str(idx_to_name[idx])
    text = _safe_text(cur).strip()
    return text


def _contains_var(expr, wanted, idx_to_name, depth=0, max_depth=14):
    if expr is None or depth > max_depth:
        return False
    name = _expr_var_name(expr, idx_to_name)
    if name and name == wanted:
        return True
    for child in _iter_children(expr):
        if _contains_var(child, wanted, idx_to_name, depth + 1, max_depth=max_depth):
            return True
    return False


def _first_hit_var(expr, target_vars, idx_to_name, depth=0, max_depth=14):
    if expr is None or depth > max_depth:
        return ""
    name = _expr_var_name(expr, idx_to_name)
    if name and name in target_vars:
        return name
    for child in _iter_children(expr):
        hit = _first_hit_var(child, target_vars, idx_to_name, depth + 1, max_depth=max_depth)
        if hit:
            return hit
    return ""


def _pointer_scale(expr):
    if expr is None:
        return 1
    tif = getattr(expr, "type", None)
    if tif is None:
        cur = _strip_casts(expr)
        if cur is not None:
            tif = getattr(cur, "type", None)
    if tif is None:
        return 1
    try:
        if bool(tif.is_ptr()):
            size = int(tif.get_ptrarr_objsize())
            if size > 0:
                return size
    except Exception:
        pass
    try:
        if bool(tif.is_array()):
            et = tif.get_array_element()
            size = _safe_size_from_type(et)
            if size > 0:
                return size
    except Exception:
        pass
    return 1


def _offset_from_address(addr_expr, var_name, idx_to_name, depth=0, max_depth=14):
    if addr_expr is None or depth > max_depth:
        return None
    cur = _strip_casts(addr_expr)
    if cur is None:
        return None
    op = int(getattr(cur, "op", -1))

    if op == int(getattr(ida_hexrays, "cot_var", -1000)):
        name = _expr_var_name(cur, idx_to_name)
        if name == var_name:
            return 0
        return None

    if op in {
        int(getattr(ida_hexrays, "cot_memptr", -1000)),
        int(getattr(ida_hexrays, "cot_memref", -1000)),
    }:
        base = getattr(cur, "x", None)
        if _contains_var(base, var_name, idx_to_name):
            try:
                return int(getattr(cur, "m", 0))
            except Exception:
                return 0
        return None

    if op == int(getattr(ida_hexrays, "cot_idx", -1000)):
        base = getattr(cur, "x", None)
        idx_expr = getattr(cur, "y", None)
        if not _contains_var(base, var_name, idx_to_name):
            return None
        idx_value = _get_number_value(idx_expr)
        if idx_value is None:
            return None
        scale = _pointer_scale(base)
        return int(idx_value) * int(scale)

    if op == int(getattr(ida_hexrays, "cot_add", -1000)):
        left = getattr(cur, "x", None)
        right = getattr(cur, "y", None)
        if _contains_var(left, var_name, idx_to_name):
            num = _get_number_value(right)
            if num is not None:
                return int(num) * int(_pointer_scale(left))
            nested = _offset_from_address(right, var_name, idx_to_name, depth + 1, max_depth=max_depth)
            if nested is not None:
                return nested
        if _contains_var(right, var_name, idx_to_name):
            num = _get_number_value(left)
            if num is not None:
                return int(num) * int(_pointer_scale(right))
            nested = _offset_from_address(left, var_name, idx_to_name, depth + 1, max_depth=max_depth)
            if nested is not None:
                return nested
        return None

    if op == int(getattr(ida_hexrays, "cot_sub", -1000)):
        left = getattr(cur, "x", None)
        right = getattr(cur, "y", None)
        if _contains_var(left, var_name, idx_to_name):
            num = _get_number_value(right)
            if num is not None:
                return -int(num) * int(_pointer_scale(left))
            nested = _offset_from_address(left, var_name, idx_to_name, depth + 1, max_depth=max_depth)
            if nested is not None:
                return nested
        return None

    for child in _iter_children(cur):
        nested = _offset_from_address(child, var_name, idx_to_name, depth + 1, max_depth=max_depth)
        if nested is not None:
            return nested
    return None


def _infer_offset(expr, var_name, idx_to_name):
    if expr is None or not var_name:
        return None
    cur = _strip_casts(expr)
    if cur is None:
        return None
    op = int(getattr(cur, "op", -1))

    if op == int(getattr(ida_hexrays, "cot_ptr", -1000)):
        return _offset_from_address(getattr(cur, "x", None), var_name, idx_to_name)
    if op in {
        int(getattr(ida_hexrays, "cot_memptr", -1000)),
        int(getattr(ida_hexrays, "cot_memref", -1000)),
        int(getattr(ida_hexrays, "cot_idx", -1000)),
    }:
        return _offset_from_address(cur, var_name, idx_to_name)
    return _offset_from_address(cur, var_name, idx_to_name)


def _is_access_expr(expr):
    if expr is None:
        return False
    cur = _strip_casts(expr)
    if cur is None:
        return False
    op = int(getattr(cur, "op", -1))
    access_ops = {
        int(getattr(ida_hexrays, "cot_ptr", -1000)),
        int(getattr(ida_hexrays, "cot_memptr", -1000)),
        int(getattr(ida_hexrays, "cot_memref", -1000)),
        int(getattr(ida_hexrays, "cot_idx", -1000)),
    }
    return op in access_ops


def _assignment_ops():
    names = [
        "cot_asg",
        "cot_asgbor",
        "cot_asgxor",
        "cot_asgband",
        "cot_asgadd",
        "cot_asgsub",
        "cot_asgmul",
        "cot_asgsshr",
        "cot_asgushr",
        "cot_asgshl",
        "cot_asgsdiv",
        "cot_asgudiv",
        "cot_asgsmod",
        "cot_asgumod",
    ]
    out = set()
    for name in names:
        out.add(int(getattr(ida_hexrays, name, -1)))
    return out


func_ea = idc.get_name_ea_simple(target_name)
if func_ea == idc.BADADDR:
    __result__ = {"error": "Function not found: %s" % target_name}
else:
    cfunc = ida_hexrays.decompile(func_ea)
    if not cfunc:
        __result__ = {"error": "Decompile failed: %s" % target_name}
    else:
        idx_to_name, arg_names = _build_local_maps(cfunc)
        requested_vars = _parse_variable_names(raw_variables)
        target_vars = set(requested_vars)
        present_vars = sorted([name for name in requested_vars if name in idx_to_name.values()])
        missing_vars = sorted([name for name in requested_vars if name not in idx_to_name.values()])

        assign_ops = _assignment_ops()
        row_map = {}

        def _upsert(expr, access_kind):
            if expr is None:
                return
            if not _is_access_expr(expr):
                return
            var_name = _first_hit_var(expr, target_vars, idx_to_name)
            if not var_name:
                return
            expr_text = _safe_text(expr).strip()
            if not expr_text:
                return
            rel_off = _infer_offset(expr, var_name, idx_to_name)
            inferred_type = _safe_type(expr)
            access_size = _safe_size(expr)
            ea = int(getattr(expr, "ea", 0) or 0)
            is_arg = var_name in arg_names
            key = (
                var_name,
                expr_text,
                rel_off if rel_off is not None else "unknown",
                inferred_type,
                int(access_size),
                ea,
            )
            row = row_map.get(key)
            if row is None:
                row = {
                    "variable_name": var_name,
                    "is_argument": bool(is_arg),
                    "expression": expr_text,
                    "relative_offset": rel_off,
                    "inferred_type": inferred_type,
                    "access_size": int(access_size),
                    "ea": ea,
                    "read": False,
                    "write": False,
                }
                row_map[key] = row
            if access_kind == "write":
                row["write"] = True
            else:
                row["read"] = True

        class AccessCollector(ida_hexrays.ctree_visitor_t):
            def __init__(self):
                ida_hexrays.ctree_visitor_t.__init__(self, ida_hexrays.CV_FAST)

            def visit_expr(self, expr):
                try:
                    op = int(getattr(expr, "op", -1))
                    _upsert(expr, "read")
                    if op in assign_ops:
                        _upsert(getattr(expr, "x", None), "write")
                except Exception:
                    pass
                return 0

        AccessCollector().apply_to(cfunc.body, None)

        rows = list(row_map.values())
        for row in rows:
            if row.get("read") and row.get("write"):
                row["access_kind"] = "read/write"
            elif row.get("write"):
                row["access_kind"] = "write"
            else:
                row["access_kind"] = "read"
            row.pop("read", None)
            row.pop("write", None)

        rows.sort(
            key=lambda item: (
                str(item.get("variable_name", "")),
                int(item.get("relative_offset", 1 << 30)) if isinstance(item.get("relative_offset"), int) else (1 << 30),
                int(item.get("ea", 0)),
                str(item.get("expression", "")),
            )
        )

        __result__ = {
            "function": target_name,
            "ea": int(func_ea),
            "requested_variables": requested_vars,
            "present_variables": present_vars,
            "missing_variables": missing_vars,
            "accesses": rows,
            "access_count": len(rows),
            "source": "ctree",
        }
