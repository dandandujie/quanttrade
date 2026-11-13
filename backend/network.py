"""行情网络配置。

国内行情源（东方财富、腾讯、Sina）经常因为本机代理断开而出现
`ProxyError / RemoteDisconnected`；这些源本身直连即可访问。
默认给它们加 NO_PROXY，代理只留给需要代理的境外流量。
需要强制走系统代理时设置 `QUANT_KEEP_PROXY=1`。
"""
from __future__ import annotations

import os

DOMESTIC_HOSTS = (
    "push2his.eastmoney.com",
    "datacenter-web.eastmoney.com",
    "qt.gtimg.cn",
    "gu.qq.com",
    "sina.com.cn",
    ".sina.com.cn",
    "sinajs.cn",
    "finance.sina.com.cn",
    "163.com",
    ".163.com",
)


def configure_network() -> None:
    if os.environ.get("QUANT_KEEP_PROXY") == "1":
        return
    current = os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or ""
    entries = [item.strip() for item in current.split(",") if item.strip()]
    for host in DOMESTIC_HOSTS:
        if host not in entries:
            entries.append(host)
    value = ",".join(entries)
    os.environ["NO_PROXY"] = value
    os.environ["no_proxy"] = value
