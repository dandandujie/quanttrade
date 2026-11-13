"""2020 年以来的本地 1 分钟历史回测。

用途：验证选股排名与 09:35 买入时机的稳定性，不参与实盘接口。
数据：本地 stock_1min parquet（2019 以前也有，默认从 2020-01-01 取），
按日聚合日线 + 09:30 竞价 bar + 09:35 首根 5 分钟确认 bar。
局限：本地 parquet 没有流通股本/换手率，用当前数据集里的流通股本做静态代理；
这是相对排名回测，不是逐笔成交回测，结论用于筛选规则而不是承诺收益。
"""
from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

import pandas as pd
import polars as pl

from . import database as db
from . import minute_store
from .costs import net_return_pct
from .data_sources import tencent_quotes
from .market_data import numeric
from .schemas import StockInput, StrategyConfig
from .strategy import ALLOWED_PREFIXES, main_reasons, metrics_for, score_main

CACHE = Path(__file__).resolve().parent.parent / "data" / "history_panel_2020.parquet"
START = "2020-01-01"
END = "2026-09-11"


def universe(limit: int = 300) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """全本地主板样本；静态流通股本优先当前数据集，缺失用腾讯盘口补。"""
    datasets = db.list_datasets()
    info: dict[str, dict[str, Any]] = {}
    if datasets:
        latest = max(datasets[:6], key=lambda item: int(item.get("stock_count") or 0))
        info = {str(item["code"]): item for item in db.get_stocks(int(latest["id"]))}
    codes = [
        path.stem.split(".")[0]
        for path in minute_store.ROOT.glob("*.parquet")
        if path.stem.split(".")[0].startswith(ALLOWED_PREFIXES)
    ]
    codes = sorted(set(codes))
    if limit and len(codes) > limit:
        step = max(1, len(codes) // limit)
        codes = codes[::step][:limit]
    missing = [code for code in codes if float(info.get(code, {}).get("free_float_shares") or 0) <= 0]
    if missing:
        try:
            quotes = tencent_quotes(missing)
        except Exception:
            quotes = {}
        for code, quote in quotes.items():
            price = float(quote.get("price") or 0)
            float_mcap = float(quote.get("float_mcap") or 0)
            if price > 0 and float_mcap > 0:
                item = dict(info.get(code, {}))
                item.update({
                    "code": code,
                    "name": str(quote.get("name") or item.get("name") or code),
                    "free_float_shares": float_mcap * 1e8 / price,
                })
                info[code] = item
    codes = [code for code in codes if float(info.get(code, {}).get("free_float_shares") or 0) > 0]
    return info, codes


def _stock_rows(code: str, info: dict[str, Any], start: str, end: str, gap_min: float = 0.02, gap_max: float = 0.10) -> list[dict[str, Any]]:
    path = minute_store.path_for(code)
    if not path.exists():
        return []
    frame = (
        pl.scan_parquet(str(path))
        .filter(
            (pl.col("trade_date") >= pl.lit(datetime.fromisoformat(start)))
            & (pl.col("trade_date") <= pl.lit(datetime.fromisoformat(end)))
            & (pl.col("trade_time").dt.time() >= pl.lit(time(9, 30)))
            & (pl.col("trade_time").dt.time() <= pl.lit(time(15, 0)))
        )
        .select("trade_date", "trade_time", "open", "high", "low", "close", "vol", "amount", "adj_factor")
        .collect()
    )
    if frame.is_empty():
        return []
    shares = float(info.get("free_float_shares") or 0)
    if shares <= 0:
        return []
    frame = frame.sort(["trade_date", "trade_time"])
    daily = (
        frame.group_by("trade_date", maintain_order=True)
        .agg(
            pl.col("open").first().alias("open"),
            pl.col("high").max().alias("high"),
            pl.col("low").min().alias("low"),
            pl.col("close").last().alias("close"),
            pl.col("vol").sum().alias("volume"),
            pl.col("amount").sum().alias("amount"),
            pl.col("adj_factor").last().alias("adj"),
        )
        .sort("trade_date")
    )
    first = (
        frame.filter(pl.col("trade_time").dt.time() == pl.lit(time(9, 30)))
        .select("trade_date", pl.col("open").alias("auction_open"), pl.col("vol").alias("auction_vol"),
                pl.col("amount").alias("auction_amount"))
    )
    first5 = frame.filter(pl.col("trade_time").dt.time() == pl.lit(time(9, 35))).select(
        "trade_date", pl.col("close").alias("first5")
    )
    first1 = frame.filter(pl.col("trade_time").dt.time() == pl.lit(time(9, 31))).select(
        "trade_date", pl.col("close").alias("first1")
    )
    head5 = (
        frame.filter(pl.col("trade_time").dt.time() <= pl.lit(time(9, 35)))
        .group_by("trade_date", maintain_order=True)
        .agg(pl.col("low").min().alias("low5"), pl.col("high").max().alias("high5"))
    )
    daily = (
        daily.join(first, on="trade_date", how="left")
        .join(first5, on="trade_date", how="left")
        .join(first1, on="trade_date", how="left")
        .join(head5, on="trade_date", how="left")
        .sort("trade_date")
        .to_pandas()
        .set_index("trade_date")
    )
    if daily.empty:
        return []
    daily["open"] = daily["auction_open"]
    daily["close_adj"] = daily["close"] * daily["adj"]
    daily["open_adj"] = daily["open"] * daily["adj"]
    daily["first5_adj"] = daily["first5"] * daily["adj"]
    daily["first1_adj"] = daily["first1"] * daily["adj"]
    daily["low5_adj"] = daily["low5"] * daily["adj"]
    daily["high5_adj"] = daily["high5"] * daily["adj"]
    daily["next_open_adj"] = daily["open_adj"].shift(-1)
    daily["next_close_adj"] = daily["close_adj"].shift(-1)
    daily["prev_close_adj"] = daily["close_adj"].shift(1)
    daily["ret5_prev"] = daily["close_adj"].shift(1) / daily["close_adj"].shift(6) - 1
    daily["ret20_prev"] = daily["close_adj"].shift(1) / daily["close_adj"].shift(21) - 1
    daily["pct"] = daily["close_adj"] / daily["prev_close_adj"] - 1
    daily["zt"] = daily["pct"] >= 0.095
    daily["zt_count_12"] = daily["zt"].rolling(12, min_periods=1).sum().shift(1).fillna(0).astype(int)
    daily["zt_count_4"] = daily["zt"].rolling(4, min_periods=1).sum().shift(1).fillna(0).astype(int)
    daily["zt_count_8"] = daily["zt"].rolling(8, min_periods=1).sum().shift(1).fillna(0).astype(int)
    daily["ma10"] = daily["close_adj"].rolling(10, min_periods=10).mean().shift(1)
    daily["yest_vol"] = daily["volume"].shift(1)
    daily["yest_pct"] = daily["pct"].shift(1) * 100
    daily["yest_high"] = daily["high"].shift(1) * daily["adj"].shift(1)
    daily["yest_low"] = daily["low"].shift(1) * daily["adj"].shift(1)
    daily["yest_zt"] = daily["zt"].shift(1).eq(True)
    # 连板高度：截至昨日连续涨停数
    consecutive = []
    run = 0
    for flag in daily["zt"].tolist():
        consecutive.append(run)
        run = run + 1 if flag else 0
    daily["zt_count"] = consecutive
    rows: list[dict[str, Any]] = []
    for day, row in daily.iterrows():
        prev_close_adj = float(row["prev_close_adj"]) if pd.notna(row["prev_close_adj"]) else 0.0
        yest_vol = float(row["yest_vol"]) if pd.notna(row["yest_vol"]) else 0.0
        if prev_close_adj <= 0 or yest_vol <= 0 or float(row["open_adj"]) <= 0:
            continue
        if pd.isna(row["first5"]) or float(row["first5_adj"]) <= 0:
            continue
        gap = float(row["open_adj"]) / prev_close_adj - 1
        if not gap_min <= gap <= gap_max:
            continue
        rows.append({
            "code": code,
            "name": str(info.get("name", code)),
            "date": day.date().isoformat(),
            "gap": gap,
            "auction_vol": float(row["auction_vol"]),
            "auction_amount": float(row["auction_amount"]),
            "yest_vol": yest_vol,
            "prev_close": prev_close_adj,
            "open_price": float(row["open_adj"]),
            "yest_high": float(row["yest_high"]) if pd.notna(row["yest_high"]) else prev_close_adj,
            "yest_low": float(row["yest_low"]) if pd.notna(row["yest_low"]) else prev_close_adj,
            "yest_pct": float(row["yest_pct"]) if pd.notna(row["yest_pct"]) else 0.0,
            "ret5_prev": float(row["ret5_prev"]) if pd.notna(row["ret5_prev"]) else 0.0,
            "ret20_prev": float(row["ret20_prev"]) if pd.notna(row["ret20_prev"]) else 0.0,
            "yest_turnover": yest_vol / shares * 100,
            "free_float_shares": shares,
            "free_market_cap": shares * prev_close_adj / 1e8,
            "is_zt": bool(row["yest_zt"]),
            "zt_count": int(row["zt_count"]),
            "zt_count_4": int(row["zt_count_4"]),
            "zt_count_8": int(row["zt_count_8"]),
            "zt_count_12": int(row["zt_count_12"]),
            "ma10": float(row["ma10"]) if pd.notna(row["ma10"]) else 0.0,
            "first5_ret": float(row["first5_adj"]) / float(row["open_adj"]) - 1,
            "first1_ret": float(row["first1_adj"]) / float(row["open_adj"]) - 1 if pd.notna(row["first1_adj"]) else None,
            "low5_ret": float(row["low5_adj"]) / float(row["open_adj"]) - 1,
            "high5_ret": float(row["high5_adj"]) / float(row["open_adj"]) - 1,
            "close_ret": float(row["close_adj"]) / float(row["open_adj"]) - 1,
            "close_from_first5": float(row["close_adj"]) / float(row["first5_adj"]) - 1,
            "close_from_first1": float(row["close_adj"]) / float(row["first1_adj"]) - 1 if pd.notna(row["first1_adj"]) else None,
            "next_open_ret": float(row["next_open_adj"]) / float(row["open_adj"]) - 1 if pd.notna(row["next_open_adj"]) else None,
            "next_close_ret": float(row["next_close_adj"]) / float(row["open_adj"]) - 1 if pd.notna(row["next_close_adj"]) else None,
            "next_close_from_close": float(row["next_close_adj"]) / float(row["close_adj"]) - 1 if pd.notna(row["next_close_adj"]) else None,
        })
    return rows


def build_panel(
    limit: int = 300, start: str = START, end: str = END, force: bool = False,
    gap_min: float = 0.02, gap_max: float = 0.10, cache: Path | None = None,
) -> pd.DataFrame:
    target = cache or CACHE
    if target.exists() and not force:
        frame = pd.read_parquet(target)
        if not frame.empty and frame["date"].min() <= start and frame["date"].max() >= end:
            return frame
    info, codes = universe(limit)
    rows: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = [
            executor.submit(_stock_rows, code, info[code], start, end, gap_min, gap_max)
            for code in codes
        ]
        for future in as_completed(futures):
            rows.extend(future.result())
    panel = pd.DataFrame(rows)
    if panel.empty:
        return panel
    market_zt = panel.groupby("date")["is_zt"].sum().rename("market_zt_count")
    panel = panel.merge(market_zt, left_on="date", right_index=True, how="left")
    panel = panel.sort_values(["date", "code"]).reset_index(drop=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    panel.to_parquet(target, index=False)
    return panel


def _stock(row: pd.Series) -> StockInput:
    return StockInput(
        code=row["code"], name=row["name"], free_market_cap=row["free_market_cap"],
        prev_close=row["prev_close"], open_price=row["open_price"],
        yest_vol=row["yest_vol"], auction_vol=row["auction_vol"],
        auction_amount=row["auction_amount"], free_float_shares=row["free_float_shares"],
        is_zt=bool(row["is_zt"]), zt_count=int(row["zt_count"]),
        zt_count_4=int(row["zt_count_4"]), zt_count_8=int(row["zt_count_8"]),
        zt_count_12=int(row["zt_count_12"]), yest_turnover=row["yest_turnover"],
        yest_high=row["yest_high"], yest_low=row["yest_low"], yest_pct=row["yest_pct"],
        ma10=row["ma10"], market_zt_count=int(row["market_zt_count"]),
        seal_amount=0, bid1_unmatched_vol=0, micro_structure_available=False,
    )


def _timing(row: pd.Series, config: dict[str, Any]) -> tuple[str, float | None]:
    first5 = float(row["first5_ret"])
    if first5 < -0.02:
        return "abort", None
    if first5 < 0 or first5 > 0.02:
        return "skip", None
    return "buy", net_return_pct(float(row["close_from_first5"]) * 100, config)


def _timing_first1(row: pd.Series, config: dict[str, Any]) -> tuple[str, float | None]:
    first1 = row.get("first1_ret")
    close_from = row.get("close_from_first1")
    if first1 is None or pd.isna(first1) or close_from is None or pd.isna(close_from):
        return "missing", None
    first1 = float(first1)
    if first1 < -0.02:
        return "abort", None
    if first1 > 0:
        return "skip", None
    return "buy", net_return_pct(float(close_from) * 100, config)


def _timing_dip(row: pd.Series, config: dict[str, Any]) -> tuple[str, float | None]:
    first5 = float(row["first5_ret"])
    if first5 < -0.02:
        return "abort", None
    if first5 > 0:
        return "skip", None
    return "buy", net_return_pct(float(row["close_from_first5"]) * 100, config)


def run_backtest(
    panel: pd.DataFrame,
    config: StrategyConfig | None = None,
    top_n: int = 9,
    ranking: str = "score",
) -> dict[str, Any]:
    config = config or StrategyConfig()
    cost_config = config.model_dump()
    picks: list[dict[str, Any]] = []
    for day, group in panel.groupby("date", sort=True):
        qualified = []
        for _, row in group.iterrows():
            try:
                stock = _stock(row)
                metrics = metrics_for(stock)
                reasons = main_reasons(stock, metrics, config)
                if reasons:
                    continue
                score = score_main(stock, metrics, config)
            except Exception:
                continue
            item = row.to_dict()
            item["score"] = float(score["total"])
            item["vol_ratio"] = float(metrics["vol_ratio"])
            item["auction_turnover"] = float(metrics["auction_turnover"])
            item["baseline_net"] = net_return_pct(float(row["close_ret"]) * 100, cost_config)
            item["first5_net"] = net_return_pct(float(row["close_from_first5"]) * 100, cost_config)
            status, confirmed = _timing(row, cost_config)
            item["timing_status"] = status
            item["confirmed_net"] = confirmed
            status1, confirmed1 = _timing_first1(row, cost_config)
            item["timing1_status"] = status1
            item["confirmed1_net"] = confirmed1
            status_dip, confirmed_dip = _timing_dip(row, cost_config)
            item["timing_dip_status"] = status_dip
            item["confirmed_dip_net"] = confirmed_dip
            qualified.append(item)
        if not qualified:
            continue
        if ranking == "gap":
            qualified.sort(key=lambda item: -abs(item["gap"] * 100 - 5.2), reverse=True)
        elif ranking == "gap_low":
            qualified.sort(key=lambda item: item["gap"])
        elif ranking == "score_low":
            qualified.sort(key=lambda item: item["score"])
        elif ranking == "vol":
            qualified.sort(key=lambda item: item["vol_ratio"], reverse=True)
        elif ranking == "vol_low":
            qualified.sort(key=lambda item: item["vol_ratio"])
        elif ranking == "gene":
            qualified.sort(key=lambda item: (item["zt_count_12"], item["score"]), reverse=True)
        elif ranking == "gene_low":
            qualified.sort(key=lambda item: item["zt_count_12"])
        elif ranking == "amount":
            qualified.sort(key=lambda item: item["auction_amount"], reverse=True)
        elif ranking == "amount_low":
            qualified.sort(key=lambda item: item["auction_amount"])
        elif ranking == "first5":
            qualified.sort(key=lambda item: item["first5_ret"], reverse=True)
        elif ranking == "first5_low":
            qualified.sort(key=lambda item: item["first5_ret"])
        elif ranking == "dip":
            qualified.sort(key=lambda item: -abs(item["first5_ret"] * 100 + 0.8), reverse=True)
        elif ranking == "dip_combo":
            qualified.sort(
                key=lambda item: (
                    0 if -0.01 <= item["first5_ret"] <= 0 else 1,
                    -abs(item["first5_ret"] * 100 + 0.5),
                    -item["free_market_cap"],
                    item["auction_turnover"],
                )
            )
        elif ranking == "dip_small":
            qualified.sort(
                key=lambda item: (
                    0 if (
                        -0.01 <= item["first5_ret"] <= 0
                        and item["free_market_cap"] <= 60
                        and item["auction_turnover"] >= 0.2
                    ) else 1,
                    -abs(item["first5_ret"] * 100 + 0.5),
                    -item["free_market_cap"],
                    item["auction_turnover"],
                )
            )
        else:
            qualified.sort(key=lambda item: item["score"], reverse=True)
        for rank, item in enumerate(qualified[:top_n], start=1):
            item["rank"] = rank
            picks.append(item)
    if not picks:
        return {"picks": 0, "ranking": ranking}
    frame = pd.DataFrame(picks)
    frame["year"] = frame["date"].str[:4]
    return {
        "ranking": ranking,
        "picks": len(frame),
        "days": int(frame["date"].nunique()),
        "baseline": _metrics(frame, "baseline_net"),
        "first5": _metrics(frame, "first5_net"),
        "confirmed": _metrics(frame[frame["timing_status"] == "buy"], "confirmed_net"),
        "confirmed_first1": _metrics(frame[frame["timing1_status"] == "buy"], "confirmed1_net"),
        "confirmed_dip": _metrics(frame[frame["timing_dip_status"] == "buy"], "confirmed_dip_net"),
        "timing_counts": frame["timing_status"].value_counts().to_dict(),
        "timing1_counts": frame["timing1_status"].value_counts().to_dict(),
        "timing_dip_counts": frame["timing_dip_status"].value_counts().to_dict(),
        "by_year": {
            year: _metrics(item, "baseline_net")
            for year, item in frame.groupby("year")
        },
        "top": {
            f"top{n}": _metrics(frame[frame["rank"] <= n], "baseline_net")
            for n in (1, 3, 5, 9, 12)
            if (frame["rank"] <= n).any()
        },
    }


def compare_rankings(panel: pd.DataFrame, top_n: int = 9) -> dict[str, Any]:
    return {
        ranking: run_backtest(panel, StrategyConfig(), top_n, ranking)
        for ranking in (
            "score", "score_low", "gap", "gap_low", "vol", "vol_low",
            "gene", "gene_low", "amount", "amount_low", "first5", "first5_low", "dip", "dip_combo", "dip_small",
        )
    }


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
        "profit_factor": round(float(gains / losses), 2) if losses > 0 else None,
        "max_drawdown": round(float((values.cumsum() - values.cumsum().cummax()).min()), 2),
    }


def main() -> None:
    panel = build_panel(limit=300)
    result = run_backtest(panel, StrategyConfig(), top_n=12)
    print(result)


if __name__ == "__main__":
    main()
