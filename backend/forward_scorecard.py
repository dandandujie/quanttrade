"""11 月以来的前向记分卡：只跟踪每日选股到 T+1 收盘，不做历史拟合。

免费日线来自腾讯；每个交易日取最新数据集的最新一次 run，
按开盘买入、次日收盘卖出，扣除当前成本参数。
"""
from __future__ import annotations

import json
import time as _time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import requests

from . import database as db
from .costs import net_return_pct
from .data_sources import tencent_daily
from .schemas import StrategyConfig

CACHE = Path(__file__).resolve().parent.parent / "data" / "forward_scorecard.json"


def _sina_close(code: str, day: str) -> float | None:
    market = "sh" if str(code).zfill(6).startswith(("6", "9")) else "sz"
    url = (
        "https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData"
        f"?symbol={market}{str(code).zfill(6)}&scale=240&ma=no&datalen=30"
    )
    try:
        session = requests.Session()
        session.trust_env = False
        response = session.get(url, timeout=10)
        rows = response.json()
    except Exception:
        return None
    if not isinstance(rows, list):
        return None
    return next((float(row["close"]) for row in rows if str(row.get("day", "")) > day), None)


def _next_close(code: str, day: str) -> float | None:
    start = date.fromisoformat(day)
    try:
        bars = tencent_daily(code, start, start + timedelta(days=5), 0.0)
        value = next((float(bar["close"]) for bar in bars if bar["trade_date"] > day), None)
        if value is not None:
            return value
    except Exception:
        pass
    return _sina_close(code, day)


def build(since: str = "2026-11-03", refresh: bool = False) -> dict[str, Any]:
    if CACHE.exists() and not refresh:
        cached = json.loads(CACHE.read_text(encoding="utf-8"))
        if cached.get("since") == since and _time.time() - cached.get("cached_at", 0) < 600:
            return cached
    config = StrategyConfig()
    cost = config.model_dump()
    days: list[dict[str, Any]] = []
    picks: list[dict[str, Any]] = []
    with db.connect() as conn:
        rows = conn.execute(
            """
            SELECT d.id, d.trading_day, MAX(r.id) AS run_id
            FROM datasets d JOIN runs r ON r.dataset_id = d.id
            WHERE d.trading_day >= ?
            GROUP BY d.trading_day
            ORDER BY d.trading_day
            """,
            (since,),
        ).fetchall()
    for dataset in rows:
        latest_run = dataset[2]
        run = db.get_run(int(latest_run))
        if not run or not run.get("main"):
            continue
        day = dataset[1]
        rows = []
        for item in run["main"][:8]:
            close = _next_close(item["code"], day)
            if close is None:
                continue
            entry = float(item["input"]["open_price"])
            net = net_return_pct((close / entry - 1) * 100, cost)
            row = {
                "code": item["code"], "name": item["name"], "rank": item["rank"],
                "entry": entry, "next_close": close, "net": round(net, 2),
            }
            rows.append(row)
            picks.append({"day": day, **row})
        if rows:
            days.append({
                "day": day, "run_id": latest_run, "count": len(rows),
                "avg": round(sum(row["net"] for row in rows) / len(rows), 2),
                "win": round(sum(1 for row in rows if row["net"] > 0) / len(rows) * 100, 1),
                "rows": rows,
            })
    values = [pick["net"] for pick in picks]
    result = {
        "since": since, "cached_at": _time.time(), "days": days,
        "summary": {
            "days": len(days), "picks": len(picks),
            "avg": round(sum(values) / len(values), 2) if values else None,
            "win": round(sum(1 for value in values if value > 0) / len(values) * 100, 1) if values else None,
        },
    }
    CACHE.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return result
