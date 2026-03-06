import idc
import ida_funcs
import idautils
from collections import deque

entry_names = __ENTRY_NAMES__
max_depth = int(__MAX_DEPTH__)
include_thunks = bool(__INCLUDE_THUNKS__)

def _resolve_entry(name):
    ea = idc.get_name_ea_simple(name)
    if ea == idc.BADADDR:
        return idc.BADADDR
    f = ida_funcs.get_func(ea)
    if not f:
        return idc.BADADDR
    return int(f.start_ea)

def _is_allowed_func(ea):
    flags = int(idc.get_func_flags(ea))
    is_thunk = bool(flags & getattr(idc, "FUNC_THUNK", 0))
    is_lib = bool(flags & getattr(idc, "FUNC_LIB", 0))
    if not include_thunks and (is_thunk or is_lib):
        return False
    return True

resolved_entries = []
missing_entries = []
for name in entry_names:
    fea = _resolve_entry(name)
    if fea == idc.BADADDR:
        missing_entries.append(name)
    else:
        resolved_entries.append({"name": name, "ea": int(fea)})

visited_depth = {}
queue = deque()
for item in resolved_entries:
    ea = int(item["ea"])
    if ea not in visited_depth or visited_depth[ea] > 0:
        visited_depth[ea] = 0
        queue.append((ea, 0))

edges_map = {}
while queue:
    cur_ea, depth = queue.popleft()
    if depth >= max_depth:
        continue

    func = ida_funcs.get_func(cur_ea)
    if not func:
        continue

    try:
        items = list(idautils.FuncItems(cur_ea))
    except Exception:
        items = []

    for head in items:
        try:
            refs = list(idautils.CodeRefsFrom(head, 0))
        except Exception:
            refs = []
        for callee in refs:
            callee_func = ida_funcs.get_func(callee)
            if not callee_func:
                continue
            to_ea = int(callee_func.start_ea)
            if to_ea == int(cur_ea):
                continue

            edge_key = (int(cur_ea), to_ea)
            if edge_key not in edges_map:
                edges_map[edge_key] = {
                    "from_ea": int(cur_ea),
                    "to_ea": to_ea,
                    "sample_callsite": int(head),
                }

            next_depth = depth + 1
            if to_ea not in visited_depth or next_depth < visited_depth[to_ea]:
                if _is_allowed_func(to_ea):
                    visited_depth[to_ea] = next_depth
                    queue.append((to_ea, next_depth))

nodes = []
for ea, depth in visited_depth.items():
    flags = int(idc.get_func_flags(ea))
    nodes.append({
        "ea": int(ea),
        "name": idc.get_func_name(ea),
        "depth": int(depth),
        "is_thunk": bool(flags & getattr(idc, "FUNC_THUNK", 0)),
        "is_lib": bool(flags & getattr(idc, "FUNC_LIB", 0)),
    })

nodes.sort(key=lambda item: (int(item["depth"]), int(item["ea"])))

edges = list(edges_map.values())
edges.sort(key=lambda item: (int(item["from_ea"]), int(item["to_ea"])))

__result__ = {
    "entries": entry_names,
    "resolved_entries": resolved_entries,
    "missing_entries": missing_entries,
    "max_depth": max_depth,
    "include_thunks": include_thunks,
    "nodes": nodes,
    "edges": edges,
}
