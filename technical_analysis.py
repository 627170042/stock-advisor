"""
A股T+1短线选股系统 - 技术分析模块 v6

v5→v6 核心重构：
1. K线从20根→120根，所有指标计算有效化
2. 核心逻辑从"跌多必反弹"→"趋势延续+动量加速"
3. 概率基线从0.30→0.18（21.6%实际胜率的现实校准）
4. 新增均线多头排列+放量突破信号（最强买入信号）
5. 新增大盘环境修正（系统性风险过滤）
6. 消除涨跌幅三重计算（v5在3个维度重复使用changepercent）

v5铁证（必须解决的失败）：
- 命中组预测概率66% vs 未命中组74% → 模型概率是反向指标
- "跌多必反弹"完全错误：小跌(-1~0%)次日胜率仅8%，均值-1.27%
- 推荐日涨0~2%的8只股全部未命中
- K线20根导致MACD/RSI/均线计算全部无效
"""
import numpy as np
import json
import os


# ==================== 基础指标计算（适配120根K线） ====================

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
    """
    ★v6: 120根K线下MACD计算完整有效
    v5用20根K线，slow=26就已超出数据范围，返回None
    """
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
    """v6: 120根K线下RSI计算完全有效"""
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


# ==================== v6: 趋势延续+动量加速 预测模型 ====================

def predict_next_day(kline_data, stock_info=None, sector_heat=None, market_env=None):
    """
    v6: 趋势延续+动量加速 预测模型

    ★核心思想转变★
    v5: "跌多必反弹" → 数据证明完全错误
    v6: "趋势延续+动量加速" → 顺势而为，找上涨趋势中的加速点

    六大维度（重新设计，消除三重计算）：
    1. 均线趋势信号 (0-30分) — 均线多头排列是趋势延续的基础
    2. 放量突破信号 (0-25分) — 资金确认是趋势加速的核心
    3. 量价健康度 (0-15分) — 涨放量跌缩量=真趋势
    4. 动量位置 (0-15分) — RSI/KDJ/MACD共振验证
    5. 板块热度 (0-10分) — 顺势板块>逆势板块
    6. 大盘环境修正 (±概率) — 系统性风险过滤

    概率基线: 0.18（而非0.30，与实际21.6%胜率匹配）
    """
    if not kline_data or len(kline_data) < 20:
        return {'prob': 0.10, 'signals': [], 'score': 10}

    closes = [k['close'] for k in kline_data]
    volumes = [k['volume'] for k in kline_data]
    current = closes[-1]

    # ★v6: 概率基线0.18 — 2%+涨幅是小概率事件，不要过度自信
    prob = 0.18
    signals = []
    score = 0

    # 计算均线（需要足够的数据）
    ma5 = calc_ma(closes, 5)
    ma10 = calc_ma(closes, 10)
    ma20 = calc_ma(closes, 20)
    ma60 = calc_ma(closes, 60) if len(closes) >= 60 else None

    # =====================================================
    # 维度1: 均线趋势信号 (0-30分, ±0.15概率) ★★★核心★★★
    # v6思想: 均线多头排列 = 趋势向上，次日大概率延续
    # =====================================================
    trend_score = 0

    if ma5 and ma10 and ma20:
        # ★最强信号: 均线多头排列 MA5>MA10>MA20
        if ma5 > ma10 > ma20:
            trend_score += 15
            signals.append("均线多头排列(趋势向上)")

            # MA60也多头 → 长线趋势确认
            if ma60 and ma20 > ma60:
                trend_score += 8
                signals.append("长线趋势确认(MA60多头)")

            # 股价在MA5附近（不是远离均线，有支撑）
            if current >= ma5 * 0.98 and current <= ma5 * 1.03:
                trend_score += 5
                signals.append("股价贴近MA5(有支撑)")

        # 均线空头排列 → 趋势向下，大概率继续跌
        elif ma5 < ma10 < ma20:
            trend_score -= 10
            prob -= 0.08
            signals.append("均线空头排列(趋势向下)")
        else:
            # 均线缠绕 → 无明确趋势
            trend_score += 2

    # MA20趋势方向（即使不是完美多头排列）
    if ma20:
        # 5日前的MA20对比（MA20的斜率）
        if len(closes) >= 25:
            ma20_5ago = calc_ma(closes[:-5], 20) if len(closes[:-5]) >= 20 else None
            if ma20_5ago and ma20 > ma20_5ago * 1.01:
                trend_score += 5
                signals.append("MA20上行(中期趋势向好)")
            elif ma20_5ago and ma20 < ma20_5ago * 0.99:
                trend_score -= 3

    score += max(0, trend_score)
    if trend_score >= 20:
        prob += 0.12
    elif trend_score >= 10:
        prob += 0.06
    elif trend_score < 0:
        prob -= 0.03

    # =====================================================
    # 维度2: 放量突破信号 (0-25分, ±0.12概率) ★★★核心★★★
    # v6思想: 放量突破 = 资金进场确认，趋势加速的开始
    # =====================================================
    breakout_score = 0

    if len(kline_data) >= 10:
        avg_vol_10 = sum(volumes[-10:]) / 10

        # ★最强信号: 放量突破前期高点
        if len(kline_data) >= 20:
            high_20 = max(k['high'] for k in kline_data[-20:-1])
            if current > high_20 and volumes[-1] > avg_vol_10 * 1.3:
                breakout_score += 12
                signals.append("放量突破20日高点(★强信号)")

        # 放量突破MA20
        if ma20 and current > ma20 and volumes[-1] > avg_vol_10 * 1.2:
            breakout_score += 8
            signals.append("放量站上MA20")

        # 温和放量上涨（非暴涨，而是趋势性放量）
        if len(closes) >= 3 and len(volumes) >= 3:
            up_days_3 = sum(1 for i in range(1, 4) if closes[-i] > closes[-i-1])
            vol_trend = volumes[-1] > volumes[-2] > volumes[-3]
            if up_days_3 >= 2 and vol_trend and closes[-1] > closes[-3]:
                breakout_score += 6
                signals.append("温和放量上涨(趋势延续)")

        # 放量长阳线（涨幅>2%，量比>1.5）
        if stock_info:
            today_change = stock_info.get('changepercent', 0)
            if today_change >= 2 and len(volumes) >= 2:
                vol_ratio = volumes[-1] / (sum(volumes[-6:-1]) / 5) if len(volumes) >= 6 else 0
                if vol_ratio > 1.5:
                    breakout_score += 8
                    signals.append(f"放量长阳(涨{today_change:.1f}%量比{vol_ratio:.1f})")

        # 突破布林上轨（强势突破信号）
        boll_upper, boll_mid, boll_lower = calc_boll(closes)
        if boll_upper and current > boll_upper and volumes[-1] > avg_vol_10:
            breakout_score += 5
            signals.append("突破布林上轨(强势)")

    score += max(0, breakout_score)
    if breakout_score >= 15:
        prob += 0.10
    elif breakout_score >= 8:
        prob += 0.05

    # =====================================================
    # 维度3: 量价健康度 (0-15分, ±0.06概率)
    # 核心: 上涨放量+下跌缩量 = 真实趋势
    # =====================================================
    vp_score = 0

    if len(kline_data) >= 10:
        up_vol_sum = 0
        down_vol_sum = 0
        up_days = 0
        down_days = 0

        for i in range(1, min(11, len(kline_data))):
            if closes[-i] > closes[-i-1]:
                up_vol_sum += volumes[-i]
                up_days += 1
            else:
                down_vol_sum += volumes[-i]
                down_days += 1

        if up_days > 0 and down_days > 0:
            avg_up_vol = up_vol_sum / up_days
            avg_down_vol = down_vol_sum / down_days

            if avg_up_vol > avg_down_vol * 1.5:
                vp_score += 12
                signals.append("量价健康(涨放量跌缩量)")
            elif avg_up_vol > avg_down_vol * 1.2:
                vp_score += 8
                signals.append("量价尚可")
            elif avg_up_vol > avg_down_vol:
                vp_score += 4
            else:
                # 上涨缩量下跌放量 → 趋势不可靠
                vp_score -= 5
                signals.append("量价背离(风险)")

    score += max(0, vp_score)
    if vp_score >= 10:
        prob += 0.05
    elif vp_score < 0:
        prob -= 0.04

    # =====================================================
    # 维度4: 动量位置 (0-15分, ±0.08概率)
    # v6思想: 寻找动量加速区，不是超卖反弹区
    # =====================================================
    momentum_score = 0

    # RSI — 趋势加速区(RSI 50-65)最优
    rsi = calc_rsi(closes, 14)
    if rsi is not None:
        if 50 <= rsi <= 65:
            # ★最佳动量区：趋势确立但未过热
            momentum_score += 8
            signals.append(f"RSI={rsi:.0f}(动量加速区)")
        elif 65 < rsi <= 75:
            # 偏强但需警惕
            momentum_score += 4
            signals.append(f"RSI={rsi:.0f}(偏强)")
        elif 40 <= rsi < 50:
            # 弱势但可能反转
            momentum_score += 2
        elif rsi > 75:
            # 超买，回调风险大
            momentum_score -= 5
            prob -= 0.05
            signals.append(f"RSI={rsi:.0f}(超买风险)")
        elif rsi < 30:
            # 极度超卖，v5会加分，但v6认为趋势向下
            momentum_score -= 3
            prob -= 0.03
            signals.append(f"RSI={rsi:.0f}(超卖，趋势弱)")

    # KDJ — 金叉确认趋势加速
    K, D, J = calc_kdj(kline_data)
    if K is not None:
        if K > D and K < 65:
            momentum_score += 5
            if K < 50:
                signals.append("KDJ低位金叉(趋势启动)")
            else:
                signals.append("KDJ金叉(趋势确认)")
        elif K > D and K >= 75:
            momentum_score -= 3
            prob -= 0.03
            signals.append("KDJ超买(谨慎)")
        elif K < D and K < 30:
            momentum_score -= 2  # v6: 不再视为超卖反弹机会

    # MACD — 红柱放大=趋势加速
    dif, dea, macd_bar = calc_macd(closes)
    if dif is not None and dea is not None:
        if dif > dea and dif > 0:
            # 零轴上方金叉 = 趋势加速
            momentum_score += 6
            signals.append("MACD零轴上方(趋势加速)")
        elif dif > dea and dif < 0:
            # 零轴下方金叉 = 趋势可能反转
            momentum_score += 3
            signals.append("MACD底部金叉(趋势转强)")
        elif dif < dea and dif > 0:
            # 零轴上方死叉 = 趋势可能见顶
            momentum_score -= 3
            prob -= 0.03
            signals.append("MACD高位死叉(警惕)")

        # MACD柱状图趋势
        if macd_bar and macd_bar > 0:
            momentum_score += 2

    score += max(0, momentum_score)
    if momentum_score >= 10:
        prob += 0.06
    elif momentum_score >= 5:
        prob += 0.03
    elif momentum_score < 0:
        prob -= 0.04

    # =====================================================
    # 维度5: 板块热度 (0-10分, ±0.05概率)
    # v6: 顺势板块加分，逆势板块减分
    # =====================================================
    heat_level = 'warm'
    heat_score_val = 50
    sector_name = ''
    if sector_heat:
        heat_level = sector_heat.get('heat_level', 'warm')
        heat_score_val = sector_heat.get('heat_score', 50)
        sector_name = sector_heat.get('sector_name', '')

    if heat_level == 'hot':
        # 热门板块 → 趋势延续概率高
        score += 8
        prob += 0.04
        signals.append(f"热门板块({sector_name})")
    elif heat_level == 'cold':
        # 冷门板块 → 即使上涨也可能是反弹
        score -= 3
        prob -= 0.03
        if stock_info and stock_info.get('changepercent', 0) > 2:
            signals.append(f"冷门板块上涨(可能反弹)")
    else:
        score += 2

    # =====================================================
    # 维度6: ★v6新增★ 大盘环境修正 (±0.10概率)
    # v5完全没有大盘过滤 → 在熊市依然推荐 → 大面积亏损
    # =====================================================
    if market_env:
        env_level = market_env.get('level', 'neutral')
        env_score = market_env.get('score', 50)

        if env_level == 'bull':
            prob += 0.06
            signals.append("大盘强势(环境友好)")
        elif env_level == 'bear':
            prob -= 0.08
            signals.append("大盘弱势(环境不利)")
        else:
            if env_score >= 55:
                prob += 0.02
            elif env_score <= 45:
                prob -= 0.03

    # =====================================================
    # 当日涨跌幅修正（★仅在此处使用一次★，不再三重计算）
    # =====================================================
    if stock_info:
        today_change = stock_info.get('changepercent', 0)

        # v5问题：涨跌幅在potential_score + tech维度1 + tech维度3 三重使用
        # v6：涨跌幅只在技术分析中作为一个修正因子，一次性使用

        if today_change >= 2 and today_change <= 5:
            # 温和上涨+技术形态好 = 趋势延续信号
            # 这不同于v5的"追涨惩罚"
            if trend_score >= 10 and breakout_score >= 8:
                # 趋势好+放量突破+温和上涨 = 最强组合
                prob += 0.05
                signals.append("趋势确认上涨(加分)")
            # 如果没有趋势支撑，单纯上涨是风险
        elif today_change > 5:
            # 大涨后回调风险
            prob -= 0.05
            signals.append("当日大涨(回调风险)")
        elif -1 <= today_change <= 1:
            # 横盘整理，需要方向选择
            # v5认为这是"安全区"，数据证明8%胜率
            prob -= 0.02
        elif today_change < -3:
            # 大跌，v5认为有反弹机会，数据证明高波动
            # v6：不主动给加分，趋势延续逻辑下大跌是趋势断裂
            prob -= 0.03

    # =====================================================
    # 最终概率归一化
    # =====================================================
    prob = max(0.08, min(0.75, prob))
    score = max(0, min(100, score))

    # ★v6简化: 概率校准 — 全局偏移
    prob = _simple_calibrate(prob)

    return {
        'prob': prob,
        'signals': signals,
        'score': score,
        'trend_score': max(0, trend_score),
        'breakout_score': max(0, breakout_score),
        'vp_score': max(0, vp_score),
        'momentum_score': max(0, momentum_score),
    }


def _simple_calibrate(raw_prob):
    """v6简化校准: 只做全局偏移"""
    calibration = load_calibration()
    offset = calibration.get('offset', 0.0)
    if offset != 0.0:
        calibrated = raw_prob + offset
        return max(0.08, min(0.75, calibrated))
    return raw_prob


# ==================== 兼容旧接口 ====================

def tech_score(kline_data, realtime=None):
    """综合技术评分 (0-100)"""
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


# ==================== 概率校准模块 v6 ====================

def load_calibration():
    """从 data/config/strategy_params.json 加载概率校准参数"""
    data_dir = os.environ.get('DATA_DIR', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data'))
    params_file = os.path.join(data_dir, 'config', 'strategy_params.json')
    if os.path.exists(params_file):
        try:
            with open(params_file, 'r') as f:
                data = json.load(f)
                cal = data.get('prob_calibration', {})
                return {'offset': cal.get('offset', 0.0)}
        except (json.JSONDecodeError, IOError):
            pass
    return {'offset': 0.0}


def calibrate_probability(raw_prob, calibration=None):
    """v6概率校准"""
    if calibration is None:
        calibration = load_calibration()
    offset = calibration.get('offset', 0.0)
    if offset != 0.0:
        calibrated = raw_prob + offset
        return max(0.08, min(0.75, calibrated))
    return raw_prob
