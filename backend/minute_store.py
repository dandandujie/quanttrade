"""本地 1 分钟 parquet 数据适配。

目录结构：<root>/000001.SZ.parquet，索引为 (trade_date, trade_time)，
字段 open/high/low/close/vol/amount/adj_factor。数据覆盖到 2026-09-11；
超出范围的日期由调用方回退到 AKShare。
"""
from __future__ import annotations

import os
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(os.environ.get("STOCK_1MIN_DIR", "E:/BaiduNetdiskDownload/stock_1min"))
LATEST_DAY = date(2026, 9, 11)
_COLUMNS = ["open", "high", "low", "close", "vol", "amount"]


def normalize(code: str) -> str:
    code = str(code).strip().upper().replace("SH", "").replace("SZ", "")
    code = code.zfill(6)
    suffix = "SH" if code.startswith(("6", "9")) else "SZ"
    return f"{code}.{suffix}"


def path_for(code: str) -> Path:
    return ROOT / f"{normalize(code)}.parquet"


def available(code: str) -> bool:
    return path_for(code).exists()


def covers(day: str) -> bool:
    return ROOT.exists() and date.fromisoformat(day) <= LATEST_DAY


@lru_cache(maxsize=4)
def _read(path: str) -> pd.DataFrame:
    frame = pd.read_parquet(path, columns=_COLUMNS)
    if not isinstance(frame.index, pd.MultiIndex):
        raise ValueError(f"{path} 不是 (trade_date, trade_time) 多级索引")
    return frame


def load(code: str, start: str | None = None, end: str | None = None) -> pd.DataFrame:
    """返回带 date/time 列的 1 分钟数据；不存在时返回空表。"""
    path = path_for(code)
    if not path.exists():
        return pd.DataFrame()
    frame = _read(str(path))
    index = frame.index
    dates = index.get_level_values(0)
    mask = pd.Series(True, index=frame.index)
    if start:
        mask &= dates >= pd.Timestamp(start)
    if end:
        mask &= dates <= pd.Timestamp(end)
    frame = frame.loc[mask]
    if frame.empty:
        return frame
    out = frame.copy()
    out["date"] = out.index.get_level_values(0).strftime("%Y-%m-%d")
    out["time"] = out.index.get_level_values(1).strftime("%H:%M")
    out["datetime"] = out.index.get_level_values(1)
    return out.reset_index(drop=True)


def auction_bar(code: str, day: str) -> dict[str, Any] | None:
    """09:30 一分钟 bar 作为历史集合竞价/开盘确认。"""
    frame = load(code, day, day)
    if frame.empty:
        return None
    row = frame[frame["time"] == "09:30"]
    if row.empty:
        return None
    row = row.iloc[0]
    volume = float(row["vol"])
    if volume <= 0:
        return None
    return {
        "open_price": float(row["open"]),
        "auction_vol": volume,
        "auction_amount": float(row["amount"]),
        "match_price_2445": float(row["close"]),
        "auction_source": "local_minute_0930",
        "auction_time": f"{day} 09:30:00",
        "micro_available": False,
        "first5_close": _first5_close(frame),
    }


def _first5_close(frame: pd.DataFrame) -> float:
    row = frame[frame["time"] == "09:35"]
    return float(row.iloc[0]["close"]) if not row.empty else 0.0


def daily_bars(code: str, start: str | None = None, end: str | None = None) -> list[dict[str, Any]]:
    """从本地 1 分钟数据聚合日线（成交量单位：手）。"""
    frame = load(code, start, end)
    if frame.empty:
        return []
    bars: list[dict[str, Any]] = []
    previous_close = 0.0
    for day, group in frame.groupby("date", sort=True):
        group = group.sort_values("time")
        close = float(group.iloc[-1]["close"])
        if close <= 0:
            continue
        bars.append({
            "trade_date": day,
            "open": float(group.iloc[0]["open"]),
            "close": close,
            "high": float(group["high"].max()),
            "low": float(group["low"].min()),
            "volume": round(float(group["vol"].sum()) / 100, 2),
            "amount": float(group["amount"].sum()),
            "pct": round((close / previous_close - 1) * 100, 4) if previous_close > 0 else 0.0,
            "turnover": 0.0,
        })
        previous_close = close
    return bars


def bars_5m(code: str, start: str, end: str) -> list[dict[str, Any]]:
    """把本地 1 分钟数据聚合成 5 分钟 bar，字段与 minutes_5day 对齐。"""
    frame = load(code, start, end)
    if frame.empty:
        return []
    frame = frame[
        ((frame["time"] >= "09:30") & (frame["time"] <= "11:30"))
        | ((frame["time"] >= "13:00") & (frame["time"] <= "15:00"))
    ].copy()
    if frame.empty:
        return []
    bars: list[dict[str, Any]] = []
    # 09:30 是集合竞价/第一分钟，单独保留，不并进第一根 5 分钟。
    first = frame[frame["time"] == "09:30"]
    for _, row in first.iterrows():
        bars.append({
            "date": row["date"], "time": "09:30",
            "open": round(float(row["open"]), 4), "close": round(float(row["close"]), 4),
            "high": round(float(row["high"]), 4), "low": round(float(row["low"]), 4),
            "volume": round(float(row["vol"]) / 100, 2), "amount": round(float(row["amount"]), 2),
        })
    rest = frame[frame["time"] > "09:30"].copy()
    if rest.empty:
        return bars
    rest["bin"] = rest["datetime"].dt.ceil("5min")
    for (day, stamp), group in rest.groupby(["date", "bin"], sort=True):
        bars.append({
            "date": day,
            "time": stamp.strftime("%H:%M"),
            "open": round(float(group["open"].iloc[0]), 4),
            "close": round(float(group["close"].iloc[-1]), 4),
            "high": round(float(group["high"].max()), 4),
            "low": round(float(group["low"].min()), 4),
            "volume": round(float(group["vol"].sum()) / 100, 2),
            "amount": round(float(group["amount"].sum()), 2),
        })
    return bars


def day_frame(code: str, day: str) -> pd.DataFrame:
    return load(code, day, day)
