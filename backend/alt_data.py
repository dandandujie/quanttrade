"""免费历史 T-1 龙虎榜。

AKShare 的 `stock_lhb_detail_em` 支持按年批量返回，不依赖逐日请求；
这里只取「代码 / 上榜日 / 龙虎榜净买额」，按交易日合并到历史面板的 T-1。
"""
from __future__ import annotations

from pathlib import Path

import akshare as ak
import pandas as pd

from . import network

network.configure_network()

INDEX_CACHE = Path(__file__).resolve().parent.parent / "data" / "index_sh000001.parquet"


def index_daily() -> pd.DataFrame:
    if INDEX_CACHE.exists():
        return pd.read_parquet(INDEX_CACHE)
    import json
    import urllib.request

    url = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=sh000001,day,,,1600,"
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=10) as response:
        payload = json.loads(response.read().decode("utf-8", errors="replace"))
    rows = ((payload.get("data") or {}).get("sh000001") or {}).get("day") or []
    frame = pd.DataFrame(rows, columns=["date", "open", "close", "high", "low", "volume"])
    frame["date"] = pd.to_datetime(frame["date"]).dt.strftime("%Y-%m-%d")
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    frame["ma20"] = frame["close"].rolling(20, min_periods=20).mean().shift(1)
    frame["ma60"] = frame["close"].rolling(60, min_periods=60).mean().shift(1)
    frame["index_ret5"] = frame["close"].pct_change(5).shift(1)
    frame["index_above_ma20"] = (frame["close"].shift(1) > frame["ma20"]).astype(int)
    frame = frame[["date", "index_ret5", "index_above_ma20"]]
    INDEX_CACHE.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(INDEX_CACHE, index=False)
    return frame

CACHE = Path(__file__).resolve().parent.parent / "data" / "lhb_2020_2026.parquet"


def build(force: bool = False) -> pd.DataFrame:
    if CACHE.exists() and not force:
        return pd.read_parquet(CACHE)
    frames = []
    for year in range(2020, 2027):
        start = f"{year}0101"
        end = f"{year}1231" if year < 2026 else "20260911"
        try:
            frame = ak.stock_lhb_detail_em(start_date=start, end_date=end)
        except Exception:
            continue
        if frame is None or frame.empty:
            continue
        frame = frame.rename(columns={"代码": "code", "上榜日": "date", "龙虎榜净买额": "lhb_net"})
        frame["code"] = frame["code"].astype(str).str.zfill(6)
        frame["date"] = pd.to_datetime(frame["date"]).dt.strftime("%Y-%m-%d")
        frame["lhb_net"] = pd.to_numeric(frame["lhb_net"], errors="coerce").fillna(0.0)
        frames.append(frame[["code", "date", "lhb_net"]])
    out = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["code", "date", "lhb_net"])
    out = out.groupby(["code", "date"], as_index=False)["lhb_net"].sum()
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(CACHE, index=False)
    return out


def attach(panel: pd.DataFrame) -> pd.DataFrame:
    lhb = build()
    dates = sorted(panel["date"].unique())
    previous = {day: dates[index - 1] for index, day in enumerate(dates) if index > 0}
    frame = panel.copy()
    frame["prev_date"] = frame["date"].map(previous)
    merged = frame.merge(
        lhb.rename(columns={"date": "prev_date"}), on=["code", "prev_date"], how="left"
    )
    merged["lhb_prev"] = merged["lhb_net"].fillna(0.0)
    merged = merged.drop(columns=["prev_date", "lhb_net"], errors="ignore")
    merged = merged.merge(index_daily(), on="date", how="left")
    merged["index_ret5"] = merged["index_ret5"].fillna(0.0)
    merged["index_above_ma20"] = merged["index_above_ma20"].fillna(0).astype(int)
    return merged
