'use strict';

const byId = (id) => document.getElementById(id);
const nav = [...document.querySelectorAll('[data-page]')];
const panels = [...document.querySelectorAll('[data-panel]')];
const cameraButtons = [...document.querySelectorAll('[data-camera]')];
const prompt = byId('prompt');
const unknown = '미확인';
const stepStatus = {
  WAITING: '대기', MOVING: '이동 수락 · 진행 중', GOAL_REPORTED: '목표 도달 보고 · STOP 확인 중',
  STOP_VERIFIED: 'STOP 확인 · 단계 완료', BLOCKED: '실행 중단',
};
const runStatus = {
  EXECUTING: '실행 진행 중', STOPPING: '정지 요청 · 응답 대기',
  COMPLETED: '모든 단계 STOP 확인 · 완료', ABORTED: '실행 중단',
};
const fingers = [[0,1,2,3,4],[0,5,6,7,8],[5,9,10,11,12],[9,13,14,15,16],[13,17,18,19,20],[17,0]];
const names = {
  Open_Palm: '손바닥 펼침', Closed_Fist: '주먹', Thumb_Up: '엄지 올림', Thumb_Down: '엄지 내림', Victory: '브이 손동작',
  FORWARD: '전진', BACKWARD: '후진', LEFT: '좌측', RIGHT: '우측', TURN_LEFT: '좌회전', TURN_RIGHT: '우회전', STOP: '정지',
  RESUME: '추종 재개', PAUSE: '일시정지', CLOSER: '목표 간격 감소', FARTHER: '목표 간격 증가',
  'NOT TESTED': '미검증', Camera: '카메라', 'Target tracking': '대상 추적', 'Four gestures': '제스처 확인',
  'Follow command directions': '추종 방향', 'ESP32 communication': 'ESP32 통신',
  'Lifted-wheel motors': '바퀴 공중 고정 시험', 'Controlled floor drive': '통제 공간 주행',
};
let page = 'ai';
let cameraMode = 'direct';
let state = null;
let frameToken = null;
let online = false;
let stateRequest = 0;
let planBusy = false;
let lastPlan = '';
let stopPending = false;
let lastNavigation = 0;
const cameraViewer = crypto.randomUUID().replaceAll('-', '');
let cameraAllowed = true;
let cameraWanted = false;
let cameraVersion = 0;
let cameraRegistered = false;
let cameraPower = Promise.resolve();

function text(id, value) {
  const node = byId(id);
  if (node.textContent !== String(value)) node.textContent = value;
}
const name = (v) => names[v] ?? v ?? unknown;
const measured = (value, unit = '') => Number.isFinite(value) ? `${Number(value.toFixed(2))}${unit}` : '미수신';

function readout(id, pairs) {
  const root = byId(id);
  const next = JSON.stringify(pairs);
  if (root.dataset.content === next) return;
  root.dataset.content = next;
  root.replaceChildren(...pairs.flatMap(([label, value]) => {
    const dt = document.createElement('dt');
    const dd = document.createElement('dd');
    dt.textContent = label;
    dd.textContent = value ?? unknown;
    return [dt, dd];
  }));
}

async function fetchJson(url, options) {
  const response = await fetch(url, { cache: 'no-store', signal: AbortSignal.timeout(2500), ...options });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || '요청이 실패했습니다.');
  return data;
}

async function command(action, extra = {}) {
  try {
    const data = await fetchJson('/api/control', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ action, ...extra }) });
    text('announcement', data.message);
    await refreshState();
    return true;
  } catch (error) {
    text('announcement', error.message === 'Failed to fetch' ? '서버와 연결할 수 없습니다.' : error.message);
    await refreshState();
    return false;
  }
}

async function stop() {
  if (stopPending) return;
  stopPending = true;
  try {
    if (!await command('estop')) text('announcement', '비상 정지 요청 실패 · 서버 연결을 확인하세요.');
  } finally {
    stopPending = false;
  }
}

function selectPage(pageId) {
  page = pageId;
  for (const button of nav) {
    if (button.dataset.page === page) {
      button.setAttribute('aria-current', 'page');
      text('page-title', button.textContent.trim());
    } else button.removeAttribute('aria-current');
  }
  for (const panel of panels) panel.hidden = panel.dataset.panel !== page;
  syncCameraPower();
  window.scrollTo(0, 0);
  render();
}

function renderPlan() {
  const agent = state?.agent;
  const plan = agent?.card ?? (!agent && state?.plan?.text === prompt.value.trim() ? state.plan : null);
  const waiting = Boolean(agent?.waiting_mode_plan);
  byId('plan-waiting').hidden = !waiting;
  byId('plan-empty').hidden = Boolean(plan) || waiting;
  byId('plan-form').querySelector('button').disabled = planBusy || !online || !prompt.value.trim();
  byId('discard').disabled = !online || planBusy || (agent ? agent.busy || !agent.card : !state?.plan);
  const key = JSON.stringify(plan);
  if (key === lastPlan) return;
  lastPlan = key;
  const requestRow = byId('user-request-row');
  requestRow.hidden = !plan || Boolean(agent);
  text('user-request', plan?.text ?? '');
  if (!requestRow.hidden) {
    const thread = byId('chat-thread');
    thread.scrollTop = thread.scrollHeight;
  }
  byId('rotate').hidden = !plan?.rotation;
  const steps = (plan?.steps ?? []).map((step) => {
    const li = document.createElement('li');
    li.textContent = step;
    return li;
  });
  byId('plan-steps').replaceChildren(...steps);
  text('plan-distance', plan ? (agent ? `계획 버전 ${plan.revision} · ${steps.length}단계` : `${steps.length}단계 · 총 이동 거리 ${Number(plan.distance_cm)} cm`) : '');
}

function renderProgress(progress) {
  const steps = byId('execution-steps');
  const key = JSON.stringify(progress ?? null);
  if (steps.dataset.content === key) return;
  steps.dataset.content = key;
  if (!progress) {
    text('execution-summary', '실행 기록이 없습니다.');
    steps.replaceChildren();
    return;
  }
  const count = progress.steps.length;
  const summary = [`계획 v${progress.revision}`, runStatus[progress.state]];
  if (count) summary.push(`단계 STOP 확인 ${progress.verified_count}/${count}`);
  if (progress.last_error) summary.push(progress.last_error);
  text('execution-summary', summary.join(' · '));
  steps.replaceChildren(...progress.steps.map((state, index) => {
    const step = document.createElement('li');
    const status = document.createElement('small');
    step.dataset.state = state;
    step.textContent = progress.labels[index];
    status.textContent = stepStatus[state];
    step.append(status);
    return step;
  }));
}

function chatMessage(row) {
  const user = row.role === 'user';
  const message = document.createElement('div');
  message.className = user ? 'dm-message dm-user' : 'dm-message';
  const avatar = document.createElement(user ? 'span' : 'img');
  avatar.className = user ? 'dm-avatar user-avatar' : 'dm-avatar mascot';
  if (user) {
    avatar.textContent = '나';
    avatar.setAttribute('aria-hidden', 'true');
  } else {
    avatar.src = '/dig-avatar-smile.png';
    avatar.alt = '';
  }
  const content = document.createElement('div');
  content.className = 'dm-content';
  const author = document.createElement('span');
  author.className = 'dm-author';
  author.textContent = user ? '나' : 'D.I.G.';
  const bubble = document.createElement('p');
  bubble.className = 'bubble';
  bubble.textContent = row.content;
  content.append(author, bubble);
  message.append(avatar, content);
  return message;
}

function renderAgent(agent) {
  const history = byId('agent-history');
  history.hidden = !agent;
  byId('execution-progress').hidden = !agent;
  if (!agent) return;
  renderProgress(agent.progress);
  const thread = byId('chat-thread');
  const followLatest = thread.scrollHeight - thread.clientHeight - thread.scrollTop < 32;
  text('chat-mode', 'Agent 대화');
  text('chat-welcome', '관제 상태를 묻거나 화면 이동과 이동 계획을 요청하세요.');
  text('chat-submit', agent.busy ? '처리 중' : '전송');
  const key = JSON.stringify(agent.messages);
  if (history.dataset.content !== key) {
    const previous = JSON.parse(history.dataset.content ?? '[]');
    history.dataset.content = key;
    if (JSON.stringify(agent.messages.slice(0, previous.length)) === JSON.stringify(previous)) {
      history.append(...agent.messages.slice(previous.length).map(chatMessage));
    } else {
      history.replaceChildren(...agent.messages.map(chatMessage));
    }
  }
  const thinking = byId('agent-thinking');
  if (thinking.hidden === agent.busy) thinking.hidden = !agent.busy;
  if (followLatest && (page === 'ai')) thread.scrollTop = thread.scrollHeight;
  if (agent.navigation_id !== lastNavigation) {
    lastNavigation = agent.navigation_id;
    if (agent.page) selectPage(agent.page === 'ai_chat' ? 'ai' : agent.page);
  }
}

function render() {
  const connected = online && state;
  const radar = connected ? state.radar : null;
  const stopProof = connected && state.stop_verified ? 'ESP32 정지 확인' : 'ESP32 정지 미확인';
  const linked = connected ? (state.connected ? '연결됨' : '미연결') : unknown;
  const source = !connected ? '서버 연결 끊김' : state.source === 'SYNTHETIC_PREVIEW' ? '시뮬레이션 · 합성 영상' : '시뮬레이션 · 실제 카메라';
  text('source', source);
  text('owner', connected ? `제어권 · ${state.owner}` : '제어권 미확인');
  text('esp32', `ESP32 · ${linked}`);
  text('rail-stop', connected ? state.stop_reason : '정지 상태 미확인');
  const cameraStatus = { CAMERA_OFF: '꺼짐', CLOSING_CAMERA: '해제 중', CAMERA_ERROR: '오류',
    CAPTURING: state?.source === 'SYNTHETIC_PREVIEW' ? '합성' : '수신 중' };
  text('camera-badge', `카메라 · ${!connected ? unknown : (cameraStatus[state.camera_status] ?? '대기')}`);
  text('radar', `레이더 ${radar?.state ?? 'UNKNOWN'} · ${radar?.blocked === false ? '차단 해제' : '이동 차단'}${connected && state.estop_latched ? ' · 긴급 정지' : ''}`);
  text('stop-proof', stopProof);
  byId('safety').dataset.tone = (connected && state.estop_latched) || radar?.state === 'EVADE' ? 'danger' : radar?.blocked === false ? 'ok' : 'warn';
  if (connected) renderAgent(state.agent);
  renderPlan();
  if (!connected) {
    byId('follow-start').disabled = true;
    return;
  }
  const rows = [['ESP32', linked], ['레이더 상태', radar.state], ['정지 응답', stopProof], ['정지 / 보류 사유', state.stop_reason]];
  if (page === 'ai') readout('ai-safety', rows);
  if (page === 'camera') {
    const follow = cameraMode === 'follow';
    byId('follow-controls').hidden = !follow;
    byId('direct-controls').hidden = follow;
    const fresh = cameraWanted && state.perception_fresh;
    byId('follow-start').disabled = !fresh || state.target == null;
    const hands = fresh ? (state.vision.raw ?? []) : [];
    text('gesture', hands.length ? hands.map((hand) => `${name(hand.name)} · ${measured(hand.confidence)}`).join(' / ') : cameraWanted ? '인식 미확인' : '카메라 꺼짐');
    readout('camera-details', [['검출 인원', fresh ? state.people : unknown],
      ['대상', !fresh || state.target == null ? '미선택' : `ID ${state.target}`],
      ['추종', fresh && state.following ? '시뮬레이션 추종 중' : '대기'], ['확정 동작', follow ? name(fresh ? state.gesture.confirmed : null) : '미리보기'],
      ['적용 동작', follow ? name(state.gesture.applied_action) : '전송 없음']]);
    readout('guide', Object.entries(follow ? state.settings.follow : state.settings.hand).map(([key, value]) => [name(key), name(value)]));
    text('frame-detail', `FPS ${measured(state.fps)} · 처리 ${measured(state.processing_ms, ' ms')}`);
  }
  if (page === 'manual') {
    readout('manual-safety', [...rows, ['실물 구동 준비', state.ready ? '준비' : '차단']]);
    const values = [0, 1, 2].map((i) => `M${i+1} · ${measured(state.motors.measured_rpm?.[i], ' RPM')}`);
    renderCells('motor-summary', values, 'span');
  }
  if (page === 'diagnostics') {
    readout('diagnostic-link', [...rows, ['레이더 상세', radar.detail], ['수신 검증', state.validation], ['복구 상태', state.recovery], ['장치 Fault', state.fault]]);
    readout('diagnostic-vision', [['영상 출처', source], ['파이프라인', state.vision_status], ['인식 유효', state.perception_fresh ? '유효' : '미확인'],
      ['처리 FPS', measured(state.fps)], ['처리 지연', measured(state.processing_ms, ' ms')]]);
    readout('test-log', state.tests.map((test) => [name(test.test), name(test.result)]));
    renderCells('motor-table', [0,1,2].map((i) => [`M${i+1}`, ...['target_rpm','measured_rpm','pwm','encoder_count'].map((key) => measured(state.motors[key]?.[i]))]), 'tr');
  }
  if (page === 'settings') {
    const cfg = state.settings;
    readout('esp-settings', [['ESP32 IPv4', cfg.esp32 || '미설정']]);
    readout('radar-settings', [['Raspberry Pi IPv4', cfg.radar || '미설정']]);
    readout('hand-settings', Object.entries(cfg.hand).map(([key, value]) => [name(key), name(value)]));
    readout('vision-settings', [['카메라 번호', cfg.camera_index], ['영상 유효시간', `${cfg.timeout_ms} ms`], ['유지시간', `${cfg.hold_s} 초`], ['추론 해상도', `${cfg.image_size} px`]]);
  }
}

function renderCells(id, values, tag) {
  const root = byId(id);
  const key = JSON.stringify(values);
  if (root.dataset.content === key) return;
  root.dataset.content = key;
  root.replaceChildren(...values.map((value) => {
    const node = document.createElement(tag);
    if (Array.isArray(value)) {
      for (const cell of value) {
        const td = document.createElement('td');
        td.textContent = cell;
        node.append(td);
      }
    } else node.textContent = value;
    return node;
  }));
}

function clearFrame(message) {
  const frame = byId('frame');
  frame.hidden = true;
  frame.removeAttribute('src');
  byId('overlays').replaceChildren();
  byId('landmarks').replaceChildren();
  text('feed-label', message);
  frameToken = null;
}

function syncCameraPower(force = false, leaving = false) {
  const enabled = page === 'camera' && cameraAllowed && !document.hidden && !leaving;
  if (enabled === cameraWanted && !force) return;
  cameraWanted = enabled;
  const version = ++cameraVersion;
  cameraRegistered = false;
  text('camera-power', cameraAllowed && page === 'camera' ? '카메라 끄기' : '카메라 켜기');
  clearFrame(enabled ? '카메라 연결 대기 중' : '카메라 꺼짐');
  cameraPower = cameraPower.then(async () => {
    if (enabled && version !== cameraVersion) return;
    await fetchJson('/api/control', {
      method: 'POST', keepalive: true, headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'camera_power', enabled, viewer: cameraViewer }),
    });
    if (version === cameraVersion) cameraRegistered = enabled;
  }).catch(() => {
    if (enabled && version === cameraVersion) clearFrame('카메라 연결 요청 실패');
  });
}

function renderFrame(data) {
  if (!data.image) {
    clearFrame(data.status);
    return;
  }
  const frame = byId('frame');
  frame.src = data.image;
  frame.hidden = false;
  byId('feed').style.aspectRatio = data.aspect;
  text('feed-label', `${state?.source === 'SYNTHETIC_PREVIEW' ? '합성 영상 · ' : ''}${data.status}`);
  frameToken = data.frame;
  byId('landmarks').replaceChildren(...data.hands.filter((points) => points.length === 21).flatMap((points) => fingers.map((finger) => {
    const line = document.createElementNS('http://www.w3.org/2000/svg', 'polyline');
    line.setAttribute('points', finger.map((i) => points[i].map((v) => 1000*v).join(',')).join(' '));
    return line;
  })));
  const overlays = byId('overlays');
  const current = new Map([...overlays.children].map((node) => [Number(node.dataset.id), node]));
  for (const { id, bbox } of data.people) {
    let track = current.get(id);
    if (!track) {
      track = document.createElement('button');
      track.type = 'button';
      track.className = 'track';
      track.dataset.id = id;
      track.textContent = `ID ${id}`;
      track.setAttribute('aria-label', `추종 대상 ${id} 선택`);
      track.addEventListener('click', () => command('select_target', { id, frame: frameToken }));
      overlays.append(track);
    }
    const [left, top, right, bottom] = bbox;
    Object.assign(track.style, { left: `${100*left}%`, top: `${100*top}%`, width: `${100*(right-left)}%`, height: `${100*(bottom-top)}%` });
    track.disabled = cameraMode !== 'follow';
    track.dataset.target = String(id === state?.target);
    current.delete(id);
  }
  for (const old of current.values()) old.remove();
}

async function refreshState() {
  const request = ++stateRequest;
  try {
    const next = await fetchJson('/api/state');
    if (request !== stateRequest) return;
    const reconnected = !online && state !== null;
    state = next;
    online = true;
    if (reconnected && cameraWanted) syncCameraPower(true);
  } catch {
    if (request !== stateRequest) return;
    online = false;
  }
  render();
}

async function pollState() {
  await refreshState();
  setTimeout(pollState, 300);
}

async function pollCamera() {
  if (cameraWanted) {
    const version = cameraVersion;
    try {
      await cameraPower;
      if (version === cameraVersion && cameraWanted && cameraRegistered) {
        const data = await fetchJson(`/api/camera?viewer=${cameraViewer}`);
        if (version === cameraVersion && cameraWanted) {
          if (data.lease_active === false) syncCameraPower(true);
          else renderFrame(data);
        }
      }
    } catch {
      if (version === cameraVersion && cameraWanted) clearFrame('영상 연결 끊김');
    }
  }
  setTimeout(pollCamera, 150);
}

byId('camera-power').addEventListener('click', () => {
  cameraAllowed = !cameraAllowed;
  syncCameraPower();
  render();
});
document.addEventListener('visibilitychange', () => syncCameraPower());
window.addEventListener('pagehide', () => syncCameraPower(false, true));
window.addEventListener('pageshow', () => syncCameraPower());
for (const button of nav) button.addEventListener('click', () => selectPage(button.dataset.page));
for (const button of cameraButtons) button.addEventListener('click', () => {
  cameraMode = button.dataset.camera;
  for (const segment of cameraButtons) segment.setAttribute('aria-pressed', String(segment === button));
  render();
});
for (const button of document.querySelectorAll('[data-command]')) {
  button.addEventListener('click', () => button.dataset.command === 'estop' ? stop() : command(button.dataset.command));
}
for (const button of document.querySelectorAll('[data-example]')) button.addEventListener('click', () => {
  prompt.value = button.dataset.example;
  prompt.focus();
  renderPlan();
});
prompt.addEventListener('input', renderPlan);
byId('estop').addEventListener('click', stop);
byId('discard').addEventListener('click', () => {
  const card = state?.agent?.card;
  if (card) command('agent_reject', {plan_id: card.id, revision: card.revision});
  else command('discard_plan');
});
byId('plan-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  if (planBusy || !online || !prompt.value.trim()) return;
  planBusy = true;
  renderPlan();
  try {
    if (state?.agent) {
      if (await command('agent_message', { text: prompt.value.trim() })) prompt.value = '';
    } else await command('plan', { text: prompt.value.trim() });
  }
  finally { planBusy = false; renderPlan(); }
});
window.addEventListener('keydown', (event) => {
  if (event.repeat || event.ctrlKey || event.metaKey || event.altKey || event.isComposing || event.target.matches?.('input,textarea,select,[contenteditable]')) return;
  if (event.code === 'Space' || event.code === 'KeyE') {
    event.preventDefault();
    stop();
  }
});
pollState();
pollCamera();
