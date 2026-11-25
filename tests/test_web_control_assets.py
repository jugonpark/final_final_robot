"""Executable checks for browser liveness and nonblocking operator confirmation."""
from pathlib import Path
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]


class WebControlAssetsTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node is required for browser JavaScript checks')
    def test_operator_lease_and_confirmation_keep_running_without_dom(self):
        script = r'''
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('python_controller/web/app.js', 'utf8');
const helpers = source.slice(source.indexOf('let operatorHealthy'), source.indexOf('function text('));
const pending = [];
let releases = 0;
const dialog = {open: false, returnValue: '',
  addEventListener(event, callback) { this.closed = callback; },
  showModal() { this.open = true; },
  close(value) { this.open = false; this.returnValue = value; this.closed(); },
};
const context = vm.createContext({
  online: true, state: {control_available: true, runtime_kind: 'browser_control', operator_ready: true},
  document: {hidden: false}, AbortSignal,
  fetchJson(url, options) {
    assert.equal(url, '/api/control');
    assert.equal(options.method, 'POST');
    assert.equal(JSON.parse(options.body).action, 'operator_heartbeat');
    return new Promise((resolve, reject) => pending.push({resolve, reject}));
  },
  releaseJog() { releases++; }, render() {}, text() {}, byId() { return dialog; },
});
const run = code => vm.runInContext(code, context);
run(helpers);
async function check() {
  assert.equal(run('operatorReady()'), false);
  let heartbeat = run('operatorHeartbeat()');
  await run('operatorHeartbeat()');
  assert.equal(pending.length, 1, 'heartbeat requests must not overlap');
  pending.shift().resolve({operator_ready: true});
  await heartbeat;
  assert.equal(run('operatorReady()'), true);
  let decided = false;
  const confirmation = run("confirmControl('현장 확인')").then(value => { decided = true; return value; });
  assert.equal(dialog.open, true);
  heartbeat = run('operatorHeartbeat()');
  assert.equal(pending.length, 1, 'operator heartbeat continues while confirmation is open');
  pending.shift().resolve({operator_ready: true});
  await heartbeat;
  assert.equal(decided, false);
  dialog.close('confirm');
  assert.equal(await confirmation, true);
  heartbeat = run('operatorHeartbeat()');
  context.document.hidden = true;
  run('suspendOperator()');
  pending.shift().resolve({operator_ready: true});
  await heartbeat;
  assert.equal(run('operatorReady()'), false, 'late response cannot restore a hidden operator');
  await run('operatorHeartbeat()');
  assert.equal(pending.length, 0, 'hidden tabs cannot renew the lease');
  context.document.hidden = false;
  heartbeat = run('operatorHeartbeat()');
  pending.shift().resolve({operator_ready: true});
  await heartbeat;
  const interrupted = run("confirmControl('확인 중 연결 상실')");
  heartbeat = run('operatorHeartbeat()');
  pending.shift().reject(new Error('offline'));
  await heartbeat;
  dialog.close('confirm');
  assert.equal(await interrupted, false, 'approval fails closed if operator liveness is lost');
  assert.equal(run('operatorReady()'), false);
  assert.ok(releases >= 2);
  context.state.runtime_kind = 'qt_shared';
  assert.equal(run('operatorReady()'), true, 'Qt keeps its existing operator source');
  await run('operatorHeartbeat()');
  assert.equal(pending.length, 0, 'Qt must not use browser operator heartbeats');
  context.state.control_available = false;
  assert.equal(run('operatorReady()'), false, 'simulation never acquires hardware controls');
  assert.ok(!source.includes('window.confirm('), 'control confirmations must not freeze heartbeat timers');
}
check().catch(error => { console.error(error); process.exitCode = 1; });
'''
        result = subprocess.run(['node', '-e', script], cwd=ROOT, capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'Node is required for browser JavaScript checks')
    def test_camera_intent_survives_navigation_and_owner_switch_is_explicit(self):
        script = r'''
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('python_controller/web/app.js', 'utf8');
const requests = [], modes = [], nodes = new Map();
let frames = 0, finishMode;
const context = vm.createContext({
  page: 'ai', cameraMode: 'direct', online: false, state: null,
  crypto: require('node:crypto').webcrypto, AbortSignal, Promise,
  document: {hidden: false}, nav: [], panels: [], cameraButtons: [],
  window: {scrollTo() {}}, render() {}, releaseJog() {}, resetRoute() {},
  refreshHistory() {}, refreshKnowledge() {}, clearFrame() {}, text() {}, setTimeout() {},
  byId(id) {
    if (!nodes.has(id)) nodes.set(id, {checked: false, setAttribute() {}});
    return nodes.get(id);
  },
  fetchJson(url, options) {
    requests.push({url, body: options && JSON.parse(options.body)});
    return Promise.resolve({image: 'mock frame', lease_active: true});
  },
  renderFrame() { frames++; },
  command(action, extra) { modes.push({action, ...extra}); return new Promise(resolve => { finishMode = resolve; }); },
});
const run = code => vm.runInContext(code, context);
for (const [start, end] of [
  ['const cameraViewer', 'let routePoints'],
  ['let operatorHealthy', 'function text('],
  ['function selectPage(', 'function renderPlan('],
  ['function syncCameraPower(', 'function renderFrame('],
  ['async function pollCamera(', 'function jogRequest('],
]) run(source.slice(source.indexOf(start), source.indexOf(end)));
async function check() {
  run("openFeature('camera')");
  await run('cameraPower');
  assert.equal(context.page, 'camera', 'feature navigation works offline');
  assert.equal(run('cameraAllowed'), false, 'camera starts off even on camera page');
  assert.equal(requests.length, 0);
  context.online = true;
  context.state = {control_available: true, session_open: true, operator_ready: true,
    runtime_kind: 'browser_control', owner: 'GESTURE_DIRECT', pending_mode: null};
  run('operatorHealthy = true; cameraAllowed = true; syncCameraPower()');
  await run('cameraPower');
  assert.equal(requests.at(-1).body.enabled, true);
  run("openFeature('ai')");
  await run('cameraPower');
  assert.equal(run('cameraWanted'), true, 'AI navigation retains explicit camera intent');
  await run('pollCamera()');
  assert.match(requests.at(-1).url, /^\/api\/camera\?viewer=/, 'background viewing renews the vision lease');
  assert.equal(frames, 0, 'hidden camera panel does not render camera frames');
  run("openFeature('camera')");
  await run('pollCamera()');
  assert.equal(frames, 1);
  context.document.hidden = true;
  run('syncCameraPower()');
  await run('cameraPower');
  assert.equal(requests.at(-1).body.enabled, false, 'hidden tab releases its camera lease');
  const beforeHidden = requests.length;
  await run('pollCamera()');
  assert.equal(requests.length, beforeHidden);
  context.document.hidden = false;
  run("openFeature('ai')");
  await run('cameraPower');
  assert.equal(requests.at(-1).body.enabled, true);
  run('cameraAllowed = false; syncCameraPower()');
  await run('cameraPower');
  assert.equal(requests.at(-1).body.enabled, false, 'explicit off works from the AI page');
  assert.equal(modes.length, 0, 'navigation and camera lifecycle never change motor ownership');
  assert.equal(context.state.owner, 'GESTURE_DIRECT');
  assert.equal(run("canSwitchOwner('LLM_COMMAND')"), true);
  assert.equal(run("canSwitchOwner('GESTURE_DIRECT')"), false);
  assert.equal(run("canSwitchOwner('MOTOR_TEST')"), false);
  for (const field of ['session_open', 'control_available', 'operator_ready']) {
    context.state[field] = false;
    assert.equal(run("canSwitchOwner('LLM_COMMAND')"), false, field);
    context.state[field] = true;
  }
  context.state.pending_mode = 'LLM_COMMAND';
  assert.equal(await run("switchOwner('LLM_COMMAND')"), false);
  context.state.pending_mode = null;
  const change = run("switchOwner('LLM_COMMAND')");
  assert.equal(await run("switchOwner('LLM_COMMAND')"), false, 'in-flight mode request cannot overlap');
  assert.deepEqual(modes, [{action: 'select_mode', mode: 'LLM_COMMAND'}]);
  finishMode(true);
  assert.equal(await change, true);
  context.state.owner = 'LLM_COMMAND';
  const back = run("switchOwner('GESTURE_DIRECT')");
  assert.equal(modes.at(-1).mode, 'GESTURE_DIRECT');
  finishMode(true);
  await back;
}
check().catch(error => { console.error(error); process.exitCode = 1; });
'''
        result = subprocess.run(['node', '-e', script], cwd=ROOT, capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
