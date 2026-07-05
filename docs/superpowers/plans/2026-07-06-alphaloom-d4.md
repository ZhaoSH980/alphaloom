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

> **D4-T2 实施修订（sanctioned deviations，审查确认后写回契约）**：
> 1. **签名**定为 `fidelity_ladder(fills, candles, *, initial_cash=10_000.0, fee_rate=0.0005, slippage_bps=5.0) -> LadderReport`（非 `(blueprint, source, fills)`）。下单意图由 `replay_intents(fills, candles)` 从成交序列反推（fill.ts = L1 执行 bar、信号 bar = 紧邻前根；stop / eod_close 腿特判，见第 4 条），零改 runner、零重跑决策——最小侵入且天然零配额。
> 2. **L0 定义精化**为"信号 bar 收盘价与次 bar 开盘价中取对交易者**更有利**一侧"。原因：纯"信号 close 成交"在**有利跳空**行情下会出现 L1 > L0，与本契约自己锁定的单调性测试（L0≥L1≥L2≥L3）自相矛盾；取优是唯一自洽解，且更贴合 L0"最乐观的谎言"叙事。
> 3. **fee 四档统一按 fee_rate 收**（非字面"手续费只在 L3"）。原因：PaperBroker（D1 基线）每笔 fill 都收费，若 L1 不收费则 L1 对不上基线 net_pnl——又一内部矛盾。L3 的"加压"体现为**额外** slippage_bps 名义额滑点。
> 4. **腿级语义修订**（实施中发现）：stop fill（tag="stop"，PaperBroker 盘中触发、成交价=stop 位）以其**真实成交价为 L1 基准**，不按"次 bar 开盘"重放（否则系统性乐观）；eod_close 合成结算腿（ts=末根+bar_ms，数据外）以**末 bar close** 定价（对齐 runner 结算语义，使 L1 与 broker net_pnl 精确对齐）；L2 成交价 **clamp 到执行 bar [low, high]**（病态宽振幅 bar 否则产生负价并破坏 L3≥L2 单调，审查者构造性反例实锤）；L3 滑点施加于 clamp 后价格、允许越出 bar 观测路径（滑点=市场冲击本可超出已观测成交带，且若 clamp L3 会在最需要加压的病态 bar 上把加压归零——与 L3 的目的相反）。

**蓝图记分卡**（spec §7）：`eval/scorecard.py::scorecard(run_report, ladder?, ablation?) -> Scorecard`——聚合样本内/验证窗表现、保真度阶梯衰减、成本证书、（可选）反事实/消融摘要。gallery 按证据排序（记分卡的综合分）。

**基线排行榜**（spec §7）：`eval/leaderboard.py::leaderboard(blueprint_runs, baselines) -> Board`。基线：buy-and-hold、默认参数网格、随机参数。同表对比 net_pnl/return/max_dd/win_rate + **样本内-验证窗泛化差距**。Agent 必须证明打得过无脑基线（若打不过，如实展示——诚实传统）。

> **D4-T3 实施注记（sanctioned deviations，控制器 T3 任务书已核准）**：
> 1. **记分卡签名**定为 `scorecard(train_report, valid_report=None, *, ladder=None, cost_cert=None, ablation=None) -> Scorecard`（非 `(run_report, ladder?, ablation?)`）——泛化差距需要 train/valid 两段 report 才能算，cost_cert 未显式给则自动取 `train_report.certificate`。综合分权重为模块级常量（valid_performance 0.40 / generalization 0.25 / fidelity 0.20 / determinism 0.15），缺证据维度按 MISSING_EVIDENCE_SCORE=25 保守计并在 evidence_coverage 如实标注（缺证据=低证据分，不是满分）。
> 2. **排行榜签名**定为 `leaderboard(entries) -> Board`，entry = `{name, train_report, valid_report?, kind: "blueprint"|"baseline"}`（蓝图与基线同构同表，不分两参）。行指标取排序窗口（valid 优先，无 valid 用 train 并标 `in_sample_only=true`），排序按 return_pct 降序、蓝图打不过基线如实垫底。
> 3. **"默认参数网格"基线**落地为 `baseline_ema_default`（默认参数 ema_cross.loom 跑真实 run_backtest，纯确定性零 LLM）；"随机参数"落地为 `baseline_random` 随机进出场（固定 seed 可复现，certificate 披露 luck_baseline=True）。buy_hold 纯数值自算，fee 语义与 PaperBroker 一致（fee=qty×price×fee_rate 每腿收，全仓=含入场费打满）。三基线 certificate.llm_calls_per_bar=0。

**委员会消融**（spec §7，D3 Carryover #1）：`eval/ablation.py::committee_ablation(base_blueprint, source, llm) -> AblationReport`。三臂：完整委员会 / 去风控官 / 去 RAG（require_citations）。同数据比较验证窗表现 + 风控官拦截的危险提案计数。**规模锁定**：3 臂 × 同一窗口，全程录制供离线回放。预期卖点：无风控官臂样本内更好看、验证窗更差——护栏价值量化。若结果相反如实发布。

> **D4-T4 实施注记（sanctioned deviations，控制器 T4 任务书已核准）**：
> 1. **签名**定为 `committee_ablation(base_blueprint, source, *, inst, bar, start_ms=None, end_ms=None, llm=None, arms=None, initial_cash=10_000.0, fee_rate=0.0005) -> AblationReport`（非 `(base_blueprint, source, llm)`）——窗口/标的参数化对齐 run_backtest；`arms` 允许子集对照（缺 full/no_risk_officer 任一臂时 guardrail_value=None，不硬造对照）。
> 2. **三臂由图变换生成**（消融=可编程的图手术，不是三份手写蓝图）：no_risk_officer 臂 = 参数手术——committee 节点新增 `skip_risk_officer: bool` param（committee.py 小改：跳过风控官角色调用，主席只读策略师 JSON，trace 两项、无 veto 可能；cost 注解维持 3 calls/bar 的**静态上界**不随参数收窄——证书是编译期注解，只许高估不许低估）；no_rag 臂 = `graph_bypass(require_citations)`（SIGNAL→SIGNAL 透传门旁路，上游直连原下游）+ 移除 knowledge_retrieve 及其悬空边。
> 3. **veto/四象限计数经 `run_backtest(breakpoints="all", on_pause=collector)` 旁路收集**（零改 runner）：从下游节点输入 signal 里的 committee_trace 数 veto bar（按 bar 去重），从 reflector verdict 载荷（trade_key+verdict）数四象限（按 trade_key 去重）。依赖 committee 输出至少接一个下游节点——真实蓝图恒成立。
> 4. **硬护栏不可消融测试锁定**：`graph_bypass(risk_gate)` 产物编译必 TYPE_MISMATCH（headline 蓝图与测试蓝图各锁一次，fix_hint 指回 RiskGate）——消融能拆的只有 LLM 风控官"软护栏"，类型系统"硬护栏"编译器不放行，这本身就是卖点。
> 5. **guardrail_value 纯计算、正负都如实**：full − no_risk_officer 的 net_pnl/return_pct/max_drawdown 差 + `guardrail_helped` 布尔。测试含正剧本（风控官拦下暴跌前危险提案：full 0.0 vs 消融臂 −510.14，delta=+510.14）与**反向剧本**（偏执风控官净误杀上涨行情的盈利交易：delta=−4753.94、guardrail_helped=false 如实展示）——代码无任何硬编码结论，T8 真实录制若结果相反照实呈现。
> 6. 录制/离线回放验证属 T8（真实讯飞录制），本任务全部测试用确定性 fake transport（纯本地函数，零网络零配额），未触碰 data/llm_calls.sqlite。

**进化实验室**（spec §6，"Agent 即研究员"终极形态）：`evolve/lab.py::evolve(seed_blueprint, source, llm, *, population=4, generations=3) -> Genealogy`。Agent 读回测报告→提出蓝图**变异**（参数/节点/连线，图 diff）→变异版过编译（**风控类型系统守门——变异不能绕风控**）→回测评估适应度→优者存活进下代→谱系树。本质遗传算法，变异算子是 LLM。**规模硬锁定**：种群≤4、代数≤3、适应度用样本内窗口+终选做验证窗打分（防过拟合，泄漏测试保障）。**降级保险丝**：只做参数变异不动图结构。全程录制。

> **D4-T5 实施注记（sanctioned deviations，控制器 T5 任务书已核准）**：
> 1. **签名**定为 `evolve(seed_blueprint, source, *, inst, bar, train_window, valid_window, llm, population=4, generations=3, mutations_per_gen=None, param_only=False, initial_cash=10_000.0, fee_rate=0.0005, temperature=0.3) -> Genealogy`（非 `(seed_blueprint, source, llm)`）——窗口/标的参数化对齐 run_backtest；`train_window`/`valid_window` 为 `(start_ms, end_ms)` 显式两窗，**重叠直接 ValueError**（防泄漏是结构保证 + 入口校验双保险）；`mutations_per_gen` 默认 = population、上限 2×MAX_POPULATION=8（LLM 预算护栏，超出 ValueError）。
> 2. **变异 patch 格式**（任务书授权"格式你定"）：`{summary, set_params: {nodeId: {param: value}}, add_nodes: [...], del_nodes: [...], add_edges: [{"from","to"}], del_edges: [...]}`，应用序 set_params → del_nodes → add_nodes → del_edges → add_edges；应用侧只校验引用存在性与 param_only 保险丝（`MutationRejected` 喂回 LLM 重试），**端点类型是否闭合一律交编译器裁决**（与消融 graph_bypass 同哲学——TYPE_MISMATCH 拦"去 risk_gate"变异，fix_hint 含 RiskGate，测试锁定）。
> 3. **compile_status 语义**：`repaired` = 经历 ≥1 轮反馈（非 JSON 回复 / patch 被拒 / 编译失败均计）后过编译，非仅"编译失败后修复"——反馈环是同一条（复用 copilot `_errors_to_feedback`），状态如实反映"没有一次过"。stillborn 记录最后一次尝试的蓝图 JSON（若从未成功应用 patch 则为 null）。
> 4. **选择 = 精英保留**：存活个体与本代孩子同池按适应度排序取 top-N（父代不自动死亡，被挤出才死；平分保老，排序稳定确定）。适应度 = train 窗 return_pct、num_trades==0 判 0（T3 零交易=零证据教义；全员亏损种群里 0 分躺平者居首是"不亏就是赢"的诚实结论，docstring 如实披露）。
> 5. **Genealogy 形状扩充**：node 增 `survived`（终局存活集标记，React Flow 高亮用）；顶层增 `param_only/population/generations`（实验规模自描述）；winner 增 `train_summary/valid_summary`（完整成绩单，不止四个契约字段）。React Flow 契约保持：nodes + parent_id 即树，blueprint_json 内嵌可直接开画布。
> 6. 全部测试确定性剧本 LLM（纯本地队列，剧本精确耗尽断言 = 零配额自证）；真实录制演示属 T8。未触碰 data/llm_calls.sqlite。

**Carryover 债务清偿批次**（D2/D3 各 Carryover，D4 前必修的）：
- `check_stamped` 深递归化（D2 Carryover 13①）——D4 有结构化端口值前处理
- `@app.on_event` → lifespan 迁移（D2 Carryover 9②）+ RunsStore/worker 连接 finalizers（D2 Carryover 9②/T3 前瞻）
- 沙箱运行期资源限额（D3 Carryover 3：算术传播 range DoS + CPU/内存/超时）+ 非 PinType 输出校验已在 D3-T8 补
- registry 命名空间（D3 Carryover：进程级 REGISTRY 跨用户可见）——D4 至少加 session 前缀或文档化单用户假设
- 止损成交价 clamp（D1 Carryover 15③，接真实数据硬性）——保真度阶梯 L2/L3 天然覆盖
- EOD 反思（D3 Carryover 8）——保真度/记分卡不依赖，可文档化延后

**REST/前端新增**：`POST /api/eval/fidelity`、`/api/eval/ablation`、`/api/evolve`；前端 Runs&Eval 区（保真度阶梯图、记分卡、排行榜、消融表、进化谱系树 React Flow）。

> **D4-T6 实施注记（sanctioned deviations，控制器 T6 任务书已核准）**：
> 1. **端点集扩为 5 个**（非契约字面 3 个）：`/api/eval/fidelity`、`/api/eval/leaderboard`、`/api/eval/ablation`、`/api/eval/scorecard`、`/api/evolve`。**scorecard 判断**：加端点而非纯前端算——综合分数学（tanh 压缩 / 四权重 / 缺证据保守分 / 零交易零证据）是**载荷型诚实评分逻辑，必须唯一实现**在后端，前端重实现会与 Python 源真相漂移（违背 D3-T11 诚实性教训）；端点接前端已算好的证据碎片（run 报告 + 阶梯 + 消融）→ 权威 `Scorecard.to_dict()`，纯数值零 LLM。
> 2. **同步执行**（plain `def` 端点 → FastAPI 自动丢线程池，与既有 `def` 端点同款，不阻塞事件循环）：数据量锁定小规模（离线 ≤400 bar 数秒级、消融 ≤3 臂、进化 pop≤4/gen≤3），走 RunService 异步只增复杂度无收益；同步直接返回报告是本 demo 的正解。source 每请求开/关（`try/finally`）。
> 3. **LLM 配额守门设计**：含 LLM 节点的蓝图（编译证书 `llm_calls_per_bar>0`）非 offline 客户端跑它会烧真配额（每 bar/每臂/每孩子调 LLM）。守门规则——LLM 蓝图仅当 `getattr(app.state.llm, "offline", False) is True`（录制回放 / 本地剧本，零配额）时放行，否则 **409**；进化端点额外守变异算子本身（每孩子调 LLM）；纯确定性蓝图（`llm_calls_per_bar==0`）无条件放行（连 `llm=None` 也照跑，基线/ema_cross 零 LLM）。测试注入的 scripted fake LLM 标 `offline=True`（本地剧本 = 真零配额 = 视同 offline 安全），`LiveLikeLLM`（`offline=False`）用于自证守门真拦住（其 `chat` 被调即 AssertionError）。
> 4. **消融臂预筛**：端点只跑蓝图实际支持的臂（无 committee → 无 no_risk_officer 对象 → 422；无 require_citations → 静默跳过 no_rag，只跑 full+no_risk_officer，`guardrail_value` 照算）。`arm_blueprint` 对缺席目标抛 ValueError → 干净 422（非 500）。
> 5. **ReplayMissError 干净 4xx**：offline 客户端 + 空录制库（消融/进化臂的 LLM 调用未录，T8 才录）→ `ReplayMissError` 捕获转 **422**（带"re-run in record mode"解释），不是 500 栈。消融 offline 真实回放留 T8 录完才通，端点+测试先用注入 fake 走通。
> 6. **规模超限 422 双保险**：pydantic 层 `population∈[1,4]`/`generations∈[1,3]`（`EvolveIn` Field 约束）先挡，evolve 内 ValueError（含窗口重叠）转 422 兜底。窗口边界沿用 RunIn 的 int64 溢出防护（`le=4_102_444_800_000`）。
> 7. **T5 审查遗留必修（本任务内做）**：`evolve/lab.py` 孩子回测的运行期错误收容——变异 param 类型垃圾（如 `period="very fast"`）编译过（int 只是声明类型不校验值）但在 `create_instance/setup` 的 `int()` 处 ValueError，此前未捕获炸掉整棵谱系（API 暴露后网络可达 DoS）。修为：孩子回测包 `try/except`，炸了记 `compile_status="runtime_error"`（新增枚举值）、`fitness=None`、错误摘要入新增 `GenealogyNode.error` 字段、不进种群、**进化继续**；**seed 跑炸仍 raise**（种子坏是调用方错误）。`to_dict` 增 `error` 字段，React Flow 形状注意 `runtime_error` 新枚举。
> 8. **C1（T6 审查 Critical）修复——沙箱自定义节点绕过 LLM 配额守门（网络可达刷真配额）**：沙箱 AST 不拦普通属性访问 `ctx.llm`，一个声称 `llm_calls_per_bar=0` 的沙箱节点能在 on_bar 偷调 `ctx.llm.chat` 刷爆讯飞配额（红队实锤：证书报 0 → 守门放行 → 真 LLM 被调 N 次 → 200）。守门信任根（成本证书）在沙箱节点在场时失效。**两层深度防御**：① **根治（引擎层）**——`NodeDef` 增 `sandboxed` 标记（`compile_node_source` 注册后 `mark_sandboxed` 回填），`create_instance` 传到实例，`engine._step_inner` 给沙箱节点一个 `_RestrictedContext`（委托真 ctx 但 `.llm`/`.audit` 访问抛 `SandboxEscapeError`）——沙箱节点运行期**真的够不到 LLM 句柄**（选运行期剥离而非 AST 名字拒绝：AST 拒可被 `c=ctx;c.llm` 别名绕过、且会误伤名为 `llm` 的无关变量；运行期剥离是硬保证，与"沙箱即合规官/不能伪造风控盖章"同款卖点）。② **守门兜底（app.py）**——`_needs_llm` 补充：蓝图含**任何** `NodeDef.sandboxed=True` 节点即按"可能烧配额"处理，非 offline 即 409（不信任沙箱节点自证，防未来同类信任缺口）。RED→GREEN 实锤：修复前该攻击链 leaderboard 返回 200 且 SpyLLM 被调 60 次；修复后 409 且 `spy.calls==0`。内置受信 LLM 节点（llm_analyst）不受剥离影响（拿真 ctx）；合法纯计算沙箱节点（`ctx=None` 或只用 clock/broker）零误伤（D3 沙箱 61 测试全绿）。
> 9. **M1（T6 审查 Minor，记账不修）**：eval 端点无窗口跨度 / bar-count 上限——已记入 D4 Carryover #5（接实盘数据源前加 span/bar-count 硬上限）。

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
5. **eval 端点窗口跨度 / bar-count 上限**（T6 审查 M1）——`/api/eval/*`（fidelity/
   leaderboard/ablation）与 `/api/evolve` 目前无显式窗口跨度或 bar 数上限，靠 demo
   DB 体量（≤400 bar）隐式兜底，非显式护栏。**接实盘/大体量数据源前**须加 span
   或 bar-count 硬上限（对齐 evolve 的规模硬锁定精神），否则超长窗口会拖垮同步端点
   （同步执行 + 线程池，单请求跑满一个 worker）。
