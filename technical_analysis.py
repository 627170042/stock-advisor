"""
A股T+1短线选股系统 - 技术分析模块 v3
核心改进：从前瞻性角度评估次日上涨概率
重点关注：趋势延续性、量价健康度、回调后反弹信号、资金承接力

v3新增：概率校准机制
- 基于历史命中/未命中的实际分布，自动修正概率偏移
- predict_next_day() 末尾应用 calibrate_probability()
- 校准参数从 data/config/strategy_params.json 读取
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


# ==================== 前瞻性分析：次日上涨概率核心模型 ====================

def predict_next_day(kline_data, stock_info=None):
    """
    前瞻性预测次日涨幅2%-5%的概率
    核心逻辑：不是"今天涨了多少"，而是"明天最可能怎么走"
    
    六大维度：
    1. 趋势延续性 — 上升趋势中的股票次日更可能继续
    2. 回调反弹信号 — 缩量回调后放量企稳，次日反弹概率高
    3. 量价健康度 — 量价配合良好说明资金真实进场
    4. 关键位置突破 — 刚突破压力位的股票次日有惯性
    5. 筹码承接力 — 均线密集区支撑力度
    6. 动量衰减检测 — 连涨后动量减弱，次日回调概率增大
    """
    if not kline_data or len(kline_data) < 8:
        return {'prob': 0.3, 'signals': [], 'score': 30}
    
    closes = [k['close'] for k in kline_data]
    volumes = [k['volume'] for k in kline_data]
    current = closes[-1]
    
    prob = 0.40  # 基础概率（略低于随机，因为2-5%是窄区间）
    signals = []
    score = 0
    
    # =====================================================
    # 维度1: 趋势延续性 (0-20分, ±0.15概率)
    # =====================================================
    ma5 = calc_ma(closes, 5)
    ma10 = calc_ma(closes, 10)
    ma20 = calc_ma(closes, 20)
    
    trend_score = 0
    if ma5 and ma10:
        if ma5 > ma10:
            trend_score += 6
            signals.append("MA5>MA10(短期多头)")
        if current > ma5:
            trend_score += 4
            signals.append("站上5日线")
    if ma20:
        if current > ma20:
            trend_score += 4
            signals.append("站上20日线")
        if ma5 and ma10 and ma20 and ma5 > ma10 > ma20:
            trend_score += 6
            signals.append("均线多头排列")
    if trend_score >= 12:
        prob += 0.10
    elif trend_score >= 6:
        prob += 0.04
    elif trend_score == 0:
        prob -= 0.05
    score += trend_score
    
    # =====================================================
    # 维度2: 回调反弹信号 (0-20分, ±0.15概率) ★★核心★★
    # 逻辑：上涨趋势中的缩量回调 → 放量企稳 → 次日反弹
    # 这比追涨更可靠！
    # =====================================================
    rebound_score = 0
    
    if len(closes) >= 5:
        # 检测近期是否有回调后企稳形态
        recent_5_closes = closes[-5:]
        recent_5_volumes = volumes[-5:]
        
        # 情况A: 前几天回调，最近1-2天企稳
        # 先跌后稳
        if (recent_5_closes[-3] < recent_5_closes[-4] and  # 3天前跌
            recent_5_closes[-1] >= recent_5_closes[-2]):    # 最近1天企稳或上涨
            rebound_score += 8
            signals.append("回调企稳形态")
            
            # 如果企稳日放量，反弹信号更强
            if recent_5_volumes[-1] > recent_5_volumes[-2] * 1.2:
                rebound_score += 5
                signals.append("企稳日放量(资金承接)")
        
        # 情况B: 回调缩量（主力未出逃）
        if len(volumes) >= 5:
            avg_vol_5 = sum(volumes[-5:]) / 5
            if recent_5_closes[-2] < recent_5_closes[-3]:  # 昨天回调
                if recent_5_volumes[-2] < avg_vol_5 * 0.8:  # 缩量回调
                    rebound_score += 5
                    signals.append("缩量回调(主力未出)")
        
        # 情况C: 今天的涨幅在0-3%区间（不追涨，而是确认启动）
        if stock_info:
            today_change = stock_info.get('changepercent', 0)
            if -2 <= today_change <= 3:
                rebound_score += 4
                if today_change < 0:
                    signals.append("微跌蓄势")
                elif today_change <= 1:
                    signals.append("温和启动")
                else:
                    signals.append("适度上涨")
    
    if rebound_score >= 10:
        prob += 0.12
    elif rebound_score >= 5:
        prob += 0.05
    score += rebound_score
    
    # =====================================================
    # 维度3: 量价健康度 (0-20分, ±0.12概率)
    # 逻辑：上涨放量、下跌缩量 = 健康的量价关系
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
            
            if avg_up_vol > avg_down_vol * 1.3:  # 上涨放量明显大于下跌
                vp_score += 12
                signals.append("量价健康(涨放量跌缩量)")
            elif avg_up_vol > avg_down_vol:
                vp_score += 6
                signals.append("量价尚可")
            else:
                vp_score -= 3
                signals.append("量价背离(风险)")
    
    # 最近3日连续缩量回调后放量
    if len(volumes) >= 4:
        if (volumes[-1] > volumes[-2] * 1.3 and
            volumes[-2] < volumes[-3] and
            closes[-1] > closes[-2]):
            vp_score += 8
            signals.append("缩量后放量上攻(强烈信号)")
    
    if vp_score >= 12:
        prob += 0.10
    elif vp_score >= 6:
        prob += 0.04
    elif vp_score < 0:
        prob -= 0.06
    score += max(0, vp_score)
    
    # =====================================================
    # 维度4: 关键位置突破 (0-15分, ±0.10概率)
    # 逻辑：刚突破重要均线或前高，次日有惯性
    # =====================================================
    breakout_score = 0
    
    if len(kline_data) >= 3:
        # 突破20日线
        if ma20:
            if closes[-2] < ma20 and current > ma20:
                breakout_score += 8
                signals.append("突破20日线")
            # 在20日线上方3%以内（刚突破不久）
            elif ma20 < current < ma20 * 1.03:
                breakout_score += 4
                signals.append("20日线上方不远")
        
        # 突破近5日高点
        if len(closes) >= 6:
            high_5 = max(closes[-6:-1])  # 前5日最高
            if current > high_5 and closes[-2] <= high_5:
                breakout_score += 5
                signals.append("突破5日高点")
    
    if breakout_score >= 6:
        prob += 0.08
    elif breakout_score > 0:
        prob += 0.03
    score += breakout_score
    
    # =====================================================
    # 维度5: MACD & KDJ 动量信号 (0-15分, ±0.10概率)
    # 逻辑：MACD金叉/红柱放大 + KDJ未超买 = 次日有上行空间
    # =====================================================
    momentum_score = 0
    
    dif, dea, macd_bar = calc_macd(closes)
    if dif is not None and dea is not None:
        if dif > 0 and dif > dea:
            momentum_score += 6
            signals.append("MACD红柱(强势)")
        elif dif > dea and dif < 0:
            momentum_score += 4
            signals.append("MACD底部金叉(回升)")
        elif dif < dea and dif > 0:
            momentum_score -= 2  # 高位死叉
            signals.append("MACD高位回落")
    
    K, D, J = calc_kdj(kline_data)
    if K is not None:
        if K > D and K < 75:
            momentum_score += 5
            signals.append("KDJ金叉未超买")
        elif K > D and K >= 75:
            momentum_score += 1
            momentum_score -= 3  # 超买风险
            signals.append("KDJ超买区(谨慎)")
        elif K < D and K < 30:
            momentum_score += 3
            signals.append("KDJ超卖区(反弹可能)")
        if J is not None and J > 100:
            momentum_score -= 4
            signals.append("J值超100(极度超买)")
    
    # RSI 位置
    rsi = calc_rsi(closes, 14)
    if rsi is not None:
        if 40 <= rsi <= 65:
            momentum_score += 4
            signals.append(f"RSI={rsi:.0f}(偏多区间)")
        elif 30 <= rsi < 40:
            momentum_score += 3
            signals.append(f"RSI={rsi:.0f}(超卖反弹)")
        elif rsi > 75:
            momentum_score -= 4
            signals.append(f"RSI={rsi:.0f}(超买)")
    
    if momentum_score >= 8:
        prob += 0.08
    elif momentum_score >= 3:
        prob += 0.03
    elif momentum_score < 0:
        prob -= 0.06
    score += max(0, momentum_score)
    
    # =====================================================
    # 维度6: 动量衰减检测 (0-10分, -0.15概率)
    # 逻辑：连涨过多 = 次日回调概率大，要惩罚！
    # =====================================================
    decay_score = 0
    
    # 连涨天数
    consecutive_up = 0
    for i in range(len(closes)-1, 0, -1):
        if closes[i] > closes[i-1]:
            consecutive_up += 1
        else:
            break
    
    if consecutive_up >= 5:
        prob -= 0.12
        decay_score -= 8
        signals.append(f"连涨{consecutive_up}天(动量衰减)")
    elif consecutive_up >= 4:
        prob -= 0.06
        decay_score -= 4
        signals.append(f"连涨{consecutive_up}天(注意回调)")
    elif consecutive_up == 1:
        prob += 0.03
        decay_score += 3
        signals.append("刚启动(动量充沛)")
    elif consecutive_up == 2:
        prob += 0.02
        decay_score += 2
    
    # 涨幅递减（连续上涨但每日涨幅变小 = 动量衰减）
    if consecutive_up >= 3 and len(closes) >= 4:
        changes = []
        for i in range(1, min(consecutive_up + 1, len(closes))):
            chg = (closes[-i] - closes[-i-1]) / closes[-i-1] * 100
            changes.append(chg)
        changes.reverse()
        if len(changes) >= 3 and changes[-1] < changes[-2] < changes[-3]:
            prob -= 0.06
            decay_score -= 5
            signals.append("涨幅递减(动量衰减)")
    
    # 当日涨幅过高（追高风险）
    if stock_info:
        today_change = stock_info.get('changepercent', 0)
        if today_change > 7:
            prob -= 0.10
            decay_score -= 5
            signals.append(f"当日涨{today_change:.1f}%(追高风险)")
        elif today_change > 5:
            prob -= 0.04
            decay_score -= 2
            signals.append(f"当日涨{today_change:.1f}%(偏高)")
    
    score += max(0, 10 + decay_score)  # 基础10分，扣除衰减
    
    # =====================================================
    # 最终概率归一化
    # =====================================================
    prob = max(0.10, min(0.90, prob))
    score = max(0, min(100, score))

    # ★v3: 概率校准 - 将模型原始输出修正为接近实际胜率
    prob = calibrate_probability(prob)

    return {
        'prob': prob,
        'signals': signals,
        'score': score,
        'trend_score': trend_score,
        'rebound_score': rebound_score,
        'vp_score': max(0, vp_score),
        'breakout_score': breakout_score,
        'momentum_score': max(0, momentum_score),
        'decay_penalty': abs(decay_score),
    }


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


# ==================== 概率校准模块 v3 ====================

def load_calibration():
    """
    从 data/config/strategy_params.json 加载概率校准参数
    返回: {'offset': float, 'scale': float, 'bucket_calibrations': dict}
    """
    data_dir = os.environ.get('DATA_DIR', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data'))
    params_file = os.path.join(data_dir, 'config', 'strategy_params.json')
    if os.path.exists(params_file):
        try:
            with open(params_file, 'r') as f:
                data = json.load(f)
                return data.get('prob_calibration', {'offset': 0.0, 'scale': 1.0, 'bucket_calibrations': {}})
        except (json.JSONDecodeError, IOError):
            pass
    return {'offset': 0.0, 'scale': 1.0, 'bucket_calibrations': {}}


def calibrate_probability(raw_prob, calibration=None):
    """
    校准概率 - 将模型原始输出修正为接近实际胜率

    策略:
    1. 优先使用分桶校准（样本>=5时用桶内融合值）
    2. 退化为全局偏移校准
    3. 无校准数据时原值返回

    防护: 结果始终裁剪到 [0.15, 0.90]
    ★保底0.15: 不因历史0%胜率就完全否定一个概率区间
    """
    if calibration is None:
        calibration = load_calibration()

    # 优先使用分桶校准
    buckets = calibration.get('bucket_calibrations', {})
    if buckets:
        for bucket_range, bucket_data in buckets.items():
            parts = bucket_range.split('-')
            low, high = float(parts[0]), float(parts[1])
            if low <= raw_prob < high:
                sample_count = bucket_data.get('count', 0)
                if sample_count >= 5:  # 样本量>=5才用分桶校准
                    # ★使用融合值而非直接替换
                    calibrated = bucket_data.get('fused_rate', bucket_data.get('actual_win_rate', raw_prob))
                    return max(0.15, min(0.90, calibrated))

    # 退化为全局偏移
    offset = calibration.get('offset', 0.0)
    if offset != 0.0:
        calibrated = raw_prob + offset
        return max(0.15, min(0.90, calibrated))

    # 无校准数据，原值返回
    return raw_prob
