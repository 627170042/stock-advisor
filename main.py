"""
A股T+1短线选股系统 - 主程序（GitHub Actions版）
功能：每日14:25自动运行，推荐2只股票，次日复盘
"""
import json
import os
import sys
from datetime import datetime
from data_fetcher import get_realtime_quote, get_kline_sina, classify_board
from stock_screener import screen_stocks, StrategyOptimizer
from technical_analysis import calc_ma, calc_rsi, calc_kdj

# 数据目录
DATA_DIR = os.environ.get('DATA_DIR', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data'))


def _format_sector_info(stock):
    """格式化板块热度信息"""
    sector_heat = stock.get('sector_heat')
    if not sector_heat:
        return ''
    level = sector_heat.get('heat_level', 'warm')
    name = sector_heat.get('sector_name', '')
    score = sector_heat.get('heat_score', 50)
    if level == 'hot':
        return f'\n  板块热度: 🔥 {name}({score}分/热门)'
    elif level == 'cold':
        return f'\n  板块热度: ❄️ {name}({score}分/冷门)'
    else:
        return f'\n  板块热度: 📊 {name}({score}分/温热)'


def generate_report(budget_pick, strong_pick, optimizer):
    """生成推荐报告"""
    today = datetime.now().strftime('%Y-%m-%d')
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    completed = [r for r in optimizer.history if r.get('result')]
    hits = [r for r in completed if r['result']['hit']]
    win_rate = len(hits) / len(completed) if completed else 0
    
    report = f"""
{'='*70}
     A股T+1短线选股系统 - 每日推荐报告
{'='*70}

生成时间: {now_str}
历史胜率: {win_rate:.1%} ({len(hits)}/{len(completed)}) 

{'─'*70}
推荐一：【低价最优解】非创业板 | 单价<=40元
{'─'*70}
"""
    
    if budget_pick:
        s = budget_pick
        sector_info = _format_sector_info(s)
        report += f"""
  代码: {s['symbol']}  名称: {s['name']}
  当前价格: {s['trade']:.2f} 元
  当日涨幅: {s['changepercent']:.2f}%
  换手率: {s['turnoverratio']:.2f}%
  技术评分: {s.get('tech_score', 'N/A')}
  次日概率: {s.get('next_day_prob', 0):.0%}
  综合评分: {s.get('total_score', 0):.1f}{sector_info}
"""
    else:
        report += "\n  今日未找到符合条件的低价股\n"
    
    report += f"""
{'─'*70}
推荐二：【市场最强】不限单价 | 短线爆发力
{'─'*70}
"""
    
    if strong_pick:
        s = strong_pick
        sector_info = _format_sector_info(s)
        report += f"""
  代码: {s['symbol']}  名称: {s['name']}
  当前价格: {s['trade']:.2f} 元
  当日涨幅: {s['changepercent']:.2f}%
  换手率: {s['turnoverratio']:.2f}%
  技术评分: {s.get('tech_score', 'N/A')}
  次日概率: {s.get('next_day_prob', 0):.0%}
  综合评分: {s.get('total_score', 0):.1f}{sector_info}
"""
    else:
        report += "\n  今日未找到符合条件的最强股\n"
    
    report += f"""
{'─'*70}
操作建议
{'─'*70}
  买入时机: 尾盘14:30-14:55分批建仓
  止损纪律: 次日低开超2%立即止损
  目标收益: 2%-5%，达标即走

{'─'*70}
免责声明: 本推荐仅供参考，不构成投资建议。股市有风险，投资需谨慎！
{'='*70}
"""
    
    return report


def run_daily_recommendation():
    """执行每日推荐流程"""
    print(f"\nA股T+1短线选股系统启动 - {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
    
    optimizer = StrategyOptimizer()
    today = datetime.now().strftime('%Y-%m-%d')
    
    # 不在推荐中执行复盘，复盘由独立的review任务负责
    # print("步骤1: 复盘前日推荐...")
    # from review import review_previous_recommendations
    # review_previous_recommendations()
    
    # 执行筛选
    print("\n步骤1: 筛选今日推荐...")
    from data_fetcher import get_top_gainers, get_top_volume
    print("  预加载市场数据...")
    gainers = get_top_gainers(80)
    volume_leaders = get_top_volume(80)
    preloaded = {}
    for s in gainers + volume_leaders:
        preloaded[s['symbol']] = s
    print(f"  已加载 {len(preloaded)} 只股票数据")
    
    # 低价股筛选（使用持久化参数）
    budget_candidates = screen_stocks('budget', max_candidates=15, preloaded_stocks=preloaded, params=optimizer.PARAMS)
    budget_pick = budget_candidates[0] if budget_candidates else None

    # 最强股筛选（使用持久化参数）
    strong_candidates = screen_stocks('strong', max_candidates=15, preloaded_stocks=preloaded, params=optimizer.PARAMS)
    if budget_pick:
        strong_candidates = [s for s in strong_candidates if s['symbol'] != budget_pick['symbol']]
    strong_pick = strong_candidates[0] if strong_candidates else None
    
    # 记录推荐
    if budget_pick:
        optimizer.record_recommendation(budget_pick, 'budget', today)
    if strong_pick:
        optimizer.record_recommendation(strong_pick, 'strong', today)
    
    # 生成报告
    report = generate_report(budget_pick, strong_pick, optimizer)
    print(report)
    
    # 保存报告
    reports_dir = os.path.join(DATA_DIR, 'reports')
    os.makedirs(reports_dir, exist_ok=True)
    
    report_file = os.path.join(reports_dir, f'report_{today}.txt')
    with open(report_file, 'w', encoding='utf-8') as f:
        f.write(report)
    
    # 保存推荐数据
    rec_data = {
        'date': today,
        'budget': {
            'symbol': budget_pick['symbol'],
            'name': budget_pick['name'],
            'price': budget_pick['trade'],
            'changepercent': budget_pick['changepercent'],
            'tech_score': budget_pick.get('tech_score'),
            'total_score': budget_pick.get('total_score'),
            'next_day_prob': budget_pick.get('next_day_prob'),
            'signals': budget_pick.get('signals', []),
            'sector_heat': budget_pick.get('sector_heat'),
        } if budget_pick else None,
        'strong': {
            'symbol': strong_pick['symbol'],
            'name': strong_pick['name'],
            'price': strong_pick['trade'],
            'changepercent': strong_pick['changepercent'],
            'tech_score': strong_pick.get('tech_score'),
            'total_score': strong_pick.get('total_score'),
            'next_day_prob': strong_pick.get('next_day_prob'),
            'signals': strong_pick.get('signals', []),
            'sector_heat': strong_pick.get('sector_heat'),
        } if strong_pick else None,
    }
    
    rec_file = os.path.join(reports_dir, 'latest.json')
    with open(rec_file, 'w', encoding='utf-8') as f:
        json.dump(rec_data, f, ensure_ascii=False, indent=2)
    
    print(f"\n报告已保存: {report_file}")
    print(f"推荐数据: {rec_file}")
    
    # 微信推送
    print("\n步骤3: 推送到企业微信...")
    from wechat_push import push_recommendation
    push_recommendation()
    
    return budget_pick, strong_pick


if __name__ == '__main__':
    budget, strong = run_daily_recommendation()
