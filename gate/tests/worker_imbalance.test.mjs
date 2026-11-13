// 结算输入 /ops/api/imbalance（逐 15 分钟）+ 页面里的 settleSlot（DA 报量百分比 / 剩余部分在 IDC 平仓还是留在不平衡）
// 和 py/analysis/tokaj_da_idc_settlement.py（final_plan / compute / summarize）逐项一致：
// gate/tests/imbalance_parity.py 造随机数据（含缺失价格 / 缺失预测）并用 Python 算出期望值（16 档提前量 × {IDC, 不平衡} × 报量 100% / 80% / 50% × 3 天）。
// 运行：node gate/tests/worker_imbalance.test.mjs（需要 python3 + pandas）
import fs from "node:fs";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";

const fx = JSON.parse(execFileSync("python3", [new URL("./imbalance_parity.py", import.meta.url).pathname], { maxBuffer: 1 << 28 }).toString());
const src = fs.readFileSync(new URL("../worker.js", import.meta.url), "utf-8").replace(/^export default/m, "const __default =");
const { handleImbalance } = await import(new URL("data:text/javascript;base64," + Buffer.from(src + "\nexport { handleImbalance };").toString("base64")));
// 页面里的结算函数：从 ops-imbalance.html 的 <core> 段取出来直接跑
const page = fs.readFileSync(new URL("../../ops/ops-imbalance.html", import.meta.url), "utf-8");
const core = page.match(/\/\/ <core>([\s\S]*?)\/\/ <\/core>/)[1];
const settleSlot = new Function(core + "\nreturn settleSlot;")();

const H = [15, 30, 45, 60, 75, 90, 105, 120, 135, 150, 165, 180, 195, 210, 225, 240];
const num = (v) => (v === null ? "" : String(v));
const cols = ["Date", ...H.map((h) => `POA h${h} [W/m²]`), ...H.map((h) => `kWh h${h}`), ...H.map((h) => `Lvl h${h}`)];
const view = "﻿" + [cols.join(","), ...fx.index.map((t, i) => [t, ...H.map(() => ""), ...H.map((h) => num(fx.leads[h][i])), ...H.map(() => "")].join(","))].join("\n") + "\n";
const theo = "Date,POA avg [W/m²],Theoretical production [MWh],Theoretical production [kWh]\n" + fx.index.map((t, i) => (fx.AH[i] === null ? null : `${t},0,0,${fx.AH[i]}`)).filter(Boolean).join("\n") + "\n";
const da = "Date,I,J,K,L,M,N\n" + fx.index.map((t, i) => (fx.I[i] === null ? null : `${t},${fx.I[i]},,,,,`)).filter(Boolean).join("\n") + "\n";
// hu-market-data 的整合表：Date 没有秒，列很多，按列名取；故意把三列打乱顺序并混入其它列
const price = "Date,IDA1 Price (HUPX) (EUR/MWh),Imbalance Price (EUR/MWh),DA Price (EUR/MWh),System direction (MWh),ID3 Price (EUR/MWh)\n"
  + fx.index.map((t, i) => `${t.slice(0, 16)},1,${num(fx.PI[i])},${num(fx.P[i])},9,${num(fx.AA[i])}`).join("\n") + "\n";
const GH = {
  "hanyu-jian/vcom/data/theoretical production/2026/15min/Putnok.csv": theo,
  "hanyu-jian/vcom/data/production forecast/2026/15min/Putnok.csv": da,
  "hanyu-jian/hu-market-data/HU_2026.csv": price,
};
const env = { UST: { get: async (k) => (k === "ust/view/2026-11/Putnok.csv" ? { text: async () => view } : null) }, GITHUB_TOKEN: "x" };
const okFetch = async (u) => {
  const m = String(u).match(/repos\/(.+?)\/contents\/(.+)$/);
  const p = decodeURIComponent(m[1] + "/" + m[2]);
  return GH[p] !== undefined ? { ok: true, status: 200, text: async () => GH[p] } : { ok: false, status: 404, text: async () => "" };
};
globalThis.fetch = okFetch;
const call = async (qs) => handleImbalance(new URL("https://x/ops/api/imbalance?" + qs), env);

const TOL = 0.0101;
const close = (a, b, what) => assert.ok(Math.abs(a - b) <= TOL, `${what}: page/worker ${a} vs python ${b}`);
let checks = 0, slots = 0;
const tmp = new Array(8);
for (const L of H) {
  const r = await call(`site=Putnok&start=2026-11-02&end=2026-11-04&lead=${L}`);
  assert.equal(r.status, 200);
  const j = await r.json();
  assert.equal(j.lead, L);
  slots = j.t.length;
  for (const mode of ["idc", "imb"]) for (const pct of ["1.0", "0.8", "0.5"]) {
    const sums = {};
    j.t.forEach((t, i) => {
      settleSlot(j.H[i], j.AH[i], j.F[i], j.P[i], j.AA[i], j.PI[i], Number(pct), mode, tmp);
      const d = t.slice(0, 10), o = sums[d] || (sums[d] = { da: 0, idc: 0, imb: 0, result: 0, ref: 0, cells: 0 });
      o.da += tmp[3]; o.idc += tmp[4]; o.imb += tmp[5]; o.result += tmp[6]; o.ref += tmp[7]; o.cells++;
    });
    for (const [d, e] of Object.entries(fx.expected[String(L)][mode][pct])) {
      const o = sums[d];
      ["da", "idc", "imb", "result"].forEach((k) => { close(o[k], e[k], `lead ${L} ${mode} ${pct} ${d} ${k}`); checks++; });
      assert.equal(o.cells, e.cells, `cells ${d}`); checks++;
      if (pct === "1.0") { close(o.ref, fx.expected[String(L)].ref[d], `lead ${L} ${d} ref (DA-only)`); checks++; }
    }
  }
}
// 100% 报量 + 留在不平衡 = DA-only 参考线（页面里的参考线和它一致）
// 价格表读不到 -> 502 且提示权限。GitHub 对没有权限的私有仓库返回 404，不能当成「没有数据」静默跳过
for (const status of [403, 404]) {
  globalThis.fetch = async (u) => (String(u).includes("hu-market-data") ? { ok: false, status, text: async () => "" } : okFetch(u));
  const r2 = await call("site=Putnok&start=2026-11-02&end=2026-11-04");
  assert.equal(r2.status, 502, `price table ${status}`); assert.match((await r2.json()).error, /hu-market-data/);
}
// PRICE_TOKEN 优先于 GITHUB_TOKEN，只用于价格仓库
const auth = [];
globalThis.fetch = async (u, init) => { auth.push([String(u).includes("hu-market-data"), init.headers.Authorization]); return okFetch(u); };
env.PRICE_TOKEN = "price-token";
assert.equal((await call("site=Putnok&start=2026-11-02&end=2026-11-04")).status, 200);
assert.ok(auth.filter(([isPrice]) => isPrice).every(([, h]) => h === "Bearer price-token"), "price repo uses PRICE_TOKEN");
assert.ok(auth.filter(([isPrice]) => !isPrice).every(([, h]) => h === "Bearer x"), "other repos keep GITHUB_TOKEN");
delete env.PRICE_TOKEN;
globalThis.fetch = okFetch;
// 校验
for (const [qs, st] of [["site=Nope&start=2026-11-02", 400], ["site=Putnok&start=2026-01-01&end=2026-12-31", 400], ["site=Putnok&start=2026-11-05&end=2026-11-01", 400], ["site=Putnok&start=2026-11-02&lead=50", 400]])
  assert.equal((await call(qs)).status, st, qs);
console.log(`全部通过（${checks} 项和 Python 结算脚本逐项一致：${slots} 个时段 × 16 档提前量 × {IDC 平仓, 留在不平衡} × 报量 100%/80%/50%；价格表 403 / 404 -> 502、PRICE_TOKEN；参数校验）`);
