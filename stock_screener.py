"""
A股T+1短线选股系统 - 筛选引擎模块 v5
★核心升级：反转选股逻辑★

v4→v5 核心变化：
1. 候选池扩展：加入跌幅榜（找回调反弹机会）
2. 评分权重反转：潜力分>技术分>概率分（概率是反向指标！）
3. 新增"微涨死亡区间"过滤 — 0~2%微涨股胜率0%
4. 简化优化器：移除桶校准、简化权重调整
5. 强化黑名单：永远不推荐0/3以下的股票
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


def filter_chase_risk(stock, max_change=3.5):
    """
    ★v5强化: 追涨风险过滤
    数据铁证: 推荐日涨0~2%的股票次日胜率0%！
    v4: max_change=4.0 → v5: max_change=3.5
    新增: 0~2%微涨区间由filter_death_zone单独处理
    """
    change_pct = stock.get('changepercent', 0)
    if change_pct > max_change:
        return False, f"追涨风险(当日涨{change_pct:.1f}%)"
    return True, "通过"


def filter_death_zone(stock):
    """
    ★v5新增: 微涨死亡区间过滤
    数据: 推荐日涨0~2%的8只股票全部未命中！
    策略: 直接过滤掉当日微涨0.5%~2%的股票（最危险区间）
    """
    change_pct = stock.get('changepercent', 0)
    if 0.5 <= change_pct < 2.0:
        # 这是一个很危险的区间，但不是100%必死
        # 如果板块热度高，可以放行
        sector_heat = stock.get('sector_heat')
        if sector_heat and sector_heat.get('heat_level') == 'hot':
            return True, "热门板块微涨(放行)"
        return False, f"微涨死亡区({change_pct:.1f}%)"
    return True, "通过"


def filter_recent_recommendations(stock, history_file, blacklist_days=7):
    """
    ★v5强化: 短期重复推荐黑名单
    v4: blacklist_days=5 → v5: blacklist_days=7
    新增: 永久黑名单 — 推荐3次以上0命中的股票
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
        
        # ★v5新增: 永久黑名单 — 推荐N次以上0命中的
        symbol_records = [r for r in history if r['symbol'] == stock['symbol']]
        if len(symbol_records) >= 3:
            hits = sum(1 for r in symbol_records if r.get('result', {}).get('hit', False))
            if hits == 0:
                return False, f"永久黑名单({len(symbol_records)}推0中)"
        
    except:
        pass
    return True, "通过"


# ==================== 次日上涨潜力评分 v5 ====================

def score_next_day_potential(stock, sector_heat=None):
    """
    ★v5: 反转评分逻辑
    核心变化:
    1. 大幅提升回调股评分（-2%以上回调加分最多）
    2. 0~2%微涨区大幅降权（死亡区间）
    3. 板块热度对回调股有额外加成
    """
    score = 50
    change_pct = stock.get('changepercent', 0)
    turnover = stock.get('turnoverratio', 0)
    nmc = stock.get('nmc', 0)

    # === 当日涨跌幅评分（★v5反转★） ===
    if change_pct <= -3:
        score += 25  # 深幅回调，反弹预期最强
    elif -3 < change_pct <= -2:
        score += 20  # 较大回调
    elif -2 < change_pct <= -1:
        score += 15  # 小幅回调
    elif -1 < change_pct < -0.5:
        score += 10  # 微跌
    elif -0.5 <= change_pct < 0:
        score += 8   # 几乎平盘偏弱
    elif 0 <= change_pct < 0.5:
        score += 3   # 微涨偏弱
    elif 0.5 <= change_pct < 2:
        score -= 10  # ★微涨死亡区，大降权
    elif 2 <= change_pct < 3:
        score -= 5   # 温和上涨
    elif 3 <= change_pct < 4:
        score -= 10  # 追涨区
    elif change_pct >= 4:
        score -= 20  # 高位追涨，重罚

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
            score += 10
        elif 20 <= nmc_yi < 50:
            score += 6
        elif 500 < nmc_yi <= 2000:
            score += 5
        elif nmc_yi < 20:
            score -= 3

    # === ★v5: 板块热度对回调股加成 ===
    if sector_heat:
        heat_score = sector_heat.get('heat_score', 50)
        heat_level = sector_heat.get('heat_level', 'warm')
        if heat_level == 'hot':
            if change_pct < 0:
                # 热门板块+回调 = 黄金组合
                score += 20
            else:
                score += 10
        elif heat_level == 'warm':
            score += 5
        else:
            # 冷门板块回调 = 可能继续跌
            if change_pct < 0:
                score -= 5
            else:
                score -= 3

    return max(0, min(100, score))


# ==================== 综合筛选流程 v5 ====================

def build_candidate_pool():
    """
    ★v5: 扩展候选池
    新增: 跌幅榜 — 寻找超跌反弹机会
    """
    print("[1/4] 多维度构建候选池（含跌幅榜）...")

    all_stocks = {}

    # 成交额前80（大盘股活跃度）
    volume_stocks = get_top_volume(80)
    for s in volume_stocks:
        all_stocks[s['symbol']] = s

    # 换手率前40（资金活跃度）
    turnover_stocks = get_top_turnover(40)
    for s in turnover_stocks:
        all_stocks[s['symbol']] = s

    # 涨幅前40（市场热点）
    gainer_stocks = get_top_gainers(40)
    for s in gainer_stocks:
        all_stocks[s['symbol']] = s

    # ★v5新增: 跌幅榜（找超跌反弹）
    loser_stocks = get_stock_list(page=1, num=40, sort='changepercent', asc=1)
    for s in loser_stocks:
        # 只选跌幅1.5%~5%的（太跌可能是利空）
        if -5 <= s.get('changepercent', 0) <= -1.5:
            all_stocks[s['symbol']] = s

    print(f"  原始候选池: {len(all_stocks)} 只 (含跌幅榜)")

    return all_stocks


def screen_stocks(category='budget', max_candidates=20, preloaded_stocks=None, params=None):
    """
    ★v5: 反转选股逻辑筛选流程
    
    核心变化:
    1. 权重反转: 潜力0.40 > 技术0.35 > 概率0.25
    2. 新增微涨死亡区过滤
    3. 强化黑名单（永久黑名单）
    4. 候选池含跌幅榜
    """
    print(f"\n{'='*60}")
    print(f"开始筛选 [{category}] 类股票 (v5反转逻辑)...")
    print(f"{'='*60}")

    DATA_DIR = os.environ.get('DATA_DIR', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data'))
    history_file = os.path.join(DATA_DIR, 'history', 'recommendations.json')

    # 加载动态参数
    if params is None:
        params = StrategyOptimizer.DEFAULT_PARAMS

    max_recommend_day_change = params.get('max_recommend_day_change', 3.0)
    min_prob_threshold = params.get('min_prob_threshold', 0.30)
    repeat_blacklist_days = params.get('repeat_blacklist_days', 7)
    weight_potential = params.get('weight_potential', 0.40)
    weight_tech = params.get('weight_tech', 0.35)
    weight_prob = params.get('weight_prob', 0.25)

    print(f"  动态参数: 涨幅上限={max_recommend_day_change}%, 概率门槛={min_prob_threshold:.0%}, "
          f"黑名单={repeat_blacklist_days}日")
    print(f"  权重: 潜力={weight_potential:.2f}, 技术={weight_tech:.2f}, 概率={weight_prob:.2f}")

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

    print("[2/4] 多层过滤...")
    candidates = []
    filter_stats = {'basic': 0, 'liquidity': 0, 'chase': 0, 'death_zone': 0, 'repeat': 0}

    for symbol, stock in all_stocks.items():
        ok, reason = filter_basic(stock, category)
        if not ok:
            filter_stats['basic'] += 1
            continue

        ok, reason = filter_liquidity(stock)
        if not ok:
            filter_stats['liquidity'] += 1
            continue

        # ★v5: 更严格的追涨过滤
        ok, reason = filter_chase_risk(stock, max_change=max_recommend_day_change)
        if not ok:
            filter_stats['chase'] += 1
            continue

        # ★v5: 获取板块热度（在死亡区过滤前，因为需要板块信息）
        from sector_heat import get_sector_heat_for_stock
        stock_sector_heat = get_sector_heat_for_stock(symbol, stock.get('name', ''), sector_map)
        stock['sector_heat'] = stock_sector_heat

        # ★v5新增: 微涨死亡区过滤
        ok, reason = filter_death_zone(stock)
        if not ok:
            filter_stats['death_zone'] += 1
            continue

        # 短期重复推荐过滤（动态天数+永久黑名单）
        ok, reason = filter_recent_recommendations(stock, history_file, blacklist_days=repeat_blacklist_days)
        if not ok:
            filter_stats['repeat'] += 1
            continue

        potential_score = score_next_day_potential(stock, sector_heat=stock_sector_heat)
        stock['potential_score'] = potential_score
        candidates.append(stock)

    print(f"  基础过滤淘汰: {filter_stats['basic']} | 流动性淘汰: {filter_stats['liquidity']}")
    print(f"  追涨淘汰: {filter_stats['chase']} | ★死亡区淘汰: {filter_stats['death_zone']}")
    print(f"  重复推荐淘汰: {filter_stats['repeat']}")
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

            # 获取板块热度（复用已缓存的映射结果）
            stock_sector_heat = stock.get('sector_heat')

            prediction = predict_next_day(kline, stock, sector_heat=stock_sector_heat)

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

            # ★v5: 反转权重 — 潜力最大，概率最小
            stock['total_score'] = (
                stock['potential_score'] * weight_potential +
                prediction['score'] * weight_tech +
                prediction['prob'] * 100 * weight_prob
            )

            # ★v5: 降低概率门槛 — 概率是反向指标，不能设太高
            if prediction['prob'] < min_prob_threshold:
                print(f"  [{i+1}] {symbol} {stock['name']} 概率{prediction['prob']:.0%}<{min_prob_threshold:.0%}，跳过")
                continue

            scored_candidates.append(stock)

            # 显示板块热度
            sector_str = ''
            if stock_sector_heat:
                heat_emoji = '🔥' if stock_sector_heat['heat_level'] == 'hot' else ('❄️' if stock_sector_heat['heat_level'] == 'cold' else '📊')
                sector_str = f" {heat_emoji}{stock_sector_heat['sector_name']}({stock_sector_heat['heat_score']}分)"

            top_signals = prediction['signals'][:3]
            signal_str = ' | '.join(top_signals) if top_signals else '—'
            print(f"  [{i+1}/{analyze_count}] {symbol} {stock['name']} "
                  f"潜力={stock['potential_score']} 技术={prediction['score']} "
                  f"概率={prediction['prob']:.0%} 综合={stock['total_score']:.1f}{sector_str}")
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

    budget_candidates = screen_stocks('budget', max_candidates=20, preloaded_stocks=all_stocks, params=params)
    strong_candidates = screen_stocks('strong', max_candidates=20, preloaded_stocks=all_stocks, params=params)

    return budget_candidates[:budget_top], strong_candidates[:strong_top]


# ==================== 策略优化模块 v5 ====================

class StrategyOptimizer:
    """
    ★v5简化: 自适应策略优化器
    
    简化方向:
    1. 移除桶校准 — 小样本不稳定
    2. 全局偏移收紧 — 概率已是反向指标，大幅偏移无意义
    3. 简化权重调整 — 概率维度降权
    4. 保留门槛调整 — 但更保守
    """

    DATA_DIR = os.environ.get('DATA_DIR', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data'))
    HISTORY_FILE = os.path.join(DATA_DIR, 'history', 'recommendations.json')
    PARAMS_FILE = os.path.join(DATA_DIR, 'config', 'strategy_params.json')

    # ★v5默认参数 — 反转权重
    DEFAULT_PARAMS = {
        'min_tech_score': 50,           # 降低技术分门槛
        'min_next_day_prob': 0.30,       # ★大幅降低概率门槛
        'weight_potential': 0.40,        # ★潜力分权重最大
        'weight_tech': 0.35,            # 技术分第二
        'weight_prob': 0.25,            # ★概率分权重最低（反向指标）
        'max_recommend_day_change': 3.5, # ★收紧追涨上限（4.0→3.5）
        'min_prob_threshold': 0.30,      # ★大幅降低概率门槛
        'repeat_blacklist_days': 7,      # 延长黑名单天数
    }

    # 参数边界约束
    PARAM_BOUNDS = {
        'min_tech_score': (40, 65),
        'min_next_day_prob': (0.20, 0.45),
        'weight_potential': (0.25, 0.50),
        'weight_tech': (0.20, 0.45),
        'weight_prob': (0.10, 0.35),
        'max_recommend_day_change': (2.0, 4.0),
        'min_prob_threshold': (0.20, 0.45),
        'repeat_blacklist_days': (5, 14),
    }

    def __init__(self):
        self.history = self._load_history()
        self.PARAMS = self._load_params()
        self.prob_calibration = {'offset': 0.0, 'scale': 1.0, 'bucket_calibrations': {}}
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
        """从strategy_params.json加载动态参数"""
        if os.path.exists(self.PARAMS_FILE):
            try:
                with open(self.PARAMS_FILE, 'r') as f:
                    data = json.load(f)
                    params = data.get('params', {})
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
            'version': 3,
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
            'potential_score': stock.get('potential_score', 0),
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
        for rec in self.history:
            if rec['symbol'] == symbol and rec['date'] == date:
                rec['result'] = {
                    'next_day_open': next_day_open,
                    'next_day_high': next_day_high,
                    'next_day_close': next_day_close,
                    'next_day_change': next_day_change,
                    'hit': next_day_change >= 2,
                    'max_profit': next_day_change,
                }
                self._save_history()
                return True
        return False

    # ==================== v5简化: 概率校准 ====================

    def _compute_prob_calibration(self, completed):
        """
        ★v5简化: 只算全局偏移，不做桶校准
        
        原因:
        1. 38个样本做桶校准统计意义不足
        2. 模型概率是反向指标，桶校准会加剧问题
        3. 全局偏移已经够用
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

        # ★v5: 偏移收紧到0.05 — 概率已是反向指标，大偏移无意义
        max_offset = 0.05
        offset = max(-max_offset, min(0, offset))

        old_offset = self.prob_calibration.get('offset', 0.0)
        self.prob_calibration = {
            'offset': round(offset, 4),
            'scale': 1.0,
            'bucket_calibrations': {},  # v5: 不再使用桶校准
        }

        print(f"  概率校准(v5简化): 偏移 {old_offset:.4f} → {offset:.4f} "
              f"(模型平均{avg_predicted:.2f}, 实际胜率{avg_actual:.2f})")

        if abs(offset - old_offset) > 0.001:
            self.params_changed = True

    # ==================== 门槛调整 ====================

    def _adjust_thresholds(self, win_rate, sample_size, hits, misses):
        """v5: 门槛调整 — 更保守，避免死循环"""
        old_params = dict(self.PARAMS)

        # 胜率>35%时放宽门槛
        if win_rate > 0.35 and sample_size >= 10:
            self.PARAMS['min_prob_threshold'] = max(0.20, self.PARAMS['min_prob_threshold'] - 0.01)
        # 胜率<15%时微调涨幅上限（降追涨而不是升概率门槛）
        elif win_rate < 0.15 and sample_size >= 20:
            self.PARAMS['max_recommend_day_change'] = max(
                self.PARAM_BOUNDS['max_recommend_day_change'][0],
                self.PARAMS['max_recommend_day_change'] - 0.2
            )

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
        """v5: 简化权重调整"""
        if len(hits) < 3 or len(misses) < 3 or sample_size < 20:
            print("  样本不足，跳过权重调整")
            return

        def safe_avg(lst, key):
            vals = [r.get(key, 0) for r in lst]
            return sum(vals) / len(vals) if vals else 0

        gap_potential = safe_avg(hits, 'potential_score') - safe_avg(misses, 'potential_score')
        gap_tech = safe_avg(hits, 'tech_score') - safe_avg(misses, 'tech_score')
        gap_prob = safe_avg(hits, 'next_day_prob') - safe_avg(misses, 'next_day_prob')

        print(f"  维度区分度: 潜力={gap_potential:+.1f}, 技术={gap_tech:+.1f}, 概率={gap_prob:+.3f}")

        old_weights = {k: self.PARAMS[k] for k in ['weight_potential', 'weight_tech', 'weight_prob']}

        max_step = 0.01

        # ★v5: 概率区分度为正时不增加权重（概率是反向指标）
        # 只有区分度为负时才微调
        if gap_potential > 0:
            self.PARAMS['weight_potential'] = min(0.50, self.PARAMS['weight_potential'] + max_step)
        if gap_tech > 0:
            self.PARAMS['weight_tech'] = min(0.45, self.PARAMS['weight_tech'] + max_step)
        # ★概率维度：区分度为负（反向指标）→ 保持低权重
        if gap_prob < 0:
            self.PARAMS['weight_prob'] = max(0.10, self.PARAMS['weight_prob'] - max_step)

        # 归一化
        total_weight = self.PARAMS['weight_potential'] + self.PARAMS['weight_tech'] + self.PARAMS['weight_prob']
        if total_weight > 0:
            self.PARAMS['weight_potential'] /= total_weight
            self.PARAMS['weight_tech'] /= total_weight
            self.PARAMS['weight_prob'] /= total_weight

        for key in ['weight_potential', 'weight_tech', 'weight_prob']:
            lo, hi = self.PARAM_BOUNDS[key]
            self.PARAMS[key] = max(lo, min(hi, self.PARAMS[key]))

        for key in old_weights:
            if abs(self.PARAMS[key] - old_weights[key]) > 0.001:
                self.params_changed = True
                print(f"  权重调整: {key} {old_weights[key]:.3f} → {self.PARAMS[key]:.3f}")

    # ==================== 优化主入口 ====================

    def optimize(self):
        """v5简化优化主入口"""
        completed = [r for r in self.history if r.get('result')]
        if len(completed) < 5:
            print("历史数据不足(需>=5)，暂不优化")
            return

        hits = [r for r in completed if r['result']['hit']]
        misses = [r for r in completed if not r['result']['hit']]

        win_rate = len(hits) / len(completed) if completed else 0

        print(f"\n{'='*60}")
        print(f"🔧 自适应策略优化 v5")
        print(f"{'='*60}")
        print(f"样本量: {len(completed)} | 命中: {len(hits)} | 胜率: {win_rate:.1%}")

        self.params_changed = False

        print("\n[1/3] 概率校准(v5简化)...")
        self._compute_prob_calibration(completed)

        print("\n[2/3] 门槛调整...")
        self._adjust_thresholds(win_rate, len(completed), hits, misses)

        print("\n[3/3] 权重微调...")
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
        print(f"📊 策略优化报告 v5")
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
