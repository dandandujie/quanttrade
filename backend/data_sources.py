"""免费行情补充源。

AKShare / 东方财富负责涨停池、日线、分钟线与资金流排行；这里再接一路腾讯行情
（qt.gtimg.cn）的五档盘口，用来补 09:25 集合竞价撮合后的未匹配买单。
所有函数返回纯 dict/list，失败向上抛异常，由调用方决定降级。
"""
from __future__ import annotations

import json
import math
import time as _time
import urllib.parse
import urllib.request

import pandas as pd
import requests
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time as clock_time
from typing import Any

import akshare as ak

from . import network

network.configure_network()

TENCENT_ENDPOINT = "http://qt.gtimg.cn/q={symbols}"
TENCENT_BATCH = 60
TENCENT_TIMEOUT = 8
FUND_FLOW_TTL = 600

_fund_flow_cache: tuple[float, dict[str, float]] | None = None


def _number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def tencent_symbol(code: str) -> str:
    """6 位代码 → 腾讯行情代码（sh/sz/bj 前缀）。"""
    code = str(code).zfill(6)
    if code.startswith(("6", "9")):
        return f"sh{code}"
    if code.startswith(("0", "3")):
        return f"sz{code}"
    if code.startswith(("4", "8")):
        return f"bj{code}"
    return f"sh{code}"


def parse_tencent(text: str) -> dict[str, dict[str, Any]]:
    """解析腾讯批量行情。字段顺序按 qt.gtimg.cn 的 ~ 分隔协议。"""
    quotes: dict[str, dict[str, Any]] = {}
    for chunk in text.split(";"):
        chunk = chunk.strip()
        if "=" not in chunk or "~" not in chunk:
            continue
        raw = chunk.split("=", 1)[1].strip().strip('"')
        fields = raw.split("~")
        if len(fields) < 50:
            continue
        code = str(fields[2]).zfill(6)
        quotes[code] = {
            "code": code,
            "name": fields[1],
            "price": _number(fields[3]),
            "prev_close": _number(fields[4]),
            "open": _number(fields[5]),
            "volume": _number(fields[6]) * 100,          # 手 → 股
            "amount": _number(fields[37]) * 10000,       # 万元 → 元
            "avg_price": (
                _number(fields[37]) * 10000 / (_number(fields[6]) * 100)
                if _number(fields[6]) > 0 else 0.0
            ),
            "bid1_price": _number(fields[9]),
            "bid1_vol": _number(fields[10]) * 100,       # 手 → 股
            "ask1_price": _number(fields[19]),
            "ask1_vol": _number(fields[20]) * 100,
            "time": fields[30],
            "pct": _number(fields[32]),
            "high": _number(fields[33]),
            "low": _number(fields[34]),
            "turnover": _number(fields[38]),
            "float_mcap": _number(fields[44]),           # 亿元
            "limit_up": _number(fields[47]),
            "limit_down": _number(fields[48]),
            "volume_ratio": _number(fields[49]),
        }
    return quotes


def _fetch_tencent_batch(codes: list[str]) -> dict[str, dict[str, Any]]:
    symbols = ",".join(tencent_symbol(code) for code in codes)
    text = network.direct_client.get_text(
        TENCENT_ENDPOINT.format(symbols=symbols),
        headers={"Referer": "https://gu.qq.com/"},
        encoding="gbk",
        timeout=TENCENT_TIMEOUT,
        attempts=2,
    )
    return parse_tencent(text)


def tencent_quotes(codes: list[str], attempts: int = 2) -> dict[str, dict[str, Any]]:
    """批量五档盘口。60 只一组、4 路并发；单组失败重试一次。"""
    wanted = [code for code in dict.fromkeys(str(item).zfill(6) for item in codes) if len(code) == 6]
    if not wanted:
        return {}
    batches = [wanted[index:index + TENCENT_BATCH] for index in range(0, len(wanted), TENCENT_BATCH)]

    def worker(batch: list[str]) -> dict[str, dict[str, Any]]:
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                return _fetch_tencent_batch(batch)
            except Exception as exc:  # noqa: BLE001 - 网络源失败统一降级
                last_error = exc
                if attempt + 1 < attempts:
                    _time.sleep(0.3 * (attempt + 1))
        if last_error:
            raise last_error
        return {}

    quotes: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=4) as executor:
        for result in executor.map(worker, batches):
            quotes.update(result)
    return quotes


def tencent_auction(codes: list[str], target: date, now: datetime) -> dict[str, dict[str, Any]]:
    """09:26—09:30 用腾讯批量快照取竞价，完全绕开东财 spot。"""
    if now.date() != target or not (clock_time(9, 26) <= now.time() < clock_time(9, 30)):
        return {}
    quotes = tencent_quotes(codes)
    result: dict[str, dict[str, Any]] = {}
    for code, quote in quotes.items():
        open_price = _number(quote.get("open"))
        volume = _number(quote.get("volume"))
        amount = _number(quote.get("amount"))
        if volume <= 0 and amount > 0 and open_price > 0:
            volume = amount / open_price
        if open_price <= 0 or volume <= 0:
            continue
        result[code] = {
            "open_price": open_price,
            "auction_vol": volume,
            "auction_amount": amount,
            "match_price_2445": open_price,
            "auction_source": "tencent_0926",
            "auction_time": now.isoformat(timespec="seconds"),
            "micro_available": False,
        }
    return result


EASTMONEY_KLINE = "https://push2his.eastmoney.com/api/qt/stock/kline/get"


def eastmoney_auction(code: str, target: date) -> dict[str, Any] | None:
    """不经过 AKShare/代理，直接取东财 1 分钟 K 线的 09:30 竞价笔。"""
    stamp = target.strftime("%Y%m%d")
    market = "1" if str(code).zfill(6).startswith(("6", "9")) else "0"
    params = {
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "ut": "7eea3edcaed734bea9cbfc24409ed989",
        "klt": "1", "fqt": "0", "secid": f"{market}.{str(code).zfill(6)}",
        "beg": stamp, "end": stamp,
    }
    url = f"{EASTMONEY_KLINE}?{urllib.parse.urlencode(params)}"
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            request = urllib.request.Request(
                url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"}
            )
            with urllib.request.urlopen(request, timeout=10) as response:
                payload = json.loads(response.read().decode("utf-8"))
            klines = ((payload.get("data") or {}).get("klines") or [])
            if not klines:
                return None
            first = str(klines[0]).split(",")
            if len(first) < 7:
                return None
            stamp_text = first[0]
            if not stamp_text.startswith(target.isoformat()):
                return None
            volume = _number(first[5]) * 100
            open_price = _number(first[1])
            if volume <= 0 or open_price <= 0:
                return None
            return {
                "open_price": open_price,
                "auction_vol": volume,
                "auction_amount": _number(first[6]),
                "match_price_2445": _number(first[2]),
                "auction_source": "eastmoney_kline_1m",
                "auction_time": stamp_text,
                "micro_available": False,
            }
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt < 2:
                _time.sleep(0.4 * (attempt + 1))
    if last_error:
        raise last_error
    return None


SINA_KLINE = "https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData"


def sina_auction(code: str, target: date) -> dict[str, Any] | None:
    """Sina 1 分钟 K 线兜底：覆盖最近约 1 个交易日，用于盘后补 09:30 竞价。"""
    market = "sh" if str(code).zfill(6).startswith(("6", "9")) else "sz"
    params = {"symbol": f"{market}{str(code).zfill(6)}", "scale": "1", "ma": "no", "datalen": "400"}
    url = f"{SINA_KLINE}?{urllib.parse.urlencode(params)}"
    try:
        request = urllib.request.Request(
            url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"}
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            rows = json.loads(response.read().decode("utf-8", errors="replace"))
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(rows, list):
        return None
    fallback = None
    for row in rows:
        day = str(row.get("day", ""))
        if not day.startswith(target.isoformat()):
            continue
        if day.startswith(f"{target.isoformat()} 09:30"):
            fallback = row
            break
        if day.startswith(f"{target.isoformat()} 09:31") and fallback is None:
            fallback = row
    if fallback is None:
        return None
    volume = _number(fallback.get("volume"))
    open_price = _number(fallback.get("open"))
    if volume <= 0 or open_price <= 0:
        return None
    is_0931 = str(fallback.get("day", "")).startswith(f"{target.isoformat()} 09:31")
    return {
        "open_price": open_price,
        "auction_vol": volume,
        "auction_amount": _number(fallback.get("amount")),
        "match_price_2445": _number(fallback.get("close")),
        "auction_source": "sina_kline_0931" if is_0931 else "sina_kline_1m",
        "auction_time": str(fallback.get("day", "")),
        "micro_available": False,
    }


def tencent_daily(code: str, start: date, end: date, shares: float = 0.0) -> list[dict[str, Any]]:
    """日线兜底：腾讯前复权日 K，缺成交额/换手时用静态流通股本估算换手。"""
    symbol = tencent_symbol(code)
    days = max(60, (end - start).days + 30)
    url = (
        "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?"
        f"param={symbol},day,,,{days},"
    )
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
    except Exception:  # noqa: BLE001
        return []
    block = ((payload.get("data") or {}).get(symbol) or {})
    rows = block.get("day") or []
    if not rows:
        return []
    bars: list[dict[str, Any]] = []
    previous_close = 0.0
    for row in rows:
        if len(row) < 6:
            continue
        day = str(row[0])
        if not (start.isoformat() <= day <= end.isoformat()):
            continue
        open_price = _number(row[1])
        close = _number(row[2])
        high = _number(row[3])
        low = _number(row[4])
        volume = _number(row[5])
        if close <= 0 or volume <= 0:
            continue
        bars.append({
            "trade_date": day, "open": open_price, "close": close,
            "high": high, "low": low, "volume": volume, "amount": 0.0,
            "pct": round((close / previous_close - 1) * 100, 4) if previous_close > 0 else 0.0,
            "turnover": round(volume * 100 / shares * 100, 4) if shares > 0 else 0.0,
        })
        previous_close = close
    return bars


def sina_daily(code: str, start: date, end: date, shares: float = 0.0) -> list[dict[str, Any]]:
    """Sina 日线兜底，成交量从股换算成手。"""
    market = "sh" if str(code).zfill(6).startswith(("6", "9")) else "sz"
    days = max(60, (end - start).days + 30)
    url = (
        "https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData"
        f"?symbol={market}{str(code).zfill(6)}&scale=240&ma=no&datalen={days}"
    )
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(request, timeout=10) as response:
            rows = json.loads(response.read().decode("utf-8", errors="replace"))
    except Exception:  # noqa: BLE001
        return []
    if not isinstance(rows, list):
        return []
    bars: list[dict[str, Any]] = []
    previous_close = 0.0
    for row in rows:
        day = str(row.get("day", ""))
        if not (start.isoformat() <= day <= end.isoformat()):
            continue
        open_price = _number(row.get("open"))
        close = _number(row.get("close"))
        volume = _number(row.get("volume")) / 100
        if close <= 0 or volume <= 0:
            continue
        bars.append({
            "trade_date": day, "open": open_price, "close": close,
            "high": _number(row.get("high")), "low": _number(row.get("low")),
            "volume": volume, "amount": 0.0,
            "pct": round((close / previous_close - 1) * 100, 4) if previous_close > 0 else 0.0,
            "turnover": round(volume * 100 / shares * 100, 4) if shares > 0 else 0.0,
        })
        previous_close = close
    return bars


def spot_auction(codes: list[str], target: date, now: datetime) -> dict[str, dict[str, Any]]:
    """09:26—09:30 用一次全市场快照批量取竞价结果，避免逐只请求被限流。

    快照的「今开 / 成交量 / 成交额」就是集合竞价撮合结果；09:30 后不再调用，
    因为成交量会变成连续竞价累计量。
    """
    if now.date() != target or not (clock_time(9, 26) <= now.time() < clock_time(9, 30)):
        return {}
    frame = ak.stock_zh_a_spot_em()
    if frame is None or frame.empty:
        return {}
    wanted = set(codes)
    result: dict[str, dict[str, Any]] = {}
    for _, row in frame.iterrows():
        code = str(row.get("代码", "")).zfill(6)
        if code not in wanted:
            continue
        open_price = _number(row.get("今开"))
        volume = _number(row.get("成交量")) * 100
        if open_price <= 0 or volume <= 0:
            continue
        result[code] = {
            "open_price": open_price,
            "auction_vol": volume,
            "auction_amount": _number(row.get("成交额")),
            "match_price_2445": open_price,
            "auction_source": "spot_0926",
            "auction_time": now.isoformat(timespec="seconds"),
            "micro_available": False,
        }
    return result


def auction_book(codes: list[str], target: date, now: datetime) -> dict[str, dict[str, Any]]:
    """09:25 集合竞价撮合后的残留盘口。

    只有目标交易日、且当前时间落在 09:25:10—09:30:00 内才有意义：
    09:25 撮合后买卖双方只剩未匹配的一边，买一量就是「未匹配买单」；
    09:30 连续竞价开始后盘口会被新挂单改写，不能当作竞价数据。
    """
    if now.date() != target:
        return {}
    clock = now.time()
    if not (clock_time(9, 25, 10) <= clock < clock_time(9, 30)):
        return {}
    stamp = target.strftime("%Y%m%d")
    quotes = tencent_quotes(codes)
    return {
        code: quote
        for code, quote in quotes.items()
        if str(quote.get("time", ""))[:8] == stamp
        and (quote.get("bid1_vol", 0) > 0 or quote.get("ask1_vol", 0) > 0)
    }


def fund_flow_5d(ttl: int = FUND_FLOW_TTL) -> dict[str, float]:
    """东方财富个股资金流排行的「5 日主力净流入-净额」，单位元。

    一次请求覆盖全市场；数据截止到最近一个交易日，历史回放不能用（会含未来信息）。
    """
    global _fund_flow_cache
    now = _time.time()
    if _fund_flow_cache and now - _fund_flow_cache[0] < ttl:
        return _fund_flow_cache[1]
    frame = ak.stock_individual_fund_flow_rank(indicator="5日")
    if frame is None or frame.empty:
        return {}
    flows: dict[str, float] = {}
    for _, row in frame.iterrows():
        code = str(row.get("代码", "")).zfill(6)
        if len(code) != 6 or not code.isdigit():
            continue
        flows[code] = _number(row.get("5日主力净流入-净额"))
    _fund_flow_cache = (now, flows)
    return flows


def lhb_net_inflow(day: date) -> dict[str, float]:
    """东方财富龙虎榜某一天的「龙虎榜净买额」，同一代码多条原因合并求和。"""
    stamp = day.strftime("%Y%m%d")
    frame = ak.stock_lhb_detail_em(start_date=stamp, end_date=stamp)
    if frame is None or frame.empty:
        return {}
    inflow: dict[str, float] = {}
    for _, row in frame.iterrows():
        code = str(row.get("代码", "")).zfill(6)
        if len(code) != 6 or not code.isdigit():
            continue
        inflow[code] = inflow.get(code, 0.0) + _number(row.get("龙虎榜净买额"))
    return inflow


def breadth_from_spot(spot: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """从全市场快照折算涨跌家数 / 涨跌停家数 / 两市成交额。"""
    values = [item for item in spot.values() if isinstance(item, dict)]
    pcts = [_number(item.get("pct")) for item in values]
    amount = sum(_number(item.get("amount")) for item in values)
    return {
        "count": len(values),
        "up": sum(1 for value in pcts if value > 0),
        "down": sum(1 for value in pcts if value < 0),
        "flat": sum(1 for value in pcts if value == 0),
        "limit_up": sum(1 for value in pcts if value >= 9.5),
        "limit_down": sum(1 for value in pcts if value <= -9.5),
        "amount": round(amount, 0),
    }

def eastmoney_zt_pool(day: date) -> pd.DataFrame:
    """直接请求涨停池，带超时，不依赖 AKShare 的无超时 requests。"""
    url = "https://push2ex.eastmoney.com/getTopicZTPool"
    params = {
        "ut": "7eea3edcaed734bea9cbfc24409ed989", "dpt": "wz.ztzt",
        "Pageindex": "0", "pagesize": "10000", "sort": "fbt:asc",
        "date": day.strftime("%Y%m%d"),
    }
    try:
        session = requests.Session()
        session.trust_env = False
        response = session.get(url, params=params, timeout=10)
        payload = response.json()
    except Exception:  # noqa: BLE001
        return pd.DataFrame()
    rows = (payload.get("data") or {}).get("pool") or []
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame([{
        "代码": str(row.get("c", "")).zfill(6),
        "名称": str(row.get("n", "")),
        "所属行业": str(row.get("hybk", "")),
        "连板数": row.get("lbc", 0),
        "流通市值": row.get("ltsz", 0),
        "炸板次数": row.get("zbc", 0),
        "封板资金": row.get("fund", 0),
        "首次封板时间": str(row.get("fbt", "")),
        "换手率": row.get("hs", 0),
    } for row in rows])
