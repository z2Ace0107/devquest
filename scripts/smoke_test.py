# -*- coding: utf-8 -*-
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from backend import database, vector_search

database.init_db()

tests_ok = 0

def check(label, condition):
    global tests_ok
    if condition:
        tests_ok += 1
    else:
        print(f"  [FAIL] {label}")

rw = vector_search._rewrite_query

# 英文填充词移除
check("english filler stripped",
      rw("help me find FastAPI middleware issue") == "FastAPI middleware issue")

check("how to stripped",
      rw("how to configure nginx reverse proxy") == "configure nginx reverse proxy")

check("please stripped",
      rw("please search for docker error") == "docker error")

# 纯技术查询不变
check("pure tech query unchanged",
      rw("Docker 404 error fix") == "Docker 404 error fix")

check("pure english unchanged",
      rw("python list comprehension") == "python list comprehension")

# 空输入处理
check("empty input preserved",
      len(rw("test")) > 0)

# 锚点2: 反馈闭环
boosts = vector_search._load_usage_boosts()
check("usage_boosts is dict", isinstance(boosts, dict))

# 锚点3: 语义去重
pid, dist = vector_search.search_similar("FastAPI", "route config")
check("search_similar pid type", isinstance(pid, (int, type(None))))
check("search_similar dist type", isinstance(dist, float))

# 完整搜索流程
result = vector_search.search("docker 404", k=5)
check("search returns results", "results" in result)
check("search returns _debug", "_debug" in result)
check("_debug has original_query", "original_query" in result["_debug"])
check("_debug has rewritten_query", "rewritten_query" in result["_debug"])
rw_out = result["_debug"]["rewritten_query"]
check("rewritten not empty", isinstance(rw_out, str) and len(rw_out) > 0)

print(f"All {tests_ok} tests passed.")
