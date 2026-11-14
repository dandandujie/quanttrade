import math
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, time as clock_time, timedelta
from typing import Any, Callable

import json
from pathlib import Path

import akshare as ak
import pandas as pd

from . import data_sources as sources
from . import database as db
from . import minute_store
from . import network
from .post_watchlist import POST_WATCHLIST

network.configure_network()

CONCEPT_PATH = Path(__file__).resolve().parent.parent / "data" / "concept_members.json"

# 统计型/风格型板块不能当题材用（成员几百只、涨停动辄上百家），只保留真正的主题板块
THEME_BLACKLIST = (
    "融资融券", "深股通", "沪股通", "昨日", "今日涨停", "最近多板", "MSCI", "富时罗素", "标准普尔",
    "百元股", "低价股", "微盘股", "次新股", "注册制", "破净", "高送转", "预亏", "预盈", "中报", "季报", "年报",
    "股权转让", "举牌", "创投", "壳资源", "B股", "上证50", "沪深300", "中证500", "央视50", "证金", "汇金",
    "深成500", "上证180", "深圳100", "创业板综", "转债", "AH股", "GDR", "漂亮100", "行业龙头", "大盘",
    "中盘", "小盘", "基金", "机构", "社保", "QFII", "券商重仓", "参股", "央国企改革", "东方财富", "题材股",
    "百日新高", "近期", "趋势股", "摘帽", "国资", "养老金", "标普", "多元金融", "高市净率", "破增发",
    "连续亏损", "扭亏", "微利", "亏损股",
)


def load_concepts() -> dict[str, list[str]]:
    """概念板块成分股（东方财富，本地缓存）。缺失时核心判定退化为行业家数。"""
    if not CONCEPT_PATH.exists():
        return {}
    data = None
    for encoding in ("utf-8", "gbk"):
        try:
            data = json.loads(CONCEPT_PATH.read_text(encoding=encoding))
            break
        except Exception:
            continue
    if not isinstance(data, dict):
        return {}
    return {
        name: codes
        for name, codes in data.items()
        if not any(word in name for word in THEME_BLACKLIST)
    }


QUALITY_NOTES = [
    "09:24:45 快照以 09:24 分钟收盘价近似（仅盘前同步时可用）",
    "买一未匹配量取自腾讯五档盘口，只在 09:25—09:30 同步窗口内有效",
    "5 日主力净流入取自东方财富资金流排行，只对最近交易日有效",
    "龙虎榜净买额取自东方财富 T-1 龙虎榜，未上榜即为 0",
    "题材涨停家数用本地概念成分 × 昨日涨停池近似，不是实时板块行情",
]


def build_quality_notes(
    *,
    backfilled: int,
    book: int,
    fund_flow: int,
    lhb: int,
    concept: bool,
) -> list[str]:
    """按本次同步实际取到的字段生成数据质量说明，取不到的明确标注。"""
    notes: list[str] = []
    if backfilled:
        notes.append(f"{backfilled} 只标的为历史回放：竞价量价取自 09:30 一分钟K线，无盘前 09:24 快照")
    else:
        notes.append("09:24:45 快照以 09:24 分钟收盘价近似")
    notes.append(
        f"买一未匹配量来自腾讯五档盘口（09:25 快照，{book} 只）" if book
        else "买一未匹配量缺失：本次同步不在 09:25—09:30 盘口窗口内"
    )
    notes.append(
        f"5 日主力净流入来自东方财富资金流排行（{fund_flow} 只）" if fund_flow
        else "5 日主力净流入未获取：只对最近交易日补取，历史回放不引入未来数据"
    )
    notes.append(
        f"龙虎榜净买额来自东方财富 T-1 龙虎榜（{lhb} 只）" if lhb
        else "龙虎榜净买额未获取：T-1 无上榜记录或接口失败"
    )
    notes.append(
        "题材涨停家数用本地概念成分 × 昨日涨停池近似" if concept
        else "概念缓存缺失，题材共振退化为同行业涨停家数"
    )
    return notes

# 免费分钟接口只能回看到最近若干个交易日，历史回放超出该窗口会失败。
BACKFILL_WINDOW = 15


def numeric(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default


_CALENDAR_CACHE: tuple[float, list[date]] | None = None
_CALENDAR_TTL = 6 * 3600


def trading_days() -> list[date]:
    """交易日历。网络源失败时用本地日线日期兜底，避免回放/复盘直接 500。"""
    global _CALENDAR_CACHE
    now = time.time()
    if _CALENDAR_CACHE and now - _CALENDAR_CACHE[0] < _CALENDAR_TTL:
        return _CALENDAR_CACHE[1]
    try:
        frame = ak.tool_trade_date_hist_sina()
        days = sorted(pd.to_datetime(frame["trade_date"]).dt.date.tolist())
        if days:
            _CALENDAR_CACHE = (now, days)
            return days
    except Exception:
        if _CALENDAR_CACHE:
            return _CALENDAR_CACHE[1]
    fallback = [date.fromisoformat(item) for item in db.trade_dates()]
    if fallback:
        _CALENDAR_CACHE = (now, fallback)
        return fallback
    raise ValueError("交易日历不可用，且本地没有日线日期可兜底")


def resolve_target_day(calendar: list[date], requested: str | None = None) -> date:
    """支持回放最近 15 个交易日，便于按文档逐日核对算法。"""
    today = datetime.now(db.TZ_CN).date()
    available = [day for day in calendar if day <= today]
    if not available:
        raise ValueError("未获取到有效交易日历")
    if not requested:
        return available[-1]
    target = date.fromisoformat(requested)
    if target not in available:
        raise ValueError(f"{requested} 不是交易日")
    window = available[-BACKFILL_WINDOW:]
    if target not in window:
        raise ValueError(
            f"免费分钟行情仅覆盖最近 {BACKFILL_WINDOW} 个交易日，"
            f"可回放 {window[0].isoformat()} 起的数据"
        )
    return target


def pool_map(frame: pd.DataFrame) -> dict[str, dict[str, Any]]:
    result = {}
    for _, row in frame.iterrows():
        code = str(row["代码"]).zfill(6)
        result[code] = {
            "code": code,
            "name": str(row["名称"]),
            "industry": str(row.get("所属行业", "")),
            "zt_count": int(numeric(row.get("连板数"), 0)),
            "free_market_cap": numeric(row.get("流通市值")) / 100000000,
            "break_count": int(numeric(row.get("炸板次数"), 0)),
            "seal_amount": numeric(row.get("封板资金")),
            "seal_time": str(row.get("首次封板时间", "")),
            "turnover": numeric(row.get("换手率")),
        }
    return result


def bar_from_row(row: pd.Series) -> dict[str, Any]:
    return {
        "trade_date": str(row["日期"])[:10],
        "open": numeric(row["开盘"]),
        "close": numeric(row["收盘"]),
        "high": numeric(row["最高"]),
        "low": numeric(row["最低"]),
        "volume": numeric(row["成交量"]),
        "amount": numeric(row["成交额"]),
        "pct": numeric(row["涨跌幅"]),
        "turnover": numeric(row["换手率"]),
    }


def retry_call(call: Callable[[], Any], attempts: int = 5) -> Any:
    last_error = None
    for attempt in range(attempts):
        try:
            return call()
        except Exception as exc:
            last_error = exc
            if attempt + 1 < attempts:
                delay = min(8.0, 0.8 * (2 ** attempt)) + random.uniform(0, 0.3)
                time.sleep(delay)
    raise last_error


_AUCTION_LOCK = threading.Lock()
_AUCTION_LAST = 0.0
_AUCTION_INTERVAL = 0.25


def auction_call(call: Callable[[], Any], attempts: int = 2) -> Any:
    """竞价接口对并发很敏感：全局串行 + 最小间隔，避免 RemoteDisconnected。"""
    global _AUCTION_LAST
    with _AUCTION_LOCK:
        wait = _AUCTION_INTERVAL - (time.time() - _AUCTION_LAST)
        if wait > 0:
            time.sleep(wait)
        try:
            return retry_call(call, attempts=attempts)
        finally:
            _AUCTION_LAST = time.time()


class SyncProgress:
    def __init__(self, job_id: str, total: int):
        self.job_id = job_id
        self.total = total
        self.completed = 0
        self.lock = threading.Lock()

    def advance(self, message: str) -> None:
        with self.lock:
            self.completed += 1
            if self.completed % 3 == 0 or self.completed == self.total:
                db.update_sync_job(
                    self.job_id,
                    progress=self.completed,
                    total=self.total,
                    message=f"{message} · {self.completed}/{self.total}",
                )


_HISTORY_EAST_FAILS = 0
_HISTORY_LOCK = threading.Lock()


def _infer_shares_from_quote(code: str) -> float:
    try:
        quote = sources.tencent_quotes([code]).get(str(code).zfill(6))
    except Exception:  # noqa: BLE001
        return 0.0
    if not quote:
        return 0.0
    price = numeric(quote.get("price")) or numeric(quote.get("prev_close"))
    float_mcap = numeric(quote.get("float_mcap"))
    return float_mcap * 1e8 / price if price > 0 and float_mcap > 0 else 0.0


def _fill_turnover(bars: list[dict[str, Any]], shares: float) -> None:
    if shares <= 0:
        return
    for bar in bars:
        if numeric(bar.get("turnover")) <= 0 and numeric(bar.get("volume")) > 0:
            bar["turnover"] = round(numeric(bar["volume"]) * 100.0 / shares * 100.0, 4)


def fetch_history(code: str, start: date, end: date, shares: float = 0.0) -> list[dict[str, Any]]:
    global _HISTORY_EAST_FAILS
    if shares <= 0:
        shares = _infer_shares_from_quote(code)
    cached = db.get_daily_bars(code, start.isoformat(), end.isoformat())
    # 零换手缓存不能复用：旧版 Sina/腾讯兜底在 shares=0 时写入 turnover=0，
    # 直接复用会让这批票永远被“昨日换手率无效”误杀。
    if (
        cached
        and cached[-1]["trade_date"] == end.isoformat()
        and cached[0]["trade_date"] <= (start + timedelta(days=12)).isoformat()
        and numeric(cached[-1].get("turnover")) > 0
    ):
        return cached
    # 先走不依赖代理的 Sina/腾讯，避免东财无响应把同步拖死。
    fallback = sources.sina_daily(code, start, end, shares)
    if not fallback:
        fallback = sources.tencent_daily(code, start, end, shares)
    if fallback:
        _fill_turnover(fallback, shares)
        db.save_daily_bars(code, fallback)
        return fallback
    # 两个直连源都失败后才尝试东财；最多 2 次，避免长时间占用线程。
    try:
        frame = retry_call(lambda: ak.stock_zh_a_hist(
            symbol=code, period="daily",
            start_date=start.strftime("%Y%m%d"), end_date=end.strftime("%Y%m%d"),
            adjust="", timeout=10,
        ), attempts=2)
    except Exception:  # noqa: BLE001
        return cached
    if frame is None or frame.empty:
        return cached
    bars = [bar_from_row(row) for _, row in frame.iterrows()]
    _fill_turnover(bars, shares)
    db.save_daily_bars(code, bars)
    return bars


def _premarket_auction(code: str, target: date) -> dict[str, Any] | None:
    try:
        frame = retry_call(lambda: ak.stock_zh_a_hist_pre_min_em(
            symbol=code, start_time="09:24:00", end_time="09:26:00"
        ), attempts=3)
    except Exception:  # noqa: BLE001 - 盘前接口失败时交给 09:30 K 线
        return None
    if frame is None or frame.empty:
        return None
    rows = {str(row["时间"])[11:16]: row for _, row in frame.iterrows()}
    auction = rows.get("09:26")
    if auction is None or str(auction["时间"])[:10] != target.isoformat():
        return None
    volume = numeric(auction["成交量"]) * 100
    open_price = numeric(auction["开盘"])
    if volume <= 0 or open_price <= 0:
        return None
    previous_minute = rows.get("09:24")
    return {
        "open_price": open_price,
        "auction_vol": volume,
        "auction_amount": numeric(auction["成交额"]),
        "match_price_2445": numeric(previous_minute["收盘"]) if previous_minute is not None else open_price,
        "auction_source": "pre_market",
        "auction_time": str(auction["时间"]),
        "micro_available": previous_minute is not None,
    }


def _minute_auction(code: str, target: date) -> dict[str, Any] | None:
    """历史回放：09:30 的一分钟K线等于集合竞价那一笔（已用免费源逐只比对过）。"""
    try:
        sina = sources.sina_auction(code, target)
    except Exception:  # noqa: BLE001
        sina = None
    if sina is not None:
        return sina
    try:
        direct = sources.eastmoney_auction(code, target)
    except Exception:  # noqa: BLE001
        direct = None
    if direct is not None:
        return direct
    day = target.strftime("%Y-%m-%d")
    try:
        frame = auction_call(lambda: ak.stock_zh_a_hist_min_em(
            symbol=code, start_date=f"{day} 09:30:00", end_date=f"{day} 09:30:00", period="1", adjust=""
        ))
    except Exception:  # noqa: BLE001
        return None
    if frame is None or frame.empty:
        return None
    row = frame.iloc[0]
    if str(row["时间"])[:10] != target.isoformat():
        return None
    volume = numeric(row["成交量"]) * 100
    open_price = numeric(row["开盘"]) or numeric(row["收盘"])
    if volume <= 0 or open_price <= 0:
        return None
    return {
        "open_price": open_price,
        "auction_vol": volume,
        "auction_amount": numeric(row["成交额"]),
        "match_price_2445": numeric(row["收盘"]),
        "auction_source": "minute_0930",
        "auction_time": str(row["时间"]),
        "micro_available": False,
    }


def fetch_auction(code: str, target: date) -> dict[str, Any]:
    if minute_store.covers(target.isoformat()):
        local = minute_store.auction_bar(code, target.isoformat())
        if local:
            local["auction_date"] = target.isoformat()
            return local
    now = datetime.now(db.TZ_CN)
    if target == now.date() and clock_time(9, 25) <= now.time() < clock_time(9, 30):
        auction = _premarket_auction(code, target)
        if auction is not None:
            auction["auction_date"] = target.isoformat()
            return auction
    auction = _minute_auction(code, target)
    if auction is None:
        auction = _premarket_auction(code, target)
    if auction is None:
        raise ValueError("未取到 09:25 集合竞价回报")
    auction["auction_date"] = target.isoformat()
    return auction


def sync_market(job_id: str, requested_day: str | None = None, limit: int | None = None) -> dict[str, Any]:
    db.update_sync_job(job_id, status="running", message="正在获取交易日历与涨停池")
    calendar = trading_days()
    target = resolve_target_day(calendar, requested_day)
    existing = db.latest_dataset_for_day(target.isoformat())
    if existing and int(existing.get("stock_count") or 0) >= 30:
        db.update_sync_job(
            job_id,
            status="completed",
            trading_day=target.isoformat(),
            dataset_id=existing["id"],
            progress=100,
            total=100,
            message=f"{target} 数据集已存在 · {existing['stock_count']} 只，跳过重复抓取",
        )
        return {
            "dataset_id": existing["id"],
            "stock_count": int(existing["stock_count"]),
            "failed_count": 0,
            "failures": {},
            "trading_day": target.isoformat(),
            "quality_notes": ["复用同日已同步数据集，未重复访问行情源"],
            "backfilled": 0,
            "reused": True,
        }
    now = datetime.now(db.TZ_CN)
    # 09:25 撮合完成后才有竞价成交；09:24 只能作为末段参考价。
    if target == now.date() and now.time() < clock_time(9, 26, 0):
        raise ValueError("集合竞价尚未结束：09:26 之后才能同步当日 09:25 竞价")
    earlier = [day for day in calendar if day < target]
    if len(earlier) < 10:
        raise ValueError("历史交易日不足")
    previous = earlier[-1]
    pool_days = earlier[-12:]
    db.update_sync_job(job_id, trading_day=target.isoformat(), message=f"目标 {target} · 获取近12日涨停池")

    pools: dict[date, dict[str, dict[str, Any]]] = {}
    pool_errors: dict[str, str] = {}

    def pool_worker(day: date) -> None:
        try:
            frame = sources.eastmoney_zt_pool(day)
            pools[day] = pool_map(frame) if frame is not None and not frame.empty else {}
        except Exception as exc:  # noqa: BLE001 - 单日涨停池失败不应拖垮整次同步
            pool_errors[day.isoformat()] = str(exc)
            pools[day] = {}

    executor = ThreadPoolExecutor(max_workers=4)
    futures = [executor.submit(pool_worker, day) for day in pool_days]
    try:
        for future in as_completed(futures, timeout=90):
            future.result()
    except TimeoutError:
        for future, day in zip(futures, pool_days):
            if not future.done():
                pool_errors[day.isoformat()] = "涨停池请求超时"
                pools.setdefault(day, {})
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
    yesterday_pool = pools[previous]
    if not yesterday_pool:
        detail = "；".join(f"{day}: {error}" for day, error in list(pool_errors.items())[-3:])
        raise ValueError(f"{previous} 涨停池为空。{detail}")

    # 只同步「昨日涨停池 + 观察池」：12 日池子只用于算基因，
    # 不再把 12 日所有涨停股都逐只抓日线/竞价，避免 09:26 被几百个请求拖死。
    ordered_codes = list(yesterday_pool)
    if limit:
        ordered_codes = ordered_codes[:limit]
    # 帖子选中但不在涨停池的票也抓一次；失败只记录，不影响主流程。
    ordered_codes.extend(code for code in POST_WATCHLIST if code not in ordered_codes)
    codes = list(dict.fromkeys(ordered_codes))

    # 盘口只在 09:25—09:30 有意义，必须在耗时的逐只抓取之前先快照，否则窗口会关掉。
    quotes_book: dict[str, dict[str, Any]] = {}
    book_error = ""
    try:
        quotes_book = sources.auction_book(codes, target, datetime.now(db.TZ_CN))
    except Exception as exc:  # noqa: BLE001 - 补充源失败不阻断主流程
        book_error = str(exc)

    spot_auctions: dict[str, dict[str, Any]] = {}
    spot_error = ""
    if target == datetime.now(db.TZ_CN).date():
        try:
            spot_auctions = sources.tencent_auction(codes, target, datetime.now(db.TZ_CN))
            if not spot_auctions:
                spot_auctions = sources.spot_auction(codes, target, datetime.now(db.TZ_CN))
        except Exception as exc:  # noqa: BLE001
            spot_error = str(exc)

    total = len(codes) * 2
    progress = SyncProgress(job_id, total)
    db.update_sync_job(job_id, total=total, message=f"准备同步 {len(codes)} 只涨停基因股票")

    histories: dict[str, list[dict[str, Any]]] = {}
    failures: dict[str, str] = {}
    history_start = previous - timedelta(days=90)
    shares_map: dict[str, float] = {}
    datasets = [item for item in db.list_datasets() if item.get("stock_count")]
    if datasets:
        latest = max(datasets[:6], key=lambda item: int(item.get("stock_count") or 0))
        for item in db.get_stocks(int(latest["id"])):
            value = float(item.get("free_float_shares") or 0)
            if value > 0:
                shares_map[str(item["code"])] = value

    def history_worker(code: str) -> None:
        try:
            histories[code] = fetch_history(code, history_start, previous, shares_map.get(code, 0.0))
        except Exception as exc:
            failures[code] = f"日线：{exc}"
        finally:
            progress.advance("日线与昨日筹码")

    with ThreadPoolExecutor(max_workers=12) as executor:
        futures = [executor.submit(history_worker, code) for code in codes]
        for future in as_completed(futures):
            future.result()

    auctions: dict[str, dict[str, Any]] = {}

    def auction_worker(code: str) -> None:
        try:
            auctions[code] = spot_auctions.get(code) or fetch_auction(code, target)
        except Exception as exc:
            failures[code] = f"竞价：{exc}"
        finally:
            progress.advance("09:25集合竞价")

    executor = ThreadPoolExecutor(max_workers=8)
    futures = {executor.submit(auction_worker, code): code for code in codes}
    try:
        for future in as_completed(futures, timeout=60):
            future.result()
    except TimeoutError:
        for future, code in futures.items():
            if not future.done():
                failures[code] = "竞价请求超时"
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    # 资金流排行的窗口截止到最近交易日，历史回放不引入未来数据。
    latest_trading_day = max(day for day in calendar if day <= datetime.now(db.TZ_CN).date())
    fund_flow: dict[str, float] = {}
    flow_error = ""
    if target == latest_trading_day:
        try:
            fund_flow = sources.fund_flow_5d()
        except Exception as exc:  # noqa: BLE001
            flow_error = str(exc)
    lhb: dict[str, float] = {}
    lhb_error = ""
    try:
        lhb = sources.lhb_net_inflow(previous)
    except Exception as exc:  # noqa: BLE001
        lhb_error = str(exc)

    industry_counts: dict[str, int] = {}
    for stock in yesterday_pool.values():
        industry = stock["industry"]
        if industry:
            industry_counts[industry] = industry_counts.get(industry, 0) + 1

    # 题材热度：用概念板块成分 × 昨日涨停池，算「该股最热题材」的涨停家数与密度
    concept_map = load_concepts()
    theme_stats: dict[str, tuple[int, float, str]] = {}
    if concept_map:
        yesterday_codes = set(yesterday_pool)
        scored = []
        for name, members in concept_map.items():
            size = len(members)
            if not 10 <= size <= 300:
                continue
            hits = len(set(members) & yesterday_codes)
            if hits < 2:
                continue
            scored.append((hits / size, hits, name, set(members)))
        for code in yesterday_codes:
            best = None
            for density, hits, name, members in scored:
                if code not in members:
                    continue
                score = hits * (density ** 0.5)
                if best is None or score > best[0]:
                    best = (score, hits, density, name)
            if best:
                theme_stats[code] = (best[1], round(best[2] * 100, 2), best[3])

    recent_info: dict[str, dict[str, Any]] = {}
    for day in reversed(pool_days):
        for code, info in pools[day].items():
            recent_info.setdefault(code, info)

    backfilled = sum(1 for auction in auctions.values() if auction["auction_source"] == "minute_0930")
    notes = build_quality_notes(
        backfilled=backfilled,
        book=len(quotes_book),
        fund_flow=len(fund_flow),
        lhb=len(lhb),
        concept=bool(concept_map),
    )
    if book_error:
        notes.append(f"腾讯盘口取数失败：{book_error}")
    if spot_error:
        notes.append(f"09:26 全市场快照取数失败：{spot_error}")
    if any(auction.get("auction_source") == "sina_kline_0931" for auction in auctions.values()):
        notes.append("部分历史竞价用 Sina 09:31 开盘近似（09:30 缺失；成交量含首分钟）")
    if flow_error:
        notes.append(f"资金流排行取数失败：{flow_error}")
    if lhb_error:
        notes.append(f"龙虎榜取数失败：{lhb_error}")
    if pool_errors:
        notes.append(
            "部分历史涨停池失败："
            + "；".join(f"{day} {error}" for day, error in list(pool_errors.items())[:3])
        )

    stocks = []
    for code in codes:
        bars = sorted(histories.get(code, []), key=lambda bar: bar["trade_date"])
        auction = auctions.get(code)
        if not bars or not auction:
            continue
        yesterday = bars[-1]
        if yesterday["trade_date"] != previous.isoformat():
            failures[code] = "昨日停牌或日线缺失"
            continue
        yest_volume = yesterday["volume"] * 100
        info = recent_info.get(code, {
            "name": POST_WATCHLIST.get(code, {}).get("name", code),
            "industry": "",
            "free_market_cap": 0,
        })
        zt_info = yesterday_pool.get(code, {})
        yest_turnover = numeric(yesterday.get("turnover"))
        # 旧缓存/兜底日线可能缺换手率；涨停池有流通市值，可用 流通市值/收盘价 反推股本。
        if yest_turnover <= 0 and yest_volume > 0:
            pool_cap = numeric(zt_info.get("free_market_cap")) or numeric(info.get("free_market_cap"))
            if pool_cap > 0 and numeric(yesterday["close"]) > 0:
                shares = pool_cap * 1e8 / numeric(yesterday["close"])
                yest_turnover = yest_volume / shares * 100.0
                yesterday["turnover"] = round(yest_turnover, 4)
        if yest_turnover <= 0 or yest_volume <= 0:
            failures[code] = "昨日换手率或成交量无效"
            continue
        free_float_shares = yest_volume / (yest_turnover / 100)
        is_zt = code in yesterday_pool
        # 免费源没有自由流通股本；用昨日成交量 / 换手率反推流通股本，再乘收盘价。
        # 字段名 free_market_cap 保留以兼容旧数据集，页面标注为“流通市值（换手推算）”。
        market_cap = free_float_shares * yesterday["close"] / 100000000
        if market_cap <= 0:
            market_cap = zt_info.get("free_market_cap", 0) or info.get("free_market_cap", 0)
        closes = [bar["close"] for bar in bars[-10:]]
        stocks.append({
            "code": code,
            "name": info["name"],
            "free_market_cap": market_cap,
            "prev_close": yesterday["close"],
            "open_price": auction["open_price"],
            "yest_vol": yest_volume,
            "auction_vol": auction["auction_vol"],
            "auction_amount": auction["auction_amount"],
            "free_float_shares": free_float_shares,
            "is_zt": is_zt,
            "zt_count": zt_info.get("zt_count", 0),
            "zt_count_4": sum(code in pools[day] for day in pool_days[-4:]),
            "zt_count_8": sum(code in pools[day] for day in pool_days[-8:]),
            "zt_count_12": sum(code in pools[day] for day in pool_days),
            "yest_turnover": yest_turnover,
            "yest_high": yesterday["high"],
            "yest_low": yesterday["low"],
            "yest_pct": yesterday["pct"],
            "ma10": sum(closes) / len(closes) if len(closes) == 10 else 0,
            "match_price_2445": auction["match_price_2445"],
            "bid1_unmatched_vol": quotes_book.get(code, {}).get("bid1_vol", 0.0),
            "micro_structure_available": auction["micro_available"],
            "market_zt_count": len(yesterday_pool),
            "seal_amount": zt_info.get("seal_amount", 0),
            "break_count": zt_info.get("break_count", 0),
            "concept_limit_ups": industry_counts.get(info.get("industry", ""), 0),
            "theme_name": theme_stats.get(code, (0, 0.0, ""))[2],
            "theme_limit_ups": theme_stats.get(code, (0, 0.0, ""))[0],
            "theme_density": theme_stats.get(code, (0, 0.0, ""))[1],
            "lhb_net_inflow": round(lhb.get(code, 0.0), 2),
            "main_force_inflow_5d": round(fund_flow.get(code, 0.0), 2),
            "is_core": None,
            "industry": info.get("industry", ""),
            "auction_date": auction["auction_date"],
            "auction_time": auction.get("auction_time", ""),
            "auction_source": auction.get("auction_source", ""),
            "post_watch": code in POST_WATCHLIST,
            "data_quality": notes,
        })

    if not stocks:
        detail = "；".join(list(failures.values())[:5])
        raise ValueError(f"没有可用的竞价样本。{detail}")
    minimum = min(50, max(5, int(len(codes) * 0.5)))
    if len(stocks) < minimum:
        detail = "；".join(list(failures.values())[:5])
        raise ValueError(f"有效样本不足：{len(stocks)}/{len(codes)}，拒绝生成残缺数据集。{detail}")
    dataset_id = db.create_dataset(
        target.isoformat(),
        f"AKShare · {target.isoformat()} 集合竞价",
        "akshare",
        stocks,
    )
    db.update_sync_job(
        job_id,
        dataset_id=dataset_id,
        progress=total,
        total=total,
        message=f"已同步 {len(stocks)} 只，失败 {len(failures)} 只，正在计算策略",
    )
    return {
        "dataset_id": dataset_id,
        "stock_count": len(stocks),
        "failed_count": len(failures),
        "failures": failures,
        "trading_day": target.isoformat(),
        "quality_notes": notes,
        "backfilled": backfilled,
    }


def enrich_dataset(dataset_id: int) -> dict[str, Any]:
    """给已有数据集补 5 日主力净流入 / T-1 龙虎榜，不重抓竞价与日线。

    资金流排行只覆盖最近一个交易日，历史数据集只补龙虎榜，避免把未来资金带进回放。
    """
    dataset = db.get_dataset(dataset_id)
    if not dataset:
        raise ValueError("数据集不存在")
    day = date.fromisoformat(dataset["trading_day"])
    calendar = trading_days()
    today = datetime.now(db.TZ_CN).date()
    earlier = [item for item in calendar if item < day]
    previous = earlier[-1] if earlier else None
    latest = max((item for item in calendar if item <= today), default=day)

    stocks = db.get_stocks(dataset_id)
    if not stocks:
        raise ValueError("数据集为空")

    lhb: dict[str, float] = {}
    lhb_error = ""
    if previous:
        try:
            lhb = sources.lhb_net_inflow(previous)
        except Exception as exc:  # noqa: BLE001
            lhb_error = str(exc)

    fund_flow: dict[str, float] = {}
    flow_error = ""
    flow_skipped = day != latest
    if not flow_skipped:
        try:
            fund_flow = sources.fund_flow_5d()
        except Exception as exc:  # noqa: BLE001
            flow_error = str(exc)

    filled_fund = filled_lhb = 0
    stamped = db.now_iso()
    for stock in stocks:
        if fund_flow:
            stock["main_force_inflow_5d"] = round(fund_flow.get(stock["code"], 0.0), 2)
            if stock["code"] in fund_flow:
                filled_fund += 1
        if lhb:
            stock["lhb_net_inflow"] = round(lhb.get(stock["code"], 0.0), 2)
            if stock["code"] in lhb:
                filled_lhb += 1
        notes = [
            note for note in stock.get("data_quality", [])
            if "主力净流入" not in note and "龙虎榜" not in note
        ]
        if fund_flow:
            notes.append(f"5 日主力净流入已补齐：东方财富资金流排行（截至最近收盘）")
        elif flow_skipped:
            notes.append("非最近交易日：5 日主力净流入未补，避免使用未来资金")
        elif flow_error:
            notes.append(f"5 日主力净流入补取失败：{flow_error}")
        else:
            notes.append("5 日主力净流入补取失败：资金流排行返回空表")
        if lhb:
            notes.append(f"龙虎榜净买额已补齐：{previous} 东方财富龙虎榜")
        elif lhb_error:
            notes.append(f"龙虎榜补取失败：{lhb_error}")
        else:
            notes.append(f"龙虎榜净买额未补齐：{previous.isoformat() if previous else '无可比交易日'} 无上榜记录")
        stock["data_quality"] = list(dict.fromkeys(notes))

    db.update_stocks(dataset_id, stocks)
    return {
        "dataset_id": dataset_id,
        "trading_day": dataset["trading_day"],
        "previous_day": previous.isoformat() if previous else None,
        "fund_flow_filled": filled_fund,
        "lhb_filled": filled_lhb,
        "fund_flow_skipped": flow_skipped,
        "fund_flow_error": flow_error,
        "lhb_error": lhb_error,
        "enriched_at": stamped,
    }
