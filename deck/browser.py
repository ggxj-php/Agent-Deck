"""浏览器操控：Playwright + Chromium（patchright 隐身版），单实例多 Context。

设计要点（照大厂 agent 的浏览器层做的）：
- 一个 Chromium 实例、每个用户一个 BrowserContext（登录态隔离，省内存）；
- 所有 Playwright 操作跑在**单独的线程**里（Playwright 的 sync API 要求单线程），
  其它线程通过队列提交任务，避免 greenlet 跨线程报错；
- 默认拦掉图片/字体/媒体渲染（省内存，图要下载走 context.request 另取）；
- 每个用户一个步骤预算（BROWSER_MAX_STEPS），超了直接拒绝，配合 core 的熔断；
- 空闲自动关 Context / 关浏览器，避免 2~4G 小机器被吃满。
"""
import os
import queue
import threading
import time
import zipfile
from concurrent.futures import Future
from urllib.parse import urlparse

from .config import (
    BROWSER_BLOCK_ASSETS,
    BROWSER_IDLE_MINUTES,
    BROWSER_MAX_CONTEXTS,
    BROWSER_MAX_STEPS,
    BROWSER_PROXY,
    BROWSER_TIMEOUT_MS,
    PROJECT_ROOT,
)

WORKSPACE = os.path.join(PROJECT_ROOT, "workspace")
BROWSER_DIR = os.path.join(WORKSPACE, "browser")
MAX_IMAGES = 40
MAX_TEXT = 6000
LAUNCH_ARGS = [
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--disable-extensions",
    "--disable-background-networking",
    "--disable-sync",
    "--mute-audio",
    "--js-flags=--max-old-space-size=160",
]

_SNAPSHOT_JS = r"""
() => {
  const sel = 'a,button,input,textarea,select,[role=button],[role=link],[role=tab],'
    + '[role=checkbox],[role=radio],[onclick],[contenteditable=true],h1,h2,h3,li';
  const seen = new Set();
  const items = [];
  const isVisible = (el) => {
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return false;
    const st = getComputedStyle(el);
    return st.display !== 'none' && st.visibility !== 'hidden' && Number(st.opacity) > 0.05;
  };
  const textOf = (el) => {
    let t = (el.innerText || el.value || el.getAttribute('aria-label')
             || el.getAttribute('placeholder') || el.getAttribute('title') || '');
    return t.replace(/\s+/g, ' ').trim().slice(0, 80);
  };
  let n = 0;
  for (const el of document.querySelectorAll(sel)) {
    if (n >= 160) break;
    if (!isVisible(el)) continue;
    const tag = el.tagName.toLowerCase();
    const role = el.getAttribute('role') || '';
    const text = textOf(el);
    if (!text && !['input', 'textarea', 'select'].includes(tag)) continue;
    const key = tag + '|' + text + '|' + Math.round(el.getBoundingClientRect().top / 24);
    if (seen.has(key)) continue;
    seen.add(key);
    n += 1;
    el.setAttribute('data-dsh-ref', String(n));
    items.push({
      ref: n, tag: tag, role: role,
      text: text,
      href: tag === 'a' ? (el.getAttribute('href') || '').slice(0, 120) : '',
      input_type: tag === 'input' ? (el.getAttribute('type') || 'text') : '',
      value: ['input', 'textarea', 'select'].includes(tag) ? String(el.value || '').slice(0, 40) : '',
    });
  }
  return {
    url: location.href,
    title: document.title || '',
    items: items,
    text: (document.body ? document.body.innerText : '').replace(/\n{3,}/g, '\n\n').slice(0, 6000),
  };
}
"""

_COLLECT_IMAGES_JS = r"""
() => {
  const urls = [];
  const push = (u, w, h, alt) => { if (u) urls.push({url: u, w: w || 0, h: h || 0, alt: (alt || '').slice(0, 60)}); };
  for (const img of document.querySelectorAll('img')) {
    push(img.currentSrc || img.src, img.naturalWidth, img.naturalHeight, img.alt);
    const srcset = img.getAttribute('srcset') || '';
    const parts = srcset.split(',').map(s => s.trim().split(/\s+/)[0]).filter(Boolean);
    if (parts.length) push(parts[parts.length - 1], img.naturalWidth, img.naturalHeight, img.alt);
  }
  for (const el of document.querySelectorAll('*')) {
    const bg = getComputedStyle(el).backgroundImage || '';
    const m = bg.match(/url\(["']?(.+?)["']?\)/);
    if (m) push(m[1], el.clientWidth, el.clientHeight, '');
  }
  for (const v of document.querySelectorAll('video')) {
    if (v.poster) push(v.poster, v.videoWidth, v.videoHeight, 'video-poster');
  }
  for (const el of document.querySelectorAll('[data-src],[data-original],[data-lazy]')) {
    push(el.getAttribute('data-src') || el.getAttribute('data-original')
         || el.getAttribute('data-lazy'), 0, 0, '');
  }
  const og = document.querySelector('meta[property="og:image"], meta[name="twitter:image"]');
  if (og) push(og.getAttribute('content'), 0, 0, 'og:image');
  const seen = new Set();
  return urls.filter(x => {
    if (!/^https?:\/\//i.test(x.url) || seen.has(x.url)) return false;
    seen.add(x.url);
    return true;
  });
}
"""

_CLICK_JS = r"""
(ref) => {
  const el = document.querySelector(`[data-dsh-ref="${ref}"]`);
  if (!el) return false;
  el.scrollIntoView({block: 'center', inline: 'center'});
  el.click();
  return true;
}
"""

_TYPE_JS = r"""
([ref, text]) => {
  const el = document.querySelector(`[data-dsh-ref="${ref}"]`);
  if (!el) return false;
  el.scrollIntoView({block: 'center'});
  el.focus();
  if ('value' in el) {
    const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, 'value') && Object.getOwnPropertyDescriptor(proto, 'value').set;
    if (setter) setter.call(el, text); else el.value = text;
    el.dispatchEvent(new Event('input', {bubbles: true}));
    el.dispatchEvent(new Event('change', {bubbles: true}));
    return true;
  }
  if (el.isContentEditable) { el.innerText = text; el.dispatchEvent(new InputEvent('input', {bubbles: true})); return true; }
  return false;
}
"""


class _Session:
    __slots__ = ("ctx", "page", "last", "steps")

    def __init__(self, ctx):
        self.ctx = ctx
        self.page = ctx.new_page()
        self.last = time.time()
        self.steps = 0

    def close(self):
        try:
            self.ctx.close()
        except Exception:                      # noqa: BLE001
            pass


class BrowserManager:
    """全局单例：一个 Chromium + 每用户一个 Context + 独立 Playwright 线程。"""

    def __init__(self):
        self._jobs = queue.Queue()
        self._sessions = {}
        self._lock = threading.Lock()
        self._pw = None
        self._browser = None
        self._worker = threading.Thread(target=self._work, daemon=True, name="browser-worker")
        self._worker.start()
        self._sweeper = threading.Thread(target=self._sweep, daemon=True, name="browser-sweeper")
        self._sweeper.start()

    # ---------- 线程基础设施 ----------
    def _work(self):
        while True:
            item = self._jobs.get()
            if item is None:
                return
            fn, args, kwargs, fut = item
            try:
                fut.set_result(fn(*args, **kwargs))
            except BaseException as exc:        # noqa: BLE001
                fut.set_exception(exc)

    def _submit(self, fn, *args, timeout=150, **kwargs):
        fut = Future()
        self._jobs.put((fn, args, kwargs, fut))
        return fut.result(timeout)

    def _ensure(self):
        if self._browser is not None:
            return
        from patchright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        kwargs = {"headless": True, "args": LAUNCH_ARGS}
        if BROWSER_PROXY:
            kwargs["proxy"] = {"server": BROWSER_PROXY}
        self._browser = self._pw.chromium.launch(**kwargs)

    def _route(self, route):
        try:
            rtype = route.request.resource_type
            if BROWSER_BLOCK_ASSETS and rtype in ("image", "media", "font"):
                route.abort()
            else:
                route.continue_()
        except Exception:                       # noqa: BLE001
            try:
                route.continue_()
            except Exception:                   # noqa: BLE001
                pass

    def _session(self, owner):
        self._ensure()
        sess = self._sessions.get(owner)
        if sess is not None:
            sess.last = time.time()
            return sess
        if len(self._sessions) >= BROWSER_MAX_CONTEXTS:
            victim_key = min(self._sessions, key=lambda k: self._sessions[k].last)
            self._sessions.pop(victim_key).close()
        ctx = self._browser.new_context(
            viewport={"width": 1280, "height": 800},
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
        )
        ctx.set_default_timeout(BROWSER_TIMEOUT_MS)
        ctx.set_default_navigation_timeout(BROWSER_TIMEOUT_MS)
        ctx.route("**/*", self._route)
        sess = _Session(ctx)
        self._sessions[owner] = sess
        return sess

    def _step(self, sess):
        sess.steps += 1
        if sess.steps > BROWSER_MAX_STEPS:
            raise RuntimeError(f"浏览器步骤预算用完了（{BROWSER_MAX_STEPS} 步）。"
                               "现在应该停下来，告诉用户已经做到哪一步、还差什么。")
        sess.last = time.time()

    def _sweep(self):
        while True:
            time.sleep(60)
            try:
                self._submit(self._sweep_once, timeout=60)
            except Exception:                   # noqa: BLE001
                pass

    def _sweep_once(self):
        now = time.time()
        idle = BROWSER_IDLE_MINUTES * 60
        for owner in list(self._sessions):
            sess = self._sessions[owner]
            if now - sess.last > idle:
                self._sessions.pop(owner).close()
        if not self._sessions and self._browser is not None:
            try:
                self._browser.close()
            finally:
                self._browser = None

    # ---------- 对外动作（在工具线程里调用） ----------
    def reset_steps(self, owner):
        def _do():
            sess = self._sessions.get(owner)
            if sess is not None:
                sess.steps = 0
                sess.last = time.time()
        try:
            self._submit(_do, timeout=10)
        except Exception:                       # noqa: BLE001
            pass

    def close(self, owner):
        def _do():
            sess = self._sessions.pop(owner, None)
            if sess is not None:
                sess.close()
            return True
        return self._submit(_do, timeout=30)

    def open(self, owner, url):
        return self._submit(self._open, owner, url)

    def snapshot(self, owner):
        return self._submit(self._snapshot, owner)

    def click(self, owner, ref):
        return self._submit(self._click, owner, ref)

    def type(self, owner, ref, text):
        return self._submit(self._type, owner, ref, text)

    def key(self, owner, key):
        return self._submit(self._key, owner, key)

    def scroll(self, owner, dy):
        return self._submit(self._scroll, owner, dy)

    def extract(self, owner):
        return self._submit(self._extract, owner)

    def images(self, owner):
        return self._submit(self._images, owner, timeout=240)

    def screenshot(self, owner):
        return self._submit(self._screenshot, owner, timeout=120)

    # ---------- 具体实现（只在 worker 线程里跑） ----------
    @staticmethod
    def _check_url(url):
        url = str(url or "").strip()
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise RuntimeError("只支持 http/https 网址")
        host = (parsed.hostname or "").lower()
        if host in ("localhost", "127.0.0.1", "0.0.0.0", "::1") or host.endswith(".local"):
            raise RuntimeError("不允许访问本机/内网地址")
        return url

    def _open(self, owner, url):
        url = self._check_url(url)
        sess = self._session(owner)
        self._step(sess)
        try:
            resp = sess.page.goto(url, wait_until="domcontentloaded",
                                  timeout=BROWSER_TIMEOUT_MS)
        except Exception as exc:                # noqa: BLE001
            # 有些站点（百度这类）一直有后台请求，domcontentloaded 迟迟不触发，
            # 但页面其实已经有内容了：能读到标题/正文就继续，不白等 30 秒。
            try:
                title = sess.page.title()
                body = sess.page.evaluate("() => document.body ? document.body.innerText.length : 0")
            except Exception:                   # noqa: BLE001
                title, body = "", 0
            if not title and not body:
                raise RuntimeError(f"打开页面失败（{exc}）。如果一直超时，可能是这个网站"
                                   "在服务器所在地区访问不了，或者被风控拦截。") from exc
        try:
            sess.page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:                       # noqa: BLE001
            pass
        if sess.page.url.startswith("chrome-error://"):
            raise RuntimeError("页面加载失败（浏览器错误页）。可能是网址打不开、"
                               "被墙、或者被网站风控拦截。")
        data = self._snapshot_data(sess)
        data["status"] = resp.status if resp else 0
        return data

    def _snapshot_data(self, sess):
        raw = sess.page.evaluate(_SNAPSHOT_JS)
        items = raw.get("items") or []
        out = []
        for item in items[:120]:
            row = {"ref": item.get("ref"), "tag": item.get("tag"),
                   "text": str(item.get("text") or "")[:80]}
            if item.get("role"):
                row["role"] = item["role"]
            if item.get("href"):
                row["href"] = item["href"]
            if item.get("input_type") and item["input_type"] != "text":
                row["type"] = item["input_type"]
            if item.get("value"):
                row["value"] = item["value"]
            out.append(row)
        return {
            "url": raw.get("url") or sess.page.url,
            "title": raw.get("title") or "",
            "elements": out,
            "text_head": str(raw.get("text") or "")[:1500],
        }

    def _snapshot(self, owner):
        sess = self._session(owner)
        self._step(sess)
        return self._snapshot_data(sess)

    def _click(self, owner, ref):
        sess = self._session(owner)
        self._step(sess)
        ok = sess.page.evaluate(_CLICK_JS, int(ref))
        if not ok:
            return {"ok": False, "error": f"页面上没有 ref={ref} 的元素，先 browser_snapshot 重新看一遍"}
        try:
            sess.page.wait_for_load_state("networkidle", timeout=6000)
        except Exception:                       # noqa: BLE001
            pass
        data = self._snapshot_data(sess)
        data["ok"] = True
        return data

    def _type(self, owner, ref, text):
        sess = self._session(owner)
        self._step(sess)
        ok = sess.page.evaluate(_TYPE_JS, [int(ref), str(text)])
        return {"ok": bool(ok), "ref": int(ref),
                "error": "" if ok else f"ref={ref} 不是可输入元素"}

    def _key(self, owner, key):
        sess = self._session(owner)
        self._step(sess)
        sess.page.keyboard.press(str(key))
        try:
            sess.page.wait_for_load_state("networkidle", timeout=6000)
        except Exception:                       # noqa: BLE001
            pass
        data = self._snapshot_data(sess)
        data["ok"] = True
        return data

    def _scroll(self, owner, dy):
        sess = self._session(owner)
        self._step(sess)
        sess.page.mouse.wheel(0, int(dy))
        time.sleep(0.4)
        data = self._snapshot_data(sess)
        data["ok"] = True
        return data

    def _extract(self, owner):
        sess = self._session(owner)
        self._step(sess)
        text = sess.page.evaluate("() => document.body ? document.body.innerText : ''")
        text = str(text or "")
        text = "\n".join(line.rstrip() for line in text.splitlines() if line.strip())
        truncated = len(text) > MAX_TEXT
        return {"url": sess.page.url, "title": sess.page.title(),
                "text": text[:MAX_TEXT], "truncated": truncated,
                "length": len(text)}

    def _images(self, owner):
        sess = self._session(owner)
        self._step(sess)
        page = sess.page
        raw = page.evaluate(_COLLECT_IMAGES_JS) or []
        seen = set()
        urls = []
        for item in raw:
            url = str(item.get("url") or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            urls.append(url)
            if len(urls) >= MAX_IMAGES:
                break
        outdir = os.path.join(BROWSER_DIR, str(owner), time.strftime("%Y%m%d-%H%M%S"))
        os.makedirs(outdir, exist_ok=True)
        saved = []
        headers = {"referer": page.url,
                   "user-agent": page.evaluate("() => navigator.userAgent")}
        for index, url in enumerate(urls, start=1):
            try:
                resp = page.context.request.get(url, headers=headers, timeout=20000)
                if not resp.ok:
                    continue
                body = resp.body()
                if len(body) < 1024:
                    continue
                ctype = (resp.headers or {}).get("content-type", "")
                ext = ".jpg" if "jpeg" in ctype or "jpg" in ctype else (
                    ".png" if "png" in ctype else (
                        ".webp" if "webp" in ctype else (
                            ".gif" if "gif" in ctype else ".jpg")))
                path = os.path.join(outdir, f"{index:03d}{ext}")
                with open(path, "wb") as fp:
                    fp.write(body)
                saved.append(path)
            except Exception:                   # noqa: BLE001
                continue
        if not saved:
            return {"ok": False, "error": "这个页面上没抓到可下载的图片（可能是懒加载，先 browser_scroll 再试）",
                    "url": page.url}
        zip_path = outdir + ".zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_STORED) as zf:
            for path in saved:
                zf.write(path, os.path.basename(path))
        return {"ok": True, "count": len(saved), "dir": outdir,
                "files": [os.path.basename(p) for p in saved],
                "zip_path": zip_path, "page_url": page.url}

    def _screenshot(self, owner):
        sess = self._session(owner)
        self._step(sess)
        outdir = os.path.join(BROWSER_DIR, str(owner))
        os.makedirs(outdir, exist_ok=True)
        path = os.path.join(outdir, time.strftime("shot-%Y%m%d-%H%M%S.png"))
        sess.page.screenshot(path=path, full_page=False)
        return {"ok": True, "path": path, "url": sess.page.url, "title": sess.page.title()}


_MANAGER = None
_MANAGER_LOCK = threading.Lock()


def manager():
    global _MANAGER
    if _MANAGER is None:
        with _MANAGER_LOCK:
            if _MANAGER is None:
                _MANAGER = BrowserManager()
    return _MANAGER


def status(owner=None):
    """给后台/测试看的运行状态（不启动浏览器）。"""
    mgr = manager()
    return {
        "running": mgr._browser is not None,
        "sessions": len(mgr._sessions),
        "owner_session": bool(owner and owner in mgr._sessions),
        "max_contexts": BROWSER_MAX_CONTEXTS,
        "max_steps": BROWSER_MAX_STEPS,
        "block_assets": BROWSER_BLOCK_ASSETS,
    }
