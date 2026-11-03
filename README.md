# European Electricity Dashboard

欧洲电力数据看板（静态页面，ECharts + Leaflet），数据由 GitHub Actions 定时更新。
本文档记录每个页面上每张图表的数据来源、计算公式和算法，方便核对口径。

- [目录结构与数据文件](#目录结构与数据文件)
- [通用约定](#通用约定)
- [Price 价格](#price-价格)：[price-overview](#price-overviewhtml) · [capture-price](#capture-pricehtml) · [price-spread](#price-spreadhtml) · [price-intraday](#price-intradayhtml) · [imbalance](#imbalancehtml) · [balancing-afrr-capacity](#balancing-afrr-capacityhtml)
- [Load 负荷](#loadhtml)
- [Generation 发电](#generation-overviewhtml)：[generation-overview](#generation-overviewhtml) · [generation-type](#generation-typehtml)
- [Installed Capacity 装机](#capacityhtml)
- [Project 项目](#projecthtml)
- [Hungary Operations 运营数据（独立权限）](#hungary-operations-运营数据独立权限)

---

## 目录结构与数据文件

```
html/        各页面（每个页面自带全部计算逻辑，没有后端）
py/          脚本
  fetch/entsoe/     ENTSO-E 抓取（fetch_entsoe / fetch_imbalance / fetch_capacity / repair_entsoe）
  fetch/enappsys/   EnAppSys(Montel) 抓取（IDA / ID3）
  fetch/mavir/      MAVIR 不平衡数据抓取
  build/            由已有数据生成宽表 / 合并表（build_15min / build_hu_combined）
  analysis/         tokaj_imbalance_price_calculation.py（Tokaj 五站预测→报量→不平衡价格计算，读 vcom 仓库数据）
  tools/            一次性维护工具（fix_csv）
  tests/            单元测试
data/
  entsoe/    ENTSO-E 数据（按小时）
    price.csv  load.csv  wind.csv  solar.csv  residual_load.csv  imbalance_price.csv  capacity.csv
    generation/<国家>.csv       发电结构（长表：date,category,value）
    15min/{price,solar,wind}.csv  由 raw_data 生成的 15 分钟宽表（py/build/build_15min.py）
  enappsys/  Montel / EnAppSys 数据（按年份拆分，避免单文件过大）
    IDA/HU_<年>.csv   HUPX 日内拍卖 IDA1/2/3 价格、成交量，15 分钟
    ID3/HU_<年>.csv   ID3 指数，列 Date, ID3, Vol（制表符分隔，日/月/年）；每天由 py/fetch/enappsys/fetch_enappsys_id3.py 接着往下更新（`update_enappsys.yml` 手动运行时可勾选 `ida_only` / `id3_only` / `afrr_capacity_only` 只更新其中一个）
    afrr/capacity/<up|down>/15min/HU_<年>.csv   匈牙利 aFRR 容量，7 列：WAP(hourly/daily/monthly)、Volume(hourly/daily/monthly)、Total volume（无成交 / 无数据写 "-"）；由 py/fetch/enappsys/fetch_enappsys_afrr_capacity.py 更新
  fx/MNB.csv  匈牙利央行汇率（HUF per EUR / RON / PLN，工作日）
  mavir/imbalance/HU_<年>.csv  MAVIR System direction（及 10 月起的 preliminary 全部列），15 分钟；每天由 `update_mavir_imbalance.yml` 更新
  （HU 整合表不放在本仓库：`py/build/build_hu_combined.py` 生成，`build_combined.yml` 在上面几个数据 workflow 跑完后重建并推送到只含这一个文件的私有仓库 `hanyu-jian/hu-market-data`。同事用只授权该仓库的只读 token 通过 GitHub contents API 取：
    `GET https://api.github.com/repos/hanyu-jian/hu-market-data/contents/HU_2026.csv`，头 `Authorization: Bearer <token>`、`Accept: application/vnd.github.raw+json`；本仓库需要 secret `DATA_REPO_TOKEN`）
raw_data/<年>/  ENTSO-E 15 分钟原始数据（A44 日前价、A65 负荷、A85 不平衡价、generation/）
```

| 文件 | 内容 | 粒度 | 单位 |
|---|---|---|---|
| `entsoe/price.csv` | 日前电价，列=国家 | 小时（15 分钟原始数据取小时均值） | €/MWh |
| `entsoe/load.csv` | 负荷 | 小时 | MW |
| `entsoe/solar.csv` / `wind.csv` | 光伏 / 风电（风电=陆上+海上） | 小时 | MW |
| `entsoe/residual_load.csv` | 剩余负荷 = 负荷 − 光伏 − 风电 | 小时 | MW |
| `entsoe/imbalance_price.csv` | 不平衡价 | 小时 | HU 在 2026-09-30 及以前为福林 HUF/MWh，2026-10-01 起为 €/MWh，其他国家为 €/MWh |
| `entsoe/generation/<国家>.csv` | 分技术发电 | 小时 | MW |
| `entsoe/capacity.csv` | 分技术装机容量 | 年 / 月 | MW |
| `entsoe/15min/*.csv` | 价格、光伏、风电 | 15 分钟 | €/MWh、MW |

覆盖国家（13 个）：AT BG DE ES FR GR HR HU IT PL RO RS SK。时间轴为 CET/CEST（Europe/Brussels），与 ENTSO-E 网站一致。

## 通用约定

- **均值**：所有"平均"都是对可用数据点的简单算术平均，缺失值（空、NaN）直接跳过，不补零（capture-price 的发电量缺失按 0 处理，见该页）。
- **聚合周期 freq**：`hourly`（YYYY-MM-DD HH:00）、`daily`、`weekly`（ISO 周，键为 `YYYY-Www`）、`monthly`、`quarterly`（`YYYY-Qn`）、`yearly`。
- **小时标签**：热力图的 `H01–H24` = 小时序号 0–23 加 1，即 H01 对应 00:00–01:00。
- **CSV 导出**：每个图表的 Export CSV 导出的是当前屏幕上用的那份数据。
- **地图配色**：对所选时段内各国数值做 min–max 线性归一化后着色。
- **热力图配色**：每个年份单独取 min / 中位数 / max，分段插值（蓝 → 浅蓝 → 浅黄 → 橙 → 红），中位数对应中间色。

---

## price-overview.html

数据：`entsoe/price.csv`（小时价）。国家可多选，年份、月份由顶部下拉选择。

1. **Price Trends（价格走势）**
   - 对所选年份、所选国家，按 freq 分组：`均价(组) = Σ小时价 / 小时数`。
   - 每个国家一条线，固定配色。
2. **Hourly Average（月内小时均价）**
   - 所选年月内，对每个小时 h（0–23）取该月所有日的均值：`P̄(h) = mean over days of P(day, h)`。
3. **Year-over-Year（同比月均价）**
   - 单个国家，对 2024 / 2025 / 2026 每年每月取小时价均值（保留 2 位小数），三年叠在同一条 1–12 月轴上。
4. **Price Difference（价差）**
   - 先算每个国家每月的小时价均值 `M_c(月)`，从 2024-01 到当前月。
   - 最多 3 组国家对 (A, B)：`差(月) = M_A(月) − M_B(月)`，任一边缺数据则该月为空。默认 HU−DE、HU−IT。
5. **地图**
   - 所选年月各国小时价均值，白色（低）→ 红色（高）线性渐变。

## capture-price.html

数据：`entsoe/15min/price.csv`、`solar.csv`、`wind.csv`（均为 15 分钟，每个点代表 0.25 小时）。
价格缺失的时刻整点跳过；发电量缺失按 0 处理。

**逐点定义**（价 `p`，发电量 `g`，MW）：

| 量 | 公式 |
|---|---|
| 限电后发电量 `g_post` | `p > 0 ? g : 0`（价格 ≤ 0 的时段视为限电） |
| 限电前收入 `rev_pre` | `g × p` |
| 限电后收入 `rev_post` | `g_post × p` |
| 负价时长 | `p < 0` 时记 0.25 h |
| 非正价时长 | `p ≤ 0` 时记 0.25 h |

**周期聚合**（对周期内所有 15 分钟点求和后再相除）：

| 指标 | 公式 |
|---|---|
| 基荷价 Baseload | `Σp / N`（所有有价格的点简单平均） |
| 捕获价（限电前）Capture Price pre | `Σ(g × p) / Σg`，即发电量加权均价 |
| 捕获价（限电后）Capture Price post | `Σ(g_post × p) / Σg_post` |
| 捕获率 Capture Ratio | `Capture Price / Baseload × 100%`，分 pre / post |
| 限电率 Curtailment | `(Σg − Σg_post) / Σg × 100%` |
| 日均非正价小时 | `非正价小时数 / 周期内天数` |

图表：

1. **Overview（概览）**：灰色柱=基荷价；橙线=光伏 Capture Price（pre）；蓝线=风电 Capture Price（pre）。切换 Ratio 模式后线改为捕获率（右轴 %）。可选 freq。
2. **Deep Dive（深入）**：选光伏 / 风电、选年份。上半部分=基荷价（柱）、Capture Price post（实线）、pre（虚线）；下半部分=非正价小时数（柱）。
   - Daily 只画所选年份；Weekly / Monthly / Quarterly / Yearly 画全部年份。
   - Daily 的小时轴固定 0–24（每天最多 24 小时）。
3. **欧洲地图（两张）**，周期可选 年 / 季度 / 月：
   - 左：所选能源的 Capture Price post（€/MWh）；Ratio 模式下为 `Capture Price post / 该国同周期基荷价 × 100%`。
   - 右：累计负价（或零价）小时数。阈值可选 `Price < 0` 或 `Price = 0`，两者都选时为 `Price ≤ 0`。数值就是满足条件的 15 分钟点数 × 0.25。
4. **汇总表 Summary Table**：选国家、光伏 / 风电，行=1–12 月、全年、自定义月份区间（起止月，若起始月大于结束月则按同一日历年内 `月 ≥ 起 或 月 ≤ 止` 处理），列按年份展示：
   - 限电率、Capture Price post / pre、基荷价、Capture Ratio post / pre、负价（或非正价）小时数。
   - 表上的阈值 `Price < 0` / `Price ≤ 0` 决定哪些点算"限电"，只影响 post 相关指标和限电率；pre 指标与阈值无关。
   - Capture Ratio 颜色：≥ 85% 绿，< 60% 红，其余默认。

## price-spread.html

数据：`entsoe/price.csv`。页面包含 2024–2026 三年。

1. **价格热力图**：单国家，每年一块，行=小时（H01–H24），列=月份，格值=该年该月该小时的所有日的小时价均值。
2. **Price Spread（峰谷价差）**
   - 参数 `N peak`、`N valley`（默认 1，限制在 1–23）。
   - 每一天：把当天所有小时价从小到大排序，
     - `valley(day) = 最低 N valley 个价格的均值`
     - `peak(day) = 最高 N peak 个价格的均值`
     - 当天价格点少于 N 时（如夏令时当天有 23 小时）取 `min(N, 点数)`。
   - 周期汇总（月 / 季度 / ISO 周 / 日 / 全年 Annual）：
     - `peak = mean(日 peak)`，`valley = mean(日 valley)`，`spread = peak − valley`，即周期内日价差的平均。
   - 标签页：Monthly（12 个月 + Annual）、Quarterly（Q1–Q4 + Annual）、Weekly（ISO 周号 W01… + 日期范围 + Annual）、Daily（逐日期 + Annual）。每个周期分别列出三年的 peak / valley / spread，方便同比。
   - 折线图画各年 spread，不含 Annual 行；数据点超过 30 个时省略数据标签。

## price-intraday.html

数据：`enappsys/ID3/HU_<年>.csv`（每行 15 分钟，`ID3` 指数 €/MWh）和 `entsoe/15min/price.csv` 的 `HU` 列（日前价 DA，€/MWh），仅匈牙利，两者按时间戳对上；任一边缺值的时段跳过。ID3 数据每天由 `update_enappsys.yml` 抓取（Montel / EnAppSys Chart API，`hu/elec/hupx/indexes/id3/version2`），接着现有表格往下写。

1. **HUPX Intraday Auctions（IDA1 / IDA2 / IDA3 价格与成交量）**，数据：`enappsys/IDA/HU_<年>.csv`（15 分钟）。选年份、频率（15min / Hourly / Daily / Weekly / Monthly，周以周一为起点；小时 = 该小时 4 个 15 分钟点的平均）。默认 15min，年份默认数据中最新的一年，图上默认显示该年最近一个月，拖动下方缩放条可看更早的数据。
   - **柱（灰色，左轴 MW）**：各 IDA 成交量；**折线（右轴 €/MWh）**：各 IDA 价格。
   - **红线（右轴）**：日前价 DA（同文件 `DA Price` 列），始终显示，不受 Threshold 按钮影响；同样按所选频率取 15 分钟点的平均。CSV 里也包含 DA 列。
   - **Threshold** 按钮：All / IDA1 / IDA2 / IDA3，选某一个时只显示该 IDA 的价格和成交量（CSV 同样只导出所选）。
   - 周期值 = 价格和成交量都有值的 15 分钟点的算术平均（量为平均功率 MW），没有数据则留空。
   - Export CSV 导出当前年份/频率下的价格、成交量及参与平均的点数。

2. **ID3 − DA 价差热力图（H01–H24）**
   - 每个 15 分钟点：`spread = ID3 − DA`。
   - 格值 = 该年该月该小时内所有 15 分钟 spread 的算术平均（每小时 4 个点，缺 ID3 或 DA 的点跳过）。
3. **Arbitrage DA vs ID3（储能套利，事后最优）**，选年份、起止月。策略固定：
   - **充电**：03:00–05:00（小时 3、4）的 DA 价。`DA_charge = 这两个小时内所有 15 分钟 DA 价的均值`。
   - **DA 放电**：17:00–21:00 四个小时（17、18、19、20），先算每小时 DA 均价，取最高的 2 个小时再平均：`DA_discharge = mean(top2(小时 DA 均价))`。
   - **ID3 放电**：同样 17:00–21:00 共 16 个 15 分钟点，取 ID3 价最高的 8 个再平均：`ID3_discharge = mean(top8(ID3))`。
   - 每日结果（€/MWh，按每 MWh 能量计）：
     - `netDA = DA_discharge − DA_charge`
     - `netID3 = ID3_discharge − DA_charge`
     - `uplift = ID3_discharge − DA_discharge`
   - 缺少充电时段或放电时段数据的日子整天剔除。
   - **月度柱图**：各月 `Σ 日 netDA`（蓝柱）与 `Σ 日 netID3`（橙色叠加部分是 `netID3 − netDA`，两者之差）。Y 轴从 0 起。
   - **KPI**（对所选月份范围内所有有效日）：
     - Total Uplift = `Σ 日 uplift`（€/MWh，每日价差累加）
     - Monthly Avg Uplift = `Total Uplift / 有数据的月份数`
     - ID3 Premium (%) = `(Σ netID3 − Σ netDA) / |Σ netDA| × 100`

## imbalance.html

数据：`entsoe/imbalance_price.csv`（小时）、`raw_data/<年>/A85.csv`（15 分钟原始不平衡价）、`entsoe/price.csv`、`solar.csv`、`wind.csv`、`load.csv`、`fx/MNB.csv`。

**币种换算（MNB 月均汇率）**：先对 `MNB.csv` 每个自然月取 EUR、RON 两列的算术平均（含义是 1 EUR / 1 RON 折合多少福林）。
- HU：`EUR/MWh = HUF/MWh ÷ 当月 EUR 汇率`
- RO：`EUR/MWh = RON/MWh × 当月 RON 汇率 ÷ 当月 EUR 汇率`
- 其他国家本身就是 €/MWh，不换算。
- 小时数据和 A85 15 分钟数据使用同一套换算。
- **例外**：HU 从 2026-10-01 起加入 PICASSO，ENTSO-E 上的 HU 不平衡价已经是 €/MWh，所以 2026-10-01 及以后的 HU 数据不再做汇率换算（RO 不受影响）。

**过滤器**（趋势图和对比图共用）：工作日（周一至周五）/ 周末（周六、周日）勾选，小时区间 `[起, 止]`（含两端）。按日期判断星期。

图表：

1. **A85 散点图**：所选国家的所有 15 分钟不平衡价（每天 96 个点，不做平均）。价格范围过滤只显示区间内的点，并同步限制 Y 轴。
2. **月度箱线图**：对每个国家每个月，取该月所有 15 分钟 A85 价（换算为 €/MWh）：
   - 箱体 Q1–Q3，中线=中位数；须线 = 第 5 与第 95 百分位；须线以外的点画成空心圆（离群点）。
   - 分位数用线性插值：`位置 = (n−1)·p`，在相邻两个值之间插值。
   - 价格范围过滤只改 Y 轴显示范围，不影响分位数计算。
3. **Imbalance 价格趋势**：所选国家的不平衡价，按 freq 对过滤后的小时价求均值。
   - 虚线（右轴）=可再生占比 `(Solar + Wind) / Load`：先对同一周期内三个序列各自求均值，再相除：`ratio = (mean(solar) + mean(wind)) / mean(load)`。load 缺失或为 0 时该周期为空；solar、wind 缺失按 0 处理。
   - X 轴从 2024（或数据最早年份）起补全到当前月，没数据的周期留空；背景按年份交替着色。
4. **Imbalance vs Day-Ahead**：单个国家，不平衡价与日前价（`price.csv`）在同样的 freq 与过滤条件下的均值对比。

## load.html

数据：`entsoe/load.csv`（小时，MW）。

1. **7 日移动平均（7-Day Moving Average）**，单国家，按年份画线：
   - X 轴按 2024（闰年）日历，非闰年的 2 月 29 日留空，保证各年同一日期对齐。
   - 过滤：工作日 / 周末勾选、小时区间。
   - `日均(日) = 该日通过过滤的所有小时负荷的均值`。
   - `7日均(日) = 最近 7 个有效日均值的平均`（"有效"指该日有数据，所以过滤后不会断线；不满 7 个有效日时为空）。
   - 标出每年 7 日均值的最大、最小点。
   - 右侧 **Load Averages**：
     - 全年（只列已完整的年份）：`年均 = mean(日均)`，同比 `(本年 − 上年) / 上年`。
     - YTD：以最新一年最后一个有数据的日期为截止，各年都取 1 月 1 日到该日的日均均值，保证同期可比。
2. **Hourly Load Trends**：单国家，三年按月日小时逐点叠加（X 轴用 2024 年的日历）。
3. **Monthly Average Load**：单国家，各年每月所有小时负荷的均值。
4. **Hourly Average**：所选月份内，各年每个小时（0–23）负荷的均值。
5. **地图**：所选年月各国负荷均值（MW），浅黄 → 橙红渐变。

## generation-overview.html

数据：`entsoe/generation/<国家>.csv`、`entsoe/load.csv`、`entsoe/price.csv`。国家可多选，`CEE` = HU + RO + RS + HR + PL + BG。起止月份可选，三个年份并排对比。

1. **Load vs Generation by Hour（全天 24 小时平均曲线）**
   - **发电**：对每个时间点先把所选国家各技术的发电量相加，再按小时 h（0–23）对所选月份区间内所有日求平均 → 每种技术一条堆叠面积。
     分类 `Load`、`Renewable share` 不计入发电。
   - **Load**（黑线）：所选国家每个时间点负荷相加，再按小时求平均。
   - **Price**（右轴，红色虚线）：每个时间点先对所选国家价格求简单平均（不加权；`CEE` 在 price.csv 中没有这一列，所以按它展开后的真实国家计算），再按小时求平均。
   - 图例可开关；左轴最大值取三年中最高堆叠值与 Load 的最大值，向上取整到 1000 并留 5% 余量；右轴价格范围取三年最小、最大值再各加 `max(15%×区间, 10)`，取整到 10。
2. **Annual Generation Structure（年度发电结构，两个环形图）**
   - 每种技术：`平均小时功率 = 24 个小时均值的平均`（取全年 1–12 月），`年发电量 MWh = 平均小时功率 × 24 × 天数`。
   - 天数：2024 为 366，2025 为 365；**2026 取实际有数据的天数**（年初至今），所以是年累计而非全年预测。
   - 左图=分技术；右图按三类合并：
     - Nuclear：名称含 `nuclear`
     - Fossil：名称含 coal / gas / oil / peat / fossil / `waste non`
     - Renewable：其余（含 Hydro、Wind、Solar、Biomass、Geothermal、Waste、Other 等）
   - 中心文字为合计 TWh（MWh ÷ 10⁶）。

## generation-type.html

数据：`entsoe/generation/<国家>.csv`、`entsoe/load.csv`。选国家和技术类型（匹配规则见下）。

- **类型匹配**：类别名转小写并把 `-`、`/` 换成空格后做子串匹配。Lignite 同时匹配 `lignite`、`brown coal`；`Other` 只匹配完全等于 `other` 的类别（不含 Other Renewable）；`Other Renewable` 匹配含 `other renewable` 的类别。
- **月发电量**：`GWh = Σ 该月所有小时的 MW 值 ÷ 1000`（发电文件是小时数据，一个小时的 MW 就等于该小时的 MWh）。负值和无效值剔除；某月没有任何数据则为空。
- **柱图**：2024 / 2025 / 2026 三年 12 个月并排，同一类型三年用深浅不同的同色系。
- **YoY 表**：`(B − A) / A × 100%`，A 为去年同月，B 为今年同月；任一月空则为空，去年为 0 则显示 N/A。表中对比 2025/2024、2026/2025。
- **Load vs Generation Summary**（受起止月份控制）：
  - `发电量 TWh = Σ 月 GWh ÷ 1000`（所选月份区间）
  - `负荷 TWh = Σ 所选月份的小时 MW ÷ 10⁶`
  - `Share = 发电量 / 负荷 × 100%`
  - Load YoY、Gen YoY = 与上一年同一月份区间相比的变化率。
  - 2026 年数据不完整，比较时请把月份区间限制在有数据的月份内。

## capacity.html

数据：`entsoe/capacity.csv`（TSO 申报的装机容量，≥ 1 MW 的机组按类型汇总）。值为 `n/e` 或空的行剔除。

1. **地图**：所选年份各国总装机 = 该年度行（月份为空的行）中所有技术装机相加，GW = MW ÷ 1000。
2. **堆叠柱图**：所选国家，起止期间内每个期间（年度或月度行）一根柱，按技术堆叠；技术按期间合计从大到小排序；顶部标注当前可见技术之和。
3. **详情面板**：区间内最后一个有数据的期间，按装机从大到小列出各技术。
4. **2030 NECP 预测表**：
   - 取区间内最后一个有数据的期间。光伏 = 含 `solar` 的类型；风电 = 陆上（含 wind 和 on）+ 海上（含 wind 和 off），两者都为 0 时退回所有含 `wind` 的类型。
   - `装机占比 = 该技术装机 / 全部技术装机之和 × 100%`。
   - 2030 年的装机（GW）和占比是写死在页面里的各国 NECP 目标值（`FORECAST_2030`），不是计算结果。

## project.html

项目清单，数据写死在页面里（`projectData`），没有从 CSV 读取。

- 国家总装机：`Σ 该国项目 capacity（MW）`。
- 汇总栏：按类型（光伏、风电等）汇总容量；运营中 / 在建分别累计容量和项目数。
- 每个项目卡片展示容量、COD、电价机制、PPA 等文字信息。

## index.html

首页，仅三个入口：Price、Load、Installed Capacity。

---

## balancing-afrr-capacity.html

匈牙利 aFRR 容量（Balancing 下，Imbalance Prices 后面的 aFRR Capacity 页）。数据：`enappsys/afrr/capacity/<up|down>/15min/HU_<年>.csv`（15 分钟，来源 EnAppSys，每天由 `update_enappsys.yml` 更新）。

- 控件：Year、Direction（Up / Down）、Frequency（15min / Hourly / Daily / Weekly / Monthly，周以周一为起点）、Product（Total / Hourly / Daily / Monthly）。默认 15min，年份默认数据中最新的一年，图上默认显示该年最近一个月，拖动下方缩放条可看更早的数据。
- 图：柱 = 容量（MW，左轴），线 = 加权平均价 WAP（€/MW/h，右轴）。Product = Total 时，柱为 hourly / daily / monthly 三个产品的堆叠（顶部即 Total volume），并画三条 WAP 线；选单个产品时只画该产品的容量和 WAP。
- 周期值 = 该周期内 15 分钟点的简单平均（价格和容量各自只对有值的点求均值）。
- Export CSV 导出当前图表所见的序列。


---

## Hungary Operations 运营数据（独立权限）

入口：`project.html` 里点 TOKAJ 卡片 → `/ops/overview`（文件 `ops/ops-overview.html`）。页面全英文，样式和主看板一致：共用 `html/nav.js` 的头部（运营会话通过 `/ops/nav.js` 取到同一份），`/ops` 页面的一级导航是单独的 TOKAJ 标签（`nav.js` 里的 `OPS_NAV_ITEMS`），二级导航是 Overview / Capture Price；点左上角标题 “European Electricity Dashboard” 回到看板。

**新增运营页面**：① 建 `ops/ops-<name>.html`（页首加 `<script src="/ops/nav.js"></script>`，并写好二级导航 `.sub-nav`）；② 把 `<name>` 加进 `gate/worker.js` 的 `OPS_PAGE_NAMES`；
③ 在每个运营页的 `.sub-nav` 里加一项（目前有 Overview 和 Capture Price）。对外地址是 `/ops/<name>`。

**权限**：和看板完全分开。`gate/worker.js` 里 `/ops` 开头的一切（含大小写 / 重复斜杠 / 编码变体）只认
`OPS_TOKENS`（Secret，`名字:token` 逗号分隔），登录页 `/ops/login`，Cookie `ops_token` 只在 `/ops` 路径下发送，`/ops/logout` 退出。
普通 `DASHBOARD_TOKENS` 进不去 `/ops`，运营 token 也进不去普通页面；`OPS_TOKENS` 没配置时 `/ops` 返回 404，不影响其它页面。

**数据**：页面请求 `/ops/api/production?site=&start=&end=&resolution=15min|hourly`，gate Worker 在服务端用 Secret `GITHUB_TOKEN`
（fine-grained、只授权 `vcom` 仓库、Contents 只读）直接读私有 vcom 仓库的
`data/theoretical production` 和 `data/production forecast`，按时间对齐后返回 JSON（列：`theoretical_kwh, da, ida1, ida2, ida3, id3_16_20, id3_11_15`）。
另有两个接口给 Capture Price 页用（都只认运营 Cookie）：`/ops/api/theoretical?site=&year=` 原样转发 vcom 仓库整年的 15 分钟理论发电量 CSV（不解析，省 Worker CPU，边缘自动压缩）；
`/ops/api/price` 转发本仓库 `data/entsoe/15min/price.csv`（运营 Cookie 只在 `/ops` 路径下发送，页面直接取 `/data/…` 会被拦）。
token 只在服务端，不会发给浏览器；数据不会进入本仓库，也不在公开静态文件里。

**部署清单**（Cloudflare → `energy-dashboard` → Settings → Variables and secrets，类型选 Secret）
1. `OPS_TOKENS`：运营口令，`名字:token`，多个用逗号隔开。
2. `GITHUB_TOKEN`：只读 token。
3. （可选）变量 `GITHUB_REPO`，默认 `hanyu-jian/vcom`。

**Overview 页面口径**
- 蓝线 Theoretical production = VCOM POA 算出的理论发电量（含 AC 限值、不含电网限电），不是电表实测。
- Production forecast：DA 是基准预测，IDA1 → IDA2 → IDA3 → ID3(11-15h) → ID3(16-20h) 是对它的逐次修正；
  某个时段某次修正没有值，就沿用上一版。橙线 = DA 叠加到所选版本为止的所有修正（默认 Latest），灰色虚线 = DA。
  叠加顺序在 `ops-overview.html` 的 `CHAIN`，与 `py/analysis/tokaj_imbalance_price_calculation.py` 的 S/T/U/Y/Z 逻辑一致。
- 汇总表只统计「理论发电量」和对应预测同时有值的时段；Abs. error = Σ|预测 − 理论| / Σ理论。
- 以后做捕获率 / 捕获价格 / 限电率：在 `/production` 里加 `metered_kwh`，价格用本仓库 `data/entsoe`、`data/enappsys`，限电率 = (理论 − 实测) / 理论。

**Capture Price 页面口径**（`ops-capture-price.html`，目前只有一张图）
- 图 = `html/capture-price.html` 里 “Solar Capture Price – HU” 那张 Deep Dive 图的复刻（Daily / Weekly / Monthly / Quarterly / Yearly；Daily 选年份，其余画全部年份；Price / Ratio 切换；Export CSV），没有风电。
- 顶部 All sites / 五个场站按钮。All sites = 五站理论发电量逐 15 分钟相加（某站该时段无值按 0）。
- 发电量用 vcom 的 15 分钟理论发电量（kWh），电价和基荷价用 `entsoe/15min/price.csv` 的 `HU` 列，按时间戳（布达佩斯当地时间、区间起点）对齐；公式与 capture-price 页完全一致：
  `Capture Price pre = Σ(g·p)/Σg`，`post` 把价格 ≤ 0 的时段发电量记 0，`Ratio = Capture Price / Baseload`，非正价时长 = 价格 ≤ 0 的点数 × 0.25 h，基荷价 = 价格简单平均。
- 基荷价用所选周期内全部 15 分钟价格点（含场站投运前，例如 2024 年用整年）；捕获价只用有发电量的时段（缺发电量按 0，权重为 0）。
- 下方 Summary by site：五个场站 + Total，选年份（All years / 单年）、月份区间（起 > 止时按「月 ≥ 起 或 月 ≤ 止」）、阈值（Price < 0 默认 / Price ≤ 0，与 capture-price 页汇总表一致）。
  列：理论发电量 MWh、经济性限电量 MWh（价格触发阈值的时段的理论发电量）、限电率 = 限电量 / 理论发电量、基荷价、Capture Price（post）、Capture Ratio（post）= Capture Price / 基荷价（≥ 85% 绿，< 60% 红）。Total 为五站相加。
  注意图里的 post 固定按 Price ≤ 0 限电，表里阈值可切换。
