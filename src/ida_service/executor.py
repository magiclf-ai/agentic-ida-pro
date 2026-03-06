"""IDAPython 脚本执行器 - 核心模块"""
import os
import sys
import tempfile
import time
import traceback
import io
import re
import ast
from typing import Dict, Any, Optional, Tuple
from contextlib import redirect_stdout, redirect_stderr

# IDA 模块导入（在 idalib 环境中）
try:
    import idapro
    import ida_idaapi
    import idaapi
    import idc
    import idautils
    HAS_IDA = True
except ImportError:
    HAS_IDA = False
    print("[WARNING] IDA modules not available. Running in mock mode.")

from . import config


class ScriptExecutor:
    """IDAPython 脚本执行器"""
    
    def __init__(self):
        self.debug_mode = config.DEBUG_MODE
        self.script_timeout = config.SCRIPT_TIMEOUT
        self.execution_count = 0
        
    def execute(self, code: str, context: Optional[Dict] = None) -> Dict[str, Any]:
        """
        执行 IDAPython 脚本
        
        Args:
            code: IDAPython 代码字符串
            context: 可选的上下文变量
            
        Returns:
            {
                "success": bool,
                "result": Any,       # 脚本的 __result__ 变量值
                "stdout": str,       # 标准输出
                "stderr": str,       # 标准错误
                "execution_time": float,
                "script_path": str   # 临时脚本路径（调试用）
            }
        """
        start_time = time.time()
        self.execution_count += 1
        
        # 准备脚本内容
        script_content = self._prepare_script(code, context)
        
        # 创建临时脚本文件
        script_path = self._create_temp_script(script_content)
        
        try:
            # 执行脚本
            result = self._execute_script_file(script_path)
            result["execution_time"] = time.time() - start_time
            result["script_path"] = script_path
            
            # 非调试模式下删除临时文件
            if not self.debug_mode:
                try:
                    os.unlink(script_path)
                except:
                    pass
                    
            return result
            
        except Exception as e:
            return {
                "success": False,
                "result": None,
                "stdout": "",
                "stderr": traceback.format_exc(),
                "execution_time": time.time() - start_time,
                "script_path": script_path
            }
    
    def _prepare_script(self, code: str, context: Optional[Dict] = None) -> str:
        """准备脚本内容，添加上下文和结果捕获"""
        lines = []
        lines.append(f"# Auto-generated IDA Script")
        lines.append(f"# Execution ID: {self.execution_count}")
        lines.append("")
        lines.append("import sys")
        lines.append("import traceback")
        lines.append("")
        lines.append("# 确保 IDA 模块可用")
        lines.append("try:")
        lines.append("    import idapro")
        lines.append("    import ida_idaapi")
        lines.append("    import idaapi")
        lines.append("    import idc")
        lines.append("    import idautils")
        lines.append("    import ida_hexrays")
        lines.append("    import ida_funcs")
        lines.append("    import ida_name")
        lines.append("    import ida_typeinf")
        lines.append("    import ida_bytes")
        lines.append("    try:")
        lines.append("        import ida_struct")
        lines.append("    except ImportError:")
        lines.append("        ida_struct = None")
        lines.append("    try:")
        lines.append("        import ida_kernwin")
        lines.append("    except ImportError:")
        lines.append("        ida_kernwin = None")
        lines.append("except ImportError as e:")
        lines.append('    print("[ERROR] Failed to import IDA modules: " + str(e))')
        lines.append("    raise")
        lines.append("")
        lines.append("# 注入上下文变量")
        context_str = repr(context or {})
        lines.append(f"__context__ = {context_str}")
        lines.append("for __key__, __value__ in __context__.items():")
        lines.append("    globals()[__key__] = __value__")
        lines.append("")
        lines.append("# 结果变量")
        lines.append("__result__ = None")
        lines.append("")
        lines.append("# 执行用户代码")
        lines.append("try:")
        
        # 缩进用户代码
        for line in code.strip().split('\n'):
            lines.append("    " + line)
        
        lines.append("except Exception as e:")
        lines.append('    print("[ERROR] " + str(e))')
        lines.append("    traceback.print_exc()")
        lines.append("")
        lines.append("# 输出结果（用于捕获）")
        lines.append("if __result__ is not None:")
        lines.append('    print("\\n[RESULT_START]")')
        lines.append("    print(__result__)")
        lines.append('    print("[RESULT_END]")')
        
        return "\n".join(lines)
    
    def _create_temp_script(self, content: str) -> str:
        """创建临时脚本文件"""
        if self.debug_mode:
            # 调试模式：使用固定命名，便于查看
            debug_dir = config.DEBUG_SCRIPT_DIR
            os.makedirs(debug_dir, exist_ok=True)
            script_path = os.path.join(
                debug_dir, 
                f"ida_script_{self.execution_count:04d}.py"
            )
            with open(script_path, 'w', encoding='utf-8') as f:
                f.write(content)
        else:
            # 生产模式：使用临时文件
            with tempfile.NamedTemporaryFile(
                mode='w', 
                suffix='.py', 
                delete=False,
                encoding='utf-8'
            ) as f:
                f.write(content)
                script_path = f.name
        
        return script_path
    
    def _execute_script_file(self, script_path: str) -> Dict[str, Any]:
        """使用 IDA 官方接口执行脚本文件"""
        if not HAS_IDA:
            return self._mock_execute(script_path)
        
        # 捕获输出
        stdout_capture = io.StringIO()
        stderr_capture = io.StringIO()
        
        # 使用 IDA 官方接口执行
        with redirect_stdout(stdout_capture), redirect_stderr(stderr_capture):
            error = ida_idaapi.IDAPython_ExecScript(script_path, globals())
        
        stdout_text = stdout_capture.getvalue()
        stderr_text = stderr_capture.getvalue()
        # 解析结果并从 stdout 中移除结果块，避免重复显示
        result_value, clean_stdout = self._extract_result_and_clean_stdout(stdout_text)
        
        return {
            "success": error is None,
            "result": result_value,
            "stdout": clean_stdout,
            "stderr": stderr_text + (error or ""),
        }
    
    def _mock_execute(self, script_path: str) -> Dict[str, Any]:
        """Mock 执行（用于测试环境）"""
        print(f"[MOCK] Would execute: {script_path}")
        return {
            "success": True,
            "result": None,
            "stdout": f"[MOCK MODE] Script prepared at: {script_path}",
            "stderr": "",
        }
    
    def _extract_result_and_clean_stdout(self, stdout: str) -> Tuple[Any, str]:
        """解析 __result__，并剥离 stdout 中的结果包裹块。"""
        pattern = re.compile(r"\n?\[RESULT_START\]\r?\n(.*?)\r?\n\[RESULT_END\]\n?", re.DOTALL)
        match = pattern.search(stdout)
        if not match:
            return None, stdout

        result_str = (match.group(1) or "").strip()
        try:
            result_value = ast.literal_eval(result_str)
        except Exception:
            result_value = result_str

        clean_stdout = pattern.sub("\n", stdout, count=1).strip()
        return result_value, clean_stdout


# 全局执行器实例
_executor = None

def get_executor() -> ScriptExecutor:
    """获取全局执行器实例"""
    global _executor
    if _executor is None:
        _executor = ScriptExecutor()
    return _executor


def execute_script(code: str, context: Optional[Dict] = None) -> Dict[str, Any]:
    """
    便捷函数：执行 IDAPython 脚本
    
    Example:
        result = execute_script('''
            func_ea = idc.get_name_ea_simple("main")
            cfunc = ida_hexrays.decompile(func_ea)
            __result__ = str(cfunc)
        ''')
    """
    return get_executor().execute(code, context)
