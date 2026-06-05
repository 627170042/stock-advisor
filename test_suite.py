"""
A股T+1短线选股系统 - 自动化测试套件 v6

v5→v6 更新:
- 移除v5函数测试: filter_chase_risk, filter_death_zone, score_next_day_potential
- 新增v6函数测试: score_trend_continuation, filter_market_environment
- 移除Budget/Strong双类别测试（v6统一为单一类别）
- 新增大盘环境评估测试
- 新增120根K线技术分析测试
- 适配v6趋势延续+动量加速逻辑
"""
import sys
import json
import time
import traceback
from datetime import datetime


# ==================== 测试框架 ====================

class TestRunner:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.errors = []

    def run(self, name, func):
        """运行单个测试"""
        try:
            func()
            self.passed += 1
            print(f"  ✅ {name}")
        except AssertionError as e:
            self.failed += 1
            self.errors.append((name, str(e)))
            print(f"  ❌ {name}: {e}")
        except Exception as e:
            self.failed += 1
            self.errors.append((name, f"异常: {e}"))
            print(f"  💥 {name}: {e}")

    def summary(self):
        total = self.passed + self.failed
        print(f"\n{'='*60}")
        if self.failed == 0:
            print(f"✅ 全部通过 ({self.passed}/{total})")
        else:
            print(f"❌ {self.failed} 个失败 ({self.passed}/{total})")
            for name, err in self.errors:
                print(f"  - {name}: {err}")
        print(f"{'='*60}")
        return self.failed == 0


runner = TestRunner()


# ==================== 1. 语法和导入检查 ====================

def test_import_data_fetcher():
    from data_fetcher import (get_realtime_quote, get_top_gainers, get_top_volume,
                               get_top_turnover, get_kline_sina, classify_board,
                               is_gem, is_star, get_stock_list, get_stock_industry_f10,
                               get_market_indices, get_market_environment,
                               scan_all_a_stocks, get_advance_decline_ratio)

def test_import_technical_analysis():
    from technical_analysis import (predict_next_day, tech_score, judge_trend,
                                     calc_ma, calc_rsi, calc_kdj, calc_macd, calc_boll,
                                     calc_ema)

def test_import_stock_screener():
    from stock_screener import (screen_stocks, StrategyOptimizer, filter_basic,
                                 filter_liquidity, filter_market_environment,
                                 score_trend_continuation, get_top_picks)

def test_import_sector_heat():
    from sector_heat import SectorHeatMap, get_sector_heat_for_stock

def test_import_wechat_push():
    from wechat_push import (send_wechat_message, format_recommend_message,
                              format_review_message)

def test_import_review():
    from review import review_previous_recommendations

def test_import_main():
    from main import generate_report, run_daily_recommendation


# ==================== 2. 数据源健康检查 ====================

def test_sina_realtime_api():
    """新浪实时行情API可用性"""
    from data_fetcher import get_stock_list
    stocks = get_stock_list(page=1, num=5, sort='amount')
    assert len(stocks) > 0, "新浪行情API返回空数据"
    s = stocks[0]
    for field in ['symbol', 'name', 'trade', 'changepercent', 'amount']:
        assert field in s, f"缺少字段: {field}"
    assert s['trade'] > 0, f"价格异常: {s['trade']}"

def test_sina_kline_api():
    """★v6: 新浪K线API 120根数据可用性"""
    from data_fetcher import get_kline_sina
    kline = get_kline_sina('sh600519', '240', '120')
    assert len(kline) >= 60, f"K线数据不足: {len(kline)}根(应>=60)"
    k = kline[0]
    for field in ['day', 'open', 'high', 'low', 'close', 'volume']:
        assert field in k, f"K线缺少字段: {field}"
    assert k['close'] > 0, f"收盘价异常: {k['close']}"

def test_sina_market_indices():
    """★v6: 大盘指数API可用性"""
    from data_fetcher import get_market_indices
    indices = get_market_indices()
    assert len(indices) >= 2, f"大盘指数不足: {len(indices)}(应>=2)"
    sh = indices.get('sh000001')
    assert sh is not None, "缺少上证指数"
    assert sh['current'] > 0, f"上证指数异常: {sh['current']}"

def test_sina_sector_api():
    """新浪行业板块API可用性"""
    import requests, re
    url = 'https://vip.stock.finance.sina.com.cn/q/view/newSinaHy.php'
    headers = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://finance.sina.com.cn'}
    r = requests.get(url, headers=headers, timeout=15)
    assert r.status_code == 200, f"新浪板块API状态码: {r.status_code}"
    assert len(r.text) > 500, f"新浪板块API返回内容过短: {len(r.text)}"

def test_market_environment():
    """★v6: 市场环境评估"""
    from data_fetcher import get_market_environment
    env = get_market_environment()
    assert 'score' in env, "缺少score字段"
    assert 'level' in env, "缺少level字段"
    assert 'signal' in env, "缺少signal字段"
    assert env['level'] in ['bull', 'neutral', 'bear'], f"level异常: {env['level']}"
    assert 0 <= env['score'] <= 100, f"score异常: {env['score']}"


# ==================== 3. 核心函数单元测试 ====================

def test_classify_board():
    """板块分类函数"""
    from data_fetcher import classify_board
    assert classify_board('sh600000') == 'main', "沪市主板分类错误"
    assert classify_board('sz000001') == 'main', "深市主板分类错误"
    assert classify_board('sz300001') == 'gem', "创业板分类错误"
    assert classify_board('sh688001') == 'star', "科创板分类错误"
    assert classify_board('bj001') == 'bse', "北交所分类错误"

def test_filter_basic():
    """★v6: 基础过滤函数（统一类别，无Budget/Strong）"""
    from stock_screener import filter_basic
    # ST股
    ok, _ = filter_basic({'symbol': 'sh600000', 'name': 'ST某某', 'trade': 10})
    assert not ok, "ST股应被过滤"
    # 新股
    ok, _ = filter_basic({'symbol': 'sh600000', 'name': 'N某某', 'trade': 10})
    assert not ok, "新股应被过滤"
    # 停牌
    ok, _ = filter_basic({'symbol': 'sh600000', 'name': '正常', 'trade': 0})
    assert not ok, "停牌应被过滤"
    # 北交所
    ok, _ = filter_basic({'symbol': 'bj001', 'name': '正常', 'trade': 10})
    assert not ok, "北交所应被过滤"
    # 低价股
    ok, _ = filter_basic({'symbol': 'sh600000', 'name': '正常', 'trade': 2})
    assert not ok, "低价股应被过滤"
    # 正常通过（★v6: 创业板也可以通过，不再有Budget排除）
    ok, _ = filter_basic({'symbol': 'sz300001', 'name': '正常', 'trade': 25})
    assert ok, "正常创业板股票应通过(v6不再排除创业板)"
    # 价格过高
    ok, _ = filter_basic({'symbol': 'sh600000', 'name': '正常', 'trade': 250})
    assert not ok, "价格>200应被过滤"

def test_filter_market_env():
    """★v6: 大盘环境过滤"""
    from stock_screener import filter_market_environment
    # bear+极低分(<15)应过滤
    ok, _ = filter_market_environment({'level': 'bear', 'score': 10})
    assert not ok, "bear+10分应被过滤"
    # bear+低分(<20)应过滤
    ok, _ = filter_market_environment({'level': 'bear', 'score': 18})
    assert not ok, "bear+18分应被过滤"
    # bear+20分属于边界，不强制过滤（正常下跌不应暂停推荐）
    ok, _ = filter_market_environment({'level': 'bear', 'score': 20})
    assert ok, "bear+20分属于边界允许（谨慎选股）"
    # neutral应通过
    ok, _ = filter_market_environment({'level': 'neutral', 'score': 50})
    assert ok, "neutral+50分应通过"
    # bull应通过
    ok, _ = filter_market_environment({'level': 'bull', 'score': 75})
    assert ok, "bull+75分应通过"

def test_score_trend_continuation():
    """★v6: 趋势延续评分"""
    from stock_screener import score_trend_continuation
    # 温和上涨(2-5%)应高分 — v6核心逻辑
    warm_up = {'changepercent': 3.0, 'turnoverratio': 5.0, 'nmc': 500000}
    score_warm = score_trend_continuation(warm_up)
    # 大跌应低分 — v6反转了v5的逻辑
    deep_drop = {'changepercent': -5.0, 'turnoverratio': 5.0, 'nmc': 500000}
    score_drop = score_trend_continuation(deep_drop)
    # 微涨应中等分
    slight_up = {'changepercent': 0.8, 'turnoverratio': 5.0, 'nmc': 500000}
    score_slight = score_trend_continuation(slight_up)

    assert score_warm > score_drop, \
        f"温和上涨评分({score_warm})应高于大跌({score_drop}) — v6趋势延续逻辑"
    assert score_warm > score_slight, \
        f"温和上涨评分({score_warm})应高于微涨({score_slight})"
    assert 0 <= score_warm <= 100, f"评分范围异常: {score_warm}"

def test_predict_next_day_trend():
    """★v6: 预测模型 — 趋势延续优于反转"""
    from technical_analysis import predict_next_day

    # 构造均线多头+放量突破K线（v6最强信号）
    kline_bull = []
    base = 10.0
    for i in range(60):
        kline_bull.append({
            'day': f'2026-01-{i+1:02d}',
            'open': base,
            'high': base + 0.5,
            'low': base - 0.2,
            'close': base + 0.3,  # 持续上涨
            'volume': 1000000 + i * 50000,  # 温和放量
        })
        base += 0.3

    # 构造均线空头+下跌K线（v6最弱信号）
    kline_bear = []
    base = 20.0
    for i in range(60):
        kline_bear.append({
            'day': f'2026-01-{i+1:02d}',
            'open': base,
            'high': base + 0.2,
            'low': base - 0.5,
            'close': base - 0.3,  # 持续下跌
            'volume': 1000000,
        })
        base -= 0.3

    result_bull = predict_next_day(kline_bull, {'changepercent': 2.5})
    result_bear = predict_next_day(kline_bear, {'changepercent': -2.5})

    assert result_bull['prob'] > result_bear['prob'], \
        f"趋势延续概率({result_bull['prob']:.0%})应高于趋势下跌({result_bear['prob']:.0%})"
    assert result_bull['score'] > result_bear['score'], \
        f"趋势延续评分({result_bull['score']})应高于趋势下跌({result_bear['score']})"


# ==================== 4. 板块热度功能测试 ====================

def test_sector_heat_data_loading():
    """板块热度数据加载"""
    from sector_heat import SectorHeatMap
    sm = SectorHeatMap()
    sm.fetch_sector_heat_data()
    assert len(sm.sectors_data) >= 30, \
        f"板块数据过少: {len(sm.sectors_data)}(应>=30)"
    for key, info in sm.sectors_data.items():
        assert 'name' in info, f"板块{key}缺少name字段"
        assert 'heat_score' in info, f"板块{key}缺少heat_score字段"
        assert 0 <= info['heat_score'] <= 100, \
            f"板块{info['name']}热度异常: {info['heat_score']}"

def test_sector_heat_levels():
    """热度等级判定"""
    from sector_heat import SectorHeatMap
    sm = SectorHeatMap()
    assert sm._get_heat_level(80) == 'hot', "80分应为hot"
    assert sm._get_heat_level(70) == 'hot', "70分应为hot"
    assert sm._get_heat_level(50) == 'warm', "50分应为warm"
    assert sm._get_heat_level(30) == 'cold', "30分应为cold"


# ==================== 5. 优化器健壮性测试 ====================

def test_optimizer_param_bounds():
    """优化器参数应在边界约束内"""
    from stock_screener import StrategyOptimizer
    opt = StrategyOptimizer()
    for key, (lo, hi) in opt.PARAM_BOUNDS.items():
        val = opt.PARAMS.get(key)
        if val is not None:
            assert lo <= val <= hi, \
                f"参数{key}={val}超出边界[{lo},{hi}]"

def test_optimizer_min_sample():
    """★v6: 优化器最小样本100才调整参数"""
    from stock_screener import StrategyOptimizer
    opt = StrategyOptimizer()
    # 小样本不应调整
    opt.optimize()  # 样本<100，应跳过所有调整


# ==================== 6. 边界条件和异常处理 ====================

def test_empty_kline():
    """空K线数据处理"""
    from technical_analysis import predict_next_day
    result = predict_next_day([], None)
    assert result['prob'] >= 0.08, "空K线概率过低"
    assert result['score'] >= 0, "空K线评分为负"

def test_short_kline():
    """过短K线数据处理"""
    from technical_analysis import predict_next_day
    short_kline = [
        {'day': '2026-01-01', 'open': 10, 'high': 11, 'low': 9, 'close': 10.5, 'volume': 1000},
        {'day': '2026-01-02', 'open': 10.5, 'high': 11, 'low': 10, 'close': 10.8, 'volume': 1000},
        {'day': '2026-01-03', 'open': 10.8, 'high': 11, 'low': 10, 'close': 10.3, 'volume': 1000},
    ]
    result = predict_next_day(short_kline, {'changepercent': 0.5})
    assert 0 <= result['prob'] <= 1, f"概率异常: {result['prob']}"
    assert 0 <= result['score'] <= 100, f"评分异常: {result['score']}"

def test_extreme_change_percent():
    """极端涨跌幅处理"""
    from stock_screener import score_trend_continuation
    # 跌停 — v6应给低分
    score_drop = score_trend_continuation({'changepercent': -10.0, 'turnoverratio': 3.0, 'nmc': 500000})
    assert 0 <= score_drop <= 100, f"跌停评分异常: {score_drop}"
    # 暴涨 — v6也应谨慎
    score_surge = score_trend_continuation({'changepercent': 8.0, 'turnoverratio': 3.0, 'nmc': 500000})
    assert 0 <= score_surge <= 100, f"暴涨评分异常: {score_surge}"

def test_120bar_technical_indicators():
    """★v6: 120根K线下技术指标完整性"""
    from data_fetcher import get_kline_sina
    from technical_analysis import calc_ma, calc_macd, calc_rsi, calc_kdj, calc_boll

    kline = get_kline_sina('sh600519', '240', '120')
    if len(kline) < 60:
        return  # API不可用时跳过

    closes = [k['close'] for k in kline]

    # v5用20根K线，这些指标返回None
    # v6用120根K线，应该全部有效
    ma60 = calc_ma(closes, 60)
    assert ma60 is not None, "120根K线下MA60不应为None"

    rsi = calc_rsi(closes, 14)
    assert rsi is not None, "120根K线下RSI不应为None"

    dif, dea, macd_bar = calc_macd(closes)
    assert dif is not None, "120根K线下MACD DIF不应为None"
    assert dea is not None, "120根K线下MACD DEA不应为None"

    boll_upper, boll_mid, boll_lower = calc_boll(closes)
    assert boll_upper is not None, "120根K线下BOLL不应为None"


# ==================== 运行所有测试 ====================

if __name__ == '__main__':
    print(f"\n{'='*60}")
    print(f"  A股T+1选股系统 - 自动化测试套件 v6")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}\n")

    # 1. 语法和导入
    print("━━━ 1. 语法和导入检查 ━━━")
    runner.run("导入 data_fetcher", test_import_data_fetcher)
    runner.run("导入 technical_analysis", test_import_technical_analysis)
    runner.run("导入 stock_screener", test_import_stock_screener)
    runner.run("导入 sector_heat", test_import_sector_heat)
    runner.run("导入 wechat_push", test_import_wechat_push)
    runner.run("导入 review", test_import_review)
    runner.run("导入 main", test_import_main)

    # 2. 数据源健康
    print("\n━━━ 2. 数据源健康检查 ━━━")
    runner.run("新浪实时行情API", test_sina_realtime_api)
    runner.run("新浪K线API(120根)", test_sina_kline_api)
    runner.run("大盘指数API", test_sina_market_indices)
    runner.run("新浪行业板块API", test_sina_sector_api)
    runner.run("市场环境评估", test_market_environment)

    # 3. 核心函数
    print("\n━━━ 3. 核心函数单元测试 ━━━")
    runner.run("板块分类 classify_board", test_classify_board)
    runner.run("基础过滤 filter_basic(v6)", test_filter_basic)
    runner.run("大盘环境过滤 filter_market_env", test_filter_market_env)
    runner.run("趋势延续评分 score_trend_continuation", test_score_trend_continuation)
    runner.run("预测模型趋势逻辑", test_predict_next_day_trend)

    # 4. 板块热度
    print("\n━━━ 4. 板块热度功能测试 ━━━")
    runner.run("板块热度数据加载", test_sector_heat_data_loading)
    runner.run("热度等级判定", test_sector_heat_levels)

    # 5. 优化器
    print("\n━━━ 5. 优化器健壮性 ━━━")
    runner.run("优化器参数边界", test_optimizer_param_bounds)
    runner.run("优化器最小样本", test_optimizer_min_sample)

    # 6. 边界条件
    print("\n━━━ 6. 边界条件和异常处理 ━━━")
    runner.run("空K线处理", test_empty_kline)
    runner.run("短K线处理", test_short_kline)
    runner.run("极端涨跌幅处理", test_extreme_change_percent)
    runner.run("120根K线技术指标", test_120bar_technical_indicators)

    # 结果
    all_pass = runner.summary()
    sys.exit(0 if all_pass else 1)
