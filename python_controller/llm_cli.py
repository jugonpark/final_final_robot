"""Prototype A+B: preview a validated plan or execute it with an operator present."""
import argparse
import os
from pathlib import Path
import sys

from dotenv import dotenv_values

from python_controller.execution.control_lease import ESP32_COMMAND_PORT, ESP32_TELEMETRY_PORT
from python_controller.execution.motion_executor import HEARTBEAT_S, LLMMotionExecutor, MotionError
from python_controller.execution.run_log import RunLog
from python_controller.llm.nvidia_client import DEFAULT_BASE_URL, NvidiaError, NvidiaPlanner
from python_controller.planning.schema import Move, Stop
from python_controller.planning.validator import POLICY_VERSION, PlanError, ValidatedPlan, parse_operator_command, validate_plan


DIRECTIONS_KO = {"forward": "앞으로", "backward": "뒤로", "left": "왼쪽으로", "right": "오른쪽으로"}


def _preview(plan):
    for index, action in enumerate(plan.actions, 1):
        label = (f"{DIRECTIONS_KO[action.direction]} {action.distance_cm:g} cm"
                 if isinstance(action, Move) else "정지")
        print(f"{index}. {label}")


def load_environment():
    return {**{key: value for key, value in dotenv_values(".env", interpolate=False).items()
               if value is not None}, **os.environ}


def prepare_plan(command, config, emit=None):
    requested = parse_operator_command(command)
    stop_only = len(requested) == 1 and isinstance(requested[0], Stop)
    if stop_only:
        plan = ValidatedPlan(requested)
        if emit:
            emit("LOCAL_STOP_PLAN")
    else:
        model = config.get("NVIDIA_MODEL")
        planner = NvidiaPlanner(api_key=config.get("NVIDIA_API_KEY"),
                                base_url=config.get("NVIDIA_BASE_URL", DEFAULT_BASE_URL), model=model)
        if emit:
            emit("PLANNING", model=model)
        plan = validate_plan(planner.plan(command), requested)
        if plan.needs_clarification:
            raise PlanError("MODEL_REQUESTED_CLARIFICATION")
    return plan


def motion_timeout(config):
    try:
        if (int(config.get("ESP32_COMMAND_PORT", ESP32_COMMAND_PORT)) != ESP32_COMMAND_PORT
                or int(config.get("ESP32_TELEMETRY_PORT", ESP32_TELEMETRY_PORT)) != ESP32_TELEMETRY_PORT
                or float(config.get("LLM_LINEAR_SPEED_CM_S", 5)) != 5
                or float(config.get("LLM_HEARTBEAT_S", HEARTBEAT_S)) != HEARTBEAT_S):
            raise ValueError
        timeout = float(config.get("LLM_TELEMETRY_TIMEOUT_S", .8))
        if not .4 <= timeout <= .8:
            raise ValueError
    except ValueError:
        raise PlanError("INVALID_ESP32_OR_TIMING_SETTING") from None
    return timeout


def _run(args, config, log):
    if config.get("LLM_DRY_RUN", "true").lower() not in ("true", "false"):
        raise PlanError("INVALID_LLM_DRY_RUN_SETTING")
    plan = prepare_plan(args.command, config, log.emit)
    log.emit("READY", actions=[action.model_dump(mode="json") for action in plan.actions])
    print("이동 계획 (로봇 몸체 기준)")
    _preview(plan)
    # An environment value cannot grant permission to move.
    if not args.execute:
        log.emit("DRY_RUN")
        print("DRY RUN · 이동 계획만 표시했습니다.")
        return 0

    timeout = motion_timeout(config)
    host = config.get("ESP32_IP", "")
    if not host:
        raise PlanError("INVALID_ESP32_OR_TIMING_SETTING")
    stop_only = len(plan.actions) == 1 and isinstance(plan.actions[0], Stop)

    if not stop_only:
        if not sys.stdin.isatty() or input("이 계획을 실행하려면 EXECUTE 입력 > ").strip() != "EXECUTE":
            log.emit("CANCELLED", reason="PLAN_APPROVAL_MISSING")
            print("실행이 취소되었습니다.")
            return 2
        log.emit("PLAN_APPROVED")
        print("다른 제어기를 종료하세요. 모터·엔코더 방향, 시험 공간 또는 바퀴 고정,"
              " ESP32 NETWORK 모드와 비상 전원 차단 수단을 확인하세요.")
        if input("실물 시험 준비를 확인했으면 READY 입력 > ").strip() != "READY":
            log.emit("CANCELLED", reason="HARDWARE_PREPARATION_NOT_CONFIRMED")
            print("실행이 취소되었습니다.")
            return 2
        print("Ctrl+C로 정지를 요청할 수 있습니다.", flush=True)
    log.emit("APPROVED", scope="STOP_ONLY" if stop_only else "MOTION", physical_distance_cm=None)
    executor = LLMMotionExecutor(host, log.emit, timeout=timeout)
    executor.run(plan, approved=True)
    print("ESP32 텔레메트리에서 정지를 확인했습니다.")
    return 0


def main(argv=None, *, log_dir=Path("logs/llm")):
    parser = argparse.ArgumentParser(description="D.I.G. NVIDIA 자연어 제어 (기본 dry-run)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="계획 표시 (기본값)")
    mode.add_argument("--execute", action="store_true", help="계획 검토 후 실제 거리 제어 시도")
    parser.add_argument("command", help="한국어 지시 한 문장")
    args = parser.parse_args(argv)
    try:
        config = load_environment()
        log = RunLog(log_dir)
        try:
            log.emit("RUN_STARTED", policy_version=POLICY_VERSION, mode="EXECUTE" if args.execute else "DRY_RUN")
            try:
                return _run(args, config, log)
            except MotionError as exc:
                log.emit("ABORTED", reason=str(exc))
                print(f"실행 중단 ({exc}). 정지 확인 결과는 로그를 확인하세요.")
                return 1
            except (NvidiaError, PlanError, ValueError) as exc:
                reason = str(exc) if isinstance(exc, (NvidiaError, PlanError)) else "INVALID_CONFIGURATION"
                log.emit("REJECTED", reason=reason)
                print(f"요청 거절 ({reason}). 설정 또는 방향·거리·단위를 확인하세요.")
                return 1
            except (EOFError, KeyboardInterrupt):
                log.emit("CANCELLED", reason="OPERATOR_CANCELLED")
                print("실행이 취소되었습니다.")
                return 2
        finally:
            log.close()
            print(f"로그 {log.path}")
    except OSError:
        print("실행 로그 또는 로컬 설정 파일에 접근하지 못했습니다.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
