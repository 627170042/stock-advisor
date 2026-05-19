#!/bin/bash
# A股T+1选股系统 - 本地执行+GitHub数据同步
# 此脚本由Agent自动化任务调用，直接在sandbox上执行推荐/复盘
# 不再依赖GitHub Actions的远程触发

set -e

TASK="${1:-recommend}"  # recommend | review
DIR="/workspace/stock-advisor-github"
LOG="$DIR/logs/local_$(date +%Y%m%d_%H%M%S).log"
PYTHON=/root/.pyenv/versions/3.11.1/bin/python3

mkdir -p "$DIR/logs"

echo "[$(date)] === 开始本地执行: $TASK ===" | tee -a "$LOG"

cd "$DIR"

# 第1步：从GitHub同步最新数据
echo "[$(date)] 同步GitHub数据..." | tee -a "$LOG"
git fetch origin data 2>&1 | tee -a "$LOG" || true
if git branch -r | grep -q "origin/data"; then
    git checkout origin/data -- data/ 2>&1 | tee -a "$LOG" || true
else
    mkdir -p data/history data/reports data/config
fi
echo "[$(date)] 数据同步完成" | tee -a "$LOG"

# 第2步：设置环境变量
export WECHAT_WEBHOOK_URL="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=b7c930e8-3865-4306-bf0d-d8418a2f214a"
export DATA_DIR="$DIR/data"

# 第3步：执行Python脚本
echo "[$(date)] 执行 $TASK..." | tee -a "$LOG"
if [ "$TASK" = "recommend" ]; then
    $PYTHON "$DIR/main.py" 2>&1 | tee -a "$LOG"
elif [ "$TASK" = "review" ]; then
    $PYTHON "$DIR/review.py" 2>&1 | tee -a "$LOG"
else
    echo "未知任务: $TASK" | tee -a "$LOG"
    exit 1
fi
EXIT_CODE=$?

echo "[$(date)] Python执行完成，退出码: $EXIT_CODE" | tee -a "$LOG"

# 第4步：将结果同步回GitHub
echo "[$(date)] 同步结果到GitHub..." | tee -a "$LOG"
cd "$DIR"
git config user.name "Stock Bot" 2>/dev/null || true
git config user.email "627170042@qq.com" 2>/dev/null || true
git add data/ 2>/dev/null || true
if ! git diff --staged --quiet 2>/dev/null; then
    git commit -m "$TASK $(TZ=Asia/Shanghai date +%Y-%m-%d)" 2>&1 | tee -a "$LOG"
    git push origin HEAD:data --force 2>&1 | tee -a "$LOG"
    echo "[$(date)] ✅ 数据已同步到GitHub" | tee -a "$LOG"
else
    echo "[$(date)] 无数据变更" | tee -a "$LOG"
fi

echo "[$(date)] === 执行完成: $TASK ===" | tee -a "$LOG"
exit $EXIT_CODE
