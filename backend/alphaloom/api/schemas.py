# backend/alphaloom/api/schemas.py
from __future__ import annotations
from pydantic import BaseModel, Field

# 评估窗口/规模上界（对齐 RunIn 的 int64 溢出防护 + evolve 规模硬锁定）
_TS_MAX = 4_102_444_800_000   # ≤2100 年，防 int64 溢出穿到 sqlite

class CompileIn(BaseModel):
    blueprint: dict
    bar: str = "1m"

class SaveBlueprintIn(BaseModel):
    blueprint: dict

class RunIn(BaseModel):
    blueprint: dict
    inst: str
    bar: str = "1m"
    start_ms: int | None = Field(default=None, ge=0, le=4_102_444_800_000)   # ≤2100 年，防 int64 溢出穿到 sqlite
    end_ms: int | None = Field(default=None, ge=0, le=4_102_444_800_000)
    cash: float = 10_000.0
    fee_rate: float = 0.0005
    breakpoints: list[str] = Field(default_factory=list)
    playback_ms: int = 15
    ws_wait_ms: int = 0
    # D3：backtest（默认）| replay（走真实 LLM/录制，加速由 playback_ms 控制）。
    # 两种模式都绑注入的 LLM 客户端——D3 replay 语义先等同 backtest 但确保 LLM 节点能跑。
    mode: str = "backtest"


class CopilotBlueprintIn(BaseModel):
    nl: str


class CopilotExplainIn(BaseModel):
    blueprint: dict


class CopilotOptimizeIn(BaseModel):
    blueprint: dict
    report: dict = Field(default_factory=dict)


class CustomNodeIn(BaseModel):
    source: str


# --------------------------------------------------------------------------- #
# 评估 / 进化端点请求模型（D4-T6）
# --------------------------------------------------------------------------- #
class EvalFidelityIn(BaseModel):
    """保真度阶梯：从一个已完成 run 取 fills+candles 重放（零 LLM）。"""
    run_id: str
    initial_cash: float = 10_000.0
    fee_rate: float = 0.0005
    slippage_bps: float = 5.0


class EvalLeaderboardIn(BaseModel):
    """基线排行榜：三基线 + 可选指定蓝图，同窗对比。"""
    inst: str
    bar: str = "1m"
    start_ms: int | None = Field(default=None, ge=0, le=_TS_MAX)
    end_ms: int | None = Field(default=None, ge=0, le=_TS_MAX)
    valid_start_ms: int | None = Field(default=None, ge=0, le=_TS_MAX)
    valid_end_ms: int | None = Field(default=None, ge=0, le=_TS_MAX)
    blueprint: dict | None = None      # 可选：额外把这张蓝图放上榜（须零 LLM 或 offline）
    blueprint_name: str = "blueprint"
    initial_cash: float = 10_000.0
    fee_rate: float = 0.0005


class EvalAblationIn(BaseModel):
    """委员会消融：三臂图手术，量化护栏价值（LLM 蓝图须 offline 回放）。"""
    blueprint: dict
    inst: str
    bar: str = "1m"
    start_ms: int | None = Field(default=None, ge=0, le=_TS_MAX)
    end_ms: int | None = Field(default=None, ge=0, le=_TS_MAX)
    initial_cash: float = 10_000.0
    fee_rate: float = 0.0005


class EvalScorecardIn(BaseModel):
    """蓝图记分卡：前端把已算好的证据碎片拼成权威综合分（评分实现只在后端一份）。

    train_report/valid_report 可为 BacktestReport 形状 {summary, certificate,...} 或
    裸 summary dict；ladder/ablation 为对应 to_dict()；cost_cert 显式给或从
    train_report.certificate 自动取。
    """
    train_report: dict
    valid_report: dict | None = None
    ladder: dict | None = None
    cost_cert: dict | None = None
    ablation: dict | None = None


class EvolveIn(BaseModel):
    """进化实验室：LLM 变异算子 + 编译守门 + 谱系树（规模硬锁定）。"""
    blueprint: dict
    inst: str
    bar: str = "1m"
    train_start_ms: int | None = Field(default=None, ge=0, le=_TS_MAX)
    train_end_ms: int | None = Field(default=None, ge=0, le=_TS_MAX)
    valid_start_ms: int | None = Field(default=None, ge=0, le=_TS_MAX)
    valid_end_ms: int | None = Field(default=None, ge=0, le=_TS_MAX)
    # 规模硬锁定（与 evolve.lab 的 MAX_POPULATION/MAX_GENERATIONS 一致）——pydantic
    # 层先挡明显超限（422），evolve 内部再兜一层 ValueError（转 422）。
    population: int = Field(default=4, ge=1, le=4)
    generations: int = Field(default=3, ge=1, le=3)
    param_only: bool = False
    initial_cash: float = 10_000.0
    fee_rate: float = 0.0005
