# 「静默忽略」家族专项排查（2026-08-27）

> 触发原因：同一族失败在三个月内咬了**三次**，按 `CLAUDE.md` C-6「连环吸取」做一次系统性排查。
> 性质：**只读审计，本次不改代码**（C1 面板对比正在 5434 上跑，期间禁改 `backend/src`）。
> 去向：见 §5。

---

## 1. 什么叫「静默忽略」

代码**接受了**一个参数 / 字段 / 配置，却**从不消费它**；调用不报错、结果看起来正常，
只是那个旋钮拧了没反应。共同特征是——**能跑过任何只验证「不抛异常」的测试**。

已知的三个实例：

| # | 实例 | 表现 | 发现时间 |
|---|---|---|---|
| 1 | pandas_ta 0.4.x 把 `bbands(std=)` 拆成 `lower_std=` / `upper_std=` | 旧参数名落进 `**kwargs` 被静默忽略，`mean_reversion` 的 `std=2.0` 长期无效（只因默认值恰好也是 2.0 才没出事）| C1 实施期 |
| 2 | Tushare `balancesheet` 对逗号多码 | 静默返回空 DataFrame，不报错 → `refresh_financials_full` 的 `success` 照常递增而 `total_equity` 永不写入 | 2026-08-10（total_equity 第 6 号 bug）|
| 3 | Tushare `fina_indicator` 被索要 `total_share` | 不报错、也不返回该列，返回列只有 `['ts_code','end_date']` | 2026-08-27 C2 前置真调 |
| 4 | Tushare `hk_hold` 逗号多码 | 静默返回 0 行（单码 1 行），不报错 | 2026-08-28 C4 前置真调 |
| 5 | **Tushare `moneyflow_hsgt.north_money` 语义变更** | **字段名 / 单位 / 供数全不变，含义从「净流入」变成「成交额」**——2024-08-16 还有负值，2024-08-19 起再无负值且量级跳约 100 倍（同一次沪深港通披露改革）。按年负值占比：2021 34.2% / 2022 47.9% / 2023 51.5% / **2025 0.0% / 2026 0.0%** | 2026-08-28 C4 前置真调 |

> 第 5 例是本族**最毒的形态**：前四例至少「拿不到值」，尚有 NaN 可循；这一例**照常每天给你一个数**，
> 只是不再是你以为的那个量。跨 2024-08-19 做任何特征工程都会得到无意义的结果，而且**没有任何信号**
> 提示你出了问题。**判据只能是分布检验**——一个"净流入"序列若长期零负值，本身就是矛盾。
> 新接入任何外部时间序列时，应先看**符号/量级的分年分布**，而不只是"取到数了吗"。

---

## 2. 排查方法与其有效性

### 路径 1：配置项定义了但没人读 —— **有效**

把 `core/config_defaults.py` 各 dataclass 字段逐个在 `src/quantpilot/` 全仓检索
（排除定义文件自身），列出零引用项。73 个字段中 **12 个零引用**，见 §3。

### 路径 2：adapter `fields=` 请求 vs 下游消费 —— **无效，全假阳性**

抽出 `data/adapters/tushare.py` 中 10 处 `fields="..."` 字面量，逐字段检索"在
tushare.py 之外是否被使用"，得到 8 个疑似项：`list_status` / `dv_ttm` /
`netprofit_yoy` / `tr_yoy` / `debt_to_assets` / `p_change_min` / `p_change_max` /
`n_income` / `yoy_net_profit` / `total_hldr_eqy_exc_min_int` / `cash_div_tax`。

**逐个核实后全部证伪**——它们都在 `tushare.py` **内部**就被重命名 / 派生后才出库
（例如 `netprofit_yoy` 写库前改名、`total_hldr_eqy_exc_min_int` 派生为 `total_equity`）。

**记录此路径失效，避免下次重做**：本代码库的 adapter 有"入口即改名"的惯例，
"请求了但外部没引用"这一静态判据在此不成立。要覆盖这一族，得靠**运行期断言**
（该字段真的有值 / 真的影响结果），不是静态检索。

---

## 3. 路径 1 的发现（12 项）

| 配置类 | 零引用字段 | 代码实际用的是什么 |
|---|---|---|
| `FactorMonitorConfig` | `ic_window`、`ic_alert_threshold`、`half_life_window`、`ic_window_days`、`icir_lag_days`、`icir_warmup_days`、`state_min_samples`、`ic_bootstrap_iterations`、`half_life_window_days`（9 个）| `services/factor_monitor_service.py` 顶部的**同值模块级常量**：`_ICIR_WINDOW_DAYS=252` / `_ICIR_LAG_DAYS=20` / `_ICIR_WARMUP_DAYS=272` / `_STATE_MIN_SAMPLES=60` / `_BOOTSTRAP_ITERS=1000` |
| `TrendStrategyConfig` | `macd_fast`、`macd_slow`（+ `macd_signal`，见 §4）| `engine/strategies/trend.py:68` 写死 `ta.macd(close, fast=12, slow=26, signal=9)` |
| `ScoringPipelineConfig` | `hysteresis_enabled` | 无人读取 → Hysteresis 恒开，开关是装饰品 |

### 3.1 严重性：这些配置**对用户可编辑**

三者都经 `services/config_service.py` 暴露（`get_factor_monitor_params` /
`get_scoring_pipeline_params` / `get_strategy_params_trend`），且 `api/v1/settings.py`
的 key 列表里含 `factor_monitor_params`、`scoring_pipeline_params`。

**后果**：用户在设置里改 `state_min_samples`、或关掉 `hysteresis_enabled`，
**值会存进库、界面显示已保存、而代码永远不读**。这是本族里最严重的形态——
面向用户的旋钮，拧了没反应，且不报错。

### 3.2 两条限定（避免夸大）

1. **MACD 那条已有【降级说明】**：`trend.py` docstring 明写「V1.0 因子内部的 rolling
   窗口与 MACD 参数仍硬编码」。属**已知降级**而非新缺陷；但配置字段仍暴露给用户，
   等于"文档里认了、界面上没认"。
2. **`FactorMonitorConfig` 当前数值一致**：常量与配置默认值逐个相同（252/20/272/60/1000），
   代码注释也写着「与 SDD v1.4 / config_defaults FactorMonitorConfig 对齐」。
   **今天的行为是对的**；风险在于（a）用户侧修改自始至终无效，（b）任一侧被改就静默分叉。

---

## 4. 审计方法自身的盲点（必须记下）

`macd_signal` **没有**出现在零引用名单里，但它同样是写死的。原因是
`trend.py` 里有个**同名的因子权重键** `"macd_signal"`（`weights = {..., "macd_signal": 0.30, ...}`）
撞了名，纯 grep 判据据此误判为"已被消费"。

**所以写死的 MACD 参数实际是 3 个，不是 2 个。**

判据教训：**按名字检索的审计，遇到同名的无关标识符会产生假阴性**——假阴性比假阳性更危险，
因为它不会被复核。对关键项应改用"改参数 → 结果必须变"的运行期断言。

---

## 5. 处置

**本次不改代码**（面板期禁改 `backend/src`），且**不应混入 C1 上线批次**——C1 三个改动
需要独立观察窗口。建议顺序：C1 上线并观察 ≥3 个交易日 → 再单独出这批修复。

修复方向（供实施时参考，非设计定稿）：

1. `factor_monitor_service` 改为**从注入的 `FactorMonitorConfig` 取值**，删掉平行常量；
   或反过来——若确认这些值不应由用户调整，就**把字段从用户可编辑的配置里摘掉**，
   两者取其一，不能维持现状。
2. `trend.py` 的 MACD 三参数按 docstring 里【降级说明】写的"Phase 11+ 参数化"落实；
   落实前至少在配置字段旁标注当前无效。
3. `hysteresis_enabled` 要么接线、要么移除。
4. **每项修复都必须配一条「改参数 → 结果变」的单测**（同 `CLAUDE.md` §4.4 对 pandas_ta
   那条的判据），否则修完还是可能悄悄失效。

登记去向：`v1_post_release_roadmap.md` §6 **V1.5-F 通知与配置**（该主题本就含"配置版本"）。

---

## 6. 2026-09-07 实施补记：本报告的计数偏低了，且漏了更严重的一层

实施 F-SI 时按本报告逐项接线，过程中发现两处本报告自身的不足。**不是苛责当时的排查**
——两处都是本报告 §4 已经预言的盲点，只是当时只找到了一个实例。

### 6.1 零引用字段是 **14 项**，不是 12 项

`TrendStrategyConfig.ma_short` / `ma_long` **同样零引用**，逃过排查的原因与
`macd_signal` **完全相同**：`MarketStateConfig` 有同名字段且被 `market_state.py:65`
消费（`self.ma_short = cfg.ma_short`），纯 grep 判为「已引用」。

即 §4 记的那条盲点（**按名字检索会因同名标识符产生假阴性**）在同一份报告里
**命中了两次**，而当时只发现一次。假阴性不会被复核——这就是它的代价。

⚠️ 这两项的接线方式需要设计决策，本批**未接**：`ma_alignment` 因子用的是
5/10/20/60 **四档 MA 阶梯**，两个配置字段表达不了；按默认值把 `ma_short→20` /
`ma_long→60` 对上去是猜，而 5/10 仍然写死，属"文档里认了一半"。

### 6.2 更严重的一层：**整个 config_key 从未被读取**（6/12）

本报告数的是「类内字段无人引用」。但还有一个更粗的口径没被检查过：
**`ConfigService` 的 getter 本身有没有人调用**。实测（`src/` + `scripts/`，AST 计数）：

| config_key | 生产消费者 |
|---|---|
| `signal_params` / `risk_limits` / `universe_params` / `backtest_defaults` / `notification_prefs` / `factor_monitor_params` | ≥1 ✓ |
| **`market_state_params`** | **0** |
| **`strategy_weights`** | **0** |
| **`strategy_params_trend` / `momentum` / `mean_reversion` / `value`** | **0** |

`api/v1/settings.py::_VALID_CONFIG_KEYS` 的 **12 个 key 里 6 个零消费者**。
后果比字段级严重：用户改四个策略的**任何**参数、改市场状态阈值、改策略权重，
全部存库、界面显示已保存、生产永不读取。

字段级排查看不见这一层——因为字段确实被某个 dataclass 的构造消费了，
只是**那个 dataclass 永远是 `DEFAULT_*`，不是用户的**。

### 6.3 本批实际交付与欠账

**已接线并配「改参数 → 结果必须变」单测**（`tests/unit/test_config_actually_consumed.py`，
14 条，8 个变异逐一验证有效）：

- `FactorMonitorConfig` 9 项 —— 删除 5 个平行模块常量，改由注入配置取值
- `ScoringPipelineConfig.hysteresis_enabled` —— 抽 `resolve_effective_order` 纯函数并接线
- `TrendStrategyConfig.macd_fast/slow/signal` 3 项 —— `ta.macd` 改读配置

**顺带堵住「上挪一层」**：给 `__init__` 加 `config` 参数并不等于用户配置生效——
6 个构造点一个都没传。改为唯一入口 `build_factor_monitor_service` +
AST 断言「除工厂外不得直接构造」。这正是 CLAUDE.md §4.11 表第 4 例的形状。

⚠️ **过程中自己踩了一次**：首版工厂写成 `async` 并在构造时 `await` 配置读取，
导致 FastAPI 依赖在**鉴权之前**打 DB（`/factor-quality` 的 401 用例变 ConnectionRefused）、
假 session 的单测全炸。改为传 provider、服务首次使用时惰性解析。
**构造器不做 IO 是这一层的既有契约，接配置不能顺手破坏它。**

**欠账（已登记 roadmap §6 V1.5-F，属 §5.4「依赖外部决策」）**：

1. 6.1 的 `ma_short` / `ma_long` —— 需先定 MA 阶梯的参数化形态
2. 6.2 的 6 个零消费者 key —— 策略参数该不该给用户调、`strategy_weights` 与
   ICIR 运行期权重孰先，都要产品拍板

两项均由 `TestEveryEditableConfigKeyHasAConsumer` 的白名单钉住：
**只许缩短不许加长**，新增第 7 个未接线 key 立刻红，接上线却忘了删白名单也红。
