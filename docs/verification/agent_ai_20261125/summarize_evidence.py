"""Recompute report metrics from local evidence; never call models or devices."""
import hashlib
import importlib.metadata
import json
import math
import platform
import re
from pathlib import Path
import statistics
import sys
import xml.etree.ElementTree as ET
from collections import Counter

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent


def read_json(path):
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def percentile95(values):
    values = sorted(values)
    return values[math.ceil(len(values) * .95) - 1]


def summarize():
    rows = [json.loads(line) for line in
            (OUT / "historical_nemotron_corpus.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    live = []
    for thinking in (False, True):
        sample = [r for r in rows if r["thinking"] == thinking and r["group"] != "example"]
        assert len(sample) == 100
        times = [r["elapsed_ms"] for r in sample if r["success"]]
        usage = [r["usage"] for r in sample if r["usage"] is not None]
        live.append({
            "thinking": thinking, "attempts": len(sample),
            "groups": {g: {"n": sum(r["group"] == g for r in sample),
                              "correct": sum(r["correct"] for r in sample if r["group"] == g)}
                       for g in ("normal", "compound", "ambiguous", "denied")},
            "correct": sum(r["correct"] for r in sample),
            "json_ok": sum(r["json_ok"] for r in sample),
            "schema_ok": sum(r["schema_ok"] for r in sample),
            "completed_responses": len(times),
            "http_status_counts": dict(Counter(str(r["http_status"]) for r in sample)),
            "api_errors": dict(Counter(r["error_type"] for r in sample if r["error_type"])),
            "local_rejected": sum(r["local_rejection"] is not None for r in sample),
            "p50_ms": statistics.median(times), "p95_ms": percentile95(times),
            "usage_rows": len(usage),
            "tokens": {k: sum(u.get(k) or 0 for u in usage) for k in
                       ("prompt_tokens", "completion_tokens", "total_tokens", "reasoning_tokens")},
        })
    retrieval = read_json("docs/verification/rag_v2/report.json")
    rag = []
    for report in retrieval:
        eligible = [r for r in report["results"] if r["kind"] == "retrieval"]
        assert len(eligible) == report["retrieval_count"] == 48
        for key, row_key in (("recall_at_5", "recall5"), ("recall_at_20", "recall20"),
                             ("mrr_at_10", "reciprocal_rank"), ("ndcg_at_5", "ndcg5")):
            assert math.isclose(report[key], statistics.mean(r[row_key] for r in eligible))
        rag.append({**{k: v for k, v in report.items() if k != "results"},
                    "case_kinds": dict(Counter(r["kind"] for r in report["results"]))})
    reranker = read_json("docs/verification/rag_v2/reranker-benchmark.json")
    rerank = []
    for candidates in (10, 20, 40):
        sample = [r for r in reranker["runs"] if r["candidates"] == candidates]
        rerank.append({"candidates": candidates, "n": len(sample),
                       "p50_ms": statistics.median(r["ms"] for r in sample),
                       "p95_ms": percentile95([r["ms"] for r in sample]),
                       "max_rss_bytes": max(r["rss_bytes"] for r in sample)})
    tests = ET.parse(OUT / "pytest.xml").getroot()
    suite = tests.find("testsuite")
    subtests = re.search(r"(\d+) subtests passed", (OUT / "pytest.log").read_text(encoding="utf-8"))
    failures = []
    groups = {}
    for case in tests.iter("testcase"):
        name = case.attrib.get("classname", "")
        group = groups.setdefault(name, Counter())
        state = next((s for s in ("failure", "error", "skipped") if case.find(s) is not None), "passed")
        group[state] += 1
        if state in ("failure", "error", "skipped"):
            failures.append({"test": name + "." + case.attrib["name"], "status": state,
                             "message": case.find(state).attrib.get("message", "")})
    sources = [
        "docs/verification/agent_ai_20261125/historical_nemotron_corpus.jsonl",
        "docs/verification/C8_NEMOTRON_API_VALIDATION.md",
        "docs/agent-evaluation/baseline.json", "docs/agent-evaluation/candidate.json",
        "docs/verification/rag_v2/report.json", "docs/verification/rag_v2/reranker-benchmark.json",
        "docs/verification/rag_v2/control-load.json", "docs/verification/rag_v2/nemotron-result.json",
        "python_controller/llm/agent_graph.py", "python_controller/llm/agent_intent.py",
        "python_controller/llm/agent_semantics.py", "python_controller/llm/unified_agent.py",
        "python_controller/llm/nvidia_client.py", "python_controller/planning/schema.py",
        "python_controller/knowledge/evaluate.py", "python_controller/knowledge/vector.py",
        "docs/verification/agent_ai_20261125/pytest.xml",
        "docs/verification/agent_ai_20261125/pytest.log",
        "docs/verification/agent_ai_20261125/current_intent_fixture.json",
        "scripts/evaluate_agent_intents.py",
    ]
    result = {
        "report_date_kst": "2026-11-25",
        "scope": "Current working tree, including pre-existing uncommitted changes. Live API and RAG benchmarks are historical evidence, not rerun.",
        "environment": {"python": sys.version, "platform": platform.platform(),
                        "packages": {p: importlib.metadata.version(p) for p in
                                     ("pytest", "openai", "pydantic", "langgraph", "python-dotenv",
                                      "fastembed", "sentence-transformers", "pypdf")}},
        "current_pytest": {"junit_suite_including_subtests": suite.attrib,
                           "primary_test_results": dict(sum(groups.values(), Counter())),
                           "primary_tests": sum(sum(group.values()) for group in groups.values()),
                           "subtests_passed": int(subtests.group(1)) if subtests else None,
                           "by_class": groups, "exceptions": failures},
        "current_intent_fixture": read_json("docs/verification/agent_ai_20261125/current_intent_fixture.json"),
        "historical_live_api_recomputed": live,
        "historical_intent_fixture": {label: {k: v for k, v in read_json(path).items() if k != "cases"}
                                      for label, path in (("baseline", "docs/agent-evaluation/baseline.json"),
                                                          ("candidate", "docs/agent-evaluation/candidate.json"))},
        "historical_rag_recomputed": rag, "historical_reranker_recomputed": rerank,
        "historical_control_load": read_json("docs/verification/rag_v2/control-load.json"),
        "source_sha256": {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in sources},
    }
    (OUT / "evidence_summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k not in ("source_sha256", "historical_intent_fixture")},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    summarize()
