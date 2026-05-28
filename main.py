"""
A股T+1短线选股系统 - 主程序 v6（GitHub Actions版）
功能：每日14:25自动运行，推荐2只股票，次日复盘

v5→v6 核心变化：
1. 取消Budget/Strong双类别 → 单一"高概率候选"类别
2. 新增大盘环境前置检查（熊市不推荐）
3. 推荐报告格式适配v6评分维度
4. 命中标准从收盘涨2%→日内最高涨2%(max_profit)
"""
import json
import os
import sys
from datetime import datetime
from data_fetcher import get_realtime_quote, get_kline_sina, classify_board, get_market_environment
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


def _format_market_env(market_env):
    """格式化大盘环境信息"""
    if not market_env:
        return '数据缺失'
    level = market_env.get('level', 'neutral')
    score = market_env.get('score', 50)
    signal = market_env.get('signal', '')

    level_cn = {'bull': '多头', 'neutral': '震荡', 'bear': '空头'}.get(level, level)
    return f'{level_cn}({score}分) {signal}'


def generate_report(picks, optimizer, market_env=None):
    """v6: 生成推荐报告（单一类别）"""
    today = datetime.now().strftime('%Y-%m-%d')
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    completed = [r for r in optimizer.history if r.get('result')]
    hits = [r for r in completed if r['result']['hit']]
    win_rate = len(hits) / len(completed) if completed else 0

    market_env_str = _format_market_env(market_env)

    report = f"""
{'='*70}
     A股T+1短线选股系统 - 每日推荐报告 (v6)
{'='*70}

生成时间: {now_str}
历史胜率: {win_rate:.1%} ({len(hits)}/{len(completed)})
大盘环境: {market_env_str}

{'─'*70}
"""

    if not picks:
        report += "\n  今日大盘环境不佳或无符合条件的股票，暂停推荐\n"
    else:
        for i, s in enumerate(picks, 1):
            sector_info = _format_sector_info(s)
            board = classify_board(s['symbol'])
            board_cn = {'main': '主板', 'gem': '创业板', 'star': '科创板'}.get(board, board)
            report += f"""
推荐第{i}名：【{s['name']}】{s['symbol']} ({board_cn})
  当前价格: {s['trade']:.2f} 元
  当日涨幅: {s['changepercent']:.2f}%
  换手率: {s['turnoverratio']:.2f}%
  趋势评分: {s.get('trend_score', 'N/A')}
  技术评分: {s.get('tech_score', 'N/A')}
  次日概率: {s.get('next_day_prob', 0):.0%}
  综合评分: {s.get('total_score', 0):.1f}{sector_info}
"""
            if s.get('signals'):
                report += f"  关键信号: {' | '.join(s['signals'][:4])}\n"
            report += f"\n{'─'*70}\n"

    report += f"""
操作建议
  买入时机: 尾盘14:30-14:55分批建仓
  止损纪律: 次日低开超2%立即止损
  止盈策略: 日内涨幅达2%即可考虑止盈
  目标收益: 2%-5%，达标即走

免责声明: 本推荐仅供参考，不构成投资建议。股市有风险，投资需谨慎！
{'='*70}
"""

    return report


def run_daily_recommendation():
    """执行每日推荐流程"""
    print(f"\nA股T+1短线选股系统 v6 启动 - {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")

    optimizer = StrategyOptimizer()
    today = datetime.now().strftime('%Y-%m-%d')

    # ★v6: 大盘环境前置检查
    print("步骤0: 大盘环境检查...")
    market_env = get_market_environment()
    env_level = market_env.get('level', 'neutral')
    env_score = market_env.get('score', 50)
    if env_score < 25 or (env_level == 'bear' and env_score < 30):
        print(f"  ⚠️ 大盘环境极弱({env_score}分)，今日暂停推荐")
        report = f"""
{'='*70}
     A股T+1短线选股系统 - 每日推荐报告 (v6)
{'='*70}

生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
大盘环境: {_format_market_env(market_env)}

⚠️ 今日大盘环境极弱，暂停推荐，规避系统性风险。
{'='*70}
"""
        # 推送暂停推荐通知
        from wechat_push import send_wechat_message
        content = f"""# ⚠️ 今日暂停推荐

> {today}

**大盘环境不佳**，{_format_market_env(market_env)}

<font color="warning">暂停推荐，规避系统性风险</font>

<font color="comment">v6策略：熊市不选股</font>"""
        send_wechat_message(content)

        # 保存报告
        reports_dir = os.path.join(DATA_DIR, 'reports')
        os.makedirs(reports_dir, exist_ok=True)
        report_file = os.path.join(reports_dir, f'report_{today}.txt')
        with open(report_file, 'w', encoding='utf-8') as f:
            f.write(report)
        return []

    # 执行筛选
    print("\n步骤1: 筛选今日推荐...")
    candidates = screen_stocks(max_candidates=25, params=optimizer.PARAMS)

    if not candidates:
        print("  未找到符合条件的股票")
        picks = []
    else:
        # 取前2名作为推荐
        picks = candidates[:2]

    # 记录推荐
    for pick in picks:
        optimizer.record_recommendation(pick, 'pick', today)

    # 生成报告
    report = generate_report(picks, optimizer, market_env)
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
        'market_env': {
            'level': market_env.get('level', 'neutral'),
            'score': market_env.get('score', 50),
            'signal': market_env.get('signal', ''),
        },
        'picks': [{
            'symbol': p['symbol'],
            'name': p['name'],
            'price': p['trade'],
            'changepercent': p['changepercent'],
            'trend_score': p.get('trend_score'),
            'tech_score': p.get('tech_score'),
            'total_score': p.get('total_score'),
            'next_day_prob': p.get('next_day_prob'),
            'signals': p.get('signals', []),
            'sector_heat': p.get('sector_heat'),
        } for p in picks],
    }

    rec_file = os.path.join(reports_dir, 'latest.json')
    with open(rec_file, 'w', encoding='utf-8') as f:
        json.dump(rec_data, f, ensure_ascii=False, indent=2)

    print(f"\n报告已保存: {report_file}")
    print(f"推荐数据: {rec_file}")

    # 微信推送
    print("\n步骤2: 推送到企业微信...")
    from wechat_push import push_recommendation
    push_recommendation()

    return picks


if __name__ == '__main__':
    picks = run_daily_recommendation()
