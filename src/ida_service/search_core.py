"""Core search/xref helpers for ida_service."""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Sequence, Tuple

try:
    import ida_funcs
    import idautils
    import idc

    HAS_IDA = True
except ImportError:
    HAS_IDA = False


_FLAG_MAP = {
    "I": re.IGNORECASE,
    "IGNORECASE": re.IGNORECASE,
    "M": re.MULTILINE,
    "MULTILINE": re.MULTILINE,
    "S": re.DOTALL,
    "DOTALL": re.DOTALL,
}

_SYMBOL_KIND = "symbol"
_STRING_KIND = "string"
_TARGET_TYPES = {"all", "symbol", "string"}
_XREF_TARGET_TYPES = {"symbol", "string", "ea"}
_XREF_DIRECTIONS = {"to", "from", "both"}


def _require_ida() -> None:
    if not HAS_IDA:
        raise RuntimeError("IDA modules unavailable in current runtime")


def _normalize_flags(flags: str) -> int:
    text = str(flags or "").strip()
    if not text:
        text = "IGNORECASE"
    value = 0
    for token in text.split("|"):
        key = str(token or "").strip().upper()
        if not key:
            continue
        if key not in _FLAG_MAP:
            raise ValueError(
                f"Unsupported regex flag '{key}'. allowed=IGNORECASE|MULTILINE|DOTALL"
            )
        value |= _FLAG_MAP[key]
    return value


def _compile_pattern(pattern: str, flags: str) -> re.Pattern:
    text = str(pattern or "")
    if not text.strip():
        raise ValueError("pattern is required")
    try:
        return re.compile(text, _normalize_flags(flags))
    except re.error as e:
        raise ValueError(f"invalid regex pattern: {e}") from e


def _normalize_page(offset: Any, count: Any) -> Tuple[int, int]:
    row_offset = int(offset)
    row_count = int(count)
    if row_offset < 0:
        raise ValueError("offset must be >= 0")
    row_count = max(1, min(row_count, 100))
    return row_offset, row_count


def _sort_key(item: Dict[str, Any]) -> Tuple[int, str, int]:
    kind = str(item.get("kind", ""))
    kind_order = 0 if kind == _SYMBOL_KIND else 1
    text = str(item.get("text", "") or "").lower()
    ea = int(item.get("ea", 0) or 0)
    return kind_order, text, ea


def _paginate(items: Sequence[Dict[str, Any]], offset: int, count: int) -> Dict[str, Any]:
    total_count = len(items)
    page = list(items[offset : offset + count])
    returned_count = len(page)
    next_offset = offset + returned_count if (offset + returned_count) < total_count else None
    return {
        "total_count": total_count,
        "returned_count": returned_count,
        "offset": offset,
        "count": count,
        "next_offset": next_offset,
        "has_more": bool(next_offset is not None),
        "items": page,
    }


def _iter_function_symbols() -> Iterable[Dict[str, Any]]:
    for func_ea in idautils.Functions():
        ea = int(func_ea)
        name = str(idc.get_func_name(ea) or "")
        if not name:
            continue
        yield {
            "kind": _SYMBOL_KIND,
            "subkind": "function",
            "ea": ea,
            "text": name,
        }


def _iter_global_symbols(function_eas: set) -> Iterable[Dict[str, Any]]:
    for ea, name in idautils.Names():
        row_ea = int(ea)
        if row_ea in function_eas:
            continue
        text = str(name or "")
        if not text:
            continue
        yield {
            "kind": _SYMBOL_KIND,
            "subkind": "global",
            "ea": row_ea,
            "text": text,
        }


def _iter_strings() -> Iterable[Dict[str, Any]]:
    for item in idautils.Strings():
        try:
            ea = int(item.ea)
            text = str(item)
        except Exception:
            continue
        if not text:
            continue
        yield {
            "kind": _STRING_KIND,
            "subkind": "literal",
            "ea": ea,
            "text": text,
        }


def _collect_symbols_for_match(pattern: re.Pattern) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    function_eas = set()
    for row in _iter_function_symbols():
        function_eas.add(int(row.get("ea", 0)))
        if pattern.search(str(row.get("text", ""))):
            rows.append(row)
    for row in _iter_global_symbols(function_eas):
        if pattern.search(str(row.get("text", ""))):
            rows.append(row)
    return rows


def _collect_strings_for_match(pattern: re.Pattern) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for row in _iter_strings():
        if pattern.search(str(row.get("text", ""))):
            rows.append(row)
    return rows


def search_symbols_strings(
    *,
    pattern: str,
    target_type: str = "all",
    offset: int = 0,
    count: int = 20,
    flags: str = "IGNORECASE",
) -> Dict[str, Any]:
    _require_ida()
    row_target_type = str(target_type or "all").strip().lower()
    if row_target_type not in _TARGET_TYPES:
        raise ValueError(f"target_type must be one of {sorted(_TARGET_TYPES)}")
    row_offset, row_count = _normalize_page(offset, count)
    regex = _compile_pattern(pattern, flags)

    rows: List[Dict[str, Any]] = []
    if row_target_type in {"all", "symbol"}:
        rows.extend(_collect_symbols_for_match(regex))
    if row_target_type in {"all", "string"}:
        rows.extend(_collect_strings_for_match(regex))

    dedup: Dict[Tuple[str, int, str], Dict[str, Any]] = {}
    for row in rows:
        key = (
            str(row.get("kind", "")),
            int(row.get("ea", 0)),
            str(row.get("text", "")),
        )
        if key not in dedup:
            dedup[key] = row

    ordered = sorted(dedup.values(), key=_sort_key)
    page = _paginate(ordered, row_offset, row_count)

    symbol_count = 0
    string_count = 0
    for row in ordered:
        if str(row.get("kind", "")) == _SYMBOL_KIND:
            symbol_count += 1
        elif str(row.get("kind", "")) == _STRING_KIND:
            string_count += 1

    page["query"] = {
        "pattern": str(pattern or ""),
        "target_type": row_target_type,
        "flags": str(flags or "IGNORECASE"),
    }
    page["summary"] = {
        "symbol_count": symbol_count,
        "string_count": string_count,
    }
    return page


def _parse_target_ea(value: str) -> int:
    text = str(value or "").strip()
    if not text:
        raise ValueError("target is required when target_type='ea'")
    try:
        return int(text, 0)
    except ValueError as e:
        raise ValueError(f"invalid ea target '{text}'") from e


def _build_ref_location(ea: int) -> Dict[str, Any]:
    row_ea = int(ea)
    func = ida_funcs.get_func(row_ea)
    if not func:
        return {
            "ref_func_name": "",
            "ref_func_start": None,
            "ref_offset": None,
            "ref_loc": f"ea=0x{row_ea:x}",
        }
    start_ea = int(func.start_ea)
    func_name = str(idc.get_func_name(start_ea) or f"sub_{start_ea:x}")
    offset = int(row_ea - start_ea)
    return {
        "ref_func_name": func_name,
        "ref_func_start": start_ea,
        "ref_offset": offset,
        "ref_loc": f"{func_name}+0x{offset:x}",
    }


def _target_rows(
    *,
    target: str,
    target_type: str,
    flags: str,
) -> List[Dict[str, Any]]:
    row_target_type = str(target_type or "").strip().lower()
    if row_target_type == "ea":
        ea = _parse_target_ea(target)
        return [{"target_kind": "ea", "target_text": f"0x{ea:x}", "target_ea": ea}]
    regex = _compile_pattern(target, flags)
    rows: List[Dict[str, Any]] = []
    if row_target_type == "symbol":
        for row in _collect_symbols_for_match(regex):
            rows.append(
                {
                    "target_kind": str(row.get("subkind", "symbol")),
                    "target_text": str(row.get("text", "")),
                    "target_ea": int(row.get("ea", 0)),
                }
            )
    elif row_target_type == "string":
        for row in _collect_strings_for_match(regex):
            rows.append(
                {
                    "target_kind": "string",
                    "target_text": str(row.get("text", "")),
                    "target_ea": int(row.get("ea", 0)),
                }
            )
    else:
        raise ValueError(f"target_type must be one of {sorted(_XREF_TARGET_TYPES)}")
    dedup: Dict[Tuple[int, str], Dict[str, Any]] = {}
    for row in rows:
        key = (int(row.get("target_ea", 0)), str(row.get("target_kind", "")))
        if key not in dedup:
            dedup[key] = row
    ordered = sorted(
        dedup.values(),
        key=lambda item: (
            str(item.get("target_kind", "")),
            str(item.get("target_text", "")).lower(),
            int(item.get("target_ea", 0)),
        ),
    )
    return ordered


def _xref_type_name(xref_type: Any) -> str:
    try:
        return str(idc.get_xref_type_name(int(xref_type)) or "")
    except Exception:
        return str(xref_type)


def search_xrefs(
    *,
    target: str,
    target_type: str,
    direction: str = "to",
    offset: int = 0,
    count: int = 20,
    flags: str = "IGNORECASE",
) -> Dict[str, Any]:
    _require_ida()
    row_direction = str(direction or "to").strip().lower()
    if row_direction not in _XREF_DIRECTIONS:
        raise ValueError(f"direction must be one of {sorted(_XREF_DIRECTIONS)}")
    row_offset, row_count = _normalize_page(offset, count)
    targets = _target_rows(target=target, target_type=target_type, flags=flags)

    items: List[Dict[str, Any]] = []
    for target_row in targets:
        target_ea = int(target_row.get("target_ea", 0))
        target_text = str(target_row.get("target_text", ""))
        target_kind = str(target_row.get("target_kind", ""))

        if row_direction in {"to", "both"}:
            for xref in idautils.XrefsTo(target_ea):
                from_ea = int(xref.frm)
                loc = _build_ref_location(from_ea)
                items.append(
                    {
                        "direction": "to",
                        "target_kind": target_kind,
                        "target_text": target_text,
                        "target_ea": target_ea,
                        "xref_ea": from_ea,
                        "from_ea": from_ea,
                        "to_ea": target_ea,
                        "xref_type": _xref_type_name(xref.type),
                        "ref_func_name": loc["ref_func_name"],
                        "ref_func_start": loc["ref_func_start"],
                        "ref_offset": loc["ref_offset"],
                        "ref_loc": loc["ref_loc"],
                    }
                )

        if row_direction in {"from", "both"}:
            for xref in idautils.XrefsFrom(target_ea):
                to_ea = int(xref.to)
                loc = _build_ref_location(to_ea)
                items.append(
                    {
                        "direction": "from",
                        "target_kind": target_kind,
                        "target_text": target_text,
                        "target_ea": target_ea,
                        "xref_ea": to_ea,
                        "from_ea": target_ea,
                        "to_ea": to_ea,
                        "xref_type": _xref_type_name(xref.type),
                        "ref_func_name": loc["ref_func_name"],
                        "ref_func_start": loc["ref_func_start"],
                        "ref_offset": loc["ref_offset"],
                        "ref_loc": loc["ref_loc"],
                    }
                )

    dedup: Dict[Tuple[str, int, int, str], Dict[str, Any]] = {}
    for row in items:
        key = (
            str(row.get("direction", "")),
            int(row.get("target_ea", 0)),
            int(row.get("xref_ea", 0)),
            str(row.get("xref_type", "")),
        )
        if key not in dedup:
            dedup[key] = row

    ordered = sorted(
        dedup.values(),
        key=lambda row: (
            str(row.get("target_kind", "")),
            str(row.get("target_text", "")).lower(),
            int(row.get("target_ea", 0)),
            str(row.get("direction", "")),
            str(row.get("ref_loc", "")).lower(),
            int(row.get("xref_ea", 0)),
        ),
    )
    page = _paginate(ordered, row_offset, row_count)
    page["query"] = {
        "target": str(target or ""),
        "target_type": str(target_type or "").strip().lower(),
        "direction": row_direction,
        "flags": str(flags or "IGNORECASE"),
    }
    page["resolved_target_count"] = len(targets)
    return page
