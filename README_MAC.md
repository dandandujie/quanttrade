# macOS 运行说明

## 环境要求

- macOS 12+
- Python 3.10+（推荐 3.11 / 3.12）
- 能访问东方财富、腾讯、新浪行情接口
- 不需要 API Key

检查 Python：

```bash
python3 -V
```

如果低于 3.10，先安装新版：

```bash
brew install python@3.12
```

## 首次运行

解压后进入目录，执行：

```bash
chmod +x start.command
./start.command
```

也可以直接在 Finder 里双击 `start.command`。

首次会自动创建 `.venv` 并安装 `requirements.txt`，约 2—5 分钟。安装完成后会自动打开：

```text
http://127.0.0.1:8000
```

如果 macOS 拦截脚本：

```bash
xattr -d com.apple.quarantine start.command 2>/dev/null || true
chmod +x start.command
./start.command
```

## 每天早盘

- 保持程序在前一晚或至少 09:15 前运行。
- 09:26:15 自动抓取 09:25 集合竞价并选股。
- 09:26—11:30 每 2 分钟检查一次：如果当日数据集缺失或首次同步失败，会自动补抓。
- 09:25—11:30、13:00—15:00 页面自动刷新：现价约 20 秒，日内/五日缩略图每 5 分钟。
- 14:40 后尾盘页每 5 分钟自动重扫。

如果收盘后没有当日数据，通常是因为程序当时没运行。第二天开盘前启动即可；启动时如果还在早盘窗口，会自动补同步。

## 数据文件

`data/quant.db` 已包含历史数据集、运行结果和日线缓存。`data/` 下的行情缓存用于历史复盘和加速同步，可以保留。

不要删除：

```text
data/quant.db
data/concept_members.json
```

## 停止

在启动终端按 `Ctrl + C`。

## 常见问题

- 端口 8000 被占用：先关闭旧进程，或执行 `lsof -i :8000` 查看。
- 首次安装依赖失败：通常是网络问题，重新执行 `./start.command`；也可以先运行：
  ```bash
  .venv/bin/python -m pip install -r requirements.txt
  ```
- 页面没有更新：确认系统时间、时区为中国时区，并保持程序运行；强刷浏览器 `Cmd + Shift + R`。
- 行情源临时断连：现价和图表有腾讯/新浪兜底；同步任务会在 09:26—11:30 自动重试。
