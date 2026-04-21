"""
A股T+1短线选股系统 - 复盘分析模块
每日对前一日推荐股进行复盘，验证涨幅是否满足2-5%
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
    
    if not pending:
        print("所有推荐已复盘")
        return
    
    print(f"\n{'='*60}")
    print(f"📋 复盘分析 - {today}")
    print(f"{'='*60}")
    
    for rec in pending:
        symbol = rec['symbol']
        rec_date = rec['date']
        rec_price = rec['recommend_price']
        category = rec['category']
        
        print(f"\n🔍 {symbol} {rec['name']} ({category})")
        print(f"   推荐日期: {rec_date} | 推荐价: {rec_price:.2f}")
        
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
                    
                    hit = 2 <= change_pct <= 5
                    result = {
                        'next_day_open': quote['open'],
                        'next_day_high': quote['high'],
                        'next_day_close': current,
                        'next_day_change': round(change_pct, 2),
                        'hit': hit,
                        'max_profit': round(change_pct, 2),
                    }
                    
                    optimizer.update_result(symbol, rec_date,
                        quote['open'], quote['high'], current, round(change_pct, 2))
                    
                    status = "✅ 命中" if hit else "❌ 未命中"
                    print(f"   次日表现: 开{quote['open']:.2f} 高{quote['high']:.2f} 收{current:.2f}")
                    print(f"   次日涨幅: {change_pct:.2f}% {status}")
                else:
                    print(f"   ⚠️ 无法获取数据，跳过")
                    continue
            else:
                # 从K线找推荐日次日的数据
                # kline按日期倒序，找推荐日期后一天
                rec_date_str = rec_date.replace('-', '-')  # 确保格式一致
                
                next_day_data = None
                for i, k in enumerate(kline):
                    if k['day'] > rec_date and k['day'] <= today:
                        next_day_data = k
                        break
                
                if next_day_data:
                    open_price = next_day_data['open']
                    high_price = next_day_data['high']
                    close_price = next_day_data['close']
                    
                    # 用推荐日收盘价计算涨幅
                    change_from_rec = (close_price - rec_price) / rec_price * 100
                    max_change = (high_price - rec_price) / rec_price * 100
                    
                    hit = 2 <= change_from_rec <= 5
                    # 也考虑日内最高涨幅
                    max_hit = 2 <= max_change <= 5
                    
                    result = {
                        'next_day_open': open_price,
                        'next_day_high': high_price,
                        'next_day_close': close_price,
                        'next_day_change': round(change_from_rec, 2),
                        'hit': hit,
                        'max_profit': round(max_change, 2),
                        'max_hit': max_hit,
                    }
                    
                    optimizer.update_result(symbol, rec_date,
                        open_price, high_price, close_price, round(change_from_rec, 2))
                    
                    status = "✅ 命中" if hit else "❌ 未命中"
                    max_status = "✅" if max_hit else ""
                    print(f"   次日表现: 开{open_price:.2f} 高{high_price:.2f} 收{close_price:.2f}")
                    print(f"   收盘涨幅: {change_from_rec:.2f}% {status}")
                    print(f"   最大涨幅: {max_change:.2f}% {max_status}")
                else:
                    # K线未包含次日数据，用实时行情对比推荐价来复盘
                    quote = get_realtime_quote(symbol)
                    if quote and quote['prev_close'] > 0:
                        # 用实时行情的当日数据作为次日数据
                        open_price = quote['open']
                        high_price = quote['high']
                        current = quote['current']
                        # 涨幅从推荐价算起
                        change_from_rec = (current - rec_price) / rec_price * 100
                        
                        hit = 2 <= change_from_rec <= 5
                        optimizer.update_result(symbol, rec_date,
                            open_price, high_price, current, round(change_from_rec, 2))
                        
                        status = "✅ 命中" if hit else "❌ 未命中"
                        print(f"   次日(实时): 开{open_price:.2f} 高{high_price:.2f} 收{current:.2f}")
                        print(f"   涨幅(从推荐价): {change_from_rec:.2f}% {status}")
                    else:
                        print(f"   ⚠️ 无次日数据，跳过")
        
        except Exception as e:
            print(f"   ❌ 复盘失败: {e}")
    
    # 统计
    completed = [r for r in optimizer.history if r.get('result')]
    if completed:
        hits = [r for r in completed if r['result']['hit']]
        win_rate = len(hits) / len(completed)
        print(f"\n{'='*60}")
        print(f"📊 累计统计: 总推荐 {len(completed)} 次 | 命中 {len(hits)} 次 | 胜率 {win_rate:.1%}")
        print(f"{'='*60}")
    
    # 执行优化
    optimizer.optimize()
    
    # 微信推送复盘
    print("\n📱 推送复盘到企业微信...")
    from wechat_push import push_review
    push_review()


if __name__ == '__main__':
    review_previous_recommendations()
