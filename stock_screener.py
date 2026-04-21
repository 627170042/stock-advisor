"""
A股T+1短线选股系统 - 筛选引擎模块 v2
核心改进：候选池不再以涨幅榜为主，而是多维度构建
重点关注：次日上涨概率，而非当日涨幅
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


# ==================== 次日上涨潜力评分（替代旧的强势分） ====================

def score_next_day_potential(stock):
    """
    基于盘面数据评估次日上涨潜力（不需要K线，用于预筛选）
    注意：这只是一个快速预估，深度分析在技术分析阶段完成
    
    核心思路：
    - 当日微涨(0-3%)或小跌(-2~0%)的股票，次日延续/反弹概率更高
    - 当日大涨(>5%)的股票，次日回调概率大
    - 换手率适中(3-10%)说明资金活跃但未过热
    - 流通市值适中的股票弹性更好
    """
    score = 50
    change_pct = stock.get('changepercent', 0)
    turnover = stock.get('turnoverratio', 0)
    nmc = stock.get('nmc', 0)  # 流通市值(万)
    
    # === 当日涨跌幅评分（关键改进：不再追涨）===
    if -2 <= change_pct < 0:
        score += 15  # 小幅回调，次日反弹概率高
    elif 0 <= change_pct <= 1:
        score += 12  # 微涨蓄势，次日继续概率高
    elif 1 < change_pct <= 3:
        score += 8   # 温和上涨，尚有空间
    elif 3 < change_pct <= 5:
        score += 3   # 涨幅偏大，次日回调风险增加
    elif -5 <= change_pct < -2:
        score += 5   # 较大回调，需观察是否有支撑
    elif change_pct > 5:
        score -= 10  # 大涨追高风险
    elif change_pct > 7:
        score -= 20  # 极度追高
    
    # === 换手率评分 ===
    if 3 <= turnover <= 8:
        score += 12  # 适中，资金活跃且不过热
    elif 1 <= turnover < 3:
        score += 5
    elif 8 < turnover <= 15:
        score += 4   # 偏高但可接受
    elif turnover > 15:
        score -= 5   # 过热
    
    # === 流通市值评分 ===
    if nmc > 0:
        nmc_yi = nmc / 10000
        if 30 <= nmc_yi <= 300:
            score += 8  # 中盘股，弹性与流动性兼顾
        elif 10 <= nmc_yi < 30:
            score += 5  # 小盘股弹性大
        elif 300 < nmc_yi <= 1000:
            score += 3  # 大盘股稳健
    
    return max(0, min(100, score))


# ==================== 综合筛选流程 ====================

def build_candidate_pool():
    """
    多维度构建候选池（核心改进：不再以涨幅榜为主）
    
    策略：
    1. 成交额榜 — 资金关注度的最直接指标
    2. 换手率榜 — 资金活跃度，换手率高说明在交易
    3. 涨幅榜(小幅) — 关注微涨蓄势的，而非追涨停
    4. 振幅榜 — 波动性适中的才有短线操作空间
    """
    print("[1/4] 多维度构建候选池...")
    
    all_stocks = {}
    
    # 数据源1: 成交额TOP60（资金关注度最高）
    volume_stocks = get_top_volume(60)
    for s in volume_stocks:
        all_stocks[s['symbol']] = s
    
    # 数据源2: 换手率TOP40（资金最活跃）
    turnover_stocks = get_top_turnover(40)
    for s in turnover_stocks:
        all_stocks[s['symbol']] = s
    
    # 数据源3: 涨幅榜TOP40（但关注的是微涨蓄势，不是追涨停）
    gainer_stocks = get_top_gainers(40)
    for s in gainer_stocks:
        all_stocks[s['symbol']] = s
    
    print(f"  原始候选池: {len(all_stocks)} 只")
    print(f"    成交额榜: {len(volume_stocks)} | 换手率榜: {len(turnover_stocks)} | 涨幅榜: {len(gainer_stocks)}")
    
    return all_stocks


def screen_stocks(category='budget', max_candidates=15, preloaded_stocks=None):
    """
    综合筛选流程 v2
    核心改变：以前瞻性"次日上涨概率"为排序依据
    """
    print(f"\n{'='*60}")
    print(f"开始筛选 [{category}] 类股票...")
    print(f"{'='*60}")
    
    # 第一步：构建候选池
    if preloaded_stocks:
        all_stocks = dict(preloaded_stocks)
        print(f"  复用已有数据 {len(all_stocks)} 只股票")
    else:
        all_stocks = build_candidate_pool()
    
    # 第二步：基础过滤 + 次日潜力预评分
    print("[2/4] 基础过滤 + 次日潜力预评分...")
    candidates = []
    
    for symbol, stock in all_stocks.items():
        ok, reason = filter_basic(stock, category)
        if not ok:
            continue
        
        ok, reason = filter_liquidity(stock)
        if not ok:
            continue
        
        # 用前瞻性评分替代旧的强势分
        potential_score = score_next_day_potential(stock)
        stock['potential_score'] = potential_score
        candidates.append(stock)
    
    print(f"  基础过滤后剩余 {len(candidates)} 只")
    
    # 按次日潜力预评分排序，只对Top候选做深度分析
    candidates.sort(key=lambda x: x['potential_score'], reverse=True)
    analyze_count = min(len(candidates), max_candidates)
    
    # 第三步：深度技术分析（前瞻性模型）
    print(f"[3/4] 前瞻性技术分析（Top {analyze_count}）...")
    scored_candidates = []
    
    for i, stock in enumerate(candidates[:analyze_count]):
        symbol = stock['symbol']
        try:
            kline = get_kline_sina(symbol, '240', '20')
            if not kline or len(kline) < 8:
                continue
            
            # 前瞻性预测模型
            prediction = predict_next_day(kline, stock)
            
            # 均线数据
            closes = [k['close'] for k in kline]
            ma5 = calc_ma(closes, 5)
            ma10 = calc_ma(closes, 10)
            ma20 = calc_ma(closes, 20)
            rsi = calc_rsi(closes)
            K, D, J = calc_kdj(kline)
            
            # 保存所有分析结果
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
            
            # 综合评分 = 次日潜力预评分*0.25 + 前瞻技术分*0.45 + 概率分*100*0.30
            # 技术分析和概率权重更高，盘面预评分降低
            stock['total_score'] = (
                stock['potential_score'] * 0.25 +
                prediction['score'] * 0.45 +
                prediction['prob'] * 100 * 0.30
            )
            
            scored_candidates.append(stock)
            
            # 输出关键信号
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
    
    # 第四步：排序（以前瞻性综合评分为准）
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
    """策略优化器 - 基于历史推荐结果不断迭代"""
    
    DATA_DIR = os.environ.get('DATA_DIR', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data'))
    HISTORY_FILE = os.path.join(DATA_DIR, 'history', 'recommendations.json')
    
    PARAMS = {
        'min_tech_score': 55,
        'min_next_day_prob': 0.45,
        'weight_potential': 0.25,
        'weight_tech': 0.45,
        'weight_prob': 0.30,
        'max_consecutive_up': 4,
        'rsi_overbought': 75,
        'rsi_oversold': 30,
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
        # 去重：同一日期+同一symbol+同一category不重复记录
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
                    'hit': 2 <= next_day_change <= 5,
                    'max_profit': next_day_change,
                }
                self._save_history()
                return True
        return False
    
    def optimize(self):
        if len(self.history) < 5:
            print("历史数据不足，暂不优化")
            return
        
        completed = [r for r in self.history if r.get('result')]
        if len(completed) < 5:
            print(f"已完成复盘{len(completed)}条，数据不足，暂不优化")
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
        
        # 参数调整
        if win_rate < 0.5 and len(completed) >= 10:
            self.PARAMS['min_tech_score'] = min(70, self.PARAMS['min_tech_score'] + 3)
            self.PARAMS['min_next_day_prob'] = min(0.65, self.PARAMS['min_next_day_prob'] + 0.03)
            print("→ 胜率偏低，已提高筛选门槛")
        elif win_rate > 0.7 and len(completed) >= 10:
            self.PARAMS['min_tech_score'] = max(45, self.PARAMS['min_tech_score'] - 2)
            self.PARAMS['min_next_day_prob'] = max(0.35, self.PARAMS['min_next_day_prob'] - 0.02)
            print("→ 胜率较好，适度放宽门槛")
        
        # 基于推荐日涨幅特征优化：如果未命中的推荐日涨幅偏高，说明追涨策略有问题
        if misses:
            high_change_misses = [r for r in misses if r['recommend_change'] > 4]
            if len(high_change_misses) > len(misses) * 0.5:
                print("→ 未命中多为当日涨幅偏高股，强化回调反弹信号权重")
                self.PARAMS['weight_tech'] = min(0.55, self.PARAMS['weight_tech'] + 0.03)
                self.PARAMS['weight_potential'] = max(0.15, self.PARAMS['weight_potential'] - 0.02)
        
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
