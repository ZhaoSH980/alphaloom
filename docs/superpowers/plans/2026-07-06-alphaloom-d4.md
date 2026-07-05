# AlphaLoom D4 Implementation Plan — 评估纪律 + 进化实验室 + 发布

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 收口 AlphaLoom 的"诚实评估"卖点并完成发布：成交模型保真度阶梯（L0-L3，回测测谎仪）、蓝图记分卡+基线排行榜、委员会消融实验、进化实验室（LLM 变异算子+谱系树）、清偿高优先 Carryover 债务、文档/README banner/截图/GIF、发布准备（不含公开推送——留给用户）。

**Architecture:** 在 D1-D3 之上加 `eval/` 评估工具链（保真度阶梯复用 okx_algorithnm 成交模型思想、记分卡聚合已有 report、排行榜对比基线、消融跑对照组）+ `evolve/` 进化（LLM 变异图 diff→编译守门→回测适应度→谱系树）+ 前端 Runs&Eval 区。全程录制保证离线零配额；实验规模锁定（消融 3 臂、进化种群≤4 代数≤3）。

**Tech Stack:** 复用 D1-D3 全栈；无新第三方依赖（保真度阶梯纯 Python 成交模型，谱系树 React Flow 复用）。

**执行约定（沿用 D1-D3 全部教训）：** 每任务实现者+单审查者两阶段审查；**实现者不得 spawn 审查子智能体**（D3-T6 教训）；**LLM/WS/浏览器/录制集成缺陷单元测试测不出，控制器必做 preview live 走查兜底**（D2 uvicorn WS、D3 serve.py 相对路径都是 live 才抓到）；**录制/数据类操作必留备份、文档必须与实物一致、"文档吹大于实物"零容忍**（D3-T11 诚实性事故教训）；计划即权威，偏差先改计划；`.env` 绝不入库；讯飞 429 耐心退避，演示走录制回放；接缝功能必须端到端连通验证不是纸面（T4/T5/T10 教训：字段名/数据流/画布连线逐一核）。

---

## 锁定契约（跨任务，改动即 sanctioned deviation）

**保真度阶梯**（spec §7，回测测谎仪，零 LLM 配额）：`eval/fidelity.py::fidelity_ladder(blueprint, source, fills, ...) -> LadderReport`。同一终选策略的成交序列在四档成交模型下重放，量化"回测乐观偏差"：
- **L0 天真收盘成交**：信号 bar 收盘价即成交（最乐观，无滑点无时序）
- **L1 次 bar 开盘**（现状 PaperBroker 语义，D1 基线）
- **L2 盘中路径代理**：用 bar 的 OHLC 路径估算更差成交（如 stop 用 low/high 触发、限价用保守侧）
- **L3 手续费+滑点加压**：L2 + 额外滑点模型（bps）
产出 `LadderReport{levels: [{level, net_pnl, max_dd, num_trades, profit_factor}], optimism_gap: L0_pnl - L3_pnl}`。**注意**：这是重放已生成的成交序列在不同成交假设下，不重跑 LLM（决策不变、只变成交撮合），故零配额。

**蓝图记分卡**（spec §7）：`eval/scorecard.py::scorecard(run_report, ladder?, ablation?) -> Scorecard`——聚合样本内/验证窗表现、保真度阶梯衰减、成本证书、（可选）反事实/消融摘要。gallery 按证据排序（记分卡的综合分）。

**基线排行榜**（spec §7）：`eval/leaderboard.py::leaderboard(blueprint_runs, baselines) -> Board`。基线：buy-and-hold、默认参数网格、随机参数。同表对比 net_pnl/return/max_dd/win_rate + **样本内-验证窗泛化差距**。Agent 必须证明打得过无脑基线（若打不过，如实展示——诚实传统）。

**委员会消融**（spec §7，D3 Carryover #1）：`eval/ablation.py::committee_ablation(base_blueprint, source, llm) -> AblationReport`。三臂：完整委员会 / 去风控官 / 去 RAG（require_citations）。同数据比较验证窗表现 + 风控官拦截的危险提案计数。**规模锁定**：3 臂 × 同一窗口，全程录制供离线回放。预期卖点：无风控官臂样本内更好看、验证窗更差——护栏价值量化。若结果相反如实发布。

**进化实验室**（spec §6，"Agent 即研究员"终极形态）：`evolve/lab.py::evolve(seed_blueprint, source, llm, *, population=4, generations=3) -> Genealogy`。Agent 读回测报告→提出蓝图**变异**（参数/节点/连线，图 diff）→变异版过编译（**风控类型系统守门——变异不能绕风控**）→回测评估适应度→优者存活进下代→谱系树。本质遗传算法，变异算子是 LLM。**规模硬锁定**：种群≤4、代数≤3、适应度用样本内窗口+终选做验证窗打分（防过拟合，泄漏测试保障）。**降级保险丝**：只做参数变异不动图结构。全程录制。

**Carryover 债务清偿批次**（D2/D3 各 Carryover，D4 前必修的）：
- `check_stamped` 深递归化（D2 Carryover 13①）——D4 有结构化端口值前处理
- `@app.on_event` → lifespan 迁移（D2 Carryover 9②）+ RunsStore/worker 连接 finalizers（D2 Carryover 9②/T3 前瞻）
- 沙箱运行期资源限额（D3 Carryover 3：算术传播 range DoS + CPU/内存/超时）+ 非 PinType 输出校验已在 D3-T8 补
- registry 命名空间（D3 Carryover：进程级 REGISTRY 跨用户可见）——D4 至少加 session 前缀或文档化单用户假设
- 止损成交价 clamp（D1 Carryover 15③，接真实数据硬性）——保真度阶梯 L2/L3 天然覆盖
- EOD 反思（D3 Carryover 8）——保真度/记分卡不依赖，可文档化延后

**REST/前端新增**：`POST /api/eval/fidelity`、`/api/eval/ablation`、`/api/evolve`；前端 Runs&Eval 区（保真度阶梯图、记分卡、排行榜、消融表、进化谱系树 React Flow）。

**发布准备（不含公开推送）**：README banner/截图/GIF、docs/evaluation-methodology.md（统计局限诚实框定）、docs/demo-script.md（10 分钟 talk track）、docs/future-work.md、LICENSE 已有、CI workflow（pytest+前端构建）。**GitHub 公开推送留给用户一键执行**（不可逆对外发布）。

---

（本文件 Task 1-N 由控制器续写；施工前补齐全部任务。以下为骨架任务序，控制器逐一展开为完整 TDD 步骤。）

## 任务序（控制器展开）
- **T1 Carryover 加固批次**：lifespan 迁移 + 连接 finalizers + 沙箱变量 range 上限 + registry session 前缀（或文档化）。全量绿。
- **T2 保真度阶梯**：eval/fidelity.py 四档成交模型 + LadderReport + 测试（optimism_gap 单调 L0≥L1≥L2≥L3 的 pnl）。
- **T3 蓝图记分卡 + 基线排行榜**：eval/scorecard.py + eval/leaderboard.py + 基线（buy-hold/默认/随机）+ 泛化差距 + 测试。
- **T4 委员会消融**：eval/ablation.py 三臂 + 录制 + 离线回放验证 + 测试（护栏价值量化，结果诚实）。
- **T5 进化实验室**：evolve/lab.py LLM 变异+编译守门+适应度+谱系树 + 录制 + 规模锁定 + 降级保险丝 + 测试。
- **T6 Eval API**：/api/eval/* + /api/evolve 端点 + 服务注入 + 离线安全 + 测试。
- **T7 前端 Runs&Eval 区**：保真度阶梯图 + 记分卡 + 排行榜 + 消融表 + 进化谱系树（React Flow）+ 编译循环防范 + build/tsc/vitest。
- **T8 评估录制种子**：真实讯飞录制消融/进化演示（小规模）+ 离线验证 + 诚实框定（沿用 D3 双源模式，保留备份）。
- **T9 文档批次**：README banner/截图/GIF + evaluation-methodology.md（统计局限）+ demo-script.md + future-work.md + CI workflow。
- **T10 D4 集成走查 + 全分支终审 + tag d4-complete + 发布准备清单**：控制器 live 走查全站（阶梯/排行榜/消融/进化谱系树渲染）+ 终审 + 打 tag + 生成"用户一键 GitHub 推送"清单（不执行推送）。

## D4 Carryover（发布后/未来）
1. 多市场真实数据接入（DataSource 抽象已备，OKX demo 盘实盘模式）——spec 承诺保留接口。
2. 进化实验室扩规模（当前种群≤4 代数≤3 是演示锁定）。
3. 沙箱完整资源限额（CPU/内存/超时 cgroup 级）——当前仅 AST + range 上限。
4. 反事实分叉 UI（spec §3.3 创新③，D4 若溢出则 future-work）。
