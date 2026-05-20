"""测试 services.py — save_problem_service / record_feedback_service"""

import pytest
from backend.models import Problem


def test_save_problem_new():
    from backend import services
    result = services.save_problem_service(
        error="docker port 3000 already in use",
        solution="netsh int ipv4 set dynamicport tcp start=49152",
        attempts=["netstat查端口", "改docker-compose端口"],
        environment={"os": "win11", "docker": "26.1"},
        project="DevQuest",
    )
    assert result["problem_id"] > 0
    assert result["merged"] is False
    assert result["solution_version"] == 1


def test_save_problem_default_project():
    from backend import services
    result = services.save_problem_service(
        error="nginx 启动失败",
        solution="检查配置文件语法 nginx -t",
    )
    assert result["problem_id"] > 0


def test_record_feedback_helpful():
    from backend import services
    r = services.save_problem_service(
        error="test feedback", solution="test", project="DevQuest")
    pid = r["problem_id"]
    fb = services.record_feedback_service(pid, helpful=True)
    assert fb["feedback_score"] == 1.0
    assert fb["feedback_count"] == 1
    assert fb["usage_count"] == 10


def test_record_feedback_unhelpful():
    from backend import services
    r = services.save_problem_service(
        error="test feedback 2", solution="test", project="DevQuest")
    pid = r["problem_id"]
    services.record_feedback_service(pid, helpful=True)
    fb = services.record_feedback_service(pid, helpful=False)
    assert fb["feedback_score"] == 0.5
    assert fb["feedback_count"] == 2
    assert fb["usage_count"] == 8


def test_record_feedback_not_found():
    from backend import services
    result = services.record_feedback_service(99999, helpful=True)
    assert "error" in result
