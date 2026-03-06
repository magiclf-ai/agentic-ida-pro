# Observability Frontend (Vue)

Agentic IDA 可观测性系统前端，用于实时查看 Agent 会话日志、Turn 流转、工具调用和统计图表。

## 快速启动（推荐）

使用一键启动脚本（同时启动前后端，**默认启用热更新**）：

```bash
cd /mnt/d/reverse/agentic_ida_pro
./start_observability.sh
```

然后打开浏览器访问：`http://<服务器IP>:5173`

### 热更新（默认启用）

启动脚本默认启用热更新模式：

- **后端**: Flask 自动检测 Python 文件变化并重载
- **前端**: Vite HMR 即时更新 Vue/CSS 修改

```bash
# 默认启用热更新
./start_observability.sh

# 禁用热更新（生产环境）
./start_observability.sh --no-reload
```

### 启动选项

```bash
# 查看所有选项
./start_observability.sh --help

# 本地开发（热更新启用）
./start_observability.sh

# 跨机器部署 - 指定后端 IP（让其他机器能访问）
./start_observability.sh --api-host 192.168.1.100

# 指定端口
./start_observability.sh --api-port 8080 --ui-port 3000

# 只启动后端 API
./start_observability.sh --backend-only

# 只启动前端 UI（后端已在运行）
./start_observability.sh --frontend-only
```

## 部署场景

### 场景1：本机开发（前后端同一台机器）

```bash
./start_observability.sh
# 访问: http://127.0.0.1:5173
```

### 场景2：服务器部署（浏览器在另一台机器）

假设服务器 IP 是 `192.168.1.100`：

```bash
# 在服务器上执行
./start_observability.sh --api-host 192.168.1.100

# 在其他机器的浏览器访问
# UI:  http://192.168.1.100:5173
# API: http://192.168.1.100:8765
```

### 场景3：前后端分离部署

```bash
# 服务器A - 只启动后端
./start_observability.sh --api-host 192.168.1.100 --backend-only

# 服务器B - 只启动前端，连接远程后端
./start_observability.sh --frontend-only
# 然后修改前端配置指向服务器A的API
```

## 手动启动（传统方式）

如果需要分别控制前后端：

### 1. 启动后端 API

```bash
cd /mnt/d/reverse/agentic_ida_pro
PYTHONPATH=src .venv/bin/python src/scripts/logs.py --host 0.0.0.0 --port 8765
```

### 2. 启动前端

```bash
cd /mnt/d/reverse/agentic_ida_pro/frontend/observability
npm install
npm run dev -- --host 0.0.0.0 --port 5173
```

## 功能特性

- **会话列表**：查看所有 Agent 会话
- **Turn 时间线**：可视化展示交互流转
- **消息查看**：支持代码语法高亮（Python）
- **统计仪表盘**：Token 趋势、工具成功率、延迟分析
- **深色模式**：一键切换主题
- **数据导出**：一键导出会话数据

## 技术栈

- Vue 3.5 + Naive UI
- Prism.js（代码高亮）
- ECharts（图表）
- dayjs（时间处理）

## API 端点

- `/api/health` - 健康检查
- `/api/sessions` - 会话列表
- `/api/sessions/<id>/summary` - 会话统计摘要
- `/api/turns` - Turn 列表
- `/api/messages` - 消息列表
- `/api/tools` - 工具调用记录
- `/api/events` - 事件流

## 注意事项

- 前端默认通过 Vite dev proxy 转发 `/api/*` 到 Flask 后端
- 如果后端不在本机，启动前端时设置：`VITE_PROXY_TARGET='http://<flask-host>:8765' npm run dev`
- 数据源：`logs/agent_sessions/agent_observability.sqlite3`
