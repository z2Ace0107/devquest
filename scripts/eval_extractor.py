# -*- coding: utf-8 -*-
"""
DevQuest — 提取引擎评测脚本

对 sample_conversations/ 中的对话运行 extractor，
与人工标注的预期结果对比，计算提取准确率。

使用:
    python scripts/eval_extractor.py
"""

import json
import sys
from pathlib import Path

# 确保项目根目录在 path 中
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from backend import extractor

# ── 配置 ───────────────────────────────────────────────────────
SAMPLES_DIR = Path(__file__).resolve().parent.parent / "sample_conversations"
EXPECTED_FILE = SAMPLES_DIR / "expected.json"


def load_expected():
    with open(EXPECTED_FILE, encoding="utf-8") as f:
        return json.load(f)


def load_conversation(filename: str) -> str:
    filepath = SAMPLES_DIR / filename
    if not filepath.exists():
        raise FileNotFoundError(f"样本文件不存在: {filepath}")
    return filepath.read_text(encoding="utf-8")


# ── 匹配算法 ───────────────────────────────────────────────────

def _keyword_score(extracted_text: str, keywords: list[str]) -> float:
    """计算提取文本中包含多少预期关键词。"""
    if not keywords:
        return 0.0
    text_lower = extracted_text.lower()
    hits = sum(1 for kw in keywords if kw.lower() in text_lower)
    return hits / len(keywords)


def _match_problems(extracted: list[dict], expected_list: list[dict]) -> dict:
    """
    贪心匹配：每个预期问题找最相似的提取结果。
    返回匹配对和未匹配的预期/提取。
    """
    matched_pairs = []
    used_extracted = set()

    for exp_idx, expected in enumerate(expected_list):
        best_score = 0.0
        best_ext_idx = -1

        for ext_idx, ext in enumerate(extracted):
            if ext_idx in used_extracted:
                continue

            # 标题关键词匹配为主
            title_score = _keyword_score(
                ext.get("title", ""), expected.get("title_keywords", [])
            )
            # 技术栈匹配为辅
            tech_score = _keyword_score(
                ext.get("tech_stack", ""), expected.get("tech_stack_keywords", [])
            )
            # 组合分数：标题权重 0.7，技术栈 0.3
            combined = title_score * 0.7 + tech_score * 0.3

            if combined > best_score:
                best_score = combined
                best_ext_idx = ext_idx

        if best_score >= 0.3 and best_ext_idx >= 0:
            matched_pairs.append({
                "expected_index": exp_idx,
                "extracted_index": best_ext_idx,
                "score": round(best_score, 3),
            })
            used_extracted.add(best_ext_idx)

    unmatched_expected = [
        i for i in range(len(expected_list))
        if not any(p["expected_index"] == i for p in matched_pairs)
    ]
    unmatched_extracted = [
        i for i in range(len(extracted))
        if i not in used_extracted
    ]

    return {
        "pairs": matched_pairs,
        "unmatched_expected": unmatched_expected,
        "unmatched_extracted": unmatched_extracted,
    }


# ── 指标计算 ───────────────────────────────────────────────────

def calculate_metrics(matches: dict, expected_list: list, extracted: list) -> dict:
    total_expected = len(expected_list)
    total_extracted = len(extracted)
    matched_count = len(matches["pairs"])

    # 召回率 = 匹配到的 / 预期的
    recall = matched_count / total_expected if total_expected > 0 else 0.0

    # 精确率 = 匹配到的 / 提取到的
    precision = matched_count / total_extracted if total_extracted > 0 else 0.0

    # F1 分数
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

    # 数量偏差
    count_deviation = abs(total_extracted - total_expected) / total_expected if total_expected > 0 else 0.0

    # 类型准确率（匹配到的 pair 中类型正确的比例）
    type_correct = 0
    for pair in matches["pairs"]:
        ext = extracted[pair["extracted_index"]]
        exp = expected_list[pair["expected_index"]]
        if ext.get("problem_type") == exp.get("problem_type"):
            type_correct += 1
    type_accuracy = type_correct / matched_count if matched_count > 0 else 0.0

    return {
        "recall": round(recall, 3),
        "precision": round(precision, 3),
        "f1": round(f1, 3),
        "count_deviation": round(count_deviation, 3),
        "type_accuracy": round(type_accuracy, 3),
        "total_expected": total_expected,
        "total_extracted": total_extracted,
        "matched": matched_count,
    }


# ── 报告生成 ───────────────────────────────────────────────────

def print_report(metrics: dict, matches: dict, expected_list: list, extracted: list, total_samples: int = 1):
    print("=" * 60)
    print("  DevQuest — 提取引擎评测报告")
    print("=" * 60)
    print()

    print("## 数据概览")
    print(f"   样本数: {total_samples}")
    print(f"   预期问题数: {metrics['total_expected']}")
    print(f"   实际提取数: {metrics['total_extracted']}")
    print(f"   成功匹配数: {metrics['matched']}")
    print(f"   漏提取: {len(matches['unmatched_expected'])} 个")
    print(f"   误提取: {len(matches['unmatched_extracted'])} 个")
    print()

    print("## 核心指标")
    print(f"   召回率 (Recall):    {metrics['recall']:.0%}  ({metrics['matched']}/{metrics['total_expected']} 个预期问题被找到)")
    print(f"   精确率 (Precision):  {metrics['precision']:.0%}  ({metrics['matched']}/{metrics['total_extracted']} 个提取结果有效)")
    print(f"   F1 分数:            {metrics['f1']:.0%}")
    print(f"   类型准确率:          {metrics['type_accuracy']:.0%}  (匹配到的问题中类型分类正确)")
    print(f"   数量偏差:            {metrics['count_deviation']:.0%}  (越接近 0 越好)")
    print()

    # 详细匹配情况
    print("## 匹配详情")
    for pair in matches["pairs"]:
        exp = expected_list[pair["expected_index"]]
        ext = extracted[pair["extracted_index"]]
        type_match = ext.get("problem_type") == exp.get("problem_type")
        print(f"  [PASS] 匹配 (分数 {pair['score']})")
        print(f"     预期: [{exp['problem_type']}] {' / '.join(exp['title_keywords'][:3])}...")
        print(f"     提取: [{ext.get('problem_type', '?')}] {ext.get('title', '?')}")
        print(f"     类型: {'[PASS]' if type_match else '[FAIL]'}", end="")
        if not type_match:
            print(f" (预期 {exp['problem_type']}, 实际 {ext.get('problem_type', '?')})", end="")
        print()
        print()

    if matches["unmatched_expected"]:
        print("## 漏提取（预期有但没找到）")
        for idx in matches["unmatched_expected"]:
            exp = expected_list[idx]
            print(f"  [FAIL] [{exp['problem_type']}] {' / '.join(exp['title_keywords'])}")
        print()

    if matches["unmatched_extracted"]:
        print("## 误提取（提取了但不匹配预期）")
        for idx in matches["unmatched_extracted"]:
            ext = extracted[idx]
            print(f"  [WARN] [{ext.get('problem_type', '?')}] {ext.get('title', '?')}")
        print()

    # 简历可用的一句话
    print("─" * 60)
    print(f"  [INFO] 简历数据: 提取召回率 {metrics['recall']:.0%}，"
          f"精确率 {metrics['precision']:.0%}，"
          f"类型准确率 {metrics['type_accuracy']:.0%}")
    print("─" * 60)


# ── 入口 ───────────────────────────────────────────────────────

def main():
    expected_data = load_expected()

    if not expected_data:
        print("未找到评测数据 (expected.json)")
        return

    total_samples = len(expected_data)
    all_metrics = []

    for filename, spec in expected_data.items():
        print(f"评测样本: {filename}")
        conversation = load_conversation(filename)
        project = spec["project"]

        # 运行提取
        print("  正在调用 DeepSeek 提取问题...")
        extracted = extractor.extract_problems(
            conversation_text=conversation,
            project_name=project,
        )
        print(f"  提取完成: {len(extracted)} 个问题")
        print()

        # 匹配
        matches = _match_problems(extracted, spec["expected_problems"])

        # 计算指标
        metrics = calculate_metrics(matches, spec["expected_problems"], extracted)
        all_metrics.append(metrics)

        # 输出报告
        print_report(metrics, matches, spec["expected_problems"], extracted, total_samples)

    # ── 汇总 ──────────────────────────────────────────────────
    if len(all_metrics) > 1:
        avg_recall = sum(m["recall"] for m in all_metrics) / len(all_metrics)
        avg_precision = sum(m["precision"] for m in all_metrics) / len(all_metrics)
        avg_f1 = sum(m["f1"] for m in all_metrics) / len(all_metrics)
        avg_type_acc = sum(m["type_accuracy"] for m in all_metrics) / len(all_metrics)
        total_expected = sum(m["total_expected"] for m in all_metrics)
        total_extracted = sum(m["total_extracted"] for m in all_metrics)
        total_matched = sum(m["matched"] for m in all_metrics)

        print("=" * 60)
        print("  汇总")
        print("=" * 60)
        print(f"  样本数: {total_samples}")
        print(f"  预期问题总数: {total_expected}")
        print(f"  实际提取总数: {total_extracted}")
        print(f"  成功匹配总数: {total_matched}")
        print(f"  平均召回率: {avg_recall:.0%}")
        print(f"  平均精确率: {avg_precision:.0%}")
        print(f"  平均 F1: {avg_f1:.0%}")
        print(f"  平均类型准确率: {avg_type_acc:.0%}")
        print()
        print(f"  [INFO] 简历数据: {total_samples} 样本 {total_expected} 预期问题, "
              f"召回率 {avg_recall:.0%}, 精确率 {avg_precision:.0%}, "
              f"类型准确率 {avg_type_acc:.0%}")


if __name__ == "__main__":
    main()
