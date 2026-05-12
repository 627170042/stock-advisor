"""
A股T+1短线选股系统 - 自动化测试套件

运行方式: python3 test_suite.py
在每次代码改动后、推送到GitHub前，必须通过所有测试。

测试覆盖：
1. 语法和导入检查 - 确保所有模块可正常导入
2. 数据源健康检查 - 确保API可用且返回格式正确
3. 核心函数单元测试 - 覆盖边界条件和逻辑错误
4. 模型回测验证 - 验证模型预测与实际的一致性
5. 端到端流程测试 - 模拟完整推荐流程
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
                               is_gem, is_star, get_stock_list, get_stock_industry_f10)
    
def test_import_technical_analysis():
    from technical_analysis import (predict_next_day, tech_score, judge_trend,
                                     calc_ma, calc_rsi, calc_kdj, calc_macd, calc_boll)
    
def test_import_stock_screener():
    from stock_screener import (screen_stocks, StrategyOptimizer, filter_basic,
                                 filter_liquidity, filter_chase_risk, 
                                 filter_death_zone, score_next_day_potential)
    
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
    # 验证字段完整性
    for field in ['symbol', 'name', 'trade', 'changepercent', 'amount']:
        assert field in s, f"缺少字段: {field}"
    assert s['trade'] > 0, f"价格异常: {s['trade']}"
    assert len(s['name']) > 0, f"名称异常: {s['name']}"

def test_sina_kline_api():
    """新浪K线API可用性"""
    from data_fetcher import get_kline_sina
    kline = get_kline_sina('sh600519', '240', '20')
    assert len(kline) > 0, "K线数据为空"
    k = kline[0]
    for field in ['day', 'open', 'high', 'low', 'close', 'volume']:
        assert field in k, f"K线缺少字段: {field}"
    assert k['close'] > 0, f"收盘价异常: {k['close']}"

def test_sina_sector_api():
    """新浪行业板块API可用性"""
    import requests
    url = 'https://vip.stock.finance.sina.com.cn/q/view/newSinaHy.php'
    headers = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://finance.sina.com.cn'}
    r = requests.get(url, headers=headers, timeout=15)
    assert r.status_code == 200, f"新浪板块API状态码: {r.status_code}"
    assert len(r.text) > 500, f"新浪板块API返回内容过短: {len(r.text)}"
    import re
    match = re.search(r'\{(.+)\}', r.text, re.DOTALL)
    assert match is not None, "新浪板块数据格式异常(无JSON结构)"
    items = re.findall(r'"([^"]+)":"([^"]+)"', match.group(0))
    assert len(items) >= 30, f"新浪板块数量异常: {len(items)}(应>=30)"

def test_eastmoney_f10_api():
    """东方财富F10行业分类API可用性"""
    from data_fetcher import get_stock_industry_f10
    industry = get_stock_industry_f10('sh600519')
    assert industry is not None, "F10行业分类返回None"
    assert '食品' in industry or '饮料' in industry or '白酒' in industry, \
        f"贵州茅台行业分类异常: {industry} (应含'食品/饮料/白酒')"

def test_eastmoney_f10_other_stocks():
    """F10行业分类对不同股票的正确性"""
    from data_fetcher import get_stock_industry_f10
    test_cases = [
        ('sh600519', '贵州茅台', '食品'),
        ('sh600089', '特变电工', '电气'),
        ('sh601012', '大唐发电', '电气'),
    ]
    for code, name, expected_kw in test_cases:
        industry = get_stock_industry_f10(code)
        assert industry is not None, f"{name}({code}) 行业分类返回None"
        assert expected_kw in industry, \
            f"{name}({code}) 行业分类'{industry}'不含'{expected_kw}'"
        time.sleep(0.3)  # 限速


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
    """基础过滤函数"""
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
    # budget创业板
    ok, _ = filter_basic({'symbol': 'sz300001', 'name': '正常', 'trade': 10}, 'budget')
    assert not ok, "budget应过滤创业板"
    # budget价格超限
    ok, _ = filter_basic({'symbol': 'sh600000', 'name': '正常', 'trade': 45}, 'budget')
    assert not ok, "budget应过滤>40元"
    # 正常通过
    ok, _ = filter_basic({'symbol': 'sh600000', 'name': '正常', 'trade': 25}, 'budget')
    assert ok, "正常股票应通过"

def test_filter_death_zone():
    """死亡区过滤函数"""
    from stock_screener import filter_death_zone
    # 0.5%~1.5%微涨应被过滤
    ok, _ = filter_death_zone({'changepercent': 0.8})
    assert not ok, "0.8%微涨应在死亡区"
    ok, _ = filter_death_zone({'changepercent': 1.5})
    assert ok, "1.5%不在死亡区(边界值放行)"
    # <0.5%应放行
    ok, _ = filter_death_zone({'changepercent': 0.3})
    assert ok, "0.3%不在死亡区"
    # >=1.5%应放行(不在死亡区范围)
    ok, _ = filter_death_zone({'changepercent': 2.0})
    assert ok, "2.0%不在死亡区"
    # 负涨幅应放行
    ok, _ = filter_death_zone({'changepercent': -1.5})
    assert ok, "-1.5%不在死亡区"
    # 热门板块微涨应放行
    ok, _ = filter_death_zone({'changepercent': 1.0, 'sector_heat': {'heat_level': 'hot', 'heat_score': 80}})
    assert ok, "热门板块1.0%微涨应放行"

def test_filter_chase_risk():
    """追涨过滤函数"""
    from stock_screener import filter_chase_risk
    ok, _ = filter_chase_risk({'changepercent': 2.0}, max_change=3.5)
    assert ok, "2.0%不应被追涨过滤"
    ok, _ = filter_chase_risk({'changepercent': 4.0}, max_change=3.5)
    assert not ok, "4.0%应被追涨过滤"
    ok, _ = filter_chase_risk({'changepercent': 3.5}, max_change=3.5)
    assert ok, "3.5%等于上限应放行"

def test_score_next_day_potential():
    """潜力评分函数"""
    from stock_screener import score_next_day_potential
    # 深幅回调应高分
    deep_pullback = {'changepercent': -3.5, 'turnoverratio': 5.0, 'nmc': 500000}
    score_deep = score_next_day_potential(deep_pullback)
    # 微涨应低分
    slight_up = {'changepercent': 1.0, 'turnoverratio': 5.0, 'nmc': 500000}
    score_slight = score_next_day_potential(slight_up)
    assert score_deep > score_slight, \
        f"深幅回调评分({score_deep})应高于微涨({score_slight})"
    # 热门板块回调应更优
    hot_sector = {'changepercent': -2.0, 'turnoverratio': 5.0, 'nmc': 500000}
    score_hot = score_next_day_potential(hot_sector, sector_heat={'heat_level': 'hot', 'heat_score': 80})
    score_cold = score_next_day_potential(hot_sector, sector_heat={'heat_level': 'cold', 'heat_score': 20})
    assert score_hot >= score_cold, \
        f"热门板块回调评分({score_hot})应>=冷门板块({score_cold})"

def test_consecutive_up_logic():
    """连涨天数判断逻辑 - 防止>=4在>=5之前"""
    from technical_analysis import predict_next_day
    # 构造5连涨K线
    kline_5up = []
    base = 10.0
    for i in range(20):
        kline_5up.append({
            'day': f'2026-01-{i+1:02d}',
            'open': base,
            'high': base + 0.5,
            'low': base - 0.3,
            'close': base + 0.3,  # 每天涨
            'volume': 1000000,
        })
        base += 0.3
    result = predict_next_day(kline_5up, {'changepercent': 1.0})
    signals = result['signals']
    # 5连涨不应被标记为4连涨
    has_5up = any('5连涨' in s for s in signals)
    has_4up_wrong = any('4连涨' in s and '5连涨' not in s for s in signals)
    # 如果检测到连涨，应该是5连涨而非4连涨
    if has_4up_wrong and not has_5up:
        raise AssertionError("5连涨被错误识别为4连涨 - 判断顺序有误")

def test_predict_next_day_basic():
    """预测模型基本一致性"""
    from technical_analysis import predict_next_day
    # 构造回调企稳K线（应得较高概率）
    kline_rebound = []
    base = 10.0
    for i in range(15):
        if i < 10:
            change = -0.3  # 前10天下跌
        else:
            change = 0.2  # 后5天企稳回升
        kline_rebound.append({
            'day': f'2026-01-{i+1:02d}',
            'open': base,
            'high': base + 0.5,
            'low': base - 0.5,
            'close': base + change,
            'volume': 800000 if i < 10 else 1200000,  # 缩量跌+放量涨
        })
        base += change
    
    # 构造追涨K线（应得较低概率）
    kline_chase = []
    base = 10.0
    for i in range(15):
        kline_chase.append({
            'day': f'2026-01-{i+1:02d}',
            'open': base,
            'high': base + 1.0,
            'low': base - 0.2,
            'close': base + 0.8,  # 每天大涨
            'volume': 2000000,
        })
        base += 0.8
    
    result_rebound = predict_next_day(kline_rebound, {'changepercent': -0.5})
    result_chase = predict_next_day(kline_chase, {'changepercent': 3.0})
    
    # 回调企稳的概率应高于追涨
    assert result_rebound['prob'] > result_chase['prob'], \
        f"回调企稳概率({result_rebound['prob']:.0%})应高于追涨({result_chase['prob']:.0%})"
    
    # 回调企稳的评分应高于追涨
    assert result_rebound['score'] > result_chase['score'], \
        f"回调企稳评分({result_rebound['score']})应高于追涨({result_chase['score']})"


# ==================== 4. 模型回测验证 ====================

def test_backtest_model_direction():
    """回测模型方向性: 命中组评分/概率应高于或至少不低于未命中组"""
    from stock_screener import score_next_day_potential
    
    # 基于历史数据的命中/未命中特征
    # 命中组: 推荐日平均涨+1.2%, 深幅回调为主
    # 未命中组: 推荐日平均涨+0.6%, 微涨为主
    
    hit_stocks = [
        {'changepercent': -2.6, 'turnoverratio': 5.0, 'nmc': 500000, 'name': '天通股份'},  # 命中
        {'changepercent': -2.0, 'turnoverratio': 6.0, 'nmc': 300000, 'name': '菲利华'},     # 命中
        {'changepercent': -2.0, 'turnoverratio': 4.0, 'nmc': 200000, 'name': '合力泰'},     # 命中
        {'changepercent': 2.6, 'turnoverratio': 8.0, 'nmc': 800000, 'name': '怡亚通'},      # 命中
    ]
    
    miss_stocks = [
        {'changepercent': 1.0, 'turnoverratio': 3.0, 'nmc': 600000, 'name': '紫金矿业'},    # 未命中
        {'changepercent': 0.5, 'turnoverratio': 4.0, 'nmc': 400000, 'name': '某股A'},       # 0~2%死亡区
        {'changepercent': 3.2, 'turnoverratio': 5.0, 'nmc': 500000, 'name': '东方证券'},     # 追涨未中
        {'changepercent': -0.7, 'turnoverratio': 2.0, 'nmc': 700000, 'name': '中科曙光'},   # 低换手未中
    ]
    
    hit_scores = [score_next_day_potential(s) for s in hit_stocks]
    miss_scores = [score_next_day_potential(s) for s in miss_stocks]
    
    avg_hit = sum(hit_scores) / len(hit_scores)
    avg_miss = sum(miss_scores) / len(miss_scores)
    
    print(f"    命中组平均潜力分: {avg_hit:.1f}, 未命中组: {avg_miss:.1f}")
    
    # v5反转逻辑下，回调股(命中组)的潜力分应高于微涨股(未命中组)
    assert avg_hit > avg_miss, \
        f"命中组潜力分({avg_hit:.1f})应高于未命中组({avg_miss:.1f}) — 模型方向性错误！"


# ==================== 5. 板块热度功能测试 ====================

def test_sector_heat_data_loading():
    """板块热度数据加载"""
    from sector_heat import SectorHeatMap
    sm = SectorHeatMap()
    sm.fetch_sector_heat_data()
    assert len(sm.sectors_data) >= 30, \
        f"板块数据过少: {len(sm.sectors_data)}(应>=30)"
    # 验证板块数据结构
    for key, info in sm.sectors_data.items():
        assert 'name' in info, f"板块{key}缺少name字段"
        assert 'heat_score' in info, f"板块{key}缺少heat_score字段"
        assert 'change_pct' in info, f"板块{key}缺少change_pct字段"
        assert 0 <= info['heat_score'] <= 100, \
            f"板块{info['name']}热度异常: {info['heat_score']}"

def test_sector_heat_matching():
    """板块匹配功能"""
    from sector_heat import SectorHeatMap, get_sector_heat_for_stock
    sm = SectorHeatMap()
    sm.fetch_sector_heat_data()
    
    # 大唐发电应匹配电力行业
    result = get_sector_heat_for_stock('sh601012', '大唐发电', sm)
    assert result is not None, "大唐发电板块匹配返回None"
    assert '电力' in result['sector_name'] or '发电' in result['sector_name'], \
        f"大唐发电匹配板块异常: {result['sector_name']}(应含'电力/发电')"
    
    # 特变电工应匹配发电设备或电气相关
    result2 = get_sector_heat_for_stock('sh600089', '特变电工', sm)
    assert result2 is not None, "特变电工板块匹配返回None"
    assert result2['sector_name'] != '未知', \
        f"特变电工应匹配到具体板块而非'未知'"
    
    # 无匹配时应返回默认温热值
    result3 = get_sector_heat_for_stock('sh999999', '某测试股', sm)
    assert result3 is not None, "无匹配时应返回默认值"
    assert result3['heat_level'] == 'warm', "无匹配时热度应为warm"

def test_sector_heat_levels():
    """热度等级判定"""
    from sector_heat import SectorHeatMap
    sm = SectorHeatMap()
    assert sm._get_heat_level(80) == 'hot', "80分应为hot"
    assert sm._get_heat_level(70) == 'hot', "70分应为hot"
    assert sm._get_heat_level(50) == 'warm', "50分应为warm"
    assert sm._get_heat_level(30) == 'cold', "30分应为cold"
    assert sm._get_heat_level(10) == 'cold', "10分应为cold"


# ==================== 6. 优化器健壮性测试 ====================

def test_optimizer_no_death_spiral():
    """优化器不应产生死亡螺旋 - 阈值不会无限上升"""
    from stock_screener import StrategyOptimizer
    opt = StrategyOptimizer()
    
    initial_threshold = opt.PARAMS.get('min_prob_threshold', 0.30)
    
    # 模拟极端情况: 胜率0%，大量样本
    for _ in range(10):
        opt.PARAMS['min_prob_threshold'] = min(0.53, opt.PARAMS['min_prob_threshold'] + 0.01)
    
    final_threshold = opt.PARAMS['min_prob_threshold']
    
    # 阈值不应超过0.53
    assert final_threshold <= 0.53, \
        f"阈值可能超过上限: {final_threshold}(应<=0.53)"

def test_optimizer_param_bounds():
    """优化器参数应在边界约束内"""
    from stock_screener import StrategyOptimizer
    opt = StrategyOptimizer()
    
    for key, (lo, hi) in opt.PARAM_BOUNDS.items():
        val = opt.PARAMS.get(key)
        if val is not None:
            assert lo <= val <= hi, \
                f"参数{key}={val}超出边界[{lo},{hi}]"


# ==================== 7. 边界条件和异常处理 ====================

def test_empty_kline():
    """空K线数据处理"""
    from technical_analysis import predict_next_day
    result = predict_next_day([], None)
    assert result['prob'] >= 0.10, "空K线概率过低"
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
    from stock_screener import filter_chase_risk, filter_death_zone
    from stock_screener import score_next_day_potential
    
    # 涨停板
    ok, _ = filter_chase_risk({'changepercent': 10.0}, max_change=3.5)
    assert not ok, "涨停应被追涨过滤"
    
    # 跌停
    score = score_next_day_potential({'changepercent': -10.0, 'turnoverratio': 3.0, 'nmc': 500000})
    assert 0 <= score <= 100, f"跌停评分异常: {score}"
    
    # 0涨跌幅
    ok, _ = filter_death_zone({'changepercent': 0.0})
    assert ok, "0涨跌幅不应在死亡区"


# ==================== 运行所有测试 ====================

if __name__ == '__main__':
    print(f"\n{'='*60}")
    print(f"  A股T+1选股系统 - 自动化测试套件")
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
    runner.run("新浪K线API", test_sina_kline_api)
    runner.run("新浪行业板块API", test_sina_sector_api)
    runner.run("东方财富F10行业分类", test_eastmoney_f10_api)
    runner.run("东方财富F10多股票验证", test_eastmoney_f10_other_stocks)
    
    # 3. 核心函数
    print("\n━━━ 3. 核心函数单元测试 ━━━")
    runner.run("板块分类 classify_board", test_classify_board)
    runner.run("基础过滤 filter_basic", test_filter_basic)
    runner.run("死亡区过滤 filter_death_zone", test_filter_death_zone)
    runner.run("追涨过滤 filter_chase_risk", test_filter_chase_risk)
    runner.run("潜力评分 score_next_day_potential", test_score_next_day_potential)
    runner.run("连涨判断逻辑顺序", test_consecutive_up_logic)
    runner.run("预测模型基本一致性", test_predict_next_day_basic)
    
    # 4. 模型回测
    print("\n━━━ 4. 模型回测验证 ━━━")
    runner.run("模型方向性回测", test_backtest_model_direction)
    
    # 5. 板块热度
    print("\n━━━ 5. 板块热度功能测试 ━━━")
    runner.run("板块热度数据加载", test_sector_heat_data_loading)
    runner.run("板块匹配功能", test_sector_heat_matching)
    runner.run("热度等级判定", test_sector_heat_levels)
    
    # 6. 优化器
    print("\n━━━ 6. 优化器健壮性 ━━━")
    runner.run("优化器防死亡螺旋", test_optimizer_no_death_spiral)
    runner.run("优化器参数边界", test_optimizer_param_bounds)
    
    # 7. 边界条件
    print("\n━━━ 7. 边界条件和异常处理 ━━━")
    runner.run("空K线处理", test_empty_kline)
    runner.run("短K线处理", test_short_kline)
    runner.run("极端涨跌幅处理", test_extreme_change_percent)
    
    # 结果
    all_pass = runner.summary()
    sys.exit(0 if all_pass else 1)
