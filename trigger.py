#!/usr/bin/env /root/.pyenv/versions/3.11.1/bin/python3
"""
外部触发GitHub Actions - 保底方案
通过GitHub API的repository_dispatch触发workflow
可以被任何外部cron服务调用
"""
import requests
import sys
import os

# GitHub配置
REPO = "627170042/stock-advisor"
PAT = os.environ.get("GH_PAT", "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")

def trigger(task="recommend"):
    """通过repository_dispatch触发GitHub Actions"""
    url = f"https://api.github.com/repos/{REPO}/dispatches"
    headers = {
        "Authorization": f"token {PAT}",
        "Accept": "application/vnd.github.v3+json"
    }
    payload = {
        "event_type": task
    }
    resp = requests.post(url, json=payload, headers=headers, timeout=15)
    if resp.status_code == 204:
        print(f"✅ {task} 触发成功")
        return True
    else:
        print(f"❌ {task} 触发失败: {resp.status_code} {resp.text}")
        return False

if __name__ == "__main__":
    task = sys.argv[1] if len(sys.argv) > 1 else "recommend"
    trigger(task)
