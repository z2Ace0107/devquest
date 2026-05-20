# -*- coding: utf-8 -*-
"""DevQuest — 飞书推送模块

通过飞书自定义机器人 Webhook 推送经验卡片。
不依赖任何 SDK、CLI、OAuth——只需一个 Webhook URL。
"""

import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

import requests

from backend.database import SessionLocal
from backend.models import Problem

logger = logging.getLogger(__name__)


def _fetch_weekly_problems() -> list[dict]:
    """查询本周新增问题，按评分降序。"""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    week_start = now - timedelta(days=now.weekday())
    week_start = week_start.replace(hour=0, minute=0, second=0, microsecond=0)

    db = SessionLocal()
    try:
        problems = (
            db.query(Problem)
            .filter(Problem.created_at >= week_start)
            .order_by(Problem.priority_score.desc(), Problem.created_at.desc())
            .all()
        )
        return [p.to_dict() for p in problems]
    finally:
        db.close()


def _format_weekly_markdown(problems: list[dict]) -> str:
    """将本周问题列表格式化为飞书卡片 Markdown。"""
    if not problems:
        return "本周暂无新增技术问题。🎉"

    by_type = {}
    for p in problems:
        pt = p.get("problem_type") or "未分类"
        by_type[pt] = by_type.get(pt, 0) + 1

    type_summary = " · ".join(f"{k} {v}条" for k, v in sorted(by_type.items(), key=lambda x: -x[1])[:5])

    # Top 5 问题
    top_lines = []
    for i, p in enumerate(problems[:5], 1):
        score = p.get("priority_score", 5)
        score_bar = "🔴" if score >= 7 else ("🟡" if score >= 4 else "🟢")
        env_str = ""
        if p.get("environment"):
            try:
                env = json.loads(p["environment"]) if isinstance(p["environment"], str) else p["environment"]
                env_str = f" · {env.get('os', '')}"
            except (json.JSONDecodeError, TypeError):
                pass
        top_lines.append(
            f"**{i}. [{p.get('problem_type', '未知')}] {p.get('title', '无标题')}**\n"
            f"{score_bar} 评分 {score}/10 · 方案版本 v{p.get('solution_version', 1)}{env_str}"
        )

    lines = [
        f"**本周新增 {len(problems)} 个技术问题**",
        "",
        f"类型分布：{type_summary}",
        "",
        "---",
        "**Top 问题**",
        "",
    ] + top_lines

    return "\n".join(lines)


def send_card(webhook_url: str, title: str, content: str, template: str = "blue") -> dict:
    """发送一条飞书消息卡片。

    参数:
        webhook_url: 飞书机器人 Webhook 地址
        title: 卡片标题
        content: 卡片正文（支持 Markdown）
        template: 卡片头部颜色 (blue/yellow/red/purple/green)
    """
    card = {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": title},
                "template": template,
            },
            "elements": [
                {"tag": "markdown", "content": content}
            ],
        },
    }

    try:
        r = requests.post(webhook_url, json=card, timeout=10)
        r.raise_for_status()
        result = r.json()
        logger.info("飞书推送成功: %s", title)
        return {"ok": True, "code": result.get("code"), "msg": result.get("msg")}
    except requests.RequestException as e:
        logger.exception("飞书推送失败")
        return {"ok": False, "error": str(e)}


def push_weekly_summary(webhook_url: str) -> dict:
    """推送本周经验摘要到飞书。

    可在 Rule-Maker 反思完成后调用，也可手动触发。
    """
    problems = _fetch_weekly_problems()
    content = _format_weekly_markdown(problems)

    week_label = datetime.now(timezone.utc).strftime("%Y-W%W")
    title = f"📊 DevQuest 本周经验摘要 ({week_label})"

    result = send_card(webhook_url, title, content)
    result["problem_count"] = len(problems)
    return result


def push_knowledge_card(webhook_url: str, summary: str, rules: list[dict]) -> dict:
    """推送 Rule-Maker 反思结果到飞书（团队知识推送）。

    参数:
        summary: 本周模式摘要
        rules: LLM 生成的规则列表
    """
    rule_lines = []
    for i, rule in enumerate(rules, 1):
        conf = rule.get("confidence", 0.5)
        conf_label = "🟢 高" if conf >= 0.9 else ("🟡 中" if conf >= 0.7 else "🔴 低")
        rule_lines.append(f"**规则 {i}** {conf_label}：{rule.get('rule', '')}")
        if rule.get("rationale"):
            rule_lines.append(f"_{rule['rationale']}_")

    content = f"**{summary}**\n\n" + "\n\n".join(rule_lines) if rule_lines else summary
    content += "\n\n---\n📎 详情见 `rules_suggestions.md`"

    title = "🧠 DevQuest 团队知识推送"
    return send_card(webhook_url, title, content, template="purple")
