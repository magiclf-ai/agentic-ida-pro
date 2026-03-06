import idc
import ida_funcs

target_name = __FUNCTION_NAME__
func_ea = idc.get_name_ea_simple(target_name)
if func_ea == idc.BADADDR:
    __result__ = {"error": f"Function not found: {target_name}"}
else:
    func = ida_funcs.get_func(func_ea)
    if not func:
        __result__ = {"error": f"Function object is None: {target_name}"}
    else:
        __result__ = {
            "ea": int(func_ea),
            "name": idc.get_func_name(func_ea),
            "start_ea": int(func.start_ea),
            "end_ea": int(func.end_ea),
            "size": int(func.size()),
            "arg_count": int(getattr(func, "argcnt", 0)),
        }
