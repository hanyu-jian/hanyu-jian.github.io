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
//   VCOM_API_KEY = 私有 vcom-api Worker 的 X-API-Key（API_KEYS 里的某一项），Secret
//   VCOM_API（Service binding，wrangler.jsonc 里已声明，指向 vcom-api）；没有绑定时退回用 VCOM_API_URL 变量
// 运营 token 只能打开 /ops/ 下的页面和接口；普通看板 token 打不开 /ops/，反过来运营 token 也打不开普通页面。
// OPS_TOKENS 没配置时 /ops/ 一律拒绝，不影响其它页面。

const PAGES = new Set([
  "capacity", "capture-price", "generation-overview", "generation-type", "imbalance", "balancing-afrr-capacity",
  "load", "price-intraday", "price-overview", "price-spread", "project",
]);
const COOKIE = "dash_token";
const OPS_COOKIE = "ops_token";
const OPS_PAGES = { "/ops/hungary": "/ops/hungary-operations" }; // 对外地址 → ops/ 目录里的文件
const OPS_API_PARAMS = ["site", "start", "end", "resolution"];
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
      const to = next.startsWith("/ops") ? next : "/ops/hungary";
      return whoIs(token, env, env.OPS_TOKENS)
        ? redirect(to, setCookie(token, OPS_COOKIE, "/ops"))
        : loginPage("Invalid token. Please try again.", to, 401, OPS_LOGIN);
    }
    const next = safeNext(url.searchParams.get("next"));
    const to = next.startsWith("/ops") ? next : "/ops/hungary";
    return whoIs(readCookie(request, OPS_COOKIE), env, env.OPS_TOKENS) ? redirect(to) : loginPage("", to, 200, OPS_LOGIN);
  }

  // 链接直达：/ops/hungary?token=xxx
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

  // 数据接口：只放行白名单参数，用服务端保存的 key 去调私有的 vcom-api
  if (path === "/ops/api/production") {
    if (request.method !== "GET") return new Response("Method not allowed", { status: 405 });
    if (!env.VCOM_API_KEY || !(env.VCOM_API || env.VCOM_API_URL)) {
      return new Response(JSON.stringify({ error: "VCOM_API / VCOM_API_KEY 还没有配置" }), {
        status: 500, headers: { "content-type": "application/json", "Cache-Control": "no-store" },
      });
    }
    const q = new URLSearchParams();
    for (const k of OPS_API_PARAMS) if (url.searchParams.has(k)) q.set(k, url.searchParams.get(k));
    const init = { headers: { "X-API-Key": env.VCOM_API_KEY } };
    let up;
    try {
      up = env.VCOM_API
        ? await env.VCOM_API.fetch(`https://vcom-api/production?${q}`, init)
        : await fetch(`${env.VCOM_API_URL.replace(/\/+$/, "")}/production?${q}`, init);
    } catch (e) {
      return new Response(JSON.stringify({ error: "无法连接 vcom-api: " + String(e.message || e) }), {
        status: 502, headers: { "content-type": "application/json", "Cache-Control": "no-store" },
      });
    }
    const out = new Response(up.body, { status: up.status });
    out.headers.set("content-type", "application/json; charset=utf-8");
    out.headers.set("Cache-Control", "private, no-store");
    out.headers.set("X-Robots-Tag", "noindex");
    return out;
  }

  const file = OPS_PAGES[path.replace(/\/+$/, "")];
  if (!file) return new Response("Not found", { status: 404, headers: { "Cache-Control": "no-store" } });
  const res = await env.ASSETS.fetch(new Request(new URL(file, url), request));
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
    if (norm === "/ops" || norm.startsWith("/ops/") || norm.includes("hungary-operations")) {
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
