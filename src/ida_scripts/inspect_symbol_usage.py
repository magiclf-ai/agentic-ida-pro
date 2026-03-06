import idc
import idautils
import ida_funcs
import ida_hexrays


def _safe_text(node):
    try:
        return node.dstr()
    except Exception:
        return ""


def _safe_type(node):
    try:
        return str(node.type)
    except Exception:
        return ""


def _safe_size(node):
    try:
        return int(node.type.get_size())
    except Exception:
        return 0


def _obj_row(expr):
    ea = int(getattr(expr, "obj_ea", idc.BADADDR))
    symbol_type = "global"
    if ea != idc.BADADDR:
        try:
            if ida_funcs.get_func(ea):
                symbol_type = "function"
        except Exception:
            symbol_type = "global"
    return {
        "ea": ea,
        "name": idc.get_name(ea) if ea != idc.BADADDR else "",
        "symbol_type": symbol_type,
        "text": _safe_text(expr),
        "type": _safe_type(expr),
        "size": _safe_size(expr),
    }


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


def _collect_objs(expr, out, depth=0, max_depth=8, max_count=32):
    if expr is None or depth > max_depth or len(out) >= max_count:
        return
    try:
        if int(getattr(expr, "op", -1)) == ida_hexrays.cot_obj:
            out.append(_obj_row(expr))
    except Exception:
        pass
    for child in _iter_children(expr):
        _collect_objs(child, out, depth + 1, max_depth=max_depth, max_count=max_count)


target_name = __FUNCTION_NAME__
include_pseudocode = bool(__INCLUDE_PSEUDOCODE__)
include_data_refs = bool(__INCLUDE_DATA_REFS__)

func_ea = idc.get_name_ea_simple(target_name)
if func_ea == idc.BADADDR:
    __result__ = {"error": f"Function not found: {target_name}"}
else:
    cfunc = ida_hexrays.decompile(func_ea)
    if not cfunc:
        __result__ = {"error": f"Decompile failed: {target_name}"}
    else:
        locals_info = []
        try:
            for lv in cfunc.get_lvars():
                is_arg = False
                is_stk = False
                try:
                    is_arg = bool(lv.is_arg_var())
                except Exception:
                    pass
                try:
                    is_stk = bool(lv.is_stk_var())
                except Exception:
                    pass
                try:
                    lv_type = str(lv.type())
                except Exception:
                    lv_type = ""
                locals_info.append(
                    {
                        "name": str(getattr(lv, "name", "")),
                        "type": lv_type,
                        "is_arg": is_arg,
                        "is_stack": is_stk,
                    }
                )
        except Exception:
            pass

        global_reads = []
        global_writes = []
        local_reads = []
        local_writes = []
        function_calls = []

        class SymbolCollector(ida_hexrays.ctree_visitor_t):
            def __init__(self):
                ida_hexrays.ctree_visitor_t.__init__(self, ida_hexrays.CV_FAST)

            def visit_expr(self, expr):
                try:
                    op = int(getattr(expr, "op", -1))
                    if op == ida_hexrays.cot_asg:
                        lhs = getattr(expr, "x", None)
                        rhs = getattr(expr, "y", None)
                        if lhs is not None:
                            if int(getattr(lhs, "op", -1)) == ida_hexrays.cot_obj:
                                row = _obj_row(lhs)
                                row["expr"] = _safe_text(expr)
                                global_writes.append(row)
                            elif int(getattr(lhs, "op", -1)) == ida_hexrays.cot_var:
                                local_writes.append(
                                    {
                                        "var": _safe_text(lhs),
                                        "expr": _safe_text(expr),
                                        "ea": int(getattr(expr, "ea", 0)),
                                    }
                                )
                        if rhs is not None:
                            obj_refs = []
                            _collect_objs(rhs, obj_refs)
                            for row in obj_refs:
                                row["expr"] = _safe_text(expr)
                                global_reads.append(row)
                            # 局部变量读：简单基于文本记录
                            if int(getattr(rhs, "op", -1)) == ida_hexrays.cot_var:
                                local_reads.append(
                                    {
                                        "var": _safe_text(rhs),
                                        "expr": _safe_text(expr),
                                        "ea": int(getattr(expr, "ea", 0)),
                                    }
                                )
                    elif op == ida_hexrays.cot_obj:
                        row = _obj_row(expr)
                        row["expr"] = _safe_text(expr)
                        global_reads.append(row)
                    elif op == ida_hexrays.cot_call:
                        callee = getattr(expr, "x", None)
                        if callee is not None and int(getattr(callee, "op", -1)) == ida_hexrays.cot_obj:
                            row = _obj_row(callee)
                            row["expr"] = _safe_text(expr)
                            function_calls.append(
                                {
                                    "ea": int(row.get("ea", idc.BADADDR)),
                                    "name": str(row.get("name", "")),
                                    "expr": str(row.get("expr", "")),
                                    "symbol_type": "function",
                                }
                            )
                except Exception:
                    pass
                return 0

        collector = SymbolCollector()
        collector.apply_to(cfunc.body, None)

        def _dedupe(rows):
            out = []
            seen = set()
            for row in rows:
                key = (int(row.get("ea", idc.BADADDR)), str(row.get("name", "")), str(row.get("expr", "")))
                if key in seen:
                    continue
                seen.add(key)
                out.append(row)
            return out

        global_reads = _dedupe(global_reads)
        global_writes = _dedupe(global_writes)
        function_calls = _dedupe(function_calls)

        data_refs = []
        if include_data_refs:
            func = ida_funcs.get_func(func_ea)
            if func:
                seen = set()
                try:
                    for item_ea in idautils.FuncItems(func.start_ea):
                        for dr in idautils.DataRefsFrom(item_ea):
                            dr = int(dr)
                            key = (dr, int(item_ea))
                            if key in seen:
                                continue
                            seen.add(key)
                            data_refs.append(
                                {
                                    "from_ea": int(item_ea),
                                    "to_ea": dr,
                                    "to_name": idc.get_name(dr) or "",
                                }
                            )
                except Exception:
                    pass

        result = {
            "function": target_name,
            "ea": int(func_ea),
            "locals": locals_info,
            "arg_count": len([x for x in locals_info if x.get("is_arg")]),
            "local_count": len(locals_info),
            "global_reads": global_reads,
            "global_writes": global_writes,
            "global_read_count": len(global_reads),
            "global_write_count": len(global_writes),
            "local_reads": local_reads[:128],
            "local_writes": local_writes[:128],
            "local_read_count": len(local_reads),
            "local_write_count": len(local_writes),
            "data_refs": data_refs[:256],
            "data_ref_count": len(data_refs),
            "function_calls": function_calls[:256],
            "function_call_count": len(function_calls),
        }
        if include_pseudocode:
            result["pseudocode"] = str(cfunc)
        __result__ = result
