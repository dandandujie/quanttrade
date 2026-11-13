"""市场情绪温度：用本地日线计算涨停/跌停、晋级率、炸板率、宽度与成交额。

参考公开复盘框架：晋级率/炸板率权重较高，连板高度、跌停家数、成交额作辅助；
情绪值用自身历史分位而非固定阈值。它只描述环境，不直接预测个股。
"""
from __future__ import annotations

import math
import time
from typing import Any

from . import database as db

WINDOW = 60


def _percentile(values: list[float], value: float) -> float:
    if not values:
        return 0.5
    return sum(1 for item in values if item <= value) / len(values)


def _metrics() -> dict[str, dict[str, float]]:
    """逐交易日计算市场情绪原始指标。"""
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT code, trade_date, close, high, pct, amount FROM daily_bars ORDER BY trade_date, code"
        ).fetchall()
    by_day: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_day.setdefault(row["trade_date"], []).append(dict(row))
    result: dict[str, dict[str, float]] = {}
    streaks: dict[str, int] = {}
    previous_close: dict[str, float] = {}
    previous_limits: set[str] = set()
    for day in sorted(by_day):
        items = by_day[day]
        limit_up: set[str] = set()
        limit_down = 0
        touched: set[str] = set()
        sealed: set[str] = set()
        up = down = 0
        amount = 0.0
        for item in items:
            code = item["code"]
            pct = float(item["pct"])
            close = float(item["close"])
            high = float(item["high"])
            amount += float(item["amount"])
            if pct > 0:
                up += 1
            elif pct < 0:
                down += 1
            if pct >= 9.5:
                limit_up.add(code)
                streaks[code] = streaks.get(code, 0) + 1
            else:
                streaks[code] = 0
            if pct <= -9.5:
                limit_down += 1
            prev = previous_close.get(code)
            if prev and prev > 0:
                limit_price = math.floor(prev * 1.1 * 100 + 0.5 + 1e-9) / 100
                if high >= limit_price - 0.001:
                    touched.add(code)
                    if close >= limit_price - 0.001:
                        sealed.add(code)
            previous_close[code] = close
        promotion = (
            len(previous_limits & limit_up) / len(previous_limits)
            if previous_limits else 0.0
        )
        break_rate = (len(touched) - len(sealed)) / len(touched) if touched else 0.0
        result[day] = {
            "limit_up": float(len(limit_up)),
            "limit_down": float(limit_down),
            "promotion": promotion,
            "break_rate": break_rate,
            "breadth": up / (up + down) if up + down else 0.5,
            "amount": amount,
            "max_streak": float(max(streaks.values(), default=0)),
            "count": float(len(items)),
        }
        previous_limits = limit_up
    return result


_CACHE: tuple[float, dict[str, dict[str, float]]] | None = None
_CACHE_TTL = 600


def _all_metrics() -> dict[str, dict[str, float]]:
    global _CACHE
    now = time.time()
    if _CACHE is None or now - _CACHE[0] > _CACHE_TTL:
        _CACHE = (now, _metrics())
    return _CACHE[1]


def sentiment_for(day: str, include_day: bool = False) -> dict[str, Any]:
    """返回 day 之前（或包含 day）的情绪温度；用于 09:25 时只取 T-1。"""
    metrics = _all_metrics()
    days = sorted(metrics)
    current_day = day if include_day else None
    if current_day is None:
        previous = [item for item in days if item < day]
        if not previous:
            return {"score": 50.0, "label": "未知", "position_factor": 0.5, "metrics": {}}
        current_day = previous[-1]
    if current_day not in metrics:
        return {"score": 50.0, "label": "未知", "position_factor": 0.5, "metrics": {}}
    history = [metrics[item] for item in days if item <= current_day][-WINDOW:]
    current = metrics[current_day]
    def pct(key: str, invert: bool = False) -> float:
        value = _percentile([item[key] for item in history], current[key])
        return 1.0 - value if invert else value
    score = 100.0 * (
        0.25 * pct("limit_up")
        + 0.20 * pct("promotion")
        + 0.20 * pct("break_rate", invert=True)
        + 0.15 * pct("limit_down", invert=True)
        + 0.10 * pct("breadth")
        + 0.05 * pct("amount")
        + 0.05 * pct("max_streak")
    )
    score = round(max(0.0, min(100.0, score)), 1)
    if score >= 70:
        label = "主升"
    elif score >= 50:
        label = "分化"
    elif score >= 30:
        label = "退潮"
    else:
        label = "冰点"
    # 短周期不是“情绪越高越加仓”：高情绪更拥挤，冰点则等待修复。
    if score >= 80:
        position_factor = 0.5
    elif score >= 60:
        position_factor = 0.7
    elif score >= 40:
        position_factor = 1.0
    elif score >= 30:
        position_factor = 0.8
    else:
        position_factor = 0.4
    if score >= 80:
        hint = "情绪过热：控制仓位，不追高"
    elif score >= 60:
        hint = "情绪分化：标准仓位 ×0.7"
    elif score >= 40:
        hint = "情绪中性：按个股信号正常执行"
    elif score >= 30:
        hint = "情绪退潮：标准仓位 ×0.8"
    else:
        hint = "情绪冰点：减少出手，等待修复"
    return {
        "score": score,
        "label": label,
        "position_factor": position_factor,
        "position_hint": hint,
        "as_of": current_day,
        "metrics": {key: round(value, 4) for key, value in current.items()},
    }
