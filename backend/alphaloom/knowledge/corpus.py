"""极简 BM25 检索（纯 stdlib，无第三方依赖）。

分词 + idf + bm25 打分。语料是 ``data/`` 下三个自撰 markdown（grid/dca/price_action）。
文档按段落（空行分隔）切成检索单元，命中的段落作为 citation 溯源（doc_id + 片段）。

设计要点：
- 纯函数、无随机——同 query 同结果（确定性，配合 KnowledgeRetrieve cost 0 deterministic）。
- 不引第三方（无 rank_bm25 / sklearn），面试可讲"我自己实现了 BM25 核心"。
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path

_DATA_DIR = Path(__file__).parent / "data"
_DEFAULT_DOCS = ("grid", "dca", "price_action")

# BM25 自由参数（经典默认值）：k1 控制词频饱和，b 控制文档长度归一化。
_K1 = 1.5
_B = 0.75

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_CJK_RE = re.compile(r"[一-鿿]+")   # CJK 统一表意文字基本区 U+4E00-U+9FFF


def _tokenize(text: str) -> list[str]:
    """小写化 + 英文字母数字 token + CJK 相邻 2-gram。

    语料中英对照：英文走 ``[a-z0-9]+`` 分词；中文无空格分词，CJK 字符连串按相邻
    2-gram 切分（"马丁格尔" → 马丁/丁格/格尔），单字连串保留单字。查询与文档共用
    同一分词规则，中文查询（如"马丁格尔 爆仓"）即可命中语料的中文段落。
    """
    lower = text.lower()
    toks = _TOKEN_RE.findall(lower)
    for run in _CJK_RE.findall(lower):
        if len(run) == 1:
            toks.append(run)
        else:
            toks.extend(run[i:i + 2] for i in range(len(run) - 1))
    return toks


@dataclass(frozen=True)
class Hit:
    """检索命中：doc_id + 命中段落文本 + BM25 score。"""
    doc_id: str
    text: str
    score: float


class Corpus:
    """BM25 语料库：把每个文档按段落切成检索单元，支持 ``search(query, top_k)``。"""

    def __init__(self, docs: dict[str, str]):
        # 检索单元 = (doc_id, 段落原文, token 列表)。段落按空行切分。
        self._units: list[tuple[str, str, list[str]]] = []
        self._doc_ids: list[str] = list(docs.keys())
        for doc_id, raw in docs.items():
            for para in _split_paragraphs(raw):
                self._units.append((doc_id, para, _tokenize(para)))

        # 文档频率 df[term] = 含该 term 的检索单元数；用于 idf。
        self._df: dict[str, int] = {}
        for _, _, toks in self._units:
            for term in set(toks):
                self._df[term] = self._df.get(term, 0) + 1

        self._n = len(self._units)
        self._avg_len = (
            sum(len(toks) for _, _, toks in self._units) / self._n
            if self._n else 0.0
        )

    def doc_ids(self) -> list[str]:
        return list(self._doc_ids)

    def _idf(self, term: str) -> float:
        # BM25 idf（含 +0.5 平滑，非负截断）。
        df = self._df.get(term, 0)
        if df == 0:
            return 0.0
        return math.log(1 + (self._n - df + 0.5) / (df + 0.5))

    def _score(self, query_terms: list[str], toks: list[str]) -> float:
        if not toks:
            return 0.0
        length = len(toks)
        # 词频表
        tf: dict[str, int] = {}
        for t in toks:
            tf[t] = tf.get(t, 0) + 1
        score = 0.0
        for term in query_terms:
            f = tf.get(term, 0)
            if f == 0:
                continue
            idf = self._idf(term)
            denom = f + _K1 * (1 - _B + _B * length / self._avg_len)
            score += idf * (f * (_K1 + 1)) / denom
        return score

    def search(self, query: str, top_k: int = 3) -> list[Hit]:
        """返回按 BM25 score 降序排列的 top_k 命中（score>0 才算命中）。"""
        query_terms = _tokenize(query)
        if not query_terms or self._n == 0:
            return []
        scored: list[Hit] = []
        for doc_id, para, toks in self._units:
            s = self._score(query_terms, toks)
            if s > 0:
                scored.append(Hit(doc_id=doc_id, text=para, score=s))
        # 稳定排序：先按 score 降序，score 相等按 doc_id 保证确定性。
        scored.sort(key=lambda h: (-h.score, h.doc_id))
        return scored[:top_k]


def _split_paragraphs(raw: str) -> list[str]:
    """按空行切分段落，去掉 markdown 标题行（#...）与空白段。"""
    paras: list[str] = []
    for block in re.split(r"\n\s*\n", raw):
        lines = [
            ln for ln in block.splitlines()
            if ln.strip() and not ln.lstrip().startswith("#")
        ]
        text = " ".join(ln.strip() for ln in lines).strip()
        if text:
            paras.append(text)
    return paras


def load_default_corpus() -> Corpus:
    """从 ``data/{grid,dca,price_action}.md`` 加载自撰语料。"""
    docs: dict[str, str] = {}
    for doc_id in _DEFAULT_DOCS:
        path = _DATA_DIR / f"{doc_id}.md"
        docs[doc_id] = path.read_text(encoding="utf-8")
    return Corpus(docs)
