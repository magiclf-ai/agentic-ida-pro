"""Probe global variable/data references used by one function."""
import idc
import idautils
import ida_funcs
import ida_hexrays


target_name = __FUNCTION_NAME__
func_ea = idc.get_name_ea_simple(target_name)
if func_ea == idc.BADADDR:
    __result__ = {"error": f"Function not found: {target_name}"}
else:
    cfunc = ida_hexrays.decompile(func_ea)
    if not cfunc:
        __result__ = {"error": f"Decompile failed: {target_name}"}
    else:
        globals_seen = []
        data_refs = []

        class Probe(ida_hexrays.ctree_visitor_t):
            def __init__(self):
                ida_hexrays.ctree_visitor_t.__init__(self, ida_hexrays.CV_FAST)

            def visit_expr(self, expr):
                try:
                    if int(getattr(expr, "op", -1)) == ida_hexrays.cot_obj:
                        ea = int(getattr(expr, "obj_ea", idc.BADADDR))
                        if ea != idc.BADADDR:
                            globals_seen.append(
                                {
                                    "ea": ea,
                                    "name": idc.get_name(ea) or "",
                                    "expr": expr.dstr() if hasattr(expr, "dstr") else "",
                                }
                            )
                except Exception:
                    pass
                return 0

        Probe().apply_to(cfunc.body, None)

        func = ida_funcs.get_func(func_ea)
        if func:
            seen = set()
            for insn_ea in idautils.FuncItems(func.start_ea):
                for dr in idautils.DataRefsFrom(insn_ea):
                    key = (int(insn_ea), int(dr))
                    if key in seen:
                        continue
                    seen.add(key)
                    data_refs.append(
                        {
                            "from_ea": int(insn_ea),
                            "to_ea": int(dr),
                            "to_name": idc.get_name(int(dr)) or "",
                        }
                    )

        __result__ = {
            "function": target_name,
            "ea": int(func_ea),
            "globals": globals_seen[:300],
            "global_count": len(globals_seen),
            "data_refs": data_refs[:300],
            "data_ref_count": len(data_refs),
        }
