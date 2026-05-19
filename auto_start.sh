#!/bin/bash
# A股T+1选股系统 - 自启动守护脚本
# 每次sandbox shell启动时执行，确保定时任务就绪
# 这是整个调度系统的基石

DIR="/workspace/stock-advisor-github"
LOG="$DIR/logs/auto_start.log"
PYTHON=/root/.pyenv/versions/3.11.1/bin/python3
TODAY=$(TZ=Asia/Shanghai date +%Y-%m-%d)
DOW=$(TZ=Asia/Shanghai date +%u)

mkdir -p "$DIR/logs"

echo "[$(TZ=Asia/Shanghai date '+%Y-%m-%d %H:%M:%S')] === 自动守护启动 ===" >> "$LOG"

# ===== 第1步：设置正确的crontab =====
# 写入使用新系统(run_local.sh)的crontab，覆盖任何旧内容
# 注意：sandbox是UTC时区，北京时间14:00 = UTC 06:00, 北京时间14:25 = UTC 06:25
CORRECT_CRON="0 6 * * 1-5 /bin/bash $DIR/run_local.sh review >> $DIR/logs/cron_review.log 2>&1
25 6 * * 1-5 /bin/bash $DIR/run_local.sh recommend >> $DIR/logs/cron_recommend.log 2>&1
*/5 * * * * pgrep -x cron > /dev/null || (cron && echo [\$(date)] cron restarted >> $DIR/logs/cron_watchdog.log)"

CURRENT_CRON=$(crontab -l 2>/dev/null || echo "")

if [ "$CURRENT_CRON" != "$CORRECT_CRON" ]; then
    echo "[$(TZ=Asia/Shanghai date '+%Y-%m-%d %H:%M:%S')] 更新crontab" >> "$LOG"
    echo "$CORRECT_CRON" | crontab -
fi

# ===== 第2步：确保cron运行 =====
if ! pgrep -x cron > /dev/null 2>&1; then
    echo "[$(TZ=Asia/Shanghai date '+%Y-%m-%d %H:%M:%S')] 启动cron" >> "$LOG"
    cron
fi

# ===== 第3步：补执行今天可能错过的任务 =====
# 检查今天是否是交易日（周一到周五）
if [ "$DOW" -le 5 ]; then
    BJ_HOUR=$(TZ=Asia/Shanghai date +%H)
    BJ_MIN=$(TZ=Asia/Shanghai date +%M)
    BJ_MINS=$((10#$BJ_HOUR * 60 + 10#$BJ_MIN))
    
    # 从GitHub拉取最新数据来检查
    cd "$DIR"
    git fetch origin data 2>/dev/null || true
    if git branch -r 2>/dev/null | grep -q "origin/data"; then
        git checkout origin/data -- data/ 2>/dev/null || true
    fi
    
    # 检查今天是否已有推荐记录
    HAS_RECOMMEND=false
    HAS_REVIEW=false
    
    if [ -f "$DIR/data/history/recommendations.json" ]; then
        TODAY_RECS=$($PYTHON -c "
import json
try:
    with open('$DIR/data/history/recommendations.json') as f:
        d = json.load(f)
    today = [r for r in d if r['date'] == '$TODAY']
    print(len(today))
except:
    print(0)
" 2>/dev/null || echo "0")
        
        if [ "$TODAY_RECS" -gt 0 ]; then
            HAS_RECOMMEND=true
        fi
        
        # 检查昨天的推荐是否已复盘
        YESTERDAY_REVIEWED=$($PYTHON -c "
import json
from datetime import datetime, timedelta
try:
    with open('$DIR/data/history/recommendations.json') as f:
        d = json.load(f)
    yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
    yest_recs = [r for r in d if r['date'] == yesterday]
    reviewed = [r for r in yest_recs if r.get('result') is not None]
    if yest_recs and len(reviewed) == len(yest_recs):
        print('yes')
    else:
        print('no')
except:
    print('error')
" 2>/dev/null || echo "error")
        
        if [ "$YESTERDAY_REVIEWED" = "yes" ]; then
            HAS_REVIEW=true
        fi
    fi
    
    # 如果已过14:00且复盘未完成，立即执行
    if [ $BJ_MINS -ge 840 ] && [ "$HAS_REVIEW" = false ]; then
        echo "[$(TZ=Asia/Shanghai date '+%Y-%m-%d %H:%M:%S')] 补执行复盘" >> "$LOG"
        bash "$DIR/run_local.sh" review >> "$LOG" 2>&1 &
    fi
    
    # 如果已过14:25且推荐未完成，立即执行
    if [ $BJ_MINS -ge 865 ] && [ "$HAS_RECOMMEND" = false ]; then
        echo "[$(TZ=Asia/Shanghai date '+%Y-%m-%d %H:%M:%S')] 补执行推荐" >> "$LOG"
        bash "$DIR/run_local.sh" recommend >> "$LOG" 2>&1 &
    fi
fi

echo "[$(TZ=Asia/Shanghai date '+%Y-%m-%d %H:%M:%S')] === 守护完成 ===" >> "$LOG"
