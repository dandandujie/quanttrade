from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StockInput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    code: str
    name: str
    free_market_cap: float = Field(gt=0, description="流通市值（按昨日换手率推算），亿元")
    prev_close: float = Field(gt=0)
    open_price: float = Field(gt=0)
    yest_vol: float = Field(gt=0, description="昨日成交量，股")
    auction_vol: float = Field(ge=0, description="竞价成交量，股")
    auction_amount: float = Field(ge=0, description="竞价成交额，元")
    free_float_shares: float = Field(gt=0)
    is_zt: bool = False
    zt_count: int = Field(default=0, ge=0)
    zt_count_4: int = Field(default=0, ge=0)
    zt_count_8: int = Field(default=0, ge=0)
    zt_count_12: int = Field(default=0, ge=0)
    yest_turnover: float = Field(default=0, ge=0)
    yest_high: float = Field(default=0, ge=0)
    yest_low: float = Field(default=0, ge=0)
    yest_pct: float = 0
    ma10: float = Field(default=0, ge=0)
    match_price_2445: float = Field(default=0, ge=0)
    bid1_unmatched_vol: float = Field(default=0, ge=0)
    micro_structure_available: bool = Field(
        default=False, description="是否拿到 09:24:45 快照与买一未匹配量"
    )
    market_zt_count: int = Field(default=0, ge=0, description="全市场昨日涨停家数")
    seal_amount: float = Field(default=0, ge=0, description="昨日封板资金，元")
    break_count: int = Field(default=0, ge=0, description="昨日炸板次数")
    concept_limit_ups: int = Field(default=0, ge=0)
    theme_name: str = ""
    theme_limit_ups: int = Field(default=0, ge=0, description="该股最热题材昨日涨停家数")
    theme_density: float = Field(default=0, ge=0, description="该题材涨停密度（%）")
    lhb_net_inflow: float = 0
    main_force_inflow_5d: float = 0
    is_core: Optional[bool] = None
    industry: str = ""
    auction_date: str = ""
    auction_time: str = ""
    auction_source: str = ""
    post_watch: bool = False
    data_quality: list[str] = Field(default_factory=list)

    @field_validator("code")
    @classmethod
    def normalize_code(cls, value: str) -> str:
        value = value.strip().lower().replace("sh", "").replace("sz", "")
        if len(value) != 6 or not value.isdigit():
            raise ValueError("股票代码应为6位数字，可带sh/sz前缀")
        return value

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("股票名称不能为空")
        return value


class StrategyConfig(BaseModel):
    free_mcap_min: float = 15.0
    free_mcap_max: float = 250.0
    yest_turnover_min: float = 1.2
    yest_turnover_max: float = 35.0
    main_open_min: float = 3.0
    main_open_max: float = 7.5
    sweet_open_min: float = 4.1
    sweet_open_max: float = 6.6
    sweet_open_anchor: float = 5.2
    main_min_vol_ratio: float = 0.5
    main_min_auction_turnover: float = 0.02
    main_min_auction_amount: float = 2_000_000
    rv_band_lo: float = 7.5
    rv_band_hi: float = 30.0
    turn_band_lo: float = 8.0
    turn_band_hi: float = 20.0
    mcap_band_lo: float = 35.0
    mcap_band_hi: float = 75.0
    w_gap: float = 1.375
    w_rv: float = 17.23
    w_turn: float = 26.04
    w_mcap: float = 34.65
    w_seal: float = 0.1
    micro_max: float = 20.0
    slippage_pct: float = 0.3
    fee_pct: float = 0.026
    stamp_duty_pct: float = 0.05
    green_min_score: float = 65.0
    green_min_open_pct: float = 6.0
    green_min_vol_ratio: float = 10.0
    green_min_zt_count: int = 80
    green_require_global_top1: bool = False
    main_pool_size: int = 8
    candidate_pool_size: int = 12
    preferred_scan: int = 12
    preferred_min_gene: int = 2
    preferred_min_mcap: float = 40.0
    preferred_min_auction_amount: float = 10_000_000
    candidate_open_min: float = 1.5
    candidate_open_max: float = 6.5
    candidate_b2_open_max: float = 6.0
    candidate_min_vol_ratio: float = 1.2
    candidate_min_auction_amount: float = 5_000_000
    core_from_samples: bool = False
    core_seal_strength: float = 2.0
    core_lhb_net_inflow: float = 20_000_000
    fund_flow_scale: float = 500_000_000
    risk_avg_open_pct: float = 0.5
    risk_limit_down_count: int = 3


class ScreenRequest(BaseModel):
    dataset_id: int
    config: StrategyConfig = Field(default_factory=StrategyConfig)


class SyncRequest(BaseModel):
    trading_day: Optional[str] = None
    limit: Optional[int] = Field(default=None, ge=1, le=2000)


class PerformanceRequest(BaseModel):
    run_id: Optional[int] = None
