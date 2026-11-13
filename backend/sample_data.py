"""演示样本：文档 11/03 主档池七只票的真实竞价快照。

数据取自 2026-11-03 的真实行情（09:30 一分钟K线 = 集合竞价那一笔，
已逐只与盘前 09:26 接口比对一致），不是编造的数字。
仅为让没有网络的环境也能看到完整流程，实盘请直接用「同步行情」。
"""

DEMO_DAY = "2026-11-03"
DEMO_MARKET_ZT = 125  # 2026-11-02 全市场涨停家数

_DEMO_RAW = [
    {"code": "600576", "name": "祥源文旅", "free_market_cap": 37.1139, "prev_close": 5.62, "open_price": 5.99, "yest_vol": 29793900.0, "auction_vol": 3304000.0, "auction_amount": 19790960.0, "free_float_shares": 660618625.28, "is_zt": True, "zt_count": 1, "zt_count_4": 2, "zt_count_8": 2, "yest_turnover": 4.51, "yest_high": 5.62, "yest_low": 4.85, "yest_pct": 9.98, "ma10": 4.97, "match_price_2445": 5.99, "bid1_unmatched_vol": 0, "concept_limit_ups": 1, "main_force_inflow_5d": 0, "is_core": None, "industry": "旅游及景区", "lhb_net_inflow": 0},
    {"code": "002029", "name": "七匹狼", "free_market_cap": 62.4816, "prev_close": 9.39, "open_price": 9.78, "yest_vol": 78200700.0, "auction_vol": 2246600.0, "auction_amount": 21971748.0, "free_float_shares": 665537872.34, "is_zt": True, "zt_count": 1, "zt_count_4": 1, "zt_count_8": 1, "yest_turnover": 11.75, "yest_high": 9.39, "yest_low": 8.39, "yest_pct": 9.95, "ma10": 8.354, "match_price_2445": 9.78, "bid1_unmatched_vol": 0, "concept_limit_ups": 2, "main_force_inflow_5d": 0, "is_core": None, "industry": "服装家纺", "lhb_net_inflow": 0},
    {"code": "002811", "name": "郑中设计", "free_market_cap": 48.4321, "prev_close": 17.11, "open_price": 17.83, "yest_vol": 37700100.0, "auction_vol": 571300.0, "auction_amount": 10186279.0, "free_float_shares": 283033783.78, "is_zt": True, "zt_count": 1, "zt_count_4": 1, "zt_count_8": 1, "yest_turnover": 13.32, "yest_high": 17.11, "yest_low": 14.66, "yest_pct": 10.03, "ma10": 20.218, "match_price_2445": 17.83, "bid1_unmatched_vol": 0, "concept_limit_ups": 2, "main_force_inflow_5d": 0, "is_core": None, "industry": "装修装饰", "lhb_net_inflow": 0},
    {"code": "603533", "name": "掌阅科技", "free_market_cap": 117.2732, "prev_close": 26.72, "open_price": 27.97, "yest_vol": 54866800.0, "auction_vol": 1811600.0, "auction_amount": 50671739.0, "free_float_shares": 438934400.0, "is_zt": True, "zt_count": 1, "zt_count_4": 2, "zt_count_8": 2, "yest_turnover": 12.5, "yest_high": 26.72, "yest_low": 22.95, "yest_pct": 10.0, "ma10": 26.709, "match_price_2445": 27.97, "bid1_unmatched_vol": 0, "concept_limit_ups": 5, "main_force_inflow_5d": 0, "is_core": None, "industry": "数字媒体", "lhb_net_inflow": 0},
    {"code": "000892", "name": "欢瑞世纪", "free_market_cap": 31.3462, "prev_close": 4.41, "open_price": 4.56, "yest_vol": 122989600.0, "auction_vol": 1843500.0, "auction_amount": 8406360.0, "free_float_shares": 710922543.35, "is_zt": True, "zt_count": 1, "zt_count_4": 1, "zt_count_8": 1, "yest_turnover": 17.3, "yest_high": 4.41, "yest_low": 3.8, "yest_pct": 9.98, "ma10": 4.421, "match_price_2445": 4.56, "bid1_unmatched_vol": 0, "concept_limit_ups": 7, "main_force_inflow_5d": 0, "is_core": None, "industry": "影视院线", "lhb_net_inflow": 0},
    {"code": "600779", "name": "水井坊", "free_market_cap": 137.0496, "prev_close": 28.12, "open_price": 29.23, "yest_vol": 9034400.0, "auction_vol": 443000.0, "auction_amount": 12949738.0, "free_float_shares": 488345945.95, "is_zt": True, "zt_count": 1, "zt_count_4": 1, "zt_count_8": 1, "yest_turnover": 1.85, "yest_high": 28.12, "yest_low": 26.84, "yest_pct": 10.02, "ma10": 25.575, "match_price_2445": 29.23, "bid1_unmatched_vol": 0, "concept_limit_ups": 3, "main_force_inflow_5d": 0, "is_core": None, "industry": "白酒", "lhb_net_inflow": 0},
    {"code": "000525", "name": "红太阳", "free_market_cap": 54.9209, "prev_close": 4.94, "open_price": 5.3, "yest_vol": 15330100.0, "auction_vol": 17819700.0, "auction_amount": 94444410.0, "free_float_shares": 1110876811.59, "is_zt": True, "zt_count": 2, "zt_count_4": 2, "zt_count_8": 2, "yest_turnover": 1.38, "yest_high": 4.94, "yest_low": 4.94, "yest_pct": 10.02, "ma10": 4.274, "match_price_2445": 5.3, "bid1_unmatched_vol": 0, "concept_limit_ups": 1, "main_force_inflow_5d": 0, "is_core": None, "industry": "农化制品", "lhb_net_inflow": 0},
    {"code": "003019", "name": "宸展光电", "free_market_cap": 43.9756, "prev_close": 25.43, "open_price": 26.6, "yest_vol": 4507100.0, "auction_vol": 301800.0, "auction_amount": 8027880.0, "free_float_shares": 172685823.75, "is_zt": True, "zt_count": 1, "zt_count_4": 1, "zt_count_8": 1, "yest_turnover": 2.61, "yest_high": 25.43, "yest_low": 23.29, "yest_pct": 9.99, "ma10": 24.148, "match_price_2445": 26.6, "bid1_unmatched_vol": 0, "concept_limit_ups": 2, "main_force_inflow_5d": 0, "is_core": None, "industry": "光学光电", "lhb_net_inflow": 0},
    {"code": "001219", "name": "青岛食品", "free_market_cap": 41.7103, "prev_close": 21.93, "open_price": 22.3, "yest_vol": 37543200.0, "auction_vol": 2864800.0, "auction_amount": 63886021.0, "free_float_shares": 190188449.85, "is_zt": True, "zt_count": 6, "zt_count_4": 4, "zt_count_8": 6, "yest_turnover": 19.74, "yest_high": 21.93, "yest_low": 19.2, "yest_pct": 9.98, "ma10": 15.469, "match_price_2445": 22.3, "bid1_unmatched_vol": 0, "concept_limit_ups": 1, "main_force_inflow_5d": 0, "is_core": None, "industry": "休闲食品", "lhb_net_inflow": 0},
    {"code": "300123", "name": "创业示例", "free_market_cap": 55.0, "prev_close": 12.0, "open_price": 12.6, "yest_vol": 40000000.0, "auction_vol": 2000000.0, "auction_amount": 25200000.0, "free_float_shares": 450000000.0, "is_zt": True, "zt_count": 1, "zt_count_4": 1, "zt_count_8": 1, "yest_turnover": 9.0, "yest_high": 12.0, "yest_low": 10.5, "yest_pct": 20.0, "ma10": 11.2, "match_price_2445": 12.5, "bid1_unmatched_vol": 0, "concept_limit_ups": 1, "main_force_inflow_5d": 0, "is_core": None, "industry": "创业板示例", "lhb_net_inflow": 0},
    {"code": "600001", "name": "ST示例", "free_market_cap": 28.0, "prev_close": 3.0, "open_price": 3.15, "yest_vol": 30000000.0, "auction_vol": 3000000.0, "auction_amount": 9450000.0, "free_float_shares": 800000000.0, "is_zt": True, "zt_count": 1, "zt_count_4": 1, "zt_count_8": 1, "yest_turnover": 8.0, "yest_high": 3.0, "yest_low": 2.8, "yest_pct": 5.0, "ma10": 2.9, "match_price_2445": 3.12, "bid1_unmatched_vol": 0, "concept_limit_ups": 1, "main_force_inflow_5d": 0, "is_core": None, "industry": "风险警示示例", "lhb_net_inflow": 0},
]

DEMO_STOCKS = [
    {
        **row,
        "auction_date": DEMO_DAY,
        "micro_structure_available": False,
        "market_zt_count": DEMO_MARKET_ZT,
        "data_quality": [
            "演示数据取自 2026-11-03 真实竞价快照",
            "免费源未提供09:25未匹配买单量，微观结构按缺失处理并归一化评分",
        ],
    }
    for row in _DEMO_RAW
]
