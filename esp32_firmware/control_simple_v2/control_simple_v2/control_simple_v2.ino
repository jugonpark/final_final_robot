#include <Arduino.h>
#include <WiFi.h>
#include <WiFiUdp.h>
#include <ArduinoJson.h>
#include <esp_system.h>
#include <math.h>

// =====================================================
// D.I.G / GRISE Wi-Fi Omni Controller
//
// Base hardware mapping: test2.ino
// Simple single-file adapter for python_controller/communication/robot_session.py
// Single-file CONTROL SIMPLE V2: common feedforward + PI motor controller.
// Preserved: original working pin map, encoder x1 + M2/M3 pullups,
// 3WD kinematics, serial motor tests, V2 UDP/session/STOP/goal telemetry.
// Both cmd_vel (manual/camera) and cmd_move (LLM) use runWheelController().
// BENCH ON remains a lifted-wheel cmd_vel-only diagnostic (not normal control).
// Motor forward/reverse feedforward values are INITIAL estimates from the
// separate V2 candidate calibration, NOT measurements of this new firmware.
// No ENCODER_JUMP fault latch; invalid pulses are excluded from speed/odometry.
// Arduino-ESP32 Core 3.x, ArduinoJson v7.
// DEVELOPMENT FIRMWARE: first test with wheels lifted. Never auto-replay moves.
// =====================================================

// =====================================================
// 1) USER SETTINGS
// =====================================================

constexpr uint32_t SERIAL_BAUD = 115200;

// ---- Wi-Fi ----
// 반드시 실제 공유기 정보로 바꿔주세요.
#include "secrets.h"
#include "hardware_profiles.h"

// DHCP is the default. Only use static IP after matching your network settings.
constexpr bool USE_STATIC_IP = false;  // true only after setting matching local network values
IPAddress LOCAL_IP(192, 168, 0, 50);
IPAddress GATEWAY (192, 168, 0, 1);
IPAddress SUBNET(255, 255, 255, 0);
IPAddress DNS1(8, 8, 8, 8);

constexpr uint16_t UDP_PORT = 8888;
constexpr uint32_t WIFI_RETRY_MS = 3000;

// Central-control V2 timing must match python_controller/communication/timing.py.
constexpr uint32_t HEARTBEAT_PERIOD_MS = 100;
constexpr uint32_t HEARTBEAT_TIMEOUT_MS = 600;
constexpr uint32_t VELOCITY_TTL_MS = 300;
constexpr uint32_t GOAL_TELEMETRY_MS = 100;
constexpr uint32_t MANUAL_PWM_TIMEOUT_MS = 2000;
constexpr uint16_t UDP_TELEMETRY_PORT = 8889;
constexpr int NETWORK_PWM_CAP = 180;
constexpr int MANUAL_PWM_CAP = 180;
constexpr float ENCODER_PLAUSIBLE_WHEEL_CM_S = 65.0f; // Outlier rejection, not a latched fault
constexpr uint32_t GOAL_BAD_ENCODER_MS = 300;
constexpr float GOAL_DISTANCE_TOLERANCE_CM = 0.5f;
constexpr float GOAL_LATERAL_TOLERANCE_CM = 2.0f;
constexpr float GOAL_ACCEL_CM_S2 = 8.0f;
// Near a distance goal, do not request the former 1.5 cm/s ultra-low-speed body motion.
// Physical accuracy of these values must be checked on the real platform.
constexpr float GOAL_APPROACH_CM_S = 3.0f;
constexpr float GOAL_MAX_DISTANCE_CM = 100.0f;


// ---- PWM ----
constexpr uint32_t PWM_FREQUENCY = 20000;
constexpr uint8_t PWM_RESOLUTION = 8;
constexpr int PWM_MAX = 255;
// Minimum continuous PWM: STARTING GUESSES, not validated as stable by wheel.
// Do not increase blindly: low duty might need a faster speed command instead.
constexpr int MIN_RUN_PWM_POS[3] = {52, 58, 54};
constexpr int MIN_RUN_PWM_NEG[3] = {52, 54, 52};

constexpr uint8_t MOTOR_COUNT = 3;

// ---- Encoder ----
// GA25-370: motor shaft 11 PPR × reduction ratio 74.83
// A phase rising only.
constexpr float COUNTS_PER_OUTPUT_REV = 11.0f * 74.83f;
constexpr uint32_t ENCODER_TELEMETRY_MS = 200;

// ---- Robot geometry ----
// 아래 2개 값은 실제 로봇을 자로 재서 최종 보정하세요.
constexpr float WHEEL_RADIUS_CM = 2.9f;
constexpr float ROBOT_RADIUS_CM = 9.0f;

// 휠 장착각: robot +X(front) 기준 CCW.
constexpr float WHEEL_ANGLE_DEG[MOTOR_COUNT] = {0.0f, 120.0f, 240.0f};

// 한 바퀴라도 이 속도를 넘으면 세 바퀴를 같은 비율로 축소.
constexpr float MAX_WHEEL_SPEED_CM_S = 60.0f;

// PC에서 오는 body command의 비정상 값 방어용 상한.
constexpr float MAX_CMD_LINEAR_CM_S = 50.0f;
constexpr float MAX_CMD_ANGULAR_RAD_S = 3.0f;

// status=SLOW일 때 ESP32에서도 한 번 더 상한을 건다.
// PC가 이미 0.4배 감속하므로 여기서는 '배율'이 아니라 최대치만 제한한다.
constexpr float SLOW_MAX_LINEAR_CM_S = 15.0f;
constexpr float SLOW_MAX_ANGULAR_RAD_S = 1.0f;

// ---- Control loop ----
constexpr uint32_t CONTROL_PERIOD_MS = 10; // 100 Hz

// PI correction of the measured wheel-speed error (in cm/s).
// The feedforward provides most of the PWM; PI only trims it.
// Conservative starting coefficients. Tune on lifted wheels before floor driving.
constexpr float WHEEL_PI_KP = 1.3f;
constexpr float WHEEL_PI_KI = 1.5f;
constexpr float PI_TRIM_LIMIT_PWM = 24.0f;
constexpr float PI_INTEGRAL_LIMIT = PI_TRIM_LIMIT_PWM / WHEEL_PI_KI;
// Smooth wheel target changes and applied PWM; STOP bypasses both ramps.
constexpr float WHEEL_TARGET_SLEW_CM_S2 = 35.0f;
constexpr float PWM_SLEW_COUNTS_PER_SEC = 320.0f;
constexpr float MIN_WHEEL_COMMAND_CM_S = 0.25f;
// Optional target floor: keep zero until a minimum smooth wheel RPM is measured.
// Nonzero values change commanded speed, including LLM distance profiles.
constexpr float MIN_CONTINUOUS_WHEEL_CM_S = 0.0f;
// Measured calibration points from the earlier V2 candidate (wheels lifted),
// NOT verified on the SIMPLE V2 controller or with its CPR convention.
constexpr uint8_t FF_POINT_COUNT = 7;
constexpr float FF_SPEED_CM_S[FF_POINT_COUNT] =
    {0.0f, 2.5f, 5.0f, 7.5f, 10.0f, 12.5f, 15.0f};
constexpr float FF_PWM_POS[MOTOR_COUNT][FF_POINT_COUNT] = {
    {0, 36, 56, 77, 99, 120, 151},
    {0, 45, 68, 90, 111, 129, 153},
    {0, 38, 57, 80, 105, 119, 141}
};
constexpr float FF_PWM_NEG[MOTOR_COUNT][FF_POINT_COUNT] = {
    {0, 35, 56, 76, 97, 114, 131},
    {0, 40, 59, 78, 97, 114, 132},
    {0, 35, 55, 75, 97, 116, 137}
};

// Instantaneous encoder-speed LPF.
constexpr float SPEED_FILTER_ALPHA = 0.20f;

// =====================================================
// 2) HARDWARE PIN MAP - test2.ino 그대로 유지
// =====================================================

constexpr uint8_t PIN_STBY = HARDWARE_PINS.stby;

constexpr uint8_t M1_IN1 = HARDWARE_PINS.m1.in1;
constexpr uint8_t M1_IN2 = HARDWARE_PINS.m1.in2;
constexpr uint8_t M1_PWM = HARDWARE_PINS.m1.pwm;
constexpr uint8_t M1_ENC_A = HARDWARE_PINS.m1.encA;
constexpr uint8_t M1_ENC_B = HARDWARE_PINS.m1.encB;

constexpr uint8_t M2_IN1 = HARDWARE_PINS.m2.in1;
constexpr uint8_t M2_IN2 = HARDWARE_PINS.m2.in2;
constexpr uint8_t M2_PWM = HARDWARE_PINS.m2.pwm;
constexpr uint8_t M2_ENC_A = HARDWARE_PINS.m2.encA;
constexpr uint8_t M2_ENC_B = HARDWARE_PINS.m2.encB;

constexpr uint8_t M3_IN1 = HARDWARE_PINS.m3.in1;
constexpr uint8_t M3_IN2 = HARDWARE_PINS.m3.in2;
constexpr uint8_t M3_PWM = HARDWARE_PINS.m3.pwm;
constexpr uint8_t M3_ENC_A = HARDWARE_PINS.m3.encA;
constexpr uint8_t M3_ENC_B = HARDWARE_PINS.m3.encB;

// =====================================================
// 3) TYPES / GLOBAL STATE
// =====================================================

struct Motor {
    uint8_t in1;
    uint8_t in2;
    uint8_t pwmPin;
    uint8_t encoderA;
    uint8_t encoderB;
    bool motorReversed;
    bool encoderReversed;
    int currentPwm;
};

Motor motors[MOTOR_COUNT] = {
    {M1_IN1, M1_IN2, M1_PWM, M1_ENC_A, M1_ENC_B, false, false, 0},
    {M2_IN1, M2_IN2, M2_PWM, M2_ENC_A, M2_ENC_B, false, false, 0},
    {M3_IN1, M3_IN2, M3_PWM, M3_ENC_A, M3_ENC_B, false, false, 0},
};

enum ControlMode {
    MODE_NETWORK,
    MODE_MANUAL_PWM,
};

ControlMode controlMode = MODE_NETWORK;

WiFiUDP udp;
bool udpStarted = false;
uint32_t lastWifiRetryMs = 0;
char packetBuffer[512];

volatile int32_t encoderCounts[MOTOR_COUNT] = {0, 0, 0};

// 200ms telemetry용
int32_t previousTelemetryCounts[MOTOR_COUNT] = {0, 0, 0};
float encoderRpm[MOTOR_COUNT] = {0.0f, 0.0f, 0.0f};
uint32_t lastTelemetryMs = 0;
bool streamEnabled = false;

// 100Hz wheel control용
int32_t previousControlCounts[MOTOR_COUNT] = {0, 0, 0};
float targetWheelSpeed[MOTOR_COUNT] = {0.0f, 0.0f, 0.0f}; // cm/s
float measuredWheelSpeed[MOTOR_COUNT] = {0.0f, 0.0f, 0.0f}; // cm/s
float tickWheelDeltaCm[MOTOR_COUNT] = {0,0,0};
bool encoderValid[MOTOR_COUNT] = {true,true,true};
bool allEncoderValid = true;
bool encoderWarning = false;
uint16_t encoderGoodSamples = 0;
uint16_t encoderRecentWindow = 0;
uint8_t encoderRecentBad = 0;
uint16_t consecutiveBadTicks = 0;
int32_t lastBadDelta[MOTOR_COUNT] = {0,0,0};
uint8_t lastBadMotorMask = 0;
uint32_t lastBadAtMs = 0;
float piIntegral[MOTOR_COUNT] = {0.0f, 0.0f, 0.0f};
float regulatedWheelTarget[MOTOR_COUNT] = {0.0f, 0.0f, 0.0f};
float wheelFeedforwardPwm[MOTOR_COUNT] = {0.0f, 0.0f, 0.0f};
float wheelPiTrimPwm[MOTOR_COUNT] = {0.0f, 0.0f, 0.0f};
uint32_t lastControlMs = 0;

// 최신 네트워크 명령
float cmdVx = 0.0f;  // cm/s, robot forward +
float cmdVy = 0.0f;  // cm/s, robot left +
float cmdW = 0.0f;   // rad/s, CCW +
String cmdStatus = "STOP";
uint32_t lastSeq = 0;
uint32_t lastCommandMs = 0;

bool networkStopped = true;
uint32_t lastStatusPrintMs = 0;

// =====================================================
// 4) ENCODER ISR
// =====================================================

void IRAM_ATTR encoder1ISR() {
    int direction = digitalRead(M1_ENC_B) ? -1 : 1;
    if (motors[0].encoderReversed) direction = -direction;
    encoderCounts[0] += direction;
}

void IRAM_ATTR encoder2ISR() {
    int direction = digitalRead(M2_ENC_B) ? -1 : 1;
    if (motors[1].encoderReversed) direction = -direction;
    encoderCounts[1] += direction;
}

void IRAM_ATTR encoder3ISR() {
    int direction = digitalRead(M3_ENC_B) ? -1 : 1;
    if (motors[2].encoderReversed) direction = -direction;
    encoderCounts[2] += direction;
}

void snapshotEncoderCounts(int32_t snapshot[MOTOR_COUNT]) {
    noInterrupts();
    for (uint8_t i = 0; i < MOTOR_COUNT; i++) snapshot[i] = encoderCounts[i];
    interrupts();
}

// =====================================================
// 5) MOTOR OUTPUT
// =====================================================

void printMotorName(uint8_t motorIndex) {
    Serial.print("M");
    Serial.print(motorIndex + 1);
}

void writeMotorPwm(uint8_t motorIndex, int pwm, bool verbose = false) {
    if (motorIndex >= MOTOR_COUNT) return;
    Motor &motor = motors[motorIndex];
    const int cap = controlMode == MODE_NETWORK ? NETWORK_PWM_CAP : MANUAL_PWM_CAP;
    pwm = constrain(pwm, -cap, cap);
    const int previous = motor.currentPwm;

    // CRITICAL: same-direction PWM changes must NOT pulse the output to zero.
    // Even an identical target needs no additional LEDC or direction-pin writes.
    if (pwm == previous) return;
    if (pwm == 0) {
        ledcWrite(motor.pwmPin, 0);
        digitalWrite(motor.in1, LOW);
        digitalWrite(motor.in2, LOW);
        motor.currentPwm = 0;
    } else {
        const bool reversing = previous != 0 && ((previous > 0) != (pwm > 0));
        if (reversing) {
            ledcWrite(motor.pwmPin, 0);
            delayMicroseconds(100); // motor-driver direction change dead time
        }
        if (previous == 0 || reversing) {
            bool forward = pwm > 0;
            if (motor.motorReversed) forward = !forward;
            digitalWrite(motor.in1, forward ? HIGH : LOW);
            digitalWrite(motor.in2, forward ? LOW : HIGH);
        }
        ledcWrite(motor.pwmPin, abs(pwm));
        motor.currentPwm = pwm;
    }
    if (verbose) {
        printMotorName(motorIndex);
        Serial.print(" PWM = ");
        Serial.println(pwm);
    }
}

void setAllMotorsManual(int m1, int m2, int m3) {
    controlMode = MODE_MANUAL_PWM;
    writeMotorPwm(0, m1, true);
    writeMotorPwm(1, m2, true);
    writeMotorPwm(2, m3, true);
}

void resetPidState() {
    for (uint8_t i = 0; i < MOTOR_COUNT; i++) {
        targetWheelSpeed[i] = 0.0f;
        regulatedWheelTarget[i] = 0.0f;
        piIntegral[i] = 0.0f;
        wheelFeedforwardPwm[i] = 0.0f;
        wheelPiTrimPwm[i] = 0.0f;
    }
}

void stopAllMotors(bool verbose = false) {
    for (uint8_t i = 0; i < MOTOR_COUNT; i++) {
        writeMotorPwm(i, 0, false);
    }
    resetPidState();
    if (verbose) Serial.println("All motors stopped.");
}

// =====================================================
// 6) ENCODER SPEED
// =====================================================

void zeroEncoders() {
    noInterrupts();
    for (uint8_t i = 0; i < MOTOR_COUNT; i++) encoderCounts[i] = 0;
    interrupts();

    for (uint8_t i = 0; i < MOTOR_COUNT; i++) {
        previousTelemetryCounts[i] = 0;
        previousControlCounts[i] = 0;
        encoderRpm[i] = 0.0f;
        measuredWheelSpeed[i] = 0.0f;
    }

    lastTelemetryMs = millis();
    lastControlMs = millis();
    Serial.println("Encoder counts reset.");
}

void updateWheelSpeedForControl(float dt) {
    int32_t current[MOTOR_COUNT];
    snapshotEncoderCounts(current);
    const float circumference = 2.0f * (float)M_PI * WHEEL_RADIUS_CM;
    const float validDt = fmaxf(.001f, dt);
    const float maxDelta = fmaxf(8.0f, ceilf(ENCODER_PLAUSIBLE_WHEEL_CM_S *
        validDt * COUNTS_PER_OUTPUT_REV / circumference));
    allEncoderValid = true;
    uint8_t badMask = 0;
    for (uint8_t i=0; i<MOTOR_COUNT; ++i) {
        // Unsigned subtraction avoids signed overflow in the delta arithmetic.
        const uint32_t wrapped = (uint32_t)current[i] - (uint32_t)previousControlCounts[i];
        const int32_t delta = (int32_t)wrapped;
        previousControlCounts[i] = current[i];
        encoderValid[i] = fabsf((float)delta) <= maxDelta;
        if (!encoderValid[i]) {
            allEncoderValid = false;
            badMask |= (1U << i);
            lastBadDelta[i] = delta;
            tickWheelDeltaCm[i] = 0.0f;
            // Let stopped feedback settle without injecting a huge false speed.
            measuredWheelSpeed[i] *= (1.0f - SPEED_FILTER_ALPHA);
            continue;
        }
        tickWheelDeltaCm[i] = (float)delta * circumference / COUNTS_PER_OUTPUT_REV;
        const float raw = tickWheelDeltaCm[i] / validDt;
        measuredWheelSpeed[i] = (1.0f-SPEED_FILTER_ALPHA) * measuredWheelSpeed[i]
                                + SPEED_FILTER_ALPHA * raw;
    }
    encoderRecentWindow = ((encoderRecentWindow << 1) | (allEncoderValid ? 0 : 1)) & 0x03ff;
    encoderRecentBad = (uint8_t)__builtin_popcount((unsigned int)encoderRecentWindow);
    if (!allEncoderValid) {
        encoderWarning = true;
        encoderGoodSamples = 0;
        if (consecutiveBadTicks < UINT16_MAX) ++consecutiveBadTicks;
        lastBadAtMs = millis();
        lastBadMotorMask = badMask;
    } else {
        consecutiveBadTicks = 0;
        if (encoderGoodSamples < UINT16_MAX) ++encoderGoodSamples;
        if (encoderGoodSamples >= 10) encoderWarning = false;
    }
}

void updateEncoderTelemetry() {
    const uint32_t now = millis();
    if ((uint32_t)(now-lastTelemetryMs) < ENCODER_TELEMETRY_MS) return;
    int32_t current[MOTOR_COUNT];
    snapshotEncoderCounts(current);
    for (uint8_t i=0; i<MOTOR_COUNT; ++i) {
        previousTelemetryCounts[i] = current[i];
        encoderRpm[i] = measuredWheelSpeed[i] * 60.0f /
                        (2.0f*(float)M_PI*WHEEL_RADIUS_CM);
    }
    lastTelemetryMs = now;
}

// =====================================================
// 7) OMNI KINEMATICS / SHARED FEEDFORWARD + PI
// =====================================================

void inverseKinematics(float vx, float vy, float w, float out[MOTOR_COUNT]) {
    for (uint8_t i = 0; i < MOTOR_COUNT; i++) {
        const float a = WHEEL_ANGLE_DEG[i] * (float)M_PI / 180.0f;
        out[i] = -sinf(a) * vx + cosf(a) * vy + ROBOT_RADIUS_CM * w;
    }

    float maxAbs = 0.0f;
    for (uint8_t i = 0; i < MOTOR_COUNT; i++) {
        maxAbs = fmaxf(maxAbs, fabsf(out[i]));
    }

    if (maxAbs > MAX_WHEEL_SPEED_CM_S) {
        const float scale = MAX_WHEEL_SPEED_CM_S / maxAbs;
        for (uint8_t i = 0; i < MOTOR_COUNT; i++) out[i] *= scale;
    }
}

// Diagnostic mode: reproduce the serial M1 80 constant-duty behavior over the
// SAME central-control UDP command path. Only use with all wheels lifted.
// OFF by default, never used for cmd_move distance goals.
bool benchRawPwm = false;
constexpr int BENCH_RAW_PWM = 80;

void runWheelPwmBench() {
    for (uint8_t i = 0; i < MOTOR_COUNT; ++i) {
        const float target = targetWheelSpeed[i];
        const int commanded = fabsf(target) < 0.3f ? 0 : (target > 0.0f ? BENCH_RAW_PWM : -BENCH_RAW_PWM);
        // Apply just once until the direction/STOP changes, like serial M1 80.
        if (motors[i].currentPwm != commanded) writeMotorPwm(i, commanded);
    }
}

// Feedforward interpolation keeps M1/M2/M3 and forward/reverse characteristics
// distinct, which matters because CCW and CW measured RPM are asymmetric.
float feedForwardDuty(uint8_t motor, bool positive, float wheelCmS) {
    const float *table = positive ? FF_PWM_POS[motor] : FF_PWM_NEG[motor];
    const float speed = fabsf(wheelCmS);
    if (speed <= 0.0f) return 0.0f;
    float duty = table[FF_POINT_COUNT - 1];
    if (speed <= FF_SPEED_CM_S[0]) duty = table[0];
    else for (uint8_t j = 1; j < FF_POINT_COUNT; ++j) {
        if (speed <= FF_SPEED_CM_S[j]) {
            const float width = FF_SPEED_CM_S[j] - FF_SPEED_CM_S[j-1];
            const float t = (speed - FF_SPEED_CM_S[j-1]) / width;
            duty = table[j-1] + t*(table[j]-table[j-1]);
            break;
        }
    }
    return fmaxf(duty, (float)(positive ? MIN_RUN_PWM_POS[motor] : MIN_RUN_PWM_NEG[motor]));
}

// Common 100-Hz controller for cmd_vel AND cmd_move. Never disables PID only
// for LLM or applies fixed BENCH PWM to distance goals.
void runWheelController(float dt) {
    const float validDt = fmaxf(0.001f, fminf(.050f, dt));
    const float targetStep = WHEEL_TARGET_SLEW_CM_S2 * validDt;
    const int pwmStep = (int)fmaxf(1.0f, ceilf(PWM_SLEW_COUNTS_PER_SEC*validDt));
    for (uint8_t i = 0; i < MOTOR_COUNT; ++i) {
        float wanted = targetWheelSpeed[i];
        if (fabsf(wanted) < MIN_WHEEL_COMMAND_CM_S) wanted = 0.0f;
        if (wanted != 0.0f && MIN_CONTINUOUS_WHEEL_CM_S > 0.0f &&
            fabsf(wanted) < MIN_CONTINUOUS_WHEEL_CM_S)
            wanted = copysignf(MIN_CONTINUOUS_WHEEL_CM_S, wanted);

        // A new command sign never drags the old PI integral across directions.
        if ((wanted == 0.0f) ||
            (regulatedWheelTarget[i] != 0.0f &&
             ((wanted > 0.0f) != (regulatedWheelTarget[i] > 0.0f)))) {
            piIntegral[i] = 0.0f;
        }
        regulatedWheelTarget[i] += constrain(
            wanted - regulatedWheelTarget[i], -targetStep, targetStep);
        float target = regulatedWheelTarget[i];
        if (fabsf(target) < MIN_WHEEL_COMMAND_CM_S && wanted == 0.0f) {
            regulatedWheelTarget[i] = 0.0f;
            piIntegral[i] = 0.0f;
            wheelFeedforwardPwm[i] = wheelPiTrimPwm[i] = 0.0f;
            writeMotorPwm(i, 0);
            continue;
        }
        if (fabsf(target) < MIN_WHEEL_COMMAND_CM_S) continue;
        const int sign = target > 0.0f ? 1 : -1;
        const int oldDuty = motors[i].currentPwm;
        const int oldSign = oldDuty > 0 ? 1 : (oldDuty < 0 ? -1 : 0);
        if (oldSign != 0 && oldSign != sign) {
            // No active reversal: pass through an actual zero-output tick.
            writeMotorPwm(i, 0);
            piIntegral[i] = 0.0f;
            wheelFeedforwardPwm[i] = wheelPiTrimPwm[i] = 0.0f;
            continue;
        }
        const float ff = feedForwardDuty(i, sign > 0, target);
        float trim = 0.0f;
        if (encoderValid[i]) {
            // Compute magnitude error in the requested direction. Encoder sign
            // calibration MUST be correct: otherwise trim will saturate.
            const float measuredInDirection = sign * measuredWheelSpeed[i];
            const float error = fabsf(target) - measuredInDirection;
            float tentativeI = constrain(piIntegral[i] + error*validDt,
                                         -PI_INTEGRAL_LIMIT, PI_INTEGRAL_LIMIT);
            float tentativeTrim = constrain(WHEEL_PI_KP*error + WHEEL_PI_KI*tentativeI,
                                            -PI_TRIM_LIMIT_PWM, PI_TRIM_LIMIT_PWM);
            const float raw = ff + tentativeTrim;
            const int minDuty = sign > 0 ? MIN_RUN_PWM_POS[i] : MIN_RUN_PWM_NEG[i];
            // Anti-windup at output limits: when stalled at a bound, do not
            // integrate even further toward the same saturation.
            if ((raw >= NETWORK_PWM_CAP && error > 0.0f) ||
                (raw <= minDuty && error < 0.0f)) {
                tentativeI = piIntegral[i];
                tentativeTrim = constrain(WHEEL_PI_KP*error + WHEEL_PI_KI*tentativeI,
                                          -PI_TRIM_LIMIT_PWM, PI_TRIM_LIMIT_PWM);
            }
            piIntegral[i] = tentativeI;
            trim = tentativeTrim;
        } // bad encoder tick: retain prior PI state, use bounded FF baseline
        wheelFeedforwardPwm[i] = sign * ff;
        wheelPiTrimPwm[i] = sign * trim;
        const int minDuty = sign > 0 ? MIN_RUN_PWM_POS[i] : MIN_RUN_PWM_NEG[i];
        const float desiredMagnitude = constrain(ff + trim,
                                                  (float)minDuty, (float)NETWORK_PWM_CAP);
        const int desired = sign * (int)lroundf(desiredMagnitude);
        // A slew on physical PWM suppresses sudden feedback-induced output jumps.
        // STOP bypasses this (stopAllMotors calls writeMotorPwm immediately).
        const int next = oldDuty + constrain(desired - oldDuty, -pwmStep, pwmStep);
        writeMotorPwm(i, next);
    }
}


// =====================================================
// 8) CENTRAL-CONTROL V2 BRIDGE (single-file implementation)
// =====================================================
// The motor driver, encoder ISR, kinematics and PID above are preserved from
// the user's working one-file firmware. Both direct and distance commands share
// the wheel controller; only the goal path manages odometry and stopping.  Do not report a fake GOAL_REACHED.

enum SimpleMotionState : uint8_t {
    SIMPLE_STOPPED, SIMPLE_VELOCITY, SIMPLE_DISTANCE_ACTIVE,
    SIMPLE_DISTANCE_BRAKING, SIMPLE_GOAL_REACHED
};
SimpleMotionState simpleState = SIMPLE_STOPPED;

uint32_t bootId = 0;
uint32_t sessionId = 0;
bool hasSession = false;
bool hasHandshake = false;
bool protocolV2 = false;
bool controllerKnown = false;
IPAddress controllerIp;
uint32_t lastAcceptedMs = 0;
uint32_t lastHeartbeatMs = 0;
uint32_t lastVelocityMs = 0;
uint32_t lastManualPwmAtMs = 0;
uint32_t lastUdpTelemetryMs = 0;
uint32_t lastStopAppliedMs = 0;
uint32_t executionToken = 0;
uint32_t retiredToken = 0;
uint32_t activeToken = 0;
bool tokenActive = false;
bool hasHeartbeat = false;
bool hasVelocity = false;
bool controllerAlive = false;
bool motionPermit = false;
uint32_t udpReceived = 0;
uint32_t udpAccepted = 0;
uint32_t udpRejected = 0;
char lastFault[32] = "NONE";  // Diagnostic; never a global latch.
bool forceTelemetry = false;
String encoderPolicy = "FILTERED_TEST";

// One active distance goal at a time. No nested state machines.
bool goalContentValid = false;
bool goalSettling = false;
int32_t goalMotionId = -1;
float goalTargetCm = 0.0f;
float goalProgressCm = 0.0f;
float goalRemainingCm = 0.0f;
float goalLateralCm = 0.0f;
float goalOvershootCm = 0.0f;
float goalUx = 0.0f;
float goalUy = 0.0f;
float goalCruiseCmS = 0.0f;
float goalOdomDxCm = 0.0f;
float goalOdomDyCm = 0.0f;
float goalOdomThetaRad = 0.0f;
uint32_t goalStartedMs = 0;
uint32_t goalTimeoutMs = 0;
uint32_t goalBadSinceMs = 0;

const char *simpleStateName() {
    switch (simpleState) {
        case SIMPLE_STOPPED: return "STOPPED";
        case SIMPLE_VELOCITY: return "VELOCITY";
        case SIMPLE_DISTANCE_ACTIVE: return "DISTANCE_ACTIVE";
        case SIMPLE_DISTANCE_BRAKING: return "DISTANCE_BRAKING";
        case SIMPLE_GOAL_REACHED: return "GOAL_REACHED";
    }
    return "STOPPED";
}

bool isNetworkMoving() {
    return simpleState == SIMPLE_VELOCITY ||
           simpleState == SIMPLE_DISTANCE_ACTIVE ||
           simpleState == SIMPLE_DISTANCE_BRAKING;
}

void retireToken() {
    if (tokenActive && activeToken > retiredToken) retiredToken = activeToken;
    tokenActive = false;
    motionPermit = false;
}

void stopNetworkMotion(const char *reason = nullptr) {
    // Important: actuator shutoff is first, before any log/JSON processing.
    stopAllMotors(false);
    lastStopAppliedMs = millis();
    cmdVx = cmdVy = cmdW = 0.0f;
    cmdStatus = "STOP";
    networkStopped = true;
    retireToken();
    simpleState = SIMPLE_STOPPED;
    goalSettling = false;
    goalContentValid = false;
    goalMotionId = -1;
    goalBadSinceMs = 0;
    forceTelemetry = true;
    if (reason && strcmp(reason,"NONE") != 0) {
        snprintf(lastFault, sizeof(lastFault), "%s", reason);
        Serial.print("[STOP] ");
        Serial.println(reason);
    }
}

void beginNetworkSession(uint32_t id, uint32_t seq, bool v2, const IPAddress &remote) {
    stopNetworkMotion();
    const bool changed = !hasSession || sessionId != id;
    sessionId = id;
    hasSession = true;
    hasHandshake = true;
    protocolV2 = v2;
    lastSeq = changed ? seq : ((seq > lastSeq) ? seq : lastSeq);
    if (changed) {
        retiredToken = 0;
        executionToken = 0;
    }
    activeToken = 0;
    controllerIp = remote;
    controllerKnown = true;
    controlMode = MODE_NETWORK;
    lastAcceptedMs = millis();
    forceTelemetry = true;
}

bool validJsonNumber(JsonObjectConst root, const char *name, float &out) {
    JsonVariantConst v = root[name];
    if (v.isNull() || v.is<bool>() ||
        !(v.is<float>() || v.is<double>() || v.is<int32_t>() || v.is<uint32_t>()))
        return false;
    out = v.as<float>();
    return isfinite(out);
}

bool getMotionFields(JsonObjectConst root, float &vx, float &vy, float &w) {
    const char *status = root["status"] | "";
    if (strcmp(status,"SLOW") != 0 && strcmp(status,"RUN") != 0) return false;
    if (!validJsonNumber(root,"vx",vx) || !validJsonNumber(root,"vy",vy) ||
        !validJsonNumber(root,"w",w)) return false;
    if (hypotf(vx,vy) > 30.0f || fabsf(w) > 2.0f) return false;
    if (strcmp(status,"SLOW") == 0) {
        const float len = hypotf(vx,vy);
        if (len > 5.0f) { vx*=5.0f/len; vy*=5.0f/len; }
        w = constrain(w,-0.5f,0.5f);
    }
    return true;
}

void applyCommandVelocity(float vx, float vy, float w, const char *status) {
    cmdVx = vx;
    cmdVy = vy;
    cmdW = w;
    cmdStatus = status;
    networkStopped = false;
    simpleState = SIMPLE_VELOCITY;
    goalContentValid = goalSettling = false;
    goalMotionId = -1;
    goalBadSinceMs = 0;
}

void startDistanceGoal(int32_t motionId, float vx, float vy, float w,
                       float distance, uint32_t nowMs) {
    // Dashboard LLM currently sends single-axis movement with w == 0.
    const float speed = hypotf(vx,vy);
    stopAllMotors(false);
    goalMotionId = motionId;
    goalTargetCm = distance;
    goalProgressCm = goalLateralCm = goalOvershootCm = 0.0f;
    goalRemainingCm = distance;
    goalUx = vx / speed;
    goalUy = vy / speed;
    goalCruiseCmS = fminf(5.0f, speed);
    goalOdomDxCm = goalOdomDyCm = goalOdomThetaRad = 0.0f;
    goalStartedMs = nowMs;
    goalTimeoutMs = (uint32_t)(distance/fmaxf(goalCruiseCmS,1.0f)*2000.0f)+3000U;
    goalContentValid = true;
    goalSettling = false;
    goalBadSinceMs = 0;
    simpleState = SIMPLE_DISTANCE_ACTIVE;
    cmdVx = goalUx*goalCruiseCmS;
    cmdVy = goalUy*goalCruiseCmS;
    cmdW = w;
    cmdStatus = "SLOW";
    networkStopped = false;
    forceTelemetry = true;
}

void receiveUdpCommands() {
    if (!udpStarted) return;
    for (int handled=0; handled<6; ++handled) {
        int size = udp.parsePacket();
        if (size <= 0) break;
        ++udpReceived;
        const IPAddress remote = udp.remoteIP();
        if (size >= (int)sizeof(packetBuffer)) {
            udp.flush();
            ++udpRejected;
            continue;
        }
        const int received = udp.read(packetBuffer,sizeof(packetBuffer)-1);
        if (received <= 0) { ++udpRejected; continue; }
        packetBuffer[received] = '\0';
        JsonDocument doc;
        if (deserializeJson(doc,packetBuffer)) { ++udpRejected; continue; }
        JsonObjectConst root = doc.as<JsonObjectConst>();
        if (root.isNull() || !root["session_id"].is<uint32_t>() ||
            !root["seq"].is<uint32_t>()) { ++udpRejected; continue; }
        const uint32_t incomingSession = root["session_id"].as<uint32_t>();
        const uint32_t incomingSeq = root["seq"].as<uint32_t>();
        if (incomingSession == 0) { ++udpRejected; continue; }
        const char *type = root["type"] | "";
        const int version = root["protocol_version"].isNull() ? 1 :
                            root["protocol_version"].as<int>();
        if (version != 1 && version != 2) { ++udpRejected; continue; }
        const uint32_t now = millis();

        // STOP is allowed from any source/session. It cannot start the motors.
        if (strcmp(type,"stop") == 0) {
            if (controlMode == MODE_MANUAL_PWM) stopAllMotors(false);
            beginNetworkSession(incomingSession,incomingSeq,version==2,remote);
            ++udpAccepted;
            continue;
        }
        // A new session must first send STOP, and manual serial mode is isolated.
        if (!hasHandshake || !hasSession || incomingSession != sessionId ||
            remote != controllerIp || controlMode != MODE_NETWORK ||
            incomingSeq <= lastSeq || !protocolV2 || version != 2) {
            ++udpRejected;
            continue;
        }

        if (strcmp(type,"heartbeat") == 0) {
            if (!root["execution_token"].is<uint32_t>() ||
                !root["controller_alive"].is<bool>() ||
                !root["motion_permit"].is<bool>() ||
                !root.containsKey("motion_id")) { ++udpRejected; continue; }
            const uint32_t incomingToken = root["execution_token"].as<uint32_t>();
            if (tokenActive && incomingToken != activeToken) { ++udpRejected; continue; }
            if (tokenActive && (simpleState == SIMPLE_DISTANCE_ACTIVE ||
                simpleState == SIMPLE_DISTANCE_BRAKING) &&
                (!root["motion_id"].is<int32_t>() ||
                 root["motion_id"].as<int32_t>() != goalMotionId)) {
                ++udpRejected; continue;
            }
            lastSeq = incomingSeq;
            lastAcceptedMs = lastHeartbeatMs = now;
            hasHeartbeat = true;
            controllerAlive = root["controller_alive"].as<bool>();
            motionPermit = tokenActive && root["motion_permit"].as<bool>();
            ++udpAccepted;
            if (tokenActive && (!controllerAlive || !motionPermit)) {
                stopNetworkMotion("CONTROLLER_UNAVAILABLE");
            }
            continue;
        }
        if (strcmp(type,"set_test_profile") == 0) {
            const char *p = root["encoder_policy"] | "";
            if (isNetworkMoving() || (strcmp(p,"FILTERED_TEST")!=0 && strcmp(p,"STRICT")!=0)) {
                ++udpRejected; continue;
            }
            encoderPolicy = p;
            lastSeq = incomingSeq;
            lastAcceptedMs = now;
            ++udpAccepted;
            forceTelemetry = true;
            continue;
        }
        if (strcmp(type,"reset_fault") == 0) {
            // No latched faults in SIMPLE. Acknowledged for UI compatibility.
            if (isNetworkMoving()) { ++udpRejected; continue; }
            lastSeq = incomingSeq;
            lastAcceptedMs = now;
            ++udpAccepted;
            continue;
        }
        if (strcmp(type,"cmd_vel") != 0 && strcmp(type,"cmd_move") != 0) {
            ++udpRejected; continue;
        }
        float vx,vy,w;
        if (!getMotionFields(root,vx,vy,w) || !root["execution_token"].is<uint32_t>()) {
            ++udpRejected;
            stopNetworkMotion("BAD_PACKET");
            continue;
        }
        const uint32_t incomingToken = root["execution_token"].as<uint32_t>();
        if (incomingToken == 0 || incomingToken <= retiredToken ||
            (tokenActive && incomingToken < activeToken)) {
            ++udpRejected;
            continue;
        }
        const bool isGoal = strcmp(type,"cmd_move") == 0;
        int32_t incomingMotionId = -1;
        float incomingDistance = 0.0f;
        if (isGoal) {
            if (!root["motion_id"].is<int32_t>() ||
                !validJsonNumber(root,"target_distance_cm",incomingDistance) ||
                incomingDistance < 5.0f || incomingDistance > GOAL_MAX_DISTANCE_CM ||
                fabsf(w) > .001f || hypotf(vx,vy) < .1f) {
                ++udpRejected;
                stopNetworkMotion("BAD_PACKET");
                continue;
            }
            incomingMotionId = root["motion_id"].as<int32_t>();
            if (incomingMotionId < 0) { ++udpRejected; continue; }
        }
        if (tokenActive && incomingToken == activeToken &&
            (isGoal != goalContentValid ||
             (isGoal && (incomingMotionId != goalMotionId ||
                         fabsf(incomingDistance-goalTargetCm)>.01f)))) {
            ++udpRejected;
            stopNetworkMotion("BAD_PACKET");
            continue;
        }
        const bool newToken = !tokenActive || incomingToken != activeToken;
        if (newToken) {
            retireToken();
            tokenActive = true;
            activeToken = executionToken = incomingToken;
            controllerAlive = motionPermit = true;  // initial one-heartbeat grace
            lastHeartbeatMs = now;
            hasHeartbeat = true;
        }
        lastSeq = incomingSeq;
        lastAcceptedMs = lastCommandMs = now;
        if (isGoal) {
            if (newToken) startDistanceGoal(incomingMotionId,vx,vy,w,incomingDistance,now);
            // Same motion_id/token retry is idempotent; progress is not reset.
        } else {
            applyCommandVelocity(vx,vy,w,root["status"] | "SLOW");
            hasVelocity = true;
            lastVelocityMs = now;
        }
        ++udpAccepted;
    }
}

// =====================================================
// 9) SIMPLE MOVEMENT / WATCHDOG / DISTANCE ODOMETRY
// =====================================================

void updateGoalOdometry() {
    if (!allEncoderValid || !goalContentValid) return;
    const float d1=tickWheelDeltaCm[0],d2=tickWheelDeltaCm[1],d3=tickWheelDeltaCm[2];
    const float dx=(d3-d2)/sqrtf(3.0f);
    const float dy=(2.0f*d1-d2-d3)/3.0f;
    const float dtheta=(d1+d2+d3)/(3.0f*ROBOT_RADIUS_CM);
    const float mid=goalOdomThetaRad+.5f*dtheta;
    goalOdomDxCm+=cosf(mid)*dx-sinf(mid)*dy;
    goalOdomDyCm+=sinf(mid)*dx+cosf(mid)*dy;
    goalOdomThetaRad+=dtheta;
    goalProgressCm=goalUx*goalOdomDxCm+goalUy*goalOdomDyCm;
    goalLateralCm=-goalUy*goalOdomDxCm+goalUx*goalOdomDyCm;
    goalRemainingCm=goalTargetCm-goalProgressCm;
    goalOvershootCm=fmaxf(0.0f,-goalRemainingCm);
}

void updateNetworkControl() {
    const uint32_t now=millis();
    if ((uint32_t)(now-lastControlMs)<CONTROL_PERIOD_MS) return;
    const float dt=fmaxf(.001f,fminf(.1f,(now-lastControlMs)/1000.0f));
    lastControlMs=now;
    updateWheelSpeedForControl(dt);  // always update, also while STOPPED
    if (controlMode != MODE_NETWORK) {
        if ((uint32_t)(now-lastManualPwmAtMs)>MANUAL_PWM_TIMEOUT_MS) {
            bool hasPwm=false;
            for (int i=0;i<MOTOR_COUNT;i++) if (motors[i].currentPwm!=0) hasPwm=true;
            if (hasPwm) stopAllMotors(false);
        }
        return;
    }
    if (!isNetworkMoving()) return;
    if (WiFi.status() != WL_CONNECTED) {
        stopNetworkMotion("WIFI_LOSS");return;
    }
    if (!tokenActive || !hasHeartbeat || !controllerAlive || !motionPermit ||
        (uint32_t)(now-lastHeartbeatMs)>=HEARTBEAT_TIMEOUT_MS) {
        stopNetworkMotion("HEARTBEAT_TIMEOUT");return;
    }
    if (simpleState==SIMPLE_VELOCITY &&
        (!hasVelocity || (uint32_t)(now-lastVelocityMs)>=VELOCITY_TTL_MS)) {
        stopNetworkMotion("VELOCITY_EXPIRED");return;
    }
    if (goalContentValid && (uint32_t)(now-goalStartedMs)>=goalTimeoutMs) {
        stopNetworkMotion("GOAL_TIMEOUT");return;
    }
    if (!allEncoderValid && encoderPolicy == "STRICT") {
        stopNetworkMotion("ENCODER_INVALID");return;
    }
    if (simpleState==SIMPLE_DISTANCE_ACTIVE || simpleState==SIMPLE_DISTANCE_BRAKING) {
        updateGoalOdometry();
        if (!allEncoderValid) {
            if (!goalBadSinceMs) goalBadSinceMs=now;
            if ((uint32_t)(now-goalBadSinceMs)>=GOAL_BAD_ENCODER_MS) {
                stopNetworkMotion("ENCODER_INVALID");return;
            }
        } else goalBadSinceMs=0;
        if (goalOvershootCm>0.0f) {
            stopNetworkMotion("ODOMETRY_INVALID");return;
        }
        if (!goalSettling && goalRemainingCm<=GOAL_DISTANCE_TOLERANCE_CM) {
            goalSettling=true;
            stopAllMotors(false);
            cmdVx=cmdVy=cmdW=0.0f;
            forceTelemetry=true;
        }
        if (goalSettling) {
            float maximum=0.0f;
            for (uint8_t i=0;i<MOTOR_COUNT;++i)
                maximum=fmaxf(maximum,fabsf(measuredWheelSpeed[i]));
            if (maximum<=0.8f) {
                if (goalRemainingCm>=0.0f && goalRemainingCm<=GOAL_DISTANCE_TOLERANCE_CM &&
                    fabsf(goalLateralCm)<=GOAL_LATERAL_TOLERANCE_CM) {
                    simpleState=SIMPLE_GOAL_REACHED;
                    retireToken();
                    lastStopAppliedMs=now;
                    forceTelemetry=true;
                } else stopNetworkMotion("ODOMETRY_INVALID");
            }
            return;
        }
        // Distance goals only generate a target profile; the motor feedback
        // and PWM output use the EXACT same runWheelController() as cmd_vel.
        const float room=fmaxf(0.0f,goalRemainingCm-GOAL_DISTANCE_TOLERANCE_CM);
        float speed=fminf(goalCruiseCmS,sqrtf(2.0f*GOAL_ACCEL_CM_S2*room));
        if (goalRemainingCm>GOAL_DISTANCE_TOLERANCE_CM) speed=fmaxf(speed,GOAL_APPROACH_CM_S);
        cmdVx=goalUx*speed;
        cmdVy=goalUy*speed;
        cmdW=0;
        simpleState=(speed<goalCruiseCmS) ? SIMPLE_DISTANCE_BRAKING : SIMPLE_DISTANCE_ACTIVE;
    }
    float wheels[MOTOR_COUNT];
    inverseKinematics(cmdVx,cmdVy,cmdW,wheels);
    for(uint8_t i=0;i<MOTOR_COUNT;++i) targetWheelSpeed[i]=wheels[i];
    if (benchRawPwm && simpleState == SIMPLE_VELOCITY) {
        runWheelPwmBench();
    } else {
        runWheelController(dt);
    }
}

// =====================================================
// 10) WI-FI / UDP TELEMETRY
// =====================================================

void startUdpIfNeeded() {
    if (WiFi.status() == WL_CONNECTED && !udpStarted) {
        udpStarted = udp.begin(UDP_PORT) == 1;
        if (udpStarted) {
            Serial.print("[UDP] command port ");Serial.println(UDP_PORT);
        }
    }
}

void beginWiFi() {
    WiFi.mode(WIFI_STA);
    if (strcmp(WIFI_SSID,"YOUR_WIFI_SSID") == 0) {
        Serial.println("[WIFI] Configure WIFI_SSID and WIFI_PASS first");
        return;
    }
    if (USE_STATIC_IP && !WiFi.config(LOCAL_IP,GATEWAY,SUBNET,DNS1))
        Serial.println("[WIFI] Static IP setup failed");
    WiFi.begin(WIFI_SSID,WIFI_PASS);
    lastWifiRetryMs=millis();
}

void maintainWiFi() {
    if (WiFi.status() == WL_CONNECTED) { startUdpIfNeeded(); return; }
    if (controlMode==MODE_NETWORK && isNetworkMoving()) stopNetworkMotion("WIFI_LOSS");
    if (udpStarted) { udp.stop();udpStarted=false;hasHandshake=false; }
    const uint32_t now=millis();
    if ((uint32_t)(now-lastWifiRetryMs)>=WIFI_RETRY_MS &&
        strcmp(WIFI_SSID,"YOUR_WIFI_SSID")!=0) {
        lastWifiRetryMs=now;
        WiFi.reconnect();
    }
}

template<typename T>
void putArray(JsonObject root, const char *name, const T *values) {
    JsonArray a=root[name].to<JsonArray>();
    for (uint8_t i=0;i<MOTOR_COUNT;i++) a.add(values[i]);
}

void sendUdpTelemetryIfDue() {
    if (!udpStarted || !controllerKnown || !hasSession ||
        WiFi.status()!=WL_CONNECTED) return;
    const uint32_t now=millis();
    const uint32_t interval=forceTelemetry ? 50U : GOAL_TELEMETRY_MS;
    if ((uint32_t)(now-lastUdpTelemetryMs)<interval) return;
    lastUdpTelemetryMs=now;
    forceTelemetry=false;
    int32_t counts[MOTOR_COUNT];
    snapshotEncoderCounts(counts);
    JsonDocument doc;
    JsonObject root=doc.to<JsonObject>();
    root["type"]="telemetry";
    root["hardware_profile"]=HARDWARE_PROFILE;
    root["firmware_version"]=FIRMWARE_VERSION;
    root["pinmap_revision"]=PINMAP_REVISION;
    root["boot_id"]=bootId;
    root["session_id"]=sessionId;
    root["last_seq"]=lastSeq;
    root["uptime_ms"]=now;
    root["command_age_ms"]=(uint32_t)(now-lastAcceptedMs);
    root["mode"]=controlMode==MODE_NETWORK ? "NETWORK":"MANUAL_TEST";
    root["state"]=simpleStateName();
    root["fault"]="NONE";  // Rejected glitches are warnings, not permanent faults.
    root["last_fault"]=lastFault;
    root["fault_latched"]=false;
    root["cmd_vx"]=cmdVx;
    root["cmd_vy"]=cmdVy;
    root["cmd_w"]=cmdW;
    putArray(root,"wheel_target",targetWheelSpeed);
    putArray(root,"wheel_control_target",regulatedWheelTarget);
    putArray(root,"wheel_ff_pwm",wheelFeedforwardPwm);
    putArray(root,"wheel_pi_trim_pwm",wheelPiTrimPwm);
    putArray(root,"wheel_speed",measuredWheelSpeed);
    // Extra diagnostics for direction-dependent CW/CCW speed comparison.
    const float toRpm = 60.0f / (2.0f*(float)M_PI*WHEEL_RADIUS_CM);
    float goalRpm[MOTOR_COUNT], measuredRpm[MOTOR_COUNT];
    for (uint8_t i = 0; i < MOTOR_COUNT; ++i) {
        goalRpm[i] = targetWheelSpeed[i] * toRpm;
        measuredRpm[i] = measuredWheelSpeed[i] * toRpm;
    }
    putArray(root,"wheel_target_rpm",goalRpm);
    putArray(root,"wheel_measured_rpm",measuredRpm);
    int pwms[MOTOR_COUNT]={motors[0].currentPwm,motors[1].currentPwm,motors[2].currentPwm};
    putArray(root,"wheel_pwm",pwms);
    putArray(root,"encoder_count",counts);
    root["protocol_version"]=2;
    root["heartbeat_timeout_ms"]=HEARTBEAT_TIMEOUT_MS;
    root["velocity_ttl_ms"]=VELOCITY_TTL_MS;
    root["heartbeat_period_ms"]=HEARTBEAT_PERIOD_MS;
    if (hasHeartbeat) root["heartbeat_age_ms"]=(uint32_t)(now-lastHeartbeatMs);
    else root["heartbeat_age_ms"]=nullptr;
    if (hasVelocity) root["velocity_age_ms"]=(uint32_t)(now-lastVelocityMs);
    else root["velocity_age_ms"]=nullptr;
    root["controller_alive"]=controllerAlive;
    root["motion_permit"]=motionPermit;
    root["execution_token"]=executionToken;
    root["control_style"] = (benchRawPwm && simpleState == SIMPLE_VELOCITY) ? "PWM_BENCH_80" : "FEEDFORWARD_PI";
    root["encoder_policy"]=encoderPolicy;
    root["encoder_warning"]=encoderWarning;
    root["encoder_good_samples"]=encoderGoodSamples;
    root["encoder_recent_bad"]=encoderRecentBad;
    root["encoder_bad_samples"]=consecutiveBadTicks;
    root["fault_motor_mask"]=lastBadMotorMask;
    root["fault_sample_ms"]=lastBadAtMs;
    putArray(root,"fault_delta_count",lastBadDelta);
    root["stop_applied_ms"]=lastStopAppliedMs;
    root["goal_active"]=simpleState==SIMPLE_DISTANCE_ACTIVE || simpleState==SIMPLE_DISTANCE_BRAKING;
    root["goal_reached"]=simpleState==SIMPLE_GOAL_REACHED;
    if (goalContentValid) {
        root["motion_id"]=goalMotionId;
        root["goal_target_cm"]=goalTargetCm;
        root["goal_progress_cm"]=goalProgressCm;
        root["remaining_cm"]=goalRemainingCm;
        root["lateral_error_cm"]=goalLateralCm;
        root["overshoot_cm"]=goalOvershootCm;
    } else {
        root["motion_id"]=nullptr;
        root["goal_target_cm"]=nullptr;
        root["goal_progress_cm"]=nullptr;
        root["remaining_cm"]=nullptr;
        root["lateral_error_cm"]=nullptr;
        root["overshoot_cm"]=nullptr;
    }
    root["odom_dx_cm"]=goalOdomDxCm;
    root["odom_dy_cm"]=goalOdomDyCm;
    root["odom_dtheta_rad"]=goalOdomThetaRad;
    root["udp_received"]=udpReceived;
    root["udp_accepted"]=udpAccepted;
    root["udp_rejected"]=udpRejected;
    char output[2048];
    if (measureJson(doc)>=sizeof(output)) {
        Serial.println("[UDP] telemetry too large");
        return;
    }
    const size_t len=serializeJson(doc,output,sizeof(output));
    if (len>0 && len<sizeof(output) && udp.beginPacket(controllerIp,UDP_TELEMETRY_PORT)) {
        udp.write((const uint8_t *)output,len);
        udp.endPacket();
    }
}

// =====================================================
// 11) SERIAL DEBUG / MANUAL COMMANDS
// =====================================================

void printEncoderStatus() {
    int32_t counts[MOTOR_COUNT];
    snapshotEncoderCounts(counts);

    Serial.println();
    Serial.println("===== ENCODER =====");
    for (uint8_t i = 0; i < MOTOR_COUNT; i++) {
        printMotorName(i);
        Serial.print(" Count=");
        Serial.print(counts[i]);
        Serial.print(" RPM=");
        Serial.print(encoderRpm[i], 2);
        Serial.print(" speed=");
        Serial.print(measuredWheelSpeed[i], 2);
        Serial.println(" cm/s");
    }
    Serial.print("COUNTS/REV=");
    Serial.println(COUNTS_PER_OUTPUT_REV, 2);
    Serial.println("===================");
}

void printStatus() {
    Serial.println();
    Serial.println("===== STATUS =====");
    Serial.print("MODE = ");
    Serial.println(controlMode == MODE_NETWORK ? "NETWORK" : "MANUAL_PWM");

    Serial.print("WiFi = ");
    if (WiFi.status() == WL_CONNECTED) {
        Serial.print("CONNECTED  IP=");
        Serial.println(WiFi.localIP());
    } else {
        Serial.println("DISCONNECTED");
    }

    Serial.print("UDP = ");
    Serial.println(udpStarted ? "ON" : "OFF");
    Serial.print("NET cmd_vel output = ");
    Serial.println(benchRawPwm ? "PWM_BENCH_80 (lifted wheel only)" : "FEEDFORWARD_PI (cmd_vel + cmd_move)");

    Serial.print("cmd = (");
    Serial.print(cmdVx, 1);
    Serial.print(", ");
    Serial.print(cmdVy, 1);
    Serial.print(", ");
    Serial.print(cmdW, 2);
    Serial.print(") status=");
    Serial.print(cmdStatus);
    Serial.print(" seq=");
    Serial.println(lastSeq);

    const uint32_t age = lastCommandMs == 0 ? 0xFFFFFFFFUL : millis() - lastCommandMs;
    Serial.print("command age ms = ");
    if (lastCommandMs == 0) Serial.println("NONE");
    else Serial.println(age);

    for (uint8_t i = 0; i < MOTOR_COUNT; i++) {
        printMotorName(i);
        Serial.print(" target=");
        Serial.print(targetWheelSpeed[i], 2);
        Serial.print(" cm/s ctl=");
        Serial.print(regulatedWheelTarget[i], 2);
        Serial.print(" measured=");
        Serial.print(measuredWheelSpeed[i], 2);
        Serial.print(" FF=");
        Serial.print(wheelFeedforwardPwm[i], 1);
        Serial.print(" PI=");
        Serial.print(wheelPiTrimPwm[i], 1);
        Serial.print(" pwm=");
        Serial.println(motors[i].currentPwm);
    }

    Serial.println("==================");
}

void printPinMap() {
    Serial.println();
    Serial.println("===== PIN MAP =====");
    Serial.printf("hardware_profile=%s firmware_version=%s pinmap_revision=%s\n",
                  HARDWARE_PROFILE, FIRMWARE_VERSION, PINMAP_REVISION);
    Serial.printf("STBY=%u\n", PIN_STBY);
    const MotorPins motorPins[] = {HARDWARE_PINS.m1, HARDWARE_PINS.m2, HARDWARE_PINS.m3};
    for (unsigned i = 0; i < MOTOR_COUNT; ++i) {
        const MotorPins &pins = motorPins[i];
        Serial.printf("M%u IN1=%u IN2=%u PWM=%u ENC_A=%u ENC_B=%u\n",
                      i + 1, pins.in1, pins.in2, pins.pwm, pins.encA, pins.encB);
    }
    Serial.println("===================");
}

void printHelp() {
    Serial.println();
    Serial.println("===== COMMANDS =====");
    Serial.println("AUTO                -> network control mode");
    Serial.println("M1 100              -> manual PWM mode");
    Serial.println("M2 -100");
    Serial.println("M3 100");
    Serial.println("ALL 100 100 100     -> manual PWM mode");
    Serial.println("STOP                -> manual STOP; use AUTO to resume network");
    Serial.println("ENC");
    Serial.println("ZERO");
    Serial.println("STREAM ON");
    Serial.println("STREAM OFF");
    Serial.println("BENCH ON            -> fixed PWM 80 for cmd_vel ONLY (lifted wheels!)");
    Serial.println("BENCH OFF           -> Feedforward+PI for cmd_vel AND cmd_move");
    Serial.println("STATUS");
    Serial.println("PIN");
    Serial.println("HELP");
    Serial.println("====================");
}

void handleSingleMotorCommand(const String &command) {
    int motorNumber = 0;
    int pwm = 0;
    if (sscanf(command.c_str(), "M%d %d", &motorNumber, &pwm) != 2) {
        Serial.println("ERROR: use M1 100");
        return;
    }
    if (motorNumber < 1 || motorNumber > 3) {
        Serial.println("ERROR: motor must be 1~3");
        return;
    }

    if (controlMode != MODE_MANUAL_PWM) stopNetworkMotion();
    controlMode = MODE_MANUAL_PWM;
    lastManualPwmAtMs = millis();
    resetPidState();
    writeMotorPwm((uint8_t)(motorNumber - 1), pwm, true);
    Serial.println("[MODE] MANUAL_PWM");
}

void handleAllMotorCommand(const String &command) {
    int m1 = 0, m2 = 0, m3 = 0;
    if (sscanf(command.c_str(), "ALL %d %d %d", &m1, &m2, &m3) != 3) {
        Serial.println("ERROR: use ALL 100 100 100");
        return;
    }
    if (controlMode != MODE_MANUAL_PWM) stopNetworkMotion();
    setAllMotorsManual(m1, m2, m3);
    lastManualPwmAtMs = millis();
    resetPidState();
    Serial.println("[MODE] MANUAL_PWM");
}

void enterNetworkMode() {
    stopNetworkMotion();
    controlMode = MODE_NETWORK;
    cmdVx = cmdVy = cmdW = 0.0f;
    cmdStatus = "STOP";
    lastCommandMs = 0; // 반드시 새 packet을 받아야 재주행
    benchRawPwm = false;  // AUTO always resets the diagnostic mode
    networkStopped = true;

    int32_t current[MOTOR_COUNT];
    snapshotEncoderCounts(current);
    for (uint8_t i = 0; i < MOTOR_COUNT; i++) {
        previousControlCounts[i] = current[i];
        piIntegral[i] = 0.0f;
        regulatedWheelTarget[i] = 0.0f;
        wheelFeedforwardPwm[i] = wheelPiTrimPwm[i] = 0.0f;
    }
    lastControlMs = millis();

    hasHandshake = false;
    tokenActive = false;
    hasHeartbeat = false;
    Serial.println("[MODE] NETWORK - waiting for a fresh UDP STOP handshake");
}

void handleSerialCommand() {
    if (Serial.available() <= 0) return;

    String command = Serial.readStringUntil('\n');
    command.trim();
    command.toUpperCase();
    if (command.length() == 0) return;

    if (command.startsWith("M1 ") || command.startsWith("M2 ") || command.startsWith("M3 ")) {
        handleSingleMotorCommand(command);
    } else if (command.startsWith("ALL ")) {
        handleAllMotorCommand(command);
    } else if (command == "STOP") {
        stopNetworkMotion();
        controlMode = MODE_MANUAL_PWM;
        lastManualPwmAtMs = millis();
        stopAllMotors(true);
        Serial.println("[MODE] MANUAL_PWM. Send AUTO to resume network control.");
    } else if (command == "AUTO") {
        enterNetworkMode();
    } else if (command == "ENC") {
        printEncoderStatus();
    } else if (command == "ZERO") {
        zeroEncoders();
    } else if (command == "BENCH ON" || command == "BENCH OFF") {
        // Never switch feedback/output modes while a motor is moving.
        bool outputsZero = true;
        for (uint8_t i = 0; i < MOTOR_COUNT; ++i)
            if (motors[i].currentPwm != 0) outputsZero = false;
        if (controlMode != MODE_NETWORK || isNetworkMoving() || !outputsZero) {
            Serial.println("[BENCH] STOP motors and enter AUTO/NETWORK before changing mode");
        } else {
            benchRawPwm = (command == "BENCH ON");
            resetPidState();
            Serial.println(benchRawPwm ?
                "[BENCH] ON: cmd_vel = constant PWM 80 per commanded wheel, lifted wheels ONLY" :
                "[BENCH] OFF: cmd_vel/cmd_move = shared feedforward + PI");
        }
    } else if (command == "STREAM ON") {
        streamEnabled = true;
        Serial.println("Encoder stream ON");
    } else if (command == "STREAM OFF") {
        streamEnabled = false;
        Serial.println("Encoder stream OFF");
    } else if (command == "STATUS") {
        printStatus();
    } else if (command == "PIN") {
        printPinMap();
    } else if (command == "HELP") {
        printHelp();
    } else {
        Serial.println("Unknown command. Send HELP.");
    }
}


// =====================================================
// 12) INITIALIZATION
// =====================================================

bool initializeMotor(Motor &motor) {
    pinMode(motor.in1, OUTPUT);
    pinMode(motor.in2, OUTPUT);
    digitalWrite(motor.in1, LOW);
    digitalWrite(motor.in2, LOW);

    const bool attached = ledcAttach(motor.pwmPin, PWM_FREQUENCY, PWM_RESOLUTION);
    if (!attached) return false;

    ledcWrite(motor.pwmPin, 0);
    motor.currentPwm = 0;
    return true;
}

void initializeEncoders() {
    // GPIO34/35/36/39 are input-only without an internal pull-up.
    const uint8_t encoderPins[] = {M1_ENC_A, M1_ENC_B, M2_ENC_A, M2_ENC_B, M3_ENC_A, M3_ENC_B};
    for (uint8_t pin : encoderPins) {
        pinMode(pin, inputOnly(pin) ? INPUT : INPUT_PULLUP);
    }

    attachInterrupt(digitalPinToInterrupt(M1_ENC_A), encoder1ISR, RISING);
    attachInterrupt(digitalPinToInterrupt(M2_ENC_A), encoder2ISR, RISING);
    attachInterrupt(digitalPinToInterrupt(M3_ENC_A), encoder3ISR, RISING);

    zeroEncoders();
}

void setup() {
    Serial.begin(SERIAL_BAUD);
    Serial.setTimeout(30);
    delay(400);

    pinMode(PIN_STBY, OUTPUT);
    digitalWrite(PIN_STBY, LOW);

    bool ok = true;
    for (uint8_t i = 0; i < MOTOR_COUNT; i++) {
        if (!initializeMotor(motors[i])) {
            Serial.print("ERROR: M");
            Serial.print(i + 1);
            Serial.println(" PWM initialization failed");
            ok = false;
        }
    }

    if (!ok) {
        digitalWrite(PIN_STBY, LOW);
        Serial.println("Motor driver disabled due to PWM init failure");
        while (true) delay(1000);
    }

    initializeEncoders();
    bootId = esp_random();
    if (bootId == 0) bootId = 1;

    // 모든 PWM=0 확인 후 TB6612 활성화
    digitalWrite(PIN_STBY, HIGH);
    stopAllMotors(false);

    beginWiFi();

    Serial.println();
    Serial.println("============================================");
    Serial.println("D.I.G / GRISE Wi-Fi omni CONTROL SIMPLE V2 ready");
    Serial.println("Default mode: NETWORK");
    Serial.println("UDP V2: stop / heartbeat / cmd_vel / cmd_move");
    Serial.println("Ports: command 8888 / telemetry 8889");
    Serial.println("Shared Feedforward + PI: cmd_vel + cmd_move");
    Serial.println("Heartbeat 600 ms / velocity TTL 300 ms");
    Serial.println("Send HELP for serial debug commands");
    Serial.println("============================================");
}

// =====================================================
// 13) MAIN LOOP
// =====================================================
void loop() {
    handleSerialCommand();
    maintainWiFi();
    receiveUdpCommands();
    updateNetworkControl();
    updateEncoderTelemetry();
    sendUdpTelemetryIfDue();
    if (streamEnabled && (uint32_t)(millis()-lastStatusPrintMs)>=1000U) {
        lastStatusPrintMs=millis();
        printStatus();
    }
    delay(0);
}
