"""RAG（KnowledgeRetrieve/BM25）+ PADecisionTree（确定性门控）测试（AlphaLoom D3 Task 4）。

覆盖：
- BM25 检索相关性（"martingale risk" → dca.md 段命中，score 排序合理）
- KnowledgeRetrieveNode 产 citations 进 signal（下游可查）
- 强制引用软约定：citations 空 vs 非空的下游行为差异可测
- PADecisionTreeNode 纯确定性数值门控（同输入同输出、cost 全 0 deterministic True）
  收紧/否决上游信号（如 long 但 close<ema 或 atr 过小 → 降级 hold；否则透传）
- 成本注解：KnowledgeRetrieve/PADecisionTree 均 cost 0 deterministic True，不触发 llm 审计红线
"""
import pytest
import alphaloom.nodes  # 触发全部内置节点注册
from alphaloom.graph.model import NodeSpec
from alphaloom.graph.types import PinType
from alphaloom.knowledge.corpus import Corpus, load_default_corpus
from alphaloom.nodes.registry import create_instance, get_node_def
from alphaloom.runtime.context import SimClock, RunContext


def _ctx():
    return RunContext(clock=SimClock(), run_id="t")


_CANDLE = {"ts": 0, "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1}


# --------------------------------------------------------------------------- #
# BM25 检索相关性
# --------------------------------------------------------------------------- #

def test_bm25_martingale_query_hits_dca_doc():
    """'martingale risk' 检索命中 dca 文档（马丁格尔属于 DCA 家族），排在 grid/price_action 之前。"""
    corpus = load_default_corpus()
    hits = corpus.search("martingale risk", top_k=3)
    assert hits, "expected at least one hit"
    top = hits[0]
    assert top.doc_id == "dca", f"top hit should be dca, got {top.doc_id}"
    assert top.score > 0
    assert "martingale" in top.text.lower()


def test_bm25_grid_query_hits_grid_doc():
    """'grid trading levels' 命中 grid 文档，验证检索能区分不同主题。"""
    corpus = load_default_corpus()
    hits = corpus.search("grid trading levels spacing", top_k=3)
    assert hits and hits[0].doc_id == "grid"


def test_bm25_price_action_query_hits_pa_doc():
    """'Al Brooks trend breakout' 命中 price_action 文档。"""
    corpus = load_default_corpus()
    hits = corpus.search("Al Brooks trend breakout H1 H2", top_k=3)
    assert hits and hits[0].doc_id == "price_action"


def test_bm25_ranks_by_relevance_not_arbitrary():
    """同一 query 下相关段 score 严格高于不相关段（BM25 打分是真排序不是任意顺序）。"""
    corpus = load_default_corpus()
    hits = corpus.search("martingale doubling down losses", top_k=5)
    scores = [h.score for h in hits]
    assert scores == sorted(scores, reverse=True), "hits must be sorted by descending score"
    assert scores[0] > 0


def test_bm25_deterministic_same_query_same_result():
    """确定性：同 query 两次检索结果完全一致（纯函数，无随机）。"""
    corpus = load_default_corpus()
    a = corpus.search("martingale risk", top_k=3)
    b = corpus.search("martingale risk", top_k=3)
    assert [(h.doc_id, h.score) for h in a] == [(h.doc_id, h.score) for h in b]


def test_corpus_bundles_three_hand_written_docs():
    """语料恰含三个自撰文档 grid/dca/price_action。"""
    corpus = load_default_corpus()
    assert set(corpus.doc_ids()) == {"grid", "dca", "price_action"}


def test_corpus_empty_query_returns_nothing():
    corpus = load_default_corpus()
    assert corpus.search("", top_k=3) == []


def test_corpus_can_be_built_from_explicit_docs():
    """Corpus 可独立于磁盘文件构造（可测性）。"""
    c = Corpus({"a": "the quick brown fox", "b": "lazy dog sleeps"})
    hits = c.search("quick fox", top_k=1)
    assert hits and hits[0].doc_id == "a"


# --------------------------------------------------------------------------- #
# KnowledgeRetrieveNode
# --------------------------------------------------------------------------- #

def test_knowledge_retrieve_cost_is_zero_deterministic():
    d = get_node_def("knowledge_retrieve")
    assert d.category == "rag"
    assert d.cost.llm_calls_per_bar == 0
    assert d.cost.deterministic is True
    assert d.cost.latency_class == "fast"


def test_knowledge_retrieve_produces_citations():
    """检索命中文档片段作为 citations 输出。"""
    node = create_instance(NodeSpec("k", "knowledge_retrieve",
                                    {"query": "martingale risk", "top_k": 2}))
    out = node.on_bar(_ctx(), {"candle": _CANDLE, "query": None})
    cites = out["citations"]
    assert isinstance(cites, list) and len(cites) >= 1
    # citation 携带 doc_id 溯源
    assert any("dca" in c for c in cites), cites


def test_knowledge_retrieve_query_input_overrides_param():
    """运行时 query 输入引脚覆盖静态 param（下游可动态检索）。"""
    node = create_instance(NodeSpec("k", "knowledge_retrieve",
                                    {"query": "martingale", "top_k": 1}))
    out = node.on_bar(_ctx(), {"candle": _CANDLE, "query": "grid spacing levels"})
    assert any("grid" in c for c in out["citations"])


def test_knowledge_retrieve_deterministic_same_input_same_output():
    node = create_instance(NodeSpec("k", "knowledge_retrieve",
                                    {"query": "martingale risk", "top_k": 2}))
    a = node.on_bar(_ctx(), {"candle": _CANDLE, "query": None})
    b = node.on_bar(_ctx(), {"candle": _CANDLE, "query": None})
    assert a == b


def test_knowledge_retrieve_no_match_returns_empty_citations():
    node = create_instance(NodeSpec("k", "knowledge_retrieve",
                                    {"query": "zzzz nonexistent xyzzy", "top_k": 2}))
    out = node.on_bar(_ctx(), {"candle": _CANDLE, "query": None})
    assert out["citations"] == []


# --------------------------------------------------------------------------- #
# 强制引用软约定：citations 空 vs 非空下游行为差异
# --------------------------------------------------------------------------- #

def test_require_citations_gate_blocks_when_empty():
    """强制引用：上游 long 但 citations 空 → 降级 hold（未经知识库背书的交易不放行）。"""
    node = create_instance(NodeSpec("g", "require_citations", {}))
    sig = {"side": "long", "qty": 0.0, "stop": 95.0, "reason": "x",
           "rationale": "x", "confidence": 0.8, "citations": []}
    out = node.on_bar(_ctx(), {"signal": sig})["signal"]
    assert out["side"] == "hold"
    assert "citation" in out["reason"].lower()


def test_require_citations_gate_passes_when_present():
    """citations 非空 → 透传原信号（引用齐全允许交易）。"""
    node = create_instance(NodeSpec("g", "require_citations", {}))
    sig = {"side": "long", "qty": 0.0, "stop": 95.0, "reason": "x",
           "rationale": "x", "confidence": 0.8, "citations": ["dca: martingale risk"]}
    out = node.on_bar(_ctx(), {"signal": sig})["signal"]
    assert out["side"] == "long"
    assert out["citations"] == ["dca: martingale risk"]


def test_require_citations_gate_passes_hold_regardless():
    """hold/flat 本就不交易 → 不受强制引用约束，原样透传。"""
    node = create_instance(NodeSpec("g", "require_citations", {}))
    sig = {"side": "hold", "qty": 0.0, "stop": None, "reason": "x",
           "rationale": "x", "confidence": 0.0, "citations": []}
    out = node.on_bar(_ctx(), {"signal": sig})["signal"]
    assert out["side"] == "hold"


# --------------------------------------------------------------------------- #
# PADecisionTreeNode（纯确定性数值门控）
# --------------------------------------------------------------------------- #

def test_pa_gate_cost_is_zero_deterministic():
    """确定性对照：读上游 signal 用数值规则收紧，cost 全 0 deterministic True，不调 LLM。"""
    d = get_node_def("pa_decision_tree")
    assert d.category == "decision"
    assert d.cost.llm_calls_per_bar == 0
    assert d.cost.deterministic is True
    assert d.cost.latency_class == "fast"
    assert d.inputs["signal"] == PinType.SIGNAL
    assert d.outputs["signal"] == PinType.SIGNAL


def _pa(params=None):
    return create_instance(NodeSpec("p", "pa_decision_tree", params or {"min_atr": 0.5}))


def test_pa_gate_passes_long_when_close_above_ema_and_atr_ok():
    """long 信号 + close>ema + atr 足够 → 透传（趋势与信号一致，波动足够）。"""
    node = _pa()
    sig = {"side": "long", "qty": 1.0, "stop": 95.0, "reason": "up",
           "rationale": "up", "confidence": 0.8, "citations": []}
    out = node.on_bar(_ctx(), {"candle": _CANDLE, "ema": 98.0, "atr": 1.0,
                               "signal": sig})["signal"]
    assert out["side"] == "long"
    assert out["stop"] == 95.0  # 原字段保留


def test_pa_gate_demotes_long_when_close_below_ema():
    """long 但 close<ema（价在均线下方，趋势不支持多头）→ 降级 hold。"""
    node = _pa()
    sig = {"side": "long", "qty": 1.0, "stop": 95.0, "reason": "up",
           "rationale": "up", "confidence": 0.8, "citations": []}
    out = node.on_bar(_ctx(), {"candle": _CANDLE, "ema": 105.0, "atr": 1.0,
                               "signal": sig})["signal"]
    assert out["side"] == "hold"
    assert "ema" in out["reason"].lower()


def test_pa_gate_demotes_when_atr_too_small():
    """atr 低于阈值（波动过小，突破无意义）→ 降级 hold，不论方向。"""
    node = _pa({"min_atr": 0.5})
    sig = {"side": "long", "qty": 1.0, "stop": 95.0, "reason": "up",
           "rationale": "up", "confidence": 0.8, "citations": []}
    out = node.on_bar(_ctx(), {"candle": _CANDLE, "ema": 98.0, "atr": 0.1,
                               "signal": sig})["signal"]
    assert out["side"] == "hold"
    assert "atr" in out["reason"].lower()


def test_pa_gate_passes_short_when_close_below_ema():
    """short 信号 + close<ema + atr 足够 → 透传（对称规则）。"""
    node = _pa()
    sig = {"side": "short", "qty": 1.0, "stop": 105.0, "reason": "down",
           "rationale": "down", "confidence": 0.8, "citations": []}
    out = node.on_bar(_ctx(), {"candle": _CANDLE, "ema": 105.0, "atr": 1.0,
                               "signal": sig})["signal"]
    assert out["side"] == "short"


def test_pa_gate_demotes_short_when_close_above_ema():
    """short 但 close>ema（价在均线上方，趋势不支持空头）→ 降级 hold。"""
    node = _pa()
    sig = {"side": "short", "qty": 1.0, "stop": 105.0, "reason": "down",
           "rationale": "down", "confidence": 0.8, "citations": []}
    out = node.on_bar(_ctx(), {"candle": _CANDLE, "ema": 95.0, "atr": 1.0,
                               "signal": sig})["signal"]
    assert out["side"] == "hold"


def test_pa_gate_passes_hold_through():
    """上游本就 hold → 无条件透传（门控只收紧不放宽）。"""
    node = _pa()
    sig = {"side": "hold", "qty": 0.0, "stop": None, "reason": "flat",
           "rationale": "flat", "confidence": 0.0, "citations": []}
    out = node.on_bar(_ctx(), {"candle": _CANDLE, "ema": 98.0, "atr": 1.0,
                               "signal": sig})["signal"]
    assert out["side"] == "hold"


def test_pa_gate_deterministic_same_input_same_output():
    """确定性自证：同输入调两次输出完全相同（无随机、无 LLM）。"""
    node = _pa()
    sig = {"side": "long", "qty": 1.0, "stop": 95.0, "reason": "up",
           "rationale": "up", "confidence": 0.8, "citations": []}
    inp = {"candle": _CANDLE, "ema": 98.0, "atr": 1.0, "signal": sig}
    a = node.on_bar(_ctx(), inp)
    b = node.on_bar(_ctx(), inp)
    assert a == b


def test_pa_gate_handles_missing_ema_or_atr_gracefully():
    """ema/atr 未 warmup（None）→ 降级 hold（数据不足不冒进），不抛异常。"""
    node = _pa()
    sig = {"side": "long", "qty": 1.0, "stop": 95.0, "reason": "up",
           "rationale": "up", "confidence": 0.8, "citations": []}
    out = node.on_bar(_ctx(), {"candle": _CANDLE, "ema": None, "atr": None,
                               "signal": sig})["signal"]
    assert out["side"] == "hold"
