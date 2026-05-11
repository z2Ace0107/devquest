# -*- coding: utf-8 -*-
"""
DevQuest Log — Streamlit 仪表盘

提供以下功能:
- 项目总览仪表盘（KPI 卡片 + 图表）
- 对话导入与问题提取
- 问题列表（筛选/排序/分页）
- 问题详情（含 STAR 故事）
- 语义搜索
- 手动评分
"""

import os
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(
    Path(__file__).resolve().parent.parent / ".env"
)

import streamlit as st
import requests
import pandas as pd
import json

# ── 配置 ────────────────────────────────────────────────────────
API_BASE = os.getenv("API_BASE", "http://localhost:8000")

st.set_page_config(
    page_title="DevQuest Log",
    page_icon="📋",
    layout="wide",
)


# ── API 工具函数 ────────────────────────────────────────────────

def api_get(path: str, params: dict = None) -> dict:
    try:
        r = requests.get(f"{API_BASE}{path}", params=params, timeout=30)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        st.error(f"API 错误: {e}")
        return {}


def api_post(path: str, body: dict = None) -> dict:
    try:
        r = requests.post(f"{API_BASE}{path}", json=body, timeout=120)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        st.error(f"API 错误: {e}")
        return {}


def api_put(path: str, body: dict = None) -> dict:
    try:
        r = requests.put(f"{API_BASE}{path}", json=body, timeout=30)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        st.error(f"API 错误: {e}")
        return {}


# ══════════════════════════════════════════════════════════════════
# 侧边栏导航
# ══════════════════════════════════════════════════════════════════

st.sidebar.title("📋 DevQuest Log")
st.sidebar.caption("开发者项目经验管理与智能复盘")

page = st.sidebar.radio(
    "导航",
    ["仪表盘", "导入对话", "问题列表", "经验搜索"],
    label_visibility="collapsed",
)

st.sidebar.divider()
st.sidebar.caption(f"后端: {API_BASE}")


# ══════════════════════════════════════════════════════════════════
# Page 1: 仪表盘
# ══════════════════════════════════════════════════════════════════

if page == "仪表盘":
    st.title("项目总览")

    data = api_get("/dashboard")

    if data.get("total", 0) == 0:
        st.info("暂无数据，请先导入对话。")
        st.stop()

    # KPI 卡片
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("总问题数", data["total"])
    c2.metric("平均优先级", data["avg_score"])
    c3.metric("技术栈种类", len(data.get("top_tech", [])))
    c4.metric("问题类型数", len(data.get("by_type", {})))

    st.divider()

    # 图表行
    col_left, col_right = st.columns(2)

    with col_left:
        st.subheader("按类型分布")
        if data.get("by_type"):
            df_type = pd.DataFrame(
                data["by_type"].items(), columns=["类型", "数量"]
            )
            st.bar_chart(df_type.set_index("类型"))

    with col_right:
        st.subheader("按分数段分布")
        if data.get("by_score_range"):
            df_score = pd.DataFrame(
                data["by_score_range"].items(), columns=["分数段", "数量"]
            )
            st.bar_chart(df_score.set_index("分数段"))

    st.subheader("热门技术栈 Top 10")
    if data.get("top_tech"):
        df_tech = pd.DataFrame(data["top_tech"])
        st.bar_chart(df_tech.set_index("tech"))


# ══════════════════════════════════════════════════════════════════
# Page 2: 导入对话
# ══════════════════════════════════════════════════════════════════

elif page == "导入对话":
    st.title("导入 AI 编程对话")

    col1, col2 = st.columns([2, 1])

    with col1:
        project_name = st.text_input(
            "项目名称", placeholder="例如: 电商后台管理系统"
        )

    with col2:
        uploaded_file = st.file_uploader(
            "上传对话文件（txt）", type=["txt"]
        )

    if uploaded_file:
        conversation_text = uploaded_file.read().decode("utf-8")
        st.text_area("对话预览", conversation_text, height=200)
    else:
        conversation_text = st.text_area(
            "对话内容",
            placeholder="粘贴 AI 编程对话记录...\n\nAce: 我的 FastAPI 应用在 Docker 容器里启动后所有请求返回 404...\nAI: 检查一下路由注册和 PYTHONPATH...",
            height=200,
        )

    if st.button("提取问题", type="primary", disabled=not (project_name and conversation_text.strip())):
        with st.spinner("正在用 LLM 分析对话..."):
            result = api_post("/extract", {
                "project_name": project_name,
                "conversation_text": conversation_text.strip(),
            })

        if result.get("count", 0) > 0:
            st.success(f"成功提取 {result['count']} 个问题，{result.get('indexed', 0)} 个已索引")
            st.json(result["problems"])
        else:
            st.info(result.get("message", "未识别到技术问题"))


# ══════════════════════════════════════════════════════════════════
# Page 3: 问题列表
# ══════════════════════════════════════════════════════════════════

elif page == "问题列表":
    st.title("问题列表")

    # 筛选栏
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        project_filter = st.text_input("项目", placeholder="全部")
    with c2:
        tech_filter = st.text_input("技术栈", placeholder="全部")
    with c3:
        min_score = st.number_input("最低分", 1, 10, 1)
    with c4:
        type_filter = st.selectbox(
            "类型", ["全部", "Bug", "性能优化", "架构决策", "环境配置", "API调试"]
        )

    params = {"limit": 100}
    if project_filter:
        params["project"] = project_filter
    if tech_filter:
        params["tech"] = tech_filter
    if min_score > 1:
        params["min_score"] = min_score
    if type_filter != "全部":
        params["problem_type"] = type_filter

    data = api_get("/problems", params)

    if data.get("total", 0) == 0:
        st.info("没有匹配的问题")
        st.stop()

    st.caption(f"共 {data['total']} 条")

    problems = data["problems"]
    for i, p in enumerate(problems):
        score_color = "🟢" if p["priority_score"] >= 7 else ("🟡" if p["priority_score"] >= 4 else "🔴")
        star_badge = "⭐" if p.get("star_story") else ""

        with st.expander(
            f"{star_badge} {score_color} [{p['problem_type']}] {p['title']} "
            f"— 评分 {p['priority_score']}",
            expanded=(i == 0),
        ):
            tab1, tab2, tab3 = st.tabs(["详情", "解决方案", "修改评分"])

            with tab1:
                st.markdown(f"**描述**\n{p['description'] or '无'}")

                if p.get("attempts"):
                    try:
                        attempts = json.loads(p["attempts"])
                        st.markdown("**尝试过的方案**")
                        for a in attempts:
                            st.markdown(f"- {a}")
                    except (json.JSONDecodeError, TypeError):
                        st.markdown(f"**尝试过的方案**\n{p['attempts']}")

                st.markdown(f"**技术栈**: `{p['tech_stack']}`")
                st.caption(f"创建时间: {p['created_at']}")

            with tab2:
                st.markdown(p.get("solution", "无"))

                if not p.get("star_story"):
                    if st.button(f"生成 STAR 故事", key=f"star_{p['id']}"):
                        with st.spinner("生成中..."):
                            star_data = api_get(f"/star/{p['id']}")
                        if star_data.get("star"):
                            st.success("已生成")
                            st.rerun()
                else:
                    try:
                        star = json.loads(p["star_story"])
                        st.markdown("### STAR 故事")
                        st.markdown(f"**S - 情境**: {star.get('situation', '')}")
                        st.markdown(f"**T - 任务**: {star.get('task', '')}")
                        st.markdown(f"**A - 行动**: {star.get('action', '')}")
                        st.markdown(f"**R - 结果**: {star.get('result', '')}")
                    except (json.JSONDecodeError, TypeError):
                        st.text(p["star_story"])

            with tab3:
                new_score = st.slider(
                    "优先级评分", 1, 10, p["priority_score"],
                    key=f"score_{p['id']}",
                )
                if new_score != p["priority_score"]:
                    if st.button("保存评分", key=f"save_{p['id']}"):
                        api_put(f"/problem/{p['id']}/score", {"priority_score": new_score})
                        st.success("已更新")
                        st.rerun()


# ══════════════════════════════════════════════════════════════════
# Page 4: 经验搜索
# ══════════════════════════════════════════════════════════════════

elif page == "经验搜索":
    st.title("经验搜索")
    st.caption("用自然语言描述你遇到的问题，从历史经验库中检索相似解决方案")

    col1, col2 = st.columns([3, 1])
    with col1:
        query = st.text_input(
            "搜索", placeholder="例如: Docker 容器里 Python 模块找不到..."
        )
    with col2:
        k = st.selectbox("结果数", [3, 5, 10, 20], index=1)
        tech_filter = st.text_input("技术栈过滤", placeholder="可选")

    if query:
        with st.spinner("搜索中..."):
            params = {"q": query, "k": k}
            if tech_filter:
                params["tech"] = tech_filter
            data = api_get("/search", params)

        if not data.get("results"):
            st.info("未找到相似经验")
        else:
            st.success(f"找到 {data['count']} 条相关经验")

            for r in data["results"]:
                similarity = max(0, int((1 - r["distance"]) * 100))
                score_color = "🟢" if r["priority_score"] >= 7 else ("🟡" if r["priority_score"] >= 4 else "🔴")

                with st.container(border=True):
                    st.markdown(
                        f"### {score_color} {r['title']} "
                        f"_(匹配度 {similarity}%, 评分 {r['priority_score']})_"
                    )
                    st.caption(f"技术栈: `{r['tech_stack']}`")
                    st.markdown(r["document"][:500])
                    if len(r.get("document", "")) > 500:
                        st.caption("... (内容过长，已截断)")
