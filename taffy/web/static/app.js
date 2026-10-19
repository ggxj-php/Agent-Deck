'use strict';

/* 网页前端：把后端的 SSE 事件流画成聊天记录。
   会话 id 存在 sessionStorage，点「新会话」换一个 id，历史就重开一份。
   用 sessionStorage 而不是 localStorage：它按标签页隔离，开两个标签页就是
   两个独立会话；同一标签页刷新仍保留，关掉标签页才丢。 */

const STORAGE_KEY = 'taffy_session_id';
/* 「深度思考」开关存 localStorage（跟会话 id 不一样：这个希望同一个浏览器
   里所有标签页共用、刷新也在）。默认是开。 */
const THINK_KEY = 'taffy_thinking';
const AVATAR = '/static/avatar.jpg';

/* 表情包清单从后端读（后台可以增删，加了新的刷新一下就有）。
   读不到就用下面这份内置的兜底，反正不能因为接口挂了就不发表情包。
   文件名带后缀，所以 gif 直接就是动图。 */
const FALLBACK_STICKERS = {
  happy: ['happy1.png', 'happy2.png', 'happy3.png'],
  think: ['think1.png', 'think2.png', 'think3.png'],
  confused: ['confused1.png', 'confused2.png', 'confused3.png'],
  proud: ['proud1.png', 'proud2.png', 'proud3.png'],
  cry: ['cry1.png', 'cry2.png'],
  angry: ['angry1.png', 'angry2.png', 'angry3.png'],
  sleepy: ['sleepy1.png', 'sleepy2.png'],
  love: ['love1.png', 'love2.png', 'love3.png'],
};

let stickerLib = null;

async function loadStickerLib() {
  try {
    const resp = await fetch('/api/stickers', { cache: 'no-store' });
    if (resp.ok) {
      const data = await resp.json();
      if (data && typeof data === 'object') stickerLib = data;
    }
  } catch (err) {
    /* 用兜底那份 */
  }
}

function stickerPool(mood) {
  const fromServer = stickerLib && stickerLib[mood];
  if (fromServer && fromServer.length) {
    return fromServer.map((item) => item.name || item);
  }
  return FALLBACK_STICKERS[mood] || [];
}

const messagesEl = document.getElementById('messages');
const formEl = document.getElementById('composer');
const inputEl = document.getElementById('input');
const sendEl = document.getElementById('send');
const statusEl = document.getElementById('status');
const newChatEl = document.getElementById('new-chat');
const toBottomEl = document.getElementById('to-bottom');
const pickImageEl = document.getElementById('pick-image');
const imageInputEl = document.getElementById('image-input');
const attachBarEl = document.getElementById('attach-bar');
const attachPreviewEl = document.getElementById('attach-preview');
const attachRemoveEl = document.getElementById('attach-remove');
const saveSessionEl = document.getElementById('save-session');
const saveModalEl = document.getElementById('save-modal');
const saveUuidEl = document.getElementById('save-uuid');
const saveMsgEl = document.getElementById('save-msg');
const saveOkEl = document.getElementById('save-ok');
const saveCancelEl = document.getElementById('save-cancel');
const loadSessionEl = document.getElementById('load-session');
const loadModalEl = document.getElementById('load-modal');
const loadUuidEl = document.getElementById('load-uuid');
const loadMsgEl = document.getElementById('load-msg');
const loadOkEl = document.getElementById('load-ok');
const loadCancelEl = document.getElementById('load-cancel');
const ctxBarEl = document.getElementById('ctx-bar');
const ctxFillEl = document.getElementById('ctx-fill');
const ctxTextEl = document.getElementById('ctx-text');
const tbMoreEl = document.getElementById('tb-more');
const tbActionsEl = document.getElementById('tb-actions');
const thinkToggleEl = document.getElementById('think-toggle');

let sessionId = sessionStorage.getItem(STORAGE_KEY);
let busy = false;
let sentCount = 0;       // 这个页面聊过几句，存上下文之前用它判断有没有东西可存
let pendingImage = null; // 已经选好、还没发出去的图片（data URL）

marked.setOptions({ gfm: true, breaks: true });

const el = (tag, cls) => {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  return node;
};

/* ---------------- 数学式子 ---------------- */

/* 模型偶尔还是漏出 LaTeX，雏草姬看不懂，这里把它翻成人能读的写法。
   代码块和行内代码里的内容一律原样保留，绝不改动。 */
const MATH_WORDS = {
  times: '×', cdot: '·', div: '÷', pm: '±', mp: '∓',
  le: '≤', leq: '≤', ge: '≥', geq: '≥', ne: '≠', neq: '≠',
  approx: '≈', equiv: '≡', sim: '∼', propto: '∝',
  infty: '∞', sum: '∑', prod: '∏', int: '∫', oint: '∮',
  partial: '∂', nabla: '∇', forall: '∀', exists: '∃',
  in: '∈', notin: '∉', subset: '⊂', subseteq: '⊆', supset: '⊃', supseteq: '⊇',
  cup: '∪', cap: '∩', emptyset: '∅', therefore: '∴', because: '∵',
  rightarrow: '→', to: '→', Rightarrow: '⇒', leftarrow: '←', Leftarrow: '⇐',
  leftrightarrow: '↔', Leftrightarrow: '⇔', mapsto: '↦',
  ldots: '…', cdots: '…', dots: '…', vdots: '⋮', ddots: '⋱',
  angle: '∠', perp: '⊥', parallel: '∥', triangle: '△', circ: '∘',
  degree: '°', log: 'log', ln: 'ln', lg: 'lg', lim: 'lim', gcd: 'gcd', mod: 'mod',
  alpha: 'α', beta: 'β', gamma: 'γ', delta: 'δ', epsilon: 'ε', varepsilon: 'ε',
  zeta: 'ζ', eta: 'η', theta: 'θ', iota: 'ι', kappa: 'κ', lambda: 'λ', mu: 'μ',
  nu: 'ν', xi: 'ξ', pi: 'π', rho: 'ρ', sigma: 'σ', tau: 'τ', upsilon: 'υ',
  phi: 'φ', varphi: 'φ', chi: 'χ', psi: 'ψ', omega: 'ω',
  Gamma: 'Γ', Delta: 'Δ', Theta: 'Θ', Lambda: 'Λ', Xi: 'Ξ', Pi: 'Π',
  Sigma: 'Σ', Upsilon: 'Υ', Phi: 'Φ', Psi: 'Ψ', Omega: 'Ω',
};

function demathExpr(expr) {
  let s = expr;
  s = s.replace(/\\[,;!]/g, ' ');
  s = s.replace(/\\(?:d|t)?frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}/g, '($1)/($2)');
  s = s.replace(/\\sqrt\s*\[([^\]]*)\]\s*\{([^{}]*)\}/g, '$1√($2)');
  s = s.replace(/\\sqrt\s*\{([^{}]*)\}/g, '√($1)');
  s = s.replace(/\^\s*\{([^{}]*)\}/g, '^($1)');
  s = s.replace(/_\s*\{([^{}]*)\}/g, '_($1)');
  s = s.replace(/\\(?:text|mathrm|mathbf|mathit|operatorname)\s*\{([^{}]*)\}/g, '$1');
  s = s.replace(/\\(?:left|right)\s*/g, '');
  s = s.replace(/\\([A-Za-z]+)/g, (m, name) => (name in MATH_WORDS ? MATH_WORDS[name] : m));
  return s;
}

function demathText(seg) {
  let out = seg
    .replace(/\$\$([\s\S]+?)\$\$/g, (m, e) => demathExpr(e))
    .replace(/\\\[([\s\S]+?)\\\]/g, (m, e) => demathExpr(e))
    .replace(/\$([^$\n]+?)\$/g, (m, e) => demathExpr(e))
    .replace(/\\\(([\s\S]+?)\\\)/g, (m, e) => demathExpr(e));
  // 兜底：散落在正文里的 \le、\times 之类也翻掉，认不出来的保留原样
  return out.replace(/\\([A-Za-z]+)/g, (m, name) => (name in MATH_WORDS ? MATH_WORDS[name] : m));
}

function demath(text) {
  // 按围栏代码块 / 行内代码切分，只处理纯文本片段
  return text
    .split(/(```[\s\S]*?```|~~~[\s\S]*?~~~|`[^`\n]*`)/g)
    .map((part) => {
      const isCode = part.startsWith('```') || part.startsWith('~~~') ||
        (part.startsWith('`') && part.endsWith('`') && part.length > 1);
      return isCode ? part : demathText(part);
    })
    .join('');
}

/* ---------------- Markdown / 代码高亮 ---------------- */

/* 模型正文可能被知识库/网页内容带偏，DOMPurify 默认白名单放行 <style> 和
   style 属性，能被拿来塞钓鱼覆盖层，这里一律禁掉。 */
const SANITIZE_CONFIG = { FORBID_TAGS: ['style'], FORBID_ATTR: ['style'] };

function renderMarkdown(text) {
  return DOMPurify.sanitize(marked.parse(demath(text)), SANITIZE_CONFIG);
}

/* 复制文本。手机连局域网 IP 打开时是 http 页面，不是安全上下文，
   navigator.clipboard 直接不存在，只调它就会「复制失败」。
   所以先用 execCommand 同步复制（还能保住用户点击的手势），不行再退回异步 API。 */
async function copyText(text) {
  const area = document.createElement('textarea');
  area.value = text;
  area.readOnly = true;
  area.contentEditable = 'true';        // iOS Safari 只有这样才能 select()
  area.style.position = 'fixed';
  area.style.top = '-1000px';
  area.style.opacity = '0';
  document.body.appendChild(area);
  area.select();
  area.setSelectionRange(0, text.length);
  let ok = false;
  try {
    ok = document.execCommand('copy');
  } catch (err) {
    ok = false;
  }
  document.body.removeChild(area);
  if (ok) return true;

  if (navigator.clipboard) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch (err) {
      return false;
    }
  }
  return false;
}

/* 复制失败就把代码选中，让雏草姬长按 / 双击手动复制，别干瞪眼 */
function selectNode(node) {
  const range = document.createRange();
  range.selectNodeContents(node);
  const selection = window.getSelection();
  selection.removeAllRanges();
  selection.addRange(range);
}

function enhance(root) {
  root.querySelectorAll('a[href]').forEach((a) => {
    a.target = '_blank';
    a.rel = 'noopener noreferrer';
  });

  root.querySelectorAll('pre > code').forEach((code) => {
    const pre = code.parentElement;
    const langMatch = /language-([\w+#-]+)/.exec(code.className);
    hljs.highlightElement(code);
    if (pre.querySelector('.code-bar')) return;

    const bar = el('div', 'code-bar');
    const lang = el('span', 'code-lang');
    lang.textContent = langMatch ? langMatch[1] : 'text';

    const copy = el('button', 'code-copy');
    copy.type = 'button';
    copy.textContent = '复制';
    copy.addEventListener('click', async () => {
      const ok = await copyText(code.textContent);
      copy.textContent = ok ? '已复制' : '已选中，长按复制';
      if (!ok) selectNode(code);
      setTimeout(() => { copy.textContent = '复制'; }, 1800);
    });

    bar.append(lang, copy);
    pre.appendChild(bar);
  });
}

/* 流式下每个增量都重渲染整块，用 rAF 压一下频率，别把帧率拖垮。
   长代码块下每帧对整个 raw 重跑 marked+hljs 是 O(n²)，再加一道时间闸：
   两次渲染之间至少隔 RENDER_MIN_MS，期间的增量合并进这一帧（读的是最新 raw），
   收尾时 send() 会再排一次，最后的内容不会漏画。 */
const RENDER_MIN_MS = 80;

function scheduleRender(block) {
  if (block.pending) return;
  block.pending = true;
  setTimeout(() => requestAnimationFrame(() => {
    block.pending = false;
    block.el.innerHTML = renderMarkdown(block.raw);
    enhance(block.el);
    updateToBottom();
  }), RENDER_MIN_MS);
}

/* ---------------- 滚动 ---------------- */

/* 不再自动吸底跟随。内容流式增长时视图一动不动，雏草姬想翻到哪就翻到哪，
   不会被新来的内容顶回去。只有发消息这一下才把最新一条带进视野。 */
function scrollToBottom() {
  messagesEl.scrollTop = messagesEl.scrollHeight;
  updateToBottom();
}

/* 只要没贴着底，右下角就冒一个「回到底部」的按钮；翻到底了它就自己藏起来。 */
const NEAR_BOTTOM = 80;

function updateToBottom() {
  const gap = messagesEl.scrollHeight - messagesEl.scrollTop - messagesEl.clientHeight;
  toBottomEl.classList.toggle('show', gap > NEAR_BOTTOM);
}

messagesEl.addEventListener('scroll', updateToBottom, { passive: true });
window.addEventListener('resize', updateToBottom);

toBottomEl.addEventListener('click', () => {
  messagesEl.scrollTo({ top: messagesEl.scrollHeight, behavior: 'smooth' });
});

/* ---------------- 消息渲染 ---------------- */

function addUser(text, image) {
  const row = el('div', 'msg user');
  const bubble = el('div', 'bubble');
  if (image) {
    const img = el('img', 'sent-image');
    img.src = image;
    img.alt = '发送的图片';
    bubble.appendChild(img);
  }
  if (text) {
    const box = el('div', 'text');
    box.textContent = text;
    bubble.appendChild(box);
  }
  row.appendChild(bubble);
  messagesEl.appendChild(row);
  scrollToBottom();
}

function addAgent() {
  const row = el('div', 'msg agent');
  const avatar = el('img', 'avatar');
  avatar.src = AVATAR;
  avatar.alt = '塔菲';
  const bubble = el('div', 'bubble');
  row.append(avatar, bubble);
  messagesEl.appendChild(row);
  scrollToBottom();
  return { row, bubble, thinking: null, content: null, tools: [] };
}

function thinkingChunk(turn, text) {
  if (!turn.thinking) {
    const box = el('details', 'thinking');
    box.open = false;
    const summary = el('summary');
    const spin = el('span', 'spin');
    const label = el('span', 'tsum');
    label.textContent = '思考中…';
    summary.append(spin, label);
    const body = el('div', 'thinking-body');
    box.append(summary, body);
    turn.bubble.appendChild(box);
    turn.thinking = { box, body };
  }
  turn.thinking.body.textContent += text;
  updateToBottom();
}

/* 正文/工具/表情包一来，就说明这段思考结束了，把标题标一下。
   盒子开不开交给雏草姬自己决定，这里不强行收起。 */
function settleThinking(turn) {
  if (!turn.thinking) return;
  turn.thinking.box.querySelector('.tsum').textContent = '已深度思考';
  turn.thinking = null;
}

function contentChunk(turn, text) {
  settleThinking(turn);
  if (!turn.content) {
    const box = el('div', 'content');
    turn.bubble.appendChild(box);
    turn.content = { el: box, raw: '', pending: false };
  }
  turn.content.raw += text;
  scheduleRender(turn.content);
}

function toolStart(turn, name, args) {
  settleThinking(turn);
  turn.content = null; // 工具跑完后的正文另起一块，保持先后顺序

  const card = el('details', 'tool');
  card.open = false;
  const summary = el('summary');
  const label = el('span', 'tname');
  label.textContent = name;
  const state = el('span', 'tstate');
  state.textContent = '运行中…';
  summary.append(label, state);

  const body = el('div', 'tool-body');
  const argsLabel = el('div', 'tool-label');
  argsLabel.textContent = '参数';
  const argsPre = el('pre', 'tool-args');
  argsPre.textContent = prettyArgs(args);
  const resultLabel = el('div', 'tool-label');
  resultLabel.textContent = '结果';
  const resultPre = el('pre', 'tool-result');
  resultPre.textContent = '等待中…';
  body.append(argsLabel, argsPre, resultLabel, resultPre);

  card.append(summary, body);
  turn.bubble.appendChild(card);
  // 按出现顺序存：一轮里同名工具可能调两次，用 name 当 key 后面那张会把前面顶掉
  turn.tools.push({ name, card, done: false });
  updateToBottom();
}

function toolEnd(turn, name, result) {
  // 找同名里还没收尾的那张（先出现的先收尾），别让后一张顶掉前一张
  const entry = turn.tools.find((t) => t.name === name && !t.done);
  if (!entry) return;
  entry.done = true;
  const card = entry.card;
  card.querySelector('.tool-result').textContent = result;
  const state = card.querySelector('.tstate');
  state.textContent = '完成';
  state.classList.add('ok');
  updateToBottom();
}

function addSticker(turn, mood) {
  settleThinking(turn);
  turn.content = null;
  const safe = String(mood).replace(/[^a-z]/gi, '');
  const pool = stickerPool(safe);
  if (!pool.length) return;
  const name = pool[Math.floor(Math.random() * pool.length)];
  const img = el('img', 'sticker');
  img.src = `/static/stickers/${encodeURIComponent(name)}`;
  img.alt = mood;
  img.loading = 'lazy';
  img.addEventListener('error', () => img.remove());
  turn.bubble.appendChild(img);
  updateToBottom();
}

function errorBubble(turn, message) {
  settleThinking(turn);
  turn.content = null;
  const box = el('div', 'error');
  box.textContent = '喵呜…出错了：' + message;
  turn.bubble.appendChild(box);
}

/* 系统提示条（不是塔菲说的话），比如「历史太长，旧的那段压成摘要了」 */
function noticeBubble(turn, text) {
  turn.content = null;
  const box = el('div', 'notice');
  box.textContent = text;
  turn.bubble.appendChild(box);
  updateToBottom();
}

function prettyArgs(raw) {
  try {
    return JSON.stringify(JSON.parse(raw), null, 2);
  } catch (err) {
    return raw || '{}';
  }
}

/* ---------------- 会话 ---------------- */

async function ensureSession(force) {
  if (!force && sessionId) return sessionId;
  const resp = await fetch('/api/session', { method: 'POST' });
  if (!resp.ok) throw new Error('开新会话失败');
  const data = await resp.json();
  sessionId = data.session_id;
  sessionStorage.setItem(STORAGE_KEY, sessionId);
  return sessionId;
}

/* ---------------- 上下文占用 ----------------
   后端按「中文 1 字 1 token、英文 4 字符 1 token」粗估（见 taffy/context.py），
   所以这个百分比是估的，不是 API 报的真实用量；够用来提醒「快满了」。
   用到窗口的八成时后端会自动把旧历史压成摘要，压完这个数会掉下来。 */

const CTX_WARN = 80;   // 到这条线就变橙：接着聊就要触发自动压缩了

function paintContext(info) {
  if (!info || !info.limit) {
    ctxBarEl.hidden = true;
    return;
  }
  const percent = Math.max(0, Math.min(100, info.percent || 0));
  ctxBarEl.hidden = false;
  ctxFillEl.style.width = `${percent}%`;
  ctxFillEl.classList.toggle('warn', percent >= CTX_WARN);
  ctxTextEl.textContent = `${percent}% · ${Math.round(info.tokens / 1000)}k/` +
    `${Math.round(info.limit / 1000)}k`;
  ctxBarEl.title = `这段对话大概占了 ${info.tokens} token，模型窗口 ${info.limit} token` +
    `（用到 ${Math.round((info.compress_at || 0.8) * 100)}% 时自动压缩）`;
}

async function refreshContext() {
  const asked = sessionId || '';   // 记下这次问的是哪个会话，回填前核对还在不在
  try {
    const resp = await fetch(
      `/api/context?session_id=${encodeURIComponent(asked)}`,
      { cache: 'no-store' },
    );
    if (!resp.ok) return;
    const info = await resp.json();
    // 切了会话的话，旧会话的用量不能画到新会话上，否则百分比误导压缩判断
    if (asked === (sessionId || '')) paintContext(info);
  } catch (err) {
    /* 拿不到就先不显示，下一轮再说 */
  }
}

/* ---------------- SSE ---------------- */

/* 手机切后台、信号抖动的时候，这条流可能悄无声息地断掉：不报错也不结束，
   reader.read() 就那么挂着，界面永远停在「正在思考喵…」、发送按钮也一直锁着，
   看起来就跟没网了一样（刷新才恢复）。所以每收到一块数据就重置一次计时器，
   静默太久就自己掐掉，把状态放出来。 */
const STREAM_IDLE_MS = 60000;

async function streamChat(id, message, onEvent, image) {
  const body = { session_id: id, message, thinking: thinkingOn() };
  if (image) body.image = image;

  const controller = new AbortController();
  let idleTimer = null;
  let stalled = false;
  const keepAlive = () => {
    clearTimeout(idleTimer);
    idleTimer = setTimeout(() => {
      stalled = true;
      controller.abort();
    }, STREAM_IDLE_MS);
  };

  // 看门狗在 fetch 之前就挂上：响应头回来之前（后端还卡在 LLM 调用）也要能超时，
  // 否则 fetch 一直挂着，busy 永远为 true、发送键锁死，只能刷新页面。
  keepAlive();
  try {
    const resp = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal: controller.signal,
    });
    if (!resp.ok || !resp.body) throw new Error(`服务返回 ${resp.status}`);

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let done_events = false; // 收到 [DONE] 后就只把剩余字节读完，不再解析

    keepAlive();
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      keepAlive();
      buffer += decoder.decode(value, { stream: true });

      let cut;
      while ((cut = buffer.indexOf('\n\n')) >= 0) {
        const chunk = buffer.slice(0, cut);
        buffer = buffer.slice(cut + 2);
        if (done_events) continue;
        const line = chunk.split('\n').find((l) => l.startsWith('data:'));
        if (!line) continue;
        const payload = line.slice(5).trim();
        if (payload === '[DONE]') {
          done_events = true;
          continue;
        }
        let event;
        try {
          event = JSON.parse(payload);
        } catch (err) {
          continue;   // 半截 JSON，忽略
        }
        onEvent(event);   // 渲染异常让它冒出去，别再当成半截 JSON 一起吞掉
      }
    }
  } catch (err) {
    if (stalled) throw new Error('这条回复断在半路了喵（网络波动），再发一次就好');
    throw err;
  } finally {
    clearTimeout(idleTimer);
  }
}

function handleEvent(turn, event) {
  switch (event.type) {
    case 'thinking':
      thinkingChunk(turn, event.text);
      break;
    case 'content':
      contentChunk(turn, event.text);
      break;
    case 'tool_start':
      // send_sticker 不用画工具卡片，紧接着的 sticker 事件就是它的样子
      if (event.name !== 'send_sticker') toolStart(turn, event.name, event.arguments);
      break;
    case 'tool_end':
      toolEnd(turn, event.name, event.result);
      break;
    case 'sticker':
      addSticker(turn, event.mood);
      break;
    case 'cut':
      noticeBubble(turn,
        `塔菲话还没说完就断了（${event.reason || '网络中断'}）。发「继续」让塔菲接着讲喵～`);
      break;
    case 'compressed':
      noticeBubble(turn,
        `这段聊得有点长啦，塔菲把前面 ${event.dropped} 条旧记录压成摘要了喵` +
        `（上下文从 ${Math.round(event.before_tokens / 1000)}k 降到 ` +
        `${Math.round(event.after_tokens / 1000)}k），接着聊就好～`);
      break;
    case 'done':
      if (event.context) paintContext(event.context);
      break;
    case 'error':
      errorBubble(turn, event.message);
      break;
    default:
      break;
  }
}

/* ---------------- 回到前台重连 ----------------
   手机上切后台再切回来，浏览器/系统常年会把之前的连接悄悄掐掉，页面就卡在死状态。
   回到前台先探一下服务还在不在：连着就把状态改回「在线喵」，连不上就再试几次，
   实在不行才提示刷新。正在跑的那条流有它自己的静默计时器兜底，这里不去打扰。 */

const RECONNECT_TRIES = 3;
const RECONNECT_WAIT_MS = 800;

async function reachable() {
  const resp = await fetch('/api/ping', { cache: 'no-store' });
  return resp.ok;
}

async function resume() {
  if (document.visibilityState !== 'visible') return;
  for (let i = 0; i < RECONNECT_TRIES; i += 1) {
    try {
      if (await reachable()) {
        if (!busy) statusEl.textContent = '在线喵';
        return;
      }
    } catch (err) {
      /* 没连上，接着试 */
    }
    statusEl.textContent = '重连中喵…';
    await new Promise((resolve) => setTimeout(resolve, RECONNECT_WAIT_MS));
  }
  statusEl.textContent = '连不上服务喵，刷新一下吧';
}

document.addEventListener('visibilitychange', resume);
window.addEventListener('online', resume);
// pageshow 每次加载都会触发，只有从 bfcache 里捞回来的那次才需要探活
window.addEventListener('pageshow', (e) => { if (e.persisted) resume(); });

/* ---------------- 发送 ---------------- */

async function send(text, image) {
  if (busy) return;
  busy = true;
  sentCount += 1;
  sendEl.disabled = true;
  pickImageEl.disabled = true;
  inputEl.value = '';
  autosize();
  statusEl.textContent = '正在思考喵…';
  addUser(text, image);
  clearImage();

  const turn = addAgent();
  try {
    const id = await ensureSession();
    await streamChat(id, text, (event) => handleEvent(turn, event), image);
  } catch (err) {
    errorBubble(turn, err && err.message ? err.message : String(err));
  } finally {
    settleThinking(turn);
    if (turn.content) scheduleRender(turn.content);
    busy = false;
    sendEl.disabled = false;
    pickImageEl.disabled = false;
    statusEl.textContent = '在线喵';
    refreshContext();     // 这一轮又添了几条，顺便把上下文占用刷新一下
    inputEl.focus();
  }
}

/* ---------------- 输入框 ---------------- */

function autosize() {
  inputEl.style.height = 'auto';
  // scrollHeight 不含边框，而全局是 border-box，所以要把上下边框加回去，
  // 否则撑高之后输入框会比两边按钮矮 2px，又错位了。
  const cs = getComputedStyle(inputEl);
  const border = parseFloat(cs.borderTopWidth) + parseFloat(cs.borderBottomWidth);
  inputEl.style.height = Math.min(inputEl.scrollHeight + border, 140) + 'px';
}

inputEl.addEventListener('input', autosize);

/* ---------------- 发图片 ----------------
   图片只在浏览器里读成 data URL，随请求发上去；服务端不落盘，塔菲看完整轮
   就把图片数据从对话历史里抹掉，所以不会在服务器上留下任何图片文件。 */

const IMAGE_MAX_BYTES = 4 * 1024 * 1024; // 原图上限，超了直接拦掉，别让 base64 撑爆请求

function clearImage() {
  pendingImage = null;
  imageInputEl.value = '';
  attachPreviewEl.removeAttribute('src');
  attachBarEl.hidden = true;
}

pickImageEl.addEventListener('click', () => imageInputEl.click());

imageInputEl.addEventListener('change', () => {
  const file = imageInputEl.files && imageInputEl.files[0];
  if (!file) return;
  if (!file.type.startsWith('image/')) {
    statusEl.textContent = '只能发图片喵';
    clearImage();
    return;
  }
  if (file.size > IMAGE_MAX_BYTES) {
    statusEl.textContent = '图片太大了喵，限 4MB';
    clearImage();
    return;
  }
  const reader = new FileReader();
  reader.onload = () => {
    pendingImage = reader.result;
    attachPreviewEl.src = pendingImage;
    attachBarEl.hidden = false;
    statusEl.textContent = '在线喵';
    inputEl.focus();
  };
  reader.onerror = () => {
    statusEl.textContent = '读图片失败喵';
    clearImage();
  };
  reader.readAsDataURL(file);
});

attachRemoveEl.addEventListener('click', () => {
  clearImage();
  inputEl.focus();
});

/* ---------------- 保存上下文 ----------------
   存成服务器上的一个 txt（见 taffy/web/sessions_store.py），历史不留在内存里。
   uuid 由雏草姬自己起，重名后端会拒掉（409），不会覆盖旧存档。 */

function fmtSize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function suggestUuid() {
  const d = new Date();
  const pad = (n) => String(n).padStart(2, '0');
  const stamp = `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}`;
  return `chat-${stamp}-${Math.random().toString(36).slice(2, 6)}`;
}

function saveMsg(text, ok) {
  saveMsgEl.textContent = text;
  saveMsgEl.classList.toggle('ok', ok === true);
  saveMsgEl.classList.toggle('bad', ok === false);
}

/* ---------------- 顶栏「⋯」更多菜单（窄屏才出现） ----------------
   手机顶栏放不下三个按钮，就把「导入 / 保存」收进这里。宽屏上 CSS 直接让
   .tb-actions 一直显示，这个菜单开关对它没影响。 */

function closeMore() {
  tbActionsEl.classList.remove('open');
  tbMoreEl.setAttribute('aria-expanded', 'false');
}

tbMoreEl.addEventListener('click', (event) => {
  event.stopPropagation();          // 别让下面那个「点别处收起」立刻又把它关掉
  const open = tbActionsEl.classList.toggle('open');
  tbMoreEl.setAttribute('aria-expanded', String(open));
});

tbActionsEl.addEventListener('click', closeMore);   // 选完就收起

document.addEventListener('click', (event) => {
  if (!tbActionsEl.classList.contains('open')) return;
  if (tbActionsEl.contains(event.target) || tbMoreEl.contains(event.target)) return;
  closeMore();
});

document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') closeMore();
});

function openSaveModal() {
  if (busy) {
    statusEl.textContent = '等塔菲说完这句再存喵';
    return;
  }
  if (!sentCount) {
    statusEl.textContent = '还没聊过喵，先说句话再存';
    return;
  }
  saveUuidEl.value = suggestUuid();
  saveMsg('');
  saveModalEl.hidden = false;
  saveUuidEl.focus();
  saveUuidEl.select();
}

function closeSaveModal() {
  saveModalEl.hidden = true;
  inputEl.focus();
}

async function doSave() {
  const uuid = saveUuidEl.value.trim();
  if (!uuid) {
    saveMsg('得给个 uuid 喵', false);
    return;
  }
  // 跟后端同一套规矩，先在前端拦一道，省一次往返
  if (uuid.indexOf('..') >= 0 || !/^[A-Za-z0-9][A-Za-z0-9._-]{1,63}$/.test(uuid)) {
    saveMsg('只能用字母、数字和 . _ -（3~64 位，开头得是字母或数字）喵', false);
    return;
  }
  saveOkEl.disabled = true;
  saveMsg('正在存喵…');
  try {
    const id = await ensureSession();
    const resp = await fetch('/api/session/save', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: id, uuid }),
    });
    let data = null;
    try {
      data = await resp.json();
    } catch (err) {
      data = null;
    }
    if (!resp.ok) {
      saveMsg((data && data.detail) || `存不上，服务返回 ${resp.status}`, false);
      return;
    }
    saveMsg(`存好啦喵：${data.file}（${data.messages} 条记录，${fmtSize(data.bytes)}）`, true);
    setTimeout(closeSaveModal, 1400);
  } catch (err) {
    saveMsg(`存不上喵：${err && err.message ? err.message : err}`, false);
  } finally {
    saveOkEl.disabled = false;
  }
}

saveSessionEl.addEventListener('click', openSaveModal);
saveCancelEl.addEventListener('click', closeSaveModal);
saveOkEl.addEventListener('click', doSave);
saveModalEl.addEventListener('click', (event) => {
  if (event.target === saveModalEl) closeSaveModal();   // 点遮罩关掉
});
saveUuidEl.addEventListener('keydown', (event) => {
  if (event.key === 'Enter') {
    event.preventDefault();
    doSave();
  } else if (event.key === 'Escape') {
    closeSaveModal();
  }
});

/* ---------------- 导入上下文 ----------------
   把存过的存档接回这个会话（见 taffy/web/app.py 的 /api/session/import）。
   接回来的是完整历史，所以「上下文 xx%」跟着一起回来——历史长、百分比却是 0
   的话，自动压缩就一直不触发，下一轮直接撞超长，这正是当初要带回来的原因。 */

const HISTORY_RENDER_MAX = 200;   // 太长的存档只画最近这么多条，别把页面卡住

function loadMsg(text, ok) {
  loadMsgEl.textContent = text;
  loadMsgEl.classList.toggle('ok', ok === true);
  loadMsgEl.classList.toggle('bad', ok === false);
}

function openLoadModal() {
  if (busy) {
    statusEl.textContent = '等塔菲说完这句再导喵';
    return;
  }
  loadUuidEl.value = '';
  loadMsg('');
  loadModalEl.hidden = false;
  loadUuidEl.focus();
}

function closeLoadModal() {
  loadModalEl.hidden = true;
  inputEl.focus();
}

/* 把历史摆回聊天区：工具调用那些不画（刷屏），只摆用户说的和塔菲的正文 */
function renderHistory(items) {
  items.forEach((item) => {
    if (item.role === 'user') {
      addUser(item.text, null);
      return;
    }
    const turn = addAgent();
    contentChunk(turn, item.text);
    if (turn.content) scheduleRender(turn.content);
  });
}

async function doImport() {
  const uuid = loadUuidEl.value.trim();
  if (!uuid) {
    loadMsg('得填个 uuid 喵', false);
    return;
  }
  // 跟后端同一套规矩，先在前端拦一道
  if (uuid.indexOf('..') >= 0 || !/^[A-Za-z0-9][A-Za-z0-9._-]{1,63}$/.test(uuid)) {
    loadMsg('只能用字母、数字和 . _ -（3~64 位，开头得是字母或数字）喵', false);
    return;
  }
  loadOkEl.disabled = true;
  loadMsg('正在接回来喵…');
  try {
    const id = await ensureSession();
    const resp = await fetch('/api/session/import', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: id, uuid }),
    });
    let data = null;
    try {
      data = await resp.json();
    } catch (err) {
      data = null;
    }
    if (!resp.ok) {
      loadMsg((data && data.detail) || `导不进来，服务返回 ${resp.status}`, false);
      return;
    }
    const items = data.history || [];
    const shown = items.slice(-HISTORY_RENDER_MAX);
    messagesEl.innerHTML = '';
    renderHistory(shown);
    const turn = addAgent();
    noticeBubble(turn,
      `接上存档「${data.uuid}」了喵：一共 ${data.messages} 条记录` +
      (shown.length < items.length ? `（这里画最近 ${shown.length} 条）` : '') +
      `，上下文跟着回到 ${Math.round(((data.context && data.context.percent) || 0) * 10) / 10}%，` +
      '接着聊就好～');
    if (data.context) paintContext(data.context);
    sentCount += 1;          // 有东西可以接着存了
    scrollToBottom();
    loadMsg('接回来啦喵', true);
    setTimeout(closeLoadModal, 1000);
  } catch (err) {
    loadMsg(`导不进来喵：${err && err.message ? err.message : err}`, false);
  } finally {
    loadOkEl.disabled = false;
  }
}

loadSessionEl.addEventListener('click', openLoadModal);
loadCancelEl.addEventListener('click', closeLoadModal);
loadOkEl.addEventListener('click', doImport);
loadModalEl.addEventListener('click', (event) => {
  if (event.target === loadModalEl) closeLoadModal();   // 点遮罩关掉
});
loadUuidEl.addEventListener('keydown', (event) => {
  if (event.key === 'Enter') {
    event.preventDefault();
    doImport();
  } else if (event.key === 'Escape') {
    closeLoadModal();
  }
});

/* 回车不发送，老老实实在输入框里换行；要发就点「发送」按钮。
   手机端也一样，软键盘的回车键只负责换行，不会手滑把半句话发出去。 */
formEl.addEventListener('submit', (e) => {
  e.preventDefault();
  const text = inputEl.value.trim();
  if ((!text && !pendingImage) || busy) return;
  send(text, pendingImage);
});

newChatEl.addEventListener('click', async () => {
  if (busy) return;
  clearImage();
  messagesEl.innerHTML = '';
  sessionId = null;
  sessionStorage.removeItem(STORAGE_KEY);
  paintContext(null);      // 新会话，占用归零
  updateToBottom();
  try {
    await ensureSession(true);
    welcome();
    refreshContext();
  } catch (err) {
    statusEl.textContent = '开新会话失败';
  }
  inputEl.focus();
});

/* ---------------- 深度思考开关 ---------------- */

/* 默认开。关掉之后后端会带 enable_thinking=false 发请求：模型不再先想一大段，
   回答快好几倍，但复杂推理、算法推导这类会变浅。 */
function thinkingOn() {
  return localStorage.getItem(THINK_KEY) !== '0';
}

function paintThinking() {
  const on = thinkingOn();
  thinkToggleEl.classList.toggle('on', on);
  thinkToggleEl.classList.toggle('off', !on);
  thinkToggleEl.setAttribute('aria-checked', on ? 'true' : 'false');
  thinkToggleEl.title = on
    ? '深度思考：开（点一下关掉，回答更快）'
    : '深度思考：关（点一下打开，难题想得更细）';
}

thinkToggleEl.addEventListener('click', () => {
  localStorage.setItem(THINK_KEY, thinkingOn() ? '0' : '1');
  paintThinking();
});

paintThinking();

/* ---------------- 开场 ---------------- */

function welcome() {
  const turn = addAgent();
  contentChunk(
    turn,
    '雏草姬来啦喵～我是永雏塔菲，有什么想聊的直接说就好喵。\n\n' +
    '算法题、代码、知识库里的资料都可以问我；嵌入式、单片机（STM32 那种）、物联网、' +
    '计算机组成原理、数字取证这些塔菲也懂喵。左下角可以发图片给塔菲看（看完就删，不会留着）；' +
    '想把这段聊天存下来就点右上角「保存上下文」（手机上收在右上角那个「⋯」里）；' +
    '下次点「导入上下文」填那个名字，塔菲就能把这段对话连上下文用量一起接回来；' +
    '想换个话题就点「新会话」，塔菲会把之前的事忘干净从头开始喵～\n\n' +
    '输入框上面那条是这段对话占了多少「上下文」，快满的时候塔菲会自己把前面聊过的' +
    '压成摘要，不会聊着聊着就卡住喵。'
  );
}

(async function init() {
  loadStickerLib();   // 不等它，表情包清单到了就用新的，没到就用兜底那份
  try {
    await ensureSession();
    welcome();
    refreshContext();  // 刷新页面后把上次聊到哪、上下文用了几成接回来
  } catch (err) {
    statusEl.textContent = '连不上服务喵';
  }
  inputEl.focus();
})();
