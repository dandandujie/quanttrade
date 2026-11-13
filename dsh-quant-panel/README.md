# dsh-quant-panel · 0925 选股插件

把本仓库的选股程序接进 DeepSeek Harness Web GUI：左侧边栏多一个「选股」图标，
点开即切换到选股界面，再点对话图标回到原来的会话。

## 组成

| 半边 | 文件 | 作用 |
| --- | --- | --- |
| Host | `index.js` | 在 Harness 自己的 Web 服务上挂 `/quant` 前缀路由，反向代理到 `http://127.0.0.1:8000`，并把页面里的 `/static/`、`/api/` 改写到 `/quant` 下 |
| Client | `client.js` | 注册 `sidebar.panellist` 的图标条目（id `quant`）与 `main` 面板（key `quant`）；面板内嵌 `/quant/`，并做后端探活、断线提示、刷新、新窗口打开 |
| 文案 | `locale/*.json` | 插件列表里的标题与描述（中/英） |
| 图标 | `icon.svg` | 插件列表与详情页的原创建图标 |

## 为什么用同源代理

选股前端是纯静态页面，所有请求都是根路径（`/static/*`、`/api/*`）。如果直接
iframe `http://127.0.0.1:8000`，就变成跨源文档，受页面 frame 策略约束，而且
两个源各自独立。改成同源 `/quant` 后：浏览器不需要 CORS，页面里的绝对路径通过
一次前缀改写就能继续工作，换端口或换机器也只需要改 `index.js` 里的 `UPSTREAM`。

## 一键启动

面板检测到 8000 端口没有服务时，会显示「一键启动」按钮。Host 半边会：

1. 依次尝试 `DSH_QUANT_PYTHON`、项目 `.venv`、`~/python/python.exe`、PATH 里的 `python`、`py -3`，
   用 `python -c "import uvicorn, fastapi"` 预检，选第一个装好依赖的解释器；
2. 以独立进程启动 `python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000`
   （`detached`，关闭面板或 Harness 都不影响后端）；
3. 轮询 `/api/health`，就绪后面板自动切到选股界面；失败时把日志尾部显示在面板里。
   日志文件：`data/backend.log`。

控制路由（同源、只监听本机）：

| 路由 | 作用 |
| --- | --- |
| `POST /quant/control/start` | 启动后端（已在运行则直接返回 online） |
| `GET /quant/control/status` | 在线状态、PID、解释器、日志尾部 |
| `POST /quant/control/stop` | 只结束由插件启动的进程，外部启动的不动 |

环境变量：

- `DSH_QUANT_PYTHON`：指定 Python 解释器
- `DSH_QUANT_ROOT`：指定项目根目录（默认从插件目录向上找 `backend/main.py`）

## 使用前提

选股后端可以由面板一键启动；手动方式（在仓库根目录）：

```bat
start.bat
```

或：

```bash
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

后端没起来时面板显示「一键启动」；起来后无需重启 Harness。

## 安装 / 卸载

在 Harness 里让 Agent 执行 `plugin_manager install_bundle`，目标目录就是本目录：

```
D:\Github\quanttrade\dsh-quant-panel
```

卸载用 `plugin_manager remove_bundle`，目标 `@local/dsh-quant-panel`。

## 说明

- 面板是本机 Harness 的入口，`/quant` 与 Harness 的 Web 服务同源同端口，
  默认只监听回环地址。
- 选股界面保持自己的视觉风格（浅色研究台），面板外框用 Harness 主题令牌，
  深浅色切换时只有外框跟随。
- 想换端口：改 `index.js` 的 `UPSTREAM`，以及 `client.js` 里 `MOUNT` 不用动；
  改完需要重新加载插件。
