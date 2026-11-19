# D.I.G. Blue UI implementation plan

**Goal:** Implement the supplied Blue kit in the existing PySide6 application.
**Spec:** `design_reference/dig_blue/docs/UI_SPEC.md` and `CODEX_TASK.md`.
**Architecture:** Keep existing widgets, signals, tab indices and control methods;
hide legacy tab bars behind a five-item sidebar. Reparent settings and the single
camera widget. All live rendering reads the existing engine snapshot.

## Constraints
- Only visualization code, resources, UI tests and documentation change.
- Preserve STOP, pressed/released, ownership transfer and approval gates.
- Startup selects AI visually without connecting or claiming a hardware mode.
- Diagnostic/settings navigation must not claim a control mode.
- Preserve a visible radar/STOP header at 940×680, 1320×920 and 1600×980.
- No model/network calls or illustrative camera data in presentation code.

## Steps
- [x] Record baseline; add sidebar, navigation/hold and safety regression tests.
- [x] Port SVG/QSS resources and build sidebar/header around legacy tabs.
- [x] Rebuild single-request AI view with validated plan steps and existing gates.
- [x] Separate camera modes and their actual gesture mappings; retain one camera.
- [x] Reorganize manual pad, diagnostic subpages and settings without dropping controls.
- [x] Run full suite, compare baseline failures and inspect captures at three sizes.

## Review focus
- Rejected transfer: visual page remains distinct from actual owner.
- Entering diagnostics while a wheel-test tab was selected: stop hold, no new claim.
- Small windows and long safety strings: scroll content while header stays visible.
- Modified input and rotation plans: no stale approval or executable rotation.
- Missing camera/radar/telemetry: preserve unknown and blocked states.

## Execution notes
User provided the visual direction, platform and full scope; implement directly.
Keep compatibility tab objects rather than introducing a second navigation state.
No commit/push requested. Physical device testing is outside UI verification.
