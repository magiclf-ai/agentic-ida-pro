"""Apply a function prototype and return re-decompiled pseudocode for validation."""
import idc
import ida_hexrays

target_name = __FUNCTION_NAME__
c_declaration = __C_DECLARATION__

func_ea = idc.get_name_ea_simple(target_name)
if func_ea == idc.BADADDR:
    __result__ = {"error": f"Function not found: {target_name}"}
else:
    success = bool(idc.SetType(func_ea, c_declaration))
    out = {
        "function": target_name,
        "ea": int(func_ea),
        "declaration": c_declaration,
        "set_type_success": success,
    }
    try:
        cfunc = ida_hexrays.decompile(func_ea)
        out["pseudocode"] = str(cfunc) if cfunc else ""
        out["decompile_success"] = bool(cfunc)
    except Exception as e:
        out["decompile_success"] = False
        out["decompile_error"] = str(e)
    __result__ = out
