import csv
import io
from typing import Any

from pydantic import ValidationError

from .schemas import StockInput

ALIASES = {
    "股票代码": "code", "代码": "code", "股票名称": "name", "名称": "name",
    "自由流通市值_亿": "free_market_cap", "流通市值_亿": "free_market_cap",
    "昨收价": "prev_close", "开盘价": "open_price",
    "昨日成交量_股": "yest_vol", "竞价成交量_股": "auction_vol", "竞价成交额_元": "auction_amount",
    "自由流通股本_股": "free_float_shares", "流通股本_股": "free_float_shares",
    "昨日是否涨停": "is_zt", "昨日连板数": "zt_count",
    "近4日涨停数": "zt_count_4", "近8日涨停数": "zt_count_8", "近12日涨停数": "zt_count_12",
    "昨日换手率_pct": "yest_turnover",
    "昨日最高价": "yest_high", "昨日最低价": "yest_low", "昨日涨跌幅_pct": "yest_pct",
    "10日均线": "ma10", "竞价092445价格": "match_price_2445", "买一未匹配量_股": "bid1_unmatched_vol",
    "概念昨日涨停家数": "concept_limit_ups", "龙虎榜净买入_元": "lhb_net_inflow",
    "近5日主力净流入_元": "main_force_inflow_5d", "是否核心": "is_core",
    "全市场昨日涨停家数": "market_zt_count",
    "题材昨涨停家数": "theme_limit_ups", "题材密度_pct": "theme_density", "题材名称": "theme_name",
}

TEMPLATE_FIELDS = [
    "code", "name", "free_market_cap", "prev_close", "open_price", "yest_vol", "auction_vol",
    "auction_amount", "free_float_shares", "is_zt", "zt_count", "zt_count_4", "zt_count_8",
    "zt_count_12", "yest_turnover", "yest_high", "yest_low", "yest_pct", "ma10",
    "match_price_2445", "bid1_unmatched_vol", "concept_limit_ups", "lhb_net_inflow",
    "main_force_inflow_5d", "is_core", "market_zt_count", "theme_limit_ups", "theme_density",
    "theme_name",
]


def parse_bool(value: Any) -> Any:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in ("1", "true", "yes", "y", "是", "涨停", "核心"):
        return True
    if normalized in ("0", "false", "no", "n", "否", "非涨停", "非核心"):
        return False
    raise ValueError(f"无法识别布尔值：{value}")


def parse_csv(content: bytes) -> list[StockInput]:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("CSV文件须使用UTF-8编码") from exc
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ValueError("CSV文件为空或缺少表头")
    rows = []
    errors = []
    for line_number, raw in enumerate(reader, start=2):
        try:
            row = {ALIASES.get(key.strip(), key.strip()): value.strip() if isinstance(value, str) else value
                   for key, value in raw.items() if key}
            for field in ("is_zt", "is_core"):
                if field in row:
                    row[field] = parse_bool(row[field])
            if row.get("is_zt") is None:
                row["is_zt"] = False
            if row.get("is_core") is None:
                row.pop("is_core", None)
            rows.append(StockInput(**row))
        except (ValidationError, ValueError) as exc:
            message = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()) if isinstance(exc, ValidationError) else str(exc)
            errors.append(f"第{line_number}行：{message}")
    if errors:
        raise ValueError("\n".join(errors[:12]))
    if not rows:
        raise ValueError("CSV中没有可导入的数据行")
    codes = [stock.code for stock in rows]
    if len(codes) != len(set(codes)):
        raise ValueError("同一数据集中股票代码不能重复")
    return rows


def template_csv() -> bytes:
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=TEMPLATE_FIELDS)
    writer.writeheader()
    writer.writerow({
        "code": "600000", "name": "示例股票", "free_market_cap": 50, "prev_close": 10,
        "open_price": 10.5, "yest_vol": 50000000, "auction_vol": 6000000,
        "auction_amount": 63000000, "free_float_shares": 500000000, "is_zt": "true",
        "zt_count": 1, "zt_count_4": 1, "zt_count_8": 1, "zt_count_12": 1,
        "yest_turnover": 10, "yest_high": 10, "yest_low": 9.2, "yest_pct": 10, "ma10": 9.5,
        "match_price_2445": 10.4, "bid1_unmatched_vol": 1000000, "concept_limit_ups": 3,
        "lhb_net_inflow": 0, "main_force_inflow_5d": 10000000, "is_core": "",
        "market_zt_count": 80, "theme_limit_ups": 3, "theme_density": 5, "theme_name": "示例题材",
    })
    return ("﻿" + output.getvalue()).encode("utf-8")
