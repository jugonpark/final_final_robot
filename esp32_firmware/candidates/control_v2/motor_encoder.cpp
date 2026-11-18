#include "motor_encoder.h"

#include <math.h>
#include <string.h>

#include "odometry_math.h"
#include "control_v2_math.h"
#include "kinematics_motion.h"

using namespace robot_config;

MotorEncoderState motorEncoderState = {};
EncoderDiagnostics encoderDiagnostics{};

namespace {

volatile uint32_t encoderCounts[MOTOR_COUNT] = {0, 0, 0};

void setDirectionPinsLow(uint8_t motorIndex) {
    digitalWrite(MOTOR_IN1_PINS[motorIndex], LOW);
    digitalWrite(MOTOR_IN2_PINS[motorIndex], LOW);
}

}  // namespace

void IRAM_ATTR onEncoderA0() {
    int direction = digitalRead(ENCODER_B_PINS[0]) ? -1 : 1;
    encoderCounts[0] += ENCODER_REVERSED[0] ? -direction : direction;
}

void IRAM_ATTR onEncoderA1() {
    int direction = digitalRead(ENCODER_B_PINS[1]) ? -1 : 1;
    encoderCounts[1] += ENCODER_REVERSED[1] ? -direction : direction;
}

void IRAM_ATTR onEncoderA2() {
    int direction = digitalRead(ENCODER_B_PINS[2]) ? -1 : 1;
    encoderCounts[2] += ENCODER_REVERSED[2] ? -direction : direction;
}

void initializeMotorOutputsSafe() {
    pinMode(MOTOR_STBY_PIN, OUTPUT);
    digitalWrite(MOTOR_STBY_PIN, LOW);

    for (uint8_t i = 0; i < MOTOR_COUNT; ++i) {
        pinMode(MOTOR_IN1_PINS[i], OUTPUT);
        pinMode(MOTOR_IN2_PINS[i], OUTPUT);
        digitalWrite(MOTOR_IN1_PINS[i], LOW);
        digitalWrite(MOTOR_IN2_PINS[i], LOW);
        ledcAttach(MOTOR_PWM_PINS[i], PWM_FREQUENCY_HZ, PWM_RESOLUTION_BITS);
        ledcWrite(MOTOR_PWM_PINS[i], 0);
        motorEncoderState.currentPwm[i] = 0;
        motorEncoderState.measuredWheelSpeed[i] = 0.0f;
        motorEncoderState.encoderValid[i] = true;
    }
    motorEncoderState.driverEnabled = false;
}

void enableMotorDriver() {
    stopAllMotorsImmediate();
    digitalWrite(MOTOR_STBY_PIN, HIGH);
    motorEncoderState.driverEnabled = true;
}

void disableMotorDriver() {
    stopAllMotorsImmediate();
    digitalWrite(MOTOR_STBY_PIN, LOW);
    motorEncoderState.driverEnabled = false;
}

void writeMotorPwm(uint8_t motorIndex, int signedPwm) {
    if (motorIndex >= MOTOR_COUNT) {
        return;
    }

    int requested = constrain(signedPwm, -PWM_MAX, PWM_MAX);
    if (MOTOR_REVERSED[motorIndex]) {
        requested = -requested;
    }

    const int previous = motorEncoderState.currentPwm[motorIndex];
    const bool directionChanged =
        requested != 0 && previous != 0 && ((requested > 0) != (previous > 0));

    if (requested == 0) {
        ledcWrite(MOTOR_PWM_PINS[motorIndex], 0);
        setDirectionPinsLow(motorIndex);
        motorEncoderState.currentPwm[motorIndex] = 0;
        return;
    }

    if (directionChanged) {
        ledcWrite(MOTOR_PWM_PINS[motorIndex], 0);
        delayMicroseconds(DIRECTION_DEADTIME_US);
    }

    if (requested > 0) {
        digitalWrite(MOTOR_IN1_PINS[motorIndex], HIGH);
        digitalWrite(MOTOR_IN2_PINS[motorIndex], LOW);
    } else {
        digitalWrite(MOTOR_IN1_PINS[motorIndex], LOW);
        digitalWrite(MOTOR_IN2_PINS[motorIndex], HIGH);
    }
    ledcWrite(MOTOR_PWM_PINS[motorIndex], abs(requested));
    motorEncoderState.currentPwm[motorIndex] = requested;
}

void stopAllMotorsImmediate() {
    for (uint8_t i = 0; i < MOTOR_COUNT; ++i) {
        ledcWrite(MOTOR_PWM_PINS[i], 0);
        setDirectionPinsLow(i);
        motorEncoderState.currentPwm[i] = 0;
    }
}

void attachEncoderInterrupts() {
    for (uint8_t i = 0; i < MOTOR_COUNT; ++i) {
        pinMode(ENCODER_A_PINS[i], INPUT);
        pinMode(ENCODER_B_PINS[i], INPUT);
    }
    attachInterrupt(digitalPinToInterrupt(ENCODER_A_PINS[0]), onEncoderA0, RISING);
    attachInterrupt(digitalPinToInterrupt(ENCODER_A_PINS[1]), onEncoderA1, RISING);
    attachInterrupt(digitalPinToInterrupt(ENCODER_A_PINS[2]), onEncoderA2, RISING);
}

void snapshotEncoderCounts(int32_t snapshot[MOTOR_COUNT]) {
    noInterrupts();
    for (uint8_t i = 0; i < MOTOR_COUNT; ++i) {
        const uint32_t raw = encoderCounts[i];
        memcpy(&snapshot[i], &raw, sizeof(raw));
    }
    interrupts();
}

bool encoderRecoveryReady() {
    return encoderDiagnostics.goodSamples >= ENCODER_RECOVERY_GOOD_SAMPLES;
}

bool filteredEncoderHoldAllowed(bool slowVelocity) {
    return mayHoldEncoderTick(encoderDiagnostics.policy == EncoderPolicy::FILTERED_TEST,
                              slowVelocity, encoderDiagnostics.recentBad);
}

void setEncoderPolicy(EncoderPolicy policy) {
    encoderDiagnostics.policy = policy;
    encoderDiagnostics.warning = false;
    encoderDiagnostics.badSamples = 0;
    encoderDiagnostics.goodSamples = 0;
    encoderDiagnostics.badWindow = 0;
    encoderDiagnostics.recentBad = 0;
    // Keep the last rejected sample across policy changes.
}

bool updateMeasuredWheelSpeed(float dtSec, uint32_t rawDtUs) {
    if (!isfinite(dtSec) || dtSec < MIN_CONTROL_DT_SEC || dtSec > MAX_CONTROL_DT_SEC)
        return false;
    int32_t snapshot[MOTOR_COUNT];
    snapshotEncoderCounts(snapshot);
    const int64_t deltaLimit = encoderDeltaLimitCounts(
        WHEEL_SPEED_LIMIT_CM_S, WHEEL_RADIUS_CM, ENCODER_COUNTS_PER_REV,
        dtSec, ENCODER_JUMP_SAFETY_MARGIN);
    EncoderSample sample;
    sample.dtUs = rawDtUs == 0 ? uint32_t(dtSec * 1000000.0f) : rawDtUs;
    for (uint8_t i = 0; i < MOTOR_COUNT; ++i) {
        sample.previous[i] = motorEncoderState.previousCount[i];
        sample.current[i] = snapshot[i];
        sample.delta[i] = encoderWrappedDelta(snapshot[i], sample.previous[i]);
        sample.pwm[i] = motorEncoderState.currentPwm[i];
        sample.target[i] = motion_control::targetWheelSpeed[i];
        sample.speed[i] = motorEncoderState.measuredWheelSpeed[i];
        motorEncoderState.count[i] = snapshot[i];
        motorEncoderState.previousCount[i] = snapshot[i];
        const bool valid = llabs(sample.delta[i]) <= deltaLimit;
        motorEncoderState.encoderValid[i] = valid;
        if (!valid) sample.motorMask |= uint8_t(1U << i);
    }
    const bool allValid = sample.motorMask == 0;
    encoderDiagnostics.badWindow = nextEncoderWindow(encoderDiagnostics.badWindow, allValid);
    encoderDiagnostics.recentBad = encoderWindowBadCount(encoderDiagnostics.badWindow);
    if (!allValid) {
        encoderDiagnostics.lastInvalid = sample;
        encoderDiagnostics.lastInvalidAtMs = millis();
        encoderDiagnostics.faultDeltaLimit = deltaLimit;
        encoderDiagnostics.warning = true;
        encoderDiagnostics.goodSamples = 0;
        if (encoderDiagnostics.badSamples < UINT16_MAX) ++encoderDiagnostics.badSamples;
    } else {
        encoderDiagnostics.badSamples = 0;
        if (encoderDiagnostics.goodSamples < UINT16_MAX) ++encoderDiagnostics.goodSamples;
        if (encoderRecoveryReady()) encoderDiagnostics.warning = false;
    }
    for (uint8_t i = 0; i < MOTOR_COUNT; ++i) {
        if (!motorEncoderState.encoderValid[i]) {
            motorEncoderState.deltaCount[i] = 0;
            continue;
        }
        motorEncoderState.deltaCount[i] = int32_t(sample.delta[i]);
        const float rawSpeed = wheelDistanceCm(motorEncoderState.deltaCount[i],
            ENCODER_COUNTS_PER_REV, WHEEL_RADIUS_CM) / dtSec;
        motorEncoderState.measuredWheelSpeed[i] = SPEED_FILTER_ALPHA * rawSpeed +
            (1.0f - SPEED_FILTER_ALPHA) * motorEncoderState.measuredWheelSpeed[i];
    }
    return allValid;
}

void zeroEncoderReference() {
    int32_t snapshot[MOTOR_COUNT];
    snapshotEncoderCounts(snapshot);
    for (uint8_t i = 0; i < MOTOR_COUNT; ++i) {
        motorEncoderState.count[i] = snapshot[i];
        motorEncoderState.previousCount[i] = snapshot[i];
        motorEncoderState.deltaCount[i] = 0;
        motorEncoderState.measuredWheelSpeed[i] = 0.0f;
        motorEncoderState.encoderValid[i] = true;
    }
}
