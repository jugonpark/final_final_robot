#pragma once

#include <stdint.h>

#if defined(DIG_BOARD_BREADBOARD) && defined(DIG_BOARD_PCB_V19)
#error Select only one hardware profile: DIG_BOARD_BREADBOARD or DIG_BOARD_PCB_V19
#endif

struct MotorPins {
    uint8_t in1, in2, pwm, encA, encB;
};

struct HardwarePins {
    uint8_t stby;
    MotorPins m1, m2, m3;
};

constexpr char FIRMWARE_VERSION[] = "SIMPLE_V2";

#if !defined(DIG_BOARD_PCB_V19)
constexpr char HARDWARE_PROFILE[] = "BREADBOARD";
constexpr char PINMAP_REVISION[] = "BREADBOARD_GPIO_V1";
constexpr HardwarePins HARDWARE_PINS = {
    27, {19, 18, 25, 34, 35}, {21, 22, 26, 16, 17}, {23, 13, 14, 32, 33}
};
#else
constexpr char HARDWARE_PROFILE[] = "PCB_V19";
constexpr char PINMAP_REVISION[] = "PCB_V19_GPIO_V1";
constexpr HardwarePins HARDWARE_PINS = {
    27, {18, 19, 25, 34, 35}, {21, 22, 26, 36, 39}, {23, 13, 14, 16, 17}
};
#endif

constexpr bool inputOnly(uint8_t pin) {
    return pin >= 34 && pin <= 39;
}

constexpr bool motorPinsValid(const HardwarePins &pins) {
    if (inputOnly(pins.stby)) {
        return false;
    }
    const MotorPins motors[] = {pins.m1, pins.m2, pins.m3};
    for (const MotorPins &motor : motors) {
        if (inputOnly(motor.in1) || inputOnly(motor.in2) || inputOnly(motor.pwm)) {
            return false;
        }
    }
    const uint8_t assigned[] = {
        pins.stby,
        pins.m1.in1, pins.m1.in2, pins.m1.pwm, pins.m1.encA, pins.m1.encB,
        pins.m2.in1, pins.m2.in2, pins.m2.pwm, pins.m2.encA, pins.m2.encB,
        pins.m3.in1, pins.m3.in2, pins.m3.pwm, pins.m3.encA, pins.m3.encB,
    };
    for (unsigned i = 0; i < sizeof(assigned); ++i) {
#ifdef DIG_BOARD_PCB_V19
        if (assigned[i] == 32 || assigned[i] == 33) {  // VBAT_ADC and NC on the V19 header
            return false;
        }
#endif
        for (unsigned j = 0; j < i; ++j) {
            if (assigned[i] == assigned[j]) {
                return false;
            }
        }
    }
    return true;
}

static_assert(motorPinsValid(HARDWARE_PINS), "Invalid motor GPIO assignment");
