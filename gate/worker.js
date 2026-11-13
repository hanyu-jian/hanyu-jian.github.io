// Cloudflare Worker：静态网站前面的「门卫」。
// 普通看板由 Cloudflare Access（公司邮箱验证码）把关，Worker 本身没有登录页。
// 前提：Worker 的 workers.dev 地址（Production 和 Preview）必须关闭，只通过绑定了 Access 的自定义域名访问；
// 否则任何人可以绕过 Access 直接打开网站。
//
// 可选的额外保险（普通变量，Worker → Settings → Variables and Secrets）：
//   ACCESS_TEAM_DOMAIN = 团队域名，形如 xxx.cloudflareaccess.com
//   ACCESS_AUD         = Access 应用的 Application Audience (AUD) Tag
// 两个都配置后，Worker 会校验 Access 签发的 JWT（签名、有效期、iss、aud），绕过 Access 的请求一律 401；不配置则不校验。
//
// 运营数据（/ops/…，匈牙利五个场站的发电量）使用「完全独立」的另一套 token：
//   OPS_TOKENS = alice:optoken1,bob:optoken2   Secret；名字:token，逗号分隔。进 /ops/ 要先通过 Access 邮箱登录，再输入运营 token
//   GITHUB_TOKEN = 只读（Contents: Read-only）、只授权 vcom 仓库的 fine-grained token，Secret；用来读私有 vcom 仓库的发电量 CSV
//   GITHUB_REPO  = 可选变量，默认 hanyu-jian/vcom
// 运营 token 只能打开 /ops/ 下的页面和接口；通过 Access 登录的同事默认打不开 /ops/，必须另有运营 token。
// OPS_TOKENS 没配置时 /ops/ 一律拒绝，不影响其它页面。

const PAGES = new Set([
  "capacity", "capture-price", "generation-overview", "generation-type", "imbalance", "balancing-afrr-capacity",
  "load", "price-intraday", "price-overview", "price-spread", "project",
]);
const OPS_COOKIE = "ops_token";
// 运营页面：对外地址 /ops/<name> → 文件 ops/ops-<name>.html。新增页面（如 capture-price）时把名字加进来即可。
const OPS_PAGE_NAMES = new Set(["overview", "capture-price", "imbalance"]);
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

function whoIs(token, env, list) {
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

const setCookie = (token, name, path = "/") =>
  `${name}=${encodeURIComponent(token)}; Path=${path}; HttpOnly; Secure; SameSite=Lax; Max-Age=${MAX_AGE}`;
const clearCookie = (name, path = "/") => `${name}=; Path=${path}; HttpOnly; Secure; SameSite=Lax; Max-Age=0`;

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

async function loadFile(env, path, repo = env.GITHUB_REPO || "hanyu-jian/vcom", token = env.GITHUB_TOKEN) {
  const res = await fetch(`https://api.github.com/repos/${repo}/contents/${path}`, {
    headers: {
      Authorization: `Bearer ${token}`,
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



// ── /ust：理论发电量 + 超短期（UST）预测发电量 ───────────────────────────────────
// UST 数据在 R2（绑定名 UST，桶里 ust/view/<YYYY-MM>/<场站>.csv），是每个 15 分钟时段存了 16 档提前量的宽表。
// 用户选提前量 H（horizon，分钟），这里对每个时段取「提前量 >= H 里最小的那一档」的 kWh，那一档缺就退到更大的。
// 时间标签和 /production 一样：布达佩斯当地时间、区间起点。hourly 由 4 个 15 分钟相加（4 个齐全才算），起点标签。
const UST_HORIZONS = [15, 30, 45, 60, 75, 90, 105, 120, 135, 150, 165, 180, 195, 210, 225, 240];
const UST_DEFAULT_H = 60;
const UST_MAX_DAYS = 62; // 宽表一行 49 列，免费版 Worker CPU 很紧，单次别查太久
// da_kwh：原来的 DA（日前报量）预测，来自 vcom 仓库 data/production forecast 的第 I 列，页面上画成灰色虚线。
const UST_COLUMNS = ["theoretical_kwh", "ust_kwh", "ust_horizon_min", "da_kwh"];

// 三个来源的宽表格式完全一样，页面上合成一条橙线（不区分来源）：
//   live     = ust/          实时影子运行（2026-10-29 起）；实测输入有时陈旧
//   replay   = ust-replay/   乙方事后回放（2026-10-25 起）
//   hindcast = ust-hindcast/ 乙方回测（2026-01-01 ~ 2026-10-25）
// 同一个时段多个来源都有值时，按下面的顺序取第一个（live 优先：它是真实运行时拿得到的值），缺了才用后面的。
// 月份范围只用来少读 R2（范围外的月份肯定没有该来源的文件）。
const UST_SOURCES = {
  live: { prefix: "ust", from: "2026-10", to: "9999-12" },
  replay: { prefix: "ust-replay", from: "2026-10", to: "9999-12" },
  hindcast: { prefix: "ust-hindcast", from: "2026-01", to: "2026-10" },
};
const UST_SOURCE_ORDER = ["live", "replay", "hindcast"];

async function loadUstView(env, month, site, prefix = "ust") {
  const obj = await env.UST.get(`${prefix}/view/${month}/${site}.csv`);
  if (!obj) return null;
  let text = await obj.text();
  if (text.charCodeAt(0) === 0xfeff) text = text.slice(1);
  return text;
}

// 宽表 -> into: Map<时间戳, [theoretical, ust_kwh, 实际用到的提前量]>，只填 ust 两个槽
// fillOnly：该时段已经有值就不覆盖（多来源合并时，靠前的来源优先）
function sliceUst(text, startKey, endKey, minH, into, fillOnly = false) {
  const nl = text.indexOf("\n");
  const header = text.slice(0, nl).replace(/\r/g, "").split(",");
  const cand = UST_HORIZONS.filter((h) => h >= minH)
    .map((h) => [h, header.indexOf(`kWh h${h}`)])
    .filter(([, i]) => i > 0);
  const a = lowerBound(text, startKey, nl + 1);
  const b = lowerBound(text, endKey, nl + 1);
  if (b <= a) return;
  for (const line of text.slice(a, b).split("\n")) {
    if (!line) continue;
    const f = line.replace(/\r/g, "").split(",");
    let hit = null;
    for (const [h, i] of cand) {
      if (f[i] !== undefined && f[i] !== "") { hit = [Number(f[i]), h]; break; }
    }
    let row = into.get(f[0]);
    if (!row) { row = new Array(UST_COLUMNS.length).fill(null); into.set(f[0], row); }
    if (hit && !(fillOnly && row[1] !== null)) { row[1] = hit[0]; row[2] = hit[1]; }
  }
}

function monthsBetween(start, end) {
  const out = [];
  let y = Number(start.slice(0, 4)), m = Number(start.slice(5, 7));
  const y1 = Number(end.slice(0, 4)), m1 = Number(end.slice(5, 7));
  while (y < y1 || (y === y1 && m <= m1)) {
    out.push(`${y}-${String(m).padStart(2, "0")}`);
    if (++m > 12) { m = 1; y++; }
  }
  return out;
}

async function handleUst(url, env) {
  const q = url.searchParams;
  const site = q.get("site");
  const start = q.get("start");
  const end = q.get("end") || start;
  const resolution = q.get("resolution") || "15min";
  const horizon = q.get("horizon") === null ? UST_DEFAULT_H : Number(q.get("horizon"));
  const source = q.get("source") || "all";   // all = live > replay > hindcast 合并；也可单独指定一个来源（排查用）

  if (source !== "all" && !(source in UST_SOURCES)) return json({ error: `source must be all / ${Object.keys(UST_SOURCES).join(" / ")}` }, 400);
  if (!SITES.includes(site)) return json({ error: `site must be one of ${SITES.join(" / ")}` }, 400);
  if (!PROD_RESOLUTIONS.includes(resolution)) return json({ error: "resolution must be 15min or hourly" }, 400);
  if (!UST_HORIZONS.includes(horizon)) return json({ error: `horizon must be one of ${UST_HORIZONS.join(" / ")}` }, 400);
  if (!DATE_RE.test(start || "") || !DATE_RE.test(end) || Number.isNaN(Date.parse(start)) || Number.isNaN(Date.parse(end)))
    return json({ error: "start / end must be YYYY-MM-DD" }, 400);
  if (end < start) return json({ error: "end must not be before start" }, 400);
  if (daysBetween(start, end) + 1 > UST_MAX_DAYS) return json({ error: `at most ${UST_MAX_DAYS} days per request` }, 400);

  const startKey = `${start} 00:00:00`;
  const endKey = `${nextDay(end)} 00:00:00`;
  const y0 = Math.max(FIRST_YEAR, Number(start.slice(0, 4)));
  const y1 = Number(end.slice(0, 4));
  const name = encodeURIComponent(site);

  const merged = new Map();
  let any = false;
  // 理论发电量 + DA 预测：和 /production 一样来自 vcom 仓库（15min 或 hourly 文件）
  for (let y = y0; y <= y1; y++) {
    const [theo, fc] = await Promise.all([
      loadFile(env, `data/theoretical production/${y}/${resolution}/${name}.csv`),
      loadFile(env, `data/production forecast/${y}/${resolution}/${name}.csv`),
    ]);
    // theoretical 列：Date, POA, MWh, kWh → 取第 3 列；production forecast 列：Date, I..N → 只取第 1 列（I = DA）
    if (theo) { any = true; sliceRows(theo, startKey, endKey, [3], merged, 0, UST_COLUMNS.length); }
    if (fc) { any = true; sliceRows(fc, startKey, endKey, [1], merged, 3, UST_COLUMNS.length); }
  }
  // 超短期：只有 15min 视图；hourly 在这里由 4 个 15min 相加
  const ust15 = new Map();
  const sources = source === "all" ? UST_SOURCE_ORDER : [source];
  for (const month of monthsBetween(start, end)) {
    for (const src of sources) {
      const cfg = UST_SOURCES[src];
      if (month < cfg.from || month > cfg.to) continue;
      const text = await loadUstView(env, month, site, cfg.prefix);
      if (text) sliceUst(text, startKey, endKey, horizon, ust15, true);
    }
  }
  if (resolution === "15min") {
    for (const [t, v] of ust15) {
      let row = merged.get(t);
      if (!row) { row = new Array(UST_COLUMNS.length).fill(null); merged.set(t, row); }
      row[1] = v[1]; row[2] = v[2];
    }
  } else {
    const hours = new Map(); // 小时起点 -> [和, 个数, 最大提前量]
    for (const [t, v] of ust15) {
      if (v[1] == null) continue;
      const k = t.slice(0, 13) + ":00:00";
      const h = hours.get(k) || [0, 0, 0];
      h[0] += v[1]; h[1] += 1; h[2] = Math.max(h[2], v[2]);
      hours.set(k, h);
    }
    for (const [k, h] of hours) {
      if (h[1] !== 4) continue;
      let row = merged.get(k);
      if (!row) { row = new Array(UST_COLUMNS.length).fill(null); merged.set(k, row); }
      row[1] = Math.round(h[0] * 1000) / 1000; row[2] = h[2];
    }
  }
  if (!any && ust15.size === 0) return json({ error: "No data for this site / period" }, 404);

  const rows = [...merged.entries()].sort((a, b) => (a[0] < b[0] ? -1 : 1)).map(([t, v]) => [t, ...v]);
  return json({
    site,
    resolution,
    horizon_min: horizon,
    source,
    unit: "kWh per interval",
    timezone: "Europe/Budapest (local time, interval start)",
    columns: ["Date", ...UST_COLUMNS],
    rows,
  });
}


// ── /ust-range：一段日期的理论发电量 + DA + 16 档提前量的 UST 预测（给「滑块 + 热力图」用）──────────────
// 返回 columns = ["Date","theoretical_kwh","da_kwh","h15",...,"h240"]，kWh per interval，布达佩斯当地时间、区间起点。
// 每个 (时段, 提前量) 单元格独立取值：live > replay > hindcast，缺了才用后面的来源；不做「退到更大提前量」的回退，
// 因为热力图每一行就是一个确定的提前量。页面拖滑块时只在浏览器里换列，不再请求。
// hourly：理论发电量 / DA 取 hourly 文件；16 档由 15min 相加（4 个齐全才算），起点标签。
const UST_RANGE_MAX_DAYS = 62;

function ustRangeFill(text, startKey, endKey, into) {
  const nl = text.indexOf("\n");
  const header = text.slice(0, nl).replace(/\r/g, "").split(",");
  const cols = UST_HORIZONS.map((h) => header.indexOf(`kWh h${h}`));
  const a = lowerBound(text, startKey, nl + 1);
  const b = lowerBound(text, endKey, nl + 1);
  if (b <= a) return;
  for (const line of text.slice(a, b).split("\n")) {
    if (!line) continue;
    const f = line.replace(/\r/g, "").split(",");
    let row = into.get(f[0]);
    if (!row) { row = new Array(2 + UST_HORIZONS.length).fill(null); into.set(f[0], row); }
    cols.forEach((i, k) => {
      if (i > 0 && row[2 + k] === null && f[i] !== undefined && f[i] !== "") row[2 + k] = Number(f[i]);
    });
  }
}

async function handleUstRange(url, env) {
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
  if (daysBetween(start, end) + 1 > UST_RANGE_MAX_DAYS) return json({ error: `at most ${UST_RANGE_MAX_DAYS} days per request` }, 400);

  const startKey = `${start} 00:00:00`;
  const endKey = `${nextDay(end)} 00:00:00`;
  const y0 = Math.max(FIRST_YEAR, Number(start.slice(0, 4)));
  const y1 = Number(end.slice(0, 4));
  const name = encodeURIComponent(site);
  const width = 2 + UST_HORIZONS.length;

  const merged = new Map();
  for (let y = y0; y <= y1; y++) {
    const [theo, fc] = await Promise.all([
      loadFile(env, `data/theoretical production/${y}/${resolution}/${name}.csv`),
      loadFile(env, `data/production forecast/${y}/${resolution}/${name}.csv`),
    ]);
    if (theo) sliceRows(theo, startKey, endKey, [3], merged, 0, width);
    if (fc) sliceRows(fc, startKey, endKey, [1], merged, 1, width);
  }
  const ust15 = new Map();
  for (const month of monthsBetween(start, end)) {
    for (const src of UST_SOURCE_ORDER) {
      const cfg = UST_SOURCES[src];
      if (month < cfg.from || month > cfg.to) continue;
      const text = await loadUstView(env, month, site, cfg.prefix);
      if (text) ustRangeFill(text, startKey, endKey, ust15);
    }
  }
  if (resolution === "15min") {
    for (const [t, v] of ust15) {
      let row = merged.get(t);
      if (!row) { row = new Array(width).fill(null); merged.set(t, row); }
      for (let k = 2; k < width; k++) row[k] = v[k];
    }
  } else {
    const hours = new Map(); // 小时起点 -> {sum[16], cnt[16]}
    for (const [t, v] of ust15) {
      const k = t.slice(0, 13) + ":00:00";
      let o = hours.get(k);
      if (!o) { o = { sum: new Array(UST_HORIZONS.length).fill(0), cnt: new Array(UST_HORIZONS.length).fill(0) }; hours.set(k, o); }
      for (let j = 0; j < UST_HORIZONS.length; j++) if (v[2 + j] !== null) { o.sum[j] += v[2 + j]; o.cnt[j]++; }
    }
    for (const [k, o] of hours) {
      let row = merged.get(k);
      if (!row) { row = new Array(width).fill(null); merged.set(k, row); }
      for (let j = 0; j < UST_HORIZONS.length; j++) if (o.cnt[j] === 4) row[2 + j] = o.sum[j];
    }
  }
  if (merged.size === 0) return json({ error: "No data for this site / period" }, 404);
  const r1 = (v) => (v === null ? null : Math.round(v * 10) / 10);
  const rows = [...merged.entries()].sort((a, b) => (a[0] < b[0] ? -1 : 1)).map(([t, v]) => [t, ...v.map(r1)]);
  return json({
    site,
    start,
    end,
    resolution,
    unit: "kWh per interval",
    timezone: "Europe/Budapest (local time, interval start)",
    columns: ["Date", "theoretical_kwh", "da_kwh", ...UST_HORIZONS.map((h) => `h${h}`)],
    rows,
  });
}

// ── /imbalance：逐 15 分钟的结算输入（DA 预测、理论发电量、提前 L 分钟的超短期预测、三个价格）────────────────
// 结算本身（DA 报量百分比、剩余部分在 IDC 平仓还是留在不平衡里）在页面里算，这样改参数不用再请求；这里只负责把数据对齐：
//   H = DA 预测（vcom production forecast 的 I 列，kWh）   AH = 理论发电量（kWh）
//   F = 提前 L 分钟的超短期预测（提前量 >= L 里最小的一档，没有则为空；live > replay > hindcast 逐单元格取）
//   P = 日前价，AA = ID3 指数价，PI = 不平衡价（单一价格），EUR/MWh；缺失为空。只返回 AH 和 H 都有值的时段。
// 价格来自 hu-market-data 仓库的 HU_<年>.csv（build_hu_combined.py 每天整合的 15 分钟表，福林已换算成欧元），列：
//   "DA Price (EUR/MWh)"、"ID3 Price (EUR/MWh)"、"Imbalance Price (EUR/MWh)"。读它要用 GITHUB_TOKEN 对该仓库有读权限（env.PRICE_REPO 可改仓库）。
// 一次最多 62 天，列式 JSON（每个字段一个数组）。
const IMB_MAX_DAYS = 62;
const IMB_PRICE_COLS = ["DA Price (EUR/MWh)", "ID3 Price (EUR/MWh)", "Imbalance Price (EUR/MWh)"];

// 价格表是「YYYY-MM-DD HH:MM」（没有秒），按字符串二分查找后只解析需要的那一段；夏令时回拨日重复的小时，后出现的覆盖先出现的（和理论发电量 / 预测文件的 96 行一致）。
function sliceImbalancePrices(text, startKey, endKey, into) {
  const nl = text.indexOf("\n");
  const header = text.slice(0, nl).replace(/\r/g, "").split(",");
  const cols = IMB_PRICE_COLS.map((c) => header.indexOf(c));
  if (cols.some((i) => i < 0)) throw new Error(`price table is missing a column (${IMB_PRICE_COLS.filter((c, k) => cols[k] < 0).join(", ")})`);
  const a = lowerBound(text, startKey.slice(0, 16), nl + 1);
  const b = lowerBound(text, endKey.slice(0, 16), nl + 1);
  if (b <= a) return;
  for (const line of text.slice(a, b).split("\n")) {
    if (!line) continue;
    const f = line.replace(/\r/g, "").split(",");
    into.set(f[0].slice(0, 16) + ":00", cols.map((i) => (f[i] === undefined || f[i] === "" ? null : Number(f[i]))));
  }
}

async function handleImbalance(url, env) {
  const q = url.searchParams;
  const site = q.get("site");
  const start = q.get("start");
  const end = q.get("end") || start;
  const lead = q.get("lead") === null ? UST_DEFAULT_H : Number(q.get("lead"));
  if (!SITES.includes(site)) return json({ error: `site must be one of ${SITES.join(" / ")}` }, 400);
  if (!UST_HORIZONS.includes(lead)) return json({ error: `lead must be one of ${UST_HORIZONS.join(" / ")}` }, 400);
  if (!DATE_RE.test(start || "") || !DATE_RE.test(end) || Number.isNaN(Date.parse(start)) || Number.isNaN(Date.parse(end)))
    return json({ error: "start / end must be YYYY-MM-DD" }, 400);
  if (end < start) return json({ error: "end must not be before start" }, 400);
  if (daysBetween(start, end) + 1 > IMB_MAX_DAYS) return json({ error: `at most ${IMB_MAX_DAYS} days per request` }, 400);

  const startKey = `${start} 00:00:00`;
  const endKey = `${nextDay(end)} 00:00:00`;
  const y0 = Math.max(FIRST_YEAR, Number(start.slice(0, 4)));
  const y1 = Number(end.slice(0, 4));
  const name = encodeURIComponent(site);
  const nl = UST_HORIZONS.length, width = 2 + nl;

  const merged = new Map();            // 时段 -> [理论, DA, h15..h240]
  for (let y = y0; y <= y1; y++) {
    const [theo, fc] = await Promise.all([
      loadFile(env, `data/theoretical production/${y}/15min/${name}.csv`),
      loadFile(env, `data/production forecast/${y}/15min/${name}.csv`),
    ]);
    if (theo) sliceRows(theo, startKey, endKey, [3], merged, 0, width);
    if (fc) sliceRows(fc, startKey, endKey, [1], merged, 1, width);
  }
  for (const month of monthsBetween(start, end)) {
    for (const src of UST_SOURCE_ORDER) {
      const cfg = UST_SOURCES[src];
      if (month < cfg.from || month > cfg.to) continue;
      const text = await loadUstView(env, month, site, cfg.prefix);
      if (text) ustRangeFill(text, startKey, endKey, merged);
    }
  }
  const prices = new Map();            // 时段 -> [P, AA, PI]
  const priceRepo = env.PRICE_REPO || "hanyu-jian/hu-market-data", priceToken = env.PRICE_TOKEN || env.GITHUB_TOKEN;
  for (let y = y0; y <= y1; y++) {
    let ptext;
    try {
      ptext = await loadFile(env, `HU_${y}.csv`, priceRepo, priceToken);
    } catch (e) {
      return json({ error: `Price table HU_${y}.csv (${priceRepo}) could not be read: ${e.message}. The token needs read access to that repository (or set the PRICE_TOKEN secret).` }, 502);
    }
    // GitHub 对「token 没有权限的私有仓库」也返回 404，所以 404 在这里不能当成「没有数据」，否则整页静默地没有价格
    if (ptext === null) return json({ error: `Price table HU_${y}.csv was not found in ${priceRepo} (GitHub answers 404 when the token has no access to a private repository). Give the token read access to it or set the PRICE_TOKEN secret.` }, 502);
    sliceImbalancePrices(ptext, startKey, endKey, prices);
  }

  const out = { t: [], H: [], AH: [], F: [], P: [], AA: [], PI: [] };
  const li = UST_HORIZONS.indexOf(lead);
  const r3 = (x) => (x === null ? null : Math.round(x * 1000) / 1000);
  for (const [t, v] of [...merged.entries()].sort((a, b) => (a[0] < b[0] ? -1 : 1))) {
    if (v[0] === null || v[1] === null) continue;            // 只返回理论发电量和 DA 都有值的时段
    let F = null;                                             // 提前量 >= L 里最小的一档
    for (let j = li; j < nl; j++) if (v[2 + j] !== null) { F = v[2 + j]; break; }
    const pr = prices.get(t) || [null, null, null];
    out.t.push(t.slice(0, 16)); out.AH.push(r3(v[0])); out.H.push(r3(v[1])); out.F.push(r3(F));
    out.P.push(pr[0]); out.AA.push(pr[1]); out.PI.push(pr[2] === null ? null : r3(pr[2]));
  }
  return json({ site, start, end, lead, unit: "kWh per 15 min (H, AH, F); EUR/MWh (P, AA, PI); t = Budapest local time, interval start", ...out });
}

// ── /theoretical：整年的 15 分钟理论发电量 CSV（原样转发，不解析，省 CPU）──────────────
// ── /meter：表计读数（vcom 仓库 data/meter/tokaj/<年>/15min|hourly/<场站>.csv）────────────
// 和 /production 一样是布达佩斯当地时间、区间起点标签，Overview 页用它叠一条「实测」线。
async function handleMeter(url, env) {
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
    const text = await loadFile(env, `data/meter/tokaj/${y}/${resolution}/${name}.csv`);
    // 列：Date, Meter [MWh], Meter [kWh] → 取第 3 列
    if (text) { any = true; sliceRows(text, startKey, endKey, [2], merged, 0, 1); }
  }
  if (!any) return json({ error: "No meter data for this site / period" }, 404);
  const rows = [...merged.entries()].filter(([, v]) => v[0] != null).sort((a, b) => (a[0] < b[0] ? -1 : 1)).map(([t, v]) => [t, v[0]]);
  return json({
    site,
    resolution,
    unit: "kWh per interval",
    timezone: "Europe/Budapest (local time, interval start)",
    columns: ["Date", "metered_kwh"],
    rows,
  });
}

// Capture Price 页面用：vcom 仓库整年的 15 分钟表计 CSV 原文（列：Date, Meter [MWh], Meter [kWh]），和 /theoretical 一样不解析、直接转发。
async function handleMetered(url, env) {
  const site = url.searchParams.get("site");
  const year = url.searchParams.get("year") || "";
  if (!SITES.includes(site)) return json({ error: `site must be one of ${SITES.join(" / ")}` }, 400);
  if (!/^\d{4}$/.test(year) || Number(year) < FIRST_YEAR || Number(year) > FIRST_YEAR + 20)
    return json({ error: `year must be ${FIRST_YEAR} or later` }, 400);
  const text = await loadFile(env, `data/meter/tokaj/${year}/15min/${encodeURIComponent(site)}.csv`);
  if (text === null) return json({ error: "No meter data for this site / year" }, 404);
  return new Response(text, {
    headers: { "content-type": "text/csv; charset=utf-8", "Cache-Control": "private, no-store", "X-Robots-Tag": "noindex" },
  });
}

// Capture Price 页面需要 2024 年至今的全部 15 分钟数据，/production 单次最多 100 天，所以另开这个接口。
// 返回 vcom 仓库里 data/theoretical production/<年>/15min/<场站>.csv 的原文（列：Date, POA, MWh, kWh），边缘会自动压缩。
async function handleTheoretical(url, env) {
  const site = url.searchParams.get("site");
  const year = url.searchParams.get("year") || "";
  if (!SITES.includes(site)) return json({ error: `site must be one of ${SITES.join(" / ")}` }, 400);
  if (!/^\d{4}$/.test(year) || Number(year) < FIRST_YEAR || Number(year) > FIRST_YEAR + 20)
    return json({ error: `year must be ${FIRST_YEAR} or later` }, 400);
  const text = await loadFile(env, `data/theoretical production/${year}/15min/${encodeURIComponent(site)}.csv`);
  if (text === null) return json({ error: "No data for this site / year" }, 404);
  return new Response(text, {
    headers: { "content-type": "text/csv; charset=utf-8", "Cache-Control": "private, no-store", "X-Robots-Tag": "noindex" },
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

  // 结算（DA + IDC 滚动）：按天 × 16 档提前量
  if (path === "/ops/api/imbalance") {
    if (request.method !== "GET") return new Response("Method not allowed", { status: 405 });
    if (!env.GITHUB_TOKEN) return json({ error: "GITHUB_TOKEN is not configured" }, 500);
    if (!env.UST) return json({ error: "R2 binding UST is not configured" }, 500);
    try {
      return await handleImbalance(url, env);
    } catch (e) {
      return json({ error: String(e.message || e) }, 502);
    }
  }

  // 一段日期：理论发电量 + DA + 16 档提前量（热力图 / 滑块）
  if (path === "/ops/api/ust-range") {
    if (request.method !== "GET") return new Response("Method not allowed", { status: 405 });
    if (!env.GITHUB_TOKEN) return json({ error: "GITHUB_TOKEN is not configured" }, 500);
    if (!env.UST) return json({ error: "R2 binding UST is not configured" }, 500);
    try {
      return await handleUstRange(url, env);
    } catch (e) {
      return json({ error: String(e.message || e) }, 502);
    }
  }

  // 超短期预测：理论发电量来自 vcom 仓库（GITHUB_TOKEN），UST 视图来自 R2（绑定 UST）
  if (path === "/ops/api/ust") {
    if (request.method !== "GET") return new Response("Method not allowed", { status: 405 });
    if (!env.GITHUB_TOKEN) return json({ error: "GITHUB_TOKEN is not configured" }, 500);
    if (!env.UST) return json({ error: "R2 binding UST is not configured" }, 500);
    try {
      return await handleUst(url, env);
    } catch (e) {
      return json({ error: String(e.message || e) }, 502);
    }
  }

  if (path === "/ops/api/meter" || path === "/ops/api/metered") {
    if (request.method !== "GET") return new Response("Method not allowed", { status: 405 });
    if (!env.GITHUB_TOKEN) return json({ error: "GITHUB_TOKEN is not configured" }, 500);
    try {
      return await (path === "/ops/api/meter" ? handleMeter(url, env) : handleMetered(url, env));
    } catch (e) {
      return json({ error: String(e.message || e) }, 502);
    }
  }

  if (path === "/ops/api/theoretical") {
    if (request.method !== "GET") return new Response("Method not allowed", { status: 405 });
    if (!env.GITHUB_TOKEN) return json({ error: "GITHUB_TOKEN is not configured" }, 500);
    try {
      return await handleTheoretical(url, env);
    } catch (e) {
      return json({ error: String(e.message || e) }, 502);
    }
  }

  // 日前价（15 分钟，各国一张宽表）：运营 Cookie 只在 /ops 下发送，页面拿不到 /data/ 下的文件，所以在这里转发
  if (path === "/ops/api/price") {
    if (request.method !== "GET") return new Response("Method not allowed", { status: 405 });
    const res = await env.ASSETS.fetch(new Request(new URL("/data/entsoe/15min/price.csv", url)));
    if (!res.ok) return json({ error: "Price data unavailable" }, 502);
    return new Response(res.body, {
      headers: { "content-type": "text/csv; charset=utf-8", "Cache-Control": "private, no-store", "X-Robots-Tag": "noindex" },
    });
  }

  const clean = path.replace(/\/+$/, "");
  if (clean === "/ops" || clean === "/ops/hungary") return redirect(OPS_HOME + url.search); // 旧地址
  const m = clean.match(/^\/ops\/([a-z-]+)$/);
  let file = null;
  if (clean === "/ops/nav.js") file = "/html/nav.js"; // 和主看板共用同一个导航脚本
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

// ── Cloudflare Access：校验请求头 Cf-Access-Jwt-Assertion（RS256）──────────────────
// 正常情况下请求已经被 Access 拦在前面；这里再验一次签名、有效期、iss 和 aud，
// 这样即使有人绕过 Access 直接打到 Worker（例如 workers.dev 预览地址），也进不来。
const teamHost = (env) => String(env.ACCESS_TEAM_DOMAIN || "").replace(/^https?:\/\//, "").replace(/\/+$/, "");
let certCache = { at: 0, keys: [] };

const b64urlToBytes = (s) => {
  s = s.replace(/-/g, "+").replace(/_/g, "/");
  return Uint8Array.from(atob(s + "=".repeat((4 - (s.length % 4)) % 4)), (c) => c.charCodeAt(0));
};
const b64urlJson = (s) => JSON.parse(new TextDecoder().decode(b64urlToBytes(s)));

async function accessKeys(env, force = false) {
  const age = Date.now() - certCache.at;
  if (certCache.keys.length && age < 3600e3 && !(force && age > 60e3)) return certCache.keys;
  const res = await fetch(`https://${teamHost(env)}/cdn-cgi/access/certs`, { cf: { cacheEverything: true, cacheTtl: 3600 } });
  if (!res.ok) throw new Error(`Access certs ${res.status}`);
  certCache = { at: Date.now(), keys: (await res.json()).keys || [] };
  return certCache.keys;
}

// 通过返回邮箱（或 sub），否则返回 null
async function accessUser(request, env) {
  const jwt = request.headers.get("Cf-Access-Jwt-Assertion");
  const parts = jwt ? jwt.split(".") : [];
  if (parts.length !== 3) return null;
  try {
    const header = b64urlJson(parts[0]);
    const claims = b64urlJson(parts[1]);
    if (header.alg !== "RS256") return null;
    let jwk = (await accessKeys(env)).find((k) => k.kid === header.kid);
    if (!jwk) jwk = (await accessKeys(env, true)).find((k) => k.kid === header.kid); // 密钥轮换后刷新一次
    if (!jwk) return null;
    const key = await crypto.subtle.importKey("jwk", jwk, { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" }, false, ["verify"]);
    const ok = await crypto.subtle.verify(
      "RSASSA-PKCS1-v1_5", key, b64urlToBytes(parts[2]), new TextEncoder().encode(`${parts[0]}.${parts[1]}`)
    );
    if (!ok) return null;
    const now = Date.now() / 1000;
    const aud = Array.isArray(claims.aud) ? claims.aud : [claims.aud];
    if (claims.iss !== `https://${teamHost(env)}` || !aud.includes(env.ACCESS_AUD)) return null;
    if (!claims.exp || claims.exp < now || (claims.nbf && claims.nbf > now + 60)) return null;
    return claims.email || claims.sub || "access-user";
  } catch {
    return null;
  }
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (teamHost(env) && env.ACCESS_AUD && !(await accessUser(request, env))) {
      return new Response("Unauthorized: please sign in through Cloudflare Access.", {
        status: 401,
        headers: { "Cache-Control": "no-store" },
      });
    }

    // /ops 下的一切（含大小写、重复斜杠、编码变体）只走运营这套 token；仅通过 Access 登录而没有运营 token 的人进不去
    let norm = url.pathname;
    try { norm = decodeURIComponent(norm); } catch {}
    norm = norm.replace(/\/+/g, "/").toLowerCase();
    if (norm === "/ops" || norm.startsWith("/ops/")) {
      return handleOps(request, env, url, norm === "/ops" ? "/ops/" : norm);
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
