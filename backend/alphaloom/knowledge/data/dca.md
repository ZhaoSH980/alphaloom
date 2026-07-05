# DCA & Martingale — Risk Notes / 定投与马丁格尔风险

**Hand-written summary for AlphaLoom RAG. Original notes, not copied from any third-party corpus.**

## English

Dollar-cost averaging (DCA) splits a position into equal instalments bought at a
fixed schedule regardless of price. It smooths the average entry and removes the
need to time the bottom. Plain DCA keeps the instalment size constant, so its
downside is bounded by the total capital committed on the schedule.

Martingale is a different and far more dangerous cousin. After each losing step
it doubles (or otherwise scales up) the next order to average the entry down and
recover the whole drawdown on a small bounce. The seductive property is a high
win rate on paper: most sequences do bounce and close green. The fatal property
is that position size and required capital grow geometrically, so a long adverse
run demands exponentially more margin. Doubling down into a persistent trend
leads to a blown account — the rare long losing streak wipes out many small wins.

Risk controls that matter: cap the maximum number of averaging steps, size the
first order so the fully-loaded position still fits inside a hard stop, and never
let martingale-style doubling run without a drawdown kill switch. Treat any
"can't lose" doubling scheme as hidden tail risk, not free money.

## 中文

定投（DCA）把一笔仓位拆成等额分批、按固定节奏无视价格买入。它平滑了平均建仓价、免去
抄底择时的负担。朴素定投每批金额恒定，因此最大亏损被计划投入的总资金封顶。

马丁格尔是它危险得多的表亲。每亏一步就把下一单加倍（或按比例放大）以摊低成本，指望一
个小反弹就收复全部回撤。它诱人的性质是纸面胜率高：多数序列确实会反弹收正。它致命的性
质是仓位与所需资金呈几何级数增长，于是一段较长的逆势行情需要指数级增加的保证金。在持
续趋势里不断加倍摊平会爆仓——罕见的长连亏会一次抹掉此前多次小赢。

真正有用的风控：限制加仓（摊平）的最大步数；把首单规模设到即使满仓也仍在硬止损之内；
绝不让马丁格尔式加倍在没有回撤熔断的情况下运行。把任何"稳赚不赔"的加倍方案当作隐藏的
尾部风险，而不是免费的钱。
