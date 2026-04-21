#!/bin/bash
# 将数据文件保存到data分支，实现跨运行持久化
# 包括：历史推荐记录、最新推荐、策略参数等

git config user.name "Stock Bot"
git config user.email "stock-bot@users.noreply.github.com"

# 切换到data分支（不存在则创建）
git checkout -B data

# 添加数据文件
git add data/ 2>/dev/null || true

# 只在有变更时提交
if git diff --staged --quiet; then
    echo "No data changes to save"
else
    git commit -m "Update stock data - $(date +%Y-%m-%d)"
    git push origin data --force
    echo "Data saved to data branch"
fi

# 切回main分支
git checkout main 2>/dev/null || true
