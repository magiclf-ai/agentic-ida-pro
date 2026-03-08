import re
import traceback
import idc


function_name = __FUNCTION_NAME__
analysis_status = __ANALYSIS_STATUS__
change_summary = __CHANGE_SUMMARY__
function_summary = __FUNCTION_SUMMARY__
repeatable = bool(__REPEATABLE__)

LLM_FUNC_BEGIN = "[LLM_FUNCTION_NOTE_BEGIN]"
LLM_FUNC_END = "[LLM_FUNCTION_NOTE_END]"


def _strip_llm_function_block(text):
    value = str(text or "")
    pattern = re.compile(
        r"\n?\[LLM_FUNCTION_NOTE_BEGIN\].*?\[LLM_FUNCTION_NOTE_END\]\n?",
        flags=re.DOTALL,
    )
    cleaned = re.sub(pattern, "\n", value)
    return str(cleaned or "").strip()


def _build_llm_comment():
    status = str(analysis_status or "").strip() or "分析完成"
    changes = str(change_summary or "").strip() or "(无显式改动)"
    summary = str(function_summary or "").strip() or "(无摘要)"
    lines = [
        f"分析状态: {status}",
        "改动摘要:",
        changes,
        "函数摘要:",
        summary,
    ]
    return "\n".join(lines).strip()


def _merge_function_comment(existing, llm_comment):
    base = _strip_llm_function_block(existing)
    block = f"{LLM_FUNC_BEGIN}\n{str(llm_comment or '').strip()}\n{LLM_FUNC_END}".strip()
    if not base:
        return block
    return f"{base}\n\n{block}".strip()


def _run():
    target_name = str(function_name or "").strip()
    if not target_name:
        return {
            "success": False,
            "error": "empty function_name",
            "mutation_effective": False,
        }

    func_ea = idc.get_name_ea_simple(target_name)
    if int(func_ea) == int(idc.BADADDR):
        return {
            "success": False,
            "function_name": target_name,
            "error": f"function not found: {target_name}",
            "mutation_effective": False,
        }

    start_ea = int(idc.get_func_attr(func_ea, idc.FUNCATTR_START) or idc.BADADDR)
    if start_ea == int(idc.BADADDR):
        return {
            "success": False,
            "function_name": target_name,
            "ea": int(func_ea),
            "error": f"target is not a function: 0x{int(func_ea):x}",
            "mutation_effective": False,
        }

    mode = 1 if bool(repeatable) else 0
    before = str(idc.get_func_cmt(start_ea, mode) or "")
    merged = _merge_function_comment(before, _build_llm_comment())
    if merged == before:
        return {
            "success": True,
            "function_name": target_name,
            "ea": int(start_ea),
            "repeatable": bool(repeatable),
            "comment_changed": False,
            "mutation_effective": False,
            "comment_after": before,
        }

    ok = bool(idc.set_func_cmt(start_ea, merged, mode))
    if not ok:
        return {
            "success": False,
            "function_name": target_name,
            "ea": int(start_ea),
            "repeatable": bool(repeatable),
            "error": "set_func_cmt failed",
            "mutation_effective": False,
        }

    after = str(idc.get_func_cmt(start_ea, mode) or "")
    changed = bool(after != before)
    return {
        "success": True,
        "function_name": target_name,
        "ea": int(start_ea),
        "repeatable": bool(repeatable),
        "comment_changed": bool(changed),
        "mutation_effective": bool(changed),
        "comment_after": after,
    }


try:
    __result__ = _run()
except Exception as e:
    __result__ = {
        "success": False,
        "function_name": str(function_name or ""),
        "error": f"set_function_comment exception: {e}",
        "traceback": traceback.format_exc(),
        "mutation_effective": False,
    }
