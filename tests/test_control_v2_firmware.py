"""Compile-time checks of the candidate's actual pure C++ lifetime/sample helpers.

This invokes the installed ESP32 C++ compiler without a board, serial port, Wi-Fi,
or upload. It is compile-only evidence; it does not execute firmware on hardware.
"""
import os
import re
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
CANDIDATE = ROOT / "esp32_firmware" / "candidates" / "control_v2"


class ControlV2FirmwareTests(unittest.TestCase):
    def test_manual_mode_guard_precedes_all_command_acceptance(self):
        sketch = (CANDIDATE / "control_v2.ino").read_text(encoding="utf-8")
        handler = sketch.split("void handleAcceptedCommand(", 1)[1].split(
            "NormalizedCommand manualCommand(", 1)[0]
        guard = handler.split("    const AcceptanceResult result =", 1)[0]
        self.assertIn("if (fromNetwork && safetyState.mode != ControllerMode::NETWORK)", guard)
        non_stop = guard.split("if (command.type != CommandType::STOP)", 1)[1].split("}", 1)[0]
        self.assertIn("++udpRejected;", non_stop)
        self.assertIn("return;", non_stop)
        self.assertNotIn("acceptCommand(", guard)
        self.assertNotIn("rememberController(", guard)
        self.assertNotIn("tryResetFault(", guard)
        self.assertIn("manualPwmActive = false;", guard)
        self.assertIn("immediateStopNow(safetyState.fault,", guard)
        self.assertIn("? command.receivedAtMs : safetyState.faultAtMs", guard)
        self.assertIn("commandMailbox, commandAcceptance);", guard)
        # STOP leaves the guard before session/sequence/IP acceptance can run.
        self.assertTrue(guard.rstrip().endswith("return;\n    }"))
        for field in ("commandAcceptance", "safetyState.fault", "controllerIp"):
            self.assertNotRegex(guard, re.escape(field) + r"\s*=(?!=)")
        safety = (CANDIDATE / "safety_telemetry.cpp").read_text(encoding="utf-8")
        stop = safety.split("void immediateStopNow(", 1)[1].split("void raiseFault(", 1)[0]
        self.assertLess(stop.index("stopAllMotorsImmediate();"), stop.index("retireExecution(acceptance);"))
        self.assertIn("safetyState.faultAtMs = nowMs;", stop)

    def test_cpp_clock_and_encoder_invariants(self):
        explicit = os.environ.get("CONTROL_V2_CXX")
        compilers = ([Path(explicit)] if explicit else sorted(
            (Path(os.environ.get("LOCALAPPDATA", "")) / "Arduino15" / "packages" /
             "esp32" / "tools" / "esp-x32").glob("*/bin/xtensa-esp32-elf-g++.exe"),
            reverse=True))
        if not compilers or not compilers[0].is_file():
            self.skipTest("ESP32 C++ compiler unavailable; firmware invariants NOT VERIFIED")
        # The production header contains static_assert checks for exact deadlines,
        # millis/count rollover, isolated vs repeated noise, and distance abort.
        result = subprocess.run(
            [str(compilers[0]), "-std=gnu++17", "-fsyntax-only", "-x", "c++",
             "-I", str(CANDIDATE), "-"],
            input='#include "control_v2_math.h"\n', text=True,
            capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
