/*
 * 单条消息的渲染逻辑，和网页版 app.js 保持一致：
 *   LaTeX 兜底转换 → marked 解析 → DOMPurify 消毒（禁 style）→ 代码高亮。
 * 渲染完把高度回报给原生层，Compose 才能给 WebView 定高。
 */
(function () {
  'use strict';

  var root = document.getElementById('root');

  var MATH_WORDS = {
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
    Sigma: 'Σ', Upsilon: 'Υ', Phi: 'Φ', Psi: 'Ψ', Omega: 'Ω'
  };

  function demathExpr(expr) {
    var s = expr;
    s = s.replace(/\\[,;!]/g, ' ');
    s = s.replace(/\\(?:d|t)?frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}/g, '($1)/($2)');
    s = s.replace(/\\sqrt\s*\[([^\]]*)\]\s*\{([^{}]*)\}/g, '$1√($2)');
    s = s.replace(/\\sqrt\s*\{([^{}]*)\}/g, '√($1)');
    s = s.replace(/\^\s*\{([^{}]*)\}/g, '^($1)');
    s = s.replace(/_\s*\{([^{}]*)\}/g, '_($1)');
    s = s.replace(/\\(?:text|mathrm|mathbf|mathit|operatorname)\s*\{([^{}]*)\}/g, '$1');
    s = s.replace(/\\(?:left|right)\s*/g, '');
    s = s.replace(/\\([A-Za-z]+)/g, function (m, name) {
      return Object.prototype.hasOwnProperty.call(MATH_WORDS, name) ? MATH_WORDS[name] : m;
    });
    return s;
  }

  function demathText(seg) {
    var out = seg
      .replace(/\$\$([\s\S]+?)\$\$/g, function (m, e) { return demathExpr(e); })
      .replace(/\\\[([\s\S]+?)\\\]/g, function (m, e) { return demathExpr(e); })
      .replace(/\$([^$\n]+?)\$/g, function (m, e) { return demathExpr(e); })
      .replace(/\\\(([\s\S]+?)\\\)/g, function (m, e) { return demathExpr(e); });
    return out.replace(/\\([A-Za-z]+)/g, function (m, name) {
      return Object.prototype.hasOwnProperty.call(MATH_WORDS, name) ? MATH_WORDS[name] : m;
    });
  }

  function demath(text) {
    return String(text || '')
      .split(/(~~~[\s\S]*?~~~|```[\s\S]*?```|`[^`\n]*`)/g)
      .map(function (part) {
        var isCode = part.indexOf('```') === 0 || part.indexOf('~~~') === 0 ||
          (part.charAt(0) === '`' && part.charAt(part.length - 1) === '`' && part.length > 1);
        return isCode ? part : demathText(part);
      })
      .join('');
  }

  marked.setOptions({ gfm: true, breaks: true });

  // 模型正文可能被资料带偏：<style> 和 style 属性一律禁掉，防钓鱼覆盖层
  var SANITIZE_CONFIG = { FORBID_TAGS: ['style'], FORBID_ATTR: ['style'] };

  function renderMarkdown(text) {
    return DOMPurify.sanitize(marked.parse(demath(text)), SANITIZE_CONFIG);
  }

  function enhance() {
    var links = root.querySelectorAll('a[href]');
    for (var i = 0; i < links.length; i++) {
      links[i].target = '_blank';
      links[i].rel = 'noopener noreferrer';
    }
    try { hljs.highlightAll(); } catch (e) { /* 高亮挂了不影响正文 */ }
  }

  var lastHeight = -1;
  var pending = false;

  function report() {
    var height = Math.ceil(document.body.getBoundingClientRect().height);
    if (height === lastHeight) return;
    lastHeight = height;
    var bridge = window.DeckBridge;
    if (bridge && typeof bridge.onHeight === 'function') {
      bridge.onHeight(height);
    }
  }

  function scheduleReport() {
    if (pending) return;
    pending = true;
    requestAnimationFrame(function () {
      pending = false;
      report();
    });
  }

  function decodeBase64(b64) {
    var binary = atob(b64);
    var bytes = new Uint8Array(binary.length);
    for (var i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
    if (window.TextDecoder) return new TextDecoder('utf-8').decode(bytes);
    return decodeURIComponent(escape(binary));
  }

  // 原生层调用：把 markdown（base64 过的 UTF-8）渲染出来
  window.deckUpdate = function (b64) {
    var text = decodeBase64(b64);
    text = text.replace(/\[sticker:([A-Za-z_]+)(?::([^\]\s]+))?\]/g, function (m, mood, name) {
      if (!name) return '';
      return '\n\n<img class="sticker" alt="' + mood + '" src="/api/persona/sticker/' +
        encodeURIComponent(name) + '">\n\n';
    });
    root.innerHTML = renderMarkdown(text);
    enhance();
    lastHeight = -1;
    scheduleReport();
  };

  window.addEventListener('load', scheduleReport);
  window.addEventListener('resize', scheduleReport);
  var images = document.getElementsByTagName('img');
  for (var i = 0; i < images.length; i++) {
    images[i].addEventListener('load', scheduleReport);
  }
  if (window.ResizeObserver) {
    new ResizeObserver(scheduleReport).observe(document.body);
  }
  scheduleReport();
})();
