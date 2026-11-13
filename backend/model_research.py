"""在大样本上验证“可执行”的排名：训练 2020—2023，测试 2024—2026。

特征只用 09:35 前可得的量价 + 09:35 首根 5 分钟；标签用 T+1 收盘（买入按 09:35，
卖出按次日收盘），扣费。输出按模型预测排序的 Top1/3/9 指标，避免只看全样本均值。
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

from .costs import net_return_pct
from .history_backtest import _stock
from .schemas import StrategyConfig
from .strategy import main_reasons, metrics_for


FEATURES = [
    "gap", "vol_ratio", "auction_turnover", "auction_amount_log",
    "free_market_cap_log", "zt_count_12", "zt_count", "yest_pct",
    "ma10_ratio", "first5_ret", "first1_ret", "low5_ret", "high5_ret",
    "market_zt_count", "lhb_prev_log", "index_ret5", "index_above_ma20",
]


def qualified(panel: pd.DataFrame) -> pd.DataFrame:
    config = StrategyConfig()
    rows: list[dict[str, Any]] = []
    for _, row in panel.iterrows():
        try:
            stock = _stock(row)
            metrics = metrics_for(stock)
            if main_reasons(stock, metrics, config):
                continue
            item = row.to_dict()
            item["vol_ratio"] = float(metrics["vol_ratio"])
            item["auction_turnover"] = float(metrics["auction_turnover"])
            item["auction_amount_log"] = float(np.log1p(max(0.0, item["auction_amount"])))
            item["free_market_cap_log"] = float(np.log1p(max(0.0, item["free_market_cap"])))
            item["ma10_ratio"] = (
                float(item["open_price"]) / float(item["ma10"]) - 1
                if item.get("ma10") and item["ma10"] > 0 else 0.0
            )
            item["market_zt_count"] = int(item.get("market_zt_count") or 0)
            item["index_ret5"] = float(item.get("index_ret5") or 0.0)
            item["index_above_ma20"] = int(item.get("index_above_ma20") or 0)
            lhb = float(item.get("lhb_prev") or 0.0)
            item["lhb_prev_log"] = float(np.sign(lhb) * np.log1p(abs(lhb)))
            item["first1_ret"] = float(item.get("first1_ret") or 0.0)
            item["low5_ret"] = float(item.get("low5_ret") or 0.0)
            item["high5_ret"] = float(item.get("high5_ret") or 0.0)
            if pd.isna(item.get("next_close_ret")):
                continue
            cost = config.model_dump()
            item["next_net"] = net_return_pct(
                (float(item["next_close_ret"]) - float(item["first5_ret"])) * 100, cost
            )
            item["close_net"] = net_return_pct(
                (float(item["close_ret"]) - float(item["first5_ret"])) * 100, cost
            )
            rows.append(item)
        except Exception:
            continue
    return pd.DataFrame(rows)


def _metrics(frame: pd.DataFrame, column: str) -> dict[str, Any]:
    values = pd.to_numeric(frame[column], errors="coerce").dropna()
    if values.empty:
        return {"n": 0}
    gains = values[values > 0].sum()
    losses = -values[values < 0].sum()
    return {
        "n": int(len(values)),
        "avg": round(float(values.mean()), 2),
        "win": round(float((values > 0).mean()) * 100, 1),
        "median": round(float(values.median()), 2),
        "pf": round(float(gains / losses), 2) if losses > 0 else None,
    }


def validate(panel: pd.DataFrame, train_end: str = "2023-12-31", target: str = "next_net") -> dict[str, Any]:
    frame = qualified(panel)
    if frame.empty:
        return {"rows": 0}
    frame = frame.sort_values("date")
    train = frame[frame["date"] <= train_end]
    test = frame[frame["date"] > train_end].copy()
    if len(train) < 100 or test.empty:
        return {"rows": len(frame), "train": len(train), "test": len(test)}
    model = HistGradientBoostingRegressor(
        max_iter=200, learning_rate=0.04, max_leaf_nodes=15,
        min_samples_leaf=20, l2_regularization=0.2, random_state=7,
    )
    model.fit(train[FEATURES], train[target])
    test["prediction"] = model.predict(test[FEATURES])
    result: dict[str, Any] = {
        "rows": len(frame), "train": len(train), "test": len(test),
        "target": target, "all": _metrics(test, target),
    }
    for top in (1, 3, 5, 9, 12):
        selected = (
            test.sort_values(["date", "prediction"], ascending=[True, False])
            .groupby("date", sort=False)
            .head(top)
        )
        result[f"top{top}"] = _metrics(selected, target)
    result["by_year"] = {
        year: _metrics(item, target)
        for year, item in test.groupby(test["date"].str[:4])
    }
    # 特征重要性（permutation 太慢，用简单相关系数代替方向检查）
    result["corr"] = {
        column: round(float(test[column].corr(test[target])), 3)
        for column in FEATURES if test[column].nunique() > 1
    }
    return result


def main() -> None:
    from .alt_data import attach
    from .history_backtest import build_panel

    panel = attach(build_panel(limit=1000))
    import json
    print(json.dumps(validate(panel), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
