"""
A股T+1短线选股系统 - 技术分析模块 v5

★v5 核心教训：反转模型逻辑★
历史数据铁证：
- 命中组预测概率66% vs 未命中组74% → 模型概率是反向指标！
- 推荐日涨0~2%的股票胜率0% → "看起来安全"的微涨最危险
- 命中组3只是prob=52-57% → "看起来犹豫"反而大赚

核心改动：
1. 概率基础从0.40降到0.30 — 降低过度自信
2. 反转"趋势延续性"评分 — 均线多头≠次日涨，可能过热
3. 大幅提升"回调反弹"权重 — 数据证明回调股胜率远高于追涨
4. 新增"当日涨幅惩罚"维度 — 0~2%微涨是死亡区间
5. 移除概率校准的桶融合（模型概率无区分度，校准无意义）
6. 简化信号体系 — 去掉假信号，保留真信号
"""
import numpy as np
import json
import os


# ==================== 基础指标计算 ====================

def calc_ma(closes, period):
    if len(closes) < period:
        return None
    return sum(closes[-period:]) / period


def calc_ema(values, period):
    if len(values) < period:
        return None
    multiplier = 2 / (period + 1)
    ema = sum(values[:period]) / period
    for val in values[period:]:
        ema = (val - ema) * multiplier + ema
    return ema


def calc_macd(closes, fast=12, slow=26, signal=9):
    if len(closes) < slow + signal:
        return None, None, None
    ema_fast = calc_ema(closes, fast)
    ema_slow = calc_ema(closes, slow)
    if ema_fast is None or ema_slow is None:
        return None, None, None
    difs = []
    for i in range(slow, len(closes) + 1):
        fast_ema = calc_ema(list(closes[:i]), fast)
        slow_ema = calc_ema(list(closes[:i]), slow)
        if fast_ema and slow_ema:
            difs.append(fast_ema - slow_ema)
    if len(difs) < signal:
        return ema_fast - ema_slow, None, None
    dea = calc_ema(difs, signal)
    dif = difs[-1]
    macd_bar = 2 * (dif - dea) if dea else None
    return dif, dea, macd_bar


def calc_rsi(closes, period=14):
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        change = closes[i] - closes[i-1]
        gains.append(max(change, 0))
        losses.append(max(-change, 0))
    if len(gains) < period:
        return None
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def calc_kdj(kline_data, n=9):
    if len(kline_data) < n:
        return None, None, None
    K, D = 50.0, 50.0
    for i in range(n, len(kline_data) + 1):
        subset = kline_data[i-n:i]
        high_n = max(k['high'] for k in subset)
        low_n = min(k['low'] for k in subset)
        close = kline_data[i-1]['close']
        rsv = (close - low_n) / (high_n - low_n) * 100 if high_n != low_n else 50
        K = 2/3 * K + 1/3 * rsv
        D = 2/3 * D + 1/3 * K
    J = 3 * K - 2 * D
    return K, D, J


def calc_boll(closes, period=20, nbdev=2):
    if len(closes) < period:
        return None, None, None
    ma = sum(closes[-period:]) / period
    std = np.std(closes[-period:])
    return ma + nbdev * std, ma, ma - nbdev * std


# ==================== v5: 反转逻辑的次日预测模型 ====================

def predict_next_day(kline_data, stock_info=None, sector_heat=None):
    """
    v5: 反转逻辑的次日预测模型
    
    ★核心教训：股市短线不是"强者恒强"，而是"物极必反"★
    - 均线多头排列 → 可能过热，次日回调
    - 回调企稳 → 蓄势待发，次日反弹
    - 微涨0~2% → 最危险的"温水煮青蛙"区间
    - 大跌-2%以上 → 反弹概率最高
    
    五大维度（v6维度精简）：
    1. 回调反弹信号 (★核心★ 权重最大)
    2. 量价健康度 (缩量回调+放量企稳 = 最强信号)
    3. 当日涨幅惩罚 (0~2%微涨=死亡区间)
    4. 动量位置 (超卖反弹 > 超买追涨)
    5. 板块热度修正 (热门板块回调=买点)
    """
    if not kline_data or len(kline_data) < 8:
        return {'prob': 0.20, 'signals': [], 'score': 20}
    
    closes = [k['close'] for k in kline_data]
    volumes = [k['volume'] for k in kline_data]
    current = closes[-1]
    
    # ★v5: 降低基础概率 — 短线2%+涨幅本就是小概率事件
    prob = 0.30
    signals = []
    score = 0
    
    ma5 = calc_ma(closes, 5)
    ma10 = calc_ma(closes, 10)
    ma20 = calc_ma(closes, 20)
    
    # =====================================================
    # 维度1: 回调反弹信号 (0-35分, ±0.20概率) ★★★核心★★★
    # 数据铁证: 命中股中3/7是推荐日跌2%+的，0/8的微涨股命中
    # =====================================================
    rebound_score = 0
    
    if len(closes) >= 5:
        recent_5_closes = closes[-5:]
        recent_5_volumes = volumes[-5:]
        
        # ★最强信号: 连续回调后今日企稳+放量
        if (recent_5_closes[-3] < recent_5_closes[-4] and  # 前几天跌
            recent_5_closes[-1] >= recent_5_closes[-2]):    # 今天企稳
            rebound_score += 12
            signals.append("回调企稳形态")
            
            # 企稳日放量 → 资金进场确认
            if recent_5_volumes[-1] > recent_5_volumes[-2] * 1.2:
                rebound_score += 8
                signals.append("企稳放量(资金确认)")
        
        # ★强信号: 缩量回调（主力未出逃）
        if len(volumes) >= 5:
            avg_vol_5 = sum(volumes[-5:]) / 5
            if recent_5_closes[-2] < recent_5_closes[-3]:  # 昨天回调
                if recent_5_volumes[-2] < avg_vol_5 * 0.8:  # 缩量回调
                    rebound_score += 8
                    signals.append("缩量回调(主力未出)")
        
        # ★强信号: 缩量后放量上攻
        if len(volumes) >= 4:
            if (volumes[-1] > volumes[-2] * 1.3 and
                volumes[-2] < volumes[-3] and
                closes[-1] > closes[-2]):
                rebound_score += 10
                signals.append("缩量后放量上攻(★最强)")
    
    # 当日跌幅加分 — 数据证明跌幅大的次日表现更好
    if stock_info:
        today_change = stock_info.get('changepercent', 0)
        if today_change <= -2:
            rebound_score += 10  # 大幅回调，反弹预期强
            signals.append("深幅回调(反弹预期强)")
        elif today_change <= -0.5:
            rebound_score += 6
            signals.append("小幅回调蓄势")
        elif today_change < 0:
            rebound_score += 3
            signals.append("微跌蓄势")
    
    # 近5日最大回撤 > 5% → 有反弹空间
    if len(closes) >= 5:
        max_5 = max(closes[-5:])
        drawdown = (max_5 - current) / max_5 * 100
        if drawdown >= 5:
            rebound_score += 5
            signals.append(f"5日回撤{drawdown:.1f}%(有反弹空间)")
    
    if rebound_score >= 20:
        prob += 0.15
    elif rebound_score >= 12:
        prob += 0.10
    elif rebound_score >= 6:
        prob += 0.05
    score += rebound_score
    
    # =====================================================
    # 维度2: 量价健康度 (0-25分, ±0.12概率)
    # 核心: 上涨放量+下跌缩量 = 真实趋势
    # =====================================================
    vp_score = 0
    
    if len(kline_data) >= 6:
        up_vol_sum = 0
        down_vol_sum = 0
        up_days = 0
        down_days = 0
        
        for i in range(1, min(6, len(kline_data))):
            if closes[-i] > closes[-i-1]:
                up_vol_sum += volumes[-i]
                up_days += 1
            else:
                down_vol_sum += volumes[-i]
                down_days += 1
        
        if up_days > 0 and down_days > 0:
            avg_up_vol = up_vol_sum / up_days
            avg_down_vol = down_vol_sum / down_days
            
            if avg_up_vol > avg_down_vol * 1.3:
                vp_score += 12
                signals.append("量价健康(涨放量跌缩量)")
            elif avg_up_vol > avg_down_vol:
                vp_score += 6
                signals.append("量价尚可")
            else:
                vp_score -= 3
                signals.append("量价背离(风险)")
    
    if vp_score >= 12:
        prob += 0.08
    elif vp_score >= 6:
        prob += 0.04
    elif vp_score < 0:
        prob -= 0.06
    score += max(0, vp_score)
    
    # =====================================================
    # 维度3: 当日涨幅惩罚 (★新增★ 0-20分, -0.25概率)
    # 数据铁证: 推荐日涨0~2% → 胜率0%！
    # 推荐日跌>2% → 胜率50%
    # =====================================================
    day_penalty = 0
    
    if stock_info:
        today_change = stock_info.get('changepercent', 0)
        
        if 0 < today_change < 2:
            # ★死亡区间: 微涨最危险 — 不上不下，次日大概率跌
            day_penalty -= 15
            prob -= 0.15
            signals.append(f"微涨{today_change:.1f}%(⚠️死亡区间)")
        elif 2 <= today_change < 3:
            day_penalty -= 8
            prob -= 0.08
            signals.append(f"涨{today_change:.1f}%(偏热)")
        elif 3 <= today_change < 4:
            day_penalty -= 5
            prob -= 0.05
            signals.append(f"涨{today_change:.1f}%(追涨)")
        elif today_change >= 4:
            day_penalty -= 10
            prob -= 0.10
            signals.append(f"涨{today_change:.1f}%(高位追涨)")
        elif -1 <= today_change < 0:
            day_penalty += 5
            signals.append("微跌(安全)")
        elif -2 <= today_change < -1:
            day_penalty += 8
            signals.append("小跌(较安全)")
        elif today_change < -2:
            day_penalty += 10
            signals.append("大跌(反弹机会)")
    
    score += max(0, 10 + day_penalty)  # 基础10分
    
    # =====================================================
    # 维度4: 动量位置 — 超卖区更有价值 (0-20分, ±0.10概率)
    # ★v5反转: 超卖加分，超买扣分（不追高）
    # =====================================================
    momentum_score = 0
    
    # RSI位置 — 超卖反弹优先
    rsi = calc_rsi(closes, 14)
    if rsi is not None:
        if 30 <= rsi < 45:
            momentum_score += 8
            signals.append(f"RSI={rsi:.0f}(超卖区反弹)")
        elif 45 <= rsi <= 60:
            momentum_score += 5
            signals.append(f"RSI={rsi:.0f}(中性偏多)")
        elif 60 < rsi <= 70:
            momentum_score += 1
        elif rsi > 70:
            momentum_score -= 5
            prob -= 0.05
            signals.append(f"RSI={rsi:.0f}(超买)")
        elif rsi < 30:
            momentum_score += 6
            signals.append(f"RSI={rsi:.0f}(极度超卖)")
    
    # KDJ位置
    K, D, J = calc_kdj(kline_data)
    if K is not None:
        if K < D and K < 30:
            momentum_score += 5
            signals.append("KDJ超卖(反弹可能)")
        elif K > D and K < 50:
            momentum_score += 4
            signals.append("KDJ低位金叉")
        elif K > D and K >= 75:
            momentum_score -= 3
            prob -= 0.03
            signals.append("KDJ超买(谨慎)")
        if J is not None and J > 100:
            momentum_score -= 5
            prob -= 0.05
            signals.append("J值超100(极度超买)")
    
    # MACD — 底部金叉价值高，高位红柱危险
    dif, dea, macd_bar = calc_macd(closes)
    if dif is not None and dea is not None:
        if dif > dea and dif < 0:
            momentum_score += 6
            signals.append("MACD底部金叉(强信号)")
        elif dif > 0 and dif > dea:
            momentum_score += 2
            # 高位红柱 — 不一定是好事，可能过热
            if current > ma20 * 1.05:  # 远离20日线
                momentum_score -= 3
                prob -= 0.03
                signals.append("MACD高位红柱(可能过热)")
        elif dif > dea and dif < 0:
            momentum_score += 4
            signals.append("MACD回升中")
    
    if momentum_score >= 10:
        prob += 0.08
    elif momentum_score >= 5:
        prob += 0.04
    elif momentum_score < 0:
        prob -= 0.05
    score += max(0, momentum_score)
    
    # =====================================================
    # 维度5: 板块热度修正 (0-15分, ±0.08概率)
    # ★v5: 热门板块的回调是买点，冷门板块的上涨是卖点
    # =====================================================
    heat_level = 'warm'
    heat_score_val = 50
    sector_name = ''
    if sector_heat:
        heat_level = sector_heat.get('heat_level', 'warm')
        heat_score_val = sector_heat.get('heat_score', 50)
        sector_name = sector_heat.get('sector_name', '')
    
    # 板块热度对回调股的加成
    if stock_info:
        today_change = stock_info.get('changepercent', 0)
        
        if heat_level == 'hot':
            if today_change < 0:
                # ★热门板块 + 回调 = 最佳买点
                prob += 0.08
                score += 10
                signals.append(f"🔥{sector_name}回调(★最佳买点)")
            elif today_change < 2:
                prob += 0.04
                score += 5
                signals.append(f"🔥{sector_name}微涨(趋势中)")
            else:
                # 热门板块大涨 → 可能短期见顶
                prob -= 0.03
                signals.append(f"🔥{sector_name}大涨(注意分歧)")
        elif heat_level == 'cold':
            if today_change < -2:
                # 冷门板块深跌 → 可能继续跌
                prob -= 0.05
                signals.append(f"❄️{sector_name}深跌(可能继续)")
            elif today_change > 0:
                # 冷门板块上涨 → 多数是反弹而非反转
                prob -= 0.03
                signals.append(f"❄️{sector_name}上涨(反弹非反转)")
    
    # 连涨天数的板块修正
    consecutive_up = 0
    for i in range(len(closes)-1, 0, -1):
        if closes[i] > closes[i-1]:
            consecutive_up += 1
        else:
            break
    
    if consecutive_up >= 5:
        if heat_level == 'hot':
            prob -= 0.05  # 热门板块5连涨，趋势延续但需谨慎
            signals.append(f"5连涨(🔥趋势延续但需谨慎)")
        else:
            prob -= 0.15
            signals.append(f"5连涨(⚠️回调风险高)")
    elif consecutive_up >= 4:
        if heat_level == 'hot':
            prob -= 0.03  # 热门板块4连涨，小幅回调风险
            signals.append(f"4连涨(🔥趋势延续但需谨慎)")
        else:
            prob -= 0.10  # 非热门板块4连涨，回调风险大
            signals.append(f"4连涨(⚠️动量衰减)")
    
    # =====================================================
    # 最终概率归一化
    # =====================================================
    prob = max(0.10, min(0.85, prob))
    score = max(0, min(100, score))

    # ★v5: 简化概率校准 — 只用全局偏移，不用桶校准
    # 原因：桶校准在小样本下极不稳定，且模型概率无区分度
    prob = _simple_calibrate(prob)

    return {
        'prob': prob,
        'signals': signals,
        'score': score,
        'trend_score': 0,  # v5: 不再使用趋势延续性
        'rebound_score': rebound_score,
        'vp_score': max(0, vp_score),
        'breakout_score': 0,  # v5: 不再使用突破信号
        'momentum_score': max(0, momentum_score),
        'decay_penalty': 0,
    }


def _simple_calibrate(raw_prob):
    """
    ★v5简化校准: 只做全局偏移，不做桶校准
    
    原因: 
    1. 模型概率是反向指标，桶校准只会让高概率降更多
    2. 38个样本做桶校准统计意义不足
    3. 全局偏移已经够用（如果模型整体偏高就减一点）
    """
    calibration = load_calibration()
    offset = calibration.get('offset', 0.0)
    if offset != 0.0:
        calibrated = raw_prob + offset
        return max(0.15, min(0.85, calibrated))
    return raw_prob


# ==================== 兼容旧接口 ====================

def tech_score(kline_data, realtime=None):
    """综合技术评分 (0-100) — 基于前瞻性模型"""
    result = predict_next_day(kline_data, realtime)
    return result['score']


def judge_trend(kline_data):
    """判断趋势方向"""
    if len(kline_data) < 10:
        return 'sideways'
    closes = [k['close'] for k in kline_data]
    ma5 = calc_ma(closes, 5)
    ma10 = calc_ma(closes, 10)
    ma20 = calc_ma(closes, 20)
    if ma5 and ma10 and ma20:
        if ma5 > ma10 > ma20:
            return 'up'
        elif ma5 < ma10 < ma20:
            return 'down'
    recent = closes[-10:]
    x = np.arange(len(recent))
    slope = np.polyfit(x, recent, 1)[0]
    avg_price = np.mean(recent)
    if abs(slope) / avg_price < 0.005:
        return 'sideways'
    return 'up' if slope > 0 else 'down'


def estimate_next_day_prob(kline_data, realtime=None):
    """评估次日涨幅2%-5%的概率"""
    result = predict_next_day(kline_data, realtime)
    return result['prob']


# ==================== 概率校准模块 v5 (简化版) ====================

def load_calibration():
    """
    从 data/config/strategy_params.json 加载概率校准参数
    返回: {'offset': float}
    """
    data_dir = os.environ.get('DATA_DIR', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data'))
    params_file = os.path.join(data_dir, 'config', 'strategy_params.json')
    if os.path.exists(params_file):
        try:
            with open(params_file, 'r') as f:
                data = json.load(f)
                cal = data.get('prob_calibration', {})
                # v5: 只返回offset，忽略bucket_calibrations
                return {'offset': cal.get('offset', 0.0)}
        except (json.JSONDecodeError, IOError):
            pass
    return {'offset': 0.0}


# ★保留兼容接口但不再使用桶校准
def calibrate_probability(raw_prob, calibration=None):
    """
    v5: 简化概率校准
    只用全局偏移，不做桶校准
    保底0.15
    """
    if calibration is None:
        calibration = load_calibration()
    
    offset = calibration.get('offset', 0.0)
    if offset != 0.0:
        calibrated = raw_prob + offset
        return max(0.15, min(0.85, calibrated))
    
    return raw_prob
