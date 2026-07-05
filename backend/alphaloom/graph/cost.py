from __future__ import annotations
from dataclasses import dataclass, asdict
from alphaloom.nodes.registry import NodeDef

_LATENCY_RANK = {"fast": 0, "slow": 1, "llm": 2}

@dataclass(frozen=True)
class CostCertificate:
    llm_calls_per_bar: int
    daily_token_ceiling: int
    worst_latency_class: str
    deterministic_ratio: float

    def to_dict(self) -> dict:
        return asdict(self)

def build_certificate(defs: list[NodeDef], bars_per_day: int) -> CostCertificate:
    calls = sum(d.cost.llm_calls_per_bar for d in defs)
    tokens = sum(d.cost.llm_calls_per_bar * d.cost.max_tokens_per_call for d in defs) * bars_per_day
    worst = max((d.cost.latency_class for d in defs),
                key=lambda c: _LATENCY_RANK[c], default="fast")
    det = (sum(1 for d in defs if d.cost.deterministic) / len(defs)) if defs else 1.0
    return CostCertificate(calls, tokens, worst, round(det, 4))
