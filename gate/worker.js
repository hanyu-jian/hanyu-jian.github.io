// Cloudflare Worker：静态网站前面的「token 门卫」。
// 没有有效 token 的请求一律不放行；token 对了就设置 Cookie，之后正常浏览。
//
// 配置（Cloudflare → 这个 Worker → Settings → Variables and Secrets，类型选 Secret）：
//   DASHBOARD_TOKENS = alice:token1,bob:token2     名字:token，逗号分隔；名字只用来区分是谁
//
// 同事使用方式：
//   1. 打开网址，在登录页粘贴 token；或者你直接发这样的链接：https://你的网址/?token=token1
//   2. 浏览器记住 30 天（HttpOnly Cookie）。想停用某人，从 DASHBOARD_TOKENS 里删掉他那一项即可。
//   3. /logout 退出登录。
//
// 运营数据（/ops/…，匈牙利五个场站的发电量）使用「完全独立」的另一套 token：
//   OPS_TOKENS = alice:optoken1,bob:optoken2   同样是 Secret；和 DASHBOARD_TOKENS 互不通用
//   GITHUB_TOKEN = 只读（Contents: Read-only）、只授权 vcom 仓库的 fine-grained token，Secret；用来读私有 vcom 仓库的发电量 CSV
//   GITHUB_REPO  = 可选变量，默认 hanyu-jian/vcom
// 运营 token 只能打开 /ops/ 下的页面和接口；普通看板 token 打不开 /ops/，反过来运营 token 也打不开普通页面。
// OPS_TOKENS 没配置时 /ops/ 一律拒绝，不影响其它页面。

const PAGES = new Set([
  "capacity", "capture-price", "generation-overview", "generation-type", "imbalance", "balancing-afrr-capacity",
  "load", "price-intraday", "price-overview", "price-spread", "project",
]);
const COOKIE = "dash_token";
const OPS_COOKIE = "ops_token";
// 运营页面：对外地址 /ops/<name> → 文件 ops/ops-<name>.html。新增页面（如 capture-price）时把名字加进来即可。
const OPS_PAGE_NAMES = new Set(["overview"]);
const OPS_HOME = "/ops/overview";
const MAX_AGE = 60 * 60 * 24 * 30;

const esc = (s) =>
  String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

function eq(a, b) {
  // 常量时间比较，避免通过响应时间猜 token
  if (a.length !== b.length) return false;
  let d = 0;
  for (let i = 0; i < a.length; i++) d |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return d === 0;
}

function whoIs(token, env, list = env.DASHBOARD_TOKENS) {
  if (!token) return null;
  let found = null;
  for (const pair of (list || "").split(",")) {
    const i = pair.indexOf(":");
    if (i > 0 && eq(pair.slice(i + 1).trim(), token)) found = pair.slice(0, i).trim();
  }
  return found;
}

function readCookie(request, name) {
  for (const part of (request.headers.get("Cookie") || "").split(";")) {
    const i = part.indexOf("=");
    if (i > 0 && part.slice(0, i).trim() === name) return decodeURIComponent(part.slice(i + 1).trim());
  }
  return null;
}

const setCookie = (token, name = COOKIE, path = "/") =>
  `${name}=${encodeURIComponent(token)}; Path=${path}; HttpOnly; Secure; SameSite=Lax; Max-Age=${MAX_AGE}`;
const clearCookie = (name = COOKIE, path = "/") => `${name}=; Path=${path}; HttpOnly; Secure; SameSite=Lax; Max-Age=0`;

// 只允许站内相对路径，防止被利用做跳转到别的网站
const safeNext = (n) => (typeof n === "string" && n.startsWith("/") && !n.startsWith("//") ? n : "/");

function redirect(location, cookie) {
  const h = new Headers({ Location: location, "Cache-Control": "no-store" });
  if (cookie) h.append("Set-Cookie", cookie);
  return new Response(null, { status: 303, headers: h });
}

function loginPage(message, next, status = 200, opts = {}) {
  const action = opts.action || "/login";
  const title = opts.title || "Access Required";
  const sub = opts.sub || "Enter your access token to continue";
  const html = `<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="robots" content="noindex"><title>${esc(title)}</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{display:flex;justify-content:center;align-items:center;min-height:100vh;background:#f0f2f5;font-family:'Segoe UI',Tahoma,Geneva,Verdana,sans-serif}
.box{background:#fff;padding:48px 40px;border-radius:20px;box-shadow:0 8px 30px rgba(0,0,0,.1);width:min(380px,92vw);text-align:center}
.lock{font-size:56px;margin-bottom:16px}h2{color:#2c3e50;font-size:1.6rem;margin-bottom:8px}
.sub{color:#6c757d;font-size:.95rem;margin-bottom:32px}
input{width:100%;padding:14px 16px;border:2px solid #e0e4e9;border-radius:10px;font-size:1rem;outline:none;margin-bottom:16px;color:#2c3e50}
input:focus{border-color:#2c3e50}
button{width:100%;padding:14px;background:#2c3e50;color:#fff;border:none;border-radius:10px;font-size:1rem;font-weight:600;cursor:pointer}
button:hover{background:#1e2b38}.err{color:#e74c3c;font-size:.9rem;margin-top:14px}
</style></head><body><form class="box" method="POST" action="${esc(action)}">
<div class="lock">🔒</div><h2>${esc(title)}</h2><p class="sub">${esc(sub)}</p>
<input type="password" name="token" placeholder="Access token" autocomplete="current-password" autofocus required>
<input type="hidden" name="next" value="${esc(next)}">
<button type="submit">Enter</button>${message ? `<p class="err">${esc(message)}</p>` : ""}
</form></body></html>`;
  return new Response(html, {
    status,
    headers: { "content-type": "text/html; charset=utf-8", "Cache-Control": "no-store", "X-Robots-Tag": "noindex" },
  });
}

// ── 读取私有 vcom 仓库的发电量 CSV（用 Secret GITHUB_TOKEN，只读 Contents）──────────────
// 需要：Secret GITHUB_TOKEN（fine-grained，只授权 vcom 仓库，Contents: Read-only）；变量 GITHUB_REPO 可选，默认 hanyu-jian/vcom
const SITES = ["Putnok", "Serenyfalva", "Sajokaza", "Szuhakálló 1", "Szuhakálló 2"];
const FIRST_YEAR = 2024;
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
const json = (obj, status = 200) =>
  new Response(JSON.stringify(obj), {
    status,
    headers: { "content-type": "application/json; charset=utf-8", "Cache-Control": "private, no-store", "X-Robots-Tag": "noindex" },
  });

const nextDay = (d) => new Date(Date.parse(d + "T00:00:00Z") + 86400000).toISOString().slice(0, 10);
const daysBetween = (a, b) => (Date.parse(b) - Date.parse(a)) / 86400000;

// 第一个时间戳 >= key 的行的起始位置。CSV 按时间升序，直接对文本做二分，不用把整个文件解析成数组
// （免费版 Worker 每次请求只有约 10ms CPU，解析几万行会超）。
function lowerBound(text, key, lo) {
  let hi = text.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    let ls = text.lastIndexOf("\n", mid - 1) + 1;
    if (ls < lo) ls = lo;
    if (text.substr(ls, 19) < key) {
      const nl = text.indexOf("\n", ls);
      lo = nl === -1 ? hi : nl + 1;
    } else {
      hi = ls;
    }
  }
  return lo;
}

async function loadFile(env, path) {
  const res = await fetch(`https://api.github.com/repos/${env.GITHUB_REPO || "hanyu-jian/vcom"}/contents/${path}`, {
    headers: {
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      Accept: "application/vnd.github.raw+json",
      "User-Agent": "energy-dashboard",
      "X-GitHub-Api-Version": "2022-11-28",
    },
    cf: { cacheEverything: true, cacheTtl: 600 }, // 数据一天才更新一次，边缘缓存 10 分钟
  });
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`GitHub ${res.status}`);
  let text = await res.text();
  if (text.charCodeAt(0) === 0xfeff) text = text.slice(1);
  return text;
}

// ── /production：理论发电量 + 预测发电量 ─────────────────────────────────────
// 两个目录的 15min / hourly 都是布达佩斯当地时间、区间起点标签，可以直接按时间戳对齐。
const PROD_RESOLUTIONS = ["15min", "hourly"];
const PROD_MAX_DAYS = { "15min": 100, hourly: 400 }; // 免费版 Worker CPU 很紧，15min 一次别查太久
const PROD_COLUMNS = ["theoretical_kwh", "da", "ida1", "ida2", "ida3", "id3_16_20", "id3_11_15"];

// 取出 [startKey, endKey) 的行，返回 Map<时间戳, 数值数组>；valueCols 是要取的列下标
function sliceRows(text, startKey, endKey, valueCols, into, slot, width) {
  const nl = text.indexOf("\n");
  const dataStart = nl + 1;
  const a = lowerBound(text, startKey, dataStart);
  const b = lowerBound(text, endKey, dataStart);
  if (b <= a) return;
  for (const line of text.slice(a, b).split("\n")) {
    if (!line) continue;
    const f = line.replace(/\r/g, "").split(",");
    let row = into.get(f[0]);
    if (!row) {
      row = new Array(width).fill(null);
      into.set(f[0], row);
    }
    valueCols.forEach((c, k) => {
      const v = f[c];
      row[slot + k] = v === undefined || v === "" ? null : Number(v);
    });
  }
}

async function handleProduction(url, env) {
  const q = url.searchParams;
  const site = q.get("site");
  const start = q.get("start");
  const end = q.get("end") || start;
  const resolution = q.get("resolution") || "15min";

  if (!SITES.includes(site)) return json({ error: `site must be one of ${SITES.join(" / ")}` }, 400);
  if (!PROD_RESOLUTIONS.includes(resolution)) return json({ error: "resolution must be 15min or hourly" }, 400);
  if (!DATE_RE.test(start || "") || !DATE_RE.test(end) || Number.isNaN(Date.parse(start)) || Number.isNaN(Date.parse(end)))
    return json({ error: "start / end must be YYYY-MM-DD" }, 400);
  if (end < start) return json({ error: "end must not be before start" }, 400);
  if (daysBetween(start, end) + 1 > PROD_MAX_DAYS[resolution])
    return json({ error: `${resolution} allows at most ${PROD_MAX_DAYS[resolution]} days per request` }, 400);

  const startKey = `${start} 00:00:00`;
  const endKey = `${nextDay(end)} 00:00:00`;
  const y0 = Math.max(FIRST_YEAR, Number(start.slice(0, 4)));
  const y1 = Number(end.slice(0, 4));
  const name = encodeURIComponent(site);

  const merged = new Map();
  let any = false;
  for (let y = y0; y <= y1; y++) {
    const [theo, fc] = await Promise.all([
      loadFile(env, `data/theoretical production/${y}/${resolution}/${name}.csv`),
      loadFile(env, `data/production forecast/${y}/${resolution}/${name}.csv`),
    ]);
    // theoretical 列：Date, POA, MWh, kWh → 取第 3 列；forecast 列：Date, I..N → 取第 1~6 列
    if (theo) { any = true; sliceRows(theo, startKey, endKey, [3], merged, 0, PROD_COLUMNS.length); }
    if (fc) { any = true; sliceRows(fc, startKey, endKey, [1, 2, 3, 4, 5, 6], merged, 1, PROD_COLUMNS.length); }
  }
  if (!any) return json({ error: "No data for this site / period" }, 404);

  const rows = [...merged.entries()].sort((a, b) => (a[0] < b[0] ? -1 : 1)).map(([t, v]) => [t, ...v]);
  return json({
    site,
    resolution,
    unit: "kWh per interval",
    timezone: "Europe/Budapest (local time, interval start)",
    columns: ["Date", ...PROD_COLUMNS],
    rows,
  });
}



// ── 运营数据（/ops/…）：独立的 token 和 Cookie，Cookie 只在 /ops 路径下发送 ──────────────
const OPS_LOGIN = { action: "/ops/login", title: "Operations Access", sub: "Enter your operations access token" };

async function handleOps(request, env, url, path) {
  if (!env.OPS_TOKENS) return new Response("Not found", { status: 404, headers: { "Cache-Control": "no-store" } });

  if (path === "/ops/logout") return redirect("/ops/login", clearCookie(OPS_COOKIE, "/ops"));

  if (path === "/ops/login") {
    if (request.method === "POST") {
      const form = await request.formData();
      const token = String(form.get("token") || "").trim();
      const next = safeNext(form.get("next"));
      const to = next.startsWith("/ops") ? next : OPS_HOME;
      return whoIs(token, env, env.OPS_TOKENS)
        ? redirect(to, setCookie(token, OPS_COOKIE, "/ops"))
        : loginPage("Invalid token. Please try again.", to, 401, OPS_LOGIN);
    }
    const next = safeNext(url.searchParams.get("next"));
    const to = next.startsWith("/ops") ? next : OPS_HOME;
    return whoIs(readCookie(request, OPS_COOKIE), env, env.OPS_TOKENS) ? redirect(to) : loginPage("", to, 200, OPS_LOGIN);
  }

  // 链接直达：/ops/overview?token=xxx
  const linkToken = url.searchParams.get("token");
  if (linkToken !== null) {
    url.searchParams.delete("token");
    const clean = url.pathname + (url.search || "");
    return whoIs(linkToken, env, env.OPS_TOKENS)
      ? redirect(clean, setCookie(linkToken, OPS_COOKIE, "/ops"))
      : loginPage("Invalid token. Please try again.", clean, 401, OPS_LOGIN);
  }

  if (!whoIs(readCookie(request, OPS_COOKIE), env, env.OPS_TOKENS)) {
    if ((request.headers.get("Accept") || "").includes("text/html")) {
      return redirect("/ops/login?next=" + encodeURIComponent(url.pathname + url.search));
    }
    return new Response("Unauthorized", { status: 401, headers: { "Cache-Control": "no-store" } });
  }

  // 数据接口：直接用服务端的 GITHUB_TOKEN 读私有 vcom 仓库，token 不会发给浏览器
  if (path === "/ops/api/production") {
    if (request.method !== "GET") return new Response("Method not allowed", { status: 405 });
    if (!env.GITHUB_TOKEN) return json({ error: "GITHUB_TOKEN is not configured" }, 500);
    try {
      return await handleProduction(url, env);
    } catch (e) {
      return json({ error: String(e.message || e) }, 502);
    }
  }

  const clean = path.replace(/\/+$/, "");
  if (clean === "/ops" || clean === "/ops/hungary") return redirect(OPS_HOME + url.search); // 旧地址
  const m = clean.match(/^\/ops\/([a-z-]+)$/);
  let file = null;
  if (clean === "/ops/ops-nav.js") file = "/ops/ops-nav.js";
  else if (m && OPS_PAGE_NAMES.has(m[1])) file = "/ops/ops-" + m[1];
  if (!file) return new Response("Not found", { status: 404, headers: { "Cache-Control": "no-store" } });
  let res = await env.ASSETS.fetch(new Request(new URL(file, url), request));
  if (res.status >= 300 && res.status < 400 && res.headers.get("Location")) {
    res = await env.ASSETS.fetch(new Request(new URL(res.headers.get("Location"), url), request));
  }
  const out = new Response(res.body, res);
  out.headers.set("Cache-Control", "private, no-store");
  out.headers.set("X-Robots-Tag", "noindex");
  return out;
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    // /ops 下的一切（含大小写、重复斜杠、编码变体）只走运营这套 token；普通 token 不能碰
    let norm = url.pathname;
    try { norm = decodeURIComponent(norm); } catch {}
    norm = norm.replace(/\/+/g, "/").toLowerCase();
    if (norm === "/ops" || norm.startsWith("/ops/")) {
      return handleOps(request, env, url, norm === "/ops" ? "/ops/" : norm);
    }

    if (!env.DASHBOARD_TOKENS) {
      return new Response("DASHBOARD_TOKENS 还没有配置，出于安全考虑网站暂时不对外开放。", { status: 500 });
    }

    if (url.pathname === "/logout") return redirect("/login", clearCookie());

    if (url.pathname === "/login") {
      if (request.method === "POST") {
        const form = await request.formData();
        const token = String(form.get("token") || "").trim();
        const next = safeNext(form.get("next"));
        return whoIs(token, env)
          ? redirect(next, setCookie(token))
          : loginPage("Invalid token. Please try again.", next, 401);
      }
      const next = safeNext(url.searchParams.get("next"));
      return whoIs(readCookie(request, COOKIE), env) ? redirect(next) : loginPage("", next);
    }

    // 链接直达：https://网址/任意页面?token=xxx  → 设置 Cookie 后跳到去掉 token 的同一页面
    const linkToken = url.searchParams.get("token");
    if (linkToken !== null) {
      url.searchParams.delete("token");
      const clean = url.pathname + (url.search || "");
      return whoIs(linkToken, env)
        ? redirect(clean, setCookie(linkToken))
        : loginPage("Invalid token. Please try again.", clean, 401);
    }

    if (!whoIs(readCookie(request, COOKIE), env)) {
      if ((request.headers.get("Accept") || "").includes("text/html")) {
        return redirect("/login?next=" + encodeURIComponent(url.pathname + url.search));
      }
      return new Response("Unauthorized", { status: 401, headers: { "Cache-Control": "no-store" } });
    }

    // 页面文件放在 html/ 里，但对外网址保持 /price-overview、/load.html 这样（不带 /html/）：
    // 在服务器内部把请求映射到 html/ 下的文件，浏览器地址栏不变。
    let assetReq = request;
    const page = url.pathname.match(/^\/(?:html\/)?([a-z-]+?)(?:\.html)?$/);
    if (url.pathname === "/nav.js") {
      assetReq = new Request(new URL("/html/nav.js", url), request);
    } else if (page && PAGES.has(page[1])) {
      if (url.pathname.startsWith("/html/")) return redirect("/" + page[1] + url.search); // 旧的带 /html/ 地址 → 去掉前缀
      assetReq = new Request(new URL("/html/" + page[1], url), request);
    }

    // 通过验证：交给静态资源（HTML/CSV/JS……）
    let res = await env.ASSETS.fetch(assetReq);
    // 静态资源层可能把 /html/x 再重定向一次，这里在内部跟随，避免把 /html/ 地址暴露给浏览器
    if (assetReq !== request && res.status >= 300 && res.status < 400 && res.headers.get("Location")) {
      res = await env.ASSETS.fetch(new Request(new URL(res.headers.get("Location"), url), request));
    }
    const out = new Response(res.body, res);
    out.headers.set("Cache-Control", "private, no-cache");
    out.headers.set("X-Robots-Tag", "noindex");
    return out;
  },
};
