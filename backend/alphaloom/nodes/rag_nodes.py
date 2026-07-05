"""RAG 检索类节点（AlphaLoom D3）。

KnowledgeRetrieveNode：BM25 检索自撰知识库，产 citations（cost 0 deterministic True，
不调 LLM——检索是纯计算）。RequireCitationsNode：强制引用软约定门控，citations 空则把
交易信号降级 hold（未经知识库背书的交易不放行；hold/flat 本就不交易，原样透传）。
"""
from __future__ import annotations

from alphaloom.graph.types import CostAnnotation, PinType
from alphaloom.knowledge.corpus import load_default_corpus
from alphaloom.nodes.registry import node

# 语料库全局缓存：加载一次复用（纯只读，不含随机/网络）。
_CORPUS = None


def _corpus():
    global _CORPUS
    if _CORPUS is None:
        _CORPUS = load_default_corpus()
    return _CORPUS


@node(
    type="knowledge_retrieve",
    category="rag",
    inputs={"candle": PinType.CANDLE, "query": PinType.SERIES},
    outputs={"citations": PinType.SERIES},
    params={"query": str, "top_k": int},
    cost=CostAnnotation(
        llm_calls_per_bar=0,
        max_tokens_per_call=0,
        latency_class="fast",
        deterministic=True,   # 检索是纯计算：同 query 同结果，不触发 llm 审计红线
    ),
)
class KnowledgeRetrieveNode:
    """BM25 检索自撰知识库，命中文档片段作为 citations 输出。

    运行时 ``query`` 输入引脚（若非 None）覆盖静态 ``query`` param，供下游动态检索。
    每条 citation 形如 ``"<doc_id>: <段落前若干字>"``，携带 doc_id 溯源。
    """

    def setup(self, params):
        self.query = str(params.get("query", ""))
        self.top_k = int(params.get("top_k", 3))

    def on_bar(self, ctx, inputs):
        # 运行时 query 输入优先于静态 param（下游可动态传 query）
        runtime_query = inputs.get("query")
        query = runtime_query if runtime_query else self.query
        hits = _corpus().search(query, top_k=self.top_k)
        citations = [_format_citation(h) for h in hits]
        return {"citations": citations}


def _format_citation(hit) -> str:
    snippet = hit.text.strip()
    if len(snippet) > 160:
        snippet = snippet[:160].rstrip() + "…"
    return f"{hit.doc_id}: {snippet}"


@node(
    type="require_citations",
    category="rag",
    inputs={"signal": PinType.SIGNAL, "citations": PinType.SERIES},
    outputs={"signal": PinType.SIGNAL},
    cost=CostAnnotation(
        llm_calls_per_bar=0,
        max_tokens_per_call=0,
        latency_class="fast",
        deterministic=True,
    ),
)
class RequireCitationsNode:
    """强制引用软约定门控：交易信号（long/short）必须携带非空 citations 才放行。

    ``citations`` 输入 pin 可选（画布连 ``knowledge_retrieve.citations`` 即组合成
    检索背书门——正向放行的可达路径）：pin 非 None 时合流进 ``sig["citations"]``
    再判门；pin 悬空（未连接 → None）时退回只看 signal 自带 citations。
    citations 空的 long/short → 降级 hold（未经知识库背书的交易不允许）。
    hold/flat 本就不交易 → 不受约束，原样透传。这是 D3 软约定 + 测试锁的形态；
    D4 可升级为编译期 RAG 盖章类型（见 D3 Carryover 4）。
    """

    def setup(self, params):
        pass

    def on_bar(self, ctx, inputs):
        sig = dict(inputs["signal"])
        cites_in = inputs.get("citations")
        if cites_in is not None:
            # 画布接进来的检索结果：合并进 signal 自带 citations（双方溯源都保留）
            pin_cites = (list(cites_in) if isinstance(cites_in, (list, tuple))
                         else [cites_in])
            sig["citations"] = list(sig.get("citations") or []) + pin_cites
        side = sig.get("side")
        citations = sig.get("citations") or []
        if side in ("long", "short") and not citations:
            sig["side"] = "hold"
            sig["qty"] = 0.0
            sig["stop"] = None
            sig["reason"] = "blocked: trade requires non-empty citations"
        return {"signal": sig}
