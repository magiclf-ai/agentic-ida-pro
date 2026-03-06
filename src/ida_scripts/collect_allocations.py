import idc
import ida_hexrays

def _safe_dstr(node):
    try:
        return node.dstr()
    except Exception:
        return ""

def _const_int(node):
    if node is None:
        return None
    try:
        return int(node.numval())
    except Exception:
        pass
    try:
        return int(node.n._value)
    except Exception:
        return None

def _strip_cast_like(node):
    cur = node
    for _ in range(8):
        if cur is None:
            break
        op = int(getattr(cur, "op", -1))
        if op in (ida_hexrays.cot_cast, ida_hexrays.cot_ref, ida_hexrays.cot_ptr) and hasattr(cur, "x"):
            cur = cur.x
            continue
        break
    return cur

def _call_name(call_expr):
    callee = getattr(call_expr, "x", None)
    if callee is None:
        return ""
    try:
        if int(getattr(callee, "op", -1)) == ida_hexrays.cot_obj:
            ea = int(getattr(callee, "obj_ea", idc.BADADDR))
            if ea != idc.BADADDR:
                return idc.get_name(ea) or ""
    except Exception:
        pass
    return _safe_dstr(callee)

def _iter_args(call_expr):
    args = []
    try:
        for arg in getattr(call_expr, "a", []):
            args.append(arg)
    except Exception:
        pass
    return args

def _normalize_alloc_name(name):
    text = (name or "").lower()
    if "calloc" in text:
        return "calloc"
    if "malloc" in text:
        return "malloc"
    if "realloc" in text:
        return "realloc"
    if "operator new" in text or text.endswith("::operator new") or text == "new":
        return "operator_new"
    return ""

def _estimate_size(alloc_kind, args):
    values = [_const_int(a) for a in args]
    if alloc_kind == "calloc" and len(values) >= 2 and values[0] is not None and values[1] is not None:
        return int(values[0]) * int(values[1]), int(values[0]), int(values[1])
    if alloc_kind in ("malloc", "operator_new") and values and values[0] is not None:
        return int(values[0]), 1, int(values[0])
    if alloc_kind == "realloc" and len(values) >= 2 and values[1] is not None:
        return int(values[1]), None, int(values[1])
    return None, None, None

try:
    target_name = __FUNCTION_NAME__
    func_ea = idc.get_name_ea_simple(target_name)
    if func_ea == idc.BADADDR:
        __result__ = {"error": f"Function not found: {target_name}"}
    else:
        cfunc = ida_hexrays.decompile(func_ea)
        if not cfunc:
            __result__ = {"error": f"Decompile failed: {target_name}"}
        else:
            class AllocationCollector(ida_hexrays.ctree_visitor_t):
                def __init__(self):
                    ida_hexrays.ctree_visitor_t.__init__(self, ida_hexrays.CV_FAST)
                    self.allocations = []
                    self.aliases = []

                def visit_expr(self, expr):
                    try:
                        if int(getattr(expr, "op", -1)) != ida_hexrays.cot_asg:
                            return 0

                        lhs = getattr(expr, "x", None)
                        rhs = getattr(expr, "y", None)
                        if lhs is None or rhs is None:
                            return 0

                        lhs_text = _safe_dstr(lhs)
                        rhs_text = _safe_dstr(rhs)
                        ea = int(getattr(expr, "ea", 0))

                        if int(getattr(rhs, "op", -1)) == ida_hexrays.cot_call:
                            call_name_raw = _call_name(rhs)
                            alloc_kind = _normalize_alloc_name(call_name_raw)
                            if alloc_kind:
                                args = _iter_args(rhs)
                                alloc_size, alloc_count, alloc_elem_size = _estimate_size(alloc_kind, args)
                                self.allocations.append(
                                    {
                                        "ea": ea,
                                        "lhs": lhs_text,
                                        "call_name": call_name_raw,
                                        "alloc_kind": alloc_kind,
                                        "arg_texts": [_safe_dstr(a) for a in args],
                                        "arg_values": [_const_int(a) for a in args],
                                        "size_bytes": alloc_size,
                                        "count": alloc_count,
                                        "elem_size": alloc_elem_size,
                                        "expr_text": _safe_dstr(expr),
                                    }
                                )
                                return 0

                        if int(getattr(lhs, "op", -1)) == ida_hexrays.cot_var:
                            rhs_base = _strip_cast_like(rhs)
                            if rhs_base is not None and int(getattr(rhs_base, "op", -1)) == ida_hexrays.cot_var:
                                self.aliases.append(
                                    {
                                        "ea": ea,
                                        "dst": lhs_text,
                                        "src": _safe_dstr(rhs_base),
                                        "expr_text": _safe_dstr(expr),
                                    }
                                )
                    except Exception:
                        pass
                    return 0

            collector = AllocationCollector()
            collector.apply_to(cfunc.body, None)

            by_lhs = {}
            for item in collector.allocations:
                key = item.get("lhs", "")
                if key:
                    by_lhs.setdefault(key, []).append(item)

            result = {
                "function": target_name,
                "ea": int(func_ea),
                "allocation_count": len(collector.allocations),
                "alias_count": len(collector.aliases),
                "allocations": collector.allocations,
                "aliases": collector.aliases,
                "allocations_by_lhs": by_lhs,
            }
            if __INCLUDE_PSEUDOCODE__:
                result["pseudocode"] = str(cfunc)
            __result__ = result
except Exception as e:
    __result__ = {"error": str(e)}
