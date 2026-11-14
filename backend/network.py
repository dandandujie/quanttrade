"""行情网络配置与全局无代理直连 HTTP 客户端。

国内行情源（东方财富、腾讯、Sina）在 Windows 环境下极易因系统代理（Clash/V2Ray）
未覆盖负载均衡子域名而触发 `ProxyError / RemoteDisconnected`。
本模块提供两层防护：
1. 补全 `DOMESTIC_HOSTS` 写入 `NO_PROXY`（照顾 AKShare 内部调用）；
2. 提供基于 `requests.Session` 连接池的 `DirectHttpClient`（显式 `trust_env=False`、
   `proxies={"http": None, "https": None}`），从传输层彻底旁路系统代理并复用 TCP/TLS 连接。
需要强制走系统代理时设置环境变量 `QUANT_KEEP_PROXY=1`。
"""
from __future__ import annotations

import os
import random
import threading
import time
from typing import Any

import requests
from requests.adapters import HTTPAdapter

DOMESTIC_HOSTS = (
    "eastmoney.com",
    ".eastmoney.com",
    "push2.eastmoney.com",
    "82.push2.eastmoney.com",
    "push2ex.eastmoney.com",
    "push2his.eastmoney.com",
    "datacenter-web.eastmoney.com",
    "quote.eastmoney.com",
    "gtimg.cn",
    ".gtimg.cn",
    "qt.gtimg.cn",
    "web.ifzq.gtimg.cn",
    "gu.qq.com",
    ".qq.com",
    "sina.com.cn",
    ".sina.com.cn",
    "sina.cn",
    ".sina.cn",
    "quotes.sina.cn",
    "hq.sinajs.cn",
    "sinajs.cn",
    "finance.sina.com.cn",
    "163.com",
    ".163.com",
)

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Connection": "keep-alive",
}


def keep_proxy() -> bool:
    return os.environ.get("QUANT_KEEP_PROXY") == "1"


def configure_network() -> None:
    if keep_proxy():
        return
    current = os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or ""
    entries = [item.strip() for item in current.split(",") if item.strip()]
    for host in DOMESTIC_HOSTS:
        if host not in entries:
            entries.append(host)
    value = ",".join(entries)
    os.environ["NO_PROXY"] = value
    os.environ["no_proxy"] = value


class DirectHttpClient:
    """线程安全的无代理长连接池客户端。"""

    def __init__(self, pool_connections: int = 24, pool_maxsize: int = 32) -> None:
        self._local = threading.local()
        self._pool_connections = pool_connections
        self._pool_maxsize = pool_maxsize

    def _session(self) -> requests.Session:
        session: requests.Session | None = getattr(self._local, "session", None)
        if session is None:
            session = requests.Session()
            if not keep_proxy():
                session.trust_env = False
                session.proxies = {"http": None, "https": None}
            adapter = HTTPAdapter(
                pool_connections=self._pool_connections,
                pool_maxsize=self._pool_maxsize,
                max_retries=0,
            )
            session.mount("http://", adapter)
            session.mount("https://", adapter)
            session.headers.update(DEFAULT_HEADERS)
            self._local.session = session
        return session

    def get(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float = 8.0,
        attempts: int = 2,
    ) -> requests.Response:
        last_error: Exception | None = None
        for attempt in range(max(1, attempts)):
            try:
                response = self._session().get(
                    url, params=params, headers=headers, timeout=timeout
                )
                response.raise_for_status()
                return response
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                # 连接被远端重置时重建当前线程 Session，避免复用坏连接
                self._local.session = None
                if attempt + 1 < attempts:
                    delay = min(3.0, 0.25 * (2 ** attempt)) + random.uniform(0.0, 0.12)
                    time.sleep(delay)
        assert last_error is not None
        raise last_error

    def get_text(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        encoding: str = "utf-8",
        timeout: float = 8.0,
        attempts: int = 2,
    ) -> str:
        response = self.get(
            url, params=params, headers=headers, timeout=timeout, attempts=attempts
        )
        return response.content.decode(encoding, errors="replace")

    def get_json(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float = 8.0,
        attempts: int = 2,
    ) -> Any:
        response = self.get(
            url, params=params, headers=headers, timeout=timeout, attempts=attempts
        )
        return response.json()


configure_network()
direct_client = DirectHttpClient()
