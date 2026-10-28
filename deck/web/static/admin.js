/* 兜底：万一脚本某一行抛异常，别让人卡在「看看钥匙…」——
   至少把登录框放出来，而不是白屏干瞪眼。 */
window.addEventListener('error', () => {
  const status = document.getElementById('status');
  if (status) status.textContent = '脚本出了点问题，刷新一下试试';
});

'use strict';

/* 后台管理前端：登录 -> 左侧导航切面板。
   面板分六组：概览（仪表盘）、模型与接口、人设（系统提示词）、素材（表情包库）、
   用户、系统（检查更新）。
   口令是按天算的，所以这里不存任何东西，cookie 交给浏览器自己管。 */

const MOODS = ['happy', 'think', 'confused', 'proud', 'cry', 'angry', 'sleepy', 'love'];
const IMG_EXTS = ['.png', '.jpg', '.jpeg', '.gif', '.webp'];
const NAV_KEY = 'taffy_admin_nav_closed';   // 哪几个分组收起来了
const NAV_HIDDEN_KEY = 'taffy_admin_nav_hidden'; // 整条侧边栏收起来了吗

const statusEl = document.getElementById('status');
const panelView = document.getElementById('panel-view');
const logoutEl = document.getElementById('logout');
const navToggleEl = document.getElementById('nav-toggle');
const navCollapseEl = document.getElementById('nav-collapse');
const sidebarEl = document.getElementById('sidebar');
const sidebarTipEl = document.getElementById('sidebar-tip');

const modelEl = document.getElementById('model');
const visionEl = document.getElementById('vision-model');
const translateEl = document.getElementById('translate-model');
const datalistEl = document.getElementById('model-list');
const historyHintEl = document.getElementById('history-hint');
const historyEl = document.getElementById('history-chips');
const saveModelsEl = document.getElementById('save-models');
const contextLimitEl = document.getElementById('context-limit');
const contextStateEl = document.getElementById('context-state');
const saveContextEl = document.getElementById('save-context');

const promptTextEl = document.getElementById('prompt-text');
const promptCountEl = document.getElementById('prompt-count');
const promptLimitEl = document.getElementById('prompt-limit');
const promptStateEl = document.getElementById('prompt-state');
const savePromptEl = document.getElementById('save-prompt');
const resetPromptEl = document.getElementById('reset-prompt');

const chatBaseEl = document.getElementById('chat-base');
const chatKeyEl = document.getElementById('chat-key');
const chatStateEl = document.getElementById('chat-state');
const visionBaseEl = document.getElementById('vision-base');
const visionKeyEl = document.getElementById('vision-key');
const visionStateEl = document.getElementById('vision-state');
const translateBaseEl = document.getElementById('translate-base');
const translateKeyEl = document.getElementById('translate-key');
const translateStateEl = document.getElementById('translate-state');
const saveKeysEl = document.getElementById('save-keys');

const embedModelEl = document.getElementById('embed-model');
const embedEnabledEl = document.getElementById('embed-enabled');
const embedSwitchStateEl = document.getElementById('embed-switch-state');
const embedBaseEl = document.getElementById('embed-base');
const embedKeyEl = document.getElementById('embed-key');
const embedMaxTokensEl = document.getElementById('embed-max-tokens');
const embedStateEl = document.getElementById('embed-state');
const embedDimEl = document.getElementById('embed-dim');
const kbStatusEl = document.getElementById('kb-status');
const saveEmbedEl = document.getElementById('save-embed');

const cpuValueEl = document.getElementById('cpu-value');
const cpuNoteEl = document.getElementById('cpu-note');
const memValueEl = document.getElementById('mem-value');
const memNoteEl = document.getElementById('mem-note');
const memBarEl = document.getElementById('mem-bar');
const diskValueEl = document.getElementById('disk-value');
const diskNoteEl = document.getElementById('disk-note');
const procValueEl = document.getElementById('proc-value');
const procNoteEl = document.getElementById('proc-note');
const sysinfoHintEl = document.getElementById('sysinfo-hint');

const stickerZipEl = document.getElementById('sticker-zip');
const stickerImportEl = document.getElementById('sticker-import');
const stickerMoodEl = document.getElementById('sticker-mood');
const stickerFileEl = document.getElementById('sticker-file');
const stickerUploadEl = document.getElementById('sticker-upload');
const stickerTotalEl = document.getElementById('sticker-total');
const stickerLibraryEl = document.getElementById('sticker-library');

const knPathEl = document.getElementById('kn-path');
const knFileEl = document.getElementById('kn-file');
const knUploadEl = document.getElementById('kn-upload');
const knStatusEl = document.getElementById('kn-status');
const knCountEl = document.getElementById('kn-count');
const knTotalEl = document.getElementById('kn-total');
const knListEl = document.getElementById('kn-list');
const knHintEl = document.getElementById('kn-hint');

const wsPathEl = document.getElementById('ws-path');
const wsFileEl = document.getElementById('ws-file');
const wsUploadEl = document.getElementById('ws-upload');
const wsDirEl = document.getElementById('ws-dir');
const wsListEl = document.getElementById('ws-list');
const wsHintEl = document.getElementById('ws-hint');


const updateRemoteEl = document.getElementById('update-remote');
const updateCheckEl = document.getElementById('update-check');
const updateApplyEl = document.getElementById('update-apply');
const updateResultEl = document.getElementById('update-result');

let lastState = null;
let activeView = '';
let library = { moods: {}, other: [], total: 0 };
let wsDir = '';

const el = (tag, cls) => {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  return node;
};

function flash(text, ok) {
  statusEl.textContent = text;
  statusEl.classList.toggle('bad', ok === false);
}

function fmtSize(bytes) {
  if (bytes === null || bytes === undefined) return '—';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return `${(bytes / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

function fmtTime(seconds) {
  if (!seconds) return '—';
  const d = new Date(seconds * 1000);
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ` +
         `${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function fmtDuration(seconds) {
  if (!seconds) return '';
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const mins = Math.floor((seconds % 3600) / 60);
  if (days) return `${days} 天 ${hours} 小时`;
  if (hours) return `${hours} 小时 ${mins} 分`;
  return `${mins} 分`;
}

/* ---------------- 请求 ---------------- */

/* 统一发请求：非 2xx 就把后端给的 detail 抛出来 */
async function api(path, body, method) {
  const hasBody = body !== undefined;
  const resp = await fetch(path, {
    method: method || (hasBody ? 'POST' : 'GET'),
    headers: hasBody ? { 'Content-Type': 'application/json' } : {},
    body: hasBody ? JSON.stringify(body) : undefined,
  });
  return readResponse(resp);
}

/* 上传：请求体直接是文件本身（后端流式落盘），文件名放在 query 里 */
async function apiUpload(path, blob) {
  const resp = await fetch(path, { method: 'POST', body: blob });
  return readResponse(resp);
}

async function readResponse(resp) {
  let data = null;
  try {
    data = await resp.json();
  } catch (err) {
    data = null;
  }
  if (!resp.ok) {
    throw makeError(resp.status, data && data.detail);
  }
  return data;
}

/* 把后端的报错翻成人话。最要紧的是 404：FastAPI 只会甩一句 "Not Found"，
   但后台看到这个几乎只有一个原因——服务拉完新代码还没重启，跑的还是旧 Python，
   所以新页面上新加的接口它不认识。这里直接把这句话说清楚，别让人猜。 */
function makeError(status, detail) {
  const raw = typeof detail === 'string' ? detail.trim() : '';
  let message = raw || `服务返回 ${status}`;
  if (status === 404 && (!raw || raw === 'Not Found')) {
    message = '后台不认识这个接口（404）——多半是服务拉完新代码还没重启，' +
      '跑的还是旧进程：重启一下服务、再刷新这个页面就好。';
  } else if (status === 401) {
    message = raw || '登录状态过期了，重新登一次';
  } else if (status === 502 || status === 503 || status === 504) {
    message = `服务现在不在（${status}）——可能正在重启，等几秒刷新看看。`;
  }
  const error = new Error(message);
  error.status = status;
  error.data = detail;
  return error;
}

function guard(err) {
  if (err && err.status === 401) showLogin(err.message);
  else flash(err.message, false);
}

/* ---------------- 侧边导航 ---------------- */

function closedGroups() {
  try {
    return JSON.parse(localStorage.getItem(NAV_KEY) || '[]');
  } catch (err) {
    return [];
  }
}

function saveClosedGroups(list) {
  try {
    localStorage.setItem(NAV_KEY, JSON.stringify(list));
  } catch (err) {
    /* 存不了就算了，顶多下次打开全展开 */
  }
}

function navHidden() {
  try {
    return localStorage.getItem(NAV_HIDDEN_KEY) === '1';
  } catch (err) {
    return false;
  }
}

function saveNavHidden(on) {
  try {
    localStorage.setItem(NAV_HIDDEN_KEY, on ? '1' : '0');
  } catch (err) {
    /* 存不了就算了，顶多下次打开还是展开的 */
  }
}

/* 收起 / 展开整条侧边栏：收起来页面立刻宽敞。
   这是桌面端的事；手机上导航本来就是抽屉（顶栏那个 ☰），按钮直接藏掉。 */
function paintNavHidden(on) {
  sidebarEl.classList.toggle('collapsed', on);
  navCollapseEl.textContent = on ? '展开导航' : '收起导航';
  navCollapseEl.title = on ? '把侧边导航放出来' : '把侧边导航收起来，页面宽敞点';
}

function initNav() {
  const closed = closedGroups();
  document.querySelectorAll('#nav .nav-group').forEach((group) => {
    const head = group.querySelector('.nav-head');
    const key = group.dataset.group || '';
    if (closed.indexOf(key) >= 0) group.classList.add('closed');
    head.addEventListener('click', () => {
      group.classList.toggle('closed');
      const list = [];
      document.querySelectorAll('#nav .nav-group').forEach((item) => {
        if (item.classList.contains('closed')) list.push(item.dataset.group || '');
      });
      saveClosedGroups(list);
    });
  });

  document.querySelectorAll('#nav .nav-item').forEach((item) => {
    item.addEventListener('click', () => showView(item.dataset.view));
  });

  paintNavHidden(navHidden());
  navCollapseEl.addEventListener('click', () => {
    const on = !sidebarEl.classList.contains('collapsed');
    paintNavHidden(on);
    saveNavHidden(on);
  });

  navToggleEl.addEventListener('click', () => {
    sidebarEl.classList.toggle('open');
  });
}

function showView(name) {
  activeView = name;
  document.querySelectorAll('.view').forEach((view) => {
    view.hidden = view.dataset.view !== name;
  });
  document.querySelectorAll('#nav .nav-item').forEach((item) => {
    item.classList.toggle('on', item.dataset.view === name);
  });
  if (location.hash.slice(2) !== name) location.hash = `#/${name}`;
  sidebarEl.classList.remove('open');
  const hook = VIEW_HOOKS[name];
  if (hook) hook();
}

const VIEW_HOOKS = {
  'core-prompt': loadCorePrompt,
  config: loadConfig,
  tools: loadTools,
  personas: loadPersonas,
  users: loadUsers,
  dashboard: refreshSysinfo,
  traffic: loadTraffic,
  prompt: loadPrompt,
  stickers: loadStickers,
  knowledge: loadKnowledge,
  workspace: () => loadWorkspace(wsDir),
  update: initUpdate,
};

function showLogin() {
  /* 后台鉴权现在走用户账号：不是管理员就回登录页（登录后再跳回来） */
  location.replace('/login?next=/admin');
}

/* ---------------- 配置：模型 / 接口 / 向量 ---------------- */

function render(state) {
  lastState = state;
  panelView.hidden = false;
  sidebarEl.hidden = false;
  navToggleEl.hidden = false;
  navCollapseEl.hidden = false;
  logoutEl.hidden = false;
  sidebarTipEl.textContent = `管理员后台 · 聊天用 ${state.model} `;

  modelEl.value = state.model;
  visionEl.value = state.vision_model;
  translateEl.value = state.translate_model || '';

  // 接口地址不是秘密，直接填进去给主人看着改；提交时没动过就不写盘
  chatBaseEl.value = state.chat_base_url;
  visionBaseEl.value = state.vision_base_url;
  translateBaseEl.value = state.translate_base_url;
  chatStateEl.textContent = state.chat_key_source === 'admin'
    ? '当前 key 是后台设过的。'
    : '当前 key 用的是 .env 里的。';
  visionStateEl.textContent = state.vision_key_source === 'admin'
    ? '当前 key 是后台单独给图片这套设过的。'
    : '当前 key 用的是 .env 里给图片这套的默认值，跟聊天那套无关。';
  translateStateEl.textContent = state.translate_base_source === 'chat' &&
    state.translate_key_source === 'chat'
    ? '现在跟聊天那套完全共用（地址和 key 都是）。'
    : `地址来自 ${state.translate_base_source === 'admin' ? '后台' : 'env'}，` +
      `key 来自 ${state.translate_key_source === 'admin' ? '后台' : 'env'}。`;

  // 向量模型：模型名和地址直接填出来给主人看着改，key 只说来源
  embedModelEl.value = state.embed_model || '';
  embedBaseEl.value = state.embed_base_url || '';
  embedMaxTokensEl.value = state.embed_max_tokens || '';   // 0 = 自动探测，留空显示
  embedDimEl.textContent = state.embed_dim;
  renderEmbedSwitch(state);
  if (!state.embed_model || !state.embed_base_url) {
    embedStateEl.textContent = '还没配全，知识库现在只用原查询和英文检索词那两路。';
  } else if (state.embed_key_source === 'admin') {
    embedStateEl.textContent = '当前 key 是后台设过的。';
  } else {
    embedStateEl.textContent = '当前 key 用的是 .env 里的。';
  }
  renderKb(state.kb);
  updateRemoteEl.value = state.update_remote || 'gitee';

  contextLimitEl.value = state.context_limit;
  const at = Math.round((state.context_compress_at || 0.8) * 100);
  const atTokens = Math.round(state.context_limit * (state.context_compress_at || 0.8) / 1000);
  contextStateEl.textContent =
    `现在按 ${state.context_limit} token 算，用到 ${atTokens}k 左右（${at}%）就自动压缩。`;

  const history = state.model_history || [];
  datalistEl.innerHTML = '';
  historyEl.innerHTML = '';
  history.forEach((name) => {
    const option = document.createElement('option');
    option.value = name;
    datalistEl.appendChild(option);

    const chip = document.createElement('button');
    chip.type = 'button';
    chip.textContent = name;
    chip.addEventListener('click', () => {
      modelEl.value = name;
      flash(`已经填进「聊天模型」了：${name}`);
    });
    historyEl.appendChild(chip);
  });
  historyHintEl.textContent = history.length
    ? '用过的模型（点一下填进聊天模型）：'
    : '还没用过别的模型。';
  historyEl.hidden = !history.length;

  if (!activeView) {
    const wanted = location.hash.slice(2);
    showView(VIEW_HOOKS[wanted] ? wanted : 'dashboard');
  }
}

/* 后台也跟着身份卡走：标题、顶栏名字、头像、网页图标、主题色 */
async function loadBranding() {
  try {
    const resp = await fetch('/api/persona', { cache: 'no-store' });
    if (!resp.ok) return;
    const info = await resp.json();
    const name = info.name || '助手';
    document.title = `${name} 后台`;
    const nameEl = document.getElementById('top-name');
    if (nameEl) nameEl.textContent = `${name} 后台`;
    const avatarEl = document.getElementById('top-avatar');
    if (avatarEl && info.avatar) {
      avatarEl.src = info.avatar;
      avatarEl.alt = name;
    }
    if (info.icon) {
      // 和聊天页一样：重建 link + 时间戳，不然浏览器会一直用旧的图标
      const full = info.icon + (info.icon.includes('?') ? '&' : '?') + 'v=' + Date.now();
      document.querySelectorAll('link[rel="icon"], link[rel="shortcut icon"], link[rel^="icon"]')
        .forEach((node) => node.remove());
      const link = document.createElement('link');
      link.rel = 'icon';
      link.href = full;
      document.head.appendChild(link);
    }
    if (info.theme) {
      applyThemeColor(info.theme);
      const meta = document.querySelector('meta[name="theme-color"]');
      if (meta) meta.content = info.theme;
    }
  } catch (err) {
    /* 拿不到就用写死的助手，不影响后台使用 */
  }
}

/* 把 #rrggbb 主题色换算成浅/深档，覆盖后台用的那几个 CSS 变量 */
function applyThemeColor(theme) {
  if (!/^#[0-9a-fA-F]{6}$/.test(theme || '')) return;
  const mix = (target, ratio) => {
    const num = parseInt(theme.replace('#', ''), 16);
    const base = [(num >> 16) & 255, (num >> 8) & 255, num & 255];
    const to = target === 'w' ? 255 : 0;
    const out = base.map((v) => Math.round(v + (to - v) * ratio));
    return '#' + out.map((n) => n.toString(16).padStart(2, '0')).join('');
  };
  const root = document.documentElement.style;
  root.setProperty('--pink', mix('w', 0.25));
  root.setProperty('--pink-dark', theme);
  root.setProperty('--pink-deep', mix('b', 0.3));
  root.setProperty('--pink-pale', mix('w', 0.45));
  root.setProperty('--pink-mist', mix('w', 0.78));
  root.setProperty('--bg', mix('w', 0.93));
  root.setProperty('--line', mix('w', 0.7));
}

/* 能力探测：这台机器没有的功能，导航里就不显示（换台机器部署也不会满屏报错） */
async function loadCapabilities() {
  try {
    const data = await api('/api/admin/capabilities');
    const caps = data.capabilities || {};
    const notes = data.notes || {};
    const map = { traffic: 'traffic', update: 'update' };
    Object.entries(map).forEach(([view, cap]) => {
      const item = document.querySelector(`#nav .nav-item[data-view="${view}"]`);
      if (!item) return;
      item.hidden = !caps[cap];
      if (!caps[cap] && notes[cap]) item.title = notes[cap];
    });
    // 整个「系统」组都没有可用项就整组藏起来
    const updateItem = document.querySelector('#nav .nav-item[data-view="update"]');
    const group = updateItem && updateItem.closest('.nav-group');
    if (group) group.hidden = Boolean(updateItem.hidden);
  } catch (err) {
    /* 探测失败就当都有，别把后台弄坏 */
  }
}

async function load(message) {
  try {
    render(await api('/api/admin/state'));
    loadBranding();
    loadCapabilities();
    flash(message || '已登录', true);
  } catch (err) {
    showLogin(err && err.status === 401 ? '先输口令' : err.message);
  }
}

/* 向量那一路的总开关：勾上 = 开。状态分三种——没设过（跟配置走）、明确开着、明确关着 */
function renderEmbedSwitch(state) {
  const on = Boolean(state.embed_on);
  const set = state.embed_switch;
  embedEnabledEl.checked = on;
  if (set === null || set === undefined) {
    embedSwitchStateEl.textContent = on
      ? '现在跟着配置走：三样都配齐了，这一路在用。想彻底关掉就取消勾选再保存。'
      : '现在跟着配置走：还没配齐（或者缺 key），这一路没用上。';
  } else if (set) {
    embedSwitchStateEl.textContent = on
      ? '开关开着，这一路在用。'
      : '开关开着，但模型 / 地址 / key 还没配齐，实际用不了。';
  } else {
    embedSwitchStateEl.textContent = '开关关着，这一路整个不用（检索只走原查询词 + 英文检索词）。';
  }
}

/* 秒数说成人话：45 秒 / 12 分 30 秒 / 3 小时 20 分 */
function humanSeconds(seconds) {
  const total = Math.round(seconds);
  if (total < 60) return `${total} 秒`;
  const minutes = Math.floor(total / 60);
  if (minutes < 60) return `${minutes} 分 ${total % 60} 秒`;
  return `${Math.floor(minutes / 60)} 小时 ${minutes % 60} 分`;
}

/* 索引 / 向量库状态那一行。建向量要好几分钟，所以单独抽出来，刷新时别碰输入框。
   这一段也是「到底建成没有」的答案：建好会说「全部 N 块都带向量 —— 向量检索已生效」，
   建的过程中会显示进度（文件 x/y、算了多少块、请求 / 重试 / 失败次数、跑了多久、多快）。 */
function renderKb(kb) {
  let text;
  const live = (kb && kb.progress) || null;
  const building = Boolean(kb && kb.building);
  if (!kb || (!kb.chunks && !building)) {
    text = '还没开始建（knowledge/ 里可能没文档）。';
  } else if (building) {
    const parts = ['正在建'];
    if (live && live.files_total) parts.push(`文件 ${live.files_done}/${live.files_total}`);
    if (live && live.chunks) parts.push(`已切 ${live.chunks} 块`);
    if (live && live.vectors) parts.push(`已算向量 ${live.vectors} 块`);
    if (live && live.current) parts.push(`当前：${live.current}`);
    if (live && live.requests) {
      let calls = `请求 ${live.requests} 次`;
      if (live.retries) calls += `，重试 ${live.retries} 次`;
      if (live.failed) calls += `，失败 ${live.failed} 批`;
      parts.push(calls);
    }
    if (live && live.elapsed > 2) {
      let pace = `已用 ${humanSeconds(live.elapsed)}`;
      if (live.speed) pace += `，约 ${live.speed} 块/秒`;
      parts.push(pace);
    }
    text = `${parts.join('，')}。`;
  } else {
    const parts = [`收进来 ${kb.chunks} 块`];
    if (kb.vectors >= kb.chunks) {
      parts.push(`全部 ${kb.vectors} 块都带向量 —— 向量检索已生效`);
    } else if (kb.vectors) {
      parts.push(`${kb.vectors} 块带向量，还有 ${kb.chunks - kb.vectors} 块没有 —— 重启会接着补`);
    } else {
      parts.push('没有向量（原查询 + 英文检索词照常用）');
    }
    text = `${parts.join('，')}。`;
  }
  if (!building && kb && kb.embed_note) text += ` 切块：${kb.embed_note}。`;
  if (!building && kb && kb.error) text += ` 向量那一路的报错：${kb.error}`;
  kbStatusEl.textContent = text;
  if (knStatusEl) knStatusEl.textContent = text;
}

async function refreshKbStatus() {
  if (panelView.hidden) return;
  try {
    renderKb((await api('/api/admin/state')).kb);
  } catch (err) {
    /* 登录过期之类的问题交给下一次 load() 处理，这里不打扰 */
  }
}

/* ---------------- 仪表盘 ---------------- */

function paintBar(percent) {
  memBarEl.style.width = `${Math.max(0, Math.min(100, percent || 0))}%`;
  memBarEl.classList.toggle('warn', percent >= 85);
}

async function refreshSysinfo() {
  if (panelView.hidden) return;
  let info = null;
  try {
    info = await api('/api/admin/sysinfo');
  } catch (err) {
    if (err.status === 401) showLogin(err.message);
    else sysinfoHintEl.textContent = `读不到服务器状态：${err.message}`;
    return;
  }

  const cpu = info.cpu || {};
  cpuValueEl.textContent = cpu.percent === null || cpu.percent === undefined
    ? '—' : `${cpu.percent}%`;
  cpuNoteEl.textContent = cpu.percent === null
    ? '这台系统读不到（/proc 只在 Linux 上有）'
    : `${cpu.cores} 核` + (cpu.load ? ` · 负载 ${cpu.load.join(' / ')}` : '');

  const mem = info.mem;
  if (mem) {
    memValueEl.textContent = `${mem.percent}%`;
    memNoteEl.textContent = `已用 ${fmtSize(mem.used_mb * 1048576)} / 共 ` +
      `${fmtSize(mem.total_mb * 1048576)} · 还能用 ${fmtSize(mem.available_mb * 1048576)}` +
      (mem.swap_total_mb ? ` · swap ${fmtSize(mem.swap_used_mb * 1048576)}` : ' · 没开 swap');
    paintBar(mem.percent);
  } else {
    memValueEl.textContent = '—';
    memNoteEl.textContent = '这台系统读不到';
    paintBar(0);
  }

  const disk = info.disk;
  if (disk) {
    diskValueEl.textContent = `${disk.percent}%`;
    diskNoteEl.textContent = `已用 ${fmtSize(disk.used_mb * 1048576)} / 共 ` +
      `${fmtSize(disk.total_mb * 1048576)} · 剩 ${fmtSize(disk.free_mb * 1048576)}`;
  } else {
    diskValueEl.textContent = '—';
    diskNoteEl.textContent = '读不到';
  }

  const proc = info.process || {};
  procValueEl.textContent = proc.rss_mb ? fmtSize(proc.rss_mb * 1048576) : '—';
  procNoteEl.textContent = proc.uptime_s
    ? `开机以来 ${fmtDuration(proc.uptime_s)}`
    : '这是本进程占的内存';

  sysinfoHintEl.textContent = '每 5 秒自己刷一次。' +
    (mem && mem.percent >= 85 ? ' 内存快满了，注意别让它开太多会话！' : '');
}

/* ---------------- 访问流量 ---------------- */

let trafficDays = 7;
let trafficOverseas = false;
let trafficScanners = true;   // 是否过滤扫描器（默认过滤）
let trafficBusy = false;

const trafficHintEl = document.getElementById('traffic-hint');
const trafficDaysEl = document.getElementById('traffic-days');
const trafficDaysHintEl = document.getElementById('traffic-days-hint');
const trafficRegionsEl = document.getElementById('traffic-regions');
const trafficPathsEl = document.getElementById('traffic-paths');
const trafficStatusEl = document.getElementById('traffic-status');
const trafficIpsEl = document.getElementById('traffic-ips');

function barRows(rows) {
  const max = Math.max(1, ...rows.map((r) => r.value));
  return rows.map((r) => {
    const width = Math.max(1, Math.round((r.value / max) * 100));
    return `<div class="hbar">
      <div class="hbar-label" title="${r.label}">${r.label}</div>
      <div class="hbar-track"><span style="width:${width}%"></span></div>
      <div class="hbar-value">${r.text || r.value}</div>
    </div>`;
  }).join('');
}

function renderTraffic(info) {
  const t = info.totals || {};
  document.getElementById('t-pv').textContent = (t.pv ?? 0).toLocaleString();
  document.getElementById('t-ips').textContent = (t.ips ?? 0).toLocaleString();
  document.getElementById('t-req').textContent = (t.requests ?? 0).toLocaleString();
  document.getElementById('t-bytes').textContent = t.bytes_human || '—';
  document.getElementById('t-chat').textContent = (t.chat ?? 0).toLocaleString();

  const r = info.range || {};
  document.getElementById('t-req-note').textContent = r.from
    ? `${r.from} ~ ${r.to}` : '这段时间没有记录';
  document.getElementById('t-bytes-note').textContent =
    t.avg_rt ? `平均响应 ${t.avg_rt}s` : 'nginx 记的平均响应时间';
  document.getElementById('t-chat-note').textContent = '发到 /api/chat 的次数';

  const ex = info.excluded || {};
  const parts = [`本机 ${ex.loopback || 0}`, `扫描器 ${ex.bots || 0} 次 / ${ex.bot_ips || 0} 个 IP`, `机房/云 ${ex.idc || 0}`, `腾讯网段 ${ex.tencent || 0}`];
  if (!info.overseas) parts.push(`海外 ${ex.overseas || 0}`);
  if (!info.scanners) parts.unshift('（扫描器没过滤）');
  const scope = info.site_label && info.site_label !== '全部站点'
    ? `仅 ${info.site_label}` : '全部站点';
  trafficHintEl.textContent = `统计范围：${scope} · ${info.log_files || 0} 个日志文件 · 已排除：${parts.join(' · ')} · ` +
    `算于 ${info.generated_at || ''}（60 秒内不会重算）`;

  // 按天趋势
  const days = info.per_day || [];
  if (!days.length) {
    trafficDaysEl.innerHTML = '<div class="hint">这段时间没有记录喵</div>';
    trafficDaysHintEl.textContent = '';
  } else {
    const max = Math.max(1, ...days.map((d) => d.requests));
    trafficDaysEl.innerHTML = days.map((d) => {
      const h = Math.max(3, Math.round((d.requests / max) * 100));
      const title = `${d.date}：${d.requests} 次请求 / PV ${d.pv} / ${d.ips} 个 IP / 聊天 ${d.chat}`;
      // 手机没有 hover，所以整根柱子做成可点的，明细显示在下面那行
      return `<div class="day-col" role="button" tabindex="0" title="${title}"
                   data-date="${d.date}" data-req="${d.requests}" data-pv="${d.pv}"
                   data-ips="${d.ips}" data-chat="${d.chat}">
        <div class="day-num">${d.requests}</div>
        <div class="day-bar-wrap"><div class="day-bar" style="height:${h}%"></div></div>
        <div class="day-label">${d.date.slice(5)}</div>
      </div>`;
    }).join('');
    trafficDaysHintEl.textContent = '柱高按「总请求数」；点一下柱子，当天的明细显示在下面（电脑上也可以悬停）。';
    // 默认把最后一天选中，手机上一进来就有数看
    paintDayDetail(trafficDaysEl.lastElementChild);
  }

  // 地区分布
  trafficRegionsEl.innerHTML = (info.regions || []).length
    ? barRows(info.regions.map((x) => ({
        label: x.name, value: x.count, text: `${x.count} 个 · ${x.percent}%`,
      })))
    : '<div class="hint">没有可统计的地区</div>';

  // 热门路径
  trafficPathsEl.innerHTML = (info.paths || []).length
    ? barRows(info.paths.map((x) => ({ label: x.path, value: x.count, text: `${x.count} 次` })))
    : '<div class="hint">没有记录</div>';

  // 状态码
  trafficStatusEl.innerHTML = (info.status || []).length
    ? barRows(info.status.map((x) => ({ label: x.code, value: x.count, text: `${x.count} 次` })))
    : '<div class="hint">没有记录</div>';

  // 访问最多的 IP（全量，前端分页）
  ipListData = info.top_ips || [];
  ipPage = 1;
  renderIpList();
  loadBlacklist();
}

const trafficDayDetailEl = document.getElementById('traffic-day-detail');

function paintDayDetail(col) {
  if (!trafficDayDetailEl) return;
  trafficDaysEl.querySelectorAll('.day-col').forEach((c) => c.classList.toggle('on', c === col));
  if (!col || !col.dataset.date) {
    trafficDayDetailEl.textContent = '点柱子看当天明细';
    return;
  }
  const d = col.dataset;
  trafficDayDetailEl.innerHTML = `<b>${d.date}</b> · ${d.req} 次请求 · PV ${d.pv}` +
    ` · 独立 IP ${d.ips} · 聊天 ${d.chat}`;
}

// 点击 / 回车都能选中（手机上只有点击）
trafficDaysEl.addEventListener('click', (ev) => {
  const col = ev.target.closest ? ev.target.closest('.day-col') : null;
  if (col) paintDayDetail(col);
});
trafficDaysEl.addEventListener('keydown', (ev) => {
  if (ev.key !== 'Enter' && ev.key !== ' ') return;
  const col = ev.target.closest ? ev.target.closest('.day-col') : null;
  if (col) { ev.preventDefault(); paintDayDetail(col); }
});

/* 点 IP 弹窗：先看日志明细，再让当前接入的模型分析它在干什么 */

const ipModalEl = document.getElementById('ip-modal');
const ipModalTitleEl = document.getElementById('ip-modal-title');
const ipModalSubEl = document.getElementById('ip-modal-sub');
const ipModalDetailEl = document.getElementById('ip-modal-detail');
const ipModalAiEl = document.getElementById('ip-modal-ai');
const ipModalRunEl = document.getElementById('ip-modal-run');
const ipModalCloseEl = document.getElementById('ip-modal-close');
let ipModalIp = null;

function closeIpModal() {
  ipModalEl.hidden = true;
  ipModalIp = null;
}

function renderIpDetail(d) {
  const g = d.geo || {};
  const place = [g.country, g.region, g.city].filter(Boolean).join(' ');
  const rows = [];

  // 归属
  rows.push(`<div class="ip-meta">${place || '归属未知'}` +
    `${g.isp ? ' · ' + g.isp : ''}${g.as ? ' · ' + g.as : ''}` +
    `${g.idc ? ' · <b class="ip-flag">机房/云主机</b>' : ''}</div>`);

  // 规模 + 频率
  rows.push(`<div class="ip-meta">${d.first} ~ ${d.last}，共 <b>${d.count}</b> 次请求` +
    (d.rate_per_min ? ` · 平均 <b>${d.rate_per_min}</b> 次/分钟` : '') + '</div>');

  // 行为特征：这几个数字最能说明问题
  const rawHost = d.raw_host || 0;
  const appHits = d.app_hits || 0;
  const sensN = (d.sensitive || []).length;
  const loginN = (d.login || []).reduce((s, x) => s + x.count, 0);
  rows.push('<div class="ip-block"><div class="ip-label">行为特征</div>' +
    `<span class="ip-chip ${rawHost ? 'warn' : ''}">裸连 IP ${rawHost} 次</span>` +
    `<span class="ip-chip ${appHits ? '' : 'warn'}">加载前端资源 ${appHits} 次</span>` +
    `<span class="ip-chip ${sensN ? 'warn' : ''}">敏感路径 ${sensN} 个</span>` +
    `<span class="ip-chip ${loginN ? 'warn' : ''}">登录接口 ${loginN} 次</span>` +
    '</div>');

  // 敏感路径 + 状态码（判断有没有得手的关键）
  if (sensN) {
    rows.push('<div class="ip-block"><div class="ip-label">敏感路径命中（路径 × 次数 → 状态码）</div>' +
      d.sensitive.map((h) => `<span class="ip-chip warn">${h.path} ×${h.count} → ` +
        h.statuses.map(([c, n]) => `${c}×${n}`).join(' ') + '</span>').join('') + '</div>');
  }
  if (loginN) {
    rows.push('<div class="ip-block"><div class="ip-label">登录接口调用</div>' +
      d.login.map((h) => `<span class="ip-chip warn">${h.path} ×${h.count} → ` +
        h.statuses.map(([c, n]) => `${c}×${n}`).join(' ') + '</span>').join('') + '</div>');
  }

  // 状态码 / 路径 / UA
  rows.push('<div class="ip-block"><div class="ip-label">状态码</div>' +
    d.statuses.map(([c, n]) => `<span class="ip-chip">${c} × ${n}</span>`).join('') + '</div>');
  rows.push('<div class="ip-block"><div class="ip-label">访问路径</div>' +
    d.paths.slice(0, 10).map(([p, n]) => `<span class="ip-chip">${p} × ${n}</span>`).join('') + '</div>');
  rows.push('<div class="ip-block"><div class="ip-label">User-Agent</div>' +
    d.uas.map(([ua, n]) => `<span class="ip-chip">${n}× ${ua}</span>`).join('') + '</div>');

  // 时间线
  rows.push('<div class="ip-block"><div class="ip-label">时间线（前 ' + d.samples.length + ' 条）</div>' +
    '<pre class="ip-timeline">' + d.samples.map((s) =>
      `${s.t}  ${s.status}  ${s.path}`).join('\n') + '</pre></div>');

  ipModalDetailEl.innerHTML = rows.join('');
}

async function openIpModal(ip) {
  ipModalIp = ip;
  ipModalTitleEl.textContent = ip;
  ipModalSubEl.textContent = '正在读取日志…';
  ipModalDetailEl.innerHTML = '';
  ipModalAiEl.hidden = true;
  ipModalAiEl.textContent = '';
  ipModalRunEl.hidden = true;
  ipModalEl.hidden = false;

  try {
    const d = await api(`/api/admin/ip-detail?ip=${encodeURIComponent(ip)}&days=${trafficDays}`);
    if (!d.ok) {
      ipModalSubEl.textContent = d.error || '读不到记录';
      return;
    }
    ipModalSubEl.textContent = ip;
    ipModalBanned = !!d.banned;
    paintBanButton();
    renderIpDetail(d);
    runIpAnalysis();     // 打开就自动分析（后端有半小时缓存，重复点不重复烧 token）
  } catch (err) {
    ipModalSubEl.textContent = `读不到：${err.message}`;
  }
}

async function runIpAnalysis() {
  if (!ipModalIp) return;
  ipModalRunEl.hidden = true;
  ipModalAiEl.hidden = false;
  ipModalAiEl.textContent = '助手正在分析这个 IP 的日志…';
  ipModalAiEl.classList.add('busy');
  try {
    const r = await api('/api/admin/ip-analyze', { ip: ipModalIp, days: trafficDays });
    ipModalAiEl.textContent = r.analysis || '（没分析出东西）';
    ipModalAiEl.classList.remove('busy');
    if (r.cached) {
      ipModalAiEl.insertAdjacentHTML('beforeend', '\n\n（这半小时内分析过，直接用缓存）');
    }
    ipModalRunEl.hidden = false;
  } catch (err) {
    ipModalAiEl.classList.remove('busy');
    ipModalAiEl.textContent = `分析失败：${err.message}`;
    ipModalRunEl.hidden = false;
  }
}

trafficIpsEl.addEventListener('click', (ev) => {
  const btn = ev.target.closest ? ev.target.closest('.pager button') : null;
  if (btn && !btn.disabled) {
    ipPage += btn.dataset.page === 'prev' ? -1 : 1;
    renderIpList();
    return;
  }
  const row = ev.target.closest ? ev.target.closest('.row.clickable') : null;
  if (row && row.dataset.ip) openIpModal(row.dataset.ip);
});
trafficIpsEl.addEventListener('keydown', (ev) => {
  if (ev.key !== 'Enter' && ev.key !== ' ') return;
  const row = ev.target.closest ? ev.target.closest('.row.clickable') : null;
  if (row && row.dataset.ip) { ev.preventDefault(); openIpModal(row.dataset.ip); }
});
ipModalCloseEl.addEventListener('click', closeIpModal);
ipModalRunEl.addEventListener('click', runIpAnalysis);
ipModalEl.addEventListener('click', (ev) => {
  if (ev.target === ipModalEl) closeIpModal();   // 点遮罩关掉
});

/* 手动查任意 IP（被过滤掉的扫描器也能查/封） */

function initIpSearch() {
  const input = document.getElementById('ip-search-input');
  const btn = document.getElementById('ip-search-btn');
  if (!input || !btn) return;
  const go = () => {
    const ip = (input.value || '').trim();
    if (!ip) return;
    input.value = ip;
    openIpModal(ip);
  };
  btn.addEventListener('click', go);
  input.addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter') { ev.preventDefault(); go(); }
  });
}
initIpSearch();

/* AI 风险分析：规则粗筛 + 模型终审 */

const RISK_LABEL = { high: '高危', medium: '中危', low: '低危' };
let riskBusy = false;
let riskSite = 'all';   // 风险分析扫哪个站：all / www / api

function renderRiskList(data) {
  const list = document.getElementById('risk-list');
  if (!list) return;
  const items = (data && data.items) || [];
  if (!items.length) {
    list.innerHTML = '<div class="hint">这段时间没有可疑 IP</div>';
    return;
  }
  list.innerHTML = items.map((it) => `<div class="row clickable risk-row" role="button"
      tabindex="0" data-ip="${it.ip}">
      <div class="row-main">
        <span class="risk-badge ${it.risk}">${RISK_LABEL[it.risk] || it.risk}</span>
        <span class="risk-ip">${it.ip}</span>
        <span class="risk-site">${it.sites || ''}</span>
        <span class="row-sub">${it.role || ''}${it.place ? ' · ' + it.place.split(' ').slice(-2).join(' ') : ''}</span>
        <div class="risk-reason">${it.reason || ''}</div>
      </div>
      <button type="button" class="mini" data-ban="${it.ip}">封禁</button>
    </div>`).join('');
}

async function runRiskScan() {
  if (riskBusy) return;
  const btn = document.getElementById('risk-scan-btn');
  const hint = document.getElementById('risk-hint');
  if (!btn || !hint) return;
  riskBusy = true;
  btn.disabled = true;
  btn.textContent = '分析中…';
  hint.textContent = '先扫日志粗筛，再让模型判定，大概 5~10 秒';
  try {
    const r = await api('/api/admin/risk-scan', { days: trafficDays, site: riskSite });
    renderRiskList(r);
    const scope = r.site_label && r.site_label !== '全部站点' ? `仅 ${r.site_label} · ` : '';
    hint.textContent = `${scope}扫了 ${r.total_scanned || 0} 个 IP，挑出 ${(r.items || []).length} 个可疑的` +
      (r.cached ? '（30 分钟内的缓存结果）' : '');
  } catch (err) {
    hint.textContent = `分析失败：${err.message}`;
  } finally {
    riskBusy = false;
    btn.disabled = false;
    btn.textContent = '重新分析';
  }
}

async function banFromList(btn) {
  const ip = btn.dataset.ban;
  if (!ip) return;
  btn.disabled = true;
  btn.textContent = '封禁中…';
  try {
    const r = await api('/api/admin/blacklist', { ip, action: 'add', reason: 'AI 风险分析' });
    banList = r.bans || [];
    renderBlacklist();
    btn.textContent = '已封';
    btn.classList.add('done');
  } catch (err) {
    btn.textContent = '失败';
    btn.disabled = false;
  }
}

document.querySelectorAll('#risk-site button').forEach((btn) => {
  btn.addEventListener('click', () => {
    riskSite = btn.dataset.site || 'all';
    document.querySelectorAll('#risk-site button').forEach((b) => b.classList.toggle('on', b === btn));
  });
});

const riskScanBtnEl = document.getElementById('risk-scan-btn');
if (riskScanBtnEl) riskScanBtnEl.addEventListener('click', runRiskScan);

const riskListEl = document.getElementById('risk-list');
if (riskListEl) {
  riskListEl.addEventListener('click', (ev) => {
    const banBtn = ev.target.closest ? ev.target.closest('[data-ban]') : null;
    if (banBtn) { ev.stopPropagation(); banFromList(banBtn); return; }
    const row = ev.target.closest ? ev.target.closest('.row.clickable') : null;
    if (row && row.dataset.ip) openIpModal(row.dataset.ip);
  });
  riskListEl.addEventListener('keydown', (ev) => {
    if (ev.key !== 'Enter' && ev.key !== ' ') return;
    const row = ev.target.closest ? ev.target.closest('.row.clickable') : null;
    if (row && row.dataset.ip) { ev.preventDefault(); openIpModal(row.dataset.ip); }
  });
}

/* IP 黑名单：封禁 / 解封（后端写 UFW） */

let banList = [];
let ipModalBanned = false;

async function loadBlacklist() {
  const el = document.getElementById('traffic-blacklist');
  if (!el) return;
  try {
    const r = await api('/api/admin/blacklist');
    banList = r.bans || [];
  } catch (err) {
    el.innerHTML = `<div class="hint">读不到黑名单：${err.message}</div>`;
    return;
  }
  renderBlacklist();
}

function renderBlacklist() {
  const el = document.getElementById('traffic-blacklist');
  if (!el) return;
  if (!banList.length) {
    el.innerHTML = '<div class="hint">还没有封任何 IP</div>';
    return;
  }
  el.innerHTML = banList.map((b) => `<div class="row">
      <div class="row-main">${b.ip}<span class="row-sub">${b.ts || ''}${b.reason ? ' · ' + b.reason : ''}</span></div>
      <button type="button" class="mini" data-unban="${b.ip}">解封</button>
    </div>`).join('');
}

function paintBanButton() {
  const btn = document.getElementById('ip-modal-ban');
  if (!btn) return;
  btn.textContent = ipModalBanned ? '解封' : '封禁';
  btn.classList.toggle('danger', !ipModalBanned);
}

async function toggleBan() {
  if (!ipModalIp) return;
  const btn = document.getElementById('ip-modal-ban');
  const wantBan = !ipModalBanned;
  btn.disabled = true;
  btn.textContent = wantBan ? '封禁中…' : '解封中…';
  try {
    const r = await api('/api/admin/blacklist', {
      ip: ipModalIp,
      action: wantBan ? 'add' : 'remove',
      reason: wantBan ? '后台手动封禁' : '',
    });
    banList = r.bans || [];
    ipModalBanned = wantBan;
    renderBlacklist();
    paintBanButton();
    if (wantBan) {
      ipModalSubEl.textContent = `${ipModalIp} · 已封禁（UFW 已生效）`;
    } else {
      ipModalSubEl.textContent = `${ipModalIp} · 已解封`;
    }
  } catch (err) {
    btn.textContent = ipModalBanned ? '解封' : '封禁';
    ipModalSubEl.textContent = `操作失败：${err.message}`;
  } finally {
    btn.disabled = false;
  }
}

const ipModalBanEl = document.getElementById('ip-modal-ban');
if (ipModalBanEl) ipModalBanEl.addEventListener('click', toggleBan);
const trafficBlacklistEl = document.getElementById('traffic-blacklist');
if (trafficBlacklistEl) trafficBlacklistEl.addEventListener('click', async (ev) => {
  const btn = ev.target.closest ? ev.target.closest('[data-unban]') : null;
  if (!btn) return;
  const ip = btn.dataset.unban;
  btn.disabled = true;
  btn.textContent = '解封中…';
  try {
    const r = await api('/api/admin/blacklist', { ip, action: 'remove' });
    banList = r.bans || [];
    renderBlacklist();
  } catch (err) {
    btn.textContent = '失败';
  }
});

/* 「访问最多的 IP」分页：全量列，一页 IP_PAGE_SIZE 条 */

const IP_PAGE_SIZE = 15;
let ipListData = [];
let ipPage = 1;

function renderIpList() {
  const total = ipListData.length;
  if (!total) {
    trafficIpsEl.innerHTML = '<div class="hint">没有记录</div>';
    return;
  }
  const pages = Math.max(1, Math.ceil(total / IP_PAGE_SIZE));
  ipPage = Math.min(Math.max(1, ipPage), pages);
  const start = (ipPage - 1) * IP_PAGE_SIZE;
  const rows = ipListData.slice(start, start + IP_PAGE_SIZE);

  let html = rows.map((x) => `<div class="row clickable" role="button" tabindex="0" data-ip="${x.ip}">
      <div class="row-main">${x.ip}<span class="row-sub">${x.place || ''}</span></div>
      <div class="row-num">${x.count}</div>
    </div>`).join('');

  if (pages > 1) {
    html += `<div class="pager">
      <button type="button" data-page="prev" ${ipPage <= 1 ? 'disabled' : ''}>上一页</button>
      <span class="pager-info">第 ${ipPage} / ${pages} 页 · 共 ${total} 个</span>
      <button type="button" data-page="next" ${ipPage >= pages ? 'disabled' : ''}>下一页</button>
    </div>`;
  } else {
    html += `<div class="pager"><span class="pager-info">共 ${total} 个 IP</span></div>`;
  }
  trafficIpsEl.innerHTML = html;
}

async function loadTraffic() {
  if (panelView.hidden || trafficBusy) return;
  trafficBusy = true;
  trafficHintEl.textContent = '正在扫日志…（第一次可能要两三秒）';
  document.querySelectorAll('#traffic-range button').forEach((b) => {
    b.classList.toggle('on', Number(b.dataset.days) === trafficDays);
  });
  try {
    const info = await api(`/api/admin/traffic?days=${trafficDays}` +
      `&overseas=${trafficOverseas ? 1 : 0}&scanners=${trafficScanners ? 1 : 0}`);
    trafficBusy = false;
    if (!info.ok) {
      trafficHintEl.textContent = info.error || '算不出来';
      return;
    }
    renderTraffic(info);
  } catch (err) {
    trafficBusy = false;
    if (err.status === 401) showLogin(err.message);
    else trafficHintEl.textContent = `读不到流量：${err.message}`;
  }
}

/* ---------------- 表情包库 ---------------- */

function nextStickerName(mood, ext) {
  let max = 0;
  (library.moods[mood] || []).forEach((item) => {
    const stem = item.name.replace(/\.[^.]+$/, '');
    const digits = (stem.match(/\d+/g) || []).join('');
    const value = digits ? parseInt(digits, 10) : 0;
    if (value > max) max = value;
  });
  return `${mood}${max + 1}${ext}`;
}

function stickerCard(item, onDelete) {
  const card = el('div', 'sticker-card');
  const img = el('img', 'sticker-thumb');
  // 表情包跟着身份卡走，走同一个按人设取图的接口（不能再用 /static/stickers）
  img.src = `/api/persona/sticker/${encodeURIComponent(item.name)}`;
  img.alt = item.name;
  img.loading = 'lazy';
  const name = el('div', 'sticker-name');
  name.textContent = item.name;
  const size = el('div', 'sticker-size');
  size.textContent = fmtSize(item.size);
  const del = el('button', 'sticker-del');
  del.type = 'button';
  del.textContent = '删';
  del.addEventListener('click', () => onDelete(item.name));
  card.append(img, name, size, del);
  return card;
}

function renderStickers(data) {
  library = data;
  stickerTotalEl.textContent = data.total;
  stickerLibraryEl.innerHTML = '';
  MOODS.forEach((mood) => {
    const items = data.moods[mood] || [];
    const block = el('div', 'mood-block');
    const head = el('div', 'mood-head');
    head.textContent = `${mood}（${items.length}）`;
    block.appendChild(head);
    const grid = el('div', 'sticker-grid');
    if (!items.length) {
      const empty = el('div', 'mood-empty');
      empty.textContent = '还没有';
      grid.appendChild(empty);
    }
    items.forEach((item) => grid.appendChild(stickerCard(item, deleteSticker)));
    block.appendChild(grid);
    stickerLibraryEl.appendChild(block);
  });

  if (data.other && data.other.length) {
    const block = el('div', 'mood-block');
    const head = el('div', 'mood-head');
    head.textContent = `没归类的（${data.other.length}）—— 文件名开头不是那八个心情，助手用不上，建议改个名`;
    block.appendChild(head);
    const grid = el('div', 'sticker-grid');
    data.other.forEach((item) => grid.appendChild(stickerCard(item, deleteSticker)));
    block.appendChild(grid);
    stickerLibraryEl.appendChild(block);
  }
}

async function loadStickers() {
  try {
    renderStickers(await api('/api/admin/stickers'));
  } catch (err) {
    guard(err);
  }
}

async function deleteSticker(name) {
  if (!confirm(`确定把表情包 ${name} 删掉？删了就找不回来了。`)) return;
  try {
    const data = await api('/api/admin/stickers/delete', { name });
    renderStickers(data.library);
    flash(`删掉啦：${data.removed}`, true);
  } catch (err) {
    guard(err);
  }
}

async function uploadSticker() {
  const file = stickerFileEl.files && stickerFileEl.files[0];
  if (!file) {
    flash('先挑一张图片', false);
    return;
  }
  const ext = (file.name.match(/\.[^.]+$/) || [''])[0].toLowerCase();
  if (IMG_EXTS.indexOf(ext) < 0) {
    flash(`只收 ${IMG_EXTS.join(' / ')} `, false);
    return;
  }
  const name = nextStickerName(stickerMoodEl.value, ext);
  stickerUploadEl.disabled = true;
  try {
    const data = await apiUpload(`/api/admin/stickers/upload?name=${encodeURIComponent(name)}`, file);
    renderStickers(data.library);
    stickerFileEl.value = '';
    flash(`收下啦：${data.name}（${fmtSize(data.size)}）`, true);
  } catch (err) {
    guard(err);
  } finally {
    stickerUploadEl.disabled = false;
  }
}

async function importZip() {
  const file = stickerZipEl.files && stickerZipEl.files[0];
  if (!file) {
    flash('先挑一个 zip ', false);
    return;
  }
  if (!/\.zip$/i.test(file.name)) {
    flash('要 .zip 压缩包', false);
    return;
  }
  stickerImportEl.disabled = true;
  flash('正在解包…');
  try {
    const data = await apiUpload('/api/admin/stickers/import', file);
    renderStickers(data.library);
    stickerZipEl.value = '';
    const skipped = data.skipped || [];
    let text = `导进来 ${data.added.length} 张`;
    if (skipped.length) text += `，跳过 ${skipped.length} 个`;
    flash(text, true);
    if (skipped.length) {
      alert('这几张没收进来：\n' + skipped.slice(0, 20)
        .map(([name, why]) => `${name} —— ${why}`).join('\n') +
        (skipped.length > 20 ? `\n……还有 ${skipped.length - 20} 个` : ''));
    }
  } catch (err) {
    guard(err);
  } finally {
    stickerImportEl.disabled = false;
  }
}

/* ---------------- 工作区文件 ---------------- */

function wsRow(item) {
  const row = el('div', 'row');
  const icon = el('span', 'row-icon');
  icon.textContent = item.dir ? '📁' : '📄';
  const name = el('span', 'row-name');
  name.textContent = item.name;
  const meta = el('span', 'row-meta');
  meta.textContent = `${item.dir ? '目录' : fmtSize(item.size)} · ${fmtTime(item.mtime)}`;
  const main = el('div', 'file-main');
  main.append(name, meta);
  row.append(icon, main);

  if (item.dir) {
    row.classList.add('clickable');
    row.addEventListener('click', () => loadWorkspace(item.path));
  }
  const del = el('button', 'row-btn');
  del.type = 'button';
  del.textContent = '删除';
  del.addEventListener('click', async (event) => {
    event.stopPropagation();
    const what = item.dir ? `目录 ${item.path}（里面的东西一起删）` : `文件 ${item.path}`;
    if (!confirm(`确定删掉${what} ？`)) return;
    try {
      const data = await api('/api/admin/workspace/delete',
                             { path: item.path, recursive: item.dir });
      flash(data.message, true);
      loadWorkspace(wsDir);
    } catch (err) {
      guard(err);
    }
  });
  row.appendChild(del);
  return row;
}

async function loadWorkspace(dir) {
  try {
    const data = await api(`/api/admin/workspace?path=${encodeURIComponent(dir || '')}`);
    wsDir = data.dir === '.' ? '' : data.dir;
    wsDirEl.textContent = '/' + wsDir;
    wsListEl.innerHTML = '';

    if (wsDir) {
      const up = el('div', 'row clickable');
      const icon = el('span', 'row-icon');
      icon.textContent = '↩';
      const name = el('span', 'row-name');
      name.textContent = '返回上一级';
      up.append(icon, name);
      up.addEventListener('click', () => {
        const parts = wsDir.split('/');
        parts.pop();
        loadWorkspace(parts.join('/'));
      });
      wsListEl.appendChild(up);
    }

    if (!data.entries.length) {
      const empty = el('div', 'row empty');
      empty.textContent = '空目录';
      wsListEl.appendChild(empty);
    }
    data.entries.forEach((item) => wsListEl.appendChild(wsRow(item)));
    wsHintEl.textContent = data.truncated
      ? '文件太多，只列了前面一部分。'
      : '点目录名进去，点「删除」删掉。助手也能读写这里。';
  } catch (err) {
    guard(err);
  }
}

async function uploadWorkspace() {
  const file = wsFileEl.files && wsFileEl.files[0];
  if (!file) {
    flash('先挑一个文件', false);
    return;
  }
  let rel = wsPathEl.value.trim().replace(/\\/g, '/').replace(/^\/+/, '');
  if (!rel) rel = file.name;
  if (rel.endsWith('/')) rel += file.name;
  wsUploadEl.disabled = true;
  flash('正在上传…');
  try {
    const data = await apiUpload(`/api/admin/workspace/upload?path=${encodeURIComponent(rel)}`, file);
    flash(`传好了：${data.path}（${fmtSize(data.size)}）`, true);
    wsFileEl.value = '';
    wsPathEl.value = '';
    const parts = data.path.split('/');
    parts.pop();
    loadWorkspace(parts.join('/'));
  } catch (err) {
    guard(err);
  } finally {
    wsUploadEl.disabled = false;
  }
}

/* ---------------- 知识库文档 ---------------- */

/* 知识库改完会自动重启服务（索引是进程内建的），重启时这个页面会断线。
   所以等 /api/ping 重新通了自己刷一下，主人不用手动刷新。
   定时器存起来：重新进来先掐掉上一串，免得叠出多条轮询链。 */
let relaxTimer = null;

function relaxAfterRestart() {
  clearTimeout(relaxTimer);
  const started = Date.now();
  const tick = async () => {
    if (Date.now() - started > 90000) return;   // 一分半还没回来就不等了
    try {
      const resp = await fetch('/api/ping', { cache: 'no-store' });
      if (resp.ok && Date.now() - started > 9000) {
        location.reload();
        return;
      }
    } catch (err) {
      /* 还在重启，接着等 */
    }
    relaxTimer = setTimeout(tick, 3000);
  };
  relaxTimer = setTimeout(tick, 9000);
}

function knRow(item) {
  const row = el('div', 'row');
  const icon = el('span', 'row-icon');
  icon.textContent = item.supported ? '📕' : '⚠️';
  const name = el('span', 'row-name');
  name.textContent = item.path;
  const meta = el('span', 'row-meta');
  meta.textContent = `${fmtSize(item.size)} · ${fmtTime(item.mtime)}` +
    (item.supported ? '' : ' · 这格式进不了索引');
  const main = el('div', 'file-main');
  main.append(name, meta);
  row.append(icon, main);

  const del = el('button', 'row-btn danger');
  del.type = 'button';
  del.textContent = '删除';
  del.addEventListener('click', async () => {
    if (!confirm(`确定把 ${item.path} 从知识库删掉？删完会自动重启重建索引。`)) return;
    try {
      const data = await api('/api/admin/knowledge/delete', { path: item.path });
      flash(data.message, true);
      loadKnowledge();
      if (data.restart) relaxAfterRestart();
    } catch (err) {
      guard(err);
    }
  });
  row.appendChild(del);
  return row;
}

async function loadKnowledge() {
  try {
    const data = await api('/api/admin/knowledge');
    const files = data.files || [];
    knCountEl.textContent = files.length;
    knTotalEl.textContent = fmtSize(data.total_bytes);
    knListEl.innerHTML = '';
    if (!files.length) {
      const empty = el('div', 'row empty');
      empty.textContent = 'knowledge/ 还是空的，上面传一个文档进来吧。';
      knListEl.appendChild(empty);
    }
    files.forEach((item) => knListEl.appendChild(knRow(item)));
    knHintEl.textContent = (data.truncated ? '文件太多，只列了前面一部分。' : '') +
      `认的格式：${(data.extensions || []).join(' / ')}；单个上限 ${fmtSize(data.max_bytes)}。`;
    refreshKbStatus();
  } catch (err) {
    // 拿不到就别把标题那行「— 个」留着不管：写清楚为什么，别让人对着问号猜
    knCountEl.textContent = '—';
    knTotalEl.textContent = '—';
    knHintEl.textContent = '';
    knListEl.innerHTML = '';
    if (!err || err.status !== 401) {
      const bad = el('div', 'row empty');
      bad.textContent = err.message;
      knListEl.appendChild(bad);
    }
    guard(err);
  }
}

async function uploadKnowledge() {
  const file = knFileEl.files && knFileEl.files[0];
  if (!file) {
    flash('先挑一个文档', false);
    return;
  }
  let rel = knPathEl.value.trim().replace(/\\/g, '/').replace(/^\/+/, '');
  if (!rel) rel = file.name;
  if (rel.endsWith('/')) rel += file.name;
  knUploadEl.disabled = true;
  flash('正在上传…');
  try {
    const data = await apiUpload(`/api/admin/knowledge/upload?path=${encodeURIComponent(rel)}`, file);
    knFileEl.value = '';
    knPathEl.value = '';
    flash(`传好了：${data.path}（${fmtSize(data.size)}）`, true);
    loadKnowledge();
    if (data.restart) relaxAfterRestart();
  } catch (err) {
    guard(err);
  } finally {
    knUploadEl.disabled = false;
  }
}

/* ---------------- 检查更新 ---------------- */

let updateInfo = null;

function initUpdate() {
  if (lastState && lastState.update_remote) updateRemoteEl.value = lastState.update_remote;
}

function renderUpdate(data, applied) {
  updateInfo = data;
  // 按钮的可用状态只由「检查更新」的fresh结果决定：应用结果不在这里改，
  // 否则会拿旧的 updateInfo 把刚拉过的按钮又点亮 → 可重复触发 git pull + 重启。
  if (!applied) {
    updateApplyEl.disabled = data.error ? true : Boolean(data.up_to_date);
  }

  const lines = [];
  if (data.error) {
    lines.push(`<div class="bad-line">出错了：${escapeHtml(data.error)}</div>`);
  } else {
    lines.push(`<div>远端：<code>${escapeHtml(data.remote)}</code> ` +
               `<code>${escapeHtml(data.url)}</code> · 分支 <code>${escapeHtml(data.branch)}</code></div>`);
    lines.push(`<div>本地 <code>${escapeHtml(data.local)}</code> → 远端 ` +
               `<code>${escapeHtml(data.remote_head)}</code></div>`);
    lines.push(data.up_to_date
      ? '<div class="ok-line">已经是最新的了。</div>'
      : `<div class="ok-line">落后 ${data.behind} 个提交，可以更新：</div>`);
    if (data.commits && data.commits.length) {
      lines.push('<pre class="update-log">' +
                 escapeHtml(data.commits.join('\n')) + '</pre>');
    }
  }
  if (applied) {
    lines.push(`<div class="${applied.ok ? 'ok-line' : 'bad-line'}">${escapeHtml(applied.message)}</div>`);
  }
  if (!data.can_auto_restart) {
    lines.push('<div class="dim">没检测到 systemd 在管 <code>' + escapeHtml(data.service) +
               '</code>，更新完需要自己重启才生效。</div>');
  }
  updateResultEl.innerHTML = lines.join('');
}

function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, (ch) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[ch]);
}

async function checkUpdate() {
  updateCheckEl.disabled = true;
  updateResultEl.textContent = '正在问远端…';
  try {
    renderUpdate(await api(`/api/admin/update?remote=${encodeURIComponent(updateRemoteEl.value)}`));
  } catch (err) {
    if (err.status === 401) showLogin(err.message);
    else updateResultEl.innerHTML = `<div class="bad-line">${escapeHtml(err.message)}</div>`;
  } finally {
    updateCheckEl.disabled = false;
  }
}

async function applyUpdate() {
  if (!confirm('会用 git pull 拉最新版，然后重启服务（聊天页会断一下）。\n确定现在更新？')) return;
  updateApplyEl.disabled = true;
  updateResultEl.textContent = '正在拉取…';
  try {
    const data = await api('/api/admin/update', { remote: updateRemoteEl.value });
    if (updateInfo) renderUpdate(updateInfo, data);
    else {
      updateResultEl.innerHTML = `<div class="${data.ok ? 'ok-line' : 'bad-line'}">` +
        `${escapeHtml(data.message)}</div>`;
    }
    if (data.restart) {
      setTimeout(() => {
        location.reload();
      }, 6000);
    } else if (!data.ok) {
      // 没成功也没重启：放出来让主人重新「检查更新」后再试，别永久禁用
      updateApplyEl.disabled = false;
    }
  } catch (err) {
    if (err.status === 401) showLogin(err.message);
    else updateResultEl.innerHTML = `<div class="bad-line">${escapeHtml(err.message)}</div>`;
    updateApplyEl.disabled = false;   // 出错也放出来，允许重新检查后再试
  }
}

/* ---------------- 输入框里的值和当前生效的一样，就当没改过，不写盘 ---------------- */

function changed(el, current) {
  const value = el.value.trim();
  return value === current ? '' : value;
}

/* ---------------- 事件绑定 ---------------- */

initNav();
MOODS.forEach((mood) => {
  const option = document.createElement('option');
  option.value = mood;
  option.textContent = mood;
  stickerMoodEl.appendChild(option);
});

logoutEl.addEventListener('click', async () => {
  try {
    await api('/api/auth/logout', {});
  } catch (err) {
    /* 退不了也照样跳登录页 */
  }
  location.replace('/login');
});

saveModelsEl.addEventListener('click', async () => {
  const model = modelEl.value.trim();
  const vision = visionEl.value.trim();
  if (!model || !vision) {
    flash('聊天和图片的模型名都得填', false);
    return;
  }
  saveModelsEl.disabled = true;
  try {
    render(await api('/api/admin/models', {
      model,
      vision_model: vision,
      translate_model: translateEl.value.trim(),
    }));
    flash('模型存好了，下一条消息就生效', true);
  } catch (err) {
    guard(err);
  } finally {
    saveModelsEl.disabled = false;
  }
});

saveKeysEl.addEventListener('click', async () => {
  const body = {
    chat_key: chatKeyEl.value.trim(),
    vision_key: visionKeyEl.value.trim(),
    translate_key: translateKeyEl.value.trim(),
    chat_base_url: changed(chatBaseEl, lastState.chat_base_url),
    vision_base_url: changed(visionBaseEl, lastState.vision_base_url),
    translate_base_url: changed(translateBaseEl, lastState.translate_base_url),
  };
  if (!Object.values(body).some((value) => value)) {
    flash('什么都没改', false);
    return;
  }
  saveKeysEl.disabled = true;
  try {
    render(await api('/api/admin/keys', body));
    chatKeyEl.value = '';
    visionKeyEl.value = '';
    translateKeyEl.value = '';
    flash('接口设置存好了，下一条消息就生效', true);
  } catch (err) {
    guard(err);
  } finally {
    saveKeysEl.disabled = false;
  }
});

saveEmbedEl.addEventListener('click', async () => {
  const body = {
    embed_model: changed(embedModelEl, lastState.embed_model || ''),
    embed_base_url: changed(embedBaseEl, lastState.embed_base_url || ''),
    embed_key: embedKeyEl.value.trim(),
  };
  // 开关只有真的拨动过才提交；没动就保持「跟着配置走」，别把它钉死
  const switchChanged = Boolean(lastState) &&
    embedEnabledEl.checked !== Boolean(lastState.embed_on);
  // 单条输入上限同理：留空 / 0 = 自动探测。没动过就别提交，免得把填好的值冲成 0
  const rawMax = embedMaxTokensEl.value.trim();
  const maxNow = lastState ? (lastState.embed_max_tokens || 0) : 0;
  let maxValue = 0;
  if (rawMax !== '') {
    maxValue = parseInt(rawMax, 10);
    if (!Number.isFinite(maxValue) || maxValue < 0) {
      flash('单条输入上限得填个数字（留空或 0 = 自动探测）', false);
      return;
    }
    if (maxValue > 0 && maxValue < 32) {
      flash('单条输入上限别小于 32 token ，块会碎成几个字', false);
      return;
    }
  }
  const maxChanged = Boolean(lastState) && maxValue !== maxNow;
  if (!body.embed_model && !body.embed_base_url && !body.embed_key &&
      !switchChanged && !maxChanged) {
    flash('什么都没改', false);
    return;
  }
  if (switchChanged) body.embed_enabled = embedEnabledEl.checked;
  if (maxChanged) body.embed_max_tokens = maxValue;
  saveEmbedEl.disabled = true;
  try {
    render(await api('/api/admin/embed', body));
    embedKeyEl.value = '';
    flash(maxChanged
      ? '向量模型存好了，重启服务重建索引后按新的块长生效'
      : '向量模型存好了，重启服务后生效', true);
  } catch (err) {
    guard(err);
  } finally {
    saveEmbedEl.disabled = false;
  }
});

saveContextEl.addEventListener('click', async () => {
  const limit = parseInt(contextLimitEl.value.trim(), 10);
  if (!limit || limit < 4096) {
    flash('上下文上限得填个大于 4096 的数字（单位是 token）', false);
    return;
  }
  saveContextEl.disabled = true;
  try {
    render(await api('/api/admin/context', { limit }));
    flash('上下文上限存好了，立刻生效', true);
  } catch (err) {
    guard(err);
  } finally {
    saveContextEl.disabled = false;
  }
});

/* ---------------- 系统提示词（人设） ----------------
   整段提示词单独走一个接口（不塞进 _state()——那玩意儿每次刷配置都带一遍，太大了）。
   改完存在 admin.json 里，新开的会话立刻生效，不用重启。 */

function paintPromptCount() {
  promptCountEl.textContent = promptTextEl.value.length;
}

function renderPrompt(data) {
  promptTextEl.value = data.text || '';
  promptLimitEl.textContent = data.limit || 40000;
  promptStateEl.textContent = data.custom
    ? '现在用的是你改过的这份（存在 admin.json 里），新开的会话就用它。'
    : '现在用的是 config.py 里内置的那份（没改过）。在这里改完保存，就换成你改的。';
  resetPromptEl.disabled = !data.custom;   // 本来就是内置的，就没什么好「恢复」的
  paintPromptCount();
}

async function loadPrompt() {
  try {
    renderPrompt(await api('/api/admin/prompt'));
  } catch (err) {
    guard(err);
  }
}

promptTextEl.addEventListener('input', paintPromptCount);

savePromptEl.addEventListener('click', async () => {
  const text = promptTextEl.value.trim();
  if (!text) {
    flash('提示词不能空着喵——想用回内置的点「恢复内置的」', false);
    return;
  }
  savePromptEl.disabled = true;
  try {
    renderPrompt(await api('/api/admin/prompt', { text }));
    flash('提示词存好了，新开的会话立刻就是这份', true);
  } catch (err) {
    guard(err);
  } finally {
    savePromptEl.disabled = false;
  }
});

resetPromptEl.addEventListener('click', async () => {
  if (!confirm('把提示词恢复成 config.py 里内置的那份？你改的这份会被丢掉。')) return;
  resetPromptEl.disabled = true;
  try {
    renderPrompt(await api('/api/admin/prompt', { text: '' }));
    flash('恢复成内置的那份了', true);
  } catch (err) {
    resetPromptEl.disabled = false;   // 没恢复成功，得能再点
    guard(err);
  }
});

document.querySelectorAll('#traffic-range button').forEach((btn) => {
  btn.addEventListener('click', () => {
    trafficDays = Number(btn.dataset.days) || 7;
    loadTraffic();
  });
});
document.getElementById('traffic-scanners').addEventListener('change', (ev) => {
  trafficScanners = ev.target.checked;
  loadTraffic();
});
document.getElementById('traffic-overseas').addEventListener('change', (ev) => {
  trafficOverseas = ev.target.checked;
  loadTraffic();
});
document.getElementById('traffic-refresh').addEventListener('click', loadTraffic);

stickerUploadEl.addEventListener('click', uploadSticker);
stickerImportEl.addEventListener('click', importZip);
knUploadEl.addEventListener('click', uploadKnowledge);
wsUploadEl.addEventListener('click', uploadWorkspace);
updateCheckEl.addEventListener('click', checkUpdate);
updateApplyEl.addEventListener('click', applyUpdate);

window.addEventListener('hashchange', () => {
  const name = location.hash.slice(2);
  if (VIEW_HOOKS[name] && name !== activeView) showView(name);
});

/* 每 5 秒刷一次「当前这个面板」里会变的东西（仪表盘 / 索引进度） */
const POLLED = { dashboard: refreshSysinfo, embed: refreshKbStatus, knowledge: refreshKbStatus };
setInterval(() => {
  const fn = POLLED[activeView];
  if (fn) fn();
}, 5000);

load();
/* ---------------- 用户管理 ----------------
   列表、建号、改权限、封号、重置密码、备注、删除。
   所有操作都走后端 /api/admin/users，后端会再校验一次「你是不是管理员」。 */

let usersCache = [];

function fmtTime(ts) {
  if (!ts) return '—';
  const d = new Date(Number(ts) * 1000);
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ` +
    `${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function userMsg(text, ok) {
  const node = document.getElementById('user-msg');
  if (!node) return;
  node.hidden = !text;
  node.textContent = text || '';
  node.classList.toggle('ok', Boolean(ok));
}

function userOp(text, handler, disabled, danger) {
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'user-op' + (danger ? ' danger' : '');
  btn.textContent = text;
  btn.disabled = Boolean(disabled);
  btn.addEventListener('click', handler);
  return btn;
}

async function loadUsers() {
  try {
    const data = await api('/api/admin/users');
    usersCache = data.users || [];
    renderUsers(data.me);
    userMsg('');
  } catch (err) {
    userMsg(err && err.message ? err.message : '用户列表拉不到', false);
  }
}

function renderUsers(meId) {
  const tbody = document.getElementById('user-rows');
  if (!tbody) return;
  tbody.innerHTML = '';
  usersCache.forEach((user) => {
    const tr = document.createElement('tr');
    const isMe = user.id === meId;
    const badge = user.is_admin
      ? '<span class="user-badge admin">管理员</span>'
      : '<span class="user-badge">普通</span>';
    let banned = '';
    if (user.banned) {
      banned = `<span class="user-badge banned">已封号（${fmtBanLeft(user.ban_until)}）</span>`;
    } else if (user.disabled) {
      banned = '<span class="user-badge">已解封（到期）</span>';
    }
    tr.innerHTML = `
      <td class="user-name">${escapeHtml(user.name)}${isMe ? ' <span class="user-badge me">我</span>' : ''}</td>
      <td><code>${escapeHtml(user.invite_code)}</code></td>
      <td>${escapeHtml(user.invited_by || '—')}</td>
      <td>${user.sessions}</td>
      <td>${fmtTime(user.created_at)}</td>
      <td>${fmtTime(user.last_login)}</td>
      <td>${fmtTime(user.last_active)}</td>
      <td title="${escapeHtml(user.last_ip || '')}">${escapeHtml(user.ip_location || '—')}</td>
      <td>${badge}${banned}</td>`;
    const ops = document.createElement('td');
    ops.className = 'user-ops';
    ops.append(
      userOp(isMe ? '自己' : (user.is_admin ? '取消管理员' : '设为管理员'),
        () => toggleAdmin(user), isMe),
      userOp(user.banned ? '解封' : '封号', () => toggleBan(user), isMe),
      userOp('改密码', () => resetPassword(user), false),
      userOp('备注', () => editNote(user), false),
      userOp('删除', () => removeUser(user), isMe, true),
    );
    tr.appendChild(ops);
    tbody.appendChild(tr);
  });
}

async function patchUser(user, body, okText) {
  try {
    await api(`/api/admin/users/${user.id}`, body, 'PATCH');
    await loadUsers();
    userMsg(okText || '改好了', true);
  } catch (err) {
    userMsg(err && err.message ? err.message : '改不了', false);
  }
}

function toggleAdmin(user) {
  return patchUser(user, { is_admin: !user.is_admin },
    user.is_admin ? `已经取消「${user.name}」的管理员权限` : `「${user.name}」现在是管理员`);
}

/* 封禁时长：支持 30m / 12h / 7d，或者纯数字（当分钟），0 = 永久 */
function parseBanDuration(text) {
  const raw = String(text || '').trim().toLowerCase();
  if (!raw) return null;
  if (['0', '永久', 'forever', 'permanent', 'inf'].includes(raw)) return 0;
  const match = raw.match(/^(\d+)\s*([mhd]?)$/);
  if (!match) return null;
  const amount = parseInt(match[1], 10);
  if (amount === 0) return 0;
  const unit = match[2] || 'm';
  const perUnit = unit === 'd' ? 1440 : unit === 'h' ? 60 : 1;
  return amount * perUnit;
}

function fmtBanLeft(until) {
  const target = Number(until) || 0;
  if (!target) return '永久';
  const left = target - Date.now() / 1000;
  if (left <= 0) return '已到期';
  if (left < 3600) return Math.ceil(left / 60) + ' 分钟后解封';
  if (left < 86400) return Math.ceil(left / 3600) + ' 小时后解封';
  return Math.ceil(left / 86400) + ' 天后解封';
}

function toggleBan(user) {
  // 已经封着的，点一下就是解封
  if (user.banned) {
    return patchUser(user, { disabled: false }, `已经解封「${user.name}」`);
  }
  const answer = window.prompt(
    `封「${user.name}」多久？\n\n` +
    '填法：30m（30 分钟）· 12h（12 小时）· 7d（7 天）· 0（永久封禁）',
    '1d');
  if (answer === null) return undefined;
  const minutes = parseBanDuration(answer);
  if (minutes === null) {
    userMsg('看不懂这个时长：用 30m / 12h / 7d / 0 这种写法', false);
    return undefined;
  }
  return patchUser(
    user,
    { disabled: true, ban_minutes: minutes },
    minutes
      ? `已封「${user.name}」，${fmtBanLeft(Date.now() / 1000 + minutes * 60).replace('后解封', '后自动解封')}`
      : `已永久封禁「${user.name}」`,
  );
}

function resetPassword(user) {
  const pass = window.prompt(`给「${user.name}」设个新密码（至少 8 位）`);
  if (!pass) return undefined;
  return patchUser(user, { password: pass }, `「${user.name}」的密码改好了，他的登录态已全部失效`);
}

function editNote(user) {
  const note = window.prompt(`给「${user.name}」写个备注`, user.note || '');
  if (note === null) return undefined;
  return patchUser(user, { note }, '备注存好了');
}

async function removeUser(user) {
  if (!window.confirm(`删掉用户「${user.name}」和他所有会话？删了就回不来了`)) return;
  try {
    await api(`/api/admin/users/${user.id}`, undefined, 'DELETE');
    await loadUsers();
    userMsg(`已经把「${user.name}」删掉了`, true);
  } catch (err) {
    userMsg(err && err.message ? err.message : '删不掉', false);
  }
}

document.getElementById('user-create').addEventListener('click', async () => {
  const nameEl = document.getElementById('user-new-name');
  const passEl = document.getElementById('user-new-pass');
  const adminEl = document.getElementById('user-new-admin');
  const noteEl = document.getElementById('user-new-note');
  const name = nameEl.value.trim();
  const password = passEl.value;
  if (!name || !password) {
    userMsg('用户名和密码都得填', false);
    return;
  }
  try {
    await api('/api/admin/users', {
      name, password,
      is_admin: adminEl.checked,
      note: noteEl.value.trim(),
    });
    nameEl.value = '';
    passEl.value = '';
    noteEl.value = '';
    adminEl.checked = false;
    await loadUsers();
    userMsg(`「${name}」建好了`, true);
  } catch (err) {
    userMsg(err && err.message ? err.message : '建不了', false);
  }
});

/* ---------------- 身份卡（人设） ----------------
   一张身份卡 = 一套人设：名字、称呼、欢迎语、状态文案、提示词、表情包。
   切到哪张，聊天页就变成哪张；表情包跟着当前身份卡走。 */

let personaList = [];
let editingPersona = '';

function personaMsg(text, ok) {
  const node = document.getElementById('persona-msg');
  if (!node) return;
  node.hidden = !text;
  node.textContent = text || '';
  node.classList.toggle('ok', Boolean(ok));
}

function personaFormEls() {
  return {
    id: document.getElementById('persona-id'),
    name: document.getElementById('persona-name'),
    self: document.getElementById('persona-self'),
    user: document.getElementById('persona-user'),
    welcome: document.getElementById('persona-welcome'),
    placeholder: document.getElementById('persona-placeholder'),
    online: document.getElementById('persona-online'),
    thinking: document.getElementById('persona-thinking'),
    theme: document.getElementById('persona-theme'),
    prompt: document.getElementById('persona-prompt'),
  };
}

function resetPersonaForm() {
  editingPersona = '';
  const els = personaFormEls();
  Object.values(els).forEach((el) => {
    if (!el) return;
    el.value = el.type === 'color' ? '#ffb7d5' : '';
  });
  if (els.id) els.id.disabled = false;
  document.getElementById('persona-form-title').textContent = '新建身份卡';
  document.getElementById('persona-save').textContent = '创建';
  document.getElementById('persona-cancel').hidden = true;
  personaMsg('');
}

async function loadPersonas() {
  try {
    const data = await api('/api/admin/personas');
    personaList = data.personas || [];
    renderPersonas(data.current);
  } catch (err) {
    personaMsg(err && err.message ? err.message : '身份卡拉不到', false);
  }
}

function renderPersonas(currentId) {
  const tbody = document.getElementById('persona-rows');
  if (!tbody) return;
  tbody.innerHTML = '';
  personaList.forEach((item) => {
    const tr = document.createElement('tr');
    const isCurrent = item.id === currentId;
    tr.innerHTML = `
      <td><code>${escapeHtml(item.id)}</code></td>
      <td class="user-name">${escapeHtml(item.name || item.id)}</td>
      <td>${escapeHtml(item.user_name || '—')}</td>
      <td>${item.stickers_count}</td>
      <td>${isCurrent ? '<span class="persona-badge">正在用</span>' : '—'}</td>`;
    const ops = document.createElement('td');
    ops.className = 'user-ops';
    ops.append(
      userOp(isCurrent ? '已启用' : '启用', () => activatePersona(item), isCurrent),
      userOp('编辑', () => editPersona(item), false),
      userOp('导出', () => exportPersona(item), false),
      userOp('删掉', () => removePersona(item), isCurrent, true),
    );
    tr.appendChild(ops);
    tbody.appendChild(tr);
  });
}

async function activatePersona(item) {
  try {
    await api(`/api/admin/personas/${item.id}/activate`, {}, 'POST');
    await loadPersonas();
    personaMsg(`已经切到「${item.name || item.id}」，刷新聊天页就能看到`, true);
  } catch (err) {
    personaMsg(err && err.message ? err.message : '切不了', false);
  }
}

async function editPersona(item) {
  try {
    const detail = await api(`/api/admin/personas/${item.id}`);
    const els = personaFormEls();
    editingPersona = item.id;
    els.id.value = detail.id || item.id;
    els.id.disabled = true;
    els.name.value = detail.name || '';
    els.self.value = detail.self_name || '';
    els.user.value = detail.user_name || '';
    els.welcome.value = detail.welcome || '';
    els.placeholder.value = detail.placeholder || '';
    els.online.value = detail.status_online || '';
    els.thinking.value = detail.status_thinking || '';
    if (els.theme) els.theme.value = detail.theme || '#ffb7d5';
    const avPrev = document.getElementById('persona-avatar-preview');
    if (avPrev) avPrev.src = detail.avatar ? detail.avatar : '/static/avatar.jpg';
    const icPrev = document.getElementById('persona-icon-preview');
    if (icPrev) icPrev.src = detail.icopath || (`/api/persona/icon?pid=${encodeURIComponent(item.id)}`);
    els.prompt.value = detail.prompt || '';
    document.getElementById('persona-form-title').textContent = `编辑「${detail.name || detail.id}」`;
    document.getElementById('persona-save').textContent = '保存修改';
    document.getElementById('persona-cancel').hidden = false;
    personaMsg('');
    window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' });
  } catch (err) {
    personaMsg(err && err.message ? err.message : '读不出这张身份卡', false);
  }
}

async function removePersona(item) {
  if (!window.confirm(`删掉身份卡「${item.name || item.id}」？提示词和表情包一起没`)) return;
  try {
    await api(`/api/admin/personas/${item.id}`, undefined, 'DELETE');
    await loadPersonas();
    personaMsg('删掉了', true);
  } catch (err) {
    personaMsg(err && err.message ? err.message : '删不掉', false);
  }
}

async function savePersona() {
  const els = personaFormEls();
  const payload = {
    name: els.name.value.trim(),
    self_name: els.self.value.trim(),
    user_name: els.user.value.trim(),
    welcome: els.welcome.value.trim(),
    placeholder: els.placeholder.value.trim(),
    status_online: els.online.value.trim(),
    status_thinking: els.thinking.value.trim(),
    theme: els.theme ? els.theme.value : '',
    prompt: els.prompt.value,
  };
  if (!payload.name) {
    personaMsg('名字得填', false);
    return;
  }
  try {
    if (editingPersona) {
      await api(`/api/admin/personas/${editingPersona}`, payload, 'PATCH');
      personaMsg('改好啦', true);
    } else {
      payload.id = els.id.value.trim().toLowerCase();
      if (!payload.id) {
        personaMsg('ID 得填（小写字母开头）', false);
        return;
      }
      await api('/api/admin/personas', payload, 'POST');
      personaMsg('建好啦，点「启用」就能用', true);
    }
    resetPersonaForm();
    await loadPersonas();
  } catch (err) {
    personaMsg(err && err.message ? err.message : '存不下来', false);
  }
}

document.getElementById('persona-save').addEventListener('click', savePersona);
document.getElementById('persona-cancel').addEventListener('click', resetPersonaForm);

/* ---------------- 工具开关 ---------------- */

let toolGroupsCache = [];

function toolsMsg(text, ok) {
  const node = document.getElementById('tools-msg');
  if (!node) return;
  node.hidden = !text;
  node.textContent = text || '';
  node.classList.toggle('ok', Boolean(ok));
}

async function loadTools() {
  try {
    const data = await api('/api/admin/tools');
    toolGroupsCache = data.groups || [];
    renderTools(data);
  } catch (err) {
    toolsMsg(err && err.message ? err.message : '工具列表拉不到', false);
  }
}

function renderTools(data) {
  const box = document.getElementById('tool-groups');
  if (!box) return;
  box.innerHTML = '';
  const active = new Set((data.groups || []).filter((g) => g.enabled).map((g) => g.id));
  (data.groups || []).forEach((group) => {
    const card = document.createElement('div');
    card.className = 'tool-group' + (active.has(group.id) ? ' on' : '');
    const tools = group.tools.map((t) => `<code>${escapeHtml(t)}</code>`).join('');
    card.innerHTML = `
      <label>
        <input type="checkbox" data-group="${escapeHtml(group.id)}"
               ${active.has(group.id) ? 'checked' : ''}>
        ${escapeHtml(group.label)}（${group.count}）
      </label>
      <div class="tg-desc">${escapeHtml(group.desc || '')}</div>
      <div>${tools}</div>`;
    const checkbox = card.querySelector('input');
    checkbox.addEventListener('change', () => {
      card.classList.toggle('on', checkbox.checked);
    });
    box.appendChild(card);
  });
  const summary = document.getElementById('tools-summary');
  if (summary) {
    summary.textContent = `模型现在能看到 ${data.active} / ${data.total} 个工具`;
  }
  toolsMsg('');
}

async function saveTools() {
  const checked = Array.from(document.querySelectorAll('#tool-groups input:checked'))
    .map((el) => el.dataset.group);
  try {
    const data = await api('/api/admin/tools', { groups: checked }, 'POST');
    renderTools(data);
    toolsMsg('存好了，下一轮对话就按这个走', true);
  } catch (err) {
    toolsMsg(err && err.message ? err.message : '存不下来', false);
  }
}

document.getElementById('tools-save').addEventListener('click', saveTools);
document.getElementById('tools-all').addEventListener('click', async () => {
  document.querySelectorAll('#tool-groups input').forEach((el) => {
    el.checked = true;
    el.closest('.tool-group').classList.add('on');
  });
  await saveTools();
});

/* ---------------- 配置总览（只读） ---------------- */

async function loadConfig() {
  const box = document.getElementById('config-groups');
  const msg = document.getElementById('config-msg');
  if (!box) return;
  try {
    const data = await api('/api/admin/config');
    box.innerHTML = '';
    (data.groups || []).forEach((group) => {
      const wrap = document.createElement('div');
      wrap.className = 'config-group';
      const title = document.createElement('h3');
      title.textContent = group.name;
      wrap.appendChild(title);
      (group.items || []).forEach((item) => {
        const row = document.createElement('div');
        row.className = 'config-item';
        const label = document.createElement('div');
        label.className = 'ci-label';
        label.textContent = item.label;
        const right = document.createElement('div');
        const value = document.createElement('div');
        value.className = 'ci-value';
        value.textContent = item.value || '（空）';
        const meta = document.createElement('div');
        meta.className = 'ci-meta';
        meta.innerHTML =
          `改法：${escapeHtml(item.source)} · 默认：${escapeHtml(item.default || '—')}` +
          (item.restart ? '<span class="ci-badge">改完要重启</span>' : '') +
          (item.secret ? '<span class="ci-badge">密钥</span>' : '') +
          (item.desc ? `<br>${escapeHtml(item.desc)}` : '');
        right.append(value, meta);
        row.append(label, right);
        wrap.appendChild(row);
      });
      box.appendChild(wrap);
    });
    if (msg) msg.hidden = true;
  } catch (err) {
    if (msg) {
      msg.hidden = false;
      msg.textContent = err && err.message ? err.message : '配置拉不到';
    }
  }
}

/* ---------------- 共享约束提示词 ---------------- */

function corePromptMsg(text, ok) {
  const node = document.getElementById('core-prompt-msg');
  if (!node) return;
  node.hidden = !text;
  node.textContent = text || '';
  node.classList.toggle('ok', Boolean(ok));
}

async function loadCorePrompt() {
  try {
    const data = await api('/api/admin/core-prompt');
    const textarea = document.getElementById('core-prompt-text');
    if (textarea) textarea.value = data.text || '';
    const state = document.getElementById('core-prompt-state');
    if (state) {
      state.textContent = data.custom
        ? `正在用后台改过的版本（上限 ${data.limit} 字）`
        : '当前是内置默认版本（没改过）';
    }
    corePromptMsg('');
  } catch (err) {
    corePromptMsg(err && err.message ? err.message : '共享约束拉不到', false);
  }
}

async function saveCorePrompt() {
  const textarea = document.getElementById('core-prompt-text');
  try {
    await api('/api/admin/core-prompt', { text: textarea.value }, 'POST');
    await loadCorePrompt();
    corePromptMsg('存好了，新会话立刻生效', true);
  } catch (err) {
    corePromptMsg(err && err.message ? err.message : '存不下来', false);
  }
}

document.getElementById('core-prompt-save').addEventListener('click', saveCorePrompt);
document.getElementById('core-prompt-reset').addEventListener('click', async () => {
  try {
    const data = await api('/api/admin/core-prompt');
    document.getElementById('core-prompt-text').value = data.default || '';
    corePromptMsg('已经填回默认版本，点保存才生效', true);
  } catch (err) {
    corePromptMsg('恢复不了', false);
  }
});

/* ---------------- 身份卡导入 / 导出 ---------------- */

function exportPersona(item) {
  window.open(`/api/admin/personas/${encodeURIComponent(item.id)}/export`, '_blank');
}

document.getElementById('persona-import-btn').addEventListener('click', () => {
  document.getElementById('persona-import-file').click();
});

document.getElementById('persona-import-file').addEventListener('change', async (event) => {
  const file = event.target.files && event.target.files[0];
  event.target.value = '';
  if (!file) return;
  try {
    const resp = await fetch('/api/admin/personas/import', {
      method: 'POST',
      headers: { 'Content-Type': 'application/zip' },
      body: file,
    });
    let data = null;
    try { data = await resp.json(); } catch (err) { data = null; }
    if (!resp.ok) {
      personaMsg((data && data.detail) || ('导入失败（' + resp.status + '）'), false);
      return;
    }
    await loadPersonas();
    personaMsg(`已经导入身份卡「${data.id}」（${data.files} 个文件），点「启用」就能用`, true);
  } catch (err) {
    personaMsg('导入失败：' + (err && err.message ? err.message : err), false);
  }
});

/* ---------------- 身份卡：头像 / 图标上传 ---------------- */

async function uploadPersonaAsset(kind) {
  const state = editingPersona || (document.getElementById('persona-id').value || '').trim();
  if (!state) {
    personaMsg('先填好 ID 再传图', false);
    return;
  }
  if (!editingPersona) {
    personaMsg('先把这张卡存下来（点创建），再传头像', false);
    return;
  }
  const input = document.getElementById(kind === 'avatar' ? 'persona-avatar-file' : 'persona-icon-file');
  const file = input.files && input.files[0];
  input.value = '';
  if (!file) return;
  try {
    const resp = await fetch(`/api/admin/personas/${encodeURIComponent(editingPersona)}/asset?kind=${kind}`, {
      method: 'POST',
      headers: { 'Content-Type': file.type || 'application/octet-stream' },
      body: file,
    });
    let data = null;
    try { data = await resp.json(); } catch (err) { data = null; }
    if (!resp.ok) {
      personaMsg((data && data.detail) || ('上传失败（' + resp.status + '）'), false);
      return;
    }
    const preview = document.getElementById(kind === 'avatar' ? 'persona-avatar-preview' : 'persona-icon-preview');
    if (preview) preview.src = data.url + '&v=' + Date.now();
    personaMsg(kind === 'avatar' ? '头像换好啦' : '网页图标换好啦，刷新页面就能看到', true);
    await loadPersonas();
  } catch (err) {
    personaMsg('上传失败：' + (err && err.message ? err.message : err), false);
  }
}

document.getElementById('persona-avatar-btn').addEventListener('click', () => {
  document.getElementById('persona-avatar-file').click();
});
document.getElementById('persona-icon-btn').addEventListener('click', () => {
  document.getElementById('persona-icon-file').click();
});
document.getElementById('persona-avatar-file').addEventListener('change', () => uploadPersonaAsset('avatar'));
document.getElementById('persona-icon-file').addEventListener('change', () => uploadPersonaAsset('icon'));
