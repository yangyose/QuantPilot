/**
 * Phase 10 §6.8：金融/量化术语统一解释。
 *
 * 与 SDD §15.1 术语表对齐，前端 `<TermLabel term="sharpe">` 自动展示 tooltip。
 * 新增术语只需在此处补充键，无需修改组件。
 */
export interface TermDef {
  /** 中文规范术语（tooltip 标题）*/
  title: string
  /** 一句话解释（tooltip 正文，避免学术化措辞）*/
  description: string
}

export const GLOSSARY: Record<string, TermDef> = {
  // ─── 绩效指标（SDD 附录 C） ────────────────────────────────
  cumulative_return: {
    title: '累计收益率',
    description: '回测期内组合相较于初始本金的总收益率。100% 表示资产翻倍。',
  },
  annualized_return: {
    title: '年化收益率',
    description: '将累计收益率折算为按年计算的等效年化率，用于跨周期对比。',
  },
  max_drawdown: {
    title: '最大回撤（MaxDD）',
    description: '从历史最高净值跌至随后最低净值的最大跌幅。-20% 表示曾从高点跌去 20%。',
  },
  sharpe: {
    title: '夏普比率',
    description: '单位风险（年化波动）下的超额收益。> 1 较优，> 2 优秀，越高越好。',
  },
  win_rate: {
    title: '胜率',
    description: '盈利交易占总交易次数的比例。胜率高 ≠ 收益高（需结合盈亏比看）。',
  },
  profit_loss_ratio: {
    title: '盈亏比',
    description: '平均盈利金额 / 平均亏损金额。> 1 表示赚得比亏得多，配合胜率综合判断。',
  },
  benchmark_return: {
    title: '基准收益率',
    description: '同期沪深 300 等基准指数的累计收益率，用于对比超额收益（α）。',
  },

  // ─── 因子质量（SDD §12.3） ────────────────────────────────
  ic: {
    title: 'IC（信息系数）',
    description: '当期因子值与下期收益的 Pearson 相关系数。|IC| > 0.02 即视为有预测力。',
  },
  rank_ic: {
    title: 'Rank IC（秩相关）',
    description: '基于排名的 Spearman 相关系数，对异常值不敏感，A 股常用此口径。',
  },
  ir: {
    title: 'IR（信息比率）',
    description: 'IC 均值 / IC 标准差，衡量因子预测力的稳定性。> 0.5 较好。',
  },
  ic_mean_3m: {
    title: '近 3 月 IC 均值',
    description: '滚动 3 个月的 IC 均值，反映因子近期预测力是否衰减。',
  },
  half_life: {
    title: '半衰期',
    description: '因子收益预测能力衰减到一半所需的天数。越长说明因子越稳定。',
  },

  // ─── 市场状态识别（SDD §6.5） ───────────────────────────
  adx: {
    title: 'ADX（平均趋向指数）',
    description: '衡量趋势强度的指标，范围 0-100。> 25 视为存在显著趋势。',
  },
  ma: {
    title: 'MA（移动平均线）',
    description: '收盘价的 N 日均值。MA20 上穿 MA60 称为「金叉」，常被视作上涨信号。',
  },
  ma_short: {
    title: 'MA 短周期',
    description: '快速移动均线（默认 20 日），用于捕捉中短期趋势。',
  },
  ma_long: {
    title: 'MA 长周期',
    description: '慢速移动均线（默认 60 日），用于过滤噪声、确认大趋势。',
  },
  macd: {
    title: 'MACD',
    description: '快慢 EMA 差值 + 信号线，用于识别趋势转折点。',
  },
  rsi: {
    title: 'RSI（相对强弱指数）',
    description: '范围 0-100，> 70 超买，< 30 超卖。常用于均值回归策略。',
  },
  bbands: {
    title: '布林带（Bollinger Bands）',
    description: 'MA ± N 倍标准差。价格触及下轨视为超卖，可用于均值回归。',
  },

  // ─── 估值/财务 ───────────────────────────────────────────
  pe_ttm: {
    title: 'PE-TTM（滚动市盈率）',
    description: '股价 / 过去 12 个月每股净利润。低 PE 说明估值偏低（注意周期股例外）。',
  },
  pb: {
    title: 'PB（市净率）',
    description: '股价 / 每股净资产。<1 称破净，常用于价值策略筛选。',
  },
  pe_pb_percentile: {
    title: 'PE/PB 历史分位',
    description: '当前 PE/PB 在过去 N 年中的百分位。0% 表示历史最低估，100% 最贵。',
  },

  // ─── 信号/仓位 ───────────────────────────────────────────
  composite_score: {
    title: '综合评分',
    description: '4 大策略加权得到的 0-100 评分。> 买入阈值（默认 80）触发买入信号。',
  },
  signal_strength: {
    title: '信号强度',
    description: 'STRONG / MODERATE，由综合评分阈值（默认 90）划分。',
  },
  stop_loss: {
    title: '硬止损',
    description: '从买入均价回撤超过设定比例（默认 8%）即触发卖出。',
  },
  t1_warning: {
    title: 'T+1 提醒',
    description: 'A 股买入当日不可卖出，最早次一交易日才能成交。',
  },
}

/**
 * 安全查询：未登记的 term 返回 undefined（组件可降级为不显示 tooltip）。
 */
export function getTerm(key: string): TermDef | undefined {
  return GLOSSARY[key]
}
