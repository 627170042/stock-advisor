# A股T+1短线选股系统

自动选股 + 微信推送 + 次日复盘 + 策略自优化

## 运行时间（北京时间）
- **14:25** 每日推荐（GitHub Actions cron）
- **09:35** 次日复盘（GitHub Actions cron）

## 手动触发
在GitHub仓库的Actions页面，点击"Run workflow"，选择任务类型。

## 环境变量（GitHub Secrets）
- `WECHAT_WEBHOOK_URL`: 企业微信机器人Webhook地址

## 数据持久化
历史推荐记录保存在 `data/` 目录，通过git的 `data` 分支跨运行持久化。
