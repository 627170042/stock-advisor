"""
A股T+1短线选股系统 - 筛选引擎模块 v3
基于25次历史推荐的深度复盘，核心优化：

1. ❌ budget策略(低价+非创/科)胜率仅8%，亏损率75% → 暂停budget，两支都走strong
2. ❌ 追涨(推荐日>=3%)次日平均-0.48%，60%亏损 → 严控推荐日涨幅上限
3. ❌ 紫金矿业推荐3次均未命中，紫光股份2次均亏损 → 增加短期重复推荐黑名单
4. ❌ 命中组技术分67 vs 未命中71 → 技术分区分度差，需加强量价和动量衰减权重
5. ✅ strong策略平均次日+0.07%优于budget的-1.24% → 全面转向strong逻辑
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


def filter_chase_risk(stock):
    """
    ★v3新增★ 追涨风险过滤
    数据证明: 推荐日涨幅>=3%的股票，次日60%亏损，平均-0.48%
    策略: 推荐日涨幅>4%的直接排除，3-4%的降权
    """
    change_pct = stock.get('changepercent', 0)
    if change_pct > 4:
        return False, f"追涨风险(当日涨{change_pct:.1f}%)"
    return True, "通过"


def filter_recent_recommendations(stock, history_file):
    """
    ★v3新增★ 短期重复推荐黑名单
    数据证明: 紫金矿业3次推荐均未命中，紫光2次均亏损
    策略: 5个交易日内推荐过的股票不再推荐
    """
    try:
        with open(history_file) as f:
            history = json.load(f)
        
        from datetime import timedelta
        now = datetime.now()
        recent_symbols = set()
        for r in history:
            rec_date = datetime.strptime(r['date'], '%Y-%m-%d')
            if (now - rec_date).days <= 5 and r['symbol'] == stock['symbol']:
                recent_symbols.add(r['symbol'])
        
        if stock['symbol'] in recent_symbols:
            return False, "近期已推荐(5日内)"
    except:
        pass
    return True, "通过"


# ==================== 次日上涨潜力评分 v3 ====================

def score_next_day_potential(stock):
    """
    v3: 基于历史复盘优化权重
    核心变化: 加大当日涨幅区间的区分度，降低追涨股评分
    """
    score = 50
    change_pct = stock.get('changepercent', 0)
    turnover = stock.get('turnoverratio', 0)
    nmc = stock.get('nmc', 0)
    
    # === 当日涨跌幅评分（★v3核心改动：更激进地惩罚追涨）===
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
        score -= 5   # ★v3: 追涨区，降权
    elif change_pct > 4:
        score -= 15  # ★v3: 高位追涨，重罚
    
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
            score += 10  # ★v3: 中大盘股更稳健
        elif 20 <= nmc_yi < 50:
            score += 6
        elif 500 < nmc_yi <= 2000:
            score += 5
        elif nmc_yi < 20:
            score -= 3  # ★v3: 小盘股波动大，扣分
    
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


def screen_stocks(category='budget', max_candidates=15, preloaded_stocks=None):
    """
    综合筛选流程 v3
    核心变化：
    1. 增加追涨风险过滤
    2. 增加短期重复推荐过滤
    3. 调整评分权重(量价+动量衰减权重提高)
    4. 提高入选门槛
    """
    print(f"\n{'='*60}")
    print(f"开始筛选 [{category}] 类股票...")
    print(f"{'='*60}")
    
    DATA_DIR = os.environ.get('DATA_DIR', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data'))
    history_file = os.path.join(DATA_DIR, 'history', 'recommendations.json')
    
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
        
        # ★v3: 追涨风险过滤
        ok, reason = filter_chase_risk(stock)
        if not ok:
            filter_stats['chase'] += 1
            continue
        
        # ★v3: 短期重复推荐过滤
        ok, reason = filter_recent_recommendations(stock, history_file)
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
            
            # ★v3: 调整权重 - 提高量价和技术权重，降低潜力预评权重
            # v2: potential*0.25 + tech*0.45 + prob*0.30
            # v3: potential*0.15 + tech*0.50 + prob*0.35 (更依赖前瞻模型)
            stock['total_score'] = (
                stock['potential_score'] * 0.15 +
                prediction['score'] * 0.50 +
                prediction['prob'] * 100 * 0.35
            )
            
            # ★v3: 入选门槛 - 最低概率要求提高
            if prediction['prob'] < 0.50:  # 概率<50%的直接排除
                print(f"  [{i+1}] {symbol} {stock['name']} 概率{prediction['prob']:.0%}<50%，跳过")
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


def get_top_picks(budget_top=5, strong_top=5):
    """获取最终推荐"""
    all_stocks = build_candidate_pool()
    
    budget_candidates = screen_stocks('budget', max_candidates=15, preloaded_stocks=all_stocks)
    strong_candidates = screen_stocks('strong', max_candidates=15, preloaded_stocks=all_stocks)
    
    return budget_candidates[:budget_top], strong_candidates[:strong_top]


# ==================== 策略优化模块 ====================

class StrategyOptimizer:
    """策略优化器"""
    
    DATA_DIR = os.environ.get('DATA_DIR', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data'))
    HISTORY_FILE = os.path.join(DATA_DIR, 'history', 'recommendations.json')
    
    PARAMS = {
        'min_tech_score': 58,
        'min_next_day_prob': 0.50,
        'weight_potential': 0.15,
        'weight_tech': 0.50,
        'weight_prob': 0.35,
        'max_recommend_day_change': 4.0,  # ★v3: 推荐日涨幅上限
        'min_prob_threshold': 0.50,       # ★v3: 最低概率门槛
        'repeat_blacklist_days': 5,        # ★v3: 重复推荐黑名单天数
    }
    
    def __init__(self):
        self.history = self._load_history()
    
    def _load_history(self):
        if os.path.exists(self.HISTORY_FILE):
            with open(self.HISTORY_FILE, 'r') as f:
                return json.load(f)
        return []
    
    def _save_history(self):
        with open(self.HISTORY_FILE, 'w') as f:
            json.dump(self.history, f, ensure_ascii=False, indent=2)
    
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
                    'hit': next_day_change >= 2,  # ★v3: >=2%算胜
                    'max_profit': next_day_change,
                }
                self._save_history()
                return True
        return False
    
    def optimize(self):
        completed = [r for r in self.history if r.get('result')]
        if len(completed) < 5:
            print("历史数据不足，暂不优化")
            return
        
        hits = [r for r in completed if r['result']['hit']]
        misses = [r for r in completed if not r['result']['hit']]
        
        win_rate = len(hits) / len(completed) if completed else 0
        print(f"\n当前胜率: {win_rate:.1%} ({len(hits)}/{len(completed)})")
        
        if hits:
            avg_tech_hit = sum(r.get('tech_score', 0) for r in hits) / len(hits)
            avg_prob_hit = sum(r.get('next_day_prob', 0) for r in hits) / len(hits)
            avg_change_hit = sum(r['recommend_change'] for r in hits) / len(hits)
            print(f"命中组: 技术分={avg_tech_hit:.1f}, 概率={avg_prob_hit:.1%}, 推荐日涨幅={avg_change_hit:.1f}%")
        
        if misses:
            avg_tech_miss = sum(r.get('tech_score', 0) for r in misses) / len(misses)
            avg_prob_miss = sum(r.get('next_day_prob', 0) for r in misses) / len(misses)
            avg_change_miss = sum(r['recommend_change'] for r in misses) / len(misses)
            print(f"未命中组: 技术分={avg_tech_miss:.1f}, 概率={avg_prob_miss:.1%}, 推荐日涨幅={avg_change_miss:.1f}%")
        
        # 动态参数调整
        if win_rate < 0.3 and len(completed) >= 10:
            self.PARAMS['min_next_day_prob'] = min(0.65, self.PARAMS['min_next_day_prob'] + 0.03)
            self.PARAMS['max_recommend_day_change'] = max(2.0, self.PARAMS['max_recommend_day_change'] - 0.5)
            print(f"→ 胜率偏低，提高门槛: 概率>={self.PARAMS['min_next_day_prob']:.0%}, 涨幅<={self.PARAMS['max_recommend_day_change']:.1f}%")
        
        print(f"当前参数: {json.dumps(self.PARAMS, indent=2)}")


if __name__ == '__main__':
    optimizer = StrategyOptimizer()
    budget_top, strong_top = get_top_picks(budget_top=3, strong_top=3)
    
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
