"""Probe AST-level facts for one function."""
import idc
import ida_hexrays


def _safe_text(node):
    try:
        return node.dstr()
    except Exception:
        return ""


target_name = __FUNCTION_NAME__
max_nodes = int(__MAX_NODES__)

func_ea = idc.get_name_ea_simple(target_name)
if func_ea == idc.BADADDR:
    __result__ = {"error": f"Function not found: {target_name}"}
else:
    cfunc = ida_hexrays.decompile(func_ea)
    if not cfunc:
        __result__ = {"error": f"Decompile failed: {target_name}"}
    else:
        expr_rows = []
        call_rows = []

        class Probe(ida_hexrays.ctree_visitor_t):
            def __init__(self):
                ida_hexrays.ctree_visitor_t.__init__(self, ida_hexrays.CV_FAST)

            def visit_expr(self, expr):
                try:
                    if len(expr_rows) < max_nodes:
                        expr_rows.append(
                            {
                                "ea": int(getattr(expr, "ea", 0)),
                                "op": int(getattr(expr, "op", -1)),
                                "text": _safe_text(expr),
                            }
                        )
                    if int(getattr(expr, "op", -1)) == ida_hexrays.cot_call:
                        call_rows.append(
                            {
                                "ea": int(getattr(expr, "ea", 0)),
                                "call_text": _safe_text(expr),
                            }
                        )
                except Exception:
                    pass
                return 0

        Probe().apply_to(cfunc.body, None)
        __result__ = {
            "function": target_name,
            "ea": int(func_ea),
            "call_count": len(call_rows),
            "calls": call_rows[:80],
            "expr_samples": expr_rows[:max_nodes],
            "pseudocode": str(cfunc),
        }
