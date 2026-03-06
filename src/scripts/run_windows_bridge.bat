@echo off
setlocal

REM Set your IDB path
set IDB_PATH=D:\reverse\agentic_ida_pro\test_binaries\complex_test.i64

set PYTHONPATH=%cd%\src
python src\scripts\service_bridge.py watch ^
  --service-cmd "python -m ida_service.daemon --host 0.0.0.0 --port 5000 --idb %IDB_PATH%" ^
  --control-file %cd%\runtime\ida_service_control.json ^
  --cwd %cd%

endlocal
