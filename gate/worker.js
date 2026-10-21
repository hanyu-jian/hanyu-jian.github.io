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

const COOKIE = "dash_token";
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

function whoIs(token, env) {
  if (!token) return null;
  let found = null;
  for (const pair of (env.DASHBOARD_TOKENS || "").split(",")) {
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

const setCookie = (token) =>
  `${COOKIE}=${encodeURIComponent(token)}; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=${MAX_AGE}`;
const clearCookie = () => `${COOKIE}=; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=0`;

// 只允许站内相对路径，防止被利用做跳转到别的网站
const safeNext = (n) => (typeof n === "string" && n.startsWith("/") && !n.startsWith("//") ? n : "/");

function redirect(location, cookie) {
  const h = new Headers({ Location: location, "Cache-Control": "no-store" });
  if (cookie) h.append("Set-Cookie", cookie);
  return new Response(null, { status: 303, headers: h });
}

function loginPage(message, next, status = 200) {
  const html = `<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="robots" content="noindex"><title>Access Required</title>
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
</style></head><body><form class="box" method="POST" action="/login">
<div class="lock">🔒</div><h2>Access Required</h2><p class="sub">Enter your access token to continue</p>
<input type="password" name="token" placeholder="Access token" autocomplete="current-password" autofocus required>
<input type="hidden" name="next" value="${esc(next)}">
<button type="submit">Enter</button>${message ? `<p class="err">${esc(message)}</p>` : ""}
</form></body></html>`;
  return new Response(html, {
    status,
    headers: { "content-type": "text/html; charset=utf-8", "Cache-Control": "no-store", "X-Robots-Tag": "noindex" },
  });
}

export default {
  async fetch(request, env) {
    if (!env.DASHBOARD_TOKENS) {
      return new Response("DASHBOARD_TOKENS 还没有配置，出于安全考虑网站暂时不对外开放。", { status: 500 });
    }
    const url = new URL(request.url);

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

    // 页面已搬到 /html/：旧地址（书签、已发出的链接）自动跳转到新位置
    const legacy = url.pathname.match(/^\/([a-z-]+\.html)$/);
    if (legacy && legacy[1] !== "index.html") return redirect("/html/" + legacy[1] + url.search);
    if (url.pathname === "/nav.js") return redirect("/html/nav.js");

    // 通过验证：交给静态资源（HTML/CSV/JS……）
    const res = await env.ASSETS.fetch(request);
    const out = new Response(res.body, res);
    out.headers.set("Cache-Control", "private, no-cache");
    out.headers.set("X-Robots-Tag", "noindex");
    return out;
  },
};
