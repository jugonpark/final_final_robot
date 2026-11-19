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
  COMPLETED: '모든 단계 STOP 확인 · 완료', ABORTED: '실행 중단', UNCONFIRMED: '종료 기록 미확인',
};
const archiveState = { COMPLETED: 'STOP 확인 후 완료', ABORTED: '중단', UNCONFIRMED: '종료 미확인' };
const archiveEvent = {
  RUN_STARTED: '실행 시작 기록', HANDSHAKE_STOP_CONFIRMED: '시작 전 STOP 확인', MOVE_ACCEPTED: '이동 수락',
  GOAL_REACHED: '목표 도달 보고', STOP_CONFIRMED: '장치 STOP 확인', STOP_REQUESTED: '정지 요청',
  ABORT_STOP_RESULT: '중단 후 STOP 응답', COMPLETED: '종료 · 모든 단계 STOP 확인', ABORTED: '실행 중단',
};
const moveDirection = { forward: '앞으로', backward: '뒤로', left: '왼쪽으로', right: '오른쪽으로' };
const moveVector = { forward: [0, -1], backward: [0, 1], left: [-1, 0], right: [1, 0] };
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
let chatClearing = false;
let chatFeed = null;
let chatFeedId = null;
let chatFeedText = null;
let draftBusy = false;
let draftDialogContext = null;
let lastPlan = '';
let stopPending = false;
let lastNavigation = 0;
const cameraViewer = crypto.randomUUID().replaceAll('-', '');
let cameraAllowed = true;
let cameraWanted = false;
let cameraVersion = 0;
let cameraRegistered = false;
let cameraPower = Promise.resolve();
let routePoints = [];
let routeFrame = 0;
let archiveRequest = 0;
let archiveSelection = null;
let latestRun = null;
let timelineUnavailable = false;
let sourceRequest = 0;

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
  if (pageId !== 'ai') resetRoute();
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
  if (page === 'history') refreshHistory();
  if (page === 'knowledge') refreshKnowledge();
}

function renderPlan() {
  const agent = state?.agent;
  const plan = agent?.card ?? (!agent && state?.plan?.text === prompt.value.trim() ? state.plan : null);
  const waiting = Boolean(agent?.waiting_mode_plan);
  byId('plan-waiting').hidden = !waiting;
  byId('plan-empty').hidden = Boolean(plan) || waiting;
  byId('plan-form').querySelector('button').disabled = planBusy || chatClearing || !online || !prompt.value.trim();
  byId('clear-chat').disabled = !online || planBusy || chatClearing || !(agent && (agent.messages.length || agent.busy || agent.card));
  byId('discard').disabled = !online || planBusy || (agent ? agent.busy || !agent.card : !state?.plan);
  byId('add-block').hidden = !agent;
  byId('add-block').disabled = !online || draftBusy || (plan?.blocks?.length ?? 0) >= 4;
  byId('draft-order-help').hidden = !(plan?.blocks?.length > 1);
  byId('request-review').hidden = !agent;
  byId('request-review').disabled = !online || draftBusy || agent?.busy || !plan || plan.rotation || plan.review_requested ||
    ['EXECUTING', 'STOPPING'].includes(agent?.execution);
  text('draft-review-state', plan?.review_requested ? `v${plan.revision} 검토 요청 · Qt 현장 승인 필요` : '실물 실행 승인 · Qt 관제');
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
  const steps = plan?.steps ?? [];
  if (plan?.blocks) renderDraftBlocks(plan);
  else renderSteps('plan-steps', steps);
  const diff = byId('plan-diff');
  diff.hidden = !plan?.before;
  diff.open = false;
  if (plan?.before) {
    const before = plan.before;
    const changed = steps.findIndex((step, index) => step !== before[index]);
    const label = before.length === steps.length && changed !== -1 ?
      `${changed + 1}단계 · ${before[changed]} → ${steps[changed]}` : `${before.length}단계 → ${steps.length}단계`;
    text('plan-before-label', `v${plan.revision - 1} → v${plan.revision} · ${label}`);
  }
  renderSteps('plan-before', plan?.before ?? []);
  renderMissionRoute(plan?.actions ?? []);
  text('plan-distance', plan ? (agent ? `미션 ${plan.id.slice(0, 6)} · v${plan.revision} · ${steps.length}블록 · ${Number(plan.distance_cm)} cm` : `${steps.length}단계 · 총 이동 거리 ${Number(plan.distance_cm)} cm`) : '');
}

async function changeDraft(plan, kind, fields = {}, focusBlock = undefined) {
  if (!online || draftBusy) return;
  draftBusy = true;
  renderPlan();
  text('draft-feedback', '초안을 확인하는 중입니다.');
  try {
    const data = await fetchJson('/api/draft', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({kind, mission_id: plan?.id ?? null, expected_revision: plan?.revision ?? 0,
        request_id: crypto.randomUUID().replaceAll('-', ''), ...fields}),
    });
    text('draft-feedback', data.message);
  } catch (error) {
    const messages = { ACTION_COUNT: '한 미션은 최대 4블록입니다.', TOTAL_DISTANCE_LIMIT: '한 미션의 이동 거리는 합계 100cm 이하여야 합니다.',
      STOP_MUST_BE_LAST: '정지 블록은 맨 끝에 있어야 합니다.' };
    text('draft-feedback', messages[error.message] ?? error.message);
  } finally {
    draftBusy = false;
    await refreshState();
    if (focusBlock !== undefined && page === 'ai') {
      (byId('plan-steps').querySelector(`[data-block="${focusBlock}"] button:not(:disabled)`) ?? byId('add-block')).focus();
    }
  }
}

function renderDraftBlocks(plan) {
  const list = byId('plan-steps');
  const ids = plan.blocks.map(block => block.block_id);
  const canMove = (id, before) => {
    const index = ids.indexOf(id);
    return index >= 0 && before !== id && before !== (ids[index + 1] ?? null);
  };
  const move = (id, before) => {
    if (canMove(id, before)) changeDraft(plan, 'reorder', {target_block_id: id, before_block_id: before}, id);
  };

  const mime = 'application/x-dig-block';
  let dragged = null;
  let marker = null;
  const clearMarker = () => {
    if (marker) delete marker.dataset.drop;
    marker = null;
  };
  const finishDrag = () => {
    if (dragged) delete dragged.dataset.dragging;
    dragged = null;
    clearMarker();
  };
  const insertionIndex = y => {
    const index = rows.findIndex(row => {
      const rect = row.getBoundingClientRect();
      return y < rect.top + rect.height / 2;
    });
    return index === -1 ? rows.length : index;
  };
  const rows = plan.blocks.map((block, index) => {
    const row = document.createElement('li');
    row.dataset.block = block.block_id;
    const grip = document.createElement('span');
    grip.className = 'block-grip';
    grip.textContent = '⠿';
    grip.title = '드래그해서 순서 변경';
    grip.setAttribute('aria-hidden', 'true');
    grip.hidden = ids.length < 2;
    grip.draggable = !grip.hidden;
    const title = document.createElement('strong');
    title.textContent = plan.steps[index];
    const source = document.createElement('small');
    source.textContent = block.source_turn_id ? '대화에서 추가·편집' : '편집기에서 추가·편집';
    const actions = document.createElement('div');
    actions.className = 'block-actions';
    const button = (name, title, action, disabled = false) => {
      const node = document.createElement('button');
      node.type = 'button';
      node.className = 'ghost';
      node.textContent = name;
      node.disabled = disabled;
      node.setAttribute('aria-label', `${index + 1}번 블록 ${title}`);
      node.addEventListener('click', action);
      actions.append(node);
    };
    button('↑', '위로', () => move(block.block_id, ids[index - 1]), index === 0);
    button('↓', '아래로', () => move(block.block_id, ids[index + 2] ?? null), index === ids.length - 1);
    button('편집', '편집', () => openDraftDialog(plan, block));
    button('삭제', '삭제', () => changeDraft(plan, 'delete', {target_block_id: block.block_id}, ids[index + 1] ?? ids[index - 1] ?? null));
    grip.addEventListener('dragstart', event => {
      if (!online || draftBusy) {
        event.preventDefault();
        return;
      }
      dragged = row;
      event.dataTransfer.effectAllowed = 'move';
      event.dataTransfer.setData(mime, block.block_id);
      event.dataTransfer.setDragImage(row, 16, 16);
      row.dataset.dragging = '';
    });
    grip.addEventListener('dragend', finishDrag);
    row.append(title, grip, source, actions);
    return row;
  });
  list.replaceChildren(...rows);
  list.ondragover = event => {
    if (!dragged || !event.dataTransfer.types.includes(mime)) return;
    const slot = insertionIndex(event.clientY);
    clearMarker();
    if (!canMove(dragged.dataset.block, ids[slot] ?? null)) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
    marker = rows[slot] ?? rows.at(-1);
    marker.dataset.drop = slot === rows.length ? 'after' : 'before';
  };
  list.ondragleave = event => {
    if (!list.contains(event.relatedTarget)) clearMarker();
  };
  list.ondrop = event => {
    if (!dragged) return;
    event.preventDefault();
    const id = dragged.dataset.block;
    finishDrag();
    if (event.dataTransfer.getData(mime) === id) move(id, ids[insertionIndex(event.clientY)] ?? null);
  };
}

function updateDraftAmount() {
  const type = byId('draft-action').value;
  const angle = type.startsWith('rotate-');
  const amount = byId('draft-amount');
  amount.disabled = amount.hidden = type === 'stop';
  byId('draft-amount-label').hidden = amount.hidden;
  text('draft-amount-label', angle ? '각도 (°)' : '거리 (cm)');
  amount.min = angle ? '0.01' : '5';
  amount.max = angle ? '360' : '50';
}

function openDraftDialog(plan, block = null, clear = false) {
  draftDialogContext = { plan, block, clear };
  const dialog = byId('draft-dialog');
  const action = block?.action;
  byId('draft-action-fields').hidden = byId('draft-amount').disabled = clear;
  byId('draft-clear-note').hidden = !clear;
  const position = byId('draft-position');
  byId('draft-position-field').hidden = clear || Boolean(block) || !plan?.blocks?.length;
  position.replaceChildren(new Option('맨 뒤', ''));
  if (!byId('draft-position-field').hidden) {
    plan.blocks.forEach((item, index) => {
      position.add(new Option(`${index + 1}번 앞 · ${plan.steps[index]}`, item.block_id));
    });
  }
  text('draft-dialog-title', clear ? '초안을 비울까요?' : block ? '블록 편집' : '블록 추가');
  text('draft-submit', clear ? '초안 비우기' : '저장');
  if (!clear) {
    byId('draft-action').value = action?.type === 'rotate' ? `rotate-${action.direction ?? 'unspecified'}` :
      action?.type === 'stop' ? 'stop' : action?.direction ?? 'forward';
    byId('draft-amount').value = action?.distance_cm ?? action?.angle_deg ?? 5;
    updateDraftAmount();
  }
  dialog.returnValue = '';
  dialog.showModal();
}

function renderSteps(id, labels) {
  byId(id).replaceChildren(...labels.map((label) => {
    const li = document.createElement('li');
    li.textContent = label;
    return li;
  }));
}

function projectRoute(actions) {
  let x = 0, y = 0, angle = 0;
  const points = [{x, y, angle, step: 0}];
  for (const [index, action] of actions.entries()) {
    if (action.type === 'move' && moveVector[action.direction]) {
      const [u, v] = moveVector[action.direction];
      const radians = angle * Math.PI / 180;
      const distance = Number(action.distance_cm);
      x += distance * (u * Math.cos(radians) - v * Math.sin(radians));
      y += distance * (u * Math.sin(radians) + v * Math.cos(radians));
    } else if (action.type === 'rotate') {
      if (!action.direction) return null;
      angle += Number(action.angle_deg) * (action.direction === 'left' ? -1 : 1);
    } else continue;
    points.push({x, y, angle, step: index + 1});
  }
  const xs = points.map(point => point.x), ys = points.map(point => point.y);
  const left = Math.min(...xs), right = Math.max(...xs), top = Math.min(...ys), bottom = Math.max(...ys);
  const scale = Math.min(22, 340 / Math.max(1, right - left), 220 / Math.max(1, bottom - top));
  return points.map(point => ({ ...point, x: 240 + (point.x - (left + right) / 2) * scale,
    y: 175 + (point.y - (top + bottom) / 2) * scale }));
}

const svgPoint = point => `${point.x.toFixed(2)},${point.y.toFixed(2)}`;

function showRobot(point) {
  byId('route-robot').setAttribute('transform', `translate(${point.x} ${point.y}) rotate(${point.angle})`);
}

function resetRoute() {
  cancelAnimationFrame(routeFrame);
  routeFrame = 0;
  showRobot(routePoints[0] ?? { x: 240, y: 175, angle: 0 });
  byId('route-trail').removeAttribute('points');
  byId('play-route').disabled = routePoints.length < 2;
  byId('reset-route').disabled = true;
  if (routePoints.length > 1) text('route-status', '가상 재생 대기');
}

function renderMissionRoute(actions) {
  const projected = projectRoute(actions);
  routePoints = projected ?? [];
  resetRoute();
  byId('route-plan').setAttribute('points', routePoints.map(svgPoint).join(' '));
  const markers = [];
  for (const point of routePoints) {
    if (point.step === 0) continue;
    const marker = document.createElementNS('http://www.w3.org/2000/svg', 'text');
    marker.setAttribute('x', point.x + 12);
    marker.setAttribute('y', point.y - 12);
    marker.textContent = point.step;
    markers.push(marker);
  }
  byId('route-markers').replaceChildren(...markers);
  if (routePoints.length < 2) text('route-status', projected ? '미리 볼 이동 경로가 없습니다.' : '회전 방향을 지정해야 경로를 그릴 수 있습니다.');
}

function playRoute() {
  if (routePoints.length < 2) return;
  resetRoute();
  byId('play-route').disabled = true;
  byId('reset-route').disabled = false;
  text('route-status', '명령 기반 가상 움직임 재생 중');
  const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const segments = routePoints.length - 1;
  const start = performance.now();
  const tick = (now) => {
    const progress = reduced ? segments : Math.min(segments, (now - start) / 760);
    const index = Math.min(Math.floor(progress), segments - 1);
    const blend = progress - index;
    const a = routePoints[index], b = routePoints[index + 1];
    const point = { x: a.x + (b.x - a.x) * blend, y: a.y + (b.y - a.y) * blend,
      angle: a.angle + (b.angle - a.angle) * blend };
    showRobot(point);
    byId('route-trail').setAttribute('points', [...routePoints.slice(0, index + 1), point].map(svgPoint).join(' '));
    if (progress < segments) routeFrame = requestAnimationFrame(tick);
    else {
      routeFrame = 0;
      byId('play-route').disabled = false;
      text('route-status', '가상 재생 완료');
    }
  };
  routeFrame = requestAnimationFrame(tick);
}

function renderProgress(progress) {
  const steps = byId('execution-steps');
  const key = JSON.stringify([progress ?? null, timelineUnavailable]);
  if (steps.dataset.content === key) return;
  steps.dataset.content = key;
  const meter = byId('execution-meter');
  byId('execution-source').hidden = !progress;
  if (!progress) {
    text('execution-summary', timelineUnavailable ? '실행 이력을 지금 읽을 수 없습니다.' : '실행 기록이 없습니다.');
    steps.replaceChildren();
    meter.hidden = true;
    return;
  }
  const count = progress.steps.length;
  meter.hidden = !count;
  meter.max = count || 1;
  meter.value = progress.verified_count;
  const saved = Boolean(progress.provenance);
  const simulated = saved && progress.provenance !== 'ESP32_EXECUTOR';
  text('execution-source', saved ? `최근 저장 기록 · ${archiveProvenance(progress)}` : '현재 실행 · ESP32 실행기 이벤트');
  const summary = [`계획 v${progress.revision}`, runStatus[progress.state]];
  if (count) summary.push(`${simulated ? '시뮬레이션 STOP 기록' : '실제 STOP 확인'} ${progress.verified_count}/${count}`);
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

const archiveTime = value => new Date(value).toLocaleString('ko-KR', { dateStyle: 'medium', timeStyle: 'medium' });
const archiveProvenance = run => run.provenance === 'ESP32_EXECUTOR' ? 'ESP32 실행기 이벤트' : '시뮬레이션 기록';
const archivedActionLabel = action => action.type === 'move' ? `${moveDirection[action.direction]} ${action.distance_cm} cm` : '정지';

function archivedProgress(run) {
  if (!run) return null;
  const labels = run.actions.filter(action => action.type === 'move').map(archivedActionLabel);
  const steps = labels.map(() => 'WAITING');
  const updates = { MOVE_ACCEPTED: 'MOVING', GOAL_REACHED: 'GOAL_REPORTED', STOP_CONFIRMED: 'STOP_VERIFIED' };
  for (const event of run.events) {
    if (updates[event.event_type] && event.step_index >= 1 && event.step_index <= steps.length) {
      steps[event.step_index - 1] = updates[event.event_type];
    }
  }
  const verified = steps.filter(step => step === 'STOP_VERIFIED').length;
  if (run.state === 'ABORTED') steps.fill('BLOCKED', verified);
  return { revision: run.revision, state: run.state, provenance: run.provenance, labels, steps, last_error: run.reason,
    verified_count: verified };
}

async function pollTimeline() {
  if (page === 'ai') {
    try {
      latestRun = archivedProgress((await fetchJson('/api/history/latest')).run);
      timelineUnavailable = false;
    } catch {
      latestRun = null;
      timelineUnavailable = true;
    }
    renderProgress((online && state?.agent?.progress) || latestRun);
  }
  setTimeout(pollTimeline, 1200);
}

async function refreshHistory() {
  const request = ++archiveRequest;
  text('history-status', '실행 이력을 읽는 중입니다.');
  byId('history-runs').replaceChildren();
  showHistoryDetail(null);
  try {
    const data = await fetchJson('/api/history');
    if (page !== 'history' || request !== archiveRequest) return;
    byId('archive-warning').hidden = !data.degraded;
    text('history-status', data.runs.length ? '' : '저장된 실행 기록이 없습니다.');
    byId('history-runs').replaceChildren(...data.runs.map(run => {
      const row = document.createElement('li');
      const button = document.createElement('button');
      const label = document.createElement('strong');
      const time = document.createElement('small');
      button.className = 'history-run';
      button.type = 'button';
      button.dataset.run = run.run_id;
      button.setAttribute('aria-pressed', 'false');
      label.textContent = `계획 v${run.revision} · ${archiveState[run.state]}`;
      time.textContent = `${archiveTime(run.started_at)} · ${archiveProvenance(run)}`;
      button.addEventListener('click', () => loadHistoryRun(run.run_id));
      button.append(label, time);
      row.append(button);
      return row;
    }));
    if (data.runs.length) loadHistoryRun(data.runs.find(run => run.run_id === archiveSelection)?.run_id ?? data.runs[0].run_id);
  } catch (error) {
    if (page === 'history' && request === archiveRequest) text('history-status', error.message);
  }
}

async function loadHistoryRun(runId) {
  const request = ++archiveRequest;
  archiveSelection = runId;
  for (const button of byId('history-runs').querySelectorAll('button')) {
    button.setAttribute('aria-pressed', String(button.dataset.run === runId));
  }
  showHistoryDetail(null);
  text('history-detail-status', '실행 상세를 읽는 중입니다.');
  try {
    const run = await fetchJson(`/api/history/${runId}`);
    if (page === 'history' && request === archiveRequest) showHistoryDetail(run);
  } catch (error) {
    if (page === 'history' && request === archiveRequest) text('history-detail-status', error.message);
  }
}

function showHistoryDetail(run) {
  text('history-detail-status', run ? '' : '목록에서 실행을 선택하세요.');
  readout('history-meta', run ? [['실행', run.run_id], ['계획 버전', `v${run.revision}`], ['출처', archiveProvenance(run)],
    ['결과', archiveState[run.state]], ['중단 사유', run.reason ?? '—']] : []);
  renderSteps('history-plan', (run?.actions ?? []).map(archivedActionLabel));
  byId('history-events').replaceChildren(...(run?.events ?? []).map(event => {
    const row = document.createElement('li');
    const title = document.createElement('strong');
    const time = document.createElement('time');
    const check = event.event_type === 'ABORT_STOP_RESULT' ? (event.evidence.stop_verified ? ' · 확인' : ' · 미확인') : '';
    title.textContent = `${event.step_index ? `${event.step_index}단계 · ` : ''}${archiveEvent[event.event_type]}${check}${event.reason ? ` · ${event.reason}` : ''}`;
    time.dateTime = event.received_at;
    time.textContent = archiveTime(event.received_at);
    row.append(title, time);
    return row;
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
  if (row.citations?.length) {
    const sources = document.createElement('div');
    sources.className = 'chat-citations';
    for (const [index, citation] of row.citations.entries()) {
      sources.append(citationButton(citation, `${index + 1}. ${citation.path.split('/').at(-1)}`));
    }
    content.append(sources);
  }
  message.append(avatar, content);
  return message;
}

const sourceScope = row => ({
  BREADBOARD_PROTO: row.source_kind === 'FIRMWARE_SOURCE' ? '브레드보드 · 펌웨어 소스' : '브레드보드 · 제공 BOM',
  PCB_V18_DESIGN: 'PCB V18 · CAD 설계 · 제작 승인 보류',
  PCB_V19_DESIGN: 'PCB V19 · CAD 설계 · 제작 승인 보류',
}[row.variant_id] ?? (row.evidence_status === 'REVIEW_ONLY' ? '검토본 · 정본 후보' : ''));
const sourceLabel = row => [
  sourceScope(row), row.page ? `${row.page}쪽` : `${row.section ?? row.heading}${row.line ? ` · ${row.line}행` : ''}`,
  `리비전 ${row.source_kind === 'FIRMWARE_SOURCE' ? row.revision : (row.revision ?? row.version).slice(0, 12)}`,
].filter(Boolean).join(' · ');

function citationButton(row, title = row.path.split('/').at(-1)) {
  const button = document.createElement('button');
  const details = document.createElement('small');
  button.type = 'button';
  button.className = 'outline source-link';
  button.textContent = title;
  details.textContent = sourceLabel(row);
  button.append(details);
  button.addEventListener('click', () => openSource(row.id));
  return button;
}

async function openSource(id) {
  const request = ++sourceRequest;
  const dialog = byId('source-dialog');
  text('source-title', '문서 출처');
  text('source-meta', '');
  text('source-text', '발췌를 읽는 중입니다.');
  if (!dialog.open) dialog.showModal();
  try {
    const row = await fetchJson(`/api/knowledge/chunks/${id}`);
    if (request !== sourceRequest) return;
    text('source-title', row.path);
    const digest = row.source_sha256 ?? row.sha;
    const excerpt = digest !== row.sha ? `\n색인 발췌 SHA-256 ${row.sha}` : '';
    text('source-meta', `${sourceLabel(row)}\n${row.source_kind ?? ''} · ${row.evidence_status ?? ''}\n${row.source_path ?? row.path}\n출처 SHA-256 ${digest}${excerpt}`);
    text('source-text', row.body);
  } catch (error) {
    if (request === sourceRequest) text('source-text', error.message);
  }
}

async function refreshKnowledge() {
  text('knowledge-library-status', '문서 목록을 읽는 중입니다.');
  try {
    const { documents } = await fetchJson('/api/knowledge/documents', {signal: AbortSignal.timeout(12000)});
    const labels = {indexed: '색인됨', scoped: '버전 지정 검색', review_only: '검토용', outdated: '이전 버전', failed: '색인 실패'};
    byId('knowledge-library').replaceChildren(...documents.map((row) => {
      const item = document.createElement('li');
      const title = document.createElement('strong');
      const detail = document.createElement('small');
      title.textContent = row.path;
      detail.textContent = [labels[row.status], sourceScope(row), `리비전 ${row.version.slice(0, 12)}`, row.detail].filter(Boolean).join(' · ');
      item.append(title, detail);
      return item;
    }));
    text('knowledge-library-status', documents.length ? '' : '등록된 문서가 없습니다.');
  } catch (error) {
    text('knowledge-library-status', error.message);
  }
}

function renderChatPreview(reply) {
  const preview = byId('agent-thinking').querySelector('.bubble');
  const thread = byId('chat-thread');
  const follow = thread.scrollHeight - thread.clientHeight - thread.scrollTop < 32;
  const content = reply || '응답을 준비하고 있어요.';
  if (preview.textContent !== content) preview.textContent = content;
  preview.classList.toggle('thinking', !reply);
  if (follow && page === 'ai') thread.scrollTop = thread.scrollHeight;
}

function connectChatFeed(id) {
  if (id === chatFeedId) return;
  chatFeed?.close();
  chatFeed = null;
  chatFeedId = id;
  chatFeedText = null;
  if (!id) return;
  const feed = new EventSource(`/api/agent/stream/${id}`);
  chatFeed = feed;
  function finish() {
    if (chatFeed !== feed) return;
    feed.close();
    chatFeed = null;
    chatFeedText = null;
    void refreshState();
  }
  feed.onmessage = event => {
    if (chatFeed !== feed) return;
    const data = JSON.parse(event.data);
    if (data.done) finish();
    else {
      chatFeedText = data.text;
      renderChatPreview(chatFeedText);
    }
  };
  feed.onerror = finish;
}

function renderAgent(agent) {
  const history = byId('agent-history');
  history.hidden = !agent;
  byId('clear-chat').hidden = !agent;
  byId('program-example').hidden = !agent;
  connectChatFeed(agent?.chat_stream_id ?? null);
  if (!agent) return;
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
  renderChatPreview(chatFeedText ?? agent.partial_reply);
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
  const knowledgeNav = nav.find(button => button.dataset.page === 'knowledge');
  knowledgeNav.hidden = connected && !state.agent;
  renderProgress((connected && state.agent?.progress) || latestRun);
  byId('history-warning').hidden = !state?.history_degraded;
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
byId('play-route').addEventListener('click', playRoute);
byId('reset-route').addEventListener('click', resetRoute);
byId('open-history').addEventListener('click', () => selectPage('history'));
byId('refresh-history').addEventListener('click', refreshHistory);
byId('knowledge-query').addEventListener('submit', async (event) => {
  event.preventDefault();
  const field = byId('knowledge-question');
  const button = event.currentTarget.querySelector('button');
  button.disabled = true;
  text('knowledge-results-status', '문서를 검색하는 중입니다.');
  byId('knowledge-results').replaceChildren();
  try {
    const { results } = await fetchJson(`/api/knowledge/search?q=${encodeURIComponent(field.value.trim())}`, {signal: AbortSignal.timeout(12000)});
    byId('knowledge-results').replaceChildren(...results.map(row => {
      const item = document.createElement('li');
      item.append(citationButton(row));
      return item;
    }));
    text('knowledge-results-status', results.length ? `${results.length}개 발췌` : '색인된 문서에서 근거를 찾지 못했습니다.');
  } catch (error) {
    text('knowledge-results-status', error.message);
  } finally {
    button.disabled = false;
  }
});
byId('knowledge-upload').addEventListener('submit', async (event) => {
  event.preventDefault();
  const input = byId('knowledge-file');
  const file = input.files[0];
  const button = event.currentTarget.querySelector('button');
  if (!file || file.size > 4 * 1024 * 1024) {
    text('knowledge-library-status', '4MB 이하의 문서를 선택하세요.');
    return;
  }
  button.disabled = true;
  text('knowledge-library-status', '문서를 색인하는 중입니다.');
  try {
    const url = await new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result);
      reader.onerror = () => reject(new Error('파일을 읽을 수 없습니다.'));
      reader.readAsDataURL(file);
    });
    const { document: row } = await fetchJson('/api/knowledge/documents', { method: 'POST', signal: AbortSignal.timeout(30000),
      headers: {'Content-Type': 'application/json'}, body: JSON.stringify({name: file.name, content: url.split(',')[1]}) });
    input.value = '';
    await refreshKnowledge();
    text('knowledge-library-status', row.status === 'indexed' ? `${row.path} 색인을 완료했습니다.` : row.detail);
  } catch (error) {
    text('knowledge-library-status', error.message);
  } finally {
    button.disabled = false;
  }
});
const clearDialog = byId('clear-chat-dialog');
byId('clear-chat').addEventListener('click', () => {
  clearDialog.returnValue = '';
  clearDialog.showModal();
});
clearDialog.addEventListener('close', async () => {
  if (clearDialog.returnValue !== 'clear') return;
  chatClearing = true;
  render();
  try {
    if (await command('agent_clear_conversation')) prompt.value = '';
  } finally {
    chatClearing = false;
    render();
    prompt.focus();
  }
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
  if (card) openDraftDialog(card, null, true);
  else command('discard_plan');
});
byId('add-block').addEventListener('click', () => openDraftDialog(state?.agent?.card));
byId('request-review').addEventListener('click', () => changeDraft(state?.agent?.card, 'request_execution'));
byId('draft-action').addEventListener('change', updateDraftAmount);
byId('draft-dialog').addEventListener('close', () => {
  if (byId('draft-dialog').returnValue !== 'apply') return;
  const { plan, block, clear } = draftDialogContext;
  if (clear) {
    changeDraft(plan, 'clear');
    return;
  }
  const type = byId('draft-action').value;
  const amount = byId('draft-amount').valueAsNumber;
  const action = type === 'stop' ? {type: 'stop'} : type.startsWith('rotate-') ?
    {type: 'rotate', direction: type === 'rotate-unspecified' ? null : type.slice(7), angle_deg: amount} :
    {type: 'move', direction: type, distance_cm: amount};
  const before = byId('draft-position').value;
  if (block) changeDraft(plan, 'replace', {actions: [action], target_block_id: block.block_id}, block.block_id);
  else if (before) changeDraft(plan, 'insert', {actions: [action], before_block_id: before});
  else changeDraft(plan, 'append', {actions: [action]});
});
byId('plan-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  if (planBusy || chatClearing || !online || !prompt.value.trim()) return;
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
pollTimeline();
