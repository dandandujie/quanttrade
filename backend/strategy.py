"""A股 09:25 集合竞价「弱转强」选股算法的工程化实现。

对应文档《A股0925集合竞价选股算法》第三、五章：
    Filter 1-6  ->  main_filter_reasons()
    S_total     ->  score_main()
    四级标签    ->  evaluate()

与文档原值不一致的参数统一登记在 CALIBRATIONS 中，随每次运行写入结果，
前端「策略规则」页直接展示，避免"改了哪里"无从追溯。
"""

import math
from typing import Any

from .schemas import StockInput, StrategyConfig
from .sentiment import sentiment_for

ALLOWED_PREFIXES = ("600", "601", "603", "605", "000", "001", "002", "003")

LABEL_GREEN = "✅ 主档"
LABEL_NEUTRAL = "主档合格·非top1"
LABEL_CANDIDATE = "候选"

SPEC_VERSION = "2.2"

# 文档给出的原始阈值 vs 按文档 19 个实盘样本反推的结果。
# 文档散文里的阈值（如竞价昨量比 ≥ 7.5%）会把它自己列出的样本全部拦掉，
# 以样本为准做最小改动，逐条登记如下。
CALIBRATIONS = [
    {
        "param": "yest_turnover_min",
        "doc": "3.5%",
        "value": "1.2%",
        "reason": "文档样本 红太阳 T-1 换手 1.38%、水井坊 1.85%，按 3.5% 会被误杀",
    },
    {
        "param": "yest_turnover_max",
        "doc": "25%",
        "value": "35%",
        "reason": "文档 11/04 主档样本 传智教育 T-1 换手 30.11%，25% 会把它拦掉",
    },
    {
        "param": "main_open_min",
        "doc": "3.30%",
        "value": "3.0%",
        "reason": "文档 11/06 主档样本 城市传媒竞价 +3.25%，压线下沿",
    },
    {
        "param": "main_min_vol_ratio",
        "doc": "7.5%",
        "value": "0.5%",
        "reason": "文档样本 引力传媒 0.91%、传智教育 0.90% 压线下沿；1.4% 会漏掉这两只，0.5% 才能覆盖样本",
    },
    {
        "param": "main_min_auction_turnover",
        "doc": "0.60%",
        "value": "0.02%",
        "reason": "免费源按流通股本计算，文档样本 金达威 0.033%、水井坊 0.091%、郑中设计 0.202%",
    },
    {
        "param": "main_min_auction_amount",
        "doc": "1200万",
        "value": "200万",
        "reason": "文档样本 城市传媒 355万、金达威 357万、春光科技 499万、南矿集团 559万；700万会漏掉四只",
    },
    {
        "param": "free_mcap_max",
        "doc": "135亿",
        "value": "250亿",
        "reason": "文档样本 西部黄金 208亿、水井坊 137亿；150亿会漏掉西部黄金",
    },
    {
        "param": "candidate_min_vol_ratio",
        "doc": "6.0%",
        "value": "1.2%",
        "reason": "文档候选样本 东方海洋 4.79%、九阳股份 2.52%、桂林三金 3.47%，按 6% 会全部落选",
    },
    {
        "param": "candidate_min_auction_amount",
        "doc": "1000万",
        "value": "500万",
        "reason": "候选池是主档之外的观察池，保留 500 万流动性底线，避免和主档 700 万门槛重复过严",
    },
    {
        "param": "green_min_vol_ratio",
        "doc": "14%",
        "value": "10%",
        "reason": "文档 11/03 ✅主档 祥源文旅真实竞价昨量比 11.09%",
    },
    {
        "param": "green_min_score",
        "doc": "82 分",
        "value": "65 分",
        "reason": "评分保留 50 分基准与 0.8 缩放，82 分在免费源上不可达；11/03 祥源文旅 65.1 分是当前口径下的实际边界",
    },
    {
        "param": "green_min_zt_count",
        "doc": "文档未量化（仅提情绪周期熔断）",
        "value": "80 家",
        "reason": "用昨日涨停家数区分主升/分化：11/02 为 125 家（11/03 亮绿标），11/03 为 48 家（11/04 不亮）",
    },
    {
        "param": "green_require_global_top1",
        "doc": "True",
        "value": "False",
        "reason": "按真实行情，11/03 祥源文旅全局量能仅第 11，Global_Top1 硬门控与文档样本自相矛盾；改用情绪周期门控",
    },
]


def rounded(value: float, digits: int = 2) -> float:
    return round(value, digits)


def metrics_for(stock: StockInput) -> dict[str, Any]:
    auction_pct = (stock.open_price / stock.prev_close - 1) * 100
    vol_ratio = stock.auction_vol / stock.yest_vol * 100
    auction_turnover = stock.auction_vol / stock.free_float_shares * 100
    micro = bool(stock.micro_structure_available) and stock.match_price_2445 > 0
    delta_last_15s = (
        (stock.open_price - stock.match_price_2445) / stock.prev_close * 100 if micro else 0.0
    )
    # 腾讯五档盘口补上「买一未匹配量」后，即使没有 09:24 快照也能算未匹配比。
    has_book = stock.bid1_unmatched_vol > 0
    unmatched_ratio = (
        stock.bid1_unmatched_vol / stock.auction_vol
        if has_book and stock.auction_vol > 0
        else 0.0
    )
    return {
        "auction_pct": auction_pct,
        "vol_ratio": vol_ratio,
        "auction_turnover": auction_turnover,
        "delta_last_15s": delta_last_15s,
        "unmatched_ratio": unmatched_ratio,
        "micro_available": micro or has_book,
    }


def display_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value if isinstance(value, bool)
        else rounded(value, 4) if isinstance(value, (int, float))
        else value
        for key, value in metrics.items()
    }


def main_reasons(
    stock: StockInput, m: dict[str, Any], config: StrategyConfig
) -> list[dict[str, str]]:
    """Filter 1-6：文档 3.1 的六大硬性算子。"""
    reasons: list[dict[str, str]] = []
    if not stock.code.startswith(ALLOWED_PREFIXES):
        reasons.append({"rule": "交易板块", "message": "仅限沪深主板 10% 涨跌幅股票"})
    if "ST" in stock.name.upper():
        reasons.append({"rule": "风险警示", "message": "剔除 ST 及风险警示股票"})
    if not config.free_mcap_min <= stock.free_market_cap <= config.free_mcap_max:
        reasons.append({
            "rule": "流通市值",
            "message": f"流通市值（换手推算）需在 {config.free_mcap_min:g}—{config.free_mcap_max:g} 亿元",
        })
    # 文档写的是"T-1 必须涨停"，但 10/30 的哈空调/天奇股份、11/04 的引力传媒/传智教育/金达威
    # 都带着主档标签却并非昨日涨停 —— 真实条件是"近 12 日有涨停基因 + 连板高度不超 2"。
    # zt_count_8 是旧数据集字段（历史同步只算了 12 日），用 max 兼容历史回放。
    gene_count = max(stock.zt_count_12, stock.zt_count_8)
    if gene_count < 1 or stock.zt_count > 2:
        reasons.append({
            "rule": "涨停基因",
            "message": "近 12 日须有过涨停，且当前连板高度不超过 2 板",
        })
    if not config.yest_turnover_min <= stock.yest_turnover <= config.yest_turnover_max:
        reasons.append({
            "rule": "昨日换手",
            "message": f"昨日换手率需在 {config.yest_turnover_min:g}%—{config.yest_turnover_max:g}%",
        })
    # 文档 Filter 4 要求 High(T-1) > Low(T-1)；但 11/04 样本 浙文影业 T-1 是一字板，
    # 真正要剔除的是"无量一字死板"，因此改为：一字板只在换手不足时剔除。
    if stock.yest_high <= stock.yest_low and stock.yest_turnover < config.yest_turnover_min:
        reasons.append({"rule": "昨日形态", "message": "剔除昨日无量一字死板"})
    if not config.main_open_min <= m["auction_pct"] <= config.main_open_max:
        reasons.append({
            "rule": "竞价涨幅",
            "message": f"竞价涨幅需在 {config.main_open_min:g}%—{config.main_open_max:g}%",
        })
    if m["vol_ratio"] < config.main_min_vol_ratio:
        reasons.append({
            "rule": "竞价昨量比",
            "message": f"竞价昨量比须 ≥ {config.main_min_vol_ratio:g}%",
        })
    if m["auction_turnover"] < config.main_min_auction_turnover:
        reasons.append({
            "rule": "竞价换手",
            "message": f"竞价换手率须 ≥ {config.main_min_auction_turnover:g}%",
        })
    if stock.auction_amount < config.main_min_auction_amount:
        reasons.append({
            "rule": "竞价成交额",
            "message": f"竞价成交额须 ≥ {config.main_min_auction_amount / 10_000:g} 万元",
        })
    return reasons


def seal_strength(stock: StockInput) -> float:
    """封单强度 = 昨日封板资金 / 流通市值（%）。"""
    if stock.seal_amount <= 0 or stock.free_market_cap <= 0:
        return 0.0
    return stock.seal_amount / (stock.free_market_cap * 1e8) * 100


def band_penalty(value: float, low: float, high: float) -> float:
    """甜点区偏离度：区间内 0，区间外按对数距离扣分（越大越好是错的方向）。

    0 值不是"落在区间内"，按最大偏离处理，否则缺失/零成交量会被当成满分。
    """
    if value <= 0:
        return -math.log(max(low, 1e-6) / 1e-6)
    if value < low:
        return -math.log(low / max(value, 1e-6))
    if value > high:
        return -math.log(value / high)
    return 0.0


def sweet_spot_score(pct: float, config: StrategyConfig) -> float:
    """F2 非对称甜点函数：黄金区 [4.10, 6.60]，锚点 5.20。"""
    if config.sweet_open_min <= pct <= config.sweet_open_max:
        return 28.0 - 2.5 * abs(pct - config.sweet_open_anchor)
    if pct < config.sweet_open_min:
        return 20.0 - 8.0 * (config.sweet_open_min - pct)
    return max(0.0, 18.0 - 22.0 * (pct - config.sweet_open_max))


def microstructure_score(m: dict[str, Any], cap: float = 20.0) -> float | None:
    """F3 末端抢筹：09:24:45 之后的 15 秒跳升 + 买一未匹配占比。

    免费源只在 09:25—09:30 窗口拿得到盘口；拿不到时返回 None，
    由调用方明确标注"未计分"，而不是伪造 0 分。
    """
    if not m.get("micro_available"):
        return None
    delta = min(12.0, 4.0 * max(0.0, float(m.get("delta_last_15s") or 0.0)))
    unmatched = min(8.0, 16.0 * max(0.0, float(m.get("unmatched_ratio") or 0.0)))
    return min(cap, delta + unmatched)


def score_main(
    stock: StockInput, m: dict[str, Any], config: StrategyConfig
) -> dict[str, Any]:
    """主档综合评分（0—100）。

    S = 50 + 0.8 × [涨幅甜点 - 量比/换手/市值偏离 + 封单强度] + 末端抢筹加分。

    权重来自 10/29—11/06 样本反推：量比、换手、市值是区间约束，
    区间内不扣分、区间外按对数距离扣分；涨幅是唯一有正向甜点的因子。
    末端抢筹最多 20 分，只在真实盘口可取时计入，历史回放不补。
    """
    f_gap = config.w_gap * sweet_spot_score(m["auction_pct"], config)
    f_rv = config.w_rv * band_penalty(m["vol_ratio"], config.rv_band_lo, config.rv_band_hi)
    f_turn = config.w_turn * band_penalty(
        stock.yest_turnover, config.turn_band_lo, config.turn_band_hi
    )
    f_mcap = config.w_mcap * band_penalty(
        stock.free_market_cap, config.mcap_band_lo, config.mcap_band_hi
    )
    seal = min(seal_strength(stock), 20.0)
    f_seal = config.w_seal * seal
    micro = microstructure_score(m, config.micro_max)
    raw = f_gap + f_rv + f_turn + f_mcap + f_seal
    total = max(0.0, min(100.0, 50.0 + raw * 0.8 + (micro or 0.0)))
    factors = [
        {
            "key": "gap",
            "label": "涨幅甜点",
            "value": rounded(f_gap),
            "max_abs": rounded(config.w_gap * 28.0),
            "kind": "reward",
            "detail": f"{m['auction_pct']:.2f}%",
        },
        {
            "key": "vol_ratio",
            "label": "量比偏离",
            "value": rounded(f_rv),
            "max_abs": 60.0,
            "kind": "penalty",
            "detail": f"{m['vol_ratio']:.2f}%",
        },
        {
            "key": "turnover",
            "label": "换手偏离",
            "value": rounded(f_turn),
            "max_abs": 100.0,
            "kind": "penalty",
            "detail": f"{stock.yest_turnover:.2f}%",
        },
        {
            "key": "market_cap",
            "label": "市值偏离",
            "value": rounded(f_mcap),
            "max_abs": 120.0,
            "kind": "penalty",
            "detail": f"{stock.free_market_cap:.1f}亿",
        },
        {
            "key": "seal",
            "label": "封单强度",
            "value": rounded(f_seal),
            "max_abs": rounded(config.w_seal * config.micro_max),
            "kind": "reward",
            "detail": f"{seal:.2f}%",
        },
        {
            "key": "micro",
            "label": "末端抢筹",
            "value": rounded(micro) if micro is not None else None,
            "max_abs": config.micro_max,
            "kind": "reward",
            "detail": (
                f"Δ15s {m['delta_last_15s']:+.2f}% / 未匹配 {m['unmatched_ratio']:.1%}"
                if micro is not None else "无盘口，未计分"
            ),
        },
    ]
    return {
        "factors": factors,
        "micro_available": micro is not None,
        "total": rounded(total),
        "raw_total": rounded(raw),
    }


def candidate_reasons(
    stock: StockInput, m: dict[str, Any], config: StrategyConfig
) -> tuple[list[dict[str, str]], str | None]:
    """Pipeline B（文档 4.1）：近 8 日涨停基因 + N 字反包 / 温和弱转强。"""
    reasons: list[dict[str, str]] = []
    subtype = None
    if not stock.code.startswith(ALLOWED_PREFIXES) or "ST" in stock.name.upper():
        reasons.append({"rule": "基础股票池", "message": "候选池同样仅限非 ST 沪深主板"})
    if not config.free_mcap_min <= stock.free_market_cap <= config.free_mcap_max:
        reasons.append({"rule": "流通市值", "message": "流通市值（换手推算）不在策略范围内"})
    if stock.zt_count_8 < 1:
        reasons.append({"rule": "近期涨停基因", "message": "近 8 个交易日须至少有 1 次涨停"})
    if stock.ma10 <= 0 or stock.prev_close < stock.ma10:
        reasons.append({"rule": "趋势约束", "message": "昨日收盘价须站上 10 日均线"})
    # 11/03 的候选 #3 青岛食品是 6 连板 —— 候选池不做连板高度限制
    if not stock.is_zt and -6.5 <= stock.yest_pct <= 3.0:
        subtype = "N字反包"
    elif stock.is_zt and config.candidate_open_min <= m["auction_pct"] <= config.candidate_b2_open_max:
        subtype = "温和弱转强"
    else:
        reasons.append({"rule": "反包子类", "message": "须满足断板洗盘或昨日涨停温和弱转强形态"})
    if not config.candidate_open_min <= m["auction_pct"] <= config.candidate_open_max:
        reasons.append({
            "rule": "候选竞价窗",
            "message": f"候选池竞价涨幅需在 {config.candidate_open_min:g}%—{config.candidate_open_max:g}%",
        })
    if m["vol_ratio"] < config.candidate_min_vol_ratio:
        reasons.append({
            "rule": "候选量能",
            "message": f"候选池竞价昨量比须 ≥ {config.candidate_min_vol_ratio:g}%",
        })
    if stock.auction_amount < config.candidate_min_auction_amount:
        reasons.append({
            "rule": "候选成交额",
            "message": f"候选池竞价成交额须 ≥ {config.candidate_min_auction_amount / 10_000:g} 万元",
        })
    return reasons, subtype


def risk_flags(stock: StockInput, m: dict[str, Any], pool: str) -> list[dict[str, Any]]:
    """数据侧风险提示：不改变硬过滤，只用于置信度和稳健首选。

    这些阈值来自 11/03—11/10 的前向收益复盘：基因只有 1 次、流通市值过小、
    竞价额过低、昨日换手过高、量比过热、连板过高是主要负收益特征。
    """
    gene = max(stock.zt_count_12, stock.zt_count_8)
    flags: list[dict[str, Any]] = []
    if gene <= 1:
        flags.append({"key": "weak_gene", "label": "涨停基因仅 1 次", "penalty": -6 if pool == "main" else -8})
    if stock.free_market_cap < 40:
        flags.append({"key": "small_cap", "label": "流通市值 < 40 亿", "penalty": -4 if pool == "main" else -5})
    if stock.auction_amount < 10_000_000:
        flags.append({"key": "thin_auction", "label": "竞价成交额 < 1000 万", "penalty": -3 if pool == "main" else -4})
    if stock.zt_count >= 3:
        flags.append({"key": "high_board", "label": f"昨日 {stock.zt_count} 连板", "penalty": -6})
    if stock.yest_turnover >= 30:
        flags.append({"key": "high_yest_turnover", "label": "昨日换手 ≥ 30%", "penalty": -3 if pool == "main" else -4})
    if m["vol_ratio"] >= 50:
        flags.append({"key": "blowoff_volume", "label": "竞价量比 ≥ 50%", "penalty": -3})
    if pool == "main" and 0.5 <= m["auction_turnover"] <= 1.0:
        flags.append({"key": "crowded_auction", "label": "竞价换手 0.5%—1.0%", "penalty": -3})
    if stock.theme_limit_ups >= 10 or stock.theme_density >= 15:
        flags.append({
            "key": "theme_crowded",
            "label": f"题材拥挤（{stock.theme_name or '概念'} {stock.theme_limit_ups}家 / {stock.theme_density:.1f}%）",
            "penalty": -2,
        })
    if stock.concept_limit_ups >= 10:
        flags.append({
            "key": "industry_crowded",
            "label": f"同行业昨日涨停 {stock.concept_limit_ups} 家",
            "penalty": -2,
        })
    return flags


def confidence_score(total: float, flags: list[dict[str, Any]]) -> float:
    return rounded(max(0.0, min(100.0, total + sum(float(flag["penalty"]) for flag in flags))), 1)


def buy_plan(stock: StockInput, action: str) -> dict[str, Any]:
    """09:25 生成、09:30 起执行的买入条件单。

    价格只是触发参考，不是保证成交价；实盘由信号监控按 5 秒行情更新。
    """
    auction = round(stock.open_price, 2)
    # 31,664 条本地 1 分钟样本（2026-01—09，主板，gap 3%—7.5%）显示：
    # 09:35 相对开盘低于 0 的承接偏弱，>2% 再追的收益转负；
    # 0%—2% 是相对可执行的确认区。
    return {
        "action": action,
        "window": "09:30:00—09:35:00",
        "auction_price": auction,
        "entry_low": round(auction * 1.0, 2),
        "entry_high": round(auction * 1.02, 2),
        "abort_below": round(auction * 0.98, 2),
        "stop_below": round(auction * 0.98, 2),
        "target_price": round(auction * 1.05, 2),
        "trigger": f"09:30 后价格在 {auction:.2f}—{round(auction * 1.02, 2)} 且站上当日均价线",
        "cancel": f"09:35 仍未触发，或跌破 {round(auction * 0.98, 2)}",
        "note": "不买负承接；高于 +2% 不追，等回踩确认区",
    }


def _percentile_ranks(values: list[float]) -> list[float]:
    """把一组值映射到 0—1 的百分位；单样本返回 0.5。"""
    count = len(values)
    if count == 0:
        return []
    if count == 1:
        return [0.5]
    result = [0.5] * count
    order = sorted(range(count), key=lambda index: values[index])
    for rank, index in enumerate(order):
        result[index] = rank / (count - 1)
    return result


def apply_return_scores(items: list[dict[str, Any]]) -> None:
    """横截面「收益优先分」：基因、非昨日涨停、低量比、大市值、高换手。

    方向来自 11/03—11/10 的次日收盘净收益：基因次数与市值正相关，
    昨日已涨停、超高量比、极高竞价换手负相关。它只使用当日可见字段，
    不引入未来数据；作为模型分之外的第二套排序。
    """
    if not items:
        return
    genes = [max(item["stock"].zt_count_12, item["stock"].zt_count_8) for item in items]
    is_zt = [1.0 if item["stock"].is_zt else 0.0 for item in items]
    rv = [item["metrics"]["vol_ratio"] for item in items]
    cap = [item["stock"].free_market_cap for item in items]
    yest = [item["stock"].yest_turnover for item in items]
    p_gene = _percentile_ranks(genes)
    p_iszt = _percentile_ranks(is_zt)
    p_rv = _percentile_ranks(rv)
    p_cap = _percentile_ranks(cap)
    p_yest = _percentile_ranks(yest)
    for index, item in enumerate(items):
        item["return_score"] = rounded(100.0 * (
            0.40 * p_gene[index]
            + 0.25 * (1.0 - p_iszt[index])
            + 0.15 * (1.0 - p_rv[index])
            + 0.10 * p_cap[index]
            + 0.10 * p_yest[index]
        ), 1)


def green_gate(
    rank: int,
    global_rank: int | None,
    score_total: float,
    m: dict[str, Any],
    zt_count: int,
    zt_count_known: bool,
    config: StrategyConfig,
) -> list[dict[str, Any]]:
    """第四级：✅ 主档 的唯一性熔断锁（文档 5.1 GREEN_TAG_HARD_RULES）。"""
    require_global = bool(getattr(config, "green_require_global_top1", False))
    return [
        {
            "key": "local_top1",
            "label": "主档池第一",
            "pass": rank == 1,
            "required": True,
            "value": f"#{rank}",
            "threshold": "#1",
        },
        {
            "key": "score",
            "label": "绝对得分",
            "pass": score_total >= config.green_min_score,
            "required": True,
            "value": f"{score_total:.1f}",
            "threshold": f"≥{config.green_min_score:g}",
        },
        {
            "key": "open_pct",
            "label": "竞价涨幅",
            "pass": m["auction_pct"] >= config.green_min_open_pct,
            "required": True,
            "value": f"{m['auction_pct']:.2f}%",
            "threshold": f"≥{config.green_min_open_pct:g}%",
        },
        {
            "key": "vol_ratio",
            "label": "竞价昨量比",
            "pass": m["vol_ratio"] >= config.green_min_vol_ratio,
            "required": True,
            "value": f"{m['vol_ratio']:.2f}%",
            "threshold": f"≥{config.green_min_vol_ratio:g}%",
        },
        {
            "key": "market_regime",
            "label": "情绪主升期",
            "pass": zt_count_known and zt_count >= config.green_min_zt_count,
            "required": True,
            "value": f"昨涨停 {zt_count} 家" if zt_count_known else "全市场家数未知",
            "threshold": f"≥{config.green_min_zt_count}家",
        },
        {
            "key": "global_top1",
            "label": "全局量能第一",
            "pass": global_rank == 1,
            "required": require_global,
            "value": f"#{global_rank}" if global_rank else "—",
            "threshold": "#1",
        },
    ]


def evaluate(
    stocks: list[StockInput],
    dataset: dict[str, Any],
    config: StrategyConfig,
    doc_ranks: dict[str, int] | None = None,
    manual_excludes: set[str] | None = None,
    core_flags: dict[str, bool] | None = None,
) -> dict[str, Any]:
    core_flags = core_flags or {}
    # doc_ranks: {代码: 作者截图里的名次}，用于在界面上标出“文档命中”
    doc_ranks = doc_ranks or {}
    manual_excludes = manual_excludes or set()
    items: list[dict[str, Any]] = []

    for stock in stocks:
        metrics = metrics_for(stock)
        # 核心（主线）判定只用纯量价：算法不含题材/概念因子。
        #   1) 昨日封单强度 ≥ core_seal_strength%（资金主攻方向）
        #   2) 或已是二板（梯队地位）
        #   3) 或 T-1 龙虎榜净买额 ≥ core_lhb_net_inflow
        auto_core = (
            seal_strength(stock) >= config.core_seal_strength
            or stock.zt_count >= 2
            or stock.lhb_net_inflow >= config.core_lhb_net_inflow
        )
        is_core = stock.is_core if stock.is_core is not None else auto_core
        if config.core_from_samples and stock.code in core_flags:
            is_core = core_flags[stock.code]   # 可选：直接对齐作者截图的 ⭐核心
        items.append({"stock": stock, "metrics": metrics, "is_core": is_core})

    global_pool = sorted(
        (item for item in items if item["stock"].is_zt),
        key=lambda item: (item["metrics"]["vol_ratio"], item["stock"].auction_amount),
        reverse=True,
    )
    for index, item in enumerate(global_pool, start=1):
        item["global_rank"] = index
    for item in items:
        item.setdefault("global_rank", None)

    zt_items = [item for item in items if item["stock"].is_zt]
    avg_open = (
        sum(item["metrics"]["auction_pct"] for item in zt_items) / len(zt_items)
        if zt_items else 0.0
    )
    # 昨日涨停家数（情绪周期）：优先用同步时记录的全市场家数。
    # CSV/旧数据集可能没有这个字段，此时不能拿样本内数量冒充全市场家数，
    # 绿标的情绪门控必须 fail-closed。
    market_counts = [stock.market_zt_count for stock in stocks if stock.market_zt_count > 0]
    zt_count_known = bool(market_counts)
    zt_total = max(market_counts) if market_counts else len(zt_items)
    limit_down_count = sum(item["metrics"]["auction_pct"] <= -9.5 for item in zt_items)
    risk_reasons: list[str] = []
    if limit_down_count >= config.risk_limit_down_count:
        risk_reasons.append(f"昨板股竞价跌停达到 {limit_down_count} 家")
    if zt_items and avg_open < config.risk_avg_open_pct:
        risk_reasons.append(f"昨板股平均竞价涨幅仅 {avg_open:.2f}%")
    market_risk = bool(risk_reasons)

    main_qualified: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []

    for item in items:
        stock = item["stock"]
        m = item["metrics"]
        reasons = main_reasons(stock, m, config)
        if stock.code in manual_excludes:
            reasons = [{"rule": "人工排除", "message": "题材维度人工判定不入选"}]
        item["main_reasons"] = reasons
        if not reasons:
            item["score"] = score_main(stock, m, config)
            main_qualified.append(item)

        c_reasons, subtype = candidate_reasons(stock, m, config)
        if stock.code in manual_excludes:
            c_reasons = c_reasons + [{"rule": "人工排除", "message": "题材维度人工判定不入选"}]
        item["candidate_reasons"] = c_reasons
        if not c_reasons:
            fund_factor = min(2.0, max(0.1, 1 + stock.main_force_inflow_5d / config.fund_flow_scale))
            item["subtype"] = subtype
            item["fund_factor"] = rounded(fund_factor, 4)
            item["candidate_raw"] = m["vol_ratio"] * m["auction_turnover"] * fund_factor
            candidates.append(item)

    apply_return_scores(main_qualified)
    apply_return_scores(candidates)
    for index, item in enumerate(
        sorted(main_qualified, key=lambda item: item["return_score"], reverse=True), start=1
    ):
        item["return_rank"] = index
    for index, item in enumerate(
        sorted(candidates, key=lambda item: item["return_score"], reverse=True), start=1
    ):
        item["return_rank"] = index

    main_qualified.sort(
        key=lambda item: (item["score"]["total"], item["score"]["raw_total"]), reverse=True
    )
    candidates.sort(key=lambda item: item["candidate_raw"], reverse=True)
    for rank, item in enumerate(main_qualified, start=1):
        if rank > config.main_pool_size:
            item["main_reasons"].append({
                "rule": "主档名额",
                "message": f"评分第 {rank}，未进入主档 Top {config.main_pool_size}",
            })
    for rank, item in enumerate(candidates, start=1):
        if rank > config.candidate_pool_size:
            item["candidate_reasons"].append({
                "rule": "候选名额",
                "message": f"资金异动第 {rank}，未进入候选 Top {config.candidate_pool_size}",
            })

    main = main_qualified[: config.main_pool_size]
    # 候选分是排序键的单调压缩：排序仍按量比 × 竞价换手 × 资金系数，
    # 但展示值固定在 0—100，避免 100 倍量比把其余候选取值压成 0.x。
    max_raw = candidates[0]["candidate_raw"] if candidates else 0.0
    for item in candidates:
        item["candidate_score"] = (
            rounded(100.0 * math.sqrt(item["candidate_raw"] / max_raw), 1)
            if max_raw > 0 else 0.0
        )
    selected_candidates = candidates[: config.candidate_pool_size]

    main_codes = {item["stock"].code for item in main}
    candidate_codes = {item["stock"].code for item in selected_candidates}

    main_output: list[dict[str, Any]] = []
    for rank, item in enumerate(main, start=1):
        stock = item["stock"]
        m = item["metrics"]
        checks = green_gate(
            rank, item["global_rank"], item["score"]["total"], m, zt_total,
            zt_count_known, config,
        )
        is_green = all(check["pass"] for check in checks if check["required"])
        flags = risk_flags(stock, m, "main")
        main_output.append({
            "code": stock.code,
            "name": stock.name,
            "rank": rank,
            "model_rank": rank,
            "return_rank": item["return_rank"],
            "return_score": item["return_score"],
            "global_rank": item["global_rank"],
            "is_core": item["is_core"],
            "is_green": is_green,
            "label": LABEL_GREEN if is_green else LABEL_NEUTRAL,
            "doc_rank": doc_ranks.get(stock.code),
            "metrics": display_metrics(m),
            "score": {key: value for key, value in item["score"].items() if key != "raw_total"},
            "confidence": confidence_score(item["score"]["total"], flags),
            "risk_flags": flags,
            "preferred": False,
            "green_checks": checks,
            "input": stock.model_dump(),
        })

    # 稳健首选：只在前 N 名里找基因、市值、竞价额都过关的票；
    # 找不到就明确输出“无首选”，不把高风险的评分第一硬当买点。
    gated: list[dict[str, Any]] = []
    for item in main_output[: max(1, config.preferred_scan)]:
        payload = item["input"]
        gene = max(payload.get("zt_count_12", 0), payload.get("zt_count_8", 0))
        if (
            gene >= config.preferred_min_gene
            and payload["free_market_cap"] >= config.preferred_min_mcap
            and payload["auction_amount"] >= config.preferred_min_auction_amount
        ):
            gated.append(item)
    preferred = max(gated, key=lambda item: item["return_score"], default=None)
    if preferred:
        preferred["preferred"] = True

    # 发帖人的算法没有情绪熔断：11/06 全市场退潮（昨板平均竞价 −0.72%）时仍照常出票，
    # 所以这里不做「退潮日关闭候选池」，market_risk 只作为界面提示保留。
    candidate_output: list[dict[str, Any]] = []
    for rank, item in enumerate(selected_candidates, start=1):
        stock = item["stock"]
        flags = risk_flags(stock, item["metrics"], "candidates")
        candidate_output.append({
            "code": stock.code,
            "name": stock.name,
            "rank": rank,
            "global_rank": item["global_rank"],
            "is_core": item["is_core"],
            "dual_pool": stock.code in main_codes,
            "subtype": item["subtype"],
            "fund_factor": item["fund_factor"],
            "candidate_score": item["candidate_score"],
            "candidate_raw": rounded(item["candidate_raw"]),
            "return_rank": item["return_rank"],
            "return_score": item["return_score"],
            "confidence": confidence_score(item["candidate_score"], flags),
            "risk_flags": flags,
            "label": LABEL_CANDIDATE,
            "doc_rank": doc_ranks.get(stock.code),
            "metrics": display_metrics(item["metrics"]),
            "input": stock.model_dump(),
        })

    overflow: list[dict[str, Any]] = []
    for rank, item in enumerate(main_qualified, start=1):
        if rank <= config.main_pool_size:
            continue
        stock = item["stock"]
        flags = risk_flags(stock, item["metrics"], "main")
        overflow.append({
            "code": stock.code,
            "name": stock.name,
            "rank": rank,
            "pool": "main_overflow",
            "return_rank": item.get("return_rank"),
            "return_score": item.get("return_score"),
            "score": item["score"],
            "confidence": confidence_score(item["score"]["total"], flags),
            "risk_flags": flags,
            "metrics": display_metrics(item["metrics"]),
            "reasons": list(item["main_reasons"]),
            "candidate_reasons": item["candidate_reasons"],
            "input": stock.model_dump(),
        })

    watchlist: list[dict[str, Any]] = []
    for item in items:
        stock = item["stock"]
        if not stock.post_watch:
            continue
        pool = "main" if stock.code in main_codes else "candidates" if stock.code in candidate_codes else None
        watchlist.append({
            "code": stock.code,
            "name": stock.name,
            "pool": pool,
            "is_core": item["is_core"],
            "metrics": display_metrics(item["metrics"]),
            "score": item.get("score"),
            "candidate_score": item.get("candidate_score"),
            "return_rank": item.get("return_rank"),
            "return_score": item.get("return_score"),
            "main_reasons": list(item["main_reasons"]),
            "candidate_reasons": item["candidate_reasons"],
            "input": stock.model_dump(),
        })

    excluded: list[dict[str, Any]] = []
    for item in items:
        stock = item["stock"]
        if stock.code in main_codes or stock.code in candidate_codes:
            continue
        excluded.append({
            "code": stock.code,
            "name": stock.name,
            "post_watch": stock.post_watch,
            "is_core": item["is_core"],
            "metrics": display_metrics(item["metrics"]),
            "reasons": list(item["main_reasons"]),
            "candidate_reasons": item["candidate_reasons"],
            "input": stock.model_dump(),
        })

    green_count = sum(item["is_green"] for item in main_output)
    return_leader = min(main_output, key=lambda item: item["return_rank"], default=None)
    sentiment = sentiment_for(dataset["trading_day"])
    position_hint = sentiment.get("position_hint", "情绪中性：按个股信号正常执行")
    for item in main_output + candidate_output:
        plan = item.get("buy_plan")
        if not plan:
            continue
        plan["regime"] = sentiment["label"]
        plan["position_factor"] = sentiment["position_factor"]
        plan["position_note"] = position_hint
    quality_notes = list(dict.fromkeys(
        note for stock in stocks for note in stock.data_quality
    ))
    return {
        "dataset": dataset,
        "config": config.model_dump(),
        "spec": {
            "version": SPEC_VERSION,
            "calibrations": CALIBRATIONS,
        },
        "data_quality": quality_notes,
        "summary": {
            "scanned": len(stocks),
            "main_count": len(main_output),
            "candidate_count": len(candidate_output),
            "green_count": green_count,
            "excluded_count": len(excluded),
            "overflow_count": len(overflow),
            "watchlist_count": len(watchlist),
            "main_qualified_count": len(main_qualified),
            "candidate_qualified_count": len(candidates),
            "pass_rate": rounded(len(main_output) / len(stocks) * 100, 1) if stocks else 0,
            "preferred_rank": preferred["rank"] if preferred else None,
            "preferred_code": preferred["code"] if preferred else None,
            "preferred_name": preferred["name"] if preferred else None,
            "preferred_confidence": preferred["confidence"] if preferred else None,
            "return_leader_code": return_leader["code"] if return_leader else None,
            "return_leader_name": return_leader["name"] if return_leader else None,
        },
        "market": {
            "status": "risk" if market_risk else "normal",
            "avg_zt_open_pct": rounded(avg_open),
            "zt_count": zt_total,
            "zt_count_known": zt_count_known,
            "limit_down_count": limit_down_count,
            "green_min_zt_count": config.green_min_zt_count,
            "regime_ok": zt_count_known and zt_total >= config.green_min_zt_count,
            "risk_reasons": risk_reasons,
            "sentiment": sentiment,
            "position_hint": position_hint,
        },
        "main": main_output,
        "candidates": candidate_output,
        "excluded": excluded,
        "overflow": overflow,
        "watchlist": watchlist,
    }
