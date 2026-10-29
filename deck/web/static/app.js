'use strict';

/* 网页前端：把后端的 SSE 事件流画成聊天记录。
   会话 id 存在 sessionStorage，点「新会话」换一个 id，历史就重开一份。
   用 sessionStorage 而不是 localStorage：它按标签页隔离，开两个标签页就是
   两个独立会话；同一标签页刷新仍保留，关掉标签页才丢。 */

const STORAGE_KEY = 'taffy_session_id';
/* 「深度思考」开关存 localStorage（跟会话 id 不一样：这个希望同一个浏览器
   里所有标签页共用、刷新也在）。默认是开。 */
const THINK_KEY = 'taffy_thinking';
let AVATAR = '/static/avatar.jpg';
/* 当前人设（名字、头像、欢迎语、输入框提示、状态文案都从后端拿，换人设不用改前端） */
let personaInfo = null;

const onlineText = () => (personaInfo && personaInfo.status_online) || '在线';
const thinkingText = () => (personaInfo && personaInfo.status_thinking) || '正在思考…';

/* 换网页图标：删掉旧的 link 再新建一个（带时间戳绕开缓存），最稳 */
function setFavicon(url) {
  if (!url) return;
  const full = url + (url.includes('?') ? '&' : '?') + 'v=' + Date.now();
  document.querySelectorAll('link[rel="icon"], link[rel="shortcut icon"], link[rel^="icon"]')
    .forEach((node) => node.remove());
  const link = document.createElement('link');
  link.rel = 'icon';
  link.type = full.includes('.png') || full.includes('/api/persona/icon') ? 'image/png' : '';
  link.href = full;
  document.head.appendChild(link);
}

/* 主题色：把一个 #rrggbb 往白 / 黑方向混，得到浅色档和深色档 */
function mixColor(hex, target, ratio) {
  const value = String(hex || '').replace('#', '');
  if (value.length !== 6) return hex;
  const num = parseInt(value, 16);
  const base = {
    r: (num >> 16) & 255, g: (num >> 8) & 255, b: num & 255,
  };
  const to = target === 'white' ? { r: 255, g: 255, b: 255 } : { r: 0, g: 0, b: 0 };
  const blend = (a, b) => Math.round(a + (b - a) * ratio);
  const out = [blend(base.r, to.r), blend(base.g, to.g), blend(base.b, to.b)];
  return '#' + out.map((n) => n.toString(16).padStart(2, '0')).join('');
}

function applyTheme(theme) {
  const color = /^#[0-9a-fA-F]{6}$/.test(theme || '') ? theme : '#ffb7d5';
  const root = document.documentElement.style;
  root.setProperty('--pink', mixColor(color, 'white', 0.25));
  root.setProperty('--pink-dark', color);
  root.setProperty('--pink-deep', mixColor(color, 'black', 0.3));
  root.setProperty('--pink-pale', mixColor(color, 'white', 0.45));
  root.setProperty('--pink-mist', mixColor(color, 'white', 0.78));
  root.setProperty('--bg', mixColor(color, 'white', 0.93));
  root.setProperty('--line', mixColor(color, 'white', 0.7));
  root.setProperty('--accent', color);
}

function applyPersona(info) {
  if (!info) return;
  personaInfo = info;
  const name = info.name || '助手';
  document.title = `${name} · Agent`;
  applyTheme(info.theme);
  const themeMeta = document.querySelector('meta[name="theme-color"]');
  if (themeMeta && /^#[0-9a-fA-F]{6}$/.test(info.theme || '')) {
    themeMeta.content = info.theme;
  }
  // 网页图标跟着身份卡换（favicon 浏览器缓存很凶，重建 link + 加时间戳才换得动）
  if (info.icon) setFavicon(info.icon);
  if (info.avatar) AVATAR = info.avatar;
  document.querySelectorAll('img.avatar, img.side-avatar, img.user-avatar').forEach((img) => {
    img.src = AVATAR;
    img.alt = name;
  });
  const topName = document.getElementById('top-name');
  if (topName) topName.textContent = name;
  if (info.placeholder) inputEl.placeholder = info.placeholder;
}

async function loadPersona() {
  try {
    const resp = await fetch('/api/persona', { cache: 'no-store' });
    if (!resp.ok) return;
    applyPersona(await resp.json());
  } catch (err) {
    /* 拿不到就用内置文案，不影响聊天 */
  }
}

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
const emptyHeroEl = document.getElementById('empty-hero');
const emptyTitleEl = document.getElementById('empty-title');
const formEl = document.getElementById('composer');
const inputEl = document.getElementById('input');
const sendEl = document.getElementById('send');
const statusEl = document.getElementById('status');
const newChatEl = document.getElementById('new-chat');
const toBottomEl = document.getElementById('to-bottom');
const imageInputEl = document.getElementById('image-input');
const fileInputEl = document.getElementById('file-input');
const pickPlusEl = document.getElementById('pick-plus');
const attachPopEl = document.getElementById('attach-pop');
let pendingFiles = [];            // 已上传、还没发出去的文件 [{id,name,size}]
const FILE_MAX_COUNT = 6;
const FILE_MAX_BYTES = 20 * 1024 * 1024;
const attachBarEl = document.getElementById('attach-bar');
const ctxBarEl = document.getElementById('ctx-bar');
const ctxFillEl = document.getElementById('ctx-fill');
const ctxTextEl = document.getElementById('ctx-text');
const thinkToggleEl = document.getElementById('think-toggle');

let sessionId = null;      // 当前会话 id（存在后端 SQLite 里）
let me = null;             // 当前登录用户
let sessions = [];         // 侧边栏的会话列表
let busy = false;
let pendingImages = []; // 已经选好、还没发出去的多张图片（data URL 列表）
const IMAGE_MAX_COUNT = 6;  // 一轮最多几张，和后端 uploads.MAX_COUNT 对齐

marked.setOptions({ gfm: true, breaks: true });

/* 任何接口回 401 都说明登录过期了，直接踢回登录页，别让页面卡在死状态 */
/* 兜底：任何没被 catch 的 JS 错都摆到状态栏上（带错误信息），
   免得页面挂了却只显示一句「连不上服务」，查都没法查。 */
window.addEventListener('error', (event) => {
  try {
    const status = document.getElementById('status');
    if (status) status.textContent = '页面出错了：' + (event.message || '未知错误');
  } catch (err) { /* 忽略 */ }
});
window.addEventListener('unhandledrejection', (event) => {
  try {
    const status = document.getElementById('status');
    const reason = event.reason;
    if (status) {
      status.textContent = '页面出错了：' + ((reason && reason.message) || reason || '未知错误');
    }
  } catch (err) { /* 忽略 */ }
});

const _realFetch = window.fetch.bind(window);
window.fetch = async (input, init) => {
  const resp = await _realFetch(input, init);
  if (resp.status === 401 && !String(input).includes('/api/auth/login')
      && !String(input).includes('/api/auth/register')) {
    location.replace('/login');
    throw new Error('登录过期了');
  }
  return resp;
};

const sidebarEl = document.getElementById('sidebar');
const sideScrimEl = document.getElementById('side-scrim');
const sideToggleEl = document.getElementById('side-toggle');
const sideListEl = document.getElementById('side-list');
const sideNameEl = document.getElementById('side-name');
const sideInviteEl = document.getElementById('side-invite');
const sideInviteCodeEl = document.getElementById('side-invite-code');
const sideNewEl = document.getElementById('side-new');
const sideAdminEl = document.getElementById('side-admin');
const sidePassEl = document.getElementById('side-pass');
const sideLogoutEl = document.getElementById('side-logout');
const passModalEl = document.getElementById('user-modal');
const passOldEl = document.getElementById('pass-old');
const passNewEl = document.getElementById('pass-new');
const passConfirmEl = document.getElementById('pass-confirm');
const passMsgEl = document.getElementById('pass-msg');
const passOkEl = document.getElementById('pass-ok');
const userOpenEl = document.getElementById('user-open');
const userCloseEl = document.getElementById('user-close');
const userLogoutEl = document.getElementById('user-logout');
const userTitleEl = document.getElementById('user-title');
const userSubEl = document.getElementById('user-sub');
const userInviteEl = document.getElementById('user-invite');
const userCreatedEl = document.getElementById('user-created');
const userSessionsEl = document.getElementById('user-sessions');

const el = (tag, cls) => {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  return node;
};

/* ---------------- 数学式子美化 ---------------- */

/* 模型大部分时候用 Unicode 写数学（x²、√、×），本来就正常；只有在写复杂公式时
   才会漏出 LaTeX。旧版用正则一个个替换，碰到嵌套花括号（比如求根公式里的
   \frac{-b\pm\sqrt{b^2-4ac}}{2a}）就全盘失效，矩阵更是原样吐出来。
   这里改成真正扫描一遍：花括号配对、递归转换，转换不了的保持原样，绝不吞内容。
   代码块和行内代码一律原样保留。 */

const MATH_WORDS = {
  // 分隔 / 括号类（表格、条件概率里很常见）
  mid: '|', vert: '|', lvert: '|', rvert: '|', Vert: '‖', backslash: '\\',
  lbrace: '{', rbrace: '}', langle: '⟨', rangle: '⟩',
  // 运算符
  times: '×', cdot: '·', div: '÷', pm: '±', mp: '∓', ast: '∗', star: '⋆',
  oplus: '⊕', otimes: '⊗', bullet: '•',
  // 关系
  le: '≤', leq: '≤', ge: '≥', geq: '≥', ne: '≠', neq: '≠', approx: '≈',
  equiv: '≡', cong: '≅', sim: '∼', simeq: '≃', propto: '∝', ll: '≪', gg: '≫',
  // 大符号
  infty: '∞', sum: '∑', prod: '∏', coprod: '∐',
  int: '∫', iint: '∬', iiint: '∭', oint: '∮',
  partial: '∂', nabla: '∇', forall: '∀', exists: '∃', nexists: '∄',
  // 集合 / 逻辑
  in: '∈', notin: '∉', ni: '∋', subset: '⊂', subseteq: '⊆', supset: '⊃',
  supseteq: '⊇', cup: '∪', cap: '∩', setminus: '∖', emptyset: '∅',
  varnothing: '∅', therefore: '∴', because: '∵', neg: '¬', land: '∧', lor: '∨',
  // 箭头
  rightarrow: '→', to: '→', Rightarrow: '⇒', leftarrow: '←', Leftarrow: '⇐',
  leftrightarrow: '↔', Leftrightarrow: '⇔', Longrightarrow: '⟹', implies: '⟹',
  iff: '⟺', mapsto: '↦', uparrow: '↑', downarrow: '↓',
  // 省略号 / 点
  ldots: '…', cdots: '⋯', dots: '…', vdots: '⋮', ddots: '⋱', prime: '′',
  // 几何
  angle: '∠', perp: '⊥', parallel: '∥', triangle: '△', circ: '∘', degree: '°',
  // 函数名：去掉反斜杠，保持原来的字母
  sin: 'sin', cos: 'cos', tan: 'tan', cot: 'cot', sec: 'sec', csc: 'csc',
  arcsin: 'arcsin', arccos: 'arccos', arctan: 'arctan',
  sinh: 'sinh', cosh: 'cosh', tanh: 'tanh',
  log: 'log', ln: 'ln', lg: 'lg', exp: 'exp', lim: 'lim', max: 'max', min: 'min',
  sup: 'sup', inf: 'inf', det: 'det', dim: 'dim', ker: 'ker', gcd: 'gcd',
  deg: 'deg', arg: 'arg', mod: 'mod', bmod: 'mod',
  // 希腊字母
  alpha: 'α', beta: 'β', gamma: 'γ', delta: 'δ', epsilon: 'ε', varepsilon: 'ε',
  zeta: 'ζ', eta: 'η', theta: 'θ', vartheta: 'ϑ', iota: 'ι', kappa: 'κ',
  lambda: 'λ', mu: 'μ', nu: 'ν', xi: 'ξ', pi: 'π', varpi: 'ϖ', rho: 'ρ',
  varrho: 'ϱ', sigma: 'σ', varsigma: 'ς', tau: 'τ', upsilon: 'υ', phi: 'φ',
  varphi: 'φ', chi: 'χ', psi: 'ψ', omega: 'ω',
  Gamma: 'Γ', Delta: 'Δ', Theta: 'Θ', Lambda: 'Λ', Xi: 'Ξ', Pi: 'Π',
  Sigma: 'Σ', Upsilon: 'Υ', Phi: 'Φ', Psi: 'Ψ', Omega: 'Ω',
  // 其他
  ell: 'ℓ', hbar: 'ℏ', Re: 'ℜ', Im: 'ℑ', aleph: 'ℵ',
  checkmark: '✓', square: '□', blacksquare: '■',
};

// 从 s[i] === '{' 开始找配对的花括号，返回 [内容, 结束位置]；配不上返回 null
function readBrace(s, i) {
  if (s[i] !== '{') return null;
  let depth = 0;
  for (let k = i; k < s.length; k++) {
    const ch = s[k];
    if (ch === '\\') { k++; continue; }   // \{ \} 这种转义不算结构
    if (ch === '{') depth++;
    else if (ch === '}') {
      depth--;
      if (depth === 0) return [s.slice(i + 1, k), k + 1];
    }
  }
  return null;
}

// 读命令后面的一个参数：{...} 或者单个字符
function readArg(s, i) {
  while (i < s.length && s[i] === ' ') i++;
  if (i >= s.length) return null;
  if (s[i] === '{') return readBrace(s, i);
  return [s[i], i + 1];
}

// 矩阵 / cases / aligned 这些环境排一下，别让 \begin{pmatrix} 原样露出来
function renderEnv(env, body) {
  const rows = body.split(/\\\\/)
    .map((r) => r.trim())
    .filter((r) => r.length);
  if (!rows.length) return '';
  const cells = rows.map((r) => r.split('&').map((c) => convertLatex(c.trim())));

  if (env === 'aligned' || env === 'align' || env === 'gathered' || env === 'split' ||
      env === 'equation' || env === 'gather') {
    // 多行推导：一行一段，保持原来的顺序
    return '<span class="math-lines">' + cells.map((r) => r.join(' ')).join('<br>') + '</span>';
  }
  const cls = env === 'cases' ? 'math-cases' : 'math-mat';
  const trs = cells.map((r) => '<tr>' + r.map((c) => '<td>' + c + '</td>').join('') + '</tr>').join('');
  return '<table class="' + cls + '"><tbody>' + trs + '</tbody></table>';
}

// 递归转换一段 LaTeX
function convertLatex(src) {
  let out = '';
  let i = 0;
  while (i < src.length) {
    const ch = src[i];

    if (ch === '\\') {
      // \{ \} \% \& \_ \# 这类是「转义成字面量」，直接输出后面的字符
      const esc = src[i + 1];
      if (esc && '{}%&_#'.includes(esc)) {
        out += esc;
        i += 2;
        continue;
      }
      const m = /^\\([A-Za-z]+|[,;:! ])/.exec(src.slice(i));
      if (!m) { out += ch; i++; continue; }
      const name = m[1];
      i += m[0].length;

      // \frac{a}{b}
      if (name === 'frac' || name === 'dfrac' || name === 'tfrac') {
        const a = readArg(src, i);
        if (!a) { out += 'frac'; continue; }
        const b = readArg(src, a[1]);
        if (!b) { out += '(' + convertLatex(a[0]) + ')/'; i = a[1]; continue; }
        const fa = convertLatex(a[0]);
        const fb = convertLatex(b[0]);
        // 简单的 a/b 不加括号，复杂一点的才包起来
        const simple = /^[\w\u0370-\u03ff.]+$/.test(fa) && /^[\w\u0370-\u03ff.]+$/.test(fb);
        out += simple ? fa + '/' + fb : '(' + fa + ')/(' + fb + ')';
        i = b[1];
        continue;
      }

      // \sqrt{x} / \sqrt[n]{x}
      if (name === 'sqrt') {
        let deg = '';
        if (src[i] === '[') {
          const close = src.indexOf(']', i);
          if (close > 0) { deg = src.slice(i + 1, close); i = close + 1; }
        }
        const a = readArg(src, i);
        if (!a) { out += '√'; continue; }
        out += (deg ? '<sup>' + deg + '</sup>' : '') + '√(' + convertLatex(a[0]) + ')';
        i = a[1];
        continue;
      }

      // \text{...} 这类只取里面的文字
      if (name === 'text' || name === 'mathrm' || name === 'mathbf' || name === 'mathit' ||
          name === 'mathsf' || name === 'mathtt' || name === 'operatorname' || name === 'mathcal' ||
          name === 'textbf' || name === 'textit' || name === 'textrm' || name === 'textsf' ||
          name === 'texttt' || name === 'textnormal' || name === 'boxed' ||
          name === 'overline' || name === 'underline') {
        const a = readArg(src, i);
        if (a) { out += convertLatex(a[0]); i = a[1]; continue; }
      }

      // \begin{pmatrix} ... \end{pmatrix}
      if (name === 'begin') {
        const a = readArg(src, i);
        if (a) {
          const env = a[0].trim();
          const endTag = '\\end{' + env + '}';
          const endIdx = src.indexOf(endTag, a[1]);
          if (endIdx >= 0) {
            const body = src.slice(a[1], endIdx);
            // document 只是个外壳，里面照常转
            out += (env === 'document') ? convertLatex(body) : renderEnv(env, body);
            i = endIdx + endTag.length;
            continue;
          }
        }
        continue;
      }

      // 导言区命令：整条丢掉（连同它的 {...} 参数）
      if (name === 'documentclass' || name === 'usepackage' || name === 'pagestyle' ||
          name === 'thispagestyle' || name === 'setlength' || name === 'newcommand') {
        const a = readArg(src, i);
        if (a) i = a[1];
        continue;
      }

      if (name === 'end') continue;   // 孤零零的 \end 丢掉
      if (name === 'left' || name === 'right' || name === 'big' || name === 'Big' ||
          name === 'bigg' || name === 'Bigg' || name === 'displaystyle' || name === 'limits' ||
          name === 'nolimits') continue;   // 纯排版命令，没有字形
      if (name === ',' || name === ';' || name === ':' || name === '!' || name === ' ') {
        out += ' ';
        continue;
      }
      if (name === 'quad') { out += ' '; continue; }
      if (name === 'qquad') { out += '  '; continue; }
      if (name in MATH_WORDS) { out += MATH_WORDS[name]; continue; }
      out += '\\' + name;   // 认不出来就原样留着，绝不吞掉
      continue;
    }

    // 上下标：^{...} _{...} 或者 ^2 _n
    // 注意前面必须是字母/数字/右括号，否则 _这是斜体_ 会被当成下标
    if (ch === '^' || ch === '_') {
      const before = i > 0 ? src[i - 1] : '';
      const canBeScript = /[0-9A-Za-z\u0370-\u03ff}\)\]]/.test(before);
      const a = canBeScript ? readArg(src, i + 1) : null;
      if (a) {
        const tag = ch === '^' ? 'sup' : 'sub';
        out += '<' + tag + '>' + convertLatex(a[0]) + '</' + tag + '>';
        i = a[1];
        continue;
      }
    }

    out += ch;
    i++;
  }
  return out;
}

// URL / 邮箱先藏起来，免得里面的 _ 被当成下标
function protectLinks(text, bag) {
  return text.replace(/https?:\/\/[^\s<>()"'`]+|[\w.+-]+@[\w.-]+\.\w+/g, (m) => {
    bag.push(m);
    return '\u0001' + (bag.length - 1) + '\u0001';
  });
}

function restoreLinks(text, bag) {
  return text.replace(/\u0001(\d+)\u0001/g, (m, n) => bag[Number(n)] || m);
}

function normalizeHeadings(text) {
  // 模型常在表情包 / 公式块后面直接跟「## 第 12 页」，没有空行 → markdown 不认标题，
  // 页面上就露出「## 」两个字。这里给标题行前面补空行。
  return text
    .replace(/([^\n])\n(#{1,6}\s)/g, '$1\n\n$2')
    .replace(/(@@sticker:\d+@@)[ \t]*(#{1,6}\s)/g, '$1\n\n$2')
    .replace(/(\$\$[^$]*\$\$)\s*(#{1,6}\s)/g, '$1\n\n$2');
}

function mathifyText(seg) {
  seg = normalizeHeadings(seg);
  const bag = [];
  let out = protectLinks(seg, bag);

  // 行间公式：单独成块
  out = out.replace(/\$\$([\s\S]+?)\$\$/g,
    (m, e) => '\n\n<div class="math-block">' + convertLatex(e) + '</div>\n\n');
  out = out.replace(/\\\[([\s\S]+?)\\\]/g,
    (m, e) => '\n\n<div class="math-block">' + convertLatex(e) + '</div>\n\n');

  // 行内公式：只认看起来真是数学的（避免把 "$5" 这种金额吃掉）
  out = out.replace(/\$([^$\n]+?)\$/g, (m, e) => {
    // 表头 / 表格里经常是 $0$、$1/3$ 这种很短的式子，光看有没有 ^_= 会漏；
    // 规则：带反斜杠或 ^_= 的一定是公式；否则只要「不含中文、不含空格单词」也当公式
    const looksMath = /[\\^_{}=<>]/.test(e)
      || (/^[\s0-9a-zA-Z()+\-*/.,|]+$/.test(e) && /[0-9]/.test(e));
    if (!looksMath) return m;
    return convertLatex(e);
  });
  out = out.replace(/\\\(([\s\S]+?)\\\)/g, (m, e) => convertLatex(e));

  // 兜底：正文里散落的 \frac、\sin、^{}、_{} 也翻掉
  out = convertLatex(out);

  // 乘法星号：两边都是字母数字才换，避免误伤 markdown 的 * 列表和 **加粗**
  out = out.replace(/([0-9A-Za-z])\s*\*\s*(?=[0-9A-Za-z])/g, '$1 × ');

  return restoreLinks(out, bag);
}

// 模型经常把公式塞进 ``` 代码块里（前半段用 Unicode 画矩阵，后半段又漂成裸 LaTeX），
// 所以代码块不能一律跳过：里面要是 LaTeX 就转，是真代码就原样留着。
const LATEX_HINT = /\\(?:frac|dfrac|tfrac|sqrt|sum|prod|int|iint|iiint|oint|lim|left|right|begin|end|pm|mp|cdot|times|div|le|leq|ge|geq|ne|neq|approx|equiv|cong|infty|alpha|beta|gamma|delta|epsilon|theta|lambda|mu|pi|rho|sigma|tau|phi|chi|psi|omega|Delta|Sigma|Omega|partial|nabla|forall|exists|in|notin|subset|cup|cap|rightarrow|Rightarrow|leftarrow|leftrightarrow|to|mapsto|quad|qquad|text|mathrm|mathbf|mathit|overline|underline|vec|hat|bar|dot|ddot|cdots|ldots|dots|vdots|ddots|prime|circ|degree|angle|perp|parallel|triangle|pm|mp)\b/;

// 真代码的特征：有这些词就绝不按公式处理
const CODE_HINT = /\b(?:function|def|class|import|from|const|let|var|return|if|else|elif|for|while|print|console|public|private|protected|void|static|struct|typedef|include|package|func|async|await|try|catch|throw|raise|lambda|echo|printf|System\.out)\b|;;|=>|->\s*\{|</;

function looksLikeLatex(body) {
  if (!body) return false;
  if (CODE_HINT.test(body)) return false;
  return LATEX_HINT.test(body);
}

function mathifyLegacy(text) {
  // 按围栏代码块 / 行内代码切分
  return text
    .split(/(```[\s\S]*?```|~~~[\s\S]*?~~~|`[^`\n]*`)/g)
    .map((part) => {
      const isFence = part.startsWith('```') || part.startsWith('~~~');
      const isInline = !isFence && part.startsWith('`') && part.endsWith('`') && part.length > 1;

      if (isFence) {
        // 去掉围栏本身，看里面装的是什么
        const body = part.replace(/^(?:```|~~~)[^\n]*\n?/, '').replace(/(?:```|~~~)\s*$/, '');
        if (looksLikeLatex(body)) {
          // LaTeX 的 % 注释行、空行先清掉
          const clean = body
            .split('\n')
            .filter((line) => !/^\s*%/.test(line))
            .join('\n')
            .trim();
          return '\n\n<div class="math-block">' + convertLatex(clean) + '</div>\n\n';
        }
        return part;
      }
      if (isInline) {
        const body = part.slice(1, -1);
        if (looksLikeLatex(body)) return convertLatex(body);
        return part;
      }
      return mathifyText(part);
    })
    .join('');
}

/* ---------------- Markdown / 代码高亮 ---------------- */

/* 模型正文可能被知识库/网页内容带偏，DOMPurify 默认白名单放行 <style> 和
   style 属性，能被拿来塞钓鱼覆盖层，这里一律禁掉。 */
const SANITIZE_CONFIG = { FORBID_TAGS: ['style'], FORBID_ATTR: ['style'] };

/* ---------------- MathJax 公式排版 ----------------
 * 自己把 LaTeX 翻译成 Unicode 永远只能做到「能认出是什么」，做不到豆包那种排版
 * （真正的上下分式、积分上下限、矩阵括号、cases 大括号）。
 * 所以公式交给 MathJax（本地打包的 tex-svg.js，输出 SVG，不依赖网络和字体文件）：
 *   1. mathify 先把 $...$ / $$...$$ 换成占位符，避免 markdown 把 _ * \ 吃掉；
 *   2. marked 渲染完、DOMPurify 洗完，restoreMath 把占位符换回公式元素；
 *   3. typesetMath 调 MathJax 排版（增量渲染只排尾巴，开销可控）。
 */
let mathBag = [];
let lastMathBag = [];
const MATH_TOKEN_RE = /@@math:(\d+)@@/;

function looksMathTex(tex) {
  return /[\\^_{}=<>]/.test(tex)
    || (/^[\s0-9a-zA-Z()+\-*/.,|]+$/.test(tex) && /[0-9]/.test(tex));
}

function mathify(text) {
  if (!window.MathJax || !window.MathJax.typesetPromise) {
    return mathifyLegacy(text);          // MathJax 没加载成功就退回老办法
  }
  const bag = [];
  const out = text
    .split(/(```[\s\S]*?```|~~~[\s\S]*?~~~|`[^`\n]*`)/g)
    .map((part) => {
      const isFence = part.startsWith('```') || part.startsWith('~~~');
      const isInline = !isFence && part.startsWith('`') && part.endsWith('`') && part.length > 1;
      if (isFence || isInline) return part;   // 代码块原样保留
      return part
        .replace(/\$\$([\s\S]+?)\$\$/g, (m, tex) => {
          bag.push({ tex, display: true });
          return `@@math:${bag.length - 1}@@`;
        })
        .replace(/\$([^$\n]+?)\$/g, (m, tex) => {
          if (!looksMathTex(tex)) return m;
          bag.push({ tex, display: false });
          return `@@math:${bag.length - 1}@@`;
        });
    })
    .join('');
  mathBag = bag;
  return out;
}

/* 把占位符换回 <span>，内容就是原始 LaTeX（带 $ 定界符），交给 MathJax */
function restoreMath(root, bag) {
  if (!root || !bag || !bag.length || !window.MathJax) return;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null);
  const nodes = [];
  while (walker.nextNode()) {
    if (walker.currentNode.nodeValue.includes('@@math:')) nodes.push(walker.currentNode);
  }
  nodes.forEach((node) => {
    const parts = node.nodeValue.split(/(@@math:\d+@@)/);
    const frag = document.createDocumentFragment();
    parts.forEach((part) => {
      const m = MATH_TOKEN_RE.exec(part);
      if (m) {
        const item = bag[Number(m[1])];
        if (item) {
          const span = document.createElement('span');
          span.className = item.display ? 'math-tex math-display' : 'math-tex';
          span.textContent = item.display ? `$$${item.tex}$$` : `$${item.tex}$`;
          frag.appendChild(span);
        }
        return;
      }
      if (part) frag.appendChild(document.createTextNode(part));
    });
    if (node.parentNode) node.parentNode.replaceChild(frag, node);
  });
}

const mathTypesetTimers = new WeakMap();

/* 流式时 tail 每 ~100ms 重渲一次，直接排太费；防抖一下，等这一波内容稳定再排 */
function scheduleTypeset(root, delay = 140) {
  if (!root) return;
  const prev = mathTypesetTimers.get(root);
  if (prev) clearTimeout(prev);
  mathTypesetTimers.set(root, setTimeout(() => {
    mathTypesetTimers.delete(root);
    typesetMath(root);
  }, delay));
}

function typesetMath(root) {
  if (!root || !window.MathJax || !window.MathJax.typesetPromise) return;
  try {
    MathJax.typesetClear([root]);
    MathJax.typesetPromise([root]).catch(() => {});
  } catch (err) {
    /* 排版失败就当普通文本留着，别影响聊天 */
  }
}

/* Agent 有时把下载链接直接写成一串 /d/<token>/文件名，markdown 不会自动识别（不是 http 开头）。
   这里在渲染前把它替换成可点击的 markdown 链接，显示成「下载附件」。 */
function linkifyDownloads(text) {
  return String(text || '').replace(
    /(^|[^\w(])(\/d\/[0-9a-f]{32}(?:\/[^\s)\]）>,，。]+)?)/g,
    (m, prefix, url) => `${prefix}[⬇ 下载附件](${url})`,
  );
}

function renderMarkdown(text) {
  mathBag = [];
  const html = DOMPurify.sanitize(marked.parse(mathify(linkifyDownloads(text))), SANITIZE_CONFIG);
  lastMathBag = mathBag;
  mathBag = [];
  return html;
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

/* 复制失败就把代码选中，让用户长按 / 双击手动复制，别干瞪眼 */
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
const RENDER_MIN_MS = 60;

/* 增量渲染：
 *   以前每来一小段就 innerHTML = renderMarkdown(整段)，回复越长每次越慢，
 *   长回复 + 表情包图片一多，主线程直接被占满 → 卡一下然后内容「一股脑」冒出来。
 *   现在把正文分两段：
 *     head —— 已经稳定的段落（以空行/代码块结束为界），只追加渲染一次，不重排；
 *     tail —— 最后一个还没写完的段落，每帧只重渲这一小段。
 */
function scheduleRender(block) {
  if (block.pending) return;
  block.pending = true;
  // 越长间隔越大，进一步压住长回复的重绘次数
  const delay = Math.min(180, RENDER_MIN_MS + Math.floor(block.raw.length / 400) * 20);
  setTimeout(() => requestAnimationFrame(() => {
    block.pending = false;
    renderBlockIncremental(block);
    updateToBottom();
  }), delay);
}

/* 找「稳定切分点」：最后一个空行，且不在没闭合的代码块里 */
function stableSplit(text) {
  let fence = 0;
  let cut = -1;
  for (let i = 0; i < text.length; i += 1) {
    if (text.startsWith('```', i)) {
      fence ^= 1;
      i += 2;
      continue;
    }
    if (!fence && text[i] === '\n' && text[i + 1] === '\n') cut = i + 2;
  }
  return cut;
}

function renderBlockIncremental(block) {
  const raw = block.raw || '';
  if (!block.head) {
    const head = el('div', 'md-head');
    const tail = el('div', 'md-tail');
    block.el.append(head, tail);
    block.head = head;
    block.tail = tail;
    block.rendered = 0;
  }
  const moods = block.turn ? block.turn.stickers : [];

  // 代码围栏还没闭合时，不做「稳定段落搬进 head」的增量优化：
  // 一旦把半个围栏搬进 head、剩下半个留在 tail，markdown 会把 ```asm 当普通文字、
  // 再单独开一个空代码块（用户看到的就是「代码跑到卡片外面」）。
  // 这时候干脆整块重渲，慢一点但一定正确。
  const fencesOpen = (raw.match(/```/g) || []).length % 2 === 1;
  if (fencesOpen) {
    block.head.innerHTML = '';
    block.rendered = 0;
    block.tail.innerHTML = renderMarkdown(raw);
    restoreMath(block.tail, lastMathBag);
    enhance(block.tail);
    hydrateStickers(block.tail, moods);
    scheduleTypeset(block.tail);
    return;
  }

  const cut = stableSplit(raw);
  if (cut > block.rendered) {
    // 稳定段落：渲染一次后整块搬进 head，后面不再碰它
    const holder = document.createElement('div');
    holder.innerHTML = renderMarkdown(raw.slice(block.rendered, cut));
    restoreMath(holder, lastMathBag);
    enhance(holder);
    hydrateStickers(holder, moods);
    while (holder.firstChild) block.head.appendChild(holder.firstChild);
    typesetMath(block.head);
    block.rendered = cut;
  }

  // 尾巴：只渲染还没稳定的那一小段
  const tailText = raw.slice(block.rendered);
  block.tail.innerHTML = renderMarkdown(tailText);
  restoreMath(block.tail, lastMathBag);
  enhance(block.tail);
  hydrateStickers(block.tail, moods);
  scheduleTypeset(block.tail);
}

/* ---------------- 滚动 ---------------- */

/* 不再自动吸底跟随。内容流式增长时视图一动不动，用户想翻到哪就翻到哪，
   不会被新来的内容顶回去。只有发消息这一下才把最新一条带进视野。 */
function scrollToBottom() {
  messagesEl.scrollTop = messagesEl.scrollHeight;
  updateToBottom();
}

/* 刷新 / 切会话之后要稳稳落到最底：markdown 是延迟渲染的、图片也异步加载，
   只滚一次会被后来的内容顶上去。这里在接下来 1 秒内补滚几次；
   用户自己往上翻过就立刻停手，不跟人抢滚动条。 */
let pinTimer = null;
let userHeldUp = false;

messagesEl.addEventListener('scroll', () => {
  const gap = messagesEl.scrollHeight - messagesEl.scrollTop - messagesEl.clientHeight;
  userHeldUp = gap > 40;
}, { passive: true });

function pinToBottom() {
  userHeldUp = false;
  scrollToBottom();
  if (pinTimer) clearInterval(pinTimer);
  const started = Date.now();
  pinTimer = setInterval(() => {
    if (userHeldUp || Date.now() - started > 1000) {
      clearInterval(pinTimer);
      pinTimer = null;
      return;
    }
    scrollToBottom();
  }, 80);
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


/* 后端会在用户消息里塞 [附件 #id uploads/xxx（名字，大小KB）：…] 标记，
   聊天记录里把它变成一排文件卡片，并把标记文本从显示内容里去掉。 */
const ATTACH_MARK_RE = /\[附件 #([0-9a-f]+) uploads\/([^\s（]+)（([^，]*)，(\d+)KB）：[^\]]*\]/g;

function splitAttachments(text) {
  const files = [];
  const clean = String(text || '').replace(ATTACH_MARK_RE, (m, id, fname, name, size) => {
    files.push({ id, name: name || fname, size: Number(size) * 1024 });
    return '';
  }).replace(/\n{3,}/g, '\n\n').trim();
  return { clean, files };
}

function fileChips(files) {
  const wrap = el('div', 'sent-files');
  files.forEach((file) => {
    const item = el('div', 'sent-file');
    const icon = el('span', 'sent-file-icon');
    icon.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/><path d="M14 3v5h5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/></svg>';
    const name = el('span', 'sent-file-name');
    name.textContent = file.name;
    const size = el('span', 'sent-file-size');
    size.textContent = `${Math.max(1, Math.round((file.size || 0) / 1024))}KB`;
    item.append(icon, name, size);
    wrap.appendChild(item);
  });
  return wrap;
}

function addUser(text, images, files) {
  const row = el('div', 'msg user');
  const bubble = el('div', 'bubble');
  if (images && images.length) {
    const wrap = el('div', 'sent-images');
    images.forEach((src) => {
      const img = el('img', 'sent-image');
      img.src = src;
      img.alt = '发送的图片';
      wrap.appendChild(img);
    });
    bubble.appendChild(wrap);
  }
  if (files && files.length) bubble.appendChild(fileChips(files));
  if (text) {
    const box = el('div', 'text');
    box.textContent = text;
    bubble.appendChild(box);
  }
  row.appendChild(bubble);
  messagesEl.appendChild(row);
  scrollToBottom();
  updateEmptyHero(true);
}

/* 历史里的用户消息长这样：「我发了两张图\n\n[图片 #abc123 uploads/abc123.jpg：摘要]」
   把标记抠出来换成缩略图，剩下的当正文；助手的正文里夹着 [sticker:mood]，按位置拆开。 */
const HISTORY_IMG_MARK = /\[图片 #([0-9a-f]{6,32})[^\]]*\]/g;

/* 产物标记：后端在 assistant 正文里留的 [[artifact:16位id|标题]] */
const ARTIFACT_MARK = /\[\[artifact:([0-9a-f]{16})\|([^\]\n]*)\]\]/g;

function updateEmptyHero(hasMessages, welcome) {
  if (!emptyHeroEl) return;
  emptyHeroEl.hidden = Boolean(hasMessages);
  if (welcome && emptyTitleEl) emptyTitleEl.textContent = welcome;
}

document.addEventListener('click', (ev) => {
  const chip = ev.target.closest && ev.target.closest('.empty-chip');
  if (!chip) return;
  inputEl.value = chip.textContent.trim();
  autosize();
  inputEl.focus();
});

function renderHistory(items) {
  // 先合并：工具循环在库里会留好几条连续的 assistant，
  // 不合并的话刷新后一条回复会被切成好几个气泡
  const blocks = [];
  (items || []).forEach((item) => {
    const role = item.role === 'user' ? 'user' : 'assistant';
    const text = String(item.text || '');
    const last = blocks[blocks.length - 1];
    if (role === 'assistant' && last && last.role === 'assistant') {
      last.text += (last.text ? '\n' : '') + text;
    } else {
      blocks.push({ role, text });
    }
  });

  blocks.forEach((block) => {
    if (block.role === 'user') {
      const ids = [];
      const re = new RegExp(HISTORY_IMG_MARK.source, 'g');
      let hit;
      while ((hit = re.exec(block.text)) !== null) ids.push(hit[1]);
      let body = block.text.replace(HISTORY_IMG_MARK, '').trim();
      const parsed = splitAttachments(body);
      body = parsed.clean;
      addUser(body || (ids.length || parsed.files.length ? '' : '（空消息）'),
              ids.map((id) => `/api/uploads/${id}`),
              parsed.files);
      return;
    }
    renderAssistantBlock(block.text);
  });
  updateEmptyHero(blocks.length > 0);
}

/* 渲染一条 assistant 正文：按 [[artifact:...]] 切成几段，段内再处理表情包标记 */
function renderAssistantBlock(text) {
  const turn = addAgent();
  ARTIFACT_MARK.lastIndex = 0;
  let last = 0;
  let hit;
  while ((hit = ARTIFACT_MARK.exec(text)) !== null) {
    renderAssistantText(turn, text.slice(last, hit.index));
    // 关键：把当前正文块「封口」，卡片后面的文字会另起一块排到卡片下方，
    // 不然它们还会往同一个 .content 里追加，DOM 上就都跑到卡片前面去了
    turn.content = null;
    turn.bubble.appendChild(buildArtifactCard(hit[1], hit[2] || ''));
    last = hit.index + hit[0].length;
  }
  renderAssistantText(turn, text.slice(last));
  if (turn.content) scheduleRender(turn.content);
}

/* 段内：正文 + 表情包标记 */
function renderAssistantText(turn, text) {
  if (!text) return;
  text = snapStickersToBoundaries(text);
  const mark = /\[\s*sticker\s*:\s*([a-zA-Z_]+)(?:\s*:\s*([^\]\s]+))?\s*\]/gi;
  let last = 0;
  let hit;
  while ((hit = mark.exec(text)) !== null) {
    const chunk = text.slice(last, hit.index);
    if (chunk) contentChunk(turn, chunk);
    addSticker(turn, hit[1].toLowerCase(), hit[2] || '');
    last = hit.index + hit[0].length;
  }
  const tail = text.slice(last);
  if (tail) contentChunk(turn, tail);
}

function addAgent() {
  const row = el('div', 'msg agent');
  const avatar = el('img', 'avatar');
  avatar.src = AVATAR;
  avatar.alt = '助手';
  const bubble = el('div', 'bubble');
  row.append(avatar, bubble);
  messagesEl.appendChild(row);
  scrollToBottom();
  return { row, bubble, thinking: null, content: null, loading: null, tools: [], stickers: [] };
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
    // 展开的瞬间滚到底部，看到最新那句
    box.addEventListener('toggle', () => {
      if (box.open) body.scrollTop = body.scrollHeight;
    });
    turn.bubble.appendChild(box);
    turn.thinking = { box, body };
  }
  const body = turn.thinking.body;
  body.textContent += text;
  // 面板开着的时候跟着新内容滚到底，不然新写的几句得手动翻
  if (turn.thinking.box.open) body.scrollTop = body.scrollHeight;
  updateToBottom();
}

/* 发出消息后、模型第一个字还没回来：气泡里先放一个「正在想…」的跳动占位，
   别让用户对着一个空气泡发呆。任何事件（思考/正文/工具/表情包/产物）一到就撤掉。 */
function showLoading(turn, labelText) {
  if (!turn) return;
  if (turn.loading) {
    if (labelText) {
      const label = turn.loading.querySelector('.loading-text');
      if (label) label.textContent = labelText;
    }
    return;
  }
  const box = el('div', 'bubble-loading');
  for (let i = 0; i < 3; i += 1) box.appendChild(el('span', 'dot'));
  const label = el('span', 'loading-text');
  label.textContent = labelText || t('loading');
  box.appendChild(label);
  turn.bubble.appendChild(box);
  turn.loading = box;
  // 秒数一直走：模型想很久的时候，用户能看见它确实在动，而不是卡死
  let seconds = 0;
  const baseText = labelText || t('loading');
  turn.loadingTimer = setInterval(() => {
    seconds += 1;
    label.textContent = `${baseText} ${seconds}s`;
  }, 1000);
  updateToBottom();
}

function clearLoading(turn) {
  if (!turn) return;
  if (turn.loadingTimer) {
    clearInterval(turn.loadingTimer);
    turn.loadingTimer = null;
  }
  if (!turn.loading) return;
  turn.loading.remove();
  turn.loading = null;
}

/* 正文/工具/表情包一来，就说明这段思考结束了，把标题标一下。
   盒子开不开交给用户自己决定，这里不强行收起。 */
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
    turn.content = { el: box, raw: '', pending: false, turn };
  }
  turn.content.raw += text;
  scheduleRender(turn.content);
  if (turn.queuedStickers && turn.queuedStickers.length) flushQueuedStickers(turn, false);
}

function toolStart(turn, name, args) {
  settleThinking(turn);
  flushQueuedStickers(turn, true);   // 这一块正文结束了，排队的表情先放掉
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

/* 表情包在正文里放一个占位符（@@sticker:0@@），markdown 渲染完再把占位符换成 <img>。
   这样 markdown 永远是一个完整的块（粗体 / 列表 / 代码块不会被劈开），
   表情包又能停在它**原本的位置**（句子之间），不会一股脑堆到最后。 */
const STICKER_TOKEN_SPLIT = /(@@sticker:\d+@@)/;

/* 表情包只放在「句子结束」处：句末标点 / 换行之后。
   模型经常把 [sticker:x] 写在句子中间（「2026 参考 / [sticker] 2027 参考」），
   而表情包是块级元素，会把一句话劈成两半，很难看。 */
const STICKER_ANY_MARK = /\[\s*sticker\s*:\s*([a-zA-Z_]+)(?:\s*:\s*([^\]\s]+))?\s*\]/gi;

function snapStickersToBoundaries(text) {
  STICKER_ANY_MARK.lastIndex = 0;
  const marks = [];
  let m;
  while ((m = STICKER_ANY_MARK.exec(text)) !== null) {
    marks.push({ start: m.index, end: STICKER_ANY_MARK.lastIndex, raw: m[0] });
  }
  if (!marks.length) return text;

  let out = '';
  let cursor = 0;
  const trailing = [];                                  // 后面找不到边界的，放到最后
  for (const mark of marks) {
    if (mark.start < cursor) continue;                 // 已经被前面的搬走了
    out += text.slice(cursor, mark.start);
    // 往后找最近的句末标点 / 换行
    let cut = -1;
    for (let i = mark.end; i < text.length; i += 1) {
      const ch = text[i];
      if ('。！？!?…；;'.includes(ch)) { cut = i + 1; break; }
      if (ch === '\n') { cut = i + 1; break; }
    }
    if (cut === -1) {
      trailing.push(mark.raw);                         // 这句没写标点：挪到这段最后
      cursor = mark.end;
    } else {
      out += text.slice(mark.end, cut) + mark.raw;     // 先说完这句，再放表情
      cursor = cut;
    }
  }
  out += text.slice(cursor) + trailing.join('');
  return out;
}
const STICKER_TOKEN_EXACT = /^@@sticker:(\d+)@@$/;

function stickerImg(value) {
  // 传进来可能是「心情」也可能是「具体文件名」：
  // 文件名（带后缀）= 发图时已经定死的，原样用；心情 = 老格式，随机挑一张兜底
  const token = String(value || '');
  let name = '';
  if (/\.[a-z0-9]+$/i.test(token)) {
    name = token;
  } else {
    const pool = stickerPool(token);
    if (!pool.length) return null;
    name = pool[Math.floor(Math.random() * pool.length)];
  }
  const img = el('img', 'sticker');
  img.src = `/api/persona/sticker/${encodeURIComponent(name)}`;
  img.alt = token || 'sticker';
  img.loading = 'lazy';
  img.addEventListener('error', () => img.remove());
  return img;
}

function stripStickerTokensInCode(root) {
  // 万一占位符落进了 <pre>/<code>，界面上只会显示 @@sticker:2@@ 这种乱码 → 清掉
  if (!root) return;
  root.querySelectorAll('pre, code').forEach((box) => {
    const walker = document.createTreeWalker(box, NodeFilter.SHOW_TEXT, null);
    const nodes = [];
    while (walker.nextNode()) {
      if (walker.currentNode.nodeValue.includes('@@sticker:')) nodes.push(walker.currentNode);
    }
    nodes.forEach((node) => {
      node.nodeValue = node.nodeValue.replace(/@@sticker:\d+@@/g, '');
    });
  });
}

function hydrateStickers(root, moods) {
  stripStickerTokensInCode(root);
  if (!moods || !moods.length || !root) return;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null);
  const nodes = [];
  while (walker.nextNode()) {
    if (walker.currentNode.nodeValue.includes('@@sticker:')) nodes.push(walker.currentNode);
  }
  nodes.forEach((node) => {
    const parts = node.nodeValue.split(STICKER_TOKEN_SPLIT);
    const frag = document.createDocumentFragment();
    parts.forEach((part) => {
      const match = part.match(STICKER_TOKEN_EXACT);
      if (match) {
        const img = stickerImg(moods[Number(match[1])]);
        if (img) frag.appendChild(img);
        return;
      }
      if (part) frag.appendChild(document.createTextNode(part));
    });
    if (node.parentNode) node.parentNode.replaceChild(frag, node);
  });
}

function addSticker(turn, mood, name) {
  settleThinking(turn);
  const safe = String(mood || '').replace(/[^a-z]/gi, '');
  // name 是发图时就定好的文件名（后端会写进历史标记），有就用它，刷新不会变
  const token = String(name || '').trim() || safe;
  if (!token) return;
  if (!turn.stickers) turn.stickers = [];
  const index = turn.stickers.length;
  turn.stickers.push(token);
  // 先排队：等正文出现句末标点/换行再插，避免把句子劈开
  if (!turn.queuedStickers) turn.queuedStickers = [];
  turn.queuedStickers.push(index);
  // 回复一开头就是表情包（正文还没开始）→ 立刻显示，不然气泡里啥也没有，
  // 后面模型去调工具（可能要十几秒），用户看着就是「卡死了」
  flushQueuedStickers(turn, !turn.content);
}

/* 把排队的表情包插进正文：force=true 时（这一块正文结束了）直接插 */
function flushQueuedStickers(turn, force) {
  if (!turn || !turn.queuedStickers || !turn.queuedStickers.length) return;
  if (!turn.content) {
    if (!force) return;
    const idx = turn.queuedStickers.splice(0);
    idx.forEach((i) => contentChunk(turn, `@@sticker:${i}@@`));
    return;
  }
  const endsAtBoundary = /[。！？!?…；;\n]\s*$/.test(turn.content.raw);
  if (!force && !endsAtBoundary) return;
  // 正文正处在未闭合的 ``` 代码围栏里：先别插，否则占位符会落进代码块变成乱码
  const fences = (turn.content.raw.match(/```/g) || []).length;
  if (!force && fences % 2 === 1) return;
  const index = turn.queuedStickers.shift();
  contentChunk(turn, `@@sticker:${index}@@`);
  if (force && turn.queuedStickers.length) flushQueuedStickers(turn, true);
}

// 助手画的图：用沙箱 iframe 渲染，脚本能跑但摸不到父页面
function addArtifact(turn, event) {
  settleThinking(turn);
  flushQueuedStickers(turn, true);
  turn.content = null;

  const id = String(event.id || '').replace(/[^0-9a-f]/gi, '');
  if (!id) return;

  // 结构化图（draw_diagram）直接内联 SVG：iframe 那套要算高度、还会留大片空白，
  // 手机上看着像「什么都没画」。内联后高度天然自适应，点「放大」还是走产物页。
  if (event.svg) {
    turn.bubble.appendChild(buildInlineSvgCard(id, event.title, event.svg));
  } else {
    turn.bubble.appendChild(buildArtifactCard(id, event.title));
  }
  updateToBottom();
}

/* 画图产物卡片：流式时和刷新后渲染历史都走这一个函数 */
/* 内联 SVG 卡片：标题栏 + 直接塞进气泡的 SVG（可点放大看原图） */
function buildInlineSvgCard(id, titleText, svg) {
  const wrap = el('div', 'artifact artifact-inline');
  const bar = el('div', 'artifact-bar');
  const title = el('span', 'artifact-title');
  title.textContent = titleText || '画的图';
  const open = el('a', 'artifact-open');
  open.href = `/api/artifact/${id}`;
  open.target = '_blank';
  open.rel = 'noopener noreferrer';
  open.textContent = '放大';
  bar.appendChild(title);
  bar.appendChild(open);

  const body = el('div', 'artifact-svg');
  // SVG 是服务端生成的，还是要过一遍净化（防标签/属性注入）
  body.innerHTML = DOMPurify.sanitize(String(svg || ''), {
    USE_PROFILES: { svg: true, svgFilters: true },
    ADD_TAGS: ['use'],
  });
  wrap.appendChild(bar);
  wrap.appendChild(body);
  return wrap;
}

function buildArtifactCard(id, titleText) {
  const wrap = el('div', 'artifact');
  const bar = el('div', 'artifact-bar');
  const title = el('span', 'artifact-title');
  title.textContent = titleText || '画的图';
  const open = el('a', 'artifact-open');
  open.href = `/api/artifact/${id}`;
  open.target = '_blank';
  open.rel = 'noopener noreferrer';
  open.textContent = '放大';
  bar.appendChild(title);
  bar.appendChild(open);

  const frame = el('iframe', 'artifact-frame');
  // 关键：只给 allow-scripts，绝不带 allow-same-origin ——
  // 这样脚本跑在不透明源里，读不到本站的 cookie / localStorage，也摸不到父页面
  frame.setAttribute('sandbox', 'allow-scripts');
  frame.setAttribute('referrerpolicy', 'no-referrer');
  frame.setAttribute('loading', 'lazy');
  frame.setAttribute('title', titleText || '画的图');
  frame.src = `/api/artifact/${id}`;

  wrap.appendChild(bar);
  wrap.appendChild(frame);

  // iframe 是 sandbox（读不到它的 document），只能把可用宽高 post 进去让它自己等比缩放
  fitArtifactFrame(frame);
  frame.addEventListener('load', () => {
    fitArtifactFrame(frame);
    setTimeout(() => fitArtifactFrame(frame), 300);
  });
  // 刷新后历史消息只剩 id（实时流里才带 svg）：把 SVG 再拉回来换掉 iframe，
  // 免得又出现「卡片里一大片空白、以为没画出来」
  inlineSvgFromServer(wrap, id);
  return wrap;
}

/* 从服务端取产物的 <svg> 并内联；拿不到就保留原来的 iframe */
async function inlineSvgFromServer(wrap, id) {
  try {
    const resp = await fetch(`/api/artifact/${id}/svg`, { cache: 'no-store' });
    if (!resp.ok) return;
    const svg = await resp.text();
    if (!svg || svg.indexOf('<svg') < 0) return;
    const frame = wrap.querySelector('iframe.artifact-frame');
    if (!frame) return;
    const body = el('div', 'artifact-svg');
    body.innerHTML = DOMPurify.sanitize(svg, {
      USE_PROFILES: { svg: true, svgFilters: true },
      ADD_TAGS: ['use'],
    });
    frame.replaceWith(body);
    wrap.classList.add('artifact-inline');
  } catch (err) {
    /* 取不到就继续用 iframe */
  }
}

/* 可视高度上限：别让一张图把整个聊天窗铺满，最多占七成 */
function artifactMaxHeight() {
  const vh = window.innerHeight || document.documentElement.clientHeight || 800;
  return Math.max(240, Math.min(820, Math.round(vh * 0.7)));
}

function fitArtifactFrame(frame) {
  if (!frame || !frame.contentWindow) return;
  try {
    frame.contentWindow.postMessage({
      type: 'taffy-artifact-fit',
      width: frame.clientWidth || 360,
      maxHeight: artifactMaxHeight(),
    }, '*');
  } catch (err) {
    /* 发不进去就算了，产物自己会按视口兜底 */
  }
}

// ➜ 窗口尺寸变了（横竖屏切换）重新适配一遍
let artifactResizeTimer = null;
window.addEventListener('resize', () => {
  clearTimeout(artifactResizeTimer);
  artifactResizeTimer = setTimeout(() => {
    document.querySelectorAll('iframe.artifact-frame').forEach(fitArtifactFrame);
  }, 200);
});

// 产物里的脚本会把自身高度报上来，据此调整 iframe 高度（最高 820px）
window.addEventListener('message', (event) => {
  const data = event.data;
  if (!data || data.type !== 'taffy-artifact-height') return;
  const frames = document.querySelectorAll('iframe.artifact-frame');
  for (const frame of frames) {
    if (frame.contentWindow === event.source) {
      const height = Number(data.height) || 0;
      if (height > 0) {
        // 上限跟着可视高度走，最多七成屏
        const maxH = artifactMaxHeight();
        frame.style.height = Math.max(120, Math.min(maxH, height)) + 'px';
      }
      break;
    }
  }
});

function errorBubble(turn, message, canContinue) {
  settleThinking(turn);
  turn.content = null;
  const box = el('div', 'error');
  box.textContent = t('errorPrefix') + message;
  turn.bubble.appendChild(box);
  // 断流 / 网络抖一下这种，给个按钮让模型接着讲，不用自己打字
  if (canContinue) addContinueAction(turn);
  updateToBottom();
}

/* 提示条下面加一个「点我继续」按钮：点了自动发「继续」 */
function addContinueAction(turn) {
  if (!turn) return;
  const row = el('div', 'bubble-actions');
  const btn = el('button', 'continue-btn');
  btn.type = 'button';
  btn.textContent = t('continueBtn');
  btn.addEventListener('click', () => {
    if (busy) return;
    btn.disabled = true;
    row.remove();
    send('继续');
  });
  row.appendChild(btn);
  turn.bubble.appendChild(row);
}

/* 系统提示条（不是助手说的话），比如「历史太长，旧的那段压成摘要了」 */
function noticeBubble(turn, text, canContinue) {
  turn.content = null;
  const box = el('div', 'notice');
  box.textContent = text;
  turn.bubble.appendChild(box);
  if (canContinue) addContinueAction(turn);
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
  // 服务正在重启的那几秒 nginx 会回 502/503/504，等 1.5 秒重试一次，
  // 不然用户点「新会话」正好撞上重启就会莫名其妙失败一次。
  for (let attempt = 0; attempt < 2; attempt += 1) {
    const resp = await fetch('/api/sessions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Client-Capabilities': 'interactive' },
      body: '{}',
    });
    if (resp.ok) {
      const data = await resp.json();
      if (!data || !data.session || !data.session.id) {
        throw new Error('服务返回的会话 id 不对');
      }
      sessionId = data.session.id;
      await loadSessions();
      return sessionId;
    }
    if ([502, 503, 504].includes(resp.status) && attempt === 0) {
      statusEl.textContent = t('restarting');
      await new Promise((resolve) => setTimeout(resolve, 1500));
      continue;
    }
    throw new Error(`HTTP ${resp.status}`);
  }
  throw new Error('重试之后还是不行');
}

/* ---------------- 侧边栏：用户 / 会话列表 ---------------- */

function openSidebar() {
  sidebarEl.classList.add('open');
  sideScrimEl.hidden = false;
}

function closeSidebar() {
  sidebarEl.classList.remove('open');
  sideScrimEl.hidden = true;
}

function paintUser() {
  if (!me) return;
  sideNameEl.textContent = me.name;
  sideInviteCodeEl.textContent = me.invite_code || '—';
  // 只有管理员才看得到「管理员后台」入口
  sideAdminEl.hidden = !me.is_admin;
}

async function loadMe() {
  const resp = await fetch('/api/auth/me', { cache: 'no-store' });
  if (!resp.ok) {
    location.replace('/login');
    return null;
  }
  const data = await resp.json();
  me = data.user;
  paintUser();
  return me;
}

async function loadSessions() {
  try {
    const resp = await fetch('/api/sessions', { cache: 'no-store' });
    if (!resp.ok) return;
    const data = await resp.json();
    sessions = data.sessions || [];
    renderSessionList();
  } catch (err) {
    /* 列不出来就先这样，下一轮再说 */
  }
}

function renderSessionList() {
  sideListEl.innerHTML = '';
  if (!sessions.length) {
    const empty = el('div', 'side-empty');
    empty.textContent = '还没有会话';
    sideListEl.appendChild(empty);
    return;
  }
  sessions.forEach((item) => {
    const row = el('div', 'side-item');
    if (item.id === sessionId) row.classList.add('on');
    const title = el('button', 'side-item-title');
    title.type = 'button';
    title.textContent = item.title || '新会话';
    title.title = item.title || '';
    title.addEventListener('click', () => switchSession(item.id));
    const del = el('button', 'side-item-del');
    del.type = 'button';
    del.textContent = '×';
    del.setAttribute('aria-label', '删除这个会话');
    del.addEventListener('click', async (event) => {
      event.stopPropagation();
      if (!window.confirm(`删掉「${item.title || '新会话'}」？删了就回不来了`)) return;
      // 记住它在列表里的位置，删完跳到「附近」的那条去，而不是新建一个
      const index = sessions.findIndex((row) => row.id === item.id);
      const wasCurrent = item.id === sessionId;
      const resp = await fetch(`/api/sessions/${item.id}`, { method: 'DELETE' });
      // 404 说明它本来就已经没了（别的设备删过），也按删成功处理
      if (!resp.ok && resp.status !== 404) {
        statusEl.textContent = t('deleteFailed', { code: resp.status });
        return;
      }
      // 先从本地列表摘掉，别等网络刷新：万一 loadSessions 失败，
      // 后面就会拿着一个已经不存在的 id 去 switchSession，直接 404
      sessions = sessions.filter((row) => row.id !== item.id);
      renderSessionList();
      await loadSessions();          // 跟服务端对齐；失败也不影响上面的本地摘除
      if (!wasCurrent) {
        // 删的不是当前会话：列表刷新一下就行
        closeSidebar();
        return;
      }
      if (sessions.length) {
        // 优先接原来位置的下一条，没有就退到上一条
        const target = sessions[Math.min(Math.max(index, 0), sessions.length - 1)];
        if (target) {
          await switchSession(target.id);
          closeSidebar();
          return;
        }
      }
      // 一条都不剩了：这时才需要新建一个
      sessionId = null;
      messagesEl.innerHTML = '';
      try {
        await ensureSession();
        welcome();
      } catch (err) {
        statusEl.textContent = t('createSessionFailed', { reason: (err && err.message) || t('unknownReason') });
      }
      closeSidebar();
    });
    row.append(title, del);
    sideListEl.appendChild(row);
  });
}

async function switchSession(id) {
  // 助手正在回话的时候切会话会让两段流搅在一起，先拦一下并说明原因
  if (busy) {
    statusEl.textContent = t('busySwitch');
    closeSidebar();
    return;
  }
  // 就算点的是当前会话也要重画一次：万一页面被某次失败清空了，点一下能救回来
  closeSidebar();
  try {
    const resp = await fetch(`/api/sessions/${id}/messages`, { cache: 'no-store' });
    if (!resp.ok) {
      if (resp.status === 404) {
        // 这条会话已经没了（别的设备删过 / 本地列表过期）：
        // 从列表里摘掉，别留着它一直 404；当前指向它也一并清掉
        sessions = sessions.filter((row) => row.id !== id);
        renderSessionList();
        if (sessionId === id) {
          sessionId = null;
          sessionStorage.removeItem(STORAGE_KEY);
        }
        statusEl.textContent = t('sessionGone');
        if (!sessionId) {
          if (sessions.length) {
            await switchSession(sessions[0].id);
          } else {
            try {
              await ensureSession(true);
              welcome();
            } catch (err) {
              statusEl.textContent = t('createSessionFailed', { reason: (err && err.message) || t('unknownReason') });
            }
          }
        }
        return;
      }
      statusEl.textContent = t('switchFailed', { code: resp.status });
      return;
    }
    const data = await resp.json();
    sessionId = id;
    sessionStorage.setItem(STORAGE_KEY, sessionId);
    messagesEl.innerHTML = '';
    const items = (data && data.history) || [];
    if (items.length) {
      renderHistory(items);
    } else {
      welcome();            // 空会话也得有开场白，不然一片空白
    }
    paintContext(data.context);
    renderSessionList();
    pinToBottom();            // 切会话后稳稳吸底
    statusEl.textContent = onlineText();
  } catch (err) {
    statusEl.textContent = t('switchFailedReason', { reason: (err && err.message) || t('unknownReason') });
  }
  inputEl.focus();
}

sideToggleEl.addEventListener('click', openSidebar);
sideScrimEl.addEventListener('click', closeSidebar);
sideNewEl.addEventListener('click', async () => {
  if (busy) return;
  messagesEl.innerHTML = '';
  sessionId = null;
  paintContext(null);
  try {
    await ensureSession(true);
    welcome();
    loadSessions();
    statusEl.textContent = onlineText();
  } catch (err) {
    statusEl.textContent = t('newSessionFailed', { reason: (err && err.message) || t('unknownReason') });
  }
  closeSidebar();
  inputEl.focus();
});

sideInviteEl.addEventListener('click', async () => {
  const code = (me && me.invite_code) || '';
  if (!code) return;
  try {
    await navigator.clipboard.writeText(code);
    statusEl.textContent = t('copyInvite');
  } catch (err) {
    statusEl.textContent = t('copyFailed', { code });
  }
});

sideLogoutEl.addEventListener('click', async () => {
  try {
    await fetch('/api/auth/logout', { method: 'POST' });
  } catch (err) { /* 忽略 */ }
  location.replace('/login');
});

/* ---------------- 用户中心（改密码 / 退出登录） ---------------- */

function fmtDay(ts) {
  if (!ts) return '—';
  const d = new Date(Number(ts) * 1000);
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

function passMsg(text, ok) {
  passMsgEl.hidden = !text;
  passMsgEl.textContent = text || '';
  passMsgEl.classList.toggle('ok', Boolean(ok));
}

function closeUserModal() {
  passModalEl.hidden = true;
  passMsg('');
  passOldEl.value = '';
  passNewEl.value = '';
  passConfirmEl.value = '';
}

async function openUserModal() {
  closeSidebar();
  passModalEl.hidden = false;
  passMsg('');
  passOldEl.value = '';
  passNewEl.value = '';
  passConfirmEl.value = '';
  // 先拿最新的用户信息，再画
  try {
    const resp = await fetch('/api/auth/me', { cache: 'no-store' });
    if (resp.ok) {
      const data = await resp.json();
      me = data.user || me;
      paintUser();
      if (me) {
        userTitleEl.textContent = me.name;
        userSubEl.textContent = me.is_admin ? '管理员 · 用户中心' : '用户中心';
        userInviteEl.textContent = me.invite_code || '—';
        userCreatedEl.textContent = fmtDay(me.created_at);
        userSessionsEl.textContent = String(data.sessions || 0) + ' 条';
      }
    }
  } catch (err) { /* 拿不到就用缓存的那份 */ }
  if (me && !userTitleEl.textContent) userTitleEl.textContent = me.name;
  passOldEl.focus();
}

userOpenEl.addEventListener('click', openUserModal);
userCloseEl.addEventListener('click', closeUserModal);
passModalEl.addEventListener('click', (event) => {
  if (event.target === passModalEl) closeUserModal();   // 点遮罩关掉
});
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape' && !passModalEl.hidden) closeUserModal();
});

userInviteEl.addEventListener('click', async () => {
  const code = (me && me.invite_code) || userInviteEl.textContent;
  try {
    await navigator.clipboard.writeText(code);
    statusEl.textContent = t('copyInvite');
  } catch (err) {
    statusEl.textContent = t('copyFailed', { code });
  }
});

document.getElementById('pass-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  passMsg('');
  const next = passNewEl.value;
  if (next !== passConfirmEl.value) {
    passMsg('两次输入的新密码不一样');
    passConfirmEl.focus();
    return;
  }
  if (next.length < 8) {
    passMsg('新密码至少 8 位');
    return;
  }
  passOkEl.disabled = true;
  try {
    const resp = await fetch('/api/auth/password', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ old_password: passOldEl.value, new_password: next }),
    });
    let data = null;
    try { data = await resp.json(); } catch (err) { data = null; }
    if (!resp.ok) {
      passMsg((data && data.detail) || '改不了');
      passOkEl.disabled = false;
      return;
    }
    passOldEl.value = '';
    passNewEl.value = '';
    passConfirmEl.value = '';
    passMsg('密码改好啦（下次登录用新的）', true);
    statusEl.textContent = t('passwordSaved');
  } catch (err) {
    passMsg('连不上服务器');
  }
  passOkEl.disabled = false;
});

userLogoutEl.addEventListener('click', async () => {
  try {
    await fetch('/api/auth/logout', { method: 'POST' });
  } catch (err) { /* 退不了也照样跳 */ }
  location.replace('/login');
});

sideLogoutEl.addEventListener('click', async () => {
  try {
    await fetch('/api/auth/logout', { method: 'POST' });
  } catch (err) { /* 忽略 */ }
  location.replace('/login');
});

/* ---------------- 改密码 ---------------- */

function closePassModal() {
  passModalEl.hidden = true;
  passMsgEl.hidden = true;
  passOldEl.value = '';
  passNewEl.value = '';
}

sidePassEl.addEventListener('click', openUserModal);

document.getElementById('pass-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  passMsgEl.hidden = true;
  const resp = await fetch('/api/auth/password', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ old_password: passOldEl.value, new_password: passNewEl.value }),
  });
  let data = null;
  try { data = await resp.json(); } catch (err) { data = null; }
  if (!resp.ok) {
    passMsgEl.hidden = false;
    passMsgEl.textContent = (data && data.detail) || '改不了';
    return;
  }
  closePassModal();
  statusEl.textContent = t('passwordSaved');
});

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
   reader.read() 就那么挂着，界面永远停在「正在思考…」、发送按钮也一直锁着，
   看起来就跟没网了一样（刷新才恢复）。所以每收到一块数据就重置一次计时器，
   静默太久就自己掐掉，把状态放出来。 */
const STREAM_IDLE_MS = 60000;

async function streamChat(id, message, onEvent, images, files) {
  const body = { session_id: id, message, thinking: thinkingOn() };
  if (images && images.length) body.images = images;
  if (files && files.length) body.files = files;

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
    if (stalled) throw new Error('这条回复断在半路了（网络波动），再发一次就好');
    throw err;
  } finally {
    clearTimeout(idleTimer);
  }
}

function handleEvent(turn, event) {
  // 有正文 / 思考 / 工具 / 产物任何一样就撤掉占位；
  // 但单纯的「表情包」事件不算：模型常先甩一张图再去跑十几秒的工具，
  // 这时候占位留着（还带秒数）比空气泡更像在干活。
  if (turn.loading && event.type !== 'sticker') clearLoading(turn);
  switch (event.type) {
    case 'thinking':
      thinkingChunk(turn, event.text);
      break;
    case 'content':
      contentChunk(turn, event.text);
      break;
    case 'tool_pending':
      // 模型正在生成工具参数（画图那一大段 HTML 可能要十几秒）：先显示进度
      showLoading(turn, `正在准备 ${event.name}…`);
      break;
    case 'tool_start':
      // send_sticker 不用画工具卡片，紧接着的 sticker 事件就是它的样子
      if (event.name !== 'send_sticker') toolStart(turn, event.name, event.arguments);
      break;
    case 'tool_end':
      toolEnd(turn, event.name, event.result);
      // 工具跑完还要等模型下一轮：先挂个「正在想…」，别让界面空着
      showLoading(turn);
      break;
    case 'sticker':
      addSticker(turn, event.mood, event.name);
      break;
    case 'artifact':
      addArtifact(turn, event);
      break;
    case 'await_user':
      showAskCard(turn, event);
      break;
    case 'cut':
      noticeBubble(turn, t('cutNotice', { reason: event.reason || '网络中断' }), true);
      break;
    case 'compressed':
      noticeBubble(turn, t('compressedNotice', {
        n: event.dropped,
        before: Math.round(event.before_tokens / 1000),
        after: Math.round(event.after_tokens / 1000),
      }));
      break;
    case 'done':
      if (event.context) paintContext(event.context);
      break;
    case 'error':
      errorBubble(turn, event.message, true);
      break;
    default:
      break;
  }
}

/* ---------- ask_user：服务端让用户拍板时弹的选择卡 ---------- */
function showAskCard(turn, event) {
  const box = el('div', 'ask-card');
  const q = el('div', 'ask-question');
  q.textContent = event.question || '要我怎么做？';
  box.appendChild(q);

  const opts = el('div', 'ask-options');
  const buttons = [];
  (event.options || []).forEach((text) => {
    const btn = el('button', 'ask-option');
    btn.type = 'button';
    btn.textContent = text;
    btn.addEventListener('click', () => submit(text));
    buttons.push(btn);
    opts.appendChild(btn);
  });
  box.appendChild(opts);

  let input = null;
  if (event.allow_custom) {
    const row = el('div', 'ask-custom');
    input = el('input', 'ask-input');
    input.type = 'text';
    input.placeholder = '也可以自己输入…';
    const ok = el('button', 'ask-option ask-send');
    ok.type = 'button';
    ok.textContent = '确定';
    ok.addEventListener('click', () => {
      const v = (input.value || '').trim();
      if (v) submit(v);
    });
    input.addEventListener('keydown', (ev) => {
      if (ev.key === 'Enter') {
        ev.preventDefault();
        const v = (input.value || '').trim();
        if (v) submit(v);
      }
    });
    row.append(input, ok);
    box.appendChild(row);
  }

  let sent = false;
  function submit(text) {
    if (sent) return;
    sent = true;
    buttons.forEach((b) => { b.disabled = true; });
    if (input) input.disabled = true;
    box.classList.add('asked');
    const picked = el('div', 'ask-picked');
    picked.textContent = `已选择：${text}`;
    box.appendChild(picked);
    send(`我选择：${text}`);
  }

  if (turn.el) turn.el.appendChild(box);
  else messagesEl.appendChild(box);
  box.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
}

/* ---------------- 回到前台重连 ----------------
   手机上切后台再切回来，浏览器/系统常年会把之前的连接悄悄掐掉，页面就卡在死状态。
   回到前台先探一下服务还在不在：连着就把状态改回「在线」，连不上就再试几次，
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
        if (!busy) statusEl.textContent = onlineText();
        return;
      }
    } catch (err) {
      /* 没连上，接着试 */
    }
    statusEl.textContent = t('reconnecting');
    await new Promise((resolve) => setTimeout(resolve, RECONNECT_WAIT_MS));
  }
  statusEl.textContent = t('offlineRefresh');
}

document.addEventListener('visibilitychange', resume);
window.addEventListener('online', resume);
// pageshow 每次加载都会触发，只有从 bfcache 里捞回来的那次才需要探活
window.addEventListener('pageshow', (e) => { if (e.persisted) resume(); });

/* ---------------- 发送 ---------------- */

async function send(text, images) {
  if (busy) return;
  busy = true;
  sendEl.disabled = true;
  pickPlusEl.disabled = true;
  inputEl.value = '';
  autosize();
  statusEl.textContent = thinkingText();
  // 先把待发附件记下来：clearImage() 会把 pendingFiles 清空，
  // 之前就是它抢在组装请求前清掉，导致 agent 永远收不到附件
  const filesToSend = pendingFiles.map((f) => ({ id: f.id }));
  addUser(text, images, filesToSend);
  clearImage();

  const turn = addAgent();
  showLoading(turn);            // 首字回来之前先显示「正在想…」
  try {
    const id = await ensureSession();
    await streamChat(id, text, (event) => handleEvent(turn, event), images, filesToSend);
  } catch (err) {
    errorBubble(turn, err && err.message ? err.message : String(err), true);
  } finally {
    clearLoading(turn);
    flushQueuedStickers(turn, true);
    settleThinking(turn);
    if (turn.content) scheduleRender(turn.content);
    busy = false;
    sendEl.disabled = false;
    pickPlusEl.disabled = false;
    statusEl.textContent = onlineText();
    refreshContext();     // 这一轮又添了几条，顺便把上下文占用刷新一下
    loadSessions();       // 首句聊完，标题会从「新会话」变成那句话
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

/* ---------------- 发图片（可以多张） ----------------
   图片只在浏览器里读成 data URL，随请求发上去；服务端先用魔术字节验一遍，
   真图片才落到 workspace/uploads/，助手自己调 read_img 看——这样图片内容
   会走「外部内容边界」，比直接塞多模态请求安全。这一轮结束服务端会把临时
   图片删掉，服务器上不留原图。 */

const IMAGE_MAX_BYTES = 10 * 1024 * 1024; // 单张上限 10MB，超了前端直接提示用户

function renderAttachBar() {
  attachBarEl.innerHTML = '';
  if (!pendingImages.length && !pendingFiles.length) {
    attachBarEl.hidden = true;
    return;
  }
  pendingImages.forEach((src, index) => {
    const item = el('div', 'attach-item');
    const img = el('img', 'attach-preview');
    img.src = src;
    img.alt = '待发送的图片';
    const del = el('button', 'attach-remove');
    del.type = 'button';
    del.textContent = '×';
    del.setAttribute('aria-label', '移除这张图片');
    del.addEventListener('click', () => {
      pendingImages.splice(index, 1);
      renderAttachBar();
      inputEl.focus();
    });
    item.append(img, del);
    attachBarEl.appendChild(item);
  });
  pendingFiles.forEach((file, index) => {
    const item = el('div', 'attach-item attach-file');
    const icon = el('div', 'attach-file-icon');
    icon.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/><path d="M14 3v5h5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/></svg>';
    const name = el('div', 'attach-file-name');
    name.textContent = file.name;
    name.title = `${file.name}（${Math.round((file.size || 0) / 1024)}KB）`;
    const del = el('button', 'attach-remove');
    del.type = 'button';
    del.textContent = '×';
    del.setAttribute('aria-label', '移除这个文件');
    del.addEventListener('click', () => {
      pendingFiles.splice(index, 1);
      renderAttachBar();
      inputEl.focus();
    });
    item.append(icon, name, del);
    attachBarEl.appendChild(item);
  });
  attachBarEl.hidden = false;
}

/* 上传一个普通文件：读成 data URL → 后端存 workspace（20 分钟自动清）→ 拿到 id */
function uploadFileEntry(file) {
  if (pendingFiles.length >= FILE_MAX_COUNT) {
    statusEl.textContent = `最多一次 ${FILE_MAX_COUNT} 个附件`;
    return;
  }
  if (file.size > FILE_MAX_BYTES) {
    statusEl.textContent = `文件太大：${file.name}（上限 20MB）`;
    return;
  }
  const reader = new FileReader();
  reader.onload = async () => {
    try {
      statusEl.textContent = `正在上传 ${file.name}…`;
      const resp = await fetch('/api/uploads/file', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: file.name, data_url: reader.result }),
      });
      const data = await resp.json();
      if (!resp.ok) throw new Error((data && data.detail) || `HTTP ${resp.status}`);
      pendingFiles.push(data.file);
      renderAttachBar();
      statusEl.textContent = `已添加 ${pendingFiles.length} 个文件`;
      inputEl.focus();
    } catch (err) {
      statusEl.textContent = `上传失败：${err.message}`;
    }
  };
  reader.onerror = () => { statusEl.textContent = '读文件失败'; };
  reader.readAsDataURL(file);
}

function clearImage() {
  pendingFiles = [];
  pendingImages = [];
  imageInputEl.value = '';
  attachBarEl.innerHTML = '';
  attachBarEl.hidden = true;
}

pickPlusEl.addEventListener('click', (ev) => {
  ev.stopPropagation();
  attachPopEl.hidden = !attachPopEl.hidden;
});
document.addEventListener('click', () => { attachPopEl.hidden = true; });
attachPopEl.addEventListener('click', (ev) => {
  const opt = ev.target.closest('.attach-opt');
  if (!opt) return;
  attachPopEl.hidden = true;
  if (opt.dataset.kind === 'image') imageInputEl.click();
  else fileInputEl.click();
});
fileInputEl.addEventListener('change', () => {
  const files = Array.from(fileInputEl.files || []);
  fileInputEl.value = '';
  files.forEach(uploadFileEntry);
});

imageInputEl.addEventListener('change', () => {
  const files = Array.from(imageInputEl.files || []);
  imageInputEl.value = '';
  if (!files.length) return;
  let rejected = 0;
  let tooBig = 0;
  files.forEach((file) => {
    if (pendingImages.length >= IMAGE_MAX_COUNT) { rejected += 1; return; }
    if (!file.type.startsWith('image/')) { rejected += 1; return; }
    if (file.size > IMAGE_MAX_BYTES) {
      rejected += 1;
      tooBig += 1;
      if (tooBig === 1) {
        statusEl.textContent = `图片太大了：${file.name}（${(file.size / 1024 / 1024).toFixed(1)}MB，单张上限 10MB）`;
      }
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      if (pendingImages.length >= IMAGE_MAX_COUNT) return;
      pendingImages.push(reader.result);
      renderAttachBar();
      statusEl.textContent = t('imagesSelected', { n: pendingImages.length });
      inputEl.focus();
    };
    reader.onerror = () => { statusEl.textContent = t('readImageFailed'); };
    reader.readAsDataURL(file);
  });
  if (rejected && !tooBig) {
    statusEl.textContent = t('imagesRejected', { n: rejected, max: IMAGE_MAX_COUNT });
  }
});


/* ---------- 桌面端：把文件拖进聊天框就能上传 ---------- */
let dragDepth = 0;
window.addEventListener('dragenter', (ev) => {
  if (!ev.dataTransfer || !Array.from(ev.dataTransfer.types || []).includes('Files')) return;
  dragDepth += 1;
  document.body.classList.add('drop-active');
});
window.addEventListener('dragover', (ev) => {
  if (ev.dataTransfer && Array.from(ev.dataTransfer.types || []).includes('Files')) {
    ev.preventDefault();
    ev.dataTransfer.dropEffect = 'copy';
  }
});
window.addEventListener('dragleave', () => {
  dragDepth = Math.max(0, dragDepth - 1);
  if (!dragDepth) document.body.classList.remove('drop-active');
});
window.addEventListener('drop', (ev) => {
  const files = Array.from((ev.dataTransfer && ev.dataTransfer.files) || []);
  if (!files.length) return;
  ev.preventDefault();
  dragDepth = 0;
  document.body.classList.remove('drop-active');
  files.forEach((file) => {
    if (file.type && file.type.startsWith('image/')) {
      if (pendingImages.length >= IMAGE_MAX_COUNT) return;
      if (file.size > IMAGE_MAX_BYTES) {
        statusEl.textContent = `图片太大了：${file.name}（单张上限 10MB）`;
        return;
      }
      const reader = new FileReader();
      reader.onload = () => { pendingImages.push(reader.result); renderAttachBar(); };
      reader.readAsDataURL(file);
    } else {
      uploadFileEntry(file);
    }
  });
});

/* 回车不发送，老老实实在输入框里换行；要发就点「发送」按钮。
   手机端也一样，软键盘的回车键只负责换行，不会手滑把半句话发出去。 */
formEl.addEventListener('submit', (e) => {
  e.preventDefault();
  const text = inputEl.value.trim();
  if ((!text && !pendingImages.length) || busy) return;
  send(text, pendingImages.slice());
});

newChatEl.addEventListener('click', async () => {
  if (busy) return;
  clearImage();
  messagesEl.innerHTML = '';
  sessionId = null;
  paintContext(null);      // 新会话，占用归零
  updateToBottom();
  try {
    await ensureSession(true);
    welcome();
    pinToBottom();
    refreshContext();
    statusEl.textContent = onlineText();
  } catch (err) {
    statusEl.textContent = t('newSessionFailed', { reason: (err && err.message) || t('unknownReason') });
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
  if (personaInfo && personaInfo.welcome) {
    contentChunk(turn, personaInfo.welcome);
    return;
  }
  contentChunk(
    turn,
    '用户来啦～我是永雏助手，有什么想聊的直接说就好。\n\n' +
    '算法题、代码、知识库里的资料都可以问我；嵌入式、单片机（STM32 那种）、物联网、' +
    '计算机组成原理、数字取证这些助手也懂。左下角可以发图片给助手看（看完就删，不会留着）；' +
    '左上角 ☰ 是侧边栏，你所有的会话都在里面，随时切回来接着聊；' +
    '点顶栏名字能进用户中心（改密码 / 退出登录）；' +
    '想换个话题就点「新会话」，助手会把之前的事忘干净从头开始～\n\n' +
    '输入框上面那条是这段对话占了多少「上下文」，快满的时候助手会自己把前面聊过的' +
    '压成摘要，不会聊着聊着就卡住。'
  );
}

// ---------- 返回键确认：防手滑把聊天关掉 ----------
//
// 手机上从左边一划（或者按返回键）就把整个页面带走了，聊到一半的内容直接没。
// 做法：页面进来先压一格「守护历史」，这样用户按返回时触发的是 popstate、
// 页面留在原地，我们先弹自己的确认框；确认了才真的退出去。
const leaveModalEl = document.getElementById('leave-modal');
const leaveCancelEl = document.getElementById('leave-cancel');
const leaveOkEl = document.getElementById('leave-ok');

let leavingConfirmed = false;   // 已经确认离开，别再拦

function closeLeaveModal() {
  leaveModalEl.hidden = true;
}

// 压一格守护历史。确认离开之后就别再压了，否则用户永远退不出去。
function armLeaveGuard() {
  if (leavingConfirmed) return;
  history.pushState({ taffyGuard: 1 }, '', location.href);
}

window.addEventListener('popstate', () => {
  if (leavingConfirmed) return;
  armLeaveGuard();              // 先把历史补回来，页面留在原地
  leaveModalEl.hidden = false;  // 弹确认框
});

leaveCancelEl.addEventListener('click', closeLeaveModal);

leaveOkEl.addEventListener('click', () => {
  leavingConfirmed = true;
  closeLeaveModal();
  // 退两格：一格是守护格，一格才是真正的上一页。
  // 第二次退的时候如果历史里已经没有更早的页面（比如微信里直接打开链接），
  // 浏览器会自己忽略，用户再按一次返回就退出了。
  history.back();
  setTimeout(() => history.back(), 120);
});

// 点遮罩空白处 = 取消
leaveModalEl.addEventListener('click', (event) => {
  if (event.target === leaveModalEl) closeLeaveModal();
});

// ESC 也取消
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape' && !leaveModalEl.hidden) closeLeaveModal();
});

armLeaveGuard();

(async function init() {
  loadStickerLib();   // 不等它，表情包清单到了就用新的，没到就用兜底那份
  loadPersona();      // 同理：名字/头像/欢迎语拿到了就换，没到先用内置文案
  try {
    await loadMe();                 // 没登录会直接跳 /login
    await loadSessions();
    if (sessions.length) {
      // 接着最近聊的那条会话
      const latest = sessions[0].id;
      const resp = await fetch(`/api/sessions/${latest}/messages`, { cache: 'no-store' });
      if (resp.ok) {
        const data = await resp.json();
        sessionId = latest;
        messagesEl.innerHTML = '';
        const items = data.history || [];
        if (items.length) {
          renderHistory(items);
        } else {
          welcome();          // 空会话给个开场白，别对着空白页发呆
        }
        paintContext(data.context);
        pinToBottom();          // 历史渲染完稳稳吸底
      }
    }
    if (!sessionId) {
      await ensureSession();
      welcome();
    }
    renderSessionList();
    refreshContext();  // 刷新页面后把上下文用了几成接回来
  } catch (err) {
    statusEl.textContent = t('offline');
  }
  inputEl.focus();
})();
