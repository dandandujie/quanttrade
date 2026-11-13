"""文档《A股0925集合竞价选股算法》1.2 节「核心样本池」的机器可读版本。

每来一天新帖，把当天截图里的标的追加到 DOC_SAMPLES，
「文档校验」页会自动拿真实行情与模型输出逐条比对，
把"系统跟文档不一样"变成一张可以逐条追责的清单。
"""

from typing import Any

from . import database as db
from .marks import excluded_for
from .post_watchlist import POST_WATCHLIST
from .schemas import StockInput
from .strategy import evaluate, metrics_for

MAIN = "main"
CANDIDATE = "candidates"
REVERSE = "candidates"

# t1_zt：文档声称的 T-1 状态（True=昨日涨停）。None 表示文档未明确。
DOC_SAMPLES: list[dict[str, Any]] = [
    # 2026-10-29 帖：截图是「候选池 共3只候选 · 09:26:16」，主档池当天另见澳洋健康
    {"date": "2026-10-29", "pool": CANDIDATE, "rank": 1, "code": "603855", "name": "华荣股份",
     "label": "候选", "price": 17.93, "gap": 5.35, "t1_zt": None,
     "note": "10/29 候选池 #1，竞价 +5.35%；帖中“虽然竞价的没有涨停”指这批候选", "outcome": "未封板"},
    {"date": "2026-10-29", "pool": CANDIDATE, "rank": 2, "code": "000812", "name": "陕西金叶",
     "label": "候选", "price": 3.52, "gap": 4.76, "t1_zt": None,
     "note": "10/29 候选池 #2，竞价 +4.76%", "outcome": "未封板"},
    {"date": "2026-10-29", "pool": CANDIDATE, "rank": 3, "code": "002078", "name": "太阳纸业",
     "label": "候选", "price": 13.35, "gap": 5.53, "t1_zt": None,
     "note": "10/29 候选池 #3，竞价 +5.53%", "outcome": "未封板"},
    {"date": "2026-10-29", "pool": MAIN, "rank": 1, "code": "002172", "name": "澳洋健康",
     "label": "✅ 主档", "price": 5.10, "gap": 4.51, "t1_zt": True, "t1_turnover": 22.0,
     "note": "首板涨停，末3秒由4.91抢至5.10，竞价7.36万手", "outcome": "9:30秒板（2连板）"},
    {"date": "2026-10-30", "pool": MAIN, "rank": 1, "code": "002172", "name": "澳洋健康",
     "label": "✅ 主档", "core": True, "price": 5.61, "gap": 4.47, "t1_zt": True, "t1_turnover": 18.6,
     "note": "2连板，竞价爆量近6000万", "outcome": "10:25封板（3连板）"},
    {"date": "2026-10-30", "pool": MAIN, "rank": 2, "code": "002086", "name": "东方海洋",
     "label": "主档合格·非top1", "core": True, "price": 2.70, "gap": 5.06, "t1_zt": True, "t1_turnover": None,
     "note": "首板涨停（成交7.59亿），高开5%分水岭", "outcome": "早盘封板（2连板）"},
    {"date": "2026-10-30", "pool": REVERSE, "rank": 1, "code": "600202", "name": "哈空调",
     "label": "候选/反包", "core": True, "price": 4.80, "gap": 4.12, "t1_zt": False, "t1_pct": -3.15,
     "note": "前期2连板后回调，T-1跌3.15%", "outcome": "冲涨停后炸板（+3.69%）"},
    {"date": "2026-10-30", "pool": REVERSE, "rank": 2, "code": "002009", "name": "天奇股份",
     "label": "候选/反包", "price": 17.20, "gap": None, "t1_zt": False, "t1_pct": -5.79,
     "note": "10/26触板后回调，T-1跌5.79%", "outcome": "9:37封板后炸（+3.80%）"},

    {"date": "2026-11-03", "pool": MAIN, "rank": 1, "code": "600576", "name": "祥源文旅",
     "label": "✅ 主档", "price": 5.99, "gap": 6.58, "t1_zt": True, "t1_turnover": 4.51,
     "note": "首板（3天2板），T-1缩量换手仅4.51%，竞价巨量", "outcome": "9:30秒板（4天3板）"},
    {"date": "2026-11-03", "pool": MAIN, "rank": 2, "code": "002029", "name": "七匹狼",
     "label": "主档合格·非top1", "core": True, "price": 9.78, "gap": 4.15, "t1_zt": True,
     "t1_turnover": 11.75, "note": "首板涨停（换手11.75%），竞价爆量高开", "outcome": "9:32封板（2连板）"},
    {"date": "2026-11-03", "pool": MAIN, "rank": 3, "code": "002811", "name": "郑中设计",
     "label": "主档合格·非top1", "core": True, "price": 17.83, "gap": 4.21, "t1_zt": True,
     "t1_turnover": 13.32, "note": "首板烂板（4次开板），竞价换手1.03%弱转强", "outcome": "早盘封板（2连板）"},
    {"date": "2026-11-03", "pool": MAIN, "rank": 4, "code": "603533", "name": "掌阅科技",
     "label": "主档合格·非top1", "core": True, "price": 27.97, "gap": 4.68, "t1_zt": True,
     "t1_turnover": 12.50, "note": "首板（4天2板），09:24:59由27.51抢至27.97", "outcome": "9:34封板（5天3板）"},
    {"date": "2026-11-03", "pool": MAIN, "rank": 5, "code": "000892", "name": "欢瑞世纪",
     "label": "主档合格·非top1", "core": True, "price": 4.56, "gap": 3.40, "t1_zt": True,
     "t1_turnover": 17.30, "note": "首板涨停（短剧概念），压3.3%下限入围", "outcome": "冲高回落未封板"},
    {"date": "2026-11-03", "pool": MAIN, "rank": 6, "code": "600779", "name": "水井坊",
     "label": "主档合格·非top1", "price": 29.23, "gap": 3.95, "t1_zt": True, "t1_turnover": 1.85,
     "note": "首板涨停（白酒），市值偏大、无核心紫标", "outcome": "震荡冲高未封板"},
    {"date": "2026-11-03", "pool": MAIN, "rank": 7, "code": "000525", "name": "红太阳",
     "label": "主档合格·非top1", "price": 5.30, "gap": 7.29, "t1_zt": True, "t1_turnover": 1.38,
     "note": "首板涨停，高开7.29%入风险区被扣分垫底", "outcome": "冲高回落/炸板"},
    {"date": "2026-11-03", "pool": CANDIDATE, "rank": 1, "code": "003019", "name": "宸展光电",
     "label": "候选", "price": 26.60, "gap": 4.60, "t1_zt": False,
     "note": "前期连板股T-1断板洗盘，N字反包形态", "outcome": "趋势冲高"},
    {"date": "2026-11-03", "pool": CANDIDATE, "rank": 2, "code": "603533", "name": "掌阅科技",
     "label": "候选（双池共振）", "price": 27.97, "gap": 4.68, "t1_zt": True,
     "note": "4天2板形态同时命中主档池#4与候选池#2", "outcome": "9:34封板涨停"},
    {"date": "2026-11-03", "pool": CANDIDATE, "rank": 3, "code": "001219", "name": "青岛食品",
     "label": "候选", "price": 22.30, "gap": 1.69, "t1_zt": True,
     "note": "T-1涨停但竞价仅+1.69%，未进主档3.3%门槛", "outcome": "温和震荡"},

    {"date": "2026-11-04", "pool": MAIN, "rank": 1, "code": "002088", "name": "鲁阳节能",
     "label": "主档合格·非top1", "core": True, "price": 8.82, "gap": 5.88, "t1_zt": True,
     "note": "T-1首板涨停，竞价高开5.88%但未触发全局Top1", "outcome": "早盘封板（2连板）"},
    {"date": "2026-11-04", "pool": MAIN, "rank": 2, "code": "603598", "name": "引力传媒",
     "label": "主档合格·非top1", "price": 18.50, "gap": 3.35, "t1_zt": True,
     "note": "T-1首板涨停，涨幅压线3.35%但竞价昨量比极高", "outcome": "封死涨停（2连板）"},
    {"date": "2026-11-04", "pool": MAIN, "rank": 3, "code": "601599", "name": "浙文影业",
     "label": "主档合格·非top1", "price": 3.99, "gap": 4.45, "t1_zt": True,
     "note": "T-1首板涨停，低价传媒股竞价放量弱转强", "outcome": "封死涨停（2连板）"},
    {"date": "2026-11-04", "pool": MAIN, "rank": 4, "code": "003032", "name": "传智教育",
     "label": "主档合格·非top1", "core": True, "price": 9.41, "gap": 4.67, "t1_zt": True,
     "note": "T-1首板涨停，竞价量比弱于引力传媒/浙文影业", "outcome": "冲高回落未封板"},
    {"date": "2026-11-04", "pool": MAIN, "rank": 5, "code": "002626", "name": "金达威",
     "label": "主档合格·非top1", "price": 17.80, "gap": 4.03, "t1_zt": True,
     "note": "T-1首板涨停，非主线核心、竞价评分排第5", "outcome": "冲高回落未封板"},

    # 2026-11-06 帖：竞价选出东方海洋（候选池），城市传媒开盘秒板
    {"date": "2026-11-06", "pool": MAIN, "rank": 1, "code": "605188", "name": "国光连锁",
     "label": "主档合格·非top1", "price": 12.94, "gap": 4.19, "t1_zt": None,
     "note": "11/06 主档池 #1，竞价 +4.19%", "outcome": ""},
    {"date": "2026-11-06", "pool": MAIN, "rank": 2, "code": "603598", "name": "引力传媒",
     "label": "主档合格·非top1", "core": True, "price": 22.45, "gap": 3.65, "t1_zt": None,
     "note": "11/06 主档池 #2，竞价 +3.65%；周三二板，帖中称浙文为龙头", "outcome": ""},
    {"date": "2026-11-06", "pool": MAIN, "rank": 3, "code": "600229", "name": "城市传媒",
     "label": "主档合格·非top1", "core": True, "price": 6.03, "gap": 3.25, "t1_zt": None,
     "note": "11/06 主档池 #3，竞价 +3.25%，开盘秒上板", "outcome": "开盘秒板"},
    {"date": "2026-11-06", "pool": CANDIDATE, "rank": 1, "code": "002086", "name": "东方海洋",
     "label": "候选", "price": 3.67, "gap": 1.66, "t1_zt": None,
     "note": "11/06 候选池 #1（09:27:23），帖中称“跨年妖股”", "outcome": ""},
    {"date": "2026-11-06", "pool": CANDIDATE, "rank": 2, "code": "002242", "name": "九阳股份",
     "label": "候选", "price": 24.80, "gap": 1.76, "t1_zt": None,
     "note": "11/06 候选池 #2，竞价 +1.76%", "outcome": ""},
    {"date": "2026-11-06", "pool": CANDIDATE, "rank": 3, "code": "002275", "name": "桂林三金",
     "label": "候选", "price": 17.92, "gap": 2.05, "t1_zt": None,
     "note": "11/06 候选池 #3，竞价 +2.05%", "outcome": ""},

    # 2026-11-05 帖：五个板三个，金陵药业炸板
    {"date": "2026-11-05", "pool": MAIN, "rank": 1, "code": "000919", "name": "金陵药业",
     "label": "主档合格·非top1", "price": 7.01, "gap": 5.10, "t1_zt": True, "t1_turnover": 6.64,
     "note": "T-1 涨停，竞价 +5.10%，当日炸板", "outcome": "炸板未封"},
    {"date": "2026-11-05", "pool": MAIN, "rank": 2, "code": "603657", "name": "春光科技",
     "label": "主档合格·非top1", "core": True, "price": 29.50, "gap": 5.36, "t1_zt": True,
     "t1_turnover": 6.28, "note": "T-1 涨停，⭐核心", "outcome": "封板"},
    {"date": "2026-11-05", "pool": MAIN, "rank": 3, "code": "002088", "name": "鲁阳节能",
     "label": "主档合格·非top1", "core": True, "price": 9.80, "gap": 6.99, "t1_zt": True,
     "t1_turnover": 6.68, "note": "T-1 涨停（2板），⭐核心，早上洗盘后再冲板", "outcome": "封板"},
    {"date": "2026-11-05", "pool": MAIN, "rank": 4, "code": "601069", "name": "西部黄金",
     "label": "主档合格·非top1", "price": 24.30, "gap": 3.76, "t1_zt": True, "t1_turnover": 3.95,
     "note": "T-1 涨停，竞价 +3.76%，流通市值约 208 亿", "outcome": "未封板"},
    {"date": "2026-11-05", "pool": MAIN, "rank": 5, "code": "001360", "name": "南矿集团",
     "label": "主档合格·非top1", "core": True, "price": 18.52, "gap": 7.36, "t1_zt": True,
     "t1_turnover": 9.72, "note": "T-1 涨停，⭐核心，竞价 +7.36%", "outcome": "封板"},

    # 2026-11-11 晚帖：竞价主档 9 只，全部「主档合格·非top1」；日海/凯撒/传智/神开当日封板
    {"date": "2026-11-11", "pool": MAIN, "rank": 1, "code": "002313", "name": "日海智能",
     "label": "主档合格·非top1", "core": True, "price": 9.79, "gap": 5.95, "t1_zt": True,
     "t1_turnover": 10.11, "note": "11/11 晚帖主档 #1，当日封板", "outcome": "涨停（+9.96%）"},
    {"date": "2026-11-11", "pool": MAIN, "rank": 2, "code": "002425", "name": "凯撒文化",
     "label": "主档合格·非top1", "core": True, "price": 3.71, "gap": 6.61, "t1_zt": True,
     "t1_turnover": 4.70, "note": "11/11 晚帖主档 #2，当日封板", "outcome": "涨停（+10.06%）"},
    {"date": "2026-11-11", "pool": MAIN, "rank": 3, "code": "600609", "name": "金杯汽车",
     "label": "主档合格·非top1", "price": 3.97, "gap": 3.66, "t1_zt": True,
     "t1_turnover": 7.88, "note": "11/11 晚帖主档 #3", "outcome": "+4.18%"},
    {"date": "2026-11-11", "pool": MAIN, "rank": 4, "code": "002009", "name": "天奇股份",
     "label": "主档合格·非top1", "core": True, "price": 20.16, "gap": 4.78, "t1_zt": True,
     "t1_turnover": 9.78, "note": "11/11 晚帖主档 #4", "outcome": "+3.48%"},
    {"date": "2026-11-11", "pool": MAIN, "rank": 5, "code": "002376", "name": "新北洋",
     "label": "主档合格·非top1", "core": True, "price": 8.16, "gap": 5.56, "t1_zt": True,
     "t1_turnover": 10.12, "note": "11/11 晚帖主档 #5", "outcome": "−0.13%"},
    {"date": "2026-11-11", "pool": MAIN, "rank": 6, "code": "002347", "name": "泰尔股份",
     "label": "主档合格·非top1", "core": True, "price": 8.24, "gap": 7.01, "t1_zt": True,
     "t1_turnover": 8.49, "note": "11/11 晚帖主档 #6", "outcome": "−4.42%"},
    {"date": "2026-11-11", "pool": MAIN, "rank": 7, "code": "002631", "name": "德尔未来",
     "label": "主档合格·非top1", "price": 10.06, "gap": 3.29, "t1_zt": True,
     "t1_turnover": 3.33, "note": "11/11 晚帖主档 #7", "outcome": "+5.13%"},
    {"date": "2026-11-11", "pool": MAIN, "rank": 8, "code": "003032", "name": "传智教育",
     "label": "主档合格·非top1", "core": True, "price": 10.45, "gap": 3.47, "t1_zt": False,
     "t1_turnover": 16.15, "note": "11/11 晚帖主档 #8，当日封板", "outcome": "涨停（+10.00%）"},
    {"date": "2026-11-11", "pool": MAIN, "rank": 9, "code": "002278", "name": "神开股份",
     "label": "主档合格·非top1", "price": 7.50, "gap": 3.45, "t1_zt": False,
     "t1_turnover": 1.43, "note": "11/11 晚帖主档 #9；近 12 日无收盘涨停，已加入 post_watchlist 强制入池", "outcome": "涨停（+10.07%）"},
]

POOL_NAMES = {MAIN: "主档池", CANDIDATE: "候选池"}


def _gap(metrics: dict[str, Any]) -> float:
    return round(metrics["auction_pct"], 2)


def check_sample(sample: dict[str, Any], dataset_id: int, result: dict[str, Any] | None) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "date": sample["date"],
        "pool": sample["pool"],
        "pool_name": POOL_NAMES[sample["pool"]],
        "rank": sample["rank"],
        "code": sample["code"],
        "name": sample["name"],
        "doc_label": sample["label"],
        "doc_core": bool(sample.get("core")),
        "doc_price": sample.get("price"),
        "doc_gap": sample.get("gap"),
        "note": sample.get("note", ""),
        "outcome": sample.get("outcome", ""),
        "issues": [],
        "model": None,
        "data": None,
        "status": "no_dataset",
    }
    if result is None:
        entry["issues"].append("该交易日尚未同步真实行情，无法核对")
        return entry
    stock = next((item for item in result["_inputs"] if item.code == sample["code"]), None)
    if stock is None:
        entry["status"] = "watch_missing" if sample["code"] in POST_WATCHLIST else "missing"
        entry["issues"].append(
            "帖子观察池已请求补抓，但本次数据集仍无该票行情/竞价"
            if sample["code"] in POST_WATCHLIST
            else "该标的未进入涨停基因样本池（近 12 日无涨停记录）"
        )
        return entry

    metrics = metrics_for(stock)
    payload = stock.model_dump()
    entry["data"] = {
        "gap": _gap(metrics),
        "vol_ratio": round(metrics["vol_ratio"], 2),
        "auction_turnover": round(metrics["auction_turnover"], 3),
        "auction_amount": payload["auction_amount"],
        "is_zt": payload["is_zt"],
        "zt_count": payload["zt_count"],
        "zt_count_8": payload["zt_count_8"],
        "yest_turnover": payload["yest_turnover"],
        "yest_pct": payload["yest_pct"],
        "free_market_cap": round(payload["free_market_cap"], 1),
    }

    in_main = next((item for item in result["main"] if item["code"] == sample["code"]), None)
    in_cand = next((item for item in result["candidates"] if item["code"] == sample["code"]), None)
    if in_main:
        entry["model"] = {"pool": MAIN, "pool_name": "主档池", "rank": in_main["rank"],
                          "label": in_main["label"], "is_core": in_main["is_core"],
                          "score": in_main["score"]["total"]}
    elif in_cand:
        entry["model"] = {"pool": CANDIDATE, "pool_name": "候选池", "rank": in_cand["rank"],
                          "label": in_cand["label"], "is_core": in_cand["is_core"],
                          "score": in_cand["candidate_score"]}

    # 先核对文档自身的行情描述，再对比模型输出。
    if sample.get("t1_zt") is True and not payload["is_zt"]:
        entry["issues"].append(
            f"文档称 T-1 涨停，真实 T-1 涨跌幅 {payload['yest_pct']:+.2f}% 且不在昨日涨停池"
        )
    if sample.get("t1_zt") is False and payload["is_zt"]:
        entry["issues"].append("文档称 T-1 断板，真实 T-1 为涨停")
    claim_turnover = sample.get("t1_turnover")
    if claim_turnover is not None and abs(claim_turnover - payload["yest_turnover"]) > 0.4:
        entry["issues"].append(
            f"文档称 T-1 换手 {claim_turnover:.2f}%，真实 {payload['yest_turnover']:.2f}%"
        )
    if sample.get("gap") is not None and abs(sample["gap"] - metrics["auction_pct"]) > 0.15:
        entry["issues"].append(
            f"文档称竞价 {sample['gap']:+.2f}%，真实 {metrics['auction_pct']:+.2f}%"
        )

    data_conflict = any("文档称" in issue for issue in entry["issues"])
    # 只有当模型也没选出来时，T-1 描述冲突才作为主状态；否则按模型命中情况判定
    if data_conflict and entry["model"] is None:
        entry["status"] = "data_conflict"
    elif entry["model"] is None:
        reasons = next((item["reasons"] for item in result["excluded"] if item["code"] == sample["code"]), [])
        if stock.post_watch:
            entry["status"] = "watch"
            entry["issues"].append("帖子观察池已补入，但硬过滤未通过：" + "、".join(reason["rule"] for reason in reasons))
            return entry
        overflow = [reason for reason in reasons if reason.get("rule") in ("主档名额", "候选名额")]
        if overflow and len(overflow) == len(reasons):
            entry["status"] = "overflow"
            entry["issues"].append("通过硬过滤，但" + "；".join(reason["message"] for reason in overflow))
        else:
            entry["status"] = "not_selected"
            entry["issues"].append("模型未纳入任何池：" + "、".join(reason["rule"] for reason in reasons))
    elif entry["model"]["pool"] != sample["pool"]:
        entry["status"] = "pool_mismatch"
        entry["issues"].append(
            f"文档在{entry['pool_name']}，模型在{entry['model']['pool_name']}"
        )
    elif entry["model"]["rank"] != sample["rank"]:
        entry["status"] = "rank_mismatch"
        entry["issues"].append(f"文档 #{sample['rank']}，模型 #{entry['model']['rank']}")
    elif bool(sample.get("core")) != bool(entry["model"]["is_core"]):
        entry["status"] = "label_mismatch"
        entry["issues"].append("⭐核心 标记与文档不一致")
    else:
        entry["status"] = "match"
    return entry


def core_flags_for(day: str) -> dict[str, bool]:
    """作者当天截图里的 ⭐核心 标记：{代码: bool}。只看主档池，同一代码出现两次时取 True。"""
    flags: dict[str, bool] = {}
    for sample in DOC_SAMPLES:
        if sample["date"] != day or sample["pool"] != MAIN:
            continue
        flags[sample["code"]] = flags.get(sample["code"], False) or bool(sample.get("core"))
    return flags


def doc_ranks_for(day: str) -> dict[str, int]:
    """作者当天主档名单：{代码: 名次}。"""
    return {
        sample["code"]: sample["rank"]
        for sample in DOC_SAMPLES
        if sample["date"] == day and sample["pool"] == MAIN
    }


def build_report(datasets: list[dict[str, Any]], config) -> dict[str, Any]:
    by_date: dict[str, dict[str, Any]] = {}
    for dataset in datasets:
        current = by_date.get(dataset["trading_day"])
        if current is None or dataset.get("stock_count", 0) > current.get("stock_count", 0):
            by_date[dataset["trading_day"]] = dataset

    cache: dict[str, dict[str, Any]] = {}
    entries: list[dict[str, Any]] = []
    for sample in DOC_SAMPLES:
        day = sample["date"]
        dataset = by_date.get(day)
        result = None
        if dataset:
            if day not in cache:
                stocks = [StockInput(**payload) for payload in db.get_stocks(dataset["id"])]
                cache[day] = evaluate(
                    stocks, dataset, config, doc_ranks_for(day), excluded_for(day), core_flags_for(day)
                ) | {"_inputs": stocks}
            result = cache[day]
        entries.append(check_sample(sample, dataset["id"] if dataset else 0, result))

    counts: dict[str, int] = {}
    for entry in entries:
        counts[entry["status"]] = counts.get(entry["status"], 0) + 1
    verified = [entry for entry in entries if entry["status"] != "no_dataset"]
    matched = sum(entry["status"] == "match" for entry in verified)
    return {
        "summary": {
            "total": len(entries),
            "verified": len(verified),
            "matched": matched,
            "match_rate": round(matched / len(verified) * 100, 1) if verified else None,
            "counts": counts,
            "dates": sorted({entry["date"] for entry in entries}),
            "verified_dates": sorted({entry["date"] for entry in verified}),
        },
        "samples": entries,
    }
