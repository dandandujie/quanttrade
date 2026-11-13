"""用本地 1 分钟 parquet 研究 09:30—09:35 开盘确认规则。

只做研究/校准，不参与实盘接口。样本是主板股票（600/601/603/605/000/001/002/003），
按日计算 gap、09:35 相对开盘、当日收盘相对开盘/09:35。"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import pandas as pd

from . import minute_store

PREFIXES = ("600", "601", "603", "605", "000", "001", "002", "003")


def sample_files(limit: int) -> list[Path]:
    files = sorted(minute_store.ROOT.glob("*.parquet"))
    files = [path for path in files if path.stem.split(".")[0].startswith(PREFIXES)]
    if limit and len(files) > limit:
        step = max(1, len(files) // limit)
        files = files[::step][:limit]
    return files


def _one(path: Path, start: str, end: str) -> list[dict[str, Any]]:
    code = path.stem.split(".")[0]
    frame = minute_store.load(code, start, end)
    if frame.empty:
        return []
    rows: list[dict[str, Any]] = []
    previous_close = 0.0
    for day, group in frame.groupby("date", sort=True):
        group = group.sort_values("time")
        first = group[group["time"] == "09:30"]
        first5 = group[group["time"] == "09:35"]
        if first.empty or first5.empty:
            previous_close = float(group.iloc[-1]["close"])
            continue
        open_price = float(first.iloc[0]["open"])
        close = float(group.iloc[-1]["close"])
        if open_price <= 0 or close <= 0:
            previous_close = close
            continue
        if previous_close > 0 and 0.8 <= open_price / previous_close <= 1.2:
            head = group[(group["time"] >= "09:30") & (group["time"] <= "09:35")]
            vol5 = float(head["vol"].sum())
            amount5 = float(head["amount"].sum())
            rows.append({
                "code": code,
                "day": day,
                "gap": open_price / previous_close - 1,
                "first5": float(first5.iloc[0]["close"]) / open_price - 1,
                "close_ret": close / open_price - 1,
                "vol5": vol5,
                "vwap5": amount5 / vol5 if vol5 > 0 else open_price,
                "above_vwap": float(first5.iloc[0]["close"]) >= (amount5 / vol5 if vol5 > 0 else open_price),
            })
        previous_close = close
    return rows


def collect(limit: int = 200, start: str = "2026-01-01", end: str = "2026-09-11") -> pd.DataFrame:
    files = sample_files(limit)
    rows: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = [executor.submit(_one, path, start, end) for path in files]
        for future in as_completed(futures):
            rows.extend(future.result())
    return pd.DataFrame(rows)


def summarize(frame: pd.DataFrame) -> dict[str, Any]:
    pool = frame[(frame["gap"] >= 0.03) & (frame["gap"] <= 0.075)].copy()
    if pool.empty:
        return {"sample": 0}
    result: dict[str, Any] = {"sample": len(pool), "stocks": int(pool["code"].nunique())}
    result["baseline"] = {
        "avg_close_ret": round(float(pool["close_ret"].mean()) * 100, 2),
        "win_rate": round(float((pool["close_ret"] > 0).mean()) * 100, 1),
    }
    for threshold in (-0.005, -0.01, -0.015, -0.02, -0.03):
        survivors = pool[pool["first5"] >= threshold]
        aborted = len(pool) - len(survivors)
        if survivors.empty:
            continue
        entry_ret = survivors["close_ret"] - survivors["first5"]
        result[f"abort_{threshold:.3f}"] = {
            "bought": len(survivors),
            "aborted": aborted,
            "avg_confirmed_ret": round(float(entry_ret.mean()) * 100, 2),
            "win_rate": round(float((entry_ret > 0).mean()) * 100, 1),
        }
    # 第一根 5 分钟涨幅分组，观察开盘承接与当日收益
    bins = [-0.05, -0.02, -0.01, -0.005, 0, 0.005, 0.01, 0.02, 0.05]
    labels = ["<-2%", "-2~-1%", "-1~-.5%", "-.5~0", "0~.5%", ".5~1%", "1~2%", ">2%"]
    pool["bucket"] = pd.cut(pool["first5"], bins=bins, labels=labels, include_lowest=True)
    grouped = pool.groupby("bucket", observed=True)
    result["by_first5"] = {
        str(key): {
            "n": int(len(group)),
            "avg_close_ret": round(float(group["close_ret"].mean()) * 100, 2),
            "win_rate": round(float((group["close_ret"] > 0).mean()) * 100, 1),
        }
        for key, group in grouped
    }
    return result
