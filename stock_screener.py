"""
A股T+1短线选股系统 - 筛选引擎模块 v6

v5→v6 核心重构：
1. 候选池: 从极端排名~200只 → 全A股技术扫描~1000只
2. 核心逻辑: 从"跌多必反弹" → "趋势延续+动量加速"
3. 消除三重计算: changepercent不再在3个维度重复使用
4. 取消Budget/Strong双类别 → 单一"高概率候选"类别
5. 新增大盘环境过滤: 熊市不推荐
6. K线从20根→120根
7. 优化器最小样本100条（v5在51条上过拟合）

v5铁证（必须彻底改变）：
- 51条推荐，21.6%胜率（约等于随机）
- Budget类17.4%胜率，结构性劣势
- 优化器持续增加potential权重（接飞刀），死亡螺旋
- 小跌(-1~0%)次日胜率8%，均值-1.27% → "跌多必反弹"是伪命题
"""
import time
import json
import os
from datetime import datetime
from data_fetcher import (
    get_top_gainers, get_top_volume, get_top_turnover,
    get_kline_sina, get_realtime_quote, get_batch_quotes,
    is_gem, is_star, classify_board, get_stock_list,
    scan_all_a_stocks, get_market_environment
)
from technical_analysis import (
    predict_next_day, tech_score, judge_trend, estimate_next_day_prob,
    calc_ma, calc_rsi, calc_kdj, calc_macd, calc_boll
)


# ==================== 基础筛选条件 v6 ====================

def filter_basic(stock):
    """
    ★v6: 统一基础过滤（不再分Budget/Strong）
    变化: 取消Budget对创业板/科创板的排除
    原因: Budget类17.4%胜率的主因是排除了高波动板块
    """
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
    if price < 3:
        return False, "低价股(风险)"
    if price > 200:
        return False, f"价格过高({price:.0f}>200)"

    return True, "通过"


def filter_liquidity(stock):
    """流动性过滤"""
    if stock.get('amount', 0) < 100000000:  # v6: 提高到1亿（原5000万）
        return False, "成交额不足1亿"
    turnover = stock.get('turnoverratio', 0)
    if turnover < 1.5:  # v6: 从1%提高到1.5%
        return False, "换手率过低"
    if turnover > 30:  # v6: 从25%放宽到30%
        return False, "换手率过高"
    return True, "通过"


def filter_market_environment(market_env):
    """
    ★v6新增: 大盘环境过滤
    熊市环境下不推荐（系统性风险）
    返回: (pass, reason)
    """
    if not market_env:
        return True, "大盘数据缺失(默认通过)"

    env_score = market_env.get('score', 50)
    env_level = market_env.get('level', 'neutral')

    if env_score < 15:
        return False, f"大盘极弱({env_score}分)"
    if env_level == 'bear' and env_score < 20:
        return False, f"熊市环境({env_score}分)"

    return True, f"环境{env_level}({env_score}分)"


def filter_recent_recommendations(stock, history_file, blacklist_days=10):
    """
    ★v6: 延长黑名单周期
    v5: 7日 → v6: 10日
    永久黑名单: 推荐3次以上0命中
    """
    try:
        with open(history_file) as f:
            history = json.load(f)

        from datetime import timedelta
        now = datetime.now()

        # 短期黑名单
        recent_symbols = set()
        for r in history:
            rec_date = datetime.strptime(r['date'], '%Y-%m-%d')
            if (now - rec_date).days <= blacklist_days and r['symbol'] == stock['symbol']:
                recent_symbols.add(r['symbol'])

        if stock['symbol'] in recent_symbols:
            return False, f"近期已推荐({blacklist_days}日内)"

        # 永久黑名单
        symbol_records = [r for r in history if r['symbol'] == stock['symbol']]
        if len(symbol_records) >= 3:
            hits = sum(1 for r in symbol_records if r.get('result', {}).get('hit', False))
            if hits == 0:
                return False, f"永久黑名单({len(symbol_records)}推0中)"

    except:
        pass
    return True, "通过"


# ==================== v6: 趋势延续+动量加速 评分 ====================

def score_trend_continuation(stock, kline=None):
    """
    ★v6核心: 趋势延续+动量加速评分
    替代v5的 score_next_day_potential (反转逻辑)

    核心思想:
    - 均线多头 + 放量 = 趋势延续
    - 不是"跌多必反弹"，而是"强者恒强"
    - 只使用当日涨跌幅一次（不再三重计算）
    """
    score = 50
    change_pct = stock.get('changepercent', 0)
    turnover = stock.get('turnoverratio', 0)
    nmc = stock.get('nmc', 0)

    # === 当日涨跌幅评分（★仅此一处使用★） ===
    # v6: 温和上涨(2-5%) + 有趋势 = 最优
    if 2 <= change_pct <= 5:
        score += 18  # 温和上涨，趋势最强
    elif 0.5 <= change_pct < 2:
        score += 8   # 微涨，趋势不明
    elif -1 <= change_pct < 0.5:
        score += 2   # 横盘
    elif -3 <= change_pct < -1:
        score -= 5   # 下跌，趋势弱
    elif change_pct < -3:
        score -= 15  # 大跌，趋势断裂
    elif 5 < change_pct <= 7:
        score += 5   # 大涨，但可能过热
    elif change_pct > 7:
        score -= 10  # 暴涨，回调风险极大

    # === 换手率评分 ===
    if 3 <= turnover <= 10:
        score += 12  # 适度活跃
    elif 1.5 <= turnover < 3:
        score += 5
    elif 10 < turnover <= 20:
        score += 4   # 高度活跃但可能有分歧
    elif turnover > 20:
        score -= 5   # 过度活跃，风险

    # === 流通市值评分 ===
    if nmc > 0:
        nmc_yi = nmc / 10000
        if 100 <= nmc_yi <= 1000:
            score += 10  # 中大盘，趋势更稳定
        elif 50 <= nmc_yi < 100:
            score += 7
        elif 20 <= nmc_yi < 50:
            score += 4
        elif nmc_yi > 2000:
            score += 3   # 超大盘，弹性不足
        elif nmc_yi < 20:
            score -= 5   # 小盘股波动大

    # === K线趋势预评分 ===
    if kline and len(kline) >= 20:
        closes = [k['close'] for k in kline]
        ma5 = calc_ma(closes, 5)
        ma10 = calc_ma(closes, 10)
        ma20 = calc_ma(closes, 20)
        if ma5 and ma10 and ma20:
            if ma5 > ma10 > ma20:
                score += 10  # 均线多头排列
            elif ma5 < ma10 < ma20:
                score -= 10  # 均线空头排列

    return max(0, min(100, score))


# ==================== v6: 综合筛选流程 ====================

def build_candidate_pool():
    """
    ★v6: 全A股候选池
    v5: 仅从极端排名选~200只 → 错过大量安静蓄势股
    v6: 扫描全A股，按流动性过滤 → ~1000只候选

    策略:
    1. 全A股扫描（成交额>1亿，换手>1.5%）
    2. 补充极端排名头部股（涨幅榜、量比榜）
    """
    print("[1/4] v6全A股候选池构建...")

    # 方法1: 全A股扫描
    all_stocks = scan_all_a_stocks(min_amount=100000000, min_turnover=1.5)

    # 方法2: 补充头部热点（确保不遗漏）
    # 涨幅前40（市场最热）
    gainer_stocks = get_top_gainers(40)
    for s in gainer_stocks:
        if s['symbol'] not in all_stocks:
            # 放宽条件，让热点股进入候选
            if s.get('amount', 0) > 50000000 and s.get('trade', 0) > 0:
                all_stocks[s['symbol']] = s

    # 换手率前40（资金最活跃）
    turnover_stocks = get_top_turnover(40)
    for s in turnover_stocks:
        if s['symbol'] not in all_stocks:
            if s.get('amount', 0) > 50000000 and s.get('trade', 0) > 0:
                all_stocks[s['symbol']] = s

    print(f"  v6候选池: {len(all_stocks)} 只 (全A股扫描+热点补充)")

    return all_stocks


def screen_stocks(max_candidates=25, preloaded_stocks=None, params=None):
    """
    ★v6: 单一类别筛选流程
    v5: Budget/Strong双类别 → v6: 统一为"高概率候选"
    原因: Budget类17.4%胜率，结构性劣势（排除创业板+10%涨跌幅限制）
    """
    print(f"\n{'='*60}")
    print(f"开始筛选 [v6 趋势延续+动量加速逻辑]...")
    print(f"{'='*60}")

    DATA_DIR = os.environ.get('DATA_DIR', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data'))
    history_file = os.path.join(DATA_DIR, 'history', 'recommendations.json')

    # 加载动态参数
    if params is None:
        params = StrategyOptimizer.DEFAULT_PARAMS

    min_prob_threshold = params.get('min_prob_threshold', 0.20)
    repeat_blacklist_days = params.get('repeat_blacklist_days', 10)
    weight_trend = params.get('weight_trend', 0.35)
    weight_tech = params.get('weight_tech', 0.45)
    weight_prob = params.get('weight_prob', 0.20)

    print(f"  参数: 概率门槛={min_prob_threshold:.0%}, 黑名单={repeat_blacklist_days}日")
    print(f"  权重: 趋势={weight_trend:.2f}, 技术={weight_tech:.2f}, 概率={weight_prob:.2f}")

    # ★v6新增: 大盘环境检查
    print("\n  [v6] 大盘环境检查...")
    market_env = get_market_environment()
    env_pass, env_reason = filter_market_environment(market_env)
    if not env_pass:
        print(f"  ⚠️ {env_reason}，今日不宜推荐")
        return []
    print(f"  ✅ {env_reason}")

    # 候选池
    if preloaded_stocks:
        all_stocks = dict(preloaded_stocks)
        print(f"  复用已有数据 {len(all_stocks)} 只股票")
    else:
        all_stocks = build_candidate_pool()

    # 加载板块热度数据
    from sector_heat import SectorHeatMap
    sector_map = SectorHeatMap()
    try:
        sector_map.fetch_sector_heat_data()
    except Exception as e:
        print(f"  ⚠️ 板块热度数据获取失败: {e}")

    # 多层过滤
    print("[2/4] 多层过滤...")
    candidates = []
    filter_stats = {'basic': 0, 'liquidity': 0, 'repeat': 0}

    for symbol, stock in all_stocks.items():
        ok, reason = filter_basic(stock)
        if not ok:
            filter_stats['basic'] += 1
            continue

        ok, reason = filter_liquidity(stock)
        if not ok:
            filter_stats['liquidity'] += 1
            continue

        # 短期重复推荐过滤
        ok, reason = filter_recent_recommendations(stock, history_file, blacklist_days=repeat_blacklist_days)
        if not ok:
            filter_stats['repeat'] += 1
            continue

        # 获取板块热度
        from sector_heat import get_sector_heat_for_stock
        stock_sector_heat = get_sector_heat_for_stock(symbol, stock.get('name', ''), sector_map)
        stock['sector_heat'] = stock_sector_heat

        # ★v6: 趋势延续评分（替代v5的potential_score）
        # 先用简单评分筛选，减少后续K线获取量
        stock['trend_score'] = score_trend_continuation(stock)

        # 初步筛选：趋势分>=40才值得深度分析
        if stock['trend_score'] < 40:
            continue

        candidates.append(stock)

    print(f"  基础淘汰: {filter_stats['basic']} | 流动性淘汰: {filter_stats['liquidity']}")
    print(f"  重复推荐淘汰: {filter_stats['repeat']}")
    print(f"  趋势初筛通过 {len(candidates)} 只")

    # 按趋势分排序，只对Top N做深度分析
    candidates.sort(key=lambda x: x['trend_score'], reverse=True)
    analyze_count = min(len(candidates), max_candidates)

    # 深度技术分析
    print(f"[3/4] 深度技术分析（Top {analyze_count}）...")
    scored_candidates = []

    for i, stock in enumerate(candidates[:analyze_count]):
        symbol = stock['symbol']
        try:
            # ★v6关键: K线从20根→120根
            kline = get_kline_sina(symbol, '240', '120')
            if not kline or len(kline) < 20:
                continue

            # 获取板块热度（复用已缓存的结果）
            stock_sector_heat = stock.get('sector_heat')

            # ★v6: 传入market_env进行大盘环境修正
            prediction = predict_next_day(kline, stock,
                                          sector_heat=stock_sector_heat,
                                          market_env=market_env)

            closes = [k['close'] for k in kline]
            ma5 = calc_ma(closes, 5)
            ma10 = calc_ma(closes, 10)
            ma20 = calc_ma(closes, 20)
            ma60 = calc_ma(closes, 60) if len(closes) >= 60 else None
            rsi = calc_rsi(closes)
            K, D, J = calc_kdj(kline)

            stock['tech_score'] = prediction['score']
            stock['trend'] = judge_trend(kline)
            stock['next_day_prob'] = prediction['prob']
            stock['signals'] = prediction['signals']
            stock['ma5'] = ma5
            stock['ma10'] = ma10
            stock['ma20'] = ma20
            stock['ma60'] = ma60
            stock['rsi'] = rsi
            stock['kdj_k'] = K
            stock['kdj_d'] = D
            stock['kdj_j'] = J
            stock['kline'] = kline

            # 用K线更新趋势分（更精确）
            stock['trend_score'] = score_trend_continuation(stock, kline=kline)

            # ★v6综合评分: 趋势*0.35 + 技术*0.45 + 概率*0.20
            # 消除三重计算：trend_score只用changepercent一次，tech_score不用changepercent
            stock['total_score'] = (
                stock['trend_score'] * weight_trend +
                prediction['score'] * weight_tech +
                prediction['prob'] * 100 * weight_prob
            )

            # 概率门槛
            if prediction['prob'] < min_prob_threshold:
                print(f"  [{i+1}] {symbol} {stock['name']} 概率{prediction['prob']:.0%}<{min_prob_threshold:.0%}，跳过")
                continue

            scored_candidates.append(stock)

            # 显示信息
            sector_str = ''
            if stock_sector_heat:
                heat_emoji = '🔥' if stock_sector_heat['heat_level'] == 'hot' else ('❄️' if stock_sector_heat['heat_level'] == 'cold' else '📊')
                sector_str = f" {heat_emoji}{stock_sector_heat['sector_name']}({stock_sector_heat['heat_score']}分)"

            top_signals = prediction['signals'][:3]
            signal_str = ' | '.join(top_signals) if top_signals else '—'
            print(f"  [{i+1}/{analyze_count}] {symbol} {stock['name']} "
                  f"趋势={stock['trend_score']} 技术={prediction['score']} "
                  f"概率={prediction['prob']:.0%} 综合={stock['total_score']:.1f}{sector_str}")
            print(f"    信号: {signal_str}")

            time.sleep(0.2)

        except Exception as e:
            print(f"  跳过 {symbol}: {e}")
            continue

    # 排序输出
    print("[4/4] 排序输出...")
    scored_candidates.sort(key=lambda x: x['total_score'], reverse=True)

    return scored_candidates


def get_top_picks(top_n=2, params=None):
    """★v6: 获取最终推荐（单一类别）"""
    all_stocks = build_candidate_pool()
    candidates = screen_stocks(max_candidates=25, preloaded_stocks=all_stocks, params=params)
    return candidates[:top_n]


# ==================== 策略优化模块 v6 ====================

class StrategyOptimizer:
    """
    ★v6简化: 自适应策略优化器

    v5→v6 核心变化:
    1. 优化器最小样本100条（v5在51条上过拟合→死亡螺旋）
    2. 取消概率桶校准（小样本下极不稳定）
    3. 权重调整更保守（步长减半）
    4. 概率校准偏移上限0.10（v5的0.05太保守，但0.20太多）
    """

    DATA_DIR = os.environ.get('DATA_DIR', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data'))
    HISTORY_FILE = os.path.join(DATA_DIR, 'history', 'recommendations.json')
    PARAMS_FILE = os.path.join(DATA_DIR, 'config', 'strategy_params.json')

    # ★v6默认参数
    DEFAULT_PARAMS = {
        'min_tech_score': 40,
        'min_next_day_prob': 0.18,       # ★v6: 与概率基线0.18匹配
        'weight_trend': 0.35,            # ★v6: 趋势延续评分权重
        'weight_tech': 0.45,             # ★v6: 技术评分权重最大
        'weight_prob': 0.20,             # ★v6: 概率权重最低
        'max_recommend_day_change': 7.0,  # ★v6: 放宽（不再用反转逻辑限制涨幅）
        'min_prob_threshold': 0.20,       # ★v6: 降低（概率基线已降至0.18）
        'repeat_blacklist_days': 10,
    }

    # 参数边界约束
    PARAM_BOUNDS = {
        'min_tech_score': (30, 60),
        'min_next_day_prob': (0.12, 0.35),
        'weight_trend': (0.20, 0.45),
        'weight_tech': (0.30, 0.55),
        'weight_prob': (0.10, 0.30),
        'max_recommend_day_change': (4.0, 10.0),
        'min_prob_threshold': (0.12, 0.35),
        'repeat_blacklist_days': (5, 14),
    }

    def __init__(self):
        self.history = self._load_history()
        self.PARAMS = self._load_params()
        self.prob_calibration = {'offset': 0.0, 'scale': 1.0}
        self.meta = {'total_reviews': 0, 'last_win_rate': 0.0, 'adjustment_history': []}
        self.params_changed = False
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
        if os.path.exists(self.PARAMS_FILE):
            try:
                with open(self.PARAMS_FILE, 'r') as f:
                    data = json.load(f)
                    params = data.get('params', {})
                    # 合并默认值（兼容旧参数文件）
                    for key, default_val in self.DEFAULT_PARAMS.items():
                        if key not in params:
                            params[key] = default_val
                    return params
            except (json.JSONDecodeError, IOError):
                print("  ⚠️ 参数文件损坏，使用默认值")
        return dict(self.DEFAULT_PARAMS)

    def _save_params(self):
        os.makedirs(os.path.dirname(self.PARAMS_FILE), exist_ok=True)
        data = {
            'version': 6,
            'last_updated': datetime.now().strftime('%Y-%m-%d'),
            'params': self.PARAMS,
            'prob_calibration': self.prob_calibration,
            'meta': self.meta,
        }
        with open(self.PARAMS_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"  ✅ 策略参数已持久化: {self.PARAMS_FILE}")

    def _load_calibration_and_meta(self):
        if os.path.exists(self.PARAMS_FILE):
            try:
                with open(self.PARAMS_FILE, 'r') as f:
                    data = json.load(f)
                    self.prob_calibration = data.get('prob_calibration', self.prob_calibration)
                    self.meta = data.get('meta', self.meta)
            except (json.JSONDecodeError, IOError):
                pass

    def record_recommendation(self, stock, category, date):
        """记录推荐（v6: category统一为'pick'，但保留字段兼容性）"""
        for existing in self.history:
            if (existing.get('date') == date and
                existing.get('symbol') == stock['symbol']):
                return existing
        rec = {
            'date': date,
            'symbol': stock['symbol'],
            'name': stock['name'],
            'category': 'pick',  # v6: 统一类别
            'recommend_price': stock['trade'],
            'recommend_change': stock['changepercent'],
            'trend_score': stock.get('trend_score', 0),       # v6: 替代potential_score
            'potential_score': stock.get('trend_score', 0),    # 兼容旧字段
            'tech_score': stock.get('tech_score', 0),
            'next_day_prob': stock.get('next_day_prob', 0),
            'total_score': stock.get('total_score', 0),
            'signals': stock.get('signals', []),
            'sector_heat': stock.get('sector_heat'),
            'result': None,
        }
        self.history.append(rec)
        self._save_history()
        return rec

    def update_result(self, symbol, date, next_day_open, next_day_high, next_day_close, next_day_change):
        """
        ★v6: 使用max_profit作为命中标准
        v5: next_day_change >= 2 为命中（只看收盘）
        v6: max_profit >= 2 为命中（看日内最高价相对于推荐价）
        原因: 短线操作可在日内止盈，收益应从推荐买入价算起
        """
        # ★关键: max_profit应从推荐价算起，不是从开盘价
        # 找到推荐记录获取推荐价
        rec_price = None
        for rec in self.history:
            if rec['symbol'] == symbol and rec['date'] == date:
                rec_price = rec.get('recommend_price', next_day_open)
                break

        base_price = rec_price if rec_price and rec_price > 0 else next_day_open
        max_profit = (next_day_high - base_price) / base_price * 100 if base_price > 0 else 0

        for rec in self.history:
            if rec['symbol'] == symbol and rec['date'] == date:
                rec['result'] = {
                    'next_day_open': next_day_open,
                    'next_day_high': next_day_high,
                    'next_day_close': next_day_close,
                    'next_day_change': next_day_change,
                    'hit': max_profit >= 2,          # ★v6: 使用max_profit
                    'max_profit': round(max_profit, 2),
                    'close_hit': next_day_change >= 2,  # 保留收盘命中供参考
                }
                self._save_history()
                return True
        return False

    # ==================== v6: 概率校准 ====================

    def _compute_prob_calibration(self, completed):
        """v6: 全局偏移校准，最小样本100"""
        if len(completed) < 100:
            print(f"  样本{len(completed)}<100，跳过概率校准（避免过拟合）")
            return

        predicted_probs = [r.get('next_day_prob', 0.5) for r in completed]
        actual_hits = [1 if r['result']['hit'] else 0 for r in completed]

        avg_predicted = sum(predicted_probs) / len(predicted_probs)
        avg_actual = sum(actual_hits) / len(actual_hits)

        offset = avg_actual - avg_predicted

        # v6: 偏移上限0.10
        max_offset = 0.10
        offset = max(-max_offset, min(max_offset, offset))

        old_offset = self.prob_calibration.get('offset', 0.0)
        self.prob_calibration = {
            'offset': round(offset, 4),
            'scale': 1.0,
        }

        print(f"  概率校准(v6): 偏移 {old_offset:.4f} → {offset:.4f} "
              f"(模型平均{avg_predicted:.2f}, 实际胜率{avg_actual:.2f})")

        if abs(offset - old_offset) > 0.001:
            self.params_changed = True

    # ==================== 门槛调整 ====================

    def _adjust_thresholds(self, win_rate, sample_size, hits, misses):
        """v6: 门槛调整 — 样本<100不调整"""
        if sample_size < 100:
            print(f"  样本{sample_size}<100，跳过门槛调整")
            return

        old_params = dict(self.PARAMS)

        if win_rate > 0.35:
            self.PARAMS['min_prob_threshold'] = max(0.12, self.PARAMS['min_prob_threshold'] - 0.01)
        elif win_rate < 0.20:
            self.PARAMS['min_prob_threshold'] = min(0.35, self.PARAMS['min_prob_threshold'] + 0.01)

        # 边界约束
        for key, (lo, hi) in self.PARAM_BOUNDS.items():
            if key in self.PARAMS:
                self.PARAMS[key] = max(lo, min(hi, self.PARAMS[key]))

        for key in self.PARAMS:
            if self.PARAMS[key] != old_params.get(key):
                self.params_changed = True
                print(f"  门槛调整: {key} {old_params.get(key)} → {self.PARAMS[key]}")

    # ==================== 权重微调 ====================

    def _adjust_weights(self, hits, misses, sample_size):
        """v6: 权重调整 — 样本<100不调整，步长0.005"""
        if sample_size < 100:
            print(f"  样本{sample_size}<100，跳过权重调整")
            return

        if len(hits) < 5 or len(misses) < 5:
            print("  命中/未命中样本不足5，跳过权重调整")
            return

        def safe_avg(lst, key):
            vals = [r.get(key, 0) for r in lst]
            return sum(vals) / len(vals) if vals else 0

        # v6: 使用trend_score替代potential_score
        gap_trend = safe_avg(hits, 'trend_score') - safe_avg(misses, 'trend_score')
        gap_tech = safe_avg(hits, 'tech_score') - safe_avg(misses, 'tech_score')
        gap_prob = safe_avg(hits, 'next_day_prob') - safe_avg(misses, 'next_day_prob')

        print(f"  维度区分度: 趋势={gap_trend:+.1f}, 技术={gap_tech:+.1f}, 概率={gap_prob:+.3f}")

        old_weights = {k: self.PARAMS[k] for k in ['weight_trend', 'weight_tech', 'weight_prob']}

        max_step = 0.005  # v6: 步长减半（v5=0.01）

        if gap_trend > 0:
            self.PARAMS['weight_trend'] = min(0.45, self.PARAMS['weight_trend'] + max_step)
        if gap_tech > 0:
            self.PARAMS['weight_tech'] = min(0.55, self.PARAMS['weight_tech'] + max_step)
        if gap_prob > 0:
            self.PARAMS['weight_prob'] = min(0.30, self.PARAMS['weight_prob'] + max_step)

        # 归一化
        total_weight = self.PARAMS['weight_trend'] + self.PARAMS['weight_tech'] + self.PARAMS['weight_prob']
        if total_weight > 0:
            self.PARAMS['weight_trend'] /= total_weight
            self.PARAMS['weight_tech'] /= total_weight
            self.PARAMS['weight_prob'] /= total_weight

        for key in ['weight_trend', 'weight_tech', 'weight_prob']:
            lo, hi = self.PARAM_BOUNDS[key]
            self.PARAMS[key] = max(lo, min(hi, self.PARAMS[key]))

        for key in old_weights:
            if abs(self.PARAMS[key] - old_weights[key]) > 0.001:
                self.params_changed = True
                print(f"  权重调整: {key} {old_weights[key]:.3f} → {self.PARAMS[key]:.3f}")

    # ==================== 优化主入口 ====================

    def optimize(self):
        """v6优化主入口"""
        completed = [r for r in self.history if r.get('result')]
        if len(completed) < 5:
            print("历史数据不足(需>=5)，暂不优化")
            return

        hits = [r for r in completed if r['result']['hit']]
        misses = [r for r in completed if not r['result']['hit']]

        win_rate = len(hits) / len(completed) if completed else 0

        print(f"\n{'='*60}")
        print(f"🔧 自适应策略优化 v6")
        print(f"{'='*60}")
        print(f"样本量: {len(completed)} | 命中: {len(hits)} | 胜率: {win_rate:.1%}")

        self.params_changed = False

        print("\n[1/3] 概率校准(v6, 最小样本100)...")
        self._compute_prob_calibration(completed)

        print("\n[2/3] 门槛调整(v6, 最小样本100)...")
        self._adjust_thresholds(win_rate, len(completed), hits, misses)

        print("\n[3/3] 权重微调(v6, 最小样本100)...")
        self._adjust_weights(hits, misses, len(completed))

        self.meta['total_reviews'] = len(completed)
        self.meta['last_win_rate'] = round(win_rate, 4)

        adjustment = {
            'date': datetime.now().strftime('%Y-%m-%d'),
            'win_rate': round(win_rate, 4),
            'sample_size': len(completed),
            'params': dict(self.PARAMS),
            'prob_offset': self.prob_calibration.get('offset', 0),
        }
        history = self.meta.get('adjustment_history', [])
        history.append(adjustment)
        self.meta['adjustment_history'] = history[-20:]

        self._save_params()
        self._print_optimization_report(win_rate, hits, misses, completed)

    def _print_optimization_report(self, win_rate, hits, misses, completed):
        """打印优化报告"""
        print(f"\n{'='*60}")
        print(f"📊 策略优化报告 v6")
        print(f"{'='*60}")

        if hits:
            avg_tech_hit = sum(r.get('tech_score', 0) for r in hits) / len(hits)
            avg_prob_hit = sum(r.get('next_day_prob', 0) for r in hits) / len(hits)
            avg_change_hit = sum(r['recommend_change'] for r in hits) / len(hits)
            avg_trend_hit = sum(r.get('trend_score', 0) for r in hits) / len(hits)
            print(f"命中组: 技术={avg_tech_hit:.1f}, 趋势={avg_trend_hit:.1f}, "
                  f"概率={avg_prob_hit:.1%}, 推荐日涨幅={avg_change_hit:.1f}%")

        if misses:
            avg_tech_miss = sum(r.get('tech_score', 0) for r in misses) / len(misses)
            avg_prob_miss = sum(r.get('next_day_prob', 0) for r in misses) / len(misses)
            avg_change_miss = sum(r['recommend_change'] for r in misses) / len(misses)
            avg_trend_miss = sum(r.get('trend_score', 0) for r in misses) / len(misses)
            print(f"未命中组: 技术={avg_tech_miss:.1f}, 趋势={avg_trend_miss:.1f}, "
                  f"概率={avg_prob_miss:.1%}, 推荐日涨幅={avg_change_miss:.1f}%")

        print(f"\n当前参数:")
        for k, v in self.PARAMS.items():
            print(f"  {k}: {v}")
        print(f"概率校准偏移: {self.prob_calibration.get('offset', 0):.4f}")


if __name__ == '__main__':
    optimizer = StrategyOptimizer()
    top_picks = get_top_picks(top_n=3, params=optimizer.PARAMS)

    print("\n" + "="*60)
    print("📊 v6推荐候选（趋势延续+动量加速）")
    print("="*60)
    for i, s in enumerate(top_picks, 1):
        print(f"\n🏆 推荐第{i}名: {s['symbol']} {s['name']}")
        print(f"   价格: {s['trade']:.2f}元 | 涨幅: {s['changepercent']:.2f}% | 换手: {s['turnoverratio']:.2f}%")
        print(f"   趋势分: {s.get('trend_score','N/A')} | 技术分: {s.get('tech_score','N/A')} | 概率: {s.get('next_day_prob',0):.0%}")
        print(f"   综合评分: {s.get('total_score', 0):.1f}")
        if s.get('signals'):
            print(f"   信号: {' | '.join(s['signals'][:4])}")

    optimizer.optimize()
