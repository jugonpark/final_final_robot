"""Probe configured NVIDIA chat models without loading the robot controller."""
import argparse
import json
import os

from dotenv import dotenv_values

from python_controller.llm.nvidia_client import DEFAULT_BASE_URL, NvidiaError, NvidiaPlanner


def main(argv=None):
    parser = argparse.ArgumentParser(description="NVIDIA 모델별 Chat Completions 연결 진단")
    parser.add_argument("models", nargs="*", help="모델 ID 목록 (생략 시 NVIDIA_MODEL)")
    args = parser.parse_args(argv)
    config = {**{key: value for key, value in dotenv_values(".env", interpolate=False).items()
                 if value is not None}, **os.environ}
    status = 0
    for model in args.models or [config.get("NVIDIA_MODEL")]:
        try:
            planner = NvidiaPlanner(api_key=config.get("NVIDIA_API_KEY"),
                                    base_url=config.get("NVIDIA_BASE_URL", DEFAULT_BASE_URL), model=model)
            result = planner.probe()
        except NvidiaError as exc:
            result = {"success": False, "error_type": str(exc)}
        print(json.dumps(result, ensure_ascii=True))
        if not result["success"]:
            status = 1
    return status


if __name__ == "__main__":
    raise SystemExit(main())
