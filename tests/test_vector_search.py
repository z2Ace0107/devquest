"""测试 vector_search.py — RRF 融合 / 环境匹配 / 时效衰减 / 查询改写 / 图谱扩展"""

from backend.vector_search import (
    _rrf_fusion, _rewrite_query, _graph_expand,
)


def test_rewrite_removes_filler():
    assert _rewrite_query("帮我查一下docker怎么配置") == "docker 配置"
    assert _rewrite_query("help me find async error") == "async error"
    assert _rewrite_query("上次那个端口冲突怎么修的") == "端口冲突"


def test_rewrite_preserves_technical():
    assert _rewrite_query("FastAPI router 404 Docker") == "FastAPI router 404 Docker"


def test_rewrite_empty_returns_original():
    result = _rewrite_query("怎么")
    assert len(result) > 0


def test_rrf_basic_merge():
    vec = [{"problem_id": 1, "title": "A", "tech_stack": "docker", "priority_score": 5}]
    kw = [{"problem_id": 2, "title": "B", "tech_stack": "nginx", "priority_score": 5}]
    result = _rrf_fusion(vec, kw, k=2)
    assert len(result) == 2


def test_rrf_usage_boost():
    vec = [
        {"problem_id": 1, "title": "高频", "tech_stack": "docker"},
        {"problem_id": 2, "title": "低频", "tech_stack": "docker"},
    ]
    usage = {1: 10}
    result = _rrf_fusion(vec, [], k=2, usage_boosts=usage)
    assert result[0]["problem_id"] == 1


def test_rrf_empty_inputs():
    assert _rrf_fusion([], [], k=5) == []


def test_rrf_returns_environment_field():
    vec = [{"problem_id": 1, "title": "test", "tech_stack": ""}]
    result = _rrf_fusion(vec, [], k=1, environment={"os": "win11"})
    assert "environment" in result[0]
    assert "environment_match" in result[0]


def test_graph_expand_empty_input():
    result = _graph_expand([], k=5)
    assert result == []


def test_graph_expand_without_links():
    """没有 Link 数据时返回空列表，不会崩溃。"""
    fused = [
        {"problem_id": 99999, "title": "不存在的 Problem", "tech_stack": "", "rrf_score": 0.05},
    ]
    result = _graph_expand(fused, k=3)
    assert isinstance(result, list)
    assert len(result) == 0
