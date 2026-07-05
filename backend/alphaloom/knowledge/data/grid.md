# Grid Trading — Mechanics / 网格交易机制

**Hand-written summary for AlphaLoom RAG. Original notes, not copied from any third-party corpus.**

## English

Grid trading places a ladder of buy and sell orders at fixed price intervals (the
grid spacing) around a reference price. As price oscillates, the strategy buys at
lower grid levels and sells at higher levels, harvesting the spread between
adjacent lines. It is a mean-reversion / range strategy: it profits from choppy,
sideways markets where price crosses the same levels repeatedly.

Key parameters are the grid spacing (distance between levels), the number of
levels above and below the anchor, the order size per level, and the upper/lower
bounds of the grid. Tighter spacing captures small oscillations but pays more
fees; wider spacing needs larger swings to trigger fills.

The core risk is a strong directional trend that breaks out of the grid bounds.
When price trends away and leaves the grid, the accumulated one-sided inventory
sits at a loss with no offsetting fills, and a trend that never reverts turns
paper losses into realized ones. Grids therefore pair well with a bounding stop
or a regime filter that disables the grid during strong trends.

## 中文

网格交易在参考价上下按固定价格间隔（网格间距）布置一梯队买卖挂单。价格来回震荡
时，策略在较低网格线买入、在较高网格线卖出，赚取相邻网格线之间的价差。它属于均值
回归 / 区间策略：在横盘震荡、价格反复穿越同一批价位的行情里获利。

关键参数是网格间距（相邻线的距离）、锚点上下的网格层数、每层下单量，以及网格的上下
边界。间距越窄能吃到越小的震荡但手续费更高；间距越宽则需要更大的波动才触发成交。

核心风险是强单边趋势突破网格边界。当价格趋势性地离开网格区间，累积的单边持仓无对手
成交对冲、被套在浮亏，一旦趋势不回归浮亏就变实亏。因此网格通常配合边界止损或在强趋势
期间关闭网格的市场状态过滤器一起使用。
