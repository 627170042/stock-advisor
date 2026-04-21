#!/bin/bash
# 从git分支恢复持久化数据
# GitHub Actions每次运行都是全新环境，需要从data分支恢复历史数据

git fetch origin data 2>/dev/null || echo "No data branch yet, starting fresh"

if git branch -r | grep -q "origin/data"; then
    # 从data分支检出数据文件
    git checkout origin/data -- data/ 2>/dev/null || echo "No data files in data branch"
    echo "Data restored from data branch"
else
    echo "No existing data, starting fresh"
    mkdir -p data/history data/reports data/config
fi
