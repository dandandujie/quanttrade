import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from typing import Any

import akshare as ak
import pandas as pd

from . import database as db
from . import minute_store
from .market_data import numeric, retry_call


def selected_stocks(result: dict[str, Any]) -> list[dict[str, Any]]:
    stocks: dict[str, dict[str, Any]] = {}
    for pool in ("main", "candidates"):
        for item in result.get(pool, []):
            stocks.setdefault(item["code"], {
                "code": item["code"],
                "name": item["name"],
                "pool": pool,
                "rank": item["rank"],
                "prev_close": item["input"]["prev_close"],
                "entry_open": item["input"]["open_price"],
            })
    return list(stocks.values())


def fetch_minutes(code: str, trading_day: str) -> pd.DataFrame:
    if minute_store.covers(trading_day):
        local = minute_store.load(code, trading_day, trading_day)
        if not local.empty:
            return pd.DataFrame({
                "时间": local["datetime"].astype(str),
                "开盘": local["open"], "收盘": local["close"],
                "最高": local["high"], "最低": local["low"],
                "成交量": local["vol"] / 100,
            })
    frame = retry_call(lambda: ak.stock_zh_a_hist_pre_min_em(
        symbol=code, start_time="09:25:00", end_time="15:00:00"
    ), attempts=2)
    if frame is not None and not frame.empty and str(frame.iloc[-1]["时间"])[:10] == trading_day:
        return frame

    target = date.fromisoformat(trading_day).strftime("%Y-%m-%d")
    frame = retry_call(lambda: ak.stock_zh_a_hist_min_em(
        symbol=code,
        start_date=f"{target} 09:25:00",
        end_date=f"{target} 15:00:00",
        period="5",
        adjust="",
    ), attempts=2)
    if frame is None or frame.empty:
        raise ValueError("免费分钟接口未覆盖该交易日")
    if str(frame.iloc[-1]["时间"])[:10] != trading_day:
        raise ValueError(f"分钟数据日期不符：{str(frame.iloc[-1]['时间'])[:10]}")
    return frame


def describe_morning(entry: float, bars: list[dict[str, Any]], limit_price: float) -> str:
    last = bars[-1]["close"]
    high = max(bar["high"] for bar in bars)
    change = (last / entry - 1) * 100
    pullback = (high / last - 1) * 100
    if last >= limit_price - 0.001:
        return "强势封板"
    if high >= limit_price - 0.001:
        return "炸板回落"
    if change >= 1 and pullback >= 1.5:
        return "冲高回落"
    if change >= 2:
        return "震荡走强"
    if change <= -2:
        return "低开走弱"
    return "红盘震荡" if change >= 0 else "绿盘震荡"


def describe_afternoon(noon: float, close: float, limit_price: float) -> str:
    change = (close / noon - 1) * 100
    if close >= limit_price - 0.001 and change > 0.5:
        return "午后封板"
    if change <= -3:
        return "午后跳水"
    if change >= 2:
        return "午后拉升"
    if abs(change) < 0.7:
        return "横盘整理"
    return "震荡回落" if change < 0 else "震荡上行"


def evaluate_stock(stock: dict[str, Any], trading_day: str) -> dict[str, Any]:
    result = {
        **stock,
        "entry_price": None,
        "open_price": None,
        "noon_price": None,
        "close_price": None,
        "open_return": None,
        "noon_return": None,
        "close_return": None,
        "max_return": None,
        "min_return": None,
        "limit_up": None,
        "morning_desc": "",
        "afternoon_desc": "",
        "status": "unavailable",
        "series": [],
        "error": "",
    }
    try:
        frame = fetch_minutes(stock["code"], trading_day)
        bars = []
        for _, row in frame.iterrows():
            timestamp = str(row["时间"])
            if timestamp[:10] != trading_day:
                continue
            time = timestamp[11:16]
            if not ("09:30" <= time <= "11:30" or "13:00" <= time <= "15:00"):
                continue
            bar = {
                "time": time,
                "open": numeric(row["开盘"]),
                "close": numeric(row["收盘"]),
                "high": numeric(row["最高"]),
                "low": numeric(row["最低"]),
                "volume": numeric(row["成交量"]),
            }
            if min(bar["open"], bar["close"], bar["high"], bar["low"]) > 0:
                bars.append(bar)
        # 集合竞价成交价就是当日 09:30 开盘价，比分钟接口的"开盘"字段更可靠；
        # 个别标的的 09:30 分钟K线会给出约 2 倍于真实价的开盘价（如 11/04 泰瑞机器）。
        entry = float(stock.get("entry_open") or 0)
        if entry <= 0:
            raise ValueError("缺少竞价开盘价")
        bars = [bar for bar in bars if abs(bar["close"] / entry - 1) < 0.35]
        if not bars:
            raise ValueError("分钟行情与竞价开盘价偏差过大")
        by_time = {bar["time"]: bar for bar in bars}
        if "09:30" not in by_time:
            bars.insert(0, {"time": "09:30", "open": entry, "close": entry,
                            "high": entry, "low": entry, "volume": 0.0})
            by_time = {bar["time"]: bar for bar in bars}
        # 交易所涨停价按四舍五入到分，不能用 Python 的银行家舍入。
        limit_price = math.floor(stock["prev_close"] * 1.1 * 100 + 0.5 + 1e-9) / 100
        noon_bar = by_time.get("11:30")
        close_bar = by_time.get("15:00")
        morning = [bar for bar in bars if bar["time"] <= "11:30"]
        afternoon = [bar for bar in bars if bar["time"] >= "13:00"]
        observed = morning + afternoon
        returns = lambda price: round((price / entry - 1) * 100, 4)
        result.update({
            "entry_price": entry,
            "open_price": entry,
            "open_return": 0.0,
            "max_return": returns(max(bar["high"] for bar in observed)),
            "min_return": returns(min(bar["low"] for bar in observed)),
            "series": bars,
            "status": "complete" if close_bar else "partial",
            "morning_desc": describe_morning(entry, morning, limit_price) if noon_bar else "等待午盘",
        })
        if noon_bar:
            result["noon_price"] = noon_bar["close"]
            result["noon_return"] = returns(noon_bar["close"])
        if close_bar:
            result["close_price"] = close_bar["close"]
            result["close_return"] = returns(close_bar["close"])
            result["limit_up"] = close_bar["close"] >= limit_price - 0.001
            result["afternoon_desc"] = describe_afternoon(noon_bar["close"], close_bar["close"], limit_price) if noon_bar else "午后复盘"
    except Exception as exc:
        result["error"] = str(exc)
    return result


def evaluate_run(run_id: int) -> dict[str, Any]:
    run = db.get_run(run_id)
    if not run:
        raise ValueError("运行记录不存在")
    if run["dataset"]["source"] != "akshare":
        raise ValueError("仅免费真实行情支持实际走势复盘，演示与CSV数据不会生成模拟表现")
    stocks = selected_stocks(run)
    if not stocks:
        return {"run_id": run_id, "evaluated": 0, "unavailable": 0, "status": "empty"}
    trading_day = run["dataset"]["trading_day"]
    performance = []
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(evaluate_stock, stock, trading_day): stock for stock in stocks}
        for future in as_completed(futures):
            performance.append(future.result())
    db.save_performance(run_id, performance)
    complete = sum(item["status"] == "complete" for item in performance)
    unavailable = sum(item["status"] == "unavailable" for item in performance)
    return {
        "run_id": run_id,
        "evaluated": len(performance) - unavailable,
        "complete": complete,
        "unavailable": unavailable,
        "status": "complete" if complete else "partial",
    }
