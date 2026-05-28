"""
A股T+1短线选股系统 - 企业微信推送模块 v6
适配GitHub Actions：优先从环境变量读取webhook URL

v5→v6 变化：
1. 推荐消息格式从Budget/Strong双类别 → 单一推荐列表
2. 新增大盘环境显示
3. 复盘消息增加max_profit显示
"""
import json
import os
import requests
from datetime import datetime

# 数据目录（GitHub Actions中通过环境变量DATA_DIR指定）
DATA_DIR = os.environ.get('DATA_DIR', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data'))
CONFIG_FILE = os.path.join(DATA_DIR, 'config', 'wechat_webhook.json')


def load_webhook_url():
    """加载Webhook地址：优先环境变量，其次配置文件"""
    env_url = os.environ.get('WECHAT_WEBHOOK_URL', '')
    if env_url:
        return env_url
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r') as f:
            config = json.load(f)
            return config.get('webhook_url', '')
    return ''


def save_webhook_url(url):
    """保存Webhook地址"""
    config_dir = os.path.dirname(CONFIG_FILE)
    os.makedirs(config_dir, exist_ok=True)
    with open(CONFIG_FILE, 'w') as f:
        json.dump({'webhook_url': url}, f, indent=2)


def send_wechat_message(content, msgtype='markdown'):
    """发送企业微信机器人消息"""
    webhook_url = load_webhook_url()
    if not webhook_url:
        print("⚠️ 未配置Webhook地址")
        return False

    payload = {
        'msgtype': msgtype,
        msgtype: {
            'content': content
        }
    }

    try:
        resp = requests.post(
            webhook_url,
            json=payload,
            headers={'Content-Type': 'application/json'},
            timeout=15
        )
        data = resp.json()
        if data.get('errcode') == 0:
            print("✅ 微信推送成功")
            return True
        else:
            print(f"❌ 推送失败: {data}")
            return False
    except Exception as e:
        print(f"❌ 推送异常: {e}")
        return False


def format_recommend_message():
    """v6: 格式化推荐消息（单一类别）"""
    latest_file = os.path.join(DATA_DIR, 'reports', 'latest.json')
    if not os.path.exists(latest_file):
        return None

    with open(latest_file, 'r', encoding='utf-8') as f:
        data = json.load(f)

    date = data.get('date', datetime.now().strftime('%Y-%m-%d'))

    # 读取历史胜率
    history_file = os.path.join(DATA_DIR, 'history', 'recommendations.json')
    win_rate = '0.0%'
    total = 0
    hits = 0
    if os.path.exists(history_file):
        with open(history_file, 'r', encoding='utf-8') as f:
            history = json.load(f)
        completed = [r for r in history if r.get('result')]
        hits_list = [r for r in completed if r['result']['hit']]
        total = len(completed)
        hits = len(hits_list)
        win_rate = f"{hits/total:.1%}" if total > 0 else '0.0%'

    # 大盘环境
    market_env = data.get('market_env', {})
    env_level = market_env.get('level', 'neutral')
    env_score = market_env.get('score', 50)
    level_cn = {'bull': '多头', 'neutral': '震荡', 'bear': '空头'}.get(env_level, env_level)

    picks = data.get('picks', [])

    content = f"""# 📈 T+1短线选股推荐 (v6)

> {date} | 胜率 <font color="warning">{win_rate}</font> ({hits}/{total}) | 大盘: {level_cn}({env_score}分)

---

"""

    if not picks:
        content += "今日暂停推荐（大盘环境不佳或无符合条件的股票）\n"
    else:
        for i, p in enumerate(picks, 1):
            signals_str = ' | '.join(p.get('signals', []))[:60] if p.get('signals') else ''
            content += f"""## 推荐第{i}名
**{p['name']}** `{p['symbol']}`

<font color="warning">💰 价格: {p['price']:.2f}元</font>
> 涨幅: {p['changepercent']:.2f}%
> 趋势: {p.get('trend_score', 'N/A')} | 技术: {p.get('tech_score', 'N/A')}
> 次日概率: <font color="warning">{p.get('next_day_prob', 0):.0%}</font>
> 综合评分: **{p.get('total_score', 0):.1f}**
"""
            # 板块热度
            sh = p.get('sector_heat')
            if sh and sh.get('sector_name', '') != '未知':
                heat_icon = '🔥' if sh['heat_level'] == 'hot' else ('❄️' if sh['heat_level'] == 'cold' else '📊')
                content += f"> 板块: {heat_icon} {sh['sector_name']}({sh['heat_score']}分)\n"
            if signals_str:
                content += f"> 信号: {signals_str}\n"
            content += "\n"

    content += """---
## 📋 操作建议
> 买入: 尾盘14:30-14:55分批建仓
> 止损: 次日低开超2%立即止损
> 止盈: 日内涨幅达2%即可考虑止盈
> 目标: **2%-5%**，达标即走

<font color="comment">⚠️ 仅供参考，不构成投资建议 | v6趋势延续+动量加速</font>
"""

    return content


def format_review_message():
    """v6: 格式化复盘消息（含max_profit）"""
    history_file = os.path.join(DATA_DIR, 'history', 'recommendations.json')
    if not os.path.exists(history_file):
        return None

    with open(history_file, 'r', encoding='utf-8') as f:
        history = json.load(f)

    completed = [r for r in history if r.get('result')]
    if not completed:
        return None

    recent = completed[-2:] if len(completed) >= 2 else completed
    hits_all = [r for r in completed if r['result']['hit']]
    win_rate = f"{len(hits_all)/len(completed):.1%}" if completed else '0.0%'
    today = datetime.now().strftime('%Y-%m-%d')

    content = f"""# 📋 T+1选股复盘 (v6)

> {today} | 累计胜率 <font color="warning">{win_rate}</font> ({len(hits_all)}/{len(completed)})

---

"""

    for rec in recent:
        result = rec['result']
        hit_mark = "✅ 命中" if result['hit'] else "❌ 未命中"
        color = "info" if result['hit'] else "warning"

        max_profit_str = ''
        if result.get('max_profit') is not None:
            max_profit_str = f"\n> 日内最高收益: <font color=\"info\">{result['max_profit']:.2f}%</font>"

        content += f"""## {rec['name']} `{rec['symbol']}`
> 推荐: {rec['date']} ¥{rec['recommend_price']:.2f}

<font color="{color}">{hit_mark} | 次日涨幅 {result['next_day_change']:.2f}%</font>
> 开盘: ¥{result['next_day_open']:.2f}
> 最高: ¥{result['next_day_high']:.2f}
> 收盘: ¥{result['next_day_close']:.2f}{max_profit_str}

"""

    content += """---
<font color="comment">⚠️ 仅供参考，不构成投资建议 | v6命中标准: 日内最高收益≥2%</font>
"""

    return content


def push_recommendation():
    """推送推荐消息到企业微信"""
    content = format_recommend_message()
    if content:
        return send_wechat_message(content)
    print("⚠️ 无推荐内容可推送")
    return False


def push_review():
    """推送复盘消息到企业微信"""
    content = format_review_message()
    if content:
        return send_wechat_message(content)
    print("⚠️ 无复盘内容可推送")
    return False


def format_optimization_notice(optimizer):
    """格式化策略优化通知"""
    params = optimizer.PARAMS
    calibration = optimizer.prob_calibration
    meta = optimizer.meta
    win_rate = meta.get('last_win_rate', 0)
    sample_size = meta.get('total_reviews', 0)

    content = f"""# 🔧 策略自适应优化 v6

> {datetime.now().strftime('%Y-%m-%d')} | 样本量 {sample_size} | 胜率 <font color="warning">{win_rate:.1%}</font>

---

## 概率校准
> 偏移量: <font color="warning">{calibration.get('offset', 0):.4f}</font>

---

## 当前参数
> 概率门槛: {params.get('min_next_day_prob', 0.18):.2f}
> 涨幅上限: {params.get('max_recommend_day_change', 7.0):.1f}%
> 技术分门槛: {params.get('min_tech_score', 40)}
> 概率入选门槛: {params.get('min_prob_threshold', 0.20):.2f}
> 黑名单天数: {params.get('repeat_blacklist_days', 10)}

---

## 权重配置
> 趋势: {params.get('weight_trend', 0.35):.3f}
> 技术: {params.get('weight_tech', 0.45):.3f}
> 概率: {params.get('weight_prob', 0.20):.3f}

<font color="comment">🤖 v6参数由策略优化器自动调整 | 最小样本100</font>
"""
    return content


def push_optimization_notice(optimizer):
    """推送策略优化通知到企业微信"""
    content = format_optimization_notice(optimizer)
    if content:
        return send_wechat_message(content)
    print("⚠️ 无优化通知可推送")
    return False


if __name__ == '__main__':
    import sys
    if len(sys.argv) < 2:
        print("用法: python3 wechat_push.py [recommend|review|config <url>|test]")
        sys.exit(0)

    cmd = sys.argv[1]
    if cmd == 'recommend':
        push_recommendation()
    elif cmd == 'review':
        push_review()
    elif cmd == 'config' and len(sys.argv) >= 3:
        save_webhook_url(sys.argv[2])
        print("✅ Webhook已保存")
    elif cmd == 'test':
        send_wechat_message("**🤖 A股短线助手v6已上线**\n> 趋势延续+动量加速策略 | 推送通道测试成功！")
    else:
        print("未知命令")
