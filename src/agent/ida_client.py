"""IDA Service HTTP 客户端"""
from pathlib import Path
import requests
from typing import Dict, Any, Optional, List


class IDAClient:
    """IDA Service HTTP 客户端 - 封装所有 API 调用"""
    
    def __init__(self, base_url: str = "http://127.0.0.1:5000", timeout: int = 300):
        """
        初始化 IDA 客户端
        
        Args:
            base_url: IDA Service 的基础 URL
            timeout: 请求超时时间（秒）
        """
        self.base_url = base_url.rstrip('/')
        self.timeout = timeout
        self.script_template_dir = (
            Path(__file__).resolve().parent.parent / "ida_scripts"
        )

    def _resolve_script_template_path(self, template_name: str) -> Path:
        name = str(template_name or "").strip()
        if not name:
            raise ValueError("template_name is required")
        if "\\" in name:
            raise ValueError("template_name must use '/' separators")
        base = self.script_template_dir.resolve()
        candidate = (base / name).resolve()
        try:
            candidate.relative_to(base)
        except ValueError as e:
            raise ValueError(f"Invalid template path: {template_name}") from e
        if not candidate.exists() or not candidate.is_file():
            raise FileNotFoundError(f"Script template not found: {candidate}")
        return candidate

    def _render_script_template(self, template_name: str, variables: Dict[str, Any]) -> str:
        path = self._resolve_script_template_path(template_name)
        script = path.read_text(encoding="utf-8")
        for key, value in variables.items():
            token = f"__{key}__"
            script = script.replace(token, repr(value))
        return script

    def list_script_templates(self, pattern: str = "*.py") -> List[str]:
        """
        列出 ida_scripts 下可执行模板

        Args:
            pattern: 文件匹配模式（例如 *.py, collect_*.py）
        """
        normalized = str(pattern or "*.py").strip() or "*.py"
        files = []
        for path in self.script_template_dir.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(self.script_template_dir).as_posix()
            if path.match(normalized):
                files.append(rel)
        files.sort()
        return files

    def execute_script_template(
        self,
        template_name: str,
        variables: Optional[Dict[str, Any]] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        执行 ida_scripts 目录中的模板脚本

        Args:
            template_name: 模板文件路径（相对 ida_scripts）
            variables: __TOKEN__ 替换变量
            context: 运行时上下文变量（传给 execute 接口）
        """
        script = self._render_script_template(template_name, variables or {})
        return self.execute_script(script=script, context=context or {})

    def health_check(self) -> Dict[str, Any]:
        """健康检查"""
        response = requests.get(f"{self.base_url}/health", timeout=5)
        response.raise_for_status()
        return response.json()
    
    def open_database(self, db_path: str) -> Dict[str, Any]:
        """
        已弃用：数据库应在服务启动时通过 --idb 打开。
        """
        raise RuntimeError(
            "open_database endpoint is removed. "
            "Start IDA Service with --idb (or IDA_DEFAULT_IDB_PATH) instead."
        )
    
    def get_db_info(self) -> Dict[str, Any]:
        """获取当前数据库信息"""
        response = requests.get(f"{self.base_url}/db/info", timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    def backup_database(
        self,
        backup_dir: Optional[str] = None,
        tag: str = "",
        filename: str = "",
    ) -> Dict[str, Any]:
        """
        请求 ida_service 备份当前 IDB。

        Args:
            backup_dir: 备份目录（可选）
            tag: 备份标签（可选）
            filename: 目标文件名（可选，不带扩展名时会自动补）
        """
        payload: Dict[str, Any] = {}
        if backup_dir:
            payload["backup_dir"] = str(backup_dir)
        if tag:
            payload["tag"] = str(tag)
        if filename:
            payload["filename"] = str(filename)

        try:
            response = requests.post(
                f"{self.base_url}/db/backup",
                json=payload,
                timeout=self.timeout,
            )
            response.raise_for_status()
            result = response.json()
            if not result.get("success"):
                raise Exception(result.get("error", "backup_database request failed"))

            data = result.get("result")
            if not isinstance(data, dict):
                raise Exception(f"Unexpected backup_database payload type: {type(data).__name__}")
            if not data.get("success"):
                raise Exception(data.get("error", "IDB backup failed"))
            return data
        except requests.HTTPError as e:
            status = e.response.status_code if e.response is not None else 0
            if int(status) != 404:
                raise
            # Backward-compatible fallback for old ida_service without /db/backup.
            return self._backup_database_via_execute(
                backup_dir=backup_dir,
                tag=tag,
                filename=filename,
            )

    def _backup_database_via_execute(
        self,
        backup_dir: Optional[str] = None,
        tag: str = "",
        filename: str = "",
    ) -> Dict[str, Any]:
        script = r'''
import os
import re
import time
import shutil
import idc


def _safe_name(text):
    value = re.sub(r"[^0-9A-Za-z._-]+", "_", str(text or "").strip())
    return value.strip("_")


current_path = str(idc.get_idb_path() or "").strip()
if not current_path:
    __result__ = {"success": False, "error": "empty current idb path"}
else:
    requested_dir = str(backup_dir or "").strip()
    requested_tag = _safe_name(tag)
    requested_filename = str(filename or "").strip()

    if not requested_dir:
        requested_dir = os.path.join(os.path.dirname(current_path), "backups")
    os.makedirs(requested_dir, exist_ok=True)

    base_name = os.path.basename(current_path)
    stem, ext = os.path.splitext(base_name)
    if not ext:
        ext = ".i64"

    if requested_filename:
        target_path = os.path.join(requested_dir, requested_filename)
        if not os.path.splitext(target_path)[1]:
            target_path = target_path + ext
    else:
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        suffix = requested_tag or "backup"
        target_path = os.path.join(requested_dir, f"{stem}_{timestamp}_{suffix}{ext}")

    save_ok = False
    save_method = ""
    save_error = ""
    try:
        save_ok = bool(idc.save_database(target_path, 0))
        if save_ok:
            save_method = "idc.save_database"
    except Exception as e:
        save_error = str(e)
        save_ok = False

    if not save_ok:
        try:
            shutil.copy2(current_path, target_path)
            save_ok = True
            save_method = "shutil.copy2"
        except Exception as e:
            if save_error:
                save_error = f"{save_error}; copy2: {e}"
            else:
                save_error = str(e)

    __result__ = {
        "success": bool(save_ok),
        "source_path": current_path,
        "backup_path": target_path,
        "method": save_method,
        "error": save_error if not save_ok else "",
    }
'''
        result = self.execute_script(
            script=script,
            context={
                "backup_dir": str(backup_dir or ""),
                "tag": str(tag or ""),
                "filename": str(filename or ""),
            },
        )
        if not result.get("success"):
            raise Exception(result.get("stderr") or result.get("error") or "backup via execute failed")
        data = result.get("result")
        if not isinstance(data, dict):
            raise Exception(f"Unexpected backup-via-execute payload type: {type(data).__name__}")
        if not data.get("success"):
            raise Exception(data.get("error", "IDB backup failed"))
        data["method"] = data.get("method", "") or "execute_script_fallback"
        return data

    def take_database_snapshot(self, description: str) -> Dict[str, Any]:
        """
        使用 IDA 内置快照功能创建数据库快照。

        Args:
            description: 快照描述
        """
        script = r'''
import time
import ida_loader
import ida_kernwin


desc = str(snapshot_desc or "").strip()
if not desc:
    desc = "snapshot_" + time.strftime("%Y%m%d_%H%M%S")

snap = ida_loader.snapshot_t()
snap.desc = desc
ok = bool(ida_kernwin.take_database_snapshot(snap))
if not ok:
    __result__ = {"success": False, "error": "take_database_snapshot returned false", "desc": desc}
else:
    root = ida_loader.snapshot_t()
    built = bool(ida_loader.build_snapshot_tree(root))
    matched = None
    total = 0
    if built:
        queue = [root]
        visited = 0
        while queue and visited < 1000:
            node = queue.pop(0)
            visited += 1
            total += 1
            if str(getattr(node, "desc", "")) == desc:
                matched = node
            try:
                queue.extend(list(node.children))
            except Exception:
                pass

    item = {
        "desc": desc,
        "id": int(getattr(matched, "id", 0)) if matched is not None else int(getattr(snap, "id", 0)),
        "filename": str(getattr(matched, "filename", "")) if matched is not None else "",
        "flags": int(getattr(matched, "flags", 0)) if matched is not None else int(getattr(snap, "flags", 0)),
    }
    __result__ = {
        "success": True,
        "snapshot": item,
        "tree_node_count": int(total),
    }
'''
        result = self.execute_script(script=script, context={"snapshot_desc": str(description or "")})
        if not result.get("success"):
            raise Exception(result.get("stderr") or result.get("error") or "take_database_snapshot failed")
        data = result.get("result")
        if not isinstance(data, dict):
            raise Exception(f"Unexpected take_database_snapshot payload type: {type(data).__name__}")
        if not data.get("success"):
            raise Exception(data.get("error", "take_database_snapshot failed"))
        return data
    
    def execute_script(self, script: str, context: Optional[Dict] = None) -> Dict[str, Any]:
        """
        执行 IDAPython 脚本
        
        Args:
            script: IDAPython 代码
            context: 可选的上下文变量
            
        Returns:
            执行结果，包含 success, result, stdout, stderr, execution_time
        """
        response = requests.post(
            f"{self.base_url}/execute",
            json={"script": script, "context": context or {}},
            timeout=self.timeout
        )
        response.raise_for_status()
        return response.json()
    
    def list_functions(self) -> List[Dict[str, Any]]:
        """
        列出所有函数
        
        Returns:
            函数列表，每个函数包含 ea, name, size
        """
        response = requests.get(f"{self.base_url}/functions", timeout=self.timeout)
        response.raise_for_status()
        result = response.json()
        if result.get('success'):
            payload = result.get('result', [])
            if isinstance(payload, list):
                return payload
            if isinstance(payload, dict) and payload.get("error"):
                raise Exception(payload["error"])
            raise Exception(f"Unexpected /functions result type: {type(payload).__name__}")
        raise Exception(result.get('error', 'Failed to list functions'))
    
    def decompile_function(
        self,
        function_name: Optional[str] = None,
        ea: Optional[int] = None,
        name: Optional[str] = None,
        addr: Optional[int] = None,
    ) -> str:
        """
        反编译函数
        
        Args:
            function_name: 函数名称
            ea: 函数地址（二选一）
            name: 兼容参数，等价于 function_name
            addr: 兼容参数，等价于 ea
            
        Returns:
            反编译后的伪代码字符串
        """
        resolved_name = function_name or name
        resolved_ea = ea if ea is not None else addr
        if not resolved_name and resolved_ea is None:
            raise ValueError("Either 'function_name' or 'ea' must be provided")
        
        data = {}
        if resolved_name:
            data["function_name"] = resolved_name
        if resolved_ea is not None:
            data["ea"] = int(resolved_ea)
        
        response = requests.post(
            f"{self.base_url}/decompile",
            json=data,
            timeout=self.timeout
        )
        response.raise_for_status()
        result = response.json()
        if result.get('success'):
            return result.get('result', '')
        raise Exception(result.get('error', 'Failed to decompile function'))

    def search(
        self,
        pattern: str,
        target_type: str = "all",
        offset: int = 0,
        count: int = 20,
        flags: str = "IGNORECASE",
    ) -> Dict[str, Any]:
        """
        搜索符号与字符串（核心逻辑由 ida_service 提供）

        Args:
            pattern: Python re 正则表达式
            target_type: all|symbol|string
            offset: 结果偏移
            count: 结果数量
            flags: IGNORECASE|MULTILINE|DOTALL
        """
        payload = {
            "pattern": str(pattern or ""),
            "target_type": str(target_type or "all"),
            "offset": int(offset),
            "count": int(count),
            "flags": str(flags or "IGNORECASE"),
        }
        response = requests.post(
            f"{self.base_url}/search",
            json=payload,
            timeout=self.timeout,
        )
        response.raise_for_status()
        result = response.json()
        if result.get("success"):
            data = result.get("result")
            if isinstance(data, dict):
                return data
            raise Exception(f"Unexpected /search payload type: {type(data).__name__}")
        raise Exception(result.get("error", "Failed to search"))

    def xrefs(
        self,
        target: str,
        target_type: str,
        direction: str = "to",
        offset: int = 0,
        count: int = 20,
        flags: str = "IGNORECASE",
    ) -> Dict[str, Any]:
        """
        搜索交叉引用（核心逻辑由 ida_service 提供）

        Args:
            target: 查询目标（symbol/string/ea）
            target_type: symbol|string|ea
            direction: to|from|both
            offset: 结果偏移
            count: 结果数量
            flags: IGNORECASE|MULTILINE|DOTALL
        """
        payload = {
            "target": str(target or ""),
            "target_type": str(target_type or ""),
            "direction": str(direction or "to"),
            "offset": int(offset),
            "count": int(count),
            "flags": str(flags or "IGNORECASE"),
        }
        response = requests.post(
            f"{self.base_url}/xrefs",
            json=payload,
            timeout=self.timeout,
        )
        response.raise_for_status()
        result = response.json()
        if result.get("success"):
            data = result.get("result")
            if isinstance(data, dict):
                return data
            raise Exception(f"Unexpected /xrefs payload type: {type(data).__name__}")
        raise Exception(result.get("error", "Failed to search xrefs"))

    def inspect_symbol_usage(
        self,
        function_name: str,
        include_pseudocode: bool = False,
        include_data_refs: bool = True,
    ) -> Dict[str, Any]:
        """
        检查函数符号使用：参数/局部变量/全局变量读写/数据引用

        Args:
            function_name: 目标函数名
            include_pseudocode: 是否返回伪代码
            include_data_refs: 是否返回指令级数据引用
        """
        script = self._render_script_template(
            "inspect_symbol_usage.py",
            {
                "FUNCTION_NAME": str(function_name),
                "INCLUDE_PSEUDOCODE": bool(include_pseudocode),
                "INCLUDE_DATA_REFS": bool(include_data_refs),
            },
        )
        result = self.execute_script(script=script)
        if result.get("success"):
            payload = result.get("result")
            if isinstance(payload, dict):
                return payload
            raise Exception(f"Unexpected inspect_symbol_usage payload type: {type(payload).__name__}")
        raise Exception(result.get("stderr") or result.get("error") or "Failed to inspect symbol usage")

    def set_identifier_type(
        self,
        function_name: str,
        kind: str = "",
        c_type: str = "",
        name: str = "",
        index: int = -1,
        operations: Optional[List[Dict[str, Any]]] = None,
        redecompile: bool = True,
        allow_substring: bool = False,
        case_sensitive: bool = True,
    ) -> Dict[str, Any]:
        """
        统一设置标识符类型（参数/局部变量/全局变量/返回值），并可选重反编译目标函数。

        Args:
            function_name: 目标函数名（执行后会重反编译该函数）
            kind: 单条模式下的类型目标（parameter/local/global/return）
            c_type: 单条模式下目标 C 类型
            name: 单条模式下标识符名称（参数名/局部变量名/全局变量名）
            index: 单条模式下参数索引（仅 parameter 生效）
            operations: 批量模式；每项支持 kind/c_type/name/index/allow_substring/case_sensitive/address
            redecompile: 是否返回重反编译伪代码
            allow_substring: 单条模式下局部变量名是否允许子串匹配
            case_sensitive: 单条模式下局部变量名匹配是否大小写敏感
        """
        ops: List[Dict[str, Any]] = []
        if isinstance(operations, list) and operations:
            for item in operations:
                if not isinstance(item, dict):
                    continue
                row: Dict[str, Any] = {
                    "kind": str(item.get("kind", "") or "").strip().lower(),
                    "c_type": str(item.get("c_type", "") or "").strip(),
                    "name": str(item.get("name", "") or "").strip(),
                    "index": int(item.get("index", -1)),
                    "allow_substring": bool(item.get("allow_substring", False)),
                    "case_sensitive": bool(item.get("case_sensitive", True)),
                }
                if "address" in item:
                    row["address"] = item.get("address")
                ops.append(row)
        else:
            ops.append(
                {
                    "kind": str(kind or "").strip().lower(),
                    "c_type": str(c_type or "").strip(),
                    "name": str(name or "").strip(),
                    "index": int(index),
                    "allow_substring": bool(allow_substring),
                    "case_sensitive": bool(case_sensitive),
                }
            )

        script = self._render_script_template(
            "set_identifier_type.py",
            {
                "FUNCTION_NAME": str(function_name),
                "OPERATIONS": ops,
                "REDECOMPILE": bool(redecompile),
            },
        )
        result = self.execute_script(script=script)
        if result.get("success"):
            payload = result.get("result")
            if isinstance(payload, dict):
                return payload
            raise Exception(f"Unexpected set_identifier_type payload type: {type(payload).__name__}")
        raise Exception(
            result.get("stderr")
            or result.get("error")
            or "Failed to set identifier type and redecompile"
        )
    
    def get_function_info(self, name: Optional[str] = None, addr: Optional[int] = None) -> Dict[str, Any]:
        """
        获取函数信息（通过执行脚本）
        
        Args:
            name: 函数名称
            addr: 函数地址
            
        Returns:
            函数信息字典
        """
        if name:
            script = self._render_script_template(
                "get_function_info_by_name.py",
                {"FUNCTION_NAME": str(name)},
            )
        elif addr:
            script = self._render_script_template(
                "get_function_info_by_addr.py",
                {"FUNCTION_ADDR": int(addr)},
            )
        else:
            raise ValueError("Either 'name' or 'addr' must be provided")
        
        result = self.execute_script(script)
        if result.get('success'):
            return result.get('result')
        raise Exception(result.get('error', 'Failed to get function info'))
    
    def get_xrefs_to(self, ea: int) -> List[Dict[str, Any]]:
        """
        获取指向指定地址的交叉引用
        
        Args:
            ea: 目标地址
            
        Returns:
            交叉引用列表
        """
        script = self._render_script_template(
            "get_xrefs_to.py",
            {"TARGET_EA": int(ea)},
        )
        result = self.execute_script(script)
        if result.get('success'):
            return result.get('result', [])
        raise Exception(result.get('error', 'Failed to get xrefs to'))
    
    def get_xrefs_from(self, ea: int) -> List[Dict[str, Any]]:
        """
        获取从指定地址发出的交叉引用
        
        Args:
            ea: 源地址
            
        Returns:
            交叉引用列表
        """
        script = self._render_script_template(
            "get_xrefs_from.py",
            {"SOURCE_EA": int(ea)},
        )
        result = self.execute_script(script)
        if result.get('success'):
            return result.get('result', [])
        raise Exception(result.get('error', 'Failed to get xrefs from'))
    
    @staticmethod
    def _field_decl_from_size(size: int) -> str:
        value = int(size or 0)
        if value == 1:
            return "uint8_t"
        if value == 2:
            return "uint16_t"
        if value == 4:
            return "uint32_t"
        if value == 8:
            return "uint64_t"
        return f"uint8_t[{max(1, value)}]"

    def _build_c_decl_from_fields(self, name: str, fields: List[Dict[str, Any]]) -> str:
        rows: List[Dict[str, Any]] = []
        for item in fields:
            if not isinstance(item, dict):
                continue
            rows.append(
                {
                    "name": str(item.get("name", "") or ""),
                    "offset": int(item.get("offset", 0)),
                    "size": int(item.get("size", 0) or 0),
                }
            )
        rows = [row for row in rows if int(row["offset"]) >= 0 and int(row["size"]) > 0]
        rows.sort(key=lambda row: int(row["offset"]))
        if not rows:
            return ""

        lines = [f"struct {str(name)} {{"]
        cursor = 0
        pad_idx = 0
        for row in rows:
            offset = int(row["offset"])
            size = int(row["size"])
            if offset < cursor:
                return ""
            if offset > cursor:
                gap = offset - cursor
                lines.append(f"    uint8_t _pad_{pad_idx:x}[{gap}];")
                pad_idx += 1
                cursor = offset
            field_name = str(row["name"] or f"field_{offset:x}")
            lines.append(f"    {self._field_decl_from_size(size)} {field_name};")
            cursor += size
        lines.append("};")
        return "\n".join(lines)

    def create_structure_detailed(
        self,
        name: str,
        c_decl: str = "",
        fields: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """创建或更新结构体并返回详细结果。"""
        normalized_fields: List[Dict[str, Any]] = []
        for field in (fields or []):
            if not isinstance(field, dict):
                continue
            normalized_fields.append(
                {
                    "name": str(field.get("name", "") or ""),
                    "offset": int(field.get("offset", 0)),
                    "size": int(field.get("size", 0) or 0),
                    "type": str(field.get("type", "FF_DWORD")),
                }
            )

        rendered_c_decl = str(c_decl or "").strip()
        if (not rendered_c_decl) and normalized_fields:
            rendered_c_decl = self._build_c_decl_from_fields(str(name), normalized_fields)

        script = self._render_script_template(
            "create_structure.py",
            {
                "STRUCT_NAME": str(name),
                "C_DECL": str(rendered_c_decl),
                "FIELDS": normalized_fields,
            },
        )
        result = self.execute_script(script)
        if not result.get("success"):
            return {
                "success": False,
                "error": result.get("stderr") or result.get("error") or "Failed to create structure",
                "mutation_effective": False,
            }
        stdout_text = str(result.get("stdout") or "")
        stderr_text = str(result.get("stderr") or result.get("error") or "")
        runtime_error = (
            ("[ERROR]" in stdout_text)
            or ("Traceback (most recent call last):" in stdout_text)
            or ("[ERROR]" in stderr_text)
            or ("Traceback (most recent call last):" in stderr_text)
        )
        if runtime_error:
            detail = (stderr_text.strip() or stdout_text.strip() or "runtime error in create_structure script")
            return {
                "success": False,
                "error": detail,
                "mutation_effective": False,
            }
        payload = result.get("result")
        if not isinstance(payload, dict):
            snippets = []
            if stderr_text.strip():
                snippets.append(f"stderr={stderr_text.strip()[:800]}")
            if stdout_text.strip():
                snippets.append(f"stdout={stdout_text.strip()[:800]}")
            detail_text = ("; ".join(snippets)).strip()
            return {
                "success": False,
                "error": (
                    f"Unexpected create_structure payload type: {type(payload).__name__}"
                    + (f" ({detail_text})" if detail_text else "")
                ),
                "mutation_effective": False,
            }
        payload.setdefault("success", False)
        payload.setdefault("mutation_effective", False)
        return payload

    def create_structure(
        self,
        name: str,
        c_decl: str = "",
        fields: Optional[List[Dict[str, Any]]] = None,
    ) -> bool:
        """
        创建或更新结构体
        
        Args:
            name: 结构体名称
            c_decl: 完整 C 结构体声明
            fields: 兼容模式字段列表，会转换为 C 声明
            
        Returns:
            是否成功
        """
        data = self.create_structure_detailed(name=name, c_decl=c_decl, fields=fields)
        if not isinstance(data, dict):
            return False
        if not bool(data.get("success", False)):
            return False
        return True

    def expand_call_path(
        self,
        function_names: List[str],
        max_depth: int = 1,
        include_thunks: bool = False,
    ) -> Dict[str, Any]:
        """
        从入口函数列表展开调用路径（BFS）

        Args:
            function_names: 入口函数名列表
            max_depth: 展开深度
            include_thunks: 是否包含 thunk/lib 函数
        """
        normalized = [str(name).strip() for name in function_names if str(name).strip()]
        if not normalized:
            return {
                "entries": [],
                "resolved_entries": [],
                "missing_entries": [],
                "max_depth": int(max_depth),
                "include_thunks": bool(include_thunks),
                "nodes": [],
                "edges": [],
            }

        script = self._render_script_template(
            "expand_call_path.py",
            {
                "ENTRY_NAMES": normalized,
                "MAX_DEPTH": int(max_depth),
                "INCLUDE_THUNKS": bool(include_thunks),
            },
        )
        result = self.execute_script(script)
        if result.get("success"):
            payload = result.get("result")
            if isinstance(payload, dict):
                return payload
            raise Exception(f"Unexpected call path payload type: {type(payload).__name__}")
        raise Exception(result.get("stderr") or result.get("error") or "Failed to expand call path")

    def close(self):
        """关闭会话"""
        return None
