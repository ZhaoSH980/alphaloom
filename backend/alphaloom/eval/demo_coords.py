# backend/alphaloom/eval/demo_coords.py
"""离线 demo 的**官方规范坐标**（消融 / 进化）—— 单一真源，后端与种子脚本共用。

D4-T8 审查实锤的漂移根因：``scripts/seed_recordings.py`` 用一套固定坐标录制离线
回放（``DEMO_ABLATION_*`` / ``DEMO_EVOLVE_*``），但前端 Eval Lab 从"选中 run 的
params"派生窗口、且硬编码 population/generations —— 与种子坐标不符 → 离线点击消融/
进化 = HTTP 422 ``replay_miss`` → 消融表和谱系树渲染不出来。

**修复原则（消除漂移）**：把这套规范坐标提到后端共享模块（本文件）。
  - ``/api/eval/ablation`` 与 ``/api/evolve`` 端点在 ``demo=True`` 时**服务端硬用**
    这里的常量（忽略请求体其他坐标，杜绝前端传错）。
  - ``scripts/seed_recordings.py`` **从本模块 import** 这些常量（而非自己定义）——
    seed 与 API 永远同源，改一处两处一起变，杜绝漂移。

坐标值必须与录制时逐字一致（否则 request hash 对不上 → 离线回放 miss）。本模块**只
定义值、不做任何 IO / 网络**，供后端与脚本安全 import。inst/bar 供两类 demo 共用。
"""
from __future__ import annotations

# 两类 demo 共用的标的 / 周期（demo.sqlite 的 BTC-USDT-SWAP 1m 一段）。
DEMO_INST = "BTC-USDT-SWAP"
DEMO_BAR = "1m"

# --- 消融演示"官方坐标"（/api/eval/ablation）------------------------------- #
# 招牌蓝图 agent_committee.loom，小窗口 50 根 1m bar（ts 0..2_940_000，含端）。
# committee 每 bar 3 次 LLM 调用，2 个独立臂（full / no_risk_officer；no_rag 与 full
# 逐字相同 → 回放命中 full 录制）。
DEMO_ABLATION_BLUEPRINT_ID = "agent_committee"
DEMO_ABLATION_START_MS = 0
DEMO_ABLATION_END_MS = 49 * 60_000        # ts 0..2_940_000（含端，50 根 bar）

# --- 进化演示"官方坐标"（/api/evolve）-------------------------------------- #
# ema_cross 种子（回测零 LLM，唯一 LLM 消耗 = 变异算子）。param_only 保守、小规模
# population=2 generations=2。train 窗种子真实成交（负收益 → 好变异有超越空间），
# valid 窗与 train 不重叠且远离（防泄漏；evolve 入口 _windows_overlap 硬校验）。
DEMO_EVOLVE_BLUEPRINT_ID = "ema_cross"
DEMO_EVOLVE_TRAIN = (57_600_000, 61_140_000)   # 60 bar，种子成交 5 笔、return≈-7%
DEMO_EVOLVE_VALID = (68_400_000, 71_940_000)   # 60 bar，与 train 无重叠（间隔充分）
DEMO_EVOLVE_POPULATION = 2
DEMO_EVOLVE_GENERATIONS = 2
DEMO_EVOLVE_PARAM_ONLY = True

__all__ = [
    "DEMO_INST",
    "DEMO_BAR",
    "DEMO_ABLATION_BLUEPRINT_ID",
    "DEMO_ABLATION_START_MS",
    "DEMO_ABLATION_END_MS",
    "DEMO_EVOLVE_BLUEPRINT_ID",
    "DEMO_EVOLVE_TRAIN",
    "DEMO_EVOLVE_VALID",
    "DEMO_EVOLVE_POPULATION",
    "DEMO_EVOLVE_GENERATIONS",
    "DEMO_EVOLVE_PARAM_ONLY",
]
