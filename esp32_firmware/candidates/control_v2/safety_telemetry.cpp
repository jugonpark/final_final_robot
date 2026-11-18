#include "safety_telemetry.h"

#include <math.h>

#include "kinematics_motion.h"
#include "motor_encoder.h"
#include "robot_config.h"
#include "control_v2_math.h"

using namespace robot_config;
using namespace motion_control;

SafetyState safetyState{};

namespace {

bool isRecoverableFault(FaultCode fault) {
    return fault == FaultCode::WIFI_LOSS || fault == FaultCode::CMD_TIMEOUT ||
           fault == FaultCode::BAD_PACKET || fault == FaultCode::HEARTBEAT_TIMEOUT ||
           fault == FaultCode::VELOCITY_EXPIRED || fault == FaultCode::GOAL_TIMEOUT ||
           ((fault == FaultCode::ENCODER_JUMP || fault == FaultCode::ENCODER_INVALID) &&
            encoderDiagnostics.policy == EncoderPolicy::FILTERED_TEST);
}

const char *motionStateName(MotionState state) {
    switch (state) {
        case MotionState::STOPPED: return "STOPPED";
        case MotionState::VELOCITY: return "VELOCITY";
        case MotionState::DISTANCE_ACTIVE: return "DISTANCE_ACTIVE";
        case MotionState::DISTANCE_BRAKING: return "DISTANCE_BRAKING";
        case MotionState::GOAL_REACHED: return "GOAL_REACHED";
        case MotionState::FAULT: return "FAULT";
    }
    return "FAULT";
}

bool isMovingState(MotionState state) {
    return state == MotionState::VELOCITY ||
           state == MotionState::DISTANCE_ACTIVE ||
           state == MotionState::DISTANCE_BRAKING;
}

void addFloatArray(JsonObject root, const char *name,
                   const float values[MOTOR_COUNT]) {
    JsonArray array = root[name].to<JsonArray>();
    for (uint8_t i = 0; i < MOTOR_COUNT; ++i) {
        array.add(values[i]);
    }
}

void addIntArray(JsonObject root, const char *name,
                 const int values[MOTOR_COUNT]) {
    JsonArray array = root[name].to<JsonArray>();
    for (uint8_t i = 0; i < MOTOR_COUNT; ++i) {
        array.add(values[i]);
    }
}

void addCountArray(JsonObject root, const char *name,
                   const int32_t values[MOTOR_COUNT]) {
    JsonArray array = root[name].to<JsonArray>();
    for (uint8_t i = 0; i < MOTOR_COUNT; ++i) {
        array.add(values[i]);
    }
}

}  // namespace

const char *faultCodeName(FaultCode fault) {
    switch (fault) {
        case FaultCode::NONE: return "NONE";
        case FaultCode::WIFI_LOSS: return "WIFI_LOSS";
        case FaultCode::CMD_TIMEOUT: return "CMD_TIMEOUT";
        case FaultCode::BAD_PACKET: return "BAD_PACKET";
        case FaultCode::SPEED_LIMIT: return "SPEED_LIMIT";
        case FaultCode::ENCODER_JUMP: return "ENCODER_JUMP";
        case FaultCode::ENCODER_INVALID: return "ENCODER_INVALID";
        case FaultCode::ODOMETRY_INVALID: return "ODOMETRY_INVALID";
        case FaultCode::PATH_DEVIATION: return "PATH_DEVIATION";
        case FaultCode::CONTROL_TIMING: return "CONTROL_TIMING";
        case FaultCode::MOTOR_STALL: return "MOTOR_STALL";
        case FaultCode::HEARTBEAT_TIMEOUT: return "HEARTBEAT_TIMEOUT";
        case FaultCode::VELOCITY_EXPIRED: return "VELOCITY_EXPIRED";
        case FaultCode::GOAL_TIMEOUT: return "GOAL_TIMEOUT";
    }
    return "CONTROL_TIMING";
}

const char *controllerModeName(ControllerMode mode) {
    return mode == ControllerMode::NETWORK ? "NETWORK" : "MANUAL_TEST";
}

void immediateStopNow(FaultCode cause, uint32_t nowMs,
                      CommandMailbox &mailbox,
                      CommandAcceptanceState &acceptance) {
    // The physical output write is deliberately the first safety action.
    stopAllMotorsImmediate();
    safetyState.stopAppliedMs = millis();
    retireExecution(acceptance);
    clearMotionMailbox(mailbox);
    cancelMotionImmediate(cause == FaultCode::NONE ? MotionState::STOPPED
                                                   : MotionState::FAULT);
    acceptance.moving = false;
    if (cause != FaultCode::NONE) {
        safetyState.fault = cause;
        safetyState.faultAtMs = nowMs;
    }
}

void raiseFault(FaultCode cause, uint32_t nowMs,
                CommandMailbox &mailbox,
                CommandAcceptanceState &acceptance) {
    const bool preserveLatched =
        safetyState.faultLatched && safetyState.fault != FaultCode::NONE;
    const FaultCode preservedCause = preserveLatched ? safetyState.fault : cause;
    const uint32_t preservedAtMs = preserveLatched ? safetyState.faultAtMs : nowMs;
    if (preserveLatched) {
        immediateStopNow(preservedCause, preservedAtMs, mailbox, acceptance);
        return;
    }
    safetyState.faultLatched = !isRecoverableFault(cause);
    safetyState.lastFault = cause;
    safetyState.lastFaultAtMs = nowMs;
    safetyState.resetAuthorized = false;
    immediateStopNow(cause, nowMs, mailbox, acceptance);
}

bool tryResetFault(bool conditionClear, bool explicitReset,
                   bool handshakeComplete) {
    const bool encoderFault = safetyState.fault == FaultCode::ENCODER_JUMP ||
                              safetyState.fault == FaultCode::ENCODER_INVALID;
    if (encoderFault && !encoderRecoveryReady()) return false;
    if (safetyState.fault == FaultCode::NONE || !conditionClear) {
        return false;
    }
    if (explicitReset && safetyState.faultLatched) {
        safetyState.resetAuthorized = true;
        return false;
    }
    if (!handshakeComplete) {
        return false;
    }
    if (safetyState.faultLatched && !safetyState.resetAuthorized) {
        return false;
    }
    safetyState.fault = FaultCode::NONE;
    safetyState.faultLatched = false;
    safetyState.resetAuthorized = false;
    safetyState.faultAtMs = 0;
    resetMotionAfterFault();
    return true;
}

FaultCode updateWatchdogs(uint32_t nowMs, bool wifiConnected, bool moving,
                          CommandMailbox &mailbox,
                          CommandAcceptanceState &acceptance) {
    if (!moving) {
        return FaultCode::NONE;
    }
    if (safetyState.mode == ControllerMode::NETWORK) {
        if (!wifiConnected) {
            raiseFault(FaultCode::WIFI_LOSS, nowMs, mailbox, acceptance);
            return FaultCode::WIFI_LOSS;
        }
        if (acceptance.protocolVersion == 2) {
            FaultCode cause = FaultCode::NONE;
            if (!acceptance.tokenActive || !acceptance.controllerAlive || !acceptance.motionPermit ||
                !acceptance.hasHeartbeat || lifetimeExpired(nowMs, acceptance.lastHeartbeatAtMs, HEARTBEAT_TIMEOUT_MS))
                cause = FaultCode::HEARTBEAT_TIMEOUT;
            else if (acceptance.activeCommand.type == CommandType::VELOCITY &&
                (!acceptance.hasVelocity || lifetimeExpired(nowMs, acceptance.lastVelocityAtMs, VELOCITY_TTL_MS)))
                cause = FaultCode::VELOCITY_EXPIRED;
            else if (acceptance.activeCommand.type == CommandType::MOVE &&
                lifetimeExpired(nowMs, acceptance.goalStartedAtMs, acceptance.goalTimeoutMs))
                cause = FaultCode::GOAL_TIMEOUT;
            if (cause != FaultCode::NONE) raiseFault(cause, nowMs, mailbox, acceptance);
            return cause;
        }
        if (!acceptance.hasSession ||
            uint32_t(nowMs - acceptance.lastAcceptedAtMs) > COMMAND_WATCHDOG_MS) {
            raiseFault(FaultCode::CMD_TIMEOUT, nowMs, mailbox, acceptance);
            return FaultCode::CMD_TIMEOUT;
        }
    } else if (uint32_t(nowMs - safetyState.lastManualCommandMs) >
               MANUAL_WATCHDOG_MS) {
        raiseFault(FaultCode::CMD_TIMEOUT, nowMs, mailbox, acceptance);
        return FaultCode::CMD_TIMEOUT;
    }
    return FaultCode::NONE;
}

void noteManualCommand(uint32_t nowMs) {
    safetyState.lastManualCommandMs = nowMs;
}

void buildTelemetry(JsonDocument &document, uint32_t nowMs, uint32_t bootId,
                    const CommandAcceptanceState &acceptance,
                    int32_t wifiRssi, uint32_t controlOverruns) {
    document.clear();
    JsonObject root = document.to<JsonObject>();
    root["type"] = "telemetry";
    root["boot_id"] = bootId;
    root["session_id"] = acceptance.hasSession ? acceptance.sessionId : 0;
    root["last_seq"] = acceptance.hasSequence ? acceptance.lastSequence : 0;
    root["uptime_ms"] = nowMs;
    root["mode"] = controllerModeName(safetyState.mode);
    root["state"] = motionStateName(motionState.state);
    root["fault"] = faultCodeName(safetyState.fault);
    root["cmd_vx"] = motionState.desiredVxCmS;
    root["cmd_vy"] = motionState.desiredVyCmS;
    root["cmd_w"] = motionState.desiredWRadS;
    if (acceptance.hasSession) {
        root["command_age_ms"] = uint32_t(nowMs - acceptance.lastAcceptedAtMs);
    } else {
        root["command_age_ms"] = nullptr;
    }

    addFloatArray(root, "wheel_target", targetWheelSpeed);
    addFloatArray(root, "wheel_speed", motorEncoderState.measuredWheelSpeed);
    addIntArray(root, "wheel_pwm", motorEncoderState.currentPwm);
    addCountArray(root, "encoder_count", motorEncoderState.count);

    if (motionState.goalContentValid) {
        root["motion_id"] = motionState.motionId;
        root["goal_target_cm"] = motionState.goalTargetCm;
        root["goal_progress_cm"] = motionState.goalProgressCm;
        root["remaining_cm"] = motionState.remainingCm;
        root["lateral_error_cm"] = motionState.lateralErrorCm;
        root["overshoot_cm"] = motionState.overshootCm;
    } else {
        root["motion_id"] = nullptr;
        root["goal_target_cm"] = nullptr;
        root["goal_progress_cm"] = nullptr;
        root["remaining_cm"] = nullptr;
        root["lateral_error_cm"] = nullptr;
        root["overshoot_cm"] = nullptr;
    }
    root["odom_dx_cm"] = motionState.odomDxCm;
    root["odom_dy_cm"] = motionState.odomDyCm;
    root["odom_dtheta_rad"] = motionState.odomDthetaRad;
    root["wifi_rssi"] = wifiRssi;
    root["control_overruns"] = controlOverruns;

    root["protocol_version"] = 2;
    root["heartbeat_timeout_ms"] = HEARTBEAT_TIMEOUT_MS;
    root["velocity_ttl_ms"] = VELOCITY_TTL_MS;
    root["heartbeat_period_ms"] = HEARTBEAT_PERIOD_MS;
    if (acceptance.hasHeartbeat) root["heartbeat_age_ms"] = uint32_t(nowMs - acceptance.lastHeartbeatAtMs);
    else root["heartbeat_age_ms"] = nullptr;
    if (acceptance.hasVelocity) root["velocity_age_ms"] = uint32_t(nowMs - acceptance.lastVelocityAtMs);
    else root["velocity_age_ms"] = nullptr;
    root["controller_alive"] = acceptance.controllerAlive;
    root["motion_permit"] = acceptance.motionPermit;
    root["execution_token"] = acceptance.executionToken;
    root["encoder_policy"] = encoderDiagnostics.policy == EncoderPolicy::STRICT ? "STRICT" : "FILTERED_TEST";
    root["encoder_warning"] = encoderDiagnostics.warning;
    root["encoder_good_samples"] = encoderDiagnostics.goodSamples;
    root["encoder_bad_samples"] = encoderDiagnostics.badSamples;
    root["encoder_recent_bad"] = encoderDiagnostics.recentBad;
    root["fault_latched"] = safetyState.faultLatched;
    root["stop_applied_ms"] = safetyState.stopAppliedMs;
    root["last_fault"] = faultCodeName(safetyState.lastFault);
    root["last_fault_at_ms"] = safetyState.lastFaultAtMs;
    const EncoderSample &latest = encoderDiagnostics.lastInvalid;
    root["fault_sample_ms"] = encoderDiagnostics.lastInvalidAtMs;
    root["fault_motor_mask"] = latest.motorMask;
    root["fault_dt_us"] = latest.dtUs;
    root["fault_delta_limit"] = encoderDiagnostics.faultDeltaLimit;
    JsonArray rawDelta = root["fault_delta_count"].to<JsonArray>();
    for (uint8_t i = 0; i < MOTOR_COUNT; ++i) rawDelta.add(latest.delta[i]);
    addCountArray(root, "fault_previous_count", latest.previous);
    addCountArray(root, "fault_current_count", latest.current);
    addIntArray(root, "fault_pwm_before", latest.pwm);
    if (acceptance.tokenActive && acceptance.activeCommand.type == CommandType::MOVE)
        root["goal_elapsed_ms"] = uint32_t(nowMs - acceptance.goalStartedAtMs);
    else root["goal_elapsed_ms"] = nullptr;

    // Migration aliases retained for the existing Windows and Pi readers.
    root["seq"] = acceptance.hasSequence ? acceptance.lastSequence : -1;
    root["status"] = isMovingState(motionState.state) ? "RUN" : "STOP";
    addCountArray(root, "counts", motorEncoderState.count);
    float rpm[MOTOR_COUNT];
    for (uint8_t i = 0; i < MOTOR_COUNT; ++i) {
        rpm[i] = motorEncoderState.measuredWheelSpeed[i] * 60.0f /
                 (2.0f * MATH_PI * WHEEL_RADIUS_CM);
    }
    addFloatArray(root, "rpm", rpm);
    addFloatArray(root, "target_speed", targetWheelSpeed);
    addIntArray(root, "pwm", motorEncoderState.currentPwm);
    root["goal_active"] = motionState.state == MotionState::DISTANCE_ACTIVE ||
                          motionState.state == MotionState::DISTANCE_BRAKING;
    root["goal_reached"] = motionState.state == MotionState::GOAL_REACHED;
    if (motionState.goalContentValid) {
        root["target_distance_cm"] = motionState.goalTargetCm;
    } else {
        root["target_distance_cm"] = nullptr;
    }
    root["action_dtheta_rad"] = motionState.odomDthetaRad;
}

bool sendTelemetry(WiFiUDP &udp, const IPAddress &controllerIp,
                   uint16_t controllerPort, JsonDocument &document) {
    char buffer[2048];
    if (measureJson(document) >= sizeof(buffer)) return false;
    const size_t length = serializeJson(document, buffer, sizeof(buffer));
    if (length == 0 || length >= sizeof(buffer) ||
        !udp.beginPacket(controllerIp, controllerPort)) {
        return false;
    }
    const size_t written = udp.write(
        reinterpret_cast<const uint8_t *>(buffer), length);
    return written == length && udp.endPacket() == 1;
}
