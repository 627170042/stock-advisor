#!/bin/bash
# A股T+1选股系统 - GitHub Actions 可靠触发器
# 包含重试机制和运行验证，解决Agent调度可能丢失dispatch的问题
set -e

REPO="627170042/stock-advisor"
TOKEN="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"
API_URL="https://api.github.com/repos/${REPO}/dispatches"
MAX_RETRIES=3
RETRY_INTERVAL=10

TASK="${1:-recommend}"  # recommend | review | check
TIMESTAMP=$(date -u +%Y-%m-%dT%H:%M:%SZ)
LOG_FILE="/workspace/stock-advisor-github/trigger.log"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"
}

log "=== 触发 GitHub Actions: ${TASK} ==="

# 第1步：发送 repository_dispatch
retry_count=0
dispatch_success=false

while [ $retry_count -lt $MAX_RETRIES ]; do
    retry_count=$((retry_count + 1))
    log "发送 dispatch (第 ${retry_count}/${MAX_RETRIES} 次)..."
    
    http_code=$(curl -s -o /dev/null -w "%{http_code}" \
        -X POST \
        -H "Accept: application/vnd.github+json" \
        -H "Authorization: Bearer ${TOKEN}" \
        "${API_URL}" \
        -d "{\"event_type\":\"${TASK}\",\"client_payload\":{\"source\":\"sandbox-trigger\",\"timestamp\":\"${TIMESTAMP}\",\"retry\":\"${retry_count}\"}}")
    
    if [ "$http_code" = "204" ]; then
        log "Dispatch 发送成功 (HTTP ${http_code})"
        dispatch_success=true
        break
    else
        log "Dispatch 发送失败 (HTTP ${http_code})，${RETRY_INTERVAL}秒后重试..."
        sleep $RETRY_INTERVAL
    fi
done

if [ "$dispatch_success" = false ]; then
    log "❌ Dispatch 发送失败，已重试 ${MAX_RETRIES} 次"
    # 尝试使用 gh CLI 作为备用方案
    log "尝试使用 gh CLI 备用方案..."
    if command -v gh &> /dev/null; then
        gh workflow run stock.yml --repo "${REPO}" -f task="${TASK}" 2>&1 | tee -a "$LOG_FILE"
        if [ $? -eq 0 ]; then
            log "✅ gh CLI 触发成功"
            dispatch_success=true
        fi
    fi
fi

if [ "$dispatch_success" = false ]; then
    log "❌ 所有触发方式均失败！"
    exit 1
fi

# 第2步：等待并验证运行是否创建
log "等待 GitHub Actions 创建运行..."
sleep 15

# 检查最近5分钟内是否有对应类型的运行
check_time=$(date -u -d '5 minutes ago' +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -v-5M +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo "")

if [ -n "$check_time" ]; then
    log "检查 ${check_time} 之后的运行记录..."
    runs=$(gh run list --repo "${REPO}" --limit 5 --json event,status,createdAt,displayTitle 2>&1 || echo "[]")
    log "最近运行: ${runs}"
    
    # 简单检查是否有 repository_dispatch 类型的新运行
    if echo "$runs" | grep -q "repository_dispatch"; then
        log "✅ 发现 repository_dispatch 运行，触发验证成功"
    else
        log "⚠️ 未发现新的 repository_dispatch 运行，但 dispatch API 返回成功"
        log "   可能运行正在排队中，或需要手动检查"
    fi
fi

log "=== 触发完成 ==="
exit 0
