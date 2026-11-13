"""交易成本口径：双边滑点 + 佣金/过户费 + 卖出印花税。

所有收益都相对买入成本（集合竞价价或 09:30 开盘价）。默认值按 A 股常见水平：
佣金万 2.5 + 过户费 0.001% ≈ 单边 0.026%，印花税卖出 0.05%。
"""
from __future__ import annotations

from typing import Any

DEFAULT_SLIPPAGE = 0.3
DEFAULT_FEE = 0.026
DEFAULT_STAMP_DUTY = 0.05


def cost_params(config: dict[str, Any] | None) -> tuple[float, float, float]:
    """返回 (双边滑点, 单边费用, 卖出印花税)，单位 %。"""
    config = config or {}
    return (
        float(config.get("slippage_pct", DEFAULT_SLIPPAGE)),
        float(config.get("fee_pct", DEFAULT_FEE)),
        float(config.get("stamp_duty_pct", DEFAULT_STAMP_DUTY)),
    )


def buy_factor(config: dict[str, Any] | None) -> float:
    """买入成本系数：滑点与费用都让成交价更高。"""
    slip, fee, _ = cost_params(config)
    return 1 + (slip + fee) / 100


def sell_factor(config: dict[str, Any] | None) -> float:
    """卖出到手系数：滑点、费用、印花税都让到手价更低。"""
    slip, fee, stamp = cost_params(config)
    return 1 - (slip + fee + stamp) / 100


def net_return_pct(raw_return: float, config: dict[str, Any] | None) -> float:
    """毛收益（%）→ 净收益（%）。"""
    return ((1 + float(raw_return) / 100) * sell_factor(config) / buy_factor(config) - 1) * 100


def net_threshold_pct(config: dict[str, Any] | None) -> float:
    """净收益由负转正所需的毛收益（%）。"""
    return (buy_factor(config) / sell_factor(config) - 1) * 100
