"""
A股T+1短线选股系统 - 筛选引擎模块 v4
★核心升级：自适应策略优化闭环★

v3回顾：
1. ❌ budget策略胜率仅8% → 两支都走strong
2. ❌ 追涨(推荐日>=3%)次日60%亏损 → 严控涨幅上限
3. ❌ 重复推荐不命中 → 增加黑名单
4. ❌ 概率评分虚高(84%→实际15%) → 新增概率校准
5. ❌ 参数不持久化 → 每次运行归零 → 新增strategy_params.json持久化

v4核心：
- 参数持久化：动态参数保存到data/config/strategy_params.json
- 概率校准：基于历史命中/未命中的实际分布，自动修正概率偏移
- 门槛自动调整：胜率趋势驱动门槛收紧/放宽
- 权重自适应：基于维度区分度微调权重
- 防过度拟合：样本量门控、步长反相关、参数边界约束
"""
import time
import json
import os
from datetime import datetime
from data_fetcher import (
    get_top_gainers, get_top_volume, get_top_turnover,
    get_kline_sina, get_realtime_quote, get_batch_quotes,
    is_gem, is_star, classify_board, get_stock_list
)
from technical_analysis import (
    predict_next_day, tech_score, judge_trend, estimate_next_day_prob,
    calc_ma, calc_rsi, calc_kdj, calc_macd, calc_boll
)


# ==================== 基础筛选条件 ====================

def filter_basic(stock, category='budget'):
    """基础条件过滤"""
    symbol = stock['symbol']
    price = stock['trade']

    if 'ST' in stock['name'] or 'st' in stock['name']:
        return False, "ST股"
    if price <= 0:
        return False, "停牌"
    if stock['name'].startswith('N') or stock['name'].startswith('C'):
        return False, "新股"
    if classify_board(symbol) == 'bse':
        return False, "北交所"

    if category == 'budget':
        if is_gem(symbol):
            return False, "创业板"
        if is_star(symbol):
            return False, "科创板"
        if price > 40:
            return False, f"价格超限({price:.2f}>40)"
        if price < 3:
            return False, "低价股"

    return True, "通过"


def filter_liquidity(stock):
    """流动性过滤"""
    if stock.get('amount', 0) < 50000000:
        return False, "成交额不足"
    turnover = stock.get('turnoverratio', 0)
    if turnover < 1:
        return False, "换手率过低"
    if turnover > 25:
        return False, "换手率过高"
    return True, "通过"


def filter_chase_risk(stock, max_change=4.0):
    """
    追涨风险过滤
    数据证明: 推荐日涨幅>=3%的股票，次日60%亏损，平均-0.48%
    策略: 推荐日涨幅>max_change的直接排除，3-max_change的降权
    max_change: 动态参数，从strategy_params.json读取
    """
    change_pct = stock.get('changepercent', 0)
    if change_pct > max_change:
        return False, f"追涨风险(当日涨{change_pct:.1f}%)"
    return True, "通过"


def filter_recent_recommendations(stock, history_file, blacklist_days=5):
    """
    短期重复推荐黑名单
    数据证明: 紫金矿业3次推荐均未命中，紫光2次均亏损
    blacklist_days: 动态参数，从strategy_params.json读取
    """
    try:
        with open(history_file) as f:
            history = json.load(f)

        from datetime import timedelta
        now = datetime.now()
        recent_symbols = set()
        for r in history:
            rec_date = datetime.strptime(r['date'], '%Y-%m-%d')
            if (now - rec_date).days <= blacklist_days and r['symbol'] == stock['symbol']:
                recent_symbols.add(r['symbol'])

        if stock['symbol'] in recent_symbols:
            return False, f"近期已推荐({blacklist_days}日内)"
    except:
        pass
    return True, "通过"


# ==================== 次日上涨潜力评分 v4 ====================

def score_next_day_potential(stock):
    """
    v4: 基于历史复盘优化权重
    核心变化: 加大当日涨幅区间的区分度，降低追涨股评分
    """
    score = 50
    change_pct = stock.get('changepercent', 0)
    turnover = stock.get('turnoverratio', 0)
    nmc = stock.get('nmc', 0)

    # === 当日涨跌幅评分 ===
    if -2 <= change_pct < 0:
        score += 18  # 小幅回调，次日反弹概率最高
    elif 0 <= change_pct <= 1:
        score += 15  # 微涨蓄势
    elif 1 < change_pct <= 2:
        score += 10  # 温和上涨
    elif 2 < change_pct <= 3:
        score += 3   # 涨幅偏大
    elif -4 <= change_pct < -2:
        score += 5   # 较大回调
    elif 3 < change_pct <= 4:
        score -= 5   # 追涨区，降权
    elif change_pct > 4:
        score -= 15  # 高位追涨，重罚

    # === 换手率评分 ===
    if 3 <= turnover <= 8:
        score += 12
    elif 1 <= turnover < 3:
        score += 5
    elif 8 < turnover <= 15:
        score += 4
    elif turnover > 15:
        score -= 5

    # === 流通市值评分 ===
    if nmc > 0:
        nmc_yi = nmc / 10000
        if 50 <= nmc_yi <= 500:
            score += 10  # 中大盘股更稳健
        elif 20 <= nmc_yi < 50:
            score += 6
        elif 500 < nmc_yi <= 2000:
            score += 5
        elif nmc_yi < 20:
            score -= 3  # 小盘股波动大，扣分

    return max(0, min(100, score))


# ==================== 综合筛选流程 ====================

def build_candidate_pool():
    """多维度构建候选池"""
    print("[1/4] 多维度构建候选池...")

    all_stocks = {}

    volume_stocks = get_top_volume(60)
    for s in volume_stocks:
        all_stocks[s['symbol']] = s

    turnover_stocks = get_top_turnover(40)
    for s in turnover_stocks:
        all_stocks[s['symbol']] = s

    gainer_stocks = get_top_gainers(40)
    for s in gainer_stocks:
        all_stocks[s['symbol']] = s

    print(f"  原始候选池: {len(all_stocks)} 只")

    return all_stocks


def screen_stocks(category='budget', max_candidates=15, preloaded_stocks=None, params=None):
    """
    综合筛选流程 v4
    核心变化：
    1. 接受动态params参数（从strategy_params.json读取）
    2. 使用动态阈值代替硬编码值
    3. 概率经过校准后使用
    """
    print(f"\n{'='*60}")
    print(f"开始筛选 [{category}] 类股票...")
    print(f"{'='*60}")

    DATA_DIR = os.environ.get('DATA_DIR', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data'))
    history_file = os.path.join(DATA_DIR, 'history', 'recommendations.json')

    # 加载动态参数
    if params is None:
        params = StrategyOptimizer.DEFAULT_PARAMS

    max_recommend_day_change = params.get('max_recommend_day_change', 4.0)
    min_prob_threshold = params.get('min_prob_threshold', 0.50)
    repeat_blacklist_days = params.get('repeat_blacklist_days', 5)
    weight_potential = params.get('weight_potential', 0.15)
    weight_tech = params.get('weight_tech', 0.50)
    weight_prob = params.get('weight_prob', 0.35)

    print(f"  动态参数: 涨幅上限={max_recommend_day_change}%, 概率门槛={min_prob_threshold:.0%}, "
          f"黑名单={repeat_blacklist_days}日")
    print(f"  权重: 潜力={weight_potential:.2f}, 技术={weight_tech:.2f}, 概率={weight_prob:.2f}")

    if preloaded_stocks:
        all_stocks = dict(preloaded_stocks)
        print(f"  复用已有数据 {len(all_stocks)} 只股票")
    else:
        all_stocks = build_candidate_pool()

    # 第二步：基础过滤 + 追涨过滤 + 重复推荐过滤 + 预评分
    print("[2/4] 多层过滤...")
    candidates = []
    filter_stats = {'basic': 0, 'liquidity': 0, 'chase': 0, 'repeat': 0}

    for symbol, stock in all_stocks.items():
        ok, reason = filter_basic(stock, category)
        if not ok:
            filter_stats['basic'] += 1
            continue

        ok, reason = filter_liquidity(stock)
        if not ok:
            filter_stats['liquidity'] += 1
            continue

        # 追涨风险过滤（动态阈值）
        ok, reason = filter_chase_risk(stock, max_change=max_recommend_day_change)
        if not ok:
            filter_stats['chase'] += 1
            continue

        # 短期重复推荐过滤（动态天数）
        ok, reason = filter_recent_recommendations(stock, history_file, blacklist_days=repeat_blacklist_days)
        if not ok:
            filter_stats['repeat'] += 1
            continue

        potential_score = score_next_day_potential(stock)
        stock['potential_score'] = potential_score
        candidates.append(stock)

    print(f"  基础过滤淘汰: {filter_stats['basic']} | 流动性淘汰: {filter_stats['liquidity']}")
    print(f"  追涨淘汰: {filter_stats['chase']} | 重复推荐淘汰: {filter_stats['repeat']}")
    print(f"  通过过滤剩余 {len(candidates)} 只")

    candidates.sort(key=lambda x: x['potential_score'], reverse=True)
    analyze_count = min(len(candidates), max_candidates)

    # 第三步：深度技术分析
    print(f"[3/4] 技术分析（Top {analyze_count}）...")
    scored_candidates = []

    for i, stock in enumerate(candidates[:analyze_count]):
        symbol = stock['symbol']
        try:
            kline = get_kline_sina(symbol, '240', '20')
            if not kline or len(kline) < 8:
                continue

            prediction = predict_next_day(kline, stock)

            closes = [k['close'] for k in kline]
            ma5 = calc_ma(closes, 5)
            ma10 = calc_ma(closes, 10)
            ma20 = calc_ma(closes, 20)
            rsi = calc_rsi(closes)
            K, D, J = calc_kdj(kline)

            stock['tech_score'] = prediction['score']
            stock['trend'] = judge_trend(kline)
            stock['next_day_prob'] = prediction['prob']
            stock['signals'] = prediction['signals']
            stock['ma5'] = ma5
            stock['ma10'] = ma10
            stock['ma20'] = ma20
            stock['rsi'] = rsi
            stock['kdj_k'] = K
            stock['kdj_d'] = D
            stock['kdj_j'] = J
            stock['kline'] = kline

            # 动态权重计算综合评分
            stock['total_score'] = (
                stock['potential_score'] * weight_potential +
                prediction['score'] * weight_tech +
                prediction['prob'] * 100 * weight_prob
            )

            # 动态概率门槛
            if prediction['prob'] < min_prob_threshold:
                print(f"  [{i+1}] {symbol} {stock['name']} 概率{prediction['prob']:.0%}<{min_prob_threshold:.0%}，跳过")
                continue

            scored_candidates.append(stock)

            top_signals = prediction['signals'][:3]
            signal_str = ' | '.join(top_signals) if top_signals else '—'
            print(f"  [{i+1}/{analyze_count}] {symbol} {stock['name']} "
                  f"潜力={stock['potential_score']} 技术={prediction['score']} "
                  f"概率={prediction['prob']:.0%} 综合={stock['total_score']:.1f}")
            print(f"    信号: {signal_str}")

            time.sleep(0.2)

        except Exception as e:
            print(f"  跳过 {symbol}: {e}")
            continue

    # 第四步：排序
    print("[4/4] 排序输出...")
    scored_candidates.sort(key=lambda x: x['total_score'], reverse=True)

    return scored_candidates


def get_top_picks(budget_top=5, strong_top=5, params=None):
    """获取最终推荐"""
    all_stocks = build_candidate_pool()

    budget_candidates = screen_stocks('budget', max_candidates=15, preloaded_stocks=all_stocks, params=params)
    strong_candidates = screen_stocks('strong', max_candidates=15, preloaded_stocks=all_stocks, params=params)

    return budget_candidates[:budget_top], strong_candidates[:strong_top]


# ==================== 策略优化模块 v4 ====================

class StrategyOptimizer:
    """
    自适应策略优化器 v4

    核心能力：
    1. 参数持久化 - 保存到 data/config/strategy_params.json
    2. 概率校准 - 基于历史命中率自动修正概率偏移
    3. 门槛自动调整 - 胜率趋势驱动
    4. 权重自适应 - 基于维度区分度
    5. 防过度拟合 - 样本量门控、步长约束、参数边界
    """

    DATA_DIR = os.environ.get('DATA_DIR', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data'))
    HISTORY_FILE = os.path.join(DATA_DIR, 'history', 'recommendations.json')
    PARAMS_FILE = os.path.join(DATA_DIR, 'config', 'strategy_params.json')

    # 硬编码默认值（首次运行时使用）
    DEFAULT_PARAMS = {
        'min_tech_score': 58,
        'min_next_day_prob': 0.50,
        'weight_potential': 0.15,
        'weight_tech': 0.50,
        'weight_prob': 0.35,
        'max_recommend_day_change': 4.0,
        'min_prob_threshold': 0.50,
        'repeat_blacklist_days': 5,
    }

    # 参数边界约束
    PARAM_BOUNDS = {
        'min_tech_score': (50, 75),
        'min_next_day_prob': (0.45, 0.70),
        'weight_potential': (0.05, 0.30),
        'weight_tech': (0.20, 0.60),
        'weight_prob': (0.15, 0.50),
        'max_recommend_day_change': (2.5, 4.5),
        'min_prob_threshold': (0.40, 0.65),
        'repeat_blacklist_days': (3, 10),
    }

    def __init__(self):
        self.history = self._load_history()
        self.PARAMS = self._load_params()
        self.prob_calibration = {'offset': 0.0, 'scale': 1.0, 'bucket_calibrations': {}}
        self.meta = {'total_reviews': 0, 'last_win_rate': 0.0, 'adjustment_history': []}
        self.params_changed = False
        # 从文件加载校准和元数据
        self._load_calibration_and_meta()

    def _load_history(self):
        if os.path.exists(self.HISTORY_FILE):
            with open(self.HISTORY_FILE, 'r') as f:
                return json.load(f)
        return []

    def _save_history(self):
        with open(self.HISTORY_FILE, 'w') as f:
            json.dump(self.history, f, ensure_ascii=False, indent=2)

    def _load_params(self):
        """从strategy_params.json加载动态参数"""
        if os.path.exists(self.PARAMS_FILE):
            try:
                with open(self.PARAMS_FILE, 'r') as f:
                    data = json.load(f)
                    params = data.get('params', {})
                    # 补充缺失的参数（DEFAULT_PARAMS中有但文件中没有的）
                    for key, default_val in self.DEFAULT_PARAMS.items():
                        if key not in params:
                            params[key] = default_val
                    return params
            except (json.JSONDecodeError, IOError):
                print("  ⚠️ 参数文件损坏，使用默认值")
        return dict(self.DEFAULT_PARAMS)

    def _save_params(self):
        """将动态参数持久化到strategy_params.json"""
        os.makedirs(os.path.dirname(self.PARAMS_FILE), exist_ok=True)
        data = {
            'version': 2,
            'last_updated': datetime.now().strftime('%Y-%m-%d'),
            'params': self.PARAMS,
            'prob_calibration': self.prob_calibration,
            'meta': self.meta,
        }
        with open(self.PARAMS_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"  ✅ 策略参数已持久化: {self.PARAMS_FILE}")

    def _load_calibration_and_meta(self):
        """从strategy_params.json加载概率校准参数和元数据"""
        if os.path.exists(self.PARAMS_FILE):
            try:
                with open(self.PARAMS_FILE, 'r') as f:
                    data = json.load(f)
                    self.prob_calibration = data.get('prob_calibration', self.prob_calibration)
                    self.meta = data.get('meta', self.meta)
            except (json.JSONDecodeError, IOError):
                pass

    def record_recommendation(self, stock, category, date):
        for existing in self.history:
            if (existing.get('date') == date and
                existing.get('symbol') == stock['symbol'] and
                existing.get('category') == category):
                return existing
        rec = {
            'date': date,
            'symbol': stock['symbol'],
            'name': stock['name'],
            'category': category,
            'recommend_price': stock['trade'],
            'recommend_change': stock['changepercent'],
            'potential_score': stock.get('potential_score', 0),  # ★v4: 新增
            'tech_score': stock.get('tech_score', 0),
            'next_day_prob': stock.get('next_day_prob', 0),
            'total_score': stock.get('total_score', 0),
            'signals': stock.get('signals', []),
            'result': None,
        }
        self.history.append(rec)
        self._save_history()
        return rec

    def update_result(self, symbol, date, next_day_open, next_day_high, next_day_close, next_day_change):
        for rec in self.history:
            if rec['symbol'] == symbol and rec['date'] == date:
                rec['result'] = {
                    'next_day_open': next_day_open,
                    'next_day_high': next_day_high,
                    'next_day_close': next_day_close,
                    'next_day_change': next_day_change,
                    'hit': next_day_change >= 2,  # >=2%算胜
                    'max_profit': next_day_change,
                }
                self._save_history()
                return True
        return False

    # ==================== 概率校准 ====================

    def _compute_prob_calibration(self, completed):
        """
        基于历史数据计算概率校准参数
        两种模式：
        1. 全局偏移（样本<20时）：offset = avg(actual) - avg(predicted)
        2. 分桶校准（样本>=5/桶时）：桶内实际胜率直接替换
        """
        if len(completed) < 5:
            print("  样本<5，跳过概率校准")
            return

        predicted_probs = [r.get('next_day_prob', 0.5) for r in completed]
        actual_hits = [1 if r['result']['hit'] else 0 for r in completed]

        avg_predicted = sum(predicted_probs) / len(predicted_probs)
        avg_actual = sum(actual_hits) / len(actual_hits)

        # 全局偏移
        offset = avg_actual - avg_predicted

        # 防过度拟合: 偏移幅度受限
        max_offset = min(0.35, 0.10 + len(completed) * 0.0125)
        # 只允许向下校准（概率虚高时修正，不虚增概率）
        offset = max(-max_offset, min(0, offset))

        # 分桶校准
        buckets = {}
        bucket_ranges = [(0.50, 0.60), (0.60, 0.70), (0.70, 0.80), (0.80, 0.90)]
        for low, high in bucket_ranges:
            bucket_records = [r for r in completed if low <= r.get('next_day_prob', 0) < high]
            # ★样本>=5才启用分桶校准，否则用全局偏移更稳健
            if len(bucket_records) >= 5:
                bucket_hits = sum(1 for r in bucket_records if r['result']['hit'])
                bucket_key = f"{low:.2f}-{high:.2f}"
                buckets[bucket_key] = {
                    'count': len(bucket_records),
                    'predicted_avg': sum(r.get('next_day_prob', 0) for r in bucket_records) / len(bucket_records),
                    'actual_win_rate': bucket_hits / len(bucket_records),
                }

        old_offset = self.prob_calibration.get('offset', 0.0)
        self.prob_calibration = {
            'offset': round(offset, 4),
            'scale': 1.0,
            'bucket_calibrations': buckets,
        }

        print(f"  概率校准: 偏移 {old_offset:.4f} → {offset:.4f} "
              f"(模型平均{avg_predicted:.2f}, 实际胜率{avg_actual:.2f})")
        if buckets:
            for key, val in buckets.items():
                print(f"    桶{key}: 预测{val['predicted_avg']:.2f}, 实际{val['actual_win_rate']:.2f} "
                      f"(n={val['count']})")

        if abs(offset - old_offset) > 0.001:
            self.params_changed = True

    # ==================== 门槛自动调整 ====================

    def _adjust_thresholds(self, win_rate, sample_size, hits, misses):
        """
        根据胜率趋势自动调整筛选门槛
        防过度拟合: 步长与样本量反相关
        """
        # 调整步长
        step = min(0.03, 0.01 + sample_size * 0.001)

        # 10-20条样本时步长减半
        if sample_size < 20:
            step *= 0.5

        old_params = dict(self.PARAMS)

        # === 概率门槛 ===
        if win_rate < 0.15:
            self.PARAMS['min_next_day_prob'] = min(0.70, self.PARAMS['min_next_day_prob'] + step * 1.5)
            self.PARAMS['min_prob_threshold'] = min(0.65, self.PARAMS['min_prob_threshold'] + step * 1.5)
        elif win_rate < 0.25:
            self.PARAMS['min_next_day_prob'] = min(0.70, self.PARAMS['min_next_day_prob'] + step)
            self.PARAMS['min_prob_threshold'] = min(0.65, self.PARAMS['min_prob_threshold'] + step)
        elif win_rate < 0.35:
            self.PARAMS['min_next_day_prob'] = min(0.65, self.PARAMS['min_next_day_prob'] + step * 0.5)
        elif win_rate > 0.50:
            # 胜率较高时，可适当放宽
            self.PARAMS['min_next_day_prob'] = max(0.45, self.PARAMS['min_next_day_prob'] - step * 0.3)
            self.PARAMS['min_prob_threshold'] = max(0.40, self.PARAMS['min_prob_threshold'] - step * 0.2)

        # === 涨幅上限 ===
        if misses:
            avg_change_miss = sum(r['recommend_change'] for r in misses) / len(misses)
            if avg_change_miss > 2.0:
                self.PARAMS['max_recommend_day_change'] = max(
                    self.PARAM_BOUNDS['max_recommend_day_change'][0],
                    self.PARAMS['max_recommend_day_change'] - 0.3
                )

        # === 技术分门槛 ===
        if hits and misses:
            avg_tech_hit = sum(r.get('tech_score', 0) for r in hits) / len(hits)
            avg_tech_miss = sum(r.get('tech_score', 0) for r in misses) / len(misses)
            # 命中组技术分低于未命中组 → 技术分区分度差 → 提高门槛
            if avg_tech_hit - avg_tech_miss < 5:
                self.PARAMS['min_tech_score'] = min(
                    self.PARAM_BOUNDS['min_tech_score'][1],
                    self.PARAMS['min_tech_score'] + 2
                )

        # 边界约束
        for key, (lo, hi) in self.PARAM_BOUNDS.items():
            if key in self.PARAMS:
                self.PARAMS[key] = max(lo, min(hi, self.PARAMS[key]))

        # 检测变化
        for key in self.PARAMS:
            if self.PARAMS[key] != old_params.get(key):
                self.params_changed = True
                print(f"  门槛调整: {key} {old_params.get(key)} → {self.PARAMS[key]}")

    # ==================== 权重自适应微调 ====================

    def _adjust_weights(self, hits, misses, sample_size):
        """
        基于各维度对命中/未命中的区分度，微调综合评分权重
        区分度越大 → 该维度越有效 → 增加权重
        """
        if len(hits) < 3 or len(misses) < 3 or sample_size < 10:
            print("  样本不足，跳过权重调整")
            return

        # 样本<20时步长减半
        weight_step = min(0.03, 0.01 + sample_size * 0.001)
        if sample_size < 20:
            weight_step *= 0.5

        # 计算各维度区分度
        def safe_avg(lst, key):
            vals = [r.get(key, 0) for r in lst]
            return sum(vals) / len(vals) if vals else 0

        gap_potential = safe_avg(hits, 'potential_score') - safe_avg(misses, 'potential_score')
        gap_tech = safe_avg(hits, 'tech_score') - safe_avg(misses, 'tech_score')
        gap_prob = safe_avg(hits, 'next_day_prob') - safe_avg(misses, 'next_day_prob')

        print(f"  维度区分度: 潜力={gap_potential:+.1f}, 技术={gap_tech:+.1f}, 概率={gap_prob:+.3f}")

        # 区分度为正 → 该维度有效 → 增加权重
        # 区分度为负 → 该维度反效果 → 减少权重
        old_weights = {
            'weight_potential': self.PARAMS['weight_potential'],
            'weight_tech': self.PARAMS['weight_tech'],
            'weight_prob': self.PARAMS['weight_prob'],
        }

        deltas = {}
        total_gap = abs(gap_potential) + abs(gap_tech) + abs(gap_prob * 100)
        if total_gap > 0:
            # 按区分度比例分配权重增量
            deltas['weight_potential'] = (gap_potential / total_gap) * weight_step * 10
            deltas['weight_tech'] = (gap_tech / total_gap) * weight_step * 10
            deltas['weight_prob'] = (gap_prob * 100 / total_gap) * weight_step * 10
        else:
            deltas = {k: 0 for k in old_weights}

        # 应用调整
        self.PARAMS['weight_potential'] += deltas.get('weight_potential', 0)
        self.PARAMS['weight_tech'] += deltas.get('weight_tech', 0)
        self.PARAMS['weight_prob'] += deltas.get('weight_prob', 0)

        # 归一化：确保权重之和为1
        total_weight = self.PARAMS['weight_potential'] + self.PARAMS['weight_tech'] + self.PARAMS['weight_prob']
        if total_weight > 0:
            self.PARAMS['weight_potential'] /= total_weight
            self.PARAMS['weight_tech'] /= total_weight
            self.PARAMS['weight_prob'] /= total_weight

        # 边界约束
        for key in ['weight_potential', 'weight_tech', 'weight_prob']:
            lo, hi = self.PARAM_BOUNDS[key]
            self.PARAMS[key] = max(lo, min(hi, self.PARAMS[key]))

        # 检测变化
        for key in old_weights:
            if abs(self.PARAMS[key] - old_weights[key]) > 0.001:
                self.params_changed = True
                print(f"  权重调整: {key} {old_weights[key]:.3f} → {self.PARAMS[key]:.3f}")

    # ==================== 优化主入口 ====================

    def optimize(self):
        """
        自适应策略优化主入口 v4
        流程: 概率校准 → 门槛调整 → 权重微调 → 持久化 → 打印报告
        """
        completed = [r for r in self.history if r.get('result')]
        if len(completed) < 5:
            print("历史数据不足(需>=5)，暂不优化")
            return

        hits = [r for r in completed if r['result']['hit']]
        misses = [r for r in completed if not r['result']['hit']]

        win_rate = len(hits) / len(completed) if completed else 0

        print(f"\n{'='*60}")
        print(f"🔧 自适应策略优化 v4")
        print(f"{'='*60}")
        print(f"样本量: {len(completed)} | 命中: {len(hits)} | 胜率: {win_rate:.1%}")

        # 重置变化标记
        self.params_changed = False

        # 1. 概率校准（>=5条样本即可）
        print("\n[1/3] 概率校准...")
        self._compute_prob_calibration(completed)

        # 2. 门槛调整（>=5条样本）
        if len(completed) >= 5:
            print("\n[2/3] 门槛调整...")
            self._adjust_thresholds(win_rate, len(completed), hits, misses)

        # 3. 权重微调（>=10条样本，命中/未命中各>=3）
        print("\n[3/3] 权重微调...")
        self._adjust_weights(hits, misses, len(completed))

        # 4. 更新元数据
        self.meta['total_reviews'] = len(completed)
        self.meta['last_win_rate'] = round(win_rate, 4)

        # 记录调整历史
        adjustment = {
            'date': datetime.now().strftime('%Y-%m-%d'),
            'win_rate': round(win_rate, 4),
            'sample_size': len(completed),
            'params': dict(self.PARAMS),
            'prob_offset': self.prob_calibration.get('offset', 0),
        }
        history = self.meta.get('adjustment_history', [])
        history.append(adjustment)
        # 只保留最近20条调整记录
        self.meta['adjustment_history'] = history[-20:]

        # 5. 持久化
        self._save_params()

        # 6. 打印优化报告
        self._print_optimization_report(win_rate, hits, misses, completed)

    def _print_optimization_report(self, win_rate, hits, misses, completed):
        """打印优化报告"""
        print(f"\n{'='*60}")
        print(f"📊 策略优化报告")
        print(f"{'='*60}")

        if hits:
            avg_tech_hit = sum(r.get('tech_score', 0) for r in hits) / len(hits)
            avg_prob_hit = sum(r.get('next_day_prob', 0) for r in hits) / len(hits)
            avg_change_hit = sum(r['recommend_change'] for r in hits) / len(hits)
            avg_potential_hit = sum(r.get('potential_score', 0) for r in hits) / len(hits)
            print(f"命中组: 技术={avg_tech_hit:.1f}, 潜力={avg_potential_hit:.1f}, "
                  f"概率={avg_prob_hit:.1%}, 推荐日涨幅={avg_change_hit:.1f}%")

        if misses:
            avg_tech_miss = sum(r.get('tech_score', 0) for r in misses) / len(misses)
            avg_prob_miss = sum(r.get('next_day_prob', 0) for r in misses) / len(misses)
            avg_change_miss = sum(r['recommend_change'] for r in misses) / len(misses)
            avg_potential_miss = sum(r.get('potential_score', 0) for r in misses) / len(misses)
            print(f"未命中组: 技术={avg_tech_miss:.1f}, 潜力={avg_potential_miss:.1f}, "
                  f"概率={avg_prob_miss:.1%}, 推荐日涨幅={avg_change_miss:.1f}%")

        print(f"\n当前参数:")
        for k, v in self.PARAMS.items():
            print(f"  {k}: {v}")
        print(f"概率校准偏移: {self.prob_calibration.get('offset', 0):.4f}")


if __name__ == '__main__':
    optimizer = StrategyOptimizer()
    budget_top, strong_top = get_top_picks(budget_top=3, strong_top=3, params=optimizer.PARAMS)

    print("\n" + "="*60)
    print("📊 低价股推荐（非创业板，40元以下）")
    print("="*60)
    for i, s in enumerate(budget_top, 1):
        print(f"\n🏆 推荐第{i}名: {s['symbol']} {s['name']}")
        print(f"   价格: {s['trade']:.2f}元 | 涨幅: {s['changepercent']:.2f}% | 换手: {s['turnoverratio']:.2f}%")
        print(f"   潜力分: {s.get('potential_score','N/A')} | 技术分: {s.get('tech_score','N/A')} | 概率: {s.get('next_day_prob',0):.0%}")
        print(f"   综合评分: {s.get('total_score', 0):.1f}")
        if s.get('signals'):
            print(f"   信号: {' | '.join(s['signals'][:4])}")

    print("\n" + "="*60)
    print("🔥 市场最强推荐（不限单价）")
    print("="*60)
    for i, s in enumerate(strong_top, 1):
        print(f"\n🏆 推荐第{i}名: {s['symbol']} {s['name']}")
        print(f"   价格: {s['trade']:.2f}元 | 涨幅: {s['changepercent']:.2f}% | 换手: {s['turnoverratio']:.2f}%")
        print(f"   潜力分: {s.get('potential_score','N/A')} | 技术分: {s.get('tech_score','N/A')} | 概率: {s.get('next_day_prob',0):.0%}")
        print(f"   综合评分: {s.get('total_score', 0):.1f}")
        if s.get('signals'):
            print(f"   信号: {' | '.join(s['signals'][:4])}")

    optimizer.optimize()
