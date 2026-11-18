"""Compare Nemotron settings on the existing Korean corpus without robot I/O."""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import json
import math
import os
from pathlib import Path
import statistics
import sys
import threading
import time

from dotenv import dotenv_values

from python_controller.llm.nvidia_client import DEFAULT_BASE_URL, NEMOTRON_ULTRA_MODEL, NvidiaError, NvidiaPlanner
from python_controller.planning.schema import ModelPlan
from python_controller.planning.validator import PlanError, parse_operator_command, strict_json_object, validate_plan

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
from korean_command_cases import AMBIGUOUS, CONNECTORS, DENIED, DISTANCES, ENDINGS


EXAMPLE = ("example", "앞으로 15cm 이동하고 오른쪽으로 10cm 가", (("forward", 15), ("right", 10)))


def cases():
    for group, forms, tail in (("normal", ENDINGS, ()), ("compound", CONNECTORS, (("right", 10),))):
        for form in forms:
            for text, direction, distance in DISTANCES:
                yield group, form.format(text), ((direction, distance), *tail)
    for group, denied in (("ambiguous", AMBIGUOUS), ("denied", DENIED)):
        for text in denied:
            yield group, text, ()


def evaluate(planner, case):
    group, command, expected = case
    try:
        requested = parse_operator_command(command)
        local_reason = None
    except PlanError as exc:
        requested, local_reason = (), str(exc)
    if tuple((action.direction, action.distance_cm) for action in requested) != expected:
        raise RuntimeError("LOCAL_POLICY_MISMATCH_WITH_FIXTURE")
    row = planner.probe(command)
    text = row.pop("text")
    row.update(group=group, command=command, thinking=planner.enable_thinking, local_rejection=local_reason,
               json_ok=False, schema_ok=False, correct=False, validation_error=None)
    if text is None:
        return row
    try:
        data = strict_json_object(text, parse_float=Decimal)
        row["json_ok"] = True
        ModelPlan.model_validate(data)
        row["schema_ok"] = True
        plan = validate_plan(text, requested)
        row["correct"] = plan.needs_clarification if not expected else not plan.needs_clarification
        if expected and not row["correct"]:
            row["validation_error"] = "MODEL_REQUESTED_CLARIFICATION"
        if group == "example" and row["correct"]:
            row["final_json"] = text
    except PlanError as exc:
        row["validation_error"] = str(exc)
    except (ValueError, ArithmeticError, RecursionError):
        row["validation_error"] = "INVALID_SCHEMA" if row["json_ok"] else "INVALID_JSON"
    return row


def summarize(rows, thinking):
    sample = [row for row in rows if row["thinking"] == thinking and row["group"] != "example"]
    counts = {}
    for group in ("normal", "compound", "ambiguous", "denied"):
        group_rows = [row for row in sample if row["group"] == group]
        counts[group] = {"correct": sum(row["correct"] for row in group_rows), "n": len(group_rows)}
    successful = [row for row in sample if row["success"]]
    times = sorted(row["elapsed_ms"] for row in successful)
    usage = [row["usage"] for row in sample if row["usage"] is not None]
    return dict(
        thinking=thinking, n=len(sample), counts=counts, json_ok=sum(row["json_ok"] for row in sample),
        schema_ok=sum(row["schema_ok"] for row in sample), completed_responses=len(successful),
        p50_ms=statistics.median(times) if times else None,
        p95_ms=times[math.ceil(.95 * len(times)) - 1] if times else None,
        api_errors=dict(Counter(row["error_type"] for row in sample if row["error_type"])),
        validation_errors=dict(Counter(row["validation_error"] for row in sample if row["validation_error"])),
        responses_with_reasoning=sum(bool(row["reasoning_present"]) for row in sample),
        usage_rows=len(usage),
        tokens={field: sum(item.get(field) or 0 for item in usage)
                for field in ("prompt_tokens", "completion_tokens", "total_tokens", "reasoning_tokens")},
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="New JSONL file for the 200 measured API responses")
    args = parser.parse_args()
    config = {**{key: value for key, value in dotenv_values(".env", interpolate=False).items()
                 if value is not None}, **os.environ}
    try:
        planners = [NvidiaPlanner(api_key=config.get("NVIDIA_API_KEY"),
                                 base_url=config.get("NVIDIA_BASE_URL", DEFAULT_BASE_URL),
                                 model=NEMOTRON_ULTRA_MODEL, enable_thinking=thinking)
                    for thinking in (False, True)]
    except NvidiaError as exc:
        parser.exit(1, f"{exc}\n")

    pace_lock = threading.Lock()
    next_start = time.monotonic()

    def run(job):
        nonlocal next_start
        planner, case = job
        with pace_lock:
            time.sleep(max(0, next_start - time.monotonic()))
            next_start = time.monotonic() + 1.65
        row = evaluate(planner, case)
        if row["http_status"] == 429:
            with pace_lock:
                next_start = max(next_start, time.monotonic() + 30)
        return row

    corpus = list(cases())
    if len(corpus) != 100:
        raise RuntimeError("UNEXPECTED_CORPUS_SIZE")
    rows = []
    with args.output.open("x", encoding="utf-8", buffering=1) as output:
        for planner in planners:
            example = run((planner, EXAMPLE))
            output.write(json.dumps(example, ensure_ascii=False) + "\n")
            print("Example", planner.enable_thinking, example["http_status"], example["correct"], flush=True)
        with ThreadPoolExecutor(max_workers=5) as pool:
            jobs = ((planner, case) for case in corpus for planner in planners)
            for index, row in enumerate(pool.map(run, jobs), 1):
                rows.append(row)
                output.write(json.dumps(row, ensure_ascii=False) + "\n")
                if index % 20 == 0:
                    print(f"Corpus {index}/200", flush=True)
    for planner in planners:
        print(json.dumps(summarize(rows, planner.enable_thinking)), flush=True)


if __name__ == "__main__":
    main()
