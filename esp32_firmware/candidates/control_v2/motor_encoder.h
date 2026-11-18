#pragma once

#include <Arduino.h>
#include <stdint.h>

#include "robot_config.h"

struct MotorEncoderState {
    int32_t count[robot_config::MOTOR_COUNT];
    int32_t previousCount[robot_config::MOTOR_COUNT];
    int32_t deltaCount[robot_config::MOTOR_COUNT];
    float measuredWheelSpeed[robot_config::MOTOR_COUNT];
    int currentPwm[robot_config::MOTOR_COUNT];
    bool encoderValid[robot_config::MOTOR_COUNT];
    bool driverEnabled;
};

enum class EncoderPolicy : uint8_t { STRICT, FILTERED_TEST };
struct EncoderSample {
    uint32_t dtUs = 0;
    uint8_t motorMask = 0;
    int64_t delta[robot_config::MOTOR_COUNT]{};
    int32_t previous[robot_config::MOTOR_COUNT]{};
    int32_t current[robot_config::MOTOR_COUNT]{};
    int pwm[robot_config::MOTOR_COUNT]{};
    float target[robot_config::MOTOR_COUNT]{};
    float speed[robot_config::MOTOR_COUNT]{};
};
struct EncoderDiagnostics {
    EncoderPolicy policy = EncoderPolicy::FILTERED_TEST;
    bool warning = false;
    uint16_t goodSamples = 0;
    uint16_t badSamples = 0;
    uint16_t badWindow = 0;
    uint8_t recentBad = 0;
    int64_t faultDeltaLimit = 0;
    uint32_t lastInvalidAtMs = 0;
    EncoderSample lastInvalid{};
};
extern EncoderDiagnostics encoderDiagnostics;
extern MotorEncoderState motorEncoderState;
bool encoderRecoveryReady();
bool filteredEncoderHoldAllowed(bool slowVelocity);
void setEncoderPolicy(EncoderPolicy policy);

void initializeMotorOutputsSafe();
void enableMotorDriver();
void disableMotorDriver();
void writeMotorPwm(uint8_t motorIndex, int signedPwm);
void stopAllMotorsImmediate();
void attachEncoderInterrupts();
void snapshotEncoderCounts(int32_t snapshot[robot_config::MOTOR_COUNT]);
bool updateMeasuredWheelSpeed(float dtSec, uint32_t rawDtUs = 0);
void zeroEncoderReference();
