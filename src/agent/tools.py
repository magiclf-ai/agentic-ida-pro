"""LangChain Tools 定义 - IDA Pro 交互工具集"""
import re
from langchain_core.tools import tool
from typing import Dict, Any, Optional, List
from pathlib import Path
from .ida_client import IDAClient


# 全局 IDA 客户端实例
_ida_client: Optional[IDAClient] = None


def _has_runtime_error_marker(text: Any) -> bool:
    value = str(text or "")
    return "[ERROR]" in value or "Traceback (most recent call last):" in value


def _module_not_found_hint(stdout: str, stderr: str) -> str:
    merged = f"{stdout}\n{stderr}"
    match = re.search(r"No module named '([^']+)'", merged)
    if not match:
        return ""
    module = match.group(1)
    fallback = {
        "ida_struct": "use `idc.SetType` or memory/ctree analysis APIs (`idc`, `idautils`, `ida_hexrays`) instead.",
        "idaapi": "prefer `idc`/`idautils`/`ida_hexrays` APIs that are available in this environment.",
    }
    advice = fallback.get(module, "check available modules in runtime context and switch to available APIs.")
    return f"Hint: module '{module}' is unavailable; {advice}"


def _name_error_hint(stdout: str, stderr: str) -> str:
    merged = f"{stdout}\n{stderr}"
    match = re.search(r"NameError:\s*name '([^']+)' is not defined", merged)
    if not match:
        return ""
    symbol = match.group(1)
    import_hints = {
        "ida_xref": "try adding `import ida_xref` before using xref constants.",
        "ida_bytes": "try adding `import ida_bytes` before memory/data-size operations.",
        "ida_lines": "try adding `import ida_lines` before tag-remove or pseudocode line cleanup.",
        "idaapi": "try adding `import idaapi` or replace with `idc`/`idautils` alternatives.",
    }
    advice = import_hints.get(
        symbol,
        "check imports and variable definitions in this script, then rerun with minimal changes.",
    )
    return f"Hint: symbol '{symbol}' is undefined; {advice}"


def _attribute_error_hint(stdout: str, stderr: str) -> str:
    merged = f"{stdout}\n{stderr}"
    lowered = merged.lower()
    if "attributeerror" not in lowered:
        return ""
    if ("idaapi" in lowered) and ("add_struc" in lowered) and ("has no attribute" in lowered):
        return (
            "Hint: `idaapi.add_struc` is unavailable in this IDA runtime. "
            "Prefer `create_structure` tool; if scripting is required, use `idc.add_struc` + "
            "`idc.add_struc_member` and keep try/except around each mutation step."
        )
    return ""


def _parse_decls_hint(stdout: str, stderr: str) -> str:
    merged = f"{stdout}\n{stderr}".lower()
    if "parse_decls" not in merged:
        return ""
    if ("missing 1 required positional argument: 'hti_flags'" in merged) or ("printer_t *" in merged):
        return (
            "Hint: parse_decls signature differs across IDA builds; stop brute-forcing this API in execute_idapython. "
            "Prefer structured tools (`create_structure`, `set_identifier_type`) "
            "to complete type-apply + redecompile convergence."
        )
    return ""


def _format_stream(text: str, max_chars: int = 4000) -> str:
    value = str(text or "")
    limit = int(max_chars)
    if len(value) <= limit:
        return value
    head = max(400, limit // 2)
    tail = max(300, limit - head)
    omitted = len(value) - head - tail
    return (
        value[:head]
        + f"\n... [omitted {omitted} chars] ...\n"
        + value[-tail:]
    )


def _scalar_text(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    return str(value)


def _render_structured_lines(value: Any, *, indent: str = "", max_items: int = 30) -> List[str]:
    if isinstance(value, dict):
        lines: List[str] = []
        keys = list(value.keys())
        for idx, key in enumerate(keys):
            if idx >= max_items:
                lines.append(f"{indent}- ... and {len(keys) - max_items} more fields")
                break
            child = value.get(key)
            if isinstance(child, (dict, list)):
                lines.append(f"{indent}- {key}:")
                lines.extend(_render_structured_lines(child, indent=indent + "  ", max_items=max(5, max_items // 2)))
            else:
                lines.append(f"{indent}- {key}: {_scalar_text(child)}")
        return lines or [f"{indent}- (empty)"]
    if isinstance(value, list):
        lines = []
        for idx, item in enumerate(value):
            if idx >= max_items:
                lines.append(f"{indent}- ... and {len(value) - max_items} more items")
                break
            if isinstance(item, (dict, list)):
                lines.append(f"{indent}- item_{idx + 1}:")
                lines.extend(_render_structured_lines(item, indent=indent + "  ", max_items=max(5, max_items // 2)))
            else:
                lines.append(f"{indent}- {_scalar_text(item)}")
        return lines or [f"{indent}- (empty)"]
    return [f"{indent}- {_scalar_text(value)}"]


def _append_structured_section(lines: List[str], title: str, value: Any, *, max_items: int = 30) -> None:
    lines.append(title)
    lines.extend(_render_structured_lines(value, indent="  ", max_items=max_items))


def _runtime_error_hints(stdout: str, stderr: str) -> str:
    hints = []
    for hint in (
        _attribute_error_hint(stdout, stderr),
        _parse_decls_hint(stdout, stderr),
        _module_not_found_hint(stdout, stderr),
        _name_error_hint(stdout, stderr),
    ):
        if hint:
            hints.append(hint)
    if not hints:
        hints.append("Hint: keep current script, patch only the failing line/import, and rerun.")
    return "\n".join(hints)


def _render_tokenized_template(script: str, variables: Dict[str, Any]) -> str:
    rendered = str(script or "")
    if not isinstance(variables, dict):
        return rendered

    for raw_key, value in variables.items():
        key = str(raw_key or "").strip()
        if not key:
            continue
        for alias in (key, key.upper(), key.lower()):
            token = f"__{alias}__"
            if token in rendered:
                rendered = rendered.replace(token, repr(value))
    return rendered


def _find_unresolved_tokens(script: str) -> List[str]:
    # Only treat all-caps placeholders as unresolved template tokens.
    # This avoids false positives on Python dunder names like __result__ or __init__.
    tokens = re.findall(r"__([A-Z][A-Z0-9_]*)__", str(script or ""))
    if not tokens:
        return []
    return sorted(set(tokens))


def _find_destructive_struct_ops(script: str) -> List[str]:
    text = str(script or "")
    lowered = text.lower()
    patterns = [
        "idc.del_struc(",
        "del_struc(",
    ]
    hits: List[str] = []
    for pattern in patterns:
        if pattern in lowered:
            hits.append(pattern.rstrip("("))
    return sorted(set(hits))


def get_ida_client() -> IDAClient:
    """获取全局 IDA 客户端实例"""
    global _ida_client
    if _ida_client is None:
        _ida_client = IDAClient()
    return _ida_client


def set_ida_client(client: IDAClient):
    """设置全局 IDA 客户端实例"""
    global _ida_client
    _ida_client = client


@tool(parse_docstring=True, error_on_invalid_docstring=True)
def execute_idapython(script: str, context: Optional[Dict[str, Any]] = None) -> str:
    """
    执行任意 IDAPython 代码（最灵活的工具）
    
    Args:
        script: 要执行的 IDAPython 代码
        context: 可选的上下文变量，会注入到脚本的命名空间
    
    Returns:
        执行结果，包含 success, result, stdout, stderr
    
    Examples:
        >>> execute_idapython("import idc; __result__ = idc.get_func_name(0x1000)")
    """
    destructive_ops = _find_destructive_struct_ops(script)
    if destructive_ops:
        return (
            "ERROR: destructive struct operation is blocked in execute_idapython.\n"
            f"Blocked operations: {destructive_ops}\n"
            "Reason: deleting existing structs can destroy recovered evidence and invalidate before/after acceptance.\n"
            "Use non-destructive updates (create_structure / set_identifier_type / member type edits) instead.\n"
        )

    client = get_ida_client()
    result = client.execute_script(script, context)
    
    # 格式化返回结果
    if result.get('success'):
        stdout = str(result.get("stdout") or "")
        stderr = str(result.get("stderr") or "")
        if _has_runtime_error_marker(stdout) or _has_runtime_error_marker(stderr):
            hint = _runtime_error_hints(stdout, stderr)
            return (
                "ERROR: runtime error detected while executing script.\n"
                f"Execution time: {result.get('execution_time', 0):.3f}s\n"
                "Partial output before failure can still be used as evidence.\n"
                f"Stdout:\n{_format_stream(stdout)}\n"
                f"Stderr:\n{_format_stream(stderr, max_chars=2000)}\n"
                f"{hint}\n"
            )
        lines = [f"OK: execute_idapython", f"Execution time: {result.get('execution_time', 0):.3f}s"]
        if result.get('result') is not None:
            if isinstance(result.get("result"), (dict, list)):
                _append_structured_section(lines, "Result:", result.get("result"), max_items=24)
            else:
                lines.append(f"Result: {result.get('result')}")
        if stdout:
            lines.append(f"Stdout:\n{_format_stream(stdout)}")
        return "\n".join(lines)
    else:
        return f"ERROR: {result.get('stderr', 'Unknown error')}"


@tool(parse_docstring=True, error_on_invalid_docstring=True)
def list_ida_script_templates(pattern: str = "*.py") -> str:
    """
    列出 src/ida_scripts 下可执行模板

    Args:
        pattern: 过滤模式（如 *.py, collect_*.py）

    Returns:
        模板清单
    """
    client = get_ida_client()
    try:
        templates = client.list_script_templates(pattern=pattern)
        lines = [
            f"IDA script templates (pattern={pattern!r}):",
            f"count={len(templates)}",
        ]
        for name in templates[:120]:
            lines.append(f"  - {name}")
        if len(templates) > 120:
            lines.append(f"  ... and {len(templates) - 120} more")
        return "\n".join(lines)
    except Exception as e:
        return f"ERROR: listing ida script templates failed: {str(e)}"


@tool(parse_docstring=True, error_on_invalid_docstring=True)
def run_ida_script_template(
    template_name: str,
    variables: Optional[Dict[str, Any]] = None,
    context: Optional[Dict[str, Any]] = None,
) -> str:
    """
    执行 src/ida_scripts 中的模板脚本（可做 __TOKEN__ 替换 + context 注入）

    Args:
        template_name: 模板文件名（相对 src/ida_scripts，例如 collect_allocations.py）
        variables: 替换模板中的 __TOKEN__ 变量
        context: 运行时注入脚本命名空间的变量

    Returns:
        执行结果
    """
    client = get_ida_client()
    try:
        result = client.execute_script_template(
            template_name=template_name,
            variables=variables or {},
            context=context or {},
        )
        if result.get("success"):
            stdout = str(result.get("stdout") or "")
            stderr = str(result.get("stderr") or "")
            if _has_runtime_error_marker(stdout) or _has_runtime_error_marker(stderr):
                return (
                    "ERROR: runtime error detected while running template.\n"
                    f"Execution time: {result.get('execution_time', 0):.3f}s\n"
                    f"Stdout:\n{stdout}\n"
                    f"Stderr:\n{stderr}\n"
                )
            lines = [f"OK: run_ida_script_template", f"Execution time: {result.get('execution_time', 0):.3f}s"]
            if result.get("result") is not None:
                if isinstance(result.get("result"), (dict, list)):
                    _append_structured_section(lines, "Result:", result.get("result"), max_items=24)
                else:
                    lines.append(f"Result: {result.get('result')}")
            if stdout:
                lines.append(f"Stdout:\n{stdout}")
            return "\n".join(lines)
        return f"ERROR: {result.get('stderr') or result.get('error') or 'unknown'}"
    except Exception as e:
        return f"ERROR: running ida script template failed: {str(e)}"


@tool(parse_docstring=True, error_on_invalid_docstring=True)
def get_function_info(name: Optional[str] = None, addr: Optional[int] = None) -> str:
    """
    获取函数的详细信息（地址、大小、参数数量等）
    
    Args:
        name: 函数名称（与 addr 二选一）
        addr: 函数地址（与 name 二选一）
    
    Returns:
        函数信息字典的字符串表示
    
    Examples:
        >>> get_function_info(name="main")
        >>> get_function_info(addr=0x1000)
    """
    client = get_ida_client()
    try:
        info = client.get_function_info(name=name, addr=addr)
        lines = ["OK: function info"]
        _append_structured_section(lines, "Details:", info, max_items=30)
        return "\n".join(lines)
    except Exception as e:
        return f"ERROR: getting function info failed: {str(e)}"


@tool(parse_docstring=True, error_on_invalid_docstring=True)
def list_all_functions() -> str:
    """
    列出数据库中的所有函数
    
    Returns:
        所有函数的列表，包含 ea, name, size
    
    Examples:
        >>> list_all_functions()
    """
    client = get_ida_client()
    try:
        functions = client.list_functions()
        output = f"OK: found {len(functions)} functions\n"
        for func in functions[:100]:  # 限制显示数量
            output += f"  0x{func['ea']:x}: {func['name']} (size: {func['size']})\n"
        if len(functions) > 100:
            output += f"  ... and {len(functions) - 100} more functions\n"
        return output
    except Exception as e:
        return f"ERROR: listing functions failed: {str(e)}"


@tool(parse_docstring=True, error_on_invalid_docstring=True)
def decompile_function(
    function_name: Optional[str] = None,
    ea: Optional[int] = None,
    name: Optional[str] = None,
    addr: Optional[int] = None,
) -> str:
    """
    反编译函数并返回伪代码
    
    Args:
        function_name: 函数名称（与 ea 二选一）
        ea: 函数地址（与 function_name 二选一）
        name: 兼容参数，等价于 function_name
        addr: 兼容参数，等价于 ea
    
    Returns:
        反编译后的伪代码字符串
    
    Examples:
        >>> decompile_function(function_name="main")
        >>> decompile_function(ea=0x1000)
    """
    client = get_ida_client()
    try:
        resolved_name = function_name or name
        resolved_ea = ea if ea is not None else addr
        code = client.decompile_function(function_name=resolved_name, ea=resolved_ea)
        rendered = str(code or "").rstrip("\n")
        return f"OK: decompiled code\n\n```c\n{rendered}\n```"
    except Exception as e:
        return f"ERROR: decompiling function failed: {str(e)}"


@tool(parse_docstring=True, error_on_invalid_docstring=True)
def search(
    pattern: str,
    target_type: str = "all",
    offset: int = 0,
    count: int = 20,
    flags: str = "IGNORECASE",
) -> str:
    """
    搜索符号和字符串（Python re 正则 + 分页）

    Args:
        pattern: Python re 正则表达式
        target_type: 目标类型（all|symbol|string）
        offset: 结果偏移（>=0）
        count: 返回条数（1..100）
        flags: 正则标志（IGNORECASE|MULTILINE|DOTALL，可用 | 连接）

    Returns:
        搜索结果（含 total_count 与分页游标）

    Examples:
        >>> search(pattern="sub_1400.*", target_type="symbol", offset=0, count=20)
        >>> search(pattern="(?i)http|token", target_type="string", offset=20, count=20)
    """
    client = get_ida_client()
    try:
        row_count = max(1, min(int(count), 100))
        row_offset = max(0, int(offset))
        data = client.search(
            pattern=pattern,
            target_type=target_type,
            offset=row_offset,
            count=row_count,
            flags=flags,
        )
        query = data.get("query", {}) if isinstance(data, dict) else {}
        items = data.get("items", []) if isinstance(data, dict) else []
        lines = [
            "# Search Results",
            f"- pattern: {query.get('pattern', pattern)}",
            f"- target_type: {query.get('target_type', target_type)}",
            f"- flags: {query.get('flags', flags)}",
            f"- total_count: {int(data.get('total_count', 0))}",
            f"- returned_count: {int(data.get('returned_count', 0))}",
            f"- offset: {int(data.get('offset', row_offset))}",
            f"- count: {int(data.get('count', row_count))}",
            f"- has_more: {str(bool(data.get('has_more', False))).lower()}",
            f"- next_offset: {data.get('next_offset') if data.get('next_offset') is not None else 'null'}",
        ]
        summary = data.get("summary", {}) if isinstance(data, dict) else {}
        if isinstance(summary, dict) and summary:
            lines.append(f"- symbol_count: {int(summary.get('symbol_count', 0))}")
            lines.append(f"- string_count: {int(summary.get('string_count', 0))}")

        if not items:
            lines.append("")
            lines.append("No matches found.")
            return "\n".join(lines)

        lines.append("")
        lines.append("## Items")
        base_no = int(data.get("offset", row_offset))
        for idx, row in enumerate(items, start=1):
            kind = str(row.get("kind", "") or "")
            subkind = str(row.get("subkind", "") or "")
            text = str(row.get("text", "") or "")
            ea_value = row.get("ea")
            ea_text = "n/a"
            try:
                ea_text = f"0x{int(ea_value):x}"
            except Exception:
                ea_text = "n/a"
            lines.append(
                f"{base_no + idx}. [{kind}/{subkind}] {text} @ {ea_text}"
            )
        return "\n".join(lines)
    except Exception as e:
        return f"ERROR: search failed: {str(e)}"


@tool(parse_docstring=True, error_on_invalid_docstring=True)
def xref(
    target: str,
    target_type: str,
    direction: str = "to",
    offset: int = 0,
    count: int = 20,
    flags: str = "IGNORECASE",
) -> str:
    """
    搜索符号/字符串/地址的交叉引用（分页）

    Args:
        target: 目标（符号正则/字符串正则/地址文本）
        target_type: 目标类型（symbol|string|ea）
        direction: 方向（to|from|both），默认 to
        offset: 结果偏移（>=0）
        count: 返回条数（1..100）
        flags: 正则标志（IGNORECASE|MULTILINE|DOTALL，可用 | 连接）

    Returns:
        交叉引用结果（优先展示 func_name+offset，否则 ea）

    Examples:
        >>> xref(target="sub_140001000", target_type="symbol", direction="to", offset=0, count=30)
        >>> xref(target="(?i)http|api", target_type="string", direction="to", offset=0, count=30)
        >>> xref(target="0x140010000", target_type="ea", direction="both", offset=0, count=20)
    """
    client = get_ida_client()
    try:
        row_count = max(1, min(int(count), 100))
        row_offset = max(0, int(offset))
        data = client.xrefs(
            target=target,
            target_type=target_type,
            direction=direction,
            offset=row_offset,
            count=row_count,
            flags=flags,
        )
        query = data.get("query", {}) if isinstance(data, dict) else {}
        items = data.get("items", []) if isinstance(data, dict) else []
        lines = [
            "# Xref Results",
            f"- target: {query.get('target', target)}",
            f"- target_type: {query.get('target_type', target_type)}",
            f"- direction: {query.get('direction', direction)}",
            f"- flags: {query.get('flags', flags)}",
            f"- resolved_target_count: {int(data.get('resolved_target_count', 0))}",
            f"- total_count: {int(data.get('total_count', 0))}",
            f"- returned_count: {int(data.get('returned_count', 0))}",
            f"- offset: {int(data.get('offset', row_offset))}",
            f"- count: {int(data.get('count', row_count))}",
            f"- has_more: {str(bool(data.get('has_more', False))).lower()}",
            f"- next_offset: {data.get('next_offset') if data.get('next_offset') is not None else 'null'}",
        ]
        if not items:
            lines.append("")
            lines.append("No xref matches found.")
            return "\n".join(lines)

        lines.append("")
        lines.append("## Items")
        base_no = int(data.get("offset", row_offset))
        for idx, row in enumerate(items, start=1):
            ref_loc = str(row.get("ref_loc", "") or "")
            if not ref_loc:
                ref_loc = f"ea=0x{int(row.get('xref_ea', 0)):x}"
            xref_ea = int(row.get("xref_ea", 0))
            target_ea = int(row.get("target_ea", 0))
            xref_type = str(row.get("xref_type", "") or "")
            row_direction = str(row.get("direction", "") or "")
            target_text = str(row.get("target_text", "") or "")
            lines.append(
                f"{base_no + idx}. {ref_loc} (xref_ea=0x{xref_ea:x}, type={xref_type}, direction={row_direction})"
                f" -> target=0x{target_ea:x} [{target_text}]"
            )
        return "\n".join(lines)
    except Exception as e:
        return f"ERROR: xref failed: {str(e)}"


@tool(parse_docstring=True, error_on_invalid_docstring=True)
def inspect_function_deep(
    function_name: str,
    include_pseudocode: bool = True,
    max_expr_samples: int = 120,
) -> str:
    """
    深度检查函数：伪代码 + AST 表达式样本 + 调用点 + 成员访问 + 局部变量

    Args:
        function_name: 目标函数名
        include_pseudocode: 是否包含伪代码
        max_expr_samples: AST 样本数量上限

    Returns:
        深度检查报告
    """
    client = get_ida_client()
    try:
        data = client.inspect_function_deep(
            function_name=function_name,
            include_pseudocode=include_pseudocode,
            max_expr_samples=max_expr_samples,
        )
        lines = [
            f"Function deep inspection: {function_name}",
            f"  ea=0x{int(data.get('ea', 0)):x}",
            f"  call_count={data.get('call_count', 0)}",
            f"  member_access_count={data.get('member_access_count', 0)}",
            f"  pointer_deref_count={data.get('pointer_deref_count', 0)}",
            f"  lvar_count={data.get('lvar_count', 0)}",
            f"  xrefs_to_count={data.get('xrefs_to_count', 0)}",
            "Top call sites:",
        ]
        for row in (data.get("calls", []) or [])[:20]:
            lines.append(
                "  "
                f"ea=0x{int(row.get('ea', 0)):x}, "
                f"callee={row.get('callee', '')}, "
                f"arg_count={row.get('arg_count', 0)}, "
                f"expr={row.get('expr_text', '')}"
            )
        if len(data.get("calls", []) or []) > 20:
            lines.append(f"  ... and {len(data.get('calls', []) or []) - 20} more call sites")
        _append_structured_section(lines, "Evidence:", data, max_items=20)
        return "\n".join(lines)
    except Exception as e:
        return f"ERROR: inspecting function deeply failed: {str(e)}"


@tool(parse_docstring=True, error_on_invalid_docstring=True)
def set_identifier_type(
    function_name: str,
    kind: str = "",
    c_type: str = "",
    name: str = "",
    index: int = -1,
    operations: Optional[List[Dict[str, Any]]] = None,
    allow_substring: bool = False,
    case_sensitive: bool = True,
    redecompile: bool = True,
) -> str:
    """
    统一设置标识符类型并强制重反编译函数（参数 / 局部变量 / 全局变量 / 返回值）

    Args:
        function_name: 目标函数名（所有改动完成后重反编译该函数）
        kind: 单条模式目标类型（parameter/local/global/return）
        c_type: 单条模式目标 C 类型
        name: 单条模式名称（参数名/局部变量名/全局变量名）
        index: 单条模式参数索引（仅 parameter）
        operations: 批量模式；每项支持 kind/c_type/name/index/allow_substring/case_sensitive/address
        allow_substring: 单条模式局部变量名是否允许子串匹配
        case_sensitive: 单条模式局部变量名匹配是否大小写敏感
        redecompile: 是否返回重反编译伪代码

    Returns:
        成功时返回简述 + 新伪代码；失败时返回 ERROR 原因
    """
    client = get_ida_client()
    try:
        data = client.set_identifier_type(
            function_name=function_name,
            kind=kind,
            c_type=c_type,
            name=name,
            index=index,
            operations=operations,
            allow_substring=allow_substring,
            case_sensitive=case_sensitive,
            redecompile=redecompile,
        )
        if not bool(data.get("success", False)):
            return f"ERROR: {data.get('error', 'set_identifier_type failed')}"

        rows = data.get("operations", []) if isinstance(data.get("operations"), list) else []
        changed_rows = [row for row in rows if bool(row.get("changed", False))]
        if changed_rows:
            desc = []
            for row in changed_rows[:8]:
                kind_text = str(row.get("kind", "") or "")
                if kind_text == "parameter":
                    target_text = f"参数#{int(row.get('index', -1))}"
                elif kind_text == "local":
                    target_text = f"局部变量 {str(row.get('name', '') or '').strip()}"
                elif kind_text == "global":
                    target_name = str(row.get("name", "") or "").strip()
                    ea = row.get("ea")
                    if target_name:
                        target_text = f"全局变量 {target_name}"
                    elif isinstance(ea, int):
                        target_text = f"全局变量@0x{int(ea):x}"
                    else:
                        target_text = "全局变量"
                else:
                    target_text = "返回值"
                desc.append(f"{target_text} -> {str(row.get('c_type', '') or '').strip()}")
            summary = "；".join(desc)
        else:
            summary = "目标类型与当前一致，未产生实际变更"

        lines = [
            f"已在函数 {function_name} 中完成类型设置：{summary}",
            f"mutation_effective={str(bool(data.get('mutation_effective', False))).lower()}",
        ]
        pseudocode = str(data.get("pseudocode", "") or "")
        if redecompile and pseudocode:
            lines.append("```c")
            lines.append(pseudocode.rstrip("\n"))
            lines.append("```")
        elif redecompile and data.get("decompile_success") is False:
            return f"ERROR: {data.get('error', 'decompile failed after type update')}"
        return "\n".join(lines)
    except Exception as e:
        return f"ERROR: setting identifier type failed: {str(e)}"


@tool(parse_docstring=True, error_on_invalid_docstring=True)
def create_structure(
    name: str,
    c_decl: str = "",
    fields: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """
    创建或更新 IDA 中的结构体定义（C 声明优先）
    
    Args:
        name: 结构体名称
        c_decl: 完整 C 结构体声明，例如 "struct Foo { uint32_t a; };"
        fields: 兼容字段列表。若未提供 c_decl，会尝试由 fields 生成声明。
    
    Returns:
        创建结果（包含 C 声明与 mutation_effective）
    
    Examples:
        >>> create_structure(
        ...     name="my_struct",
        ...     c_decl="struct my_struct { uint32_t field1; uint64_t field2; };",
        ... )
    """
    client = get_ida_client()
    try:
        has_c_decl = bool(str(c_decl or "").strip())
        has_fields = isinstance(fields, list) and bool(fields)
        if (not has_c_decl) and (not has_fields):
            return (
                "ERROR: invalid input. create_structure requires `c_decl` or a non-empty `fields` list. "
                "Example: create_structure(name='my_struct', c_decl='struct my_struct { uint32_t field0; };')."
            )

        data = client.create_structure_detailed(name=name, c_decl=c_decl, fields=fields or [])
        if not bool(data.get("success", False)):
            return f"ERROR: {data.get('error', 'create structure failed')}"

        rendered = str(data.get("c_declaration", "") or "").strip()
        if not rendered:
            return f"ERROR: structure '{name}' updated but failed to render C declaration"
        return (
            f"{rendered}\n"
            f"mutation_effective={str(bool(data.get('mutation_effective', False))).lower()}"
        )
    except Exception as e:
        return f"ERROR: creating structure failed: {str(e)}"


@tool(parse_docstring=True, error_on_invalid_docstring=True)
def get_xrefs_to(ea: int) -> str:
    """
    获取指向指定地址的所有交叉引用
    
    Args:
        ea: 目标地址（十进制或十六进制，如 0x1000）
    
    Returns:
        交叉引用列表
    
    Examples:
        >>> get_xrefs_to(0x1000)
        >>> get_xrefs_to(4096)
    """
    client = get_ida_client()
    try:
        xrefs = client.get_xrefs_to(ea) or []
        output = f"OK: found {len(xrefs)} xrefs to 0x{ea:x}\n"
        for xref in xrefs:
            output += f"  from 0x{xref['from']:x} (type: {xref['type']})\n"
        return output
    except Exception as e:
        return f"ERROR: getting xrefs to failed: {str(e)}"


@tool(parse_docstring=True, error_on_invalid_docstring=True)
def get_xrefs_from(ea: int) -> str:
    """
    获取从指定地址发出的所有交叉引用
    
    Args:
        ea: 源地址（十进制或十六进制，如 0x1000）
    
    Returns:
        交叉引用列表
    
    Examples:
        >>> get_xrefs_from(0x1000)
        >>> get_xrefs_from(4096)
    """
    client = get_ida_client()
    try:
        xrefs = client.get_xrefs_from(ea) or []
        output = f"OK: found {len(xrefs)} xrefs from 0x{ea:x}\n"
        for xref in xrefs:
            output += f"  to 0x{xref['to']:x} (type: {xref['type']})\n"
        return output
    except Exception as e:
        return f"ERROR: getting xrefs from failed: {str(e)}"


@tool(parse_docstring=True, error_on_invalid_docstring=True)
def get_database_info() -> str:
    """
    获取当前 IDA 数据库的基本信息
    
    Returns:
        数据库信息（路径、处理器、基址等）
    
    Examples:
        >>> get_database_info()
    """
    client = get_ida_client()
    try:
        info = client.get_db_info()
        lines = ["OK: database info"]
        _append_structured_section(lines, "Details:", info, max_items=40)
        return "\n".join(lines)
    except Exception as e:
        return f"ERROR: getting database info failed: {str(e)}"


@tool(parse_docstring=True, error_on_invalid_docstring=True)
def collect_allocations(function_name: str, include_pseudocode: bool = False) -> str:
    """
    收集函数中的内存分配与变量别名传播证据（malloc/calloc/new + alias）

    Args:
        function_name: 目标函数名
        include_pseudocode: 是否附带伪代码

    Returns:
        分配与别名报告
    """
    client = get_ida_client()
    try:
        report = client.collect_allocations(
            function_name=function_name,
            include_pseudocode=include_pseudocode,
        )
        if isinstance(report, dict) and report.get("error"):
            return f"ERROR: collecting allocations failed: {report['error']}"

        allocs = report.get("allocations", []) if isinstance(report, dict) else []
        aliases = report.get("aliases", []) if isinstance(report, dict) else []
        lines = [
            f"Allocation report for '{function_name}':",
            f"  allocation_count={len(allocs)}",
            f"  alias_count={len(aliases)}",
        ]
        if allocs:
            lines.append("Allocations:")
            for row in allocs[:30]:
                lines.append(
                    "  "
                    f"lhs={row.get('lhs', '')}, "
                    f"kind={row.get('alloc_kind', '')}, "
                    f"size_bytes={row.get('size_bytes')}, "
                    f"count={row.get('count')}, "
                    f"elem_size={row.get('elem_size')}, "
                    f"call={row.get('call_name', '')}, "
                    f"expr={row.get('expr_text', '')}"
                )
            if len(allocs) > 30:
                lines.append(f"  ... and {len(allocs) - 30} more allocations")
        if aliases:
            lines.append("Aliases (top 50):")
            for row in aliases[:50]:
                lines.append(
                    "  "
                    f"dst={row.get('dst', '')}, "
                    f"src={row.get('src', '')}, "
                    f"expr={row.get('expr_text', '')}"
                )
            if len(aliases) > 50:
                lines.append(f"  ... and {len(aliases) - 50} more aliases")
        _append_structured_section(lines, "Evidence:", report, max_items=24)
        return "\n".join(lines)
    except Exception as e:
        return f"ERROR: collecting allocations failed: {str(e)}"


@tool(parse_docstring=True, error_on_invalid_docstring=True)
def inspect_symbol_usage(
    function_name: str,
    include_pseudocode: bool = False,
    include_data_refs: bool = True,
) -> str:
    """
    检查函数符号使用：参数/局部变量/全局变量读写/数据引用

    Args:
        function_name: 目标函数名
        include_pseudocode: 是否返回伪代码
        include_data_refs: 是否返回指令级数据引用

    Returns:
        符号使用报告
    """
    client = get_ida_client()
    try:
        data = client.inspect_symbol_usage(
            function_name=function_name,
            include_pseudocode=include_pseudocode,
            include_data_refs=include_data_refs,
        )
        symbol_map: Dict[str, Dict[str, str]] = {}

        def _add_symbol(sym_name: Any, sym_type: Any, expr: Any) -> None:
            name_text = str(sym_name or "").strip()
            kind_text = str(sym_type or "").strip().lower()
            expr_text = str(expr or "").strip()
            if not name_text or kind_text not in {"function", "global", "local"}:
                return
            key = f"{kind_text}:{name_text}"
            if key in symbol_map:
                return
            symbol_map[key] = {
                "name": name_text,
                "type": kind_text,
                "expr": expr_text,
            }

        for row in (data.get("global_reads", []) or []):
            _add_symbol(
                row.get("name", ""),
                row.get("symbol_type", "global"),
                row.get("expr", ""),
            )
        for row in (data.get("global_writes", []) or []):
            _add_symbol(
                row.get("name", ""),
                row.get("symbol_type", "global"),
                row.get("expr", ""),
            )
        for row in (data.get("local_reads", []) or []):
            _add_symbol(
                row.get("var", ""),
                "local",
                row.get("expr", ""),
            )
        for row in (data.get("local_writes", []) or []):
            _add_symbol(
                row.get("var", ""),
                "local",
                row.get("expr", ""),
            )
        for row in (data.get("function_calls", []) or []):
            _add_symbol(
                row.get("name", ""),
                "function",
                row.get("expr", ""),
            )

        rows = list(symbol_map.values())
        type_order = {"function": 0, "global": 1, "local": 2}
        rows.sort(key=lambda item: (type_order.get(item.get("type", ""), 9), item.get("name", "")))
        if not rows:
            return f"Symbol usage for '{function_name}':\n(empty)"

        lines = [f"Symbol usage for '{function_name}':"]
        for row in rows:
            expr = str(row.get("expr", "") or "").strip()
            if expr:
                lines.append(
                    f"- {row.get('name', '')}, type: {row.get('type', '')}, expr: {expr}"
                )
            else:
                lines.append(
                    f"- {row.get('name', '')}, type: {row.get('type', '')}"
                )
        return "\n".join(lines)
    except Exception as e:
        return f"ERROR: inspecting symbol usage failed: {str(e)}"


def _normalize_function_names(function_names: List[str]) -> List[str]:
    normalized = []
    for name in function_names:
        text = str(name).strip()
        if text:
            normalized.append(text)
    # 保序去重
    seen = set()
    deduped = []
    for name in normalized:
        if name in seen:
            continue
        seen.add(name)
        deduped.append(name)
    return deduped


def _skills_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "skills"


def _ida_script_skills_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "ida_scripts" / "skills"


def _artifact_root_map() -> Dict[str, Path]:
    root = Path(__file__).resolve().parent.parent.parent
    return {
        "ida_scripts": (root / "src" / "ida_scripts").resolve(),
        "skills": (root / "src" / "skills").resolve(),
        "reference": (root / "reference").resolve(),
        "agent_reports": (root / "logs" / "agent_reports").resolve(),
    }


def _safe_relative_path(base: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(base.resolve()).as_posix()
    except Exception:
        return path.name


def _find_snippet(text: str, query: str, max_chars: int) -> str:
    body = str(text or "")
    q = str(query or "").strip()
    cap = max(120, int(max_chars))
    if not body:
        return ""
    if not q:
        return body[:cap]
    lower_body = body.lower()
    lower_q = q.lower()
    pos = lower_body.find(lower_q)
    if pos < 0:
        return ""
    half = max(40, cap // 2)
    start = max(0, pos - half)
    end = min(len(body), pos + len(q) + half)
    snippet = body[start:end]
    if start > 0:
        snippet = "... " + snippet
    if end < len(body):
        snippet = snippet + " ..."
    return snippet


@tool(parse_docstring=True, error_on_invalid_docstring=True)
def read_artifact(
    artifact_index: str = "",
    query: str = "",
    path_glob: str = "**/*",
    max_hits: int = 8,
    max_chars: int = 1200,
) -> str:
    """
    在白名单 artifact 索引中检索文本并返回片段证据。

    Args:
        artifact_index: 索引名（ida_scripts|skills|reference|agent_reports），为空表示全部索引
        query: 文本检索关键词
        path_glob: 文件匹配模式
        max_hits: 最多返回命中数量
        max_chars: 每条命中的最大片段长度

    Returns:
        纯文本 markdown 检索结果
    """
    idx = str(artifact_index or "").strip()
    q = str(query or "").strip()
    pattern = str(path_glob or "**/*").strip() or "**/*"
    if ".." in pattern or pattern.startswith("/") or pattern.startswith("\\"):
        return "ERROR: invalid path_glob; traversal and absolute patterns are not allowed"
    cap_hits = max(1, min(int(max_hits), 50))
    cap_chars = max(200, min(int(max_chars), 4000))
    roots = _artifact_root_map()

    if idx:
        root = roots.get(idx)
        if root is None:
            return f"ERROR: unknown artifact_index '{idx}'. valid={sorted(roots.keys())}"
        targets = [(idx, root)]
    else:
        targets = list(roots.items())

    hits: List[Dict[str, str]] = []
    scanned = 0
    for index_name, base in targets:
        if not base.exists() or (not base.is_dir()):
            continue
        try:
            candidates = list(base.glob(pattern))
        except Exception as e:
            return f"ERROR: invalid path_glob '{pattern}': {e}"
        for path in sorted(candidates):
            if len(hits) >= cap_hits:
                break
            if not path.is_file():
                continue
            scanned += 1
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            snippet = _find_snippet(text=text, query=q, max_chars=cap_chars)
            if q and (not snippet):
                continue
            if not q:
                snippet = snippet or text[:cap_chars]
            if not snippet.strip():
                continue
            hits.append(
                {
                    "index": index_name,
                    "path": _safe_relative_path(base, path),
                    "snippet": snippet.strip(),
                }
            )
        if len(hits) >= cap_hits:
            break

    lines = [
        "# Artifact Search",
        f"- artifact_index: {idx or 'all'}",
        f"- query: {q or '(none)'}",
        f"- path_glob: {pattern}",
        f"- scanned_files: {scanned}",
        f"- hit_count: {len(hits)}",
        "",
    ]
    if not hits:
        lines.append("No matching artifact content found.")
        lines.append("Try broader `path_glob` or shorter `query`.")
        return "\n".join(lines)

    lines.append("## Hits")
    for i, row in enumerate(hits, start=1):
        lines.append(f"### {i}. [{row['index']}] {row['path']}")
        lines.append("```text")
        lines.append(row["snippet"])
        lines.append("```")
    return "\n".join(lines)


@tool(parse_docstring=True, error_on_invalid_docstring=True)
def list_skill_templates(skill_name: Optional[str] = None) -> str:
    """
    列出可执行的 Skill 模板脚本（templates/*.py）

    Args:
        skill_name: 可选，指定某个 skill 名称

    Returns:
        模板列表
    """
    root = _skills_dir()
    if not root.exists():
        return f"Skills dir not found: {root}"

    skills: List[str]
    if skill_name:
        skills = [skill_name]
    else:
        skills = sorted([p.name for p in root.iterdir() if p.is_dir() and not p.name.startswith("__")])

    lines = [f"Skills templates under: {root}"]
    total = 0
    for name in skills:
        tpl_dir = root / name / "templates"
        if not tpl_dir.exists():
            continue
        files = sorted([p.name for p in tpl_dir.glob("*.py") if p.is_file()])
        if not files:
            continue
        total += len(files)
        lines.append(f"- {name}: {len(files)} templates")
        for filename in files[:50]:
            lines.append(f"  - {filename}")
        if len(files) > 50:
            lines.append(f"  - ... and {len(files) - 50} more")

    # Also expose generic IDAPython skill templates under src/ida_scripts/skills.
    generic_dir = _ida_script_skills_dir()
    if generic_dir.exists():
        generic_files = sorted([p.name for p in generic_dir.glob("*.py") if p.is_file()])
        if generic_files:
            lines.append(f"- ida_scripts.skills: {len(generic_files)} templates")
            for filename in generic_files[:50]:
                lines.append(f"  - {filename}")
            if len(generic_files) > 50:
                lines.append(f"  - ... and {len(generic_files) - 50} more")
            total += len(generic_files)
    lines.append(f"Total templates: {total}")
    return "\n".join(lines)


@tool(parse_docstring=True, error_on_invalid_docstring=True)
def run_skill_template(
    skill_name: str,
    template_name: str,
    context: Optional[Dict[str, Any]] = None,
) -> str:
    """
    执行 skill 模板脚本（用于 LLM 按任务动态选择模板）

    Args:
        skill_name: skill 名称（如 struct_recovery）
        template_name: 模板文件名（可省略 .py）
        context: 注入模板脚本的上下文变量

    Returns:
        执行结果
    """
    client = get_ida_client()
    try:
        skill = str(skill_name).strip()
        template = str(template_name).strip()
        if not skill or not template:
            return "ERROR: skill_name and template_name are required"
        if "/" in skill or ".." in skill or "/" in template or ".." in template:
            return "ERROR: invalid skill/template name"

        if not template.endswith(".py"):
            template = f"{template}.py"

        # Preferred location: src/skills/<skill>/templates/<template>.py
        path = _skills_dir() / skill / "templates" / template
        if not path.exists():
            # Fallback location: src/ida_scripts/skills/<template>.py
            # This keeps old call shape run_skill_template("function_ast_probe", "function_ast_probe", ...)
            # compatible with generic script skill profiles.
            fallback = _ida_script_skills_dir() / template
            if fallback.exists():
                path = fallback
            else:
                return f"ERROR: template not found: {path} (fallback tried: {fallback})"

        runtime_context = context or {}
        script = path.read_text(encoding="utf-8")
        script = _render_tokenized_template(script=script, variables=runtime_context)
        unresolved = _find_unresolved_tokens(script)
        if unresolved:
            return (
                "ERROR: unresolved template tokens in skill script.\n"
                f"Template: {path}\n"
                f"Unresolved tokens: {unresolved}\n"
                "Hint: pass required keys via context, e.g. {'FUNCTION_NAME': 'main'}."
            )

        result = client.execute_script(script=script, context=runtime_context)
        if result.get("success"):
            stdout = str(result.get("stdout") or "")
            stderr = str(result.get("stderr") or "")
            if _has_runtime_error_marker(stdout) or _has_runtime_error_marker(stderr):
                return (
                    "ERROR: runtime error detected while running skill template.\n"
                    f"Execution time: {result.get('execution_time', 0):.3f}s\n"
                    f"Stdout:\n{stdout}\n"
                    f"Stderr:\n{stderr}\n"
                )
            output = f"OK: run_skill_template\nExecution time: {result.get('execution_time', 0):.3f}s\n"
            if result.get("result") is not None:
                output += f"Result: {result.get('result')}\n"
            if stdout:
                output += f"Stdout:\n{stdout}\n"
            return output
        return f"ERROR: {result.get('stderr') or result.get('error') or 'unknown'}"
    except Exception as e:
        return f"ERROR: running skill template failed: {str(e)}"


@tool(parse_docstring=True, error_on_invalid_docstring=True)
def expand_call_path(
    function_names: List[str],
    max_depth: int = 1,
    include_thunks: bool = False,
) -> str:
    """
    展开函数调用路径（从入口函数列表向下游函数 BFS）

    Args:
        function_names: 入口函数名列表
        max_depth: 调用深度
        include_thunks: 是否包含 thunk/lib 函数

    Returns:
        调用路径摘要与原始数据
    """
    client = get_ida_client()
    try:
        names = _normalize_function_names(function_names)
        graph = client.expand_call_path(
            function_names=names,
            max_depth=max_depth,
            include_thunks=include_thunks,
        )
        nodes = graph.get("nodes", []) if isinstance(graph, dict) else []
        edges = graph.get("edges", []) if isinstance(graph, dict) else []
        node_map: Dict[int, Dict[str, Any]] = {}
        for row in nodes:
            if not isinstance(row, dict):
                continue
            node_map[int(row.get("ea", 0))] = row

        edges_by_from: Dict[int, List[int]] = {}
        for row in edges:
            if not isinstance(row, dict):
                continue
            from_ea = int(row.get("from_ea", 0))
            to_ea = int(row.get("to_ea", 0))
            if from_ea not in edges_by_from:
                edges_by_from[from_ea] = []
            if to_ea not in edges_by_from[from_ea]:
                edges_by_from[from_ea].append(to_ea)

        ordered_nodes = sorted(
            [row for row in nodes if isinstance(row, dict)],
            key=lambda item: (int(item.get("depth", 0)), int(item.get("ea", 0))),
        )
        lines: List[str] = []
        missing = graph.get("missing_entries", []) if isinstance(graph, dict) else []
        if isinstance(missing, list) and missing:
            lines.append(f"missing: {', '.join([str(x) for x in missing])}")
        for node in ordered_nodes:
            ea = int(node.get("ea", 0))
            name = str(node.get("name", "") or f"sub_{ea:x}")
            lines.append(f"{name}@0x{ea:x} -->")
            for to_ea in edges_by_from.get(ea, []):
                callee = node_map.get(int(to_ea), {})
                callee_name = str(callee.get("name", "") or f"sub_{int(to_ea):x}")
                lines.append(f"    {callee_name}@0x{int(to_ea):x}")
        return "\n".join(lines) if lines else "(empty)"
    except Exception as e:
        return f"ERROR: expanding call path failed: {str(e)}"


@tool(parse_docstring=True, error_on_invalid_docstring=True)
def inspect_symbol_usage_on_call_path(
    function_names: List[str],
    max_depth: int = 1,
    include_thunks: bool = False,
    include_pseudocode: bool = False,
    include_data_refs: bool = True,
) -> str:
    """
    在调用路径上批量检查参数/局部/全局符号使用

    Args:
        function_names: 入口函数名列表
        max_depth: 调用深度
        include_thunks: 是否包含 thunk/lib 函数
        include_pseudocode: 是否附带伪代码
        include_data_refs: 是否附带指令级数据引用

    Returns:
        调用路径符号使用报告
    """
    client = get_ida_client()
    try:
        names = _normalize_function_names(function_names)
        data = client.inspect_symbol_usage_on_call_path(
            function_names=names,
            max_depth=max_depth,
            include_thunks=include_thunks,
            include_pseudocode=include_pseudocode,
            include_data_refs=include_data_refs,
        )
        reports = data.get("reports", [])
        lines = [
            f"Symbol usage on call path entries={names}",
            f"  max_depth={data.get('max_depth', max_depth)}",
            f"  include_thunks={data.get('include_thunks', include_thunks)}",
            f"  scanned_function_count={data.get('scanned_function_count', 0)}",
            f"  report_count={len(reports)}",
            f"  error_count={data.get('error_count', 0)}",
            "Per-function (top 60):",
        ]
        for row in reports[:60]:
            lines.append(
                "  "
                f"function={row.get('function', '')}, "
                f"args={row.get('arg_count', 0)}, "
                f"locals={row.get('local_count', 0)}, "
                f"global_reads={row.get('global_read_count', 0)}, "
                f"global_writes={row.get('global_write_count', 0)}, "
                f"data_refs={row.get('data_ref_count', 0)}"
            )
        if len(reports) > 60:
            lines.append(f"  ... and {len(reports) - 60} more functions")
        lines.append("Global reads summary (top 40):")
        for row in (data.get("global_reads", []) or [])[:40]:
                lines.append(
                    "  "
                    f"ea=0x{int(row.get('ea', 0)):x}, name={row.get('name', '')}, "
                    f"function_count={row.get('function_count', 0)}, functions={','.join([str(x) for x in (row.get('functions', []) or [])])}"
                )
        lines.append("Global writes summary (top 40):")
        for row in (data.get("global_writes", []) or [])[:40]:
                lines.append(
                    "  "
                    f"ea=0x{int(row.get('ea', 0)):x}, name={row.get('name', '')}, "
                    f"function_count={row.get('function_count', 0)}, functions={','.join([str(x) for x in (row.get('functions', []) or [])])}"
                )
        _append_structured_section(lines, "Evidence:", data, max_items=24)
        return "\n".join(lines)
    except Exception as e:
        return f"ERROR: inspecting symbol usage on call path failed: {str(e)}"


# 默认导出：最小闭环工具集（分析 -> 建模 -> 应用类型 -> 验证）
CORE_TOOLS = [
    execute_idapython,
    read_artifact,
    decompile_function,
    search,
    xref,
    inspect_symbol_usage,
    create_structure,
    set_identifier_type,
    expand_call_path,
]

# 扩展工具：仅在 full profile 下暴露
OPTIONAL_TOOLS = [
    list_ida_script_templates,
    run_ida_script_template,
    get_function_info,
    list_all_functions,
    inspect_function_deep,
    get_xrefs_to,
    get_xrefs_from,
    get_database_info,
    list_skill_templates,
    run_skill_template,
    collect_allocations,
    inspect_symbol_usage_on_call_path,
]


def _build_tool_map(rows: List[Any]) -> Dict[str, Any]:
    return {tool.name: tool for tool in rows}


tools = list(CORE_TOOLS)
tool_map = _build_tool_map(tools)

full_tools = list(CORE_TOOLS) + list(OPTIONAL_TOOLS)
full_tool_map = _build_tool_map(full_tools)
