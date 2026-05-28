"""
A股T+1短线选股系统 - 复盘分析模块 v6
每日对前一日推荐股进行复盘

v5→v6 核心变化：
1. 命中标准从收盘涨2%→日内最高涨2%(max_profit)
2. 记录max_profit字段（从推荐价到次日最高价的收益率）
"""
import json
import os
from datetime import datetime, timedelta
from data_fetcher import get_realtime_quote, get_kline_sina
from stock_screener import StrategyOptimizer


def review_previous_recommendations():
    """复盘前一日推荐"""
    optimizer = StrategyOptimizer()
    history = optimizer.history

    if not history:
        print("暂无历史推荐记录")
        return

    today = datetime.now().strftime('%Y-%m-%d')

    # 找出未复盘的推荐（排除今日刚推荐的，需要次日才有数据）
    pending = [r for r in history if r.get('result') is None and r['date'] < today]

    newly_reviewed = 0

    if not pending:
        print("所有推荐已复盘，检查是否需要推送已完成的复盘结果...")
    else:
        print(f"\n{'='*60}")
        print(f"📋 复盘分析 - {today}")
        print(f"{'='*60}")

    for rec in pending:
        symbol = rec['symbol']
        rec_date = rec['date']
        rec_price = rec['recommend_price']
        category = rec.get('category', 'pick')

        print(f"\n🔍 {symbol} {rec['name']} ({category})")
        print(f"   推荐日期: {rec_date} | 推荐价: {rec_price:.2f}")
        newly_reviewed += 1

        try:
            # 获取推荐日次日K线数据
            kline = get_kline_sina(symbol, '240', '10')

            if not kline or len(kline) < 2:
                # 尝试获取实时行情
                quote = get_realtime_quote(symbol)
                if quote:
                    prev_close = quote['prev_close']
                    current = quote['current']
                    change_pct = quote.get('change_pct', (current - prev_close) / prev_close * 100 if prev_close > 0 else 0)

                    # ★v6: max_profit = 从推荐价到次日最高价
                    max_profit = (quote['high'] - rec_price) / rec_price * 100 if rec_price > 0 else 0
                    close_hit = 2 <= change_pct <= 5
                    max_hit = max_profit >= 2

                    result = {
                        'next_day_open': quote['open'],
                        'next_day_high': quote['high'],
                        'next_day_close': current,
                        'next_day_change': round(change_pct, 2),
                        'hit': max_profit >= 2,  # ★v6: 使用max_profit
                        'max_profit': round(max_profit, 2),
                        'close_hit': close_hit,
                        'max_hit': max_hit,
                    }

                    optimizer.update_result(symbol, rec_date,
                        quote['open'], quote['high'], current, round(change_pct, 2))

                    status = "✅ 命中" if max_hit else "❌ 未命中"
                    print(f"   次日表现: 开{quote['open']:.2f} 高{quote['high']:.2f} 收{current:.2f}")
                    print(f"   收盘涨幅: {change_pct:.2f}% {'✅' if close_hit else ''}")
                    print(f"   日内最高收益: {max_profit:.2f}% {status}")
                else:
                    print(f"   ⚠️ 无法获取数据，跳过")
                    continue
            else:
                # 从K线找推荐日次日的数据
                next_day_data = None
                for i, k in enumerate(kline):
                    if k['day'] > rec_date and k['day'] <= today:
                        next_day_data = k
                        break

                if next_day_data:
                    open_price = next_day_data['open']
                    high_price = next_day_data['high']
                    close_price = next_day_data['close']

                    # 用推荐价计算涨幅
                    change_from_rec = (close_price - rec_price) / rec_price * 100
                    # ★v6: max_profit = 从推荐价到次日最高价
                    max_profit = (high_price - rec_price) / rec_price * 100 if rec_price > 0 else 0

                    close_hit = 2 <= change_from_rec <= 5
                    max_hit = max_profit >= 2

                    result = {
                        'next_day_open': open_price,
                        'next_day_high': high_price,
                        'next_day_close': close_price,
                        'next_day_change': round(change_from_rec, 2),
                        'hit': max_profit >= 2,  # ★v6: 使用max_profit
                        'max_profit': round(max_profit, 2),
                        'close_hit': close_hit,
                        'max_hit': max_hit,
                    }

                    optimizer.update_result(symbol, rec_date,
                        open_price, high_price, close_price, round(change_from_rec, 2))

                    status = "✅ 命中" if max_hit else "❌ 未命中"
                    print(f"   次日表现: 开{open_price:.2f} 高{high_price:.2f} 收{close_price:.2f}")
                    print(f"   收盘涨幅: {change_from_rec:.2f}% {'✅' if close_hit else ''}")
                    print(f"   日内最高收益: {max_profit:.2f}% {status}")
                else:
                    # K线未包含次日数据，用实时行情对比推荐价来复盘
                    quote = get_realtime_quote(symbol)
                    if quote and quote['prev_close'] > 0:
                        open_price = quote['open']
                        high_price = quote['high']
                        current = quote['current']
                        change_from_rec = (current - rec_price) / rec_price * 100
                        max_profit = (high_price - rec_price) / rec_price * 100 if rec_price > 0 else 0

                        max_hit = max_profit >= 2
                        optimizer.update_result(symbol, rec_date,
                            open_price, high_price, current, round(change_from_rec, 2))

                        status = "✅ 命中" if max_hit else "❌ 未命中"
                        print(f"   次日(实时): 开{open_price:.2f} 高{high_price:.2f} 收{current:.2f}")
                        print(f"   涨幅(从推荐价): {change_from_rec:.2f}%")
                        print(f"   日内最高收益: {max_profit:.2f}% {status}")
                    else:
                        print(f"   ⚠️ 无次日数据，跳过")

        except Exception as e:
            print(f"   ❌ 复盘失败: {e}")

    # 统计
    completed = [r for r in optimizer.history if r.get('result')]
    if completed:
        hits = [r for r in completed if r['result']['hit']]
        win_rate = len(hits) / len(completed)
        # 也统计收盘命中
        close_hits = [r for r in completed if r['result'].get('close_hit', False)]
        close_win_rate = len(close_hits) / len(completed)
        print(f"\n{'='*60}")
        print(f"📊 累计统计 (v6命中标准: 日内最高收益≥2%)")
        print(f"   总推荐 {len(completed)} 次 | 命中 {len(hits)} 次 | 胜率 {win_rate:.1%}")
        print(f"   (收盘命中 {len(close_hits)} 次 | 收盘胜率 {close_win_rate:.1%})")
        print(f"{'='*60}")

    # 执行优化（自动持久化参数）
    optimizer.optimize()

    # 如果参数有变化，推送优化通知
    if optimizer.params_changed:
        print("\n📱 策略参数已优化，推送通知...")
        from wechat_push import push_optimization_notice
        push_optimization_notice(optimizer)

    # 微信推送复盘
    if newly_reviewed > 0:
        print(f"\n📱 本轮新复盘 {newly_reviewed} 只，推送复盘到企业微信...")
    else:
        print("\n📱 本轮无新复盘，但推送最近复盘结果到企业微信...")
    from wechat_push import push_review
    push_review()


if __name__ == '__main__':
    review_previous_recommendations()
