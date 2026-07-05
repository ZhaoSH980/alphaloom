"""经验库：按市场状态桶索引的 SQLite 存储 + 市场状态桶派生纯函数（AlphaLoom D3）。

反思闭环的记忆层：平仓后 Reflector 把 {桶, 配置摘要, 结局, 教训} 写进 ExperienceStore；
下一次决策时 ExperienceRetrieve 按**当前市场状态桶**检索历史教训注入决策上下文。

市场状态桶（regime bucket）由 ema 斜率 + atr 派生（纯函数，无 IO/随机）：
- ema 明显上升（斜率超阈值）→ trend_up
- ema 明显下降 → trend_down
- ema 走平 / 数据未 warmup → range（震荡市，保守默认）

桶是经验检索的隔离键——trend_up 学到的教训只在 trend_up 复现时被召回，不跨状态串味。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

# ema 斜率阈值：|ema - ema_prev| / max(atr,eps) 超过此值才判趋势，否则 range。
# 用 atr 归一化（把斜率换算成"多少个 atr"的移动）使阈值对不同价位/波动尺度稳健。
_SLOPE_ATR_RATIO = 0.10


def derive_regime_bucket(ema, ema_prev, atr) -> str:
    """由 ema 斜率 + atr 派生市场状态桶（纯函数，确定性）。

    - 任一输入缺失（未 warmup）→ range（数据不足不冒进猜趋势）。
    - |ema - ema_prev| 相对 atr 的比值 > 阈值：ema 上升 → trend_up，下降 → trend_down。
    - 否则（ema 走平）→ range。
    """
    if ema is None or ema_prev is None or atr is None:
        return "range"
    atr_f = float(atr)
    if atr_f <= 0:
        return "range"
    slope = float(ema) - float(ema_prev)
    if abs(slope) / atr_f <= _SLOPE_ATR_RATIO:
        return "range"
    return "trend_up" if slope > 0 else "trend_down"


class ExperienceStore:
    """按市场状态桶索引的经验库（SQLite，data/experience.sqlite）。

    幂等键 (bucket, trade_key)：同一笔平仓的反思写多次只留一行（UPSERT），
    避免引擎重放 / 重复触发把同一教训灌爆库。
    """

    def __init__(self, db_path):
        self.db_path = str(db_path)
        parent = Path(self.db_path).parent
        if str(parent) and parent != Path("."):
            parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self):
        # 每次调用开一条**短命**连接并显式关闭：节点从不显式 close()，长命连接会
        # 泄漏句柄——Windows 上还会锁住 db 文件破坏测试 teardown（评审 PLAUSIBLE 修）。
        return sqlite3.connect(self.db_path)

    def _init_db(self):
        conn = self._connect()
        try:
            with conn:   # 事务：成功即 commit
                conn.execute(
                    """CREATE TABLE IF NOT EXISTS experience (
                           bucket         TEXT NOT NULL,
                           trade_key      TEXT NOT NULL,
                           config_summary TEXT NOT NULL,
                           outcome        TEXT NOT NULL,
                           pnl            REAL NOT NULL,
                           lesson         TEXT NOT NULL,
                           PRIMARY KEY (bucket, trade_key)
                       )"""
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_bucket ON experience(bucket)")
        finally:
            conn.close()

    def write(self, *, bucket: str, trade_key: str, config_summary: str,
              outcome: str, pnl: float, lesson: str) -> None:
        """写一条经验；(bucket, trade_key) 已存在则覆盖（幂等）。"""
        conn = self._connect()
        try:
            with conn:
                conn.execute(
                    """INSERT INTO experience
                           (bucket, trade_key, config_summary, outcome, pnl, lesson)
                       VALUES (?, ?, ?, ?, ?, ?)
                       ON CONFLICT(bucket, trade_key) DO UPDATE SET
                           config_summary=excluded.config_summary,
                           outcome=excluded.outcome,
                           pnl=excluded.pnl,
                           lesson=excluded.lesson""",
                    (bucket, trade_key, config_summary, outcome, float(pnl), lesson),
                )
        finally:
            conn.close()

    def retrieve(self, *, bucket: str, top_k: int = 5) -> list[dict]:
        """按桶检索最近 top_k 条经验（隔离键：只返回该桶的经验）。

        排序：rowid 降序（最近写入优先）——最新教训权重更高。
        """
        conn = self._connect()
        try:
            rows = conn.execute(
                """SELECT bucket, trade_key, config_summary, outcome, pnl, lesson
                       FROM experience WHERE bucket = ?
                       ORDER BY rowid DESC LIMIT ?""",
                (bucket, int(top_k)),
            ).fetchall()
        finally:
            conn.close()
        cols = ("bucket", "trade_key", "config_summary", "outcome", "pnl", "lesson")
        return [dict(zip(cols, r)) for r in rows]
