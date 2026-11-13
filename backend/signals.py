"""09:30 后的买入信号监控。

09:25 选股只给出计划；真正买入要等开盘承接确认。本模块用腾讯五档/成交
数据每 5 秒更新一次，按计划价格区间、当日均价线和放弃价生成
BUY / ABORT / CANCEL / STOP 事件，并写入 SQLite signals 表。
"""
from __future__ import annotations

import threading
import time as clock
from datetime import datetime, time as day_time
from typing import Any

from . import database as db
from .data_sources import tencent_quotes

WINDOW_START = day_time(9, 25, 0)
WINDOW_END = day_time(9, 40, 0)
CONFIRM_START = day_time(9, 30, 0)
CONFIRM_END = day_time(9, 35, 0)

ACTION_LABELS = {
    "ARMED": "等待确认",
    "WAIT": "不追高，等回踩",
    "BUY": "触发买入",
    "ABORT": "放弃",
    "STOP": "止损",
    "CANCEL": "过时取消",
}


def plans_from_result(result: dict[str, Any]) -> list[dict[str, Any]]:
    plans: dict[str, dict[str, Any]] = {}
    for pool in ("main", "candidates"):
        for item in result.get(pool, []):
            code = item["code"]
            if code in plans:
                continue
            plan = dict(item.get("buy_plan") or {})
            if not plan:
                continue
            if item.get("preferred"):
                priority = 0
            elif item.get("return_rank") == 1:
                priority = 1
            elif item.get("return_rank") and item["return_rank"] <= 3:
                priority = 2
            else:
                priority = 3
            plan.update({
                "code": code,
                "name": item["name"],
                "pool": pool,
                "rank": item.get("rank"),
                "return_rank": item.get("return_rank"),
                "preferred": bool(item.get("preferred")),
                "status": "ARMED",
                "last_price": None,
                "last_time": "",
                "priority": priority,
                "monitored": priority <= 2,
            })
            plans[code] = plan
    return sorted(plans.values(), key=lambda item: (item["priority"], item.get("return_rank") or 999))


def evaluate_tick(plan: dict[str, Any], quote: dict[str, Any], now: datetime) -> dict[str, Any] | None:
    """纯函数：给一个盘口快照，返回状态变化或 None。"""
    status = plan.get("status")
    price = float(quote.get("price") or 0)
    if status not in {"ARMED", "WAIT", "BUY"} or price <= 0:
        return None
    current = now.time()
    average = float(quote.get("avg_price") or 0)
    if status in {"ARMED", "WAIT"}:
        if current < CONFIRM_START:
            return None
        if current > CONFIRM_END:
            return {"action": "CANCEL", "reason": "09:35 前未触发", "price": price}
        if price < plan["abort_below"]:
            return {"action": "ABORT", "reason": f"跌破放弃价 {plan['abort_below']}", "price": price}
        if price > plan["entry_high"]:
            if status == "WAIT":
                return None
            return {
                "action": "WAIT",
                "reason": f"高于追高上沿 {plan['entry_high']}，等回踩",
                "price": price,
            }
        if price >= plan["entry_low"] and (average <= 0 or price >= average):
            return {
                "action": "BUY",
                "reason": f"价格 {price:.2f} 在买入区且站上均价 {average:.2f}",
                "price": price,
            }
        return None
    if status == "BUY" and price < plan["stop_below"]:
        return {"action": "STOP", "reason": f"跌破止损 {plan['stop_below']}", "price": price}
    return None


class SignalMonitor:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._day = ""
        self._plans: list[dict[str, Any]] = []
        self._events: list[dict[str, Any]] = []
        self._running = False
        self._error = ""
        self._started_at = ""
        self._last_poll = ""

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "running": self._running,
                "day": self._day,
                "started_at": self._started_at,
                "last_poll": self._last_poll,
                "error": self._error,
                "plans": [dict(plan) for plan in self._plans],
                "events": list(self._events[-100:]),
                "window": "09:30:00—09:35:00",
            }

    def start(self, result: dict[str, Any], day: str) -> dict[str, Any]:
        with self._lock:
            if self._running and self._day == day:
                return self.snapshot()
            self._stop.set()
            self._day = day
            self._plans = plans_from_result(result)
            self._events = []
            self._error = ""
            self._started_at = db.now_iso()
            self._running = True
            self._stop = threading.Event()
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
            return self.snapshot()

    def stop(self) -> dict[str, Any]:
        self._stop.set()
        with self._lock:
            self._running = False
        return self.snapshot()

    def _record(self, plan: dict[str, Any], action: str, price: float, reason: str) -> None:
        event = {
            "code": plan["code"], "name": plan["name"], "action": action,
            "price": round(price, 2), "reason": reason, "at": db.now_iso(),
        }
        with self._lock:
            for current in self._plans:
                if current["code"] == plan["code"]:
                    current["status"] = action
                    current["last_price"] = round(price, 2)
                    current["last_time"] = event["at"]
                    break
            self._events.append(event)
        db.save_signal(self._day, plan["code"], plan["name"], action, price, reason, plan)

    def _run(self) -> None:
        while not self._stop.is_set():
            now = datetime.now(db.TZ_CN)
            if now.date().isoformat() != self._day:
                self._stop.wait(10)
                continue
            if now.time() > WINDOW_END:
                with self._lock:
                    for plan in self._plans:
                        if plan["status"] in {"ARMED", "WAIT"}:
                            plan["status"] = "CANCEL"
                with self._lock:
                    self._running = False
                return
            if now.time() < WINDOW_START:
                self._stop.wait(5)
                continue
            codes = [
                plan["code"] for plan in self._plans
                if plan.get("monitored", True) and plan["status"] in {"ARMED", "WAIT", "BUY"}
            ]
            if not codes:
                with self._lock:
                    self._running = False
                return
            try:
                quotes = tencent_quotes(codes)
            except Exception as exc:  # noqa: BLE001 - 行情源失败只记录
                with self._lock:
                    self._error = str(exc)
                self._stop.wait(5)
                continue
            with self._lock:
                self._last_poll = now.isoformat(timespec="seconds")
                current_plans = [dict(plan) for plan in self._plans]
            for plan in current_plans:
                quote = quotes.get(plan["code"])
                if not quote:
                    continue
                change = evaluate_tick(plan, quote, now)
                if change:
                    self._record(plan, change["action"], change["price"], change["reason"])
            self._stop.wait(5)


monitor = SignalMonitor()
