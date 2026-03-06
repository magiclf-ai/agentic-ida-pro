import idc
import ida_funcs

target_ea = int(__FUNCTION_ADDR__)
func = ida_funcs.get_func(target_ea)
if not func:
    __result__ = {"error": f"Function not found at address 0x{target_ea:x}"}
else:
    __result__ = {
        "ea": int(func.start_ea),
        "name": idc.get_func_name(func.start_ea),
        "start_ea": int(func.start_ea),
        "end_ea": int(func.end_ea),
        "size": int(func.size()),
        "arg_count": int(getattr(func, "argcnt", 0)),
    }
