import idc
import idautils
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


def _callee_name(call_expr):
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
    return _safe_text(callee)


target_name = __FUNCTION_NAME__
include_pseudocode = bool(__INCLUDE_PSEUDOCODE__)
max_expr_samples = int(__MAX_EXPR_SAMPLES__)

func_ea = idc.get_name_ea_simple(target_name)
if func_ea == idc.BADADDR:
    __result__ = {"error": f"Function not found: {target_name}"}
else:
    cfunc = ida_hexrays.decompile(func_ea)
    if not cfunc:
        __result__ = {"error": f"Decompile failed: {target_name}"}
    else:
        call_sites = []
        member_accesses = []
        pointer_derefs = []
        expr_samples = []

        class DeepCollector(ida_hexrays.ctree_visitor_t):
            def __init__(self):
                ida_hexrays.ctree_visitor_t.__init__(self, ida_hexrays.CV_FAST)

            def visit_expr(self, expr):
                try:
                    if len(expr_samples) < max_expr_samples:
                        expr_samples.append(
                            {
                                "ea": int(getattr(expr, "ea", 0)),
                                "op": int(getattr(expr, "op", -1)),
                                "text": _safe_text(expr),
                                "type": _safe_type(expr),
                                "size": _safe_size(expr),
                            }
                        )

                    op = int(getattr(expr, "op", -1))
                    if op == ida_hexrays.cot_call:
                        args = []
                        try:
                            for arg in getattr(expr, "a", []):
                                args.append(_safe_text(arg))
                        except Exception:
                            pass
                        call_sites.append(
                            {
                                "ea": int(getattr(expr, "ea", 0)),
                                "callee": _callee_name(expr),
                                "arg_count": len(args),
                                "args": args[:8],
                                "expr_text": _safe_text(expr),
                            }
                        )
                    elif op in (ida_hexrays.cot_memptr, ida_hexrays.cot_memref):
                        member_accesses.append(
                            {
                                "ea": int(getattr(expr, "ea", 0)),
                                "op": "memptr" if op == ida_hexrays.cot_memptr else "memref",
                                "offset": int(getattr(expr, "m", 0)),
                                "size": _safe_size(expr),
                                "expr_text": _safe_text(expr),
                                "base_text": _safe_text(getattr(expr, "x", None)),
                                "base_type": _safe_type(getattr(expr, "x", None)),
                            }
                        )
                    elif op == ida_hexrays.cot_ptr:
                        pointer_derefs.append(
                            {
                                "ea": int(getattr(expr, "ea", 0)),
                                "size": _safe_size(expr),
                                "expr_text": _safe_text(expr),
                                "base_text": _safe_text(getattr(expr, "x", None)),
                                "base_type": _safe_type(getattr(expr, "x", None)),
                            }
                        )
                except Exception:
                    pass
                return 0

        collector = DeepCollector()
        collector.apply_to(cfunc.body, None)

        lvars = []
        try:
            for lv in cfunc.get_lvars():
                try:
                    lvars.append({"name": str(lv.name), "type": str(lv.type())})
                except Exception:
                    lvars.append({"name": str(getattr(lv, "name", "")), "type": ""})
        except Exception:
            pass

        xrefs_to = []
        try:
            for x in idautils.XrefsTo(func_ea):
                xrefs_to.append({"from": int(x.frm), "type": int(x.type)})
        except Exception:
            pass

        result = {
            "function": target_name,
            "ea": int(func_ea),
            "calls": call_sites,
            "call_count": len(call_sites),
            "member_accesses": member_accesses,
            "member_access_count": len(member_accesses),
            "pointer_derefs": pointer_derefs,
            "pointer_deref_count": len(pointer_derefs),
            "lvars": lvars,
            "lvar_count": len(lvars),
            "xrefs_to_count": len(xrefs_to),
            "xrefs_to": xrefs_to[:128],
            "expr_samples": expr_samples,
        }
        if include_pseudocode:
            result["pseudocode"] = str(cfunc)
        __result__ = result
