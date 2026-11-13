import csv
import io
import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import asynccontextmanager
from datetime import date, datetime, time, timedelta
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import database as db
from .closing_samples import CLOSING_SAMPLES
from .costs import net_return_pct
from .csv_import import parse_csv, template_csv
from .doc_samples import build_report, core_flags_for, doc_ranks_for
from .marks import excluded_for, load_marks, save_marks
from .data_sources import breadth_from_spot
from .market_data import QUALITY_NOTES, enrich_dataset, fetch_history, sync_market, trading_days
from .performance import evaluate_run
from .sample_data import DEMO_DAY, DEMO_STOCKS
from .schemas import PerformanceRequest, ScreenRequest, StockInput, StrategyConfig, SyncRequest
from .sentiment import sentiment_for
from .signals import monitor as signal_monitor, plans_from_result
from .strategy import evaluate

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
scheduler = BackgroundScheduler(timezone=db.TZ_CN)
sync_lock = threading.Lock()
performance_lock = threading.Lock()


def run_performance(run_id: int) -> dict | None:
    if not performance_lock.acquire(blocking=False):
        return None
    try:
        return evaluate_run(run_id)
    finally:
        performance_lock.release()


def start_signal_monitor() -> None:
    """09:27 为最近一次当日运行启动 09:30—09:35 买入信号监控。"""
    now = datetime.now(db.TZ_CN)
    if not (time(9, 25) <= now.time() <= time(9, 40)):
        return
    datasets = [
        item for item in db.list_datasets()
        if item["source"] == "akshare" and item["trading_day"] == now.date().isoformat() and item["latest_run_id"]
    ]
    dataset = max(datasets, key=lambda item: item.get("stock_count", 0), default=None)
    if not dataset:
        return
    run = db.get_run(dataset["latest_run_id"])
    if run:
        signal_monitor.start(run, dataset["trading_day"])


def review_today() -> None:
    today = datetime.now(db.TZ_CN).date().isoformat()
    candidates = [
        item for item in db.list_datasets()
        if item["source"] == "akshare" and item["trading_day"] == today and item["latest_run_id"]
    ]
    dataset = max(candidates, key=lambda item: item.get("stock_count", 0), default=None)
    if dataset:
        run_performance(dataset["latest_run_id"])


def run_screen(dataset_id: int, config: StrategyConfig) -> dict:
    dataset = db.get_dataset(dataset_id)
    if not dataset:
        raise HTTPException(status_code=404, detail="数据集不存在")
    raw_stocks = db.get_stocks(dataset_id)
    if not raw_stocks:
        raise HTTPException(status_code=400, detail="数据集为空")
    stocks = [StockInput(**stock) for stock in raw_stocks]
    result = evaluate(
        stocks,
        dataset,
        config,
        doc_ranks_for(dataset["trading_day"]),
        excluded_for(dataset["trading_day"]),
        core_flags_for(dataset["trading_day"]),
    )
    run_id = db.create_run(dataset_id, config.model_dump(), result)
    result["run_id"] = run_id
    now = datetime.now(db.TZ_CN)
    if (
        dataset["trading_day"] == now.date().isoformat()
        and time(9, 25) <= now.time() <= time(9, 40)
    ):
        signal_monitor.start(result, dataset["trading_day"])
    return result


def seed_demo() -> None:
    if db.list_datasets():
        return
    dataset_id = db.create_dataset(DEMO_DAY, "09:25策略演示样本", "demo", DEMO_STOCKS)
    config = StrategyConfig()
    dataset = db.get_dataset(dataset_id)
    stocks = [StockInput(**stock) for stock in DEMO_STOCKS]
    result = evaluate(stocks, dataset, config)
    db.create_run(dataset_id, config.model_dump(), result)


def execute_sync(job_id: str, request: SyncRequest) -> None:
    try:
        synced = sync_market(job_id, request.trading_day, request.limit)
        result = run_screen(synced["dataset_id"], StrategyConfig())
        db.update_sync_job(
            job_id,
            status="completed",
            run_id=result["run_id"],
            message=f"同步完成 · {synced['stock_count']} 只有效样本 · {synced['failed_count']} 只失败",
        )
        if datetime.now(db.TZ_CN).time() >= time(11, 35):
            threading.Thread(target=run_performance, args=(result["run_id"],), daemon=True).start()
    except Exception as exc:
        db.update_sync_job(job_id, status="failed", error=str(exc), message=f"同步失败：{exc}")
    finally:
        sync_lock.release()


def start_sync(request: SyncRequest) -> str | None:
    if not sync_lock.acquire(blocking=False):
        return None
    try:
        job_id = uuid.uuid4().hex[:12]
        db.create_sync_job(job_id)
        threading.Thread(target=execute_sync, args=(job_id, request), daemon=True).start()
        return job_id
    except Exception:
        sync_lock.release()
        raise


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init_db()
    seed_demo()
    scheduler.add_job(
        lambda: start_sync(SyncRequest()),
        "cron",
        id="auction-sync",
        day_of_week="mon-fri",
        hour=9,
        minute=26,
        second=15,
        replace_existing=True,
    )
    scheduler.add_job(
        review_today,
        "cron",
        id="performance-review",
        day_of_week="mon-fri",
        hour=15,
        minute=5,
        replace_existing=True,
    )
    scheduler.add_job(
        warm_closing,
        "cron",
        id="closing-warm",
        day_of_week="mon-fri",
        hour=14,
        minute=46,
        second=10,
        replace_existing=True,
    )
    scheduler.add_job(
        start_signal_monitor,
        "cron",
        id="signal-monitor",
        day_of_week="mon-fri",
        hour=9,
        minute=27,
        second=5,
        replace_existing=True,
    )
    scheduler.start()
    latest = db.get_latest_sync_job()
    today = datetime.now(db.TZ_CN).date()
    try:
        calendar = trading_days()
        latest_day = max(day for day in calendar if day <= today)
    except Exception:  # noqa: BLE001 - 交易日历不可用时退回今天，交给同步任务自己报错
        latest_day = today
    has_latest = any(
        item["source"] == "akshare" and item["trading_day"] == latest_day.isoformat()
        and int(item.get("stock_count") or 0) >= 30
        for item in db.list_datasets()
    )
    # 周末重启不应再把上一交易日重抓一遍：有「最近交易日」的数据集就跳过自动同步。
    should_sync = (
        not latest
        or latest["status"] in {"queued", "running", "failed"}
        or not has_latest
    )
    now_time = datetime.now(db.TZ_CN).time()
    in_sync_window = time(9, 26) <= now_time < time(11, 30)
    if should_sync and in_sync_window:
        start_sync(SyncRequest())
    elif now_time >= time(11, 35):
        threading.Thread(target=review_today, daemon=True).start()
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title="竞价·0925选股系统", version="2.2.0", lifespan=lifespan)


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "auction-0925"}


@app.get("/api/config")
def get_config():
    return StrategyConfig().model_dump()


@app.get("/api/market/status")
def market_status():
    scheduled = scheduler.get_job("auction-sync")
    return {
        "provider": "AKShare / 东方财富 + 腾讯行情",
        "requires_api_key": False,
        "schedule": "交易日 09:26:15 自动同步",
        "next_run": str(scheduled.next_run_time) if scheduled else None,
        "active": sync_lock.locked(),
        "latest_job": db.get_latest_sync_job(),
        "quality_notes": QUALITY_NOTES,
        "research_warning": "2020—2026 本地 1 分钟回测：当前主档/买入规则扣费后无正期望，结果仅供研究，不构成买入建议",
        "sources": [
            {
                "id": "akshare",
                "badge": "AK",
                "name": "AKShare / 东方财富",
                "fields": "涨停池 · 日线 · 分钟 · 资金流排行 · 龙虎榜",
                "status": "已接入",
            },
            {
                "id": "tencent",
                "badge": "TX",
                "name": "腾讯行情",
                "fields": "五档盘口 · 09:25 未匹配买单 · 量比",
                "status": "09:25 窗口",
            },
        ],
    }


@app.get("/api/market/breadth")
def market_breadth():
    """全市场宽度：涨跌家数 / 涨跌停 / 两市成交额（60 秒缓存）。"""
    breadth = breadth_from_spot(_spot_map())
    breadth["as_of"] = datetime.now(db.TZ_CN).isoformat(timespec="seconds")
    return breadth


@app.post("/api/datasets/{dataset_id}/enrich")
def enrich_dataset_endpoint(dataset_id: int):
    """给已有数据集补资金流/龙虎榜并重跑策略，不重新抓竞价与日线。"""
    if not db.get_dataset(dataset_id):
        raise HTTPException(status_code=404, detail="数据集不存在")
    try:
        summary = enrich_dataset(dataset_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"补齐失败：{exc}") from exc
    result = run_screen(dataset_id, StrategyConfig())
    return {"enrich": summary, "result": result}


@app.get("/api/market/jobs/{job_id}")
def market_job(job_id: str):
    job = db.get_sync_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="同步任务不存在")
    return job


@app.post("/api/market/sync", status_code=202)
def sync_now(request: SyncRequest):
    job_id = start_sync(request)
    if not job_id:
        raise HTTPException(status_code=409, detail="已有行情同步任务正在运行")
    return {"job_id": job_id, "status": "queued"}


@app.get("/api/datasets")
def datasets():
    return db.list_datasets()


@app.get("/api/datasets/{dataset_id}/stocks")
def dataset_stocks(dataset_id: int):
    if not db.get_dataset(dataset_id):
        raise HTTPException(status_code=404, detail="数据集不存在")
    return db.get_stocks(dataset_id)


@app.post("/api/datasets/demo")
def load_demo():
    dataset_id = db.create_dataset(DEMO_DAY, "09:25策略演示样本", "demo", DEMO_STOCKS)
    result = run_screen(dataset_id, StrategyConfig())
    return {"dataset_id": dataset_id, "run_id": result["run_id"]}


@app.post("/api/datasets/import")
async def import_dataset(
    file: UploadFile = File(...),
    trading_day: str = Form(...),
):
    try:
        date.fromisoformat(trading_day)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="交易日期格式应为YYYY-MM-DD") from exc
    if not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="请上传CSV文件")
    content = await file.read()
    if len(content) > 20 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="CSV文件不能超过20MB")
    try:
        stocks = parse_csv(content)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    dataset_id = db.create_dataset(
        trading_day,
        Path(file.filename).stem,
        "csv",
        [stock.model_dump() for stock in stocks],
    )
    return {"dataset_id": dataset_id, "stock_count": len(stocks)}


@app.get("/api/import-template")
def download_template():
    return Response(
        content=template_csv(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=auction_0925_template.csv"},
    )


@app.post("/api/screen")
def screen(request: ScreenRequest):
    return run_screen(request.dataset_id, request.config)


@app.get("/api/runs")
def runs():
    return db.list_runs()


@app.get("/api/doc-check")
def doc_check():
    """文档「核心样本池」与真实行情、模型输出的逐条核对。"""
    datasets = [item for item in db.list_datasets() if item["source"] == "akshare"]
    return build_report(datasets, StrategyConfig())


_QUOTE_CACHE: dict[str, tuple[float, dict]] = {}

def _quote(code: str) -> dict:
    import time as _time

    import akshare as ak

    from .market_data import numeric
    now = _time.time()
    cached = _QUOTE_CACHE.get(code)
    if cached and now - cached[0] < 15:
        return cached[1]
    frame = ak.stock_bid_ask_em(symbol=code)
    data = {str(row["item"]): numeric(row["value"]) for _, row in frame.iterrows()}
    out = {
        "code": code,
        "price": data.get("最新", 0.0),
        "pct": data.get("涨幅", 0.0),
        "prev_close": data.get("昨收", 0.0),
        "open": data.get("今开", 0.0),
        "high": data.get("最高", 0.0),
        "low": data.get("最低", 0.0),
    }
    _QUOTE_CACHE[code] = (now, out)
    return out


_SPOT_CACHE: dict[str, tuple[float, dict]] = {}


def _spot_map() -> dict[str, dict]:
    """全市场快照（60 秒缓存），一次调用拿全部现价/涨幅/成交量。"""
    import time as _time

    import akshare as ak

    from .market_data import numeric
    hit = _SPOT_CACHE.get("all")
    if hit and _time.time() - hit[0] < 60:
        return hit[1]
    frame = ak.stock_zh_a_spot_em()
    out = {}
    for _, row in frame.iterrows():
        code = str(row["代码"]).zfill(6)
        out[code] = {
            "name": str(row["名称"]),
            "price": numeric(row["最新价"]),
            "pct": numeric(row["涨跌幅"]),
            "volume": numeric(row["成交量"]),
            "amount": numeric(row["成交额"]),
            "high": numeric(row["最高"]),
            "low": numeric(row["最低"]),
            "open": numeric(row["今开"]),
            "prev_close": numeric(row["昨收"]),
            "volume_ratio": numeric(row["量比"]),
        }
    _SPOT_CACHE["all"] = (_time.time(), out)
    return out


CLOSING_SNAPSHOT = Path(__file__).resolve().parent.parent / "data" / "closing_snapshot.json"
CLOSING_TRACK_LABELS = {
    "reclaim": "缩量回踩",
    "floor": "地板反核",
    "ignition": "点火",
    "reversal": "强势反包",
}
# 尾盘规则参数集中在这里，随快照一起归档；改阈值时不需要同时改 checks / 盘前预筛。
CLOSING_RULES = {
    "version": "1.1",
    "tail_start": "14:15",
    "tail_cutoff": "14:40",
    "min_tail_share": 3.0,
    "not_sealed_pct": 9.5,
    "min_gene": 1,
    "reclaim": {"min_pct": -3.0, "max_pct": 4.0, "max_vol_ratio": 1.0},
    "floor": {
        "trigger_low_pct": -9.5,
        "trigger_pct": -4.0,
        "max_close_low": 3.0,
        "max_vol_ratio": 1.3,
    },
    "ignition": {
        "min_pct": 4.0,
        "min_vol_ratio": 1.5,
        "min_close_high": 0.96,
    },
    "reversal": {
        "min_pct": 4.0,
        "min_low_pct": -5.0,
        "min_close_low": 4.0,
        "max_vol_ratio": 1.2,
    },
}
_CLOSING_LOCK = threading.Lock()
_CLOSING_STATE_LOCK = threading.Lock()
_CLOSING_STATE: dict = {"payload": None, "refreshing": False, "error": ""}


def _load_closing_snapshot() -> None:
    """启动后第一次访问时把上一次尾盘快照读进内存，页面不再等冷扫描。"""
    with _CLOSING_STATE_LOCK:
        if _CLOSING_STATE["payload"] is not None:
            return
    try:
        raw = json.loads(CLOSING_SNAPSHOT.read_text(encoding="utf-8"))
        payload = raw.get("payload")
    except Exception:
        return
    if isinstance(payload, dict) and payload.get("version", 0) >= 10 and isinstance(payload.get("dataset"), dict):
        with _CLOSING_STATE_LOCK:
            if _CLOSING_STATE["payload"] is None:
                _CLOSING_STATE["payload"] = payload


def _store_closing(payload: dict) -> None:
    with _CLOSING_STATE_LOCK:
        _CLOSING_STATE["payload"] = payload
        _CLOSING_STATE["error"] = ""
    try:
        CLOSING_SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
        tmp = CLOSING_SNAPSHOT.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"saved_at": payload.get("as_of"), "payload": payload}, ensure_ascii=False),
            encoding="utf-8",
        )
        tmp.replace(CLOSING_SNAPSHOT)
    except Exception:
        pass


def _closing_age(payload: dict) -> float | None:
    try:
        stamp = datetime.fromisoformat(str(payload.get("as_of")))
        return max(0.0, (datetime.now(db.TZ_CN) - stamp).total_seconds())
    except Exception:
        return None


def _refresh_closing_async(dataset: dict) -> None:
    """后台重算，不阻塞当前请求；算完替换内存与磁盘快照。"""
    with _CLOSING_STATE_LOCK:
        if _CLOSING_STATE["refreshing"]:
            return
        _CLOSING_STATE["refreshing"] = True

    def worker() -> None:
        try:
            with _CLOSING_LOCK:
                payload = _closing_payload(dataset, 12)
            _store_closing(payload)
        except Exception as exc:
            with _CLOSING_STATE_LOCK:
                _CLOSING_STATE["error"] = str(exc)
        finally:
            with _CLOSING_STATE_LOCK:
                _CLOSING_STATE["refreshing"] = False

    threading.Thread(target=worker, daemon=True).start()


def _previous_trade_day(day: str) -> date | None:
    """本地日线里的上一交易日；不依赖网络日历。"""
    return max((date.fromisoformat(item) for item in db.trade_dates() if item < day), default=None)


def _forward_returns(code: str, day: str, entry: float, config: dict) -> dict:
    """以 entry 为成本，返回当日收盘 / 次日开盘 / 次日收盘的毛收益与净收益。"""
    if not entry or entry <= 0:
        return {"same_close": None, "next_day": None, "next_open": None, "next_close": None}
    bars = db.get_daily_bars(code, day, "2099-12-31")
    today = next((bar for bar in bars if bar["trade_date"] == day), None)
    future = next((bar for bar in bars if bar["trade_date"] > day), None)
    def gross(price):
        return round((price / entry - 1) * 100, 4) if price else None
    same_close = gross(today["close"]) if today else None
    next_open = gross(future["open"]) if future else None
    next_close = gross(future["close"]) if future else None
    def net(value):
        return round(net_return_pct(value, config), 4) if value is not None else None
    return {
        "same_close": same_close,
        "same_close_net": net(same_close),
        "next_day": future["trade_date"] if future else None,
        "next_open": next_open,
        "next_open_net": net(next_open),
        "next_close": next_close,
        "next_close_net": net(next_close),
    }


def _local_breadth(day: str) -> dict:
    """用本地日线计算信号日市场宽度，供回放与后续 regime 研究。"""
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT pct FROM daily_bars WHERE trade_date = ?", (day,)
        ).fetchall()
    pcts = [float(row["pct"]) for row in rows]
    return {
        "count": len(pcts),
        "up": sum(1 for pct in pcts if pct > 0),
        "down": sum(1 for pct in pcts if pct < 0),
        "limit_up": sum(1 for pct in pcts if pct >= 9.5),
        "limit_down": sum(1 for pct in pcts if pct <= -9.5),
        "avg_pct": round(sum(pcts) / len(pcts), 2) if pcts else 0.0,
        "status": "risk" if sum(1 for pct in pcts if pct <= -9.5) >= 3 else "normal",
    }


def _return_summary(rows: list[dict]) -> dict:
    """收益聚合：只统计有对应价格的数据，避免缺失值被当成 0。"""
    same = [row["same_close_net"] for row in rows if row.get("same_close_net") is not None]
    next_open = [row["next_open_net"] for row in rows if row.get("next_open_net") is not None]
    next_close = [row["next_close_net"] for row in rows if row.get("next_close_net") is not None]
    def avg(values):
        return round(sum(values) / len(values), 2) if values else None
    def win(values):
        return round(sum(1 for value in values if value > 0) / len(values) * 100, 1) if values else None
    return {
        "count": len(rows),
        "evaluated": len(next_close),
        "avg_same_close_net": avg(same),
        "avg_next_open_net": avg(next_open),
        "avg_next_close_net": avg(next_close),
        "win_rate": win(next_close),
        "next_open_win_rate": win(next_open),
    }


def _closing_window(now: datetime, day: str) -> dict:
    """尾盘窗口：14:46 快照，14:15—14:40 的尾盘量能才有意义。"""
    active = day == now.date().isoformat() and time(14, 45) <= now.time() <= time(15, 5)
    return {"label": "14:45—15:00", "active": active, "now": now.strftime("%H:%M:%S")}


def _closing_candidate(
    track: str, m: dict, bars: list[dict], tail_share: float, intraday_pct: float
) -> dict:
    """四条尾盘轨道的统一评分与规则检查。

    评分构成固定为 100 分，避免出现 parts 之和大于总分、
    或前端进度条超过 100% 的口径不一致。
    """
    rules = CLOSING_RULES
    shrink = max(0.0, 1 - m["vol_ratio"])
    ma_text = " / ".join(
        name for name, ok in (("MA5", m["above_ma5"]), ("MA10", m["above_ma10"])) if ok
    ) or "—"
    if track == "reclaim":
        parts = [
            {"label": "尾盘抢筹", "value": round(min(58.0, tail_share * 2.2), 1), "max": 58},
            {"label": "缩量程度", "value": round(min(30.0, shrink * 30), 1), "max": 30},
            {"label": "站上 MA5", "value": 6.0 if m["above_ma5"] else 0.0, "max": 6},
            {"label": "涨停基因", "value": min(m["zt_gene"], 3) * 2, "max": 6},
        ]
        checks = [
            {"label": "近期涨停基因", "pass": m["zt_gene"] >= 1,
             "value": f"{m['zt_gene']} 次", "threshold": "近 10 日 ≥1 次"},
            {"label": "今日涨幅", "pass": rules["reclaim"]["min_pct"] <= m["pct"] <= rules["reclaim"]["max_pct"],
             "value": f"{m['pct']:.2f}%", "threshold": f"{rules['reclaim']['min_pct']:g}%—{rules['reclaim']['max_pct']:g}%"},
            {"label": "均线支撑", "pass": m["above_ma5"] or m["above_ma10"],
             "value": ma_text, "threshold": "至少站上一条"},
            {"label": "缩量洗盘", "pass": m["vol_ratio"] < rules["reclaim"]["max_vol_ratio"],
             "value": f"{m['vol_ratio']:.2f}×", "threshold": f"＜{rules['reclaim']['max_vol_ratio']:g}×"},
            {"label": "尾盘资金", "pass": tail_share >= rules["min_tail_share"],
             "value": f"{tail_share:.1f}%", "threshold": f"{rules['tail_start']}—{rules['tail_cutoff']} ≥{rules['min_tail_share']:g}%"},
        ]
    elif track == "floor":
        parts = [
            {"label": "触板深度", "value": round(min(abs(m["low_pct"]), 10) * 5, 1), "max": 50},
            {"label": "缩量程度", "value": round(min(20.0, shrink * 20), 1), "max": 20},
            {"label": "涨停基因", "value": min(m["zt_gene"], 5) * 3, "max": 15},
            {"label": "尾盘量能", "value": round(min(15.0, tail_share * 0.75), 1), "max": 15},
        ]
        checks = [
            {"label": "触板 / 深跌", "pass": m["low_pct"] <= rules["floor"]["trigger_low_pct"] or m["pct"] <= rules["floor"]["trigger_pct"],
             "value": f"低点 {m['low_pct']:+.1f}%", "threshold": f"跌停或 ≤{rules['floor']['trigger_pct']:g}%"},
            {"label": "仍在低位", "pass": m["close_low"] <= rules["floor"]["max_close_low"],
             "value": f"{m['close_low']:.1f}%", "threshold": f"距低点 ≤{rules['floor']['max_close_low']:g}%"},
            {"label": "缩量", "pass": m["vol_ratio"] <= rules["floor"]["max_vol_ratio"],
             "value": f"{m['vol_ratio']:.2f}×", "threshold": f"≤{rules['floor']['max_vol_ratio']:g}×"},
            {"label": "均线", "pass": m["above_ma5"] or m["above_ma10"],
             "value": ma_text, "threshold": "至少站上一条"},
            {"label": "涨停基因", "pass": m["zt_gene"] >= rules["min_gene"],
             "value": f"{m['zt_gene']} 次", "threshold": f"近 10 日 ≥{rules['min_gene']} 次"},
            {"label": "未封板", "pass": m["pct"] < rules["not_sealed_pct"],
             "value": f"{m['pct']:+.2f}%", "threshold": f"涨幅 <{rules['not_sealed_pct']:g}%"},
            {"label": "尾盘量能", "pass": tail_share >= rules["min_tail_share"],
             "value": f"{tail_share:.1f}%", "threshold": f"{rules['tail_start']}—{rules['tail_cutoff']} ≥{rules['min_tail_share']:g}%"},
        ]
    elif track == "ignition":
        parts = [
            {"label": "涨幅强度", "value": round(m["pct"] * 4, 1), "max": 40},
            {"label": "放量倍数", "value": round(min(m["vol_ratio"], 4) * 10, 1), "max": 40},
            {"label": "收盘位置", "value": round(m["close_high"] * 10, 1), "max": 10},
            {"label": "尾盘量能", "value": round(min(10.0, tail_share * 0.8), 1), "max": 10},
        ]
        checks = [
            {"label": "今日涨幅", "pass": rules["ignition"]["min_pct"] <= m["pct"] < rules["not_sealed_pct"],
             "value": f"{m['pct']:+.2f}%", "threshold": f"{rules['ignition']['min_pct']:g}%—{rules['not_sealed_pct']:g}%"},
            {"label": "放量点火", "pass": m["vol_ratio"] >= rules["ignition"]["min_vol_ratio"],
             "value": f"{m['vol_ratio']:.2f}×", "threshold": f"≥{rules['ignition']['min_vol_ratio']:g}×"},
            {"label": "收盘位置", "pass": m["close_high"] >= rules["ignition"]["min_close_high"],
             "value": f"{m['close_high'] * 100:.1f}%", "threshold": f"≥{rules['ignition']['min_close_high'] * 100:.0f}%"},
            {"label": "站上 MA5", "pass": m["above_ma5"],
             "value": ma_text, "threshold": "MA5"},
            {"label": "尾盘量能", "pass": tail_share >= rules["min_tail_share"],
             "value": f"{tail_share:.1f}%", "threshold": f"{rules['tail_start']}—{rules['tail_cutoff']} ≥{rules['min_tail_share']:g}%"},
        ]
    else:
        parts = [
            {"label": "收盘离低点", "value": round(min(m["close_low"], 10) * 4, 1), "max": 40},
            {"label": "缩量程度", "value": round(min(20.0, shrink * 20), 1), "max": 20},
            {"label": "涨停基因", "value": min(m["zt_gene"], 5) * 4, "max": 20},
            {"label": "尾盘量能", "value": round(min(20.0, tail_share * 0.8), 1), "max": 20},
        ]
        checks = [
            {"label": "盘中深跌", "pass": m["low_pct"] <= rules["reversal"]["min_low_pct"],
             "value": f"低点 {m['low_pct']:+.1f}%", "threshold": f"≤{rules['reversal']['min_low_pct']:g}%"},
            {"label": "收盘离低点", "pass": m["close_low"] >= rules["reversal"]["min_close_low"],
             "value": f"{m['close_low']:.1f}%", "threshold": f"≥{rules['reversal']['min_close_low']:g}%"},
            {"label": "缩量", "pass": m["vol_ratio"] <= rules["reversal"]["max_vol_ratio"],
             "value": f"{m['vol_ratio']:.2f}×", "threshold": f"≤{rules['reversal']['max_vol_ratio']:g}×"},
            {"label": "站上 MA5", "pass": m["above_ma5"],
             "value": ma_text, "threshold": "MA5"},
            {"label": "涨停基因", "pass": m["zt_gene"] >= rules["min_gene"],
             "value": f"{m['zt_gene']} 次", "threshold": f"近 10 日 ≥{rules['min_gene']} 次"},
            {"label": "未封板", "pass": m["pct"] < rules["not_sealed_pct"],
             "value": f"{m['pct']:+.2f}%", "threshold": f"涨幅 <{rules['not_sealed_pct']:g}%"},
            {"label": "尾盘量能", "pass": tail_share >= rules["min_tail_share"],
             "value": f"{tail_share:.1f}%", "threshold": f"{rules['tail_start']}—{rules['tail_cutoff']} ≥{rules['min_tail_share']:g}%"},
        ]
    return {
        **m,
        "track": track,
        "track_label": CLOSING_TRACK_LABELS[track],
        "tail_share": tail_share,
        "intraday_pct": intraday_pct,
        "score": round(sum(part["value"] for part in parts), 1),
        "score_parts": parts,
        "checks": checks,
        "series": [
            {
                "time": bar["time"], "open": bar.get("open"),
                "high": bar["high"], "low": bar["low"],
                "close": bar["close"], "volume": bar["volume"],
            }
            for bar in bars
        ],
    }


def _annotate_track_scores(by_track: dict[str, list[dict]]) -> None:
    """给每条轨道单独排名，避免用不同量纲的原始分跨轨道硬比。"""
    for track, items in by_track.items():
        items.sort(key=lambda item: item["score"], reverse=True)
        total = len(items)
        for index, item in enumerate(items, start=1):
            item["track"] = track
            item["track_label"] = CLOSING_TRACK_LABELS[track]
            item["track_rank"] = index
            item["track_percentile"] = round(100.0 * (total - index + 1) / total, 1) if total else 0.0
            hits = list(item.get("tracks_hit") or [])
            if track not in hits:
                hits.append(track)
            item["tracks_hit"] = hits


def _merge_track_candidates(by_track: dict[str, list[dict]], limit: int) -> list[dict]:
    """跨轨道只按「轨道内百分位」合并，原始分只做同百分位的次要排序。"""
    merged: dict[str, dict] = {}
    for track, items in by_track.items():
        for item in items:
            code = item["code"]
            current = merged.get(code)
            if current is None:
                merged[code] = item
                continue
            hits = sorted(set((current.get("tracks_hit") or []) + (item.get("tracks_hit") or []) + [track]))
            if (item.get("track_percentile", 0.0), item["score"]) > (
                current.get("track_percentile", 0.0), current["score"]
            ):
                item["tracks_hit"] = hits
                merged[code] = item
            else:
                current["tracks_hit"] = hits
    return sorted(
        merged.values(),
        key=lambda item: (item.get("track_percentile", 0.0), item["score"]),
        reverse=True,
    )[:limit]


def _closing_payload(dataset: dict, top: int) -> dict:
    """扫描尾盘候选：预筛 → 并行取当日 5 分钟线 → 14:15—14:40 尾盘量能/评分。"""
    from datetime import date as _date

    stocks = db.get_stocks(dataset["id"])
    spot = _spot_map()
    day = dataset["trading_day"]
    start = (_date.fromisoformat(day) - timedelta(days=90)).isoformat()

    pre = []
    for stock in stocks:
        code = stock["code"]
        if not code.startswith(("600", "601", "603", "605", "000", "001", "002", "003")):
            continue
        if "ST" in stock["name"].upper():
            continue
        live = spot.get(code)
        if not live or live["price"] <= 0:
            continue
        # 1) 缩量回踩：今日窄幅震荡且缩量，未封板
        if not (CLOSING_RULES["reclaim"]["min_pct"] <= live["pct"] <= CLOSING_RULES["reclaim"]["max_pct"]):
            continue
        bars = db.get_daily_bars(code, start, day)
        prev = [b for b in bars if b["trade_date"] < day]
        if len(prev) < 10:
            continue
        yest = prev[-1]
        vol_ratio = live["volume"] / yest["volume"] if yest["volume"] else 0
        if vol_ratio <= 0 or vol_ratio >= CLOSING_RULES["reclaim"]["max_vol_ratio"]:
            continue
        closes = [b["close"] for b in prev[-10:]]
        ma5 = sum(closes[-5:]) / 5
        ma10 = sum(closes) / 10
        if live["price"] < ma5 and live["price"] < ma10:   # 破位不选
            continue
        # 2) 近期涨停基因（回调/反包的前提）
        zt_gene = sum(1 for b in prev[-10:] if b["pct"] >= 9.5)
        if zt_gene < CLOSING_RULES["min_gene"]:
            continue
        pre.append({
            "code": code, "name": live["name"], "industry": stock.get("industry", ""),
            "price": live["price"], "pct": round(live["pct"], 2),
            "open": live["open"], "prev_close": live["prev_close"],
            "high": live["high"], "low": live["low"],
            "low_pct": round((live["low"] / live["prev_close"] - 1) * 100, 2) if live["prev_close"] else 0,
            "close_low": round((live["price"] / live["low"] - 1) * 100, 2) if live["low"] else 0,
            "close_high": round(live["price"] / live["high"], 4) if live["high"] else 1,
            "vol_ratio": round(vol_ratio, 3), "ma5": round(ma5, 2), "ma10": round(ma10, 2),
            "above_ma5": live["price"] >= ma5, "above_ma10": live["price"] >= ma10,
            "zt_gene": zt_gene,
        })
    pre.sort(key=lambda item: item["vol_ratio"])      # 越缩量越靠前
    shortlist = pre[: max(top * 2, 12)]

    # 3) 尾盘资金：14:15—14:40 的成交量占 09:30—14:40 累计量的比重（并行取 5 分钟线）
    def load_day_bars(code: str) -> list[dict]:
        try:
            data = minutes_5day(code)
            return [bar for bar in data["bars"] if bar["date"] == day]
        except Exception:
            return []

    codes = [item["code"] for item in shortlist]
    with ThreadPoolExecutor(max_workers=6) as executor:
        bars_by_code = dict(zip(codes, executor.map(load_day_bars, codes)))

    now = datetime.now(db.TZ_CN)
    results = []
    for item in shortlist:
        bars = bars_by_code.get(item["code"], [])
        window_bars = [bar for bar in bars if bar["time"] <= CLOSING_RULES["tail_cutoff"]]
        total = sum(bar["volume"] for bar in window_bars)
        tail = sum(bar["volume"] for bar in window_bars if bar["time"] >= CLOSING_RULES["tail_start"])
        tail_share = round(tail / total * 100, 2) if total else 0.0
        if tail_share < CLOSING_RULES["min_tail_share"]:
            continue
        first = next((bar for bar in bars if bar["time"] == "09:30"), bars[0]) if bars else None
        intraday_pct = round((bars[-1]["close"] / first["close"] - 1) * 100, 2) if bars and first else 0.0
        results.append(_closing_candidate("reclaim", item, bars, tail_share, intraday_pct))
    results.sort(key=lambda item: item["score"], reverse=True)
    track_lists = _closing_track_lists(stocks, spot, day, start)
    by_track = {"reclaim": results[:8], **track_lists}
    _annotate_track_scores(by_track)
    candidates = _merge_track_candidates(by_track, max(top, 12))
    breadth = breadth_from_spot(spot)
    sample_rows = [dict(sample) for sample in CLOSING_SAMPLES]
    return {
        "version": 10,
        "dataset": {"id": dataset["id"], "trading_day": day, "name": dataset["name"]},
        "rule": {
            "name": "尾盘先手（14:46—14:57）",
            "params": CLOSING_RULES,
            "steps": [
                "近期有过涨停（近 10 日内 ≥ 1 次），属于回调/反包候选",
                "今日涨幅 −3% ~ +4%，且未涨停（还在洗盘阶段）",
                "现价不破 5 日或 10 日均线",
                "今日成交量 < 昨日（缩量洗盘）",
                "14:15—14:40 成交量占 09:30—14:40 累计量 ≥ 3%（14:46 时 14:45 后的 5 分钟线尚未完成）",
            ],
        },
        "market": {
            "up": breadth["up"],
            "down": breadth["down"],
            "limit_up": breadth["limit_up"],
            "limit_down": breadth["limit_down"],
            "status": "risk" if breadth["limit_down"] >= 3 else "normal",
        },
        "sentiment": sentiment_for(day),
        "window": _closing_window(now, day),
        "as_of": now.isoformat(timespec="seconds"),
        "cached": False,
        "age": 0,
        "scanned": len(stocks),
        "universe": len(stocks),
        "prefiltered": len(pre),
        "shortlisted": len(shortlist),
        "candidates": candidates,
        "by_track": by_track,
        "counts": {key: len(items) for key, items in by_track.items()},
        "tracks": [
            {"id": key, "label": label}
            for key, label in CLOSING_TRACK_LABELS.items()
        ],
        "tracks_rule": {
            "name": "对照 2026-11-09 帖子",
            "steps": [
                "地板反核：盘中触板或深跌，仍在低位，缩量且站上均线，有涨停基因",
                "点火：放量突破、收盘接近最高，借题材启动；不要求近期涨停基因",
                "强势反包：深跌后收回、收盘离低点远，仍缩量且站上均线",
            ],
        },
        "samples": sample_rows,
    }


def _closing_track_hits(m: dict) -> list[str]:
    """对照发帖人帖子的三类尾盘形态；返回命中的轨道 id。"""
    r = CLOSING_RULES
    hits = []
    if (
        (m["low_pct"] <= r["floor"]["trigger_low_pct"] or m["pct"] <= r["floor"]["trigger_pct"])
        and m["pct"] < r["not_sealed_pct"]
        and m["close_low"] <= r["floor"]["max_close_low"]
        and m["vol_ratio"] <= r["floor"]["max_vol_ratio"]
        and (m["above_ma5"] or m["above_ma10"])
        and m["zt_gene"] >= r["min_gene"]
    ):
        hits.append("floor")
    if (
        r["ignition"]["min_pct"] <= m["pct"] < r["not_sealed_pct"]
        and m["vol_ratio"] >= r["ignition"]["min_vol_ratio"]
        and m["close_high"] >= r["ignition"]["min_close_high"]
        and m["above_ma5"]
    ):
        hits.append("ignition")
    if (
        r["reversal"]["min_pct"] <= m["pct"] < r["not_sealed_pct"]
        and m["low_pct"] <= r["reversal"]["min_low_pct"]
        and m["close_low"] >= r["reversal"]["min_close_low"]
        and m["vol_ratio"] <= r["reversal"]["max_vol_ratio"]
        and m["above_ma5"]
        and m["zt_gene"] >= r["min_gene"]
    ):
        hits.append("reversal")
    return hits


def _closing_track_lists(
    stocks: list[dict],
    spot: dict[str, dict],
    day: str,
    start: str,
) -> dict[str, list[dict]]:
    """对照发帖人 11/09 帖：地板反核 / 点火 / 强势反包。

    点火不要求近期涨停基因，所以额外从全市场快照补候选；
    地板/反包仍以早盘数据集的股票池为基础。
    """
    from datetime import date as _date

    dataset_codes = {stock["code"] for stock in stocks}

    def metrics_from(code: str, name: str, industry: str, live: dict, bars: list[dict]) -> dict | None:
        prev = [bar for bar in bars if bar["trade_date"] < day]
        if len(prev) < 10:
            return None
        yest = prev[-1]
        vol_ratio = live["volume"] / yest["volume"] if yest["volume"] else 0
        if vol_ratio <= 0:
            return None
        closes = [bar["close"] for bar in prev[-10:]]
        ma5 = sum(closes[-5:]) / 5
        ma10 = sum(closes) / 10
        prev_close = live["prev_close"] or yest["close"]
        low = live["low"] or live["price"]
        high = live["high"] or live["price"]
        return {
            "code": code, "name": name, "industry": industry,
            "price": live["price"], "pct": round(live["pct"], 2),
            "open": live["open"], "prev_close": prev_close,
            "high": high, "low": low,
            "low_pct": round((low / prev_close - 1) * 100, 2) if prev_close else 0,
            "close_low": round((live["price"] / low - 1) * 100, 2) if low else 0,
            "close_high": round(live["price"] / high, 4) if high else 1,
            "vol_ratio": round(vol_ratio, 3), "ma5": round(ma5, 2), "ma10": round(ma10, 2),
            "above_ma5": live["price"] >= ma5, "above_ma10": live["price"] >= ma10,
            "zt_gene": sum(1 for bar in prev[-10:] if bar["pct"] >= 9.5),
        }

    prepared = []
    for stock in stocks:
        code = stock["code"]
        if not code.startswith(("600", "601", "603", "605", "000", "001", "002", "003")):
            continue
        if "ST" in stock["name"].upper():
            continue
        live = spot.get(code)
        if not live or live["price"] <= 0:
            continue
        metrics = metrics_from(code, live.get("name", stock["name"]), stock.get("industry", ""), live,
                               db.get_daily_bars(code, start, day))
        if metrics is None:
            continue
        hits = _closing_track_hits(metrics)
        if hits:
            prepared.append({"metrics": metrics, "hits": hits})

    # 点火票可能没有近期涨停基因，不在早盘数据集里（帖子里东方环宇就是这种）：
    # 从全市场快照补候选，按需拉日线补 MA5/10 与昨量。
    outside = []
    for code, live in spot.items():
        if code in dataset_codes:
            continue
        name = str(live.get("name", ""))
        if not code.startswith(("600", "601", "603", "605", "000", "001", "002", "003")):
            continue
        if "ST" in name.upper() or live["price"] <= 0 or not (4.0 <= live["pct"] < 9.5):
            continue
        if live.get("volume_ratio", 0) < 1.5:
            continue
        high = live["high"]
        if high <= 0 or live["price"] / high < 0.97:
            continue
        outside.append((code, live))
    outside.sort(key=lambda pair: pair[1]["pct"], reverse=True)
    outside = outside[:20]

    if outside:
        previous = _previous_trade_day(day)
        start_date = _date.fromisoformat(start)

        def load_history(code: str) -> tuple[str, list[dict]]:
            bars = db.get_daily_bars(code, start, day)
            if len([bar for bar in bars if bar["trade_date"] < day]) >= 10 or previous is None:
                return code, bars
            try:
                return code, fetch_history(code, start_date, previous)
            except Exception:
                return code, bars

        fetched: dict[str, list[dict]] = {}
        with ThreadPoolExecutor(max_workers=4) as executor:
            for code, bars in executor.map(load_history, [code for code, _ in outside]):
                fetched[code] = bars
        for code, live in outside:
            metrics = metrics_from(code, live.get("name", code), "", live, fetched.get(code, []))
            if metrics is None:
                continue
            hits = _closing_track_hits(metrics)
            if hits:
                prepared.append({"metrics": metrics, "hits": hits})

    per_track: dict[str, list[dict]] = {}
    for track in ("floor", "ignition", "reversal"):
        items = [item for item in prepared if track in item["hits"]]
        def sort_key(item):
            if track == "ignition":
                return item["metrics"]["pct"]
            if track == "floor":
                return -item["metrics"]["low_pct"]
            return item["metrics"]["close_low"]
        items.sort(key=sort_key, reverse=True)
        per_track[track] = items[:12]

    codes = list(dict.fromkeys(item["metrics"]["code"] for items in per_track.values() for item in items))

    def load_day_bars(code: str) -> list[dict]:
        try:
            data = minutes_5day(code)
            return [bar for bar in data["bars"] if bar["date"] == day]
        except Exception:
            return []

    with ThreadPoolExecutor(max_workers=6) as executor:
        bars_by_code = dict(zip(codes, executor.map(load_day_bars, codes)))

    result: dict[str, list[dict]] = {}
    for track, items in per_track.items():
        built = []
        seen = set()
        for item in items:
            m = item["metrics"]
            if m["code"] in seen:
                continue
            bars = bars_by_code.get(m["code"], [])
            window_bars = [bar for bar in bars if bar["time"] <= CLOSING_RULES["tail_cutoff"]]
            total = sum(bar["volume"] for bar in window_bars)
            tail = sum(bar["volume"] for bar in window_bars if bar["time"] >= CLOSING_RULES["tail_start"])
            tail_share = round(tail / total * 100, 2) if total else 0.0
            if tail_share < CLOSING_RULES["min_tail_share"]:
                continue
            first = next((bar for bar in bars if bar["time"] == "09:30"), bars[0]) if bars else None
            intraday_pct = round((bars[-1]["close"] / first["close"] - 1) * 100, 2) if bars and first else 0.0
            built.append(_closing_candidate(track, m, bars, tail_share, intraday_pct))
            seen.add(m["code"])
        built.sort(key=lambda item: item["score"], reverse=True)
        result[track] = built[:8]
    return result


_CLOSING_REPLAY_CACHE: dict[str, dict] = {}


def _replay_cache_path(day: str) -> Path:
    return Path(__file__).resolve().parent.parent / "data" / f"closing_replay_{day}.json"


def _load_replay_cache(day: str) -> dict | None:
    hit = _CLOSING_REPLAY_CACHE.get(day)
    if hit:
        return hit
    try:
        raw = json.loads(_replay_cache_path(day).read_text(encoding="utf-8"))
        payload = raw.get("payload")
        if isinstance(payload, dict) and payload.get("version", 0) >= 5:
            _CLOSING_REPLAY_CACHE[day] = payload
            return payload
    except Exception:
        return None
    return None


def _store_replay_cache(day: str, payload: dict) -> None:
    _CLOSING_REPLAY_CACHE[day] = payload
    try:
        path = _replay_cache_path(day)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"saved_at": payload.get("as_of"), "payload": payload}, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except Exception:
        pass


def _relaxed_track_hits(m: dict) -> list[str]:
    """回放预筛：用日线收盘状态放宽阈值，最终由 14:46 的 5 分钟状态复核。"""
    hits = []
    if (
        (m["low_pct"] <= -4.0 or m["pct"] <= -4.0)
        and m["close_low"] <= 6.0
        and m["vol_ratio"] <= 1.5
        and (m["above_ma5"] or m["above_ma10"])
        and m["zt_gene"] >= 1
        and m["pct"] < 9.5
    ):
        hits.append("floor")
    if (
        3.5 <= m["pct"] < 9.5
        and m["vol_ratio"] >= 1.3
        and m["close_high"] >= 0.94
        and m["above_ma5"]
    ):
        hits.append("ignition")
    if (
        3.5 <= m["pct"] < 9.5
        and m["low_pct"] <= -4.0
        and m["close_low"] >= 3.0
        and m["vol_ratio"] <= 1.4
        and m["above_ma5"]
        and m["zt_gene"] >= 1
    ):
        hits.append("reversal")
    if (
        -3.0 <= m["pct"] <= 4.0
        and m["vol_ratio"] < 1.1
        and (m["above_ma5"] or m["above_ma10"])
        and m["zt_gene"] >= 1
    ):
        hits.append("reclaim")
    return hits


def _closing_replay_payload(dataset: dict) -> dict:
    """按历史交易日回放 14:46 尾盘扫描。

    股票池：当天及之后交易日的数据集（它们的日线覆盖当天）+ 帖子样本；
    预筛用当天日线，最终状态用当天 5 分钟线重建到 14:40。
    """
    from datetime import date as _date

    day = dataset["trading_day"]
    day_date = _date.fromisoformat(day)
    start_date = day_date - timedelta(days=90)
    start = start_date.isoformat()
    previous = _previous_trade_day(day)

    datasets = [
        item for item in db.list_datasets()
        if item["source"] == "akshare" and item["trading_day"] >= day
    ]
    stocks: dict[str, dict] = {}
    for item in datasets:
        for stock in db.get_stocks(item["id"]):
            stocks.setdefault(stock["code"], stock)
    sample_codes = {sample["code"] for sample in CLOSING_SAMPLES if sample["signal_date"] == day}
    for sample in CLOSING_SAMPLES:
        if sample["signal_date"] == day:
            stocks.setdefault(sample["code"], {
                "code": sample["code"], "name": sample["name"], "industry": "",
            })

    bases = []
    for code, stock in stocks.items():
        if not code.startswith(("600", "601", "603", "605", "000", "001", "002", "003")):
            continue
        if "ST" in stock["name"].upper():
            continue
        bars = db.get_daily_bars(code, start, day)
        day_bar = next((bar for bar in bars if bar["trade_date"] == day), None)
        if day_bar is None and code in sample_codes:
            try:
                bars = fetch_history(code, start_date, day_date)
                day_bar = next((bar for bar in bars if bar["trade_date"] == day), None)
            except Exception:
                pass
        prev = [bar for bar in bars if bar["trade_date"] < day]
        if day_bar is None or len(prev) < 10:
            continue
        yest = prev[-1]
        closes = [bar["close"] for bar in prev[-10:]]
        ma5 = sum(closes[-5:]) / 5
        ma10 = sum(closes) / 10
        prev_close = yest["close"]
        low = day_bar["low"]
        high = day_bar["high"]
        metrics = {
            "code": code, "name": stock["name"], "industry": stock.get("industry", ""),
            "price": day_bar["close"], "pct": round(day_bar["pct"], 2),
            "open": day_bar["open"], "prev_close": prev_close,
            "high": high, "low": low,
            "low_pct": round((low / prev_close - 1) * 100, 2) if prev_close else 0,
            "close_low": round((day_bar["close"] / low - 1) * 100, 2) if low else 0,
            "close_high": round(day_bar["close"] / high, 4) if high else 1,
            "vol_ratio": round(day_bar["volume"] / yest["volume"], 3) if yest["volume"] else 0,
            "ma5": round(ma5, 2), "ma10": round(ma10, 2),
            "above_ma5": day_bar["close"] >= ma5, "above_ma10": day_bar["close"] >= ma10,
            "zt_gene": sum(1 for bar in prev[-10:] if bar["pct"] >= 9.5),
        }
        hits = _relaxed_track_hits(metrics)
        if hits:
            bases.append({"metrics": metrics, "hits": hits, "prev": prev})

    per_track: dict[str, list[dict]] = {}
    for track in ("reclaim", "floor", "ignition", "reversal"):
        items = [item for item in bases if track in item["hits"]]
        if track == "floor":
            items.sort(key=lambda item: item["metrics"]["low_pct"])
        elif track == "ignition":
            items.sort(key=lambda item: item["metrics"]["pct"], reverse=True)
        elif track == "reversal":
            items.sort(key=lambda item: item["metrics"]["close_low"], reverse=True)
        else:
            items.sort(key=lambda item: item["metrics"]["vol_ratio"])
        per_track[track] = items[:12]

    codes = list(dict.fromkeys(
        item["metrics"]["code"] for items in per_track.values() for item in items
    ))

    def load_day_bars(code: str) -> list[dict]:
        try:
            data = minutes_5day(code, day)
            return [bar for bar in data["bars"] if bar["date"] == day and bar["time"] <= CLOSING_RULES["tail_cutoff"]]
        except Exception:
            return []

    with ThreadPoolExecutor(max_workers=6) as executor:
        bars_by_code = dict(zip(codes, executor.map(load_day_bars, codes)))
    minute_available = sum(1 for code in codes if bars_by_code.get(code))
    if codes and minute_available == 0:
        raise ValueError("分钟行情不可用，未生成回放缓存")

    by_track: dict[str, list[dict]] = {}
    for track, items in per_track.items():
        built = []
        for item in items:
            base = item["metrics"]
            bars = bars_by_code.get(base["code"], [])
            if not bars:
                continue
            price = bars[-1]["close"]
            high = max(bar["high"] for bar in bars)
            low = min(bar["low"] for bar in bars)
            volume = sum(bar["volume"] for bar in bars)
            prev_close = base["prev_close"]
            total = volume
            tail = sum(bar["volume"] for bar in bars if bar["time"] >= CLOSING_RULES["tail_start"])
            tail_share = round(tail / total * 100, 2) if total else 0.0
            if tail_share < CLOSING_RULES["min_tail_share"]:
                continue
            yest = item["prev"][-1]
            metrics = {
                **base,
                "price": price,
                "pct": round((price / prev_close - 1) * 100, 2) if prev_close else 0,
                "high": high, "low": low,
                "low_pct": round((low / prev_close - 1) * 100, 2) if prev_close else 0,
                "close_low": round((price / low - 1) * 100, 2) if low else 0,
                "close_high": round(price / high, 4) if high else 1,
                "vol_ratio": round(volume / yest["volume"], 3) if yest["volume"] else 0,
                "above_ma5": price >= base["ma5"], "above_ma10": price >= base["ma10"],
            }
            if track == "reclaim":
                reclaim_ok = (
                    CLOSING_RULES["reclaim"]["min_pct"] <= metrics["pct"] <= CLOSING_RULES["reclaim"]["max_pct"]
                    and metrics["vol_ratio"] < CLOSING_RULES["reclaim"]["max_vol_ratio"]
                    and (metrics["above_ma5"] or metrics["above_ma10"])
                    and metrics["zt_gene"] >= CLOSING_RULES["min_gene"]
                )
                if not reclaim_ok:
                    continue
            elif track not in _closing_track_hits(metrics):
                continue
            first = bars[0]
            intraday_pct = round((bars[-1]["close"] / first["close"] - 1) * 100, 2) if first["close"] else 0.0
            built.append(_closing_candidate(track, metrics, bars, tail_share, intraday_pct))
        built.sort(key=lambda item: item["score"], reverse=True)
        by_track[track] = built[:8]

    _annotate_track_scores(by_track)
    candidates = _merge_track_candidates(by_track, 16)
    counts = {key: len(items) for key, items in by_track.items()}
    return {
        "version": 5,
        "mode": "replay",
        "minute_data": {"requested": len(codes), "available": minute_available},
        "dataset": {"id": dataset["id"], "trading_day": day, "name": dataset["name"]},
        "as_of": f"{day}T14:46:00+08:00",
        "window": {"label": "14:46 回放", "active": False, "now": "14:46"},
        "market": _local_breadth(day),
        "sentiment": sentiment_for(day),
        "rule": {
            "name": f"尾盘回放（{day} 14:46）",
            "params": CLOSING_RULES,
            "steps": [
                "用当天日线预筛，再用当天 5 分钟线重建 14:46 状态",
                "尾盘量能 = 14:15—14:40 成交量 / 09:30—14:40 累计成交量（14:46 时 14:45 后的 5 分钟线尚未完成）",
                "地板反核 / 点火 / 强势反包 / 缩量回踩四条轨道",
            ],
        },
        "candidates": candidates,
        "by_track": by_track,
        "counts": counts,
        "samples": [dict(sample) for sample in CLOSING_SAMPLES if sample["signal_date"] == day],
        "tracks": [{"id": key, "label": label} for key, label in CLOSING_TRACK_LABELS.items()],
        "scanned": len(stocks),
        "universe": len(stocks),
        "prefiltered": len(bases),
        "cached": False,
        "age": 0,
    }


@app.get("/api/closing/replay")
def closing_replay(dataset_id: int, refresh: bool = False):
    """回放某个历史交易日 14:46 的尾盘候选（日线预筛 + 当天 5 分钟线）。"""
    dataset = db.get_dataset(dataset_id)
    if not dataset or dataset["source"] != "akshare":
        raise HTTPException(status_code=404, detail="没有该交易日的真实行情数据集")
    day = dataset["trading_day"]
    if not refresh:
        hit = _load_replay_cache(day)
        if hit:
            result = dict(hit)
            result["cached"] = True
            return result
    try:
        with _CLOSING_LOCK:
            payload = _closing_replay_payload(dataset)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"尾盘回放失败：{exc}") from exc
    _store_replay_cache(day, payload)
    return payload


@app.get("/api/closing/scorecard")
def closing_scorecard(dataset_id: int):
    """用 14:46 回放价买入，统计每条轨道到当日收盘 / 次日开盘 / 次日收盘的净收益。"""
    dataset = db.get_dataset(dataset_id)
    if not dataset or dataset["source"] != "akshare":
        raise HTTPException(status_code=404, detail="没有该交易日的真实行情数据集")
    day = dataset["trading_day"]
    payload = _load_replay_cache(day)
    if not payload:
        try:
            with _CLOSING_LOCK:
                payload = _closing_replay_payload(dataset)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=503, detail=f"尾盘回放失败：{exc}") from exc
        _store_replay_cache(day, payload)
    config = StrategyConfig().model_dump()
    rows: list[dict] = []
    for track, items in (payload.get("by_track") or {}).items():
        for item in items:
            forward = _forward_returns(item["code"], day, item["price"], config)
            rows.append({
                "track": track,
                "track_label": CLOSING_TRACK_LABELS.get(track, track),
                "track_rank": item.get("track_rank"),
                "code": item["code"],
                "name": item["name"],
                "price": item["price"],
                "score": item["score"],
                **forward,
            })
    by_track = {
        track: _return_summary([row for row in rows if row["track"] == track])
        for track in CLOSING_TRACK_LABELS
    }
    next_days = sorted({row["next_day"] for row in rows if row.get("next_day")})
    return {
        "dataset": dataset,
        "payload_version": payload.get("version"),
        "cost": {
            "slippage_pct": config["slippage_pct"],
            "fee_pct": config["fee_pct"],
            "stamp_duty_pct": config["stamp_duty_pct"],
        },
        "next_day": next_days[0] if next_days else None,
        "summary": _return_summary(rows),
        "by_track": by_track,
        "rows": rows,
    }


@app.get("/api/forward/scorecard")
def forward_scorecard(since: str = "2026-11-03", refresh: bool = False):
    from .forward_scorecard import build
    return build(since=since, refresh=refresh)


@app.get("/api/backtest/morning")
def morning_backtest(top: int = 12):
    """早盘算法前向收益：入口 = 09:25 竞价价，统计当日收盘 / 次日开盘 / 次日收盘。"""
    top = max(1, min(int(top), 20))
    datasets: dict[str, dict] = {}
    for item in db.list_datasets():
        if item["source"] != "akshare":
            continue
        current = datasets.get(item["trading_day"])
        if current is None or item.get("stock_count", 0) > current.get("stock_count", 0):
            datasets[item["trading_day"]] = item
    config_model = StrategyConfig()
    config = config_model.model_dump()
    rows: list[dict] = []
    days: list[dict] = []
    for day, dataset in sorted(datasets.items()):
        stocks = [StockInput(**payload) for payload in db.get_stocks(dataset["id"])]
        result = evaluate(stocks, dataset, config_model)
        day_rows: list[dict] = []
        for item in result["main"][:top]:
            entry = item["input"]["open_price"]
            forward = _forward_returns(item["code"], day, entry, config)
            row = {
                "day": day,
                "rank": item["rank"],
                "return_rank": item.get("return_rank"),
                "return_score": item.get("return_score"),
                "code": item["code"],
                "name": item["name"],
                "label": item["label"],
                "is_green": item["is_green"],
                "preferred": item.get("preferred", False),
                "confidence": item.get("confidence"),
                "risk_flags": [flag["key"] for flag in item.get("risk_flags", [])],
                "score": item["score"]["total"],
                "entry": entry,
                **forward,
            }
            rows.append(row)
            day_rows.append(row)
        days.append({"day": day, "main_count": len(result["main"]), "rows": day_rows})
    return {
        "top": top,
        "cost": {
            "slippage_pct": config["slippage_pct"],
            "fee_pct": config["fee_pct"],
            "stamp_duty_pct": config["stamp_duty_pct"],
        },
        "summary": _return_summary(rows),
        "preferred": _return_summary([row for row in rows if row["preferred"]]),
        "by_rank": {
            str(rank): _return_summary([row for row in rows if row["rank"] == rank])
            for rank in range(1, top + 1)
        },
        "by_return_rank": {
            str(rank): _return_summary([row for row in rows if row["return_rank"] == rank])
            for rank in range(1, top + 1)
        },
        "days": days,
    }


def warm_closing() -> None:
    """14:46 定时生成尾盘快照，页面打开时直接命中缓存。"""
    try:
        closing_screen(refresh=True)
    except Exception:
        pass


@app.get("/api/closing")
def closing_screen(dataset_id: int | None = None, top: int = 12, refresh: bool = False):
    """尾盘先手（14:46—14:57）：缩量回踩均线 + 尾盘资金抢筹，不做题材。
    规则依据文档 4.2 与发帖人 10/29 帖（模塑科技 / 宁波东力 / 安记食品）。
    页面永远先返回磁盘/内存里的最近快照；盘中快照过期时后台重算，refresh=1 才阻塞重算。"""
    dataset = None
    if dataset_id:
        dataset = db.get_dataset(dataset_id)
    datasets = [item for item in db.list_datasets() if item["source"] == "akshare"]
    # 同一天可能因手动重跑留下多个数据集；取股票数最多的那份，而不是 id 最大的半成品。
    latest_day = max((item["trading_day"] for item in datasets), default=None)
    latest = max(
        (item for item in datasets if item["trading_day"] == latest_day),
        key=lambda item: item.get("stock_count", 0),
        default=None,
    ) if latest_day else None
    # 尾盘是实时扫描：即使页面上选的是历史数据集，也只用最近交易日，避免把今天的现价
    # 套到 11-06 的股票池上。历史回放是另一个模式，不能复用这条链路。
    if latest is None:
        raise HTTPException(status_code=404, detail="暂无可用的涨停基因数据集")
    if not dataset or dataset["trading_day"] != latest["trading_day"]:
        dataset = latest
    top = max(1, min(int(top), 50))

    _load_closing_snapshot()
    now = datetime.now(db.TZ_CN)
    window = _closing_window(now, dataset["trading_day"])
    with _CLOSING_STATE_LOCK:
        payload = _CLOSING_STATE["payload"]
        refreshing = bool(_CLOSING_STATE["refreshing"])
        error = _CLOSING_STATE["error"]

    if refresh or payload is None:
        try:
            with _CLOSING_LOCK:
                payload = _closing_payload(dataset, top)
        except Exception as exc:  # noqa: BLE001 - 行情源失败要给页面可读信息
            raise HTTPException(status_code=503, detail=f"尾盘扫描失败：{exc}") from exc
        _store_closing(payload)
        result = dict(payload)
        result.update({"cached": False, "age": 0, "refreshing": False, "stale": False, "window": window, "error": ""})
        return result

    age = _closing_age(payload)
    snapshot_day = (payload.get("dataset") or {}).get("trading_day")
    stale = bool(window["active"]) and (
        snapshot_day != dataset["trading_day"] or age is None or age > 60
    )
    if stale and not refreshing:
        _refresh_closing_async(dataset)
        refreshing = True
    result = dict(payload)
    result.update({
        "cached": True,
        "age": round(age, 1) if age is not None else None,
        "refreshing": refreshing,
        "stale": stale,
        "window": window,
        "error": error,
    })
    return result


@app.get("/api/closing/backtest")
def closing_backtest(day: str, top: int = 8):
    """尾盘先手回测：以 D 日收盘价买入，看 D+1 开盘/收盘的毛收益与净收益。"""
    from datetime import date as _date

    top = max(1, min(int(top), 50))
    try:
        target = _date.fromisoformat(day)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="日期格式应为YYYY-MM-DD") from exc
    datasets = [item for item in db.list_datasets() if item["source"] == "akshare"]
    same_day = [item for item in datasets if item["trading_day"] == day]
    if not same_day:
        raise HTTPException(status_code=404, detail=f"{day} 没有真实行情数据集")
    dataset = max(same_day, key=lambda item: item.get("stock_count", 0))
    stocks = {item["code"]: item for item in db.get_stocks(dataset["id"])}
    start = (target - timedelta(days=90)).isoformat()
    later = [item["trading_day"] for item in datasets if item["trading_day"] > day]
    next_day = min(later) if later else None
    if not next_day:
        calendar = [item for item in db.trade_dates() if item > day]
        next_day = min(calendar) if calendar else None

    config = StrategyConfig().model_dump()
    rows = []
    for code, stock in stocks.items():
        if not code.startswith(("600", "601", "603", "605", "000", "001", "002", "003")):
            continue
        if "ST" in stock["name"].upper():
            continue
        bars = db.get_daily_bars(code, start, day)
        today = [b for b in bars if b["trade_date"] == day]
        if not today:
            continue
        cur = today[0]
        prev = [b for b in bars if b["trade_date"] < day]
        if len(prev) < 10:
            continue
        pct = cur["pct"]
        if not (-3.0 <= pct <= 4.0):
            continue
        yest = prev[-1]
        vol_ratio = cur["volume"] / yest["volume"] if yest["volume"] else 0
        if not (0 < vol_ratio < 1.0):
            continue
        closes = [b["close"] for b in prev[-10:]]
        ma5 = sum(closes[-5:]) / 5
        ma10 = sum(closes) / 10
        if cur["close"] < ma5 and cur["close"] < ma10:
            continue
        gene = sum(1 for b in prev[-10:] if b["pct"] >= 9.5)
        if gene < 1:
            continue
        try:
            data = minutes_5day(code, day)
            mbars = [b for b in data["bars"] if b["date"] == day and b["time"] <= CLOSING_RULES["tail_cutoff"]]
        except Exception:
            mbars = []
        total = sum(b["volume"] for b in mbars)
        tail = sum(b["volume"] for b in mbars if b["time"] >= CLOSING_RULES["tail_start"])
        tail_share = round(tail / total * 100, 2) if total else 0.0
        if tail_share < CLOSING_RULES["min_tail_share"]:
            continue
        nxt_open = nxt_close = None
        if next_day:
            nb = [b for b in db.get_daily_bars(code, day, next_day) if b["trade_date"] == next_day]
            if nb:
                nxt_open, nxt_close = nb[0]["open"], nb[0]["close"]
        open_return = round((nxt_open / cur["close"] - 1) * 100, 2) if nxt_open else None
        close_return = round((nxt_close / cur["close"] - 1) * 100, 2) if nxt_close else None
        prev_close = yest["close"]
        metrics = {
            "code": code, "name": stock["name"], "price": cur["close"],
            "pct": round(pct, 2), "open": cur["open"], "prev_close": prev_close,
            "high": cur["high"], "low": cur["low"],
            "low_pct": round((cur["low"] / prev_close - 1) * 100, 2) if prev_close else 0,
            "close_low": round((cur["close"] / cur["low"] - 1) * 100, 2) if cur["low"] else 0,
            "close_high": round(cur["close"] / cur["high"], 4) if cur["high"] else 1,
            "vol_ratio": round(vol_ratio, 3), "ma5": round(ma5, 2), "ma10": round(ma10, 2),
            "above_ma5": cur["close"] >= ma5, "above_ma10": cur["close"] >= ma10,
            "zt_gene": gene,
        }
        first = mbars[0] if mbars else None
        intraday_pct = round((mbars[-1]["close"] / first["close"] - 1) * 100, 2) if first and first.get("close") else 0.0
        scored = _closing_candidate("reclaim", metrics, mbars, tail_share, intraday_pct)
        scored.update({
            "next_open": nxt_open, "next_close": nxt_close,
            "open_return": open_return, "close_return": close_return,
            "open_return_net": round(net_return_pct(open_return, config), 2) if open_return is not None else None,
            "close_return_net": round(net_return_pct(close_return, config), 2) if close_return is not None else None,
        })
        rows.append(scored)
    rows.sort(key=lambda item: item["score"], reverse=True)
    picked = rows[:top]
    done = [r for r in picked if r["close_return_net"] is not None]
    return {
        "day": day, "next_day": next_day, "scanned": len(stocks), "candidates": picked,
        "cost": {
            "slippage_pct": config["slippage_pct"],
            "fee_pct": config["fee_pct"],
            "stamp_duty_pct": config["stamp_duty_pct"],
        },
        "summary": {
            "count": len(picked), "evaluated": len(done),
            "avg_open_return": round(sum(r["open_return_net"] for r in done) / len(done), 2) if done else None,
            "avg_close_return": round(sum(r["close_return_net"] for r in done) / len(done), 2) if done else None,
            "win_rate": round(sum(1 for r in done if r["close_return_net"] > 0) / len(done) * 100, 1) if done else None,
            "gap_up_rate": round(sum(1 for r in done if r["open_return_net"] > 0) / len(done) * 100, 1) if done else None,
        },
    }


@app.get("/api/marks")
def get_marks():
    return load_marks()


@app.post("/api/marks")
def post_mark(payload: dict):
    day = str(payload.get("day", ""))
    code = str(payload.get("code", ""))
    action = str(payload.get("action", "exclude"))
    if not day or not code:
        raise HTTPException(status_code=400, detail="day 与 code 必填")
    marks = load_marks()
    bucket = marks.setdefault(day, {"exclude": []})
    codes = set(bucket.get("exclude", []))
    if action == "exclude":
        codes.add(code)
    else:
        codes.discard(code)
    bucket["exclude"] = sorted(codes)
    save_marks(marks)
    return {"day": day, "exclude": bucket["exclude"]}


@app.get("/api/quotes")
def quotes(codes: str):
    """实时现价与涨幅（15 秒缓存）。"""
    wanted = [code.strip().lower() for code in codes.split(",") if code.strip()][:20]
    out = {}
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {executor.submit(_quote, code): code for code in wanted}
        for future in as_completed(futures):
            code = futures[future]
            try:
                out[code] = future.result()
            except Exception:
                out[code] = None
    return out


_MINUTE_CACHE: dict[str, tuple[float, dict]] = {}


@app.get("/api/minutes/{code}")
def minutes_5day(code: str, day: str | None = None):
    """5 分钟行情。优先使用本地 stock_1min parquet，超出覆盖范围时回退 AKShare。"""
    import time as _time

    import akshare as ak

    from . import minute_store

    cache_key = f"{day or 'recent'}:{code}"
    hit = _MINUTE_CACHE.get(cache_key)
    if hit and _time.time() - hit[0] < 60:
        return hit[1]

    if day and minute_store.covers(day):
        local = minute_store.bars_5m(code, day, day)
        if local:
            payload = {"code": code, "bars": [
                {
                    "date": bar["date"], "time": bar["time"], "close": bar["close"],
                    "high": bar["high"], "low": bar["low"], "volume": bar["volume"],
                }
                for bar in local
            ]}
            _MINUTE_CACHE[cache_key] = (_time.time(), payload)
            return payload

    from .market_data import numeric, retry_call
    today = datetime.now(db.TZ_CN).date()
    start = (today - timedelta(days=12)).strftime("%Y-%m-%d 09:30:00")
    end = today.strftime("%Y-%m-%d 15:00:00")
    frame = retry_call(lambda: ak.stock_zh_a_hist_min_em(
        symbol=code, start_date=start, end_date=end, period="5", adjust=""
    ), attempts=2)
    if frame is None or frame.empty:
        return {"code": code, "bars": []}
    bars = []
    for _, row in frame.tail(600).iterrows():
        ts = str(row["时间"])
        time = ts[11:16]
        if not ("09:30" <= time <= "11:30" or "13:00" <= time <= "15:00"):
            continue
        close = numeric(row["收盘"])
        if close <= 0:
            continue
        bars.append({
            "date": ts[:10], "time": time, "close": close,
            "high": numeric(row["最高"]), "low": numeric(row["最低"]),
            "volume": numeric(row["成交量"]),
        })
    payload = {"code": code, "bars": bars}
    _MINUTE_CACHE[cache_key] = (_time.time(), payload)
    return payload


@app.get("/api/minute-data/status")
def minute_data_status():
    from . import minute_store
    files = list(minute_store.ROOT.glob("*.parquet")) if minute_store.ROOT.exists() else []
    return {
        "root": str(minute_store.ROOT),
        "exists": minute_store.ROOT.exists(),
        "files": len(files),
        "latest_day": minute_store.LATEST_DAY.isoformat(),
        "env": "STOCK_1MIN_DIR",
    }


@app.get("/api/daily-bars/{code}")
def daily_bars(code: str, date: str):
    """近 40 个交易日的日线，用于列表缩略图与详情页 K 线。"""
    try:
        end = datetime.fromisoformat(date).date()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="日期格式应为YYYY-MM-DD") from exc
    start = end - timedelta(days=90)
    bars = db.get_daily_bars(code.lower().replace("sh", "").replace("sz", ""), start.isoformat(), end.isoformat())
    return {"code": code, "bars": bars[-40:]}


@app.get("/api/timing/backtest")
def timing_backtest(run_id: int):
    """用历史 5 分钟线粗测「09:35 开盘确认」：跌破拍卖价 2% 放弃，高于 +2% 不追，0%—+2% 才买。

    31,664 条本地 1 分钟样本（2026-01—09）显示：09:35 相对开盘 <0 的承接偏弱，
    >+2% 再追的收益转负；0%—+2% 是相对可执行的确认区。实盘监控是 5 秒，本回测是 5 分钟。
    """
    run = db.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="运行记录不存在")
    config = run.get("config") or {}
    performance = {item["code"]: item for item in run.get("performance", [])}
    rows = []
    for item in run.get("main", []):
        perf = performance.get(item["code"])
        if not perf or not perf.get("series"):
            continue
        auction = float(item["input"]["open_price"])
        close = perf.get("close_price")
        if not close:
            continue
        first = next((bar for bar in perf["series"] if bar.get("time", "") >= "09:31"), None)
        if not first:
            continue
        first_close = float(first["close"])
        if first_close < auction * 0.98:
            rows.append({
                "code": item["code"], "name": item["name"], "status": "abort",
                "auction": auction, "first_close": first_close,
                "baseline_net": round(net_return_pct((close / auction - 1) * 100, config), 2),
                "confirmed_net": None,
            })
            continue
        if first_close < auction or first_close > auction * 1.02:
            rows.append({
                "code": item["code"], "name": item["name"], "status": "skip",
                "auction": auction, "first_close": first_close,
                "baseline_net": round(net_return_pct((close / auction - 1) * 100, config), 2),
                "confirmed_net": None,
            })
            continue
        entry = first_close
        rows.append({
            "code": item["code"], "name": item["name"], "status": "buy",
            "auction": auction, "first_close": first_close, "entry": round(entry, 2),
            "baseline_net": round(net_return_pct((close / auction - 1) * 100, config), 2),
            "confirmed_net": round(net_return_pct((close / entry - 1) * 100, config), 2),
        })
    bought = [row for row in rows if row["status"] == "buy" and row["confirmed_net"] is not None]
    aborted = [row for row in rows if row["status"] == "abort"]
    skipped = [row for row in rows if row["status"] == "skip"]
    baseline = [row["baseline_net"] for row in rows if row["baseline_net"] is not None]
    confirmed = [row["confirmed_net"] for row in bought]
    return {
        "run_id": run_id,
        "day": run["dataset"]["trading_day"],
        "precision": "5分钟线，确认时点约 09:35",
        "rows": rows,
        "summary": {
            "signals": len(rows),
            "bought": len(bought),
            "aborted": len(aborted),
            "skipped": len(skipped),
            "avg_baseline_net": round(sum(baseline) / len(baseline), 2) if baseline else None,
            "avg_confirmed_net": round(sum(confirmed) / len(confirmed), 2) if confirmed else None,
            "baseline_win_rate": round(sum(1 for value in baseline if value > 0) / len(baseline) * 100, 1) if baseline else None,
            "confirmed_win_rate": round(sum(1 for value in confirmed if value > 0) / len(confirmed) * 100, 1) if confirmed else None,
        },
    }


@app.get("/api/sentiment")
def get_sentiment(day: str | None = None, include_day: bool = False):
    target = day or datetime.now(db.TZ_CN).date().isoformat()
    return sentiment_for(target, include_day=include_day)


@app.get("/api/signals")
def get_signals(day: str | None = None):
    """当前买入信号监控状态 + 当日历史事件。"""
    snapshot = signal_monitor.snapshot()
    target = day or snapshot.get("day") or datetime.now(db.TZ_CN).date().isoformat()
    if not snapshot.get("plans"):
        datasets = [
            item for item in db.list_datasets()
            if item["source"] == "akshare" and item["trading_day"] == target and item["latest_run_id"]
        ]
        dataset = max(datasets, key=lambda item: item.get("stock_count", 0), default=None)
        if dataset:
            run = db.get_run(dataset["latest_run_id"])
            if run:
                snapshot["plans"] = plans_from_result(run)
    snapshot["history"] = db.get_signals(target)
    return snapshot


@app.post("/api/signals/start")
def start_signals(run_id: int | None = None):
    now = datetime.now(db.TZ_CN)
    if not (time(9, 25) <= now.time() <= time(9, 40)):
        raise HTTPException(status_code=400, detail="当前不在 09:25—09:40 买入监控窗口")
    run = None
    if run_id is not None:
        run = db.get_run(run_id)
    else:
        job = db.get_latest_sync_job()
        if job and job.get("run_id"):
            run = db.get_run(int(job["run_id"]))
        if run is None:
            datasets = [
                item for item in db.list_datasets()
                if item["source"] == "akshare" and item["trading_day"] == now.date().isoformat()
                and item["latest_run_id"]
            ]
            dataset = max(datasets, key=lambda item: item.get("stock_count", 0), default=None)
            if dataset:
                run = db.get_run(dataset["latest_run_id"])
    if not run:
        raise HTTPException(status_code=404, detail="没有可监控的当日选股结果")
    day = (run.get("dataset") or {}).get("trading_day")
    if day != now.date().isoformat():
        raise HTTPException(status_code=400, detail="只能监控当日选股结果")
    return signal_monitor.start(run, day)


@app.post("/api/signals/stop")
def stop_signals():
    return signal_monitor.stop()


@app.post("/api/performance/evaluate")
def performance_evaluate(request: PerformanceRequest):
    run_id = request.run_id
    if run_id is None:
        job = db.get_latest_sync_job()
        run_id = job.get("run_id") if job and job["status"] == "completed" else None
    if run_id is None:
        datasets = [item for item in db.list_datasets() if item["source"] == "akshare" and item["latest_run_id"]]
        dataset = max(
            datasets,
            key=lambda item: (item["trading_day"], item.get("stock_count", 0)),
            default=None,
        )
        run_id = dataset.get("latest_run_id") if dataset else None
    if run_id is None:
        raise HTTPException(status_code=404, detail="暂无可复盘的真实行情选股记录")
    try:
        return run_performance(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/runs/latest")
def latest_run(dataset_id: int):
    result = db.get_latest_run(dataset_id)
    if not result:
        raise HTTPException(status_code=404, detail="该数据集还没有运行记录")
    return result


@app.get("/api/runs/{run_id}")
def run_detail(run_id: int):
    result = db.get_run(run_id)
    if not result:
        raise HTTPException(status_code=404, detail="运行记录不存在")
    return result


@app.get("/api/runs/{run_id}/export")
def export_run(run_id: int):
    result = db.get_run(run_id)
    if not result:
        raise HTTPException(status_code=404, detail="运行记录不存在")
    output = io.StringIO()
    fields = [
        "pool", "rank", "return_rank", "return_score", "code", "name", "label", "score",
        "confidence", "preferred", "risk_flags", "buy_action", "signal_window",
        "entry_low", "entry_high", "abort_below", "stop_below", "target_price",
        "auction_pct", "vol_ratio",
        "auction_turnover", "is_core", "global_rank", "entry_price", "noon_price",
        "close_price", "open_return", "open_return_net", "noon_return", "noon_return_net",
        "close_return", "close_return_net", "max_return", "max_return_net",
        "min_return", "min_return_net", "limit_up", "morning_desc", "afternoon_desc",
        "performance_status",
    ]
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    config = result.get("config") or {}

    def net(value):
        if value is None or value == "":
            return ""
        return round(net_return_pct(float(value), config), 4)

    performance = {item["code"]: item for item in result["performance"]}
    for pool in ("main", "candidates"):
        for item in result[pool]:
            actual = performance.get(item["code"], {})
            writer.writerow({
                "pool": "主档池" if pool == "main" else "候选池",
                "rank": item["rank"],
                "return_rank": item.get("return_rank", ""),
                "return_score": item.get("return_score", ""),
                "code": item["code"], "name": item["name"],
                "label": item["label"] if pool == "main" else item["subtype"],
                "score": item["score"]["total"] if pool == "main" else item["candidate_score"],
                "confidence": item.get("confidence", ""),
                "preferred": item.get("preferred", False),
                "risk_flags": " / ".join(flag["key"] for flag in item.get("risk_flags", [])),
                "buy_action": (item.get("buy_plan") or {}).get("action", ""),
                "signal_window": (item.get("buy_plan") or {}).get("window", ""),
                "entry_low": (item.get("buy_plan") or {}).get("entry_low", ""),
                "entry_high": (item.get("buy_plan") or {}).get("entry_high", ""),
                "abort_below": (item.get("buy_plan") or {}).get("abort_below", ""),
                "stop_below": (item.get("buy_plan") or {}).get("stop_below", ""),
                "target_price": (item.get("buy_plan") or {}).get("target_price", ""),
                "auction_pct": item["metrics"]["auction_pct"], "vol_ratio": item["metrics"]["vol_ratio"],
                "auction_turnover": item["metrics"]["auction_turnover"], "is_core": item["is_core"],
                "global_rank": item["global_rank"] or "",
                "entry_price": actual.get("entry_price") or "",
                "noon_price": actual.get("noon_price") or "",
                "close_price": actual.get("close_price") or "",
                "open_return": actual.get("open_return") if actual else "",
                "open_return_net": net(actual.get("open_return") if actual else ""),
                "noon_return": actual.get("noon_return") if actual.get("noon_return") is not None else "",
                "noon_return_net": net(actual.get("noon_return") if actual else ""),
                "close_return": actual.get("close_return") if actual.get("close_return") is not None else "",
                "close_return_net": net(actual.get("close_return") if actual else ""),
                "max_return": actual.get("max_return") if actual else "",
                "max_return_net": net(actual.get("max_return") if actual else ""),
                "min_return": actual.get("min_return") if actual else "",
                "min_return_net": net(actual.get("min_return") if actual else ""),
                "limit_up": actual.get("limit_up") if actual.get("limit_up") is not None else "",
                "morning_desc": actual.get("morning_desc", ""),
                "afternoon_desc": actual.get("afternoon_desc", ""),
                "performance_status": actual.get("status", "pending"),
            })
    content = "﻿" + output.getvalue()
    filename = f"screen_{result['dataset']['trading_day']}_{run_id}.csv"
    return StreamingResponse(
        iter([content.encode("utf-8")]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.get("/")
def index():
    return FileResponse(FRONTEND / "index.html")


app.mount("/static", StaticFiles(directory=FRONTEND), name="static")
