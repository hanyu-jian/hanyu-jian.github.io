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
const { settleSlot, imbalancePct, profitPct } = new Function(core + "\nreturn { settleSlot, imbalancePct, profitPct };")();

const H = [15, 30, 45, 60, 75, 90, 105, 120, 135, 150, 165, 180, 195, 210, 225, 240];
const num = (v) => (v === null ? "" : String(v));
const cols = ["Date", ...H.map((h) => `POA h${h} [W/m²]`), ...H.map((h) => `kWh h${h}`), ...H.map((h) => `Lvl h${h}`)];
const view = "﻿" + [cols.join(","), ...fx.index.map((t, i) => [t, ...H.map(() => ""), ...H.map((h) => num(fx.leads[h][i])), ...H.map(() => "")].join(","))].join("\n") + "\n";
const theo = "Date,POA avg [W/m²],Theoretical production [MWh],Theoretical production [kWh]\n" + fx.index.map((t, i) => (fx.AH[i] === null ? null : `${t},0,0,${fx.AH[i]}`)).filter(Boolean).join("\n") + "\n";
const da = "Date,I,J,K,L,M,N\n" + fx.index.map((t, i) => (fx.I[i] === null ? null : `${t},${fx.I[i]},,,,,`)).filter(Boolean).join("\n") + "\n";
// hu-market-data 的整合表：Date 没有秒，列很多，按列名取；故意把三列打乱顺序并混入其它列
const price = "Date,IDA1 Price (HUPX) (EUR/MWh),Imbalance Price (EUR/MWh),DA Price (EUR/MWh),System direction (MWh),ID3 Price (EUR/MWh)\n"
  + fx.index.map((t, i) => `${t.slice(0, 16)},1,${num(fx.PI[i])},${num(fx.P[i])},9,${num(fx.AA[i])}`).join("\n") + "\n";
// R2 里的价格表（build_combined.yml 上传的三列瘦身版，Worker 按列名取）
const slimPrice = "Date,DA Price (EUR/MWh),ID3 Price (EUR/MWh),Imbalance Price (EUR/MWh)\n"
  + fx.index.map((t, i) => `${t.slice(0, 16)},${num(fx.P[i])},${num(fx.AA[i])},${num(fx.PI[i])}`).join("\n") + "\n";
let r2Prices = true;
const GH = {
  "hanyu-jian/vcom/data/theoretical production/2026/15min/Putnok.csv": theo,
  "hanyu-jian/vcom/data/production forecast/2026/15min/Putnok.csv": da,
  "hanyu-jian/hu-market-data/HU_2026.csv": price,
};
const env = { UST: { get: async (k) => (k === "ust/view/2026-11/Putnok.csv" ? { text: async () => view } : k === "market/prices_HU_2026.csv" && r2Prices ? { text: async () => slimPrice } : null) }, GITHUB_TOKEN: "x" };
const okFetch = async (u) => {
  const m = String(u).match(/repos\/(.+?)\/contents\/(.+)$/);
  const p = decodeURIComponent(m[1] + "/" + m[2]);
  return GH[p] !== undefined ? { ok: true, status: 200, text: async () => GH[p] } : { ok: false, status: 404, text: async () => "" };
};
const priceRepoCalls = [];
globalThis.fetch = async (u, init) => { if (String(u).includes("hu-market-data")) priceRepoCalls.push(String(u)); return okFetch(u, init); };
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
// 付给光伏场站的钱 / 售电公司利润 / 「日前价低于阈值就不发电」
{
  const o = new Array(11), n = new Array(11);
  // 手算：H=100, F=120, AH=110, P=50, AA=60, PI=70, DA 报量 100%、剩余在 IDC：DA 收入 5，IDC 收入 1.2，不平衡费用 −0.7，总收入 5.5
  // 付给场站 = AH × P / 1000 = 110 × 50 / 1000 = 5.5，利润 = 5.5 − 5.5 = 0
  settleSlot(100, 110, 120, 50, 60, 70, 1, "idc", o);
  const near = (a, b, w) => assert.ok(Math.abs(a - b) < 1e-9, `${w}: ${a} vs ${b}`);
  near(o[3], 5, "DA revenue"); near(o[4], 1.2, "IDC revenue"); near(o[5], -0.7, "imbalance cost"); near(o[6], 5.5, "total revenue");
  near(o[8], 5.5, "payment to the plant"); near(o[6] - o[8], 0, "profit"); near(o[10], 0, "not curtailed");
  near(o[7], (50 * 100 - (100 - 110) * 70) / 1000, "DA-only reference revenue (unchanged)"); near(o[9], o[7] - 5.5, "DA-only reference profit");
  // 阈值关闭（不传 / null）和以前完全一样
  settleSlot(100, 110, 120, 50, 60, 70, 1, "idc", n, null);
  o.forEach((v, i) => near(n[i], v, `thr null, out[${i}]`));
  // 价格 < 阈值：没有收入、没有不平衡费用、不付场站，参考线不受影响
  settleSlot(100, 110, 120, 50, 60, 70, 1, "idc", n, 51);
  [0, 1, 2, 3, 4, 5, 6, 8].forEach(i => near(n[i], 0, `curtailed out[${i}]`)); near(n[10], 1, "curtailed flag"); near(n[7], o[7], "reference unchanged"); near(n[9], o[9], "reference profit unchanged");
  // 价格 = 阈值也停发（≤）；价格为空不触发
  settleSlot(100, 110, 120, 50, 60, 70, 1, "idc", n, 50); near(n[6], 0, "price equal to the threshold is curtailed"); near(n[10], 1, "flagged");
  settleSlot(100, 110, 120, 50.01, 60, 70, 1, "idc", n, 50); near(n[10], 0, "price just above the threshold generates");
  settleSlot(100, 110, 120, null, 60, 70, 1, "idc", n, 50); near(n[10], 0, "unknown price never curtails");
  // 云量策略：云多的时段 DA 报量和 IDC 最终仓位都乘以 kf（这里 0.5）；kf = 1 或不传和以前完全一样
  settleSlot(100, 110, 120, 50, 60, 70, 1, "idc", n, null, 0.5);
  near(n[0], 50, "cloudy DA bid halves"); near(n[1], 60, "cloudy IDC position = 0.5 x F"); near(n[2], 10, "idc volume"); near(n[3], 2.5, "DA revenue"); near(n[4], 0.6, "IDC revenue"); near(n[5], 3.5, "imbalance (cheaper to be short)"); near(n[6], 6.6, "total"); near(n[7], o[7], "reference unchanged"); near(n[8], o[8], "plant is still paid for what it generates");
  settleSlot(100, 110, 120, 50, 60, 70, 1, "imb", n, null, 0.5); near(n[1], 50, "leave-in-imbalance: position = scaled DA bid"); near(n[6], 6.7, "imbalance mode total");
  settleSlot(100, 110, null, 50, 60, 70, 1, "idc", n, null, 0.5); near(n[1], 50, "no UST: position = scaled DA bid"); near(n[2], 0, "nothing traded in IDC");
  settleSlot(100, 110, 120, 50, 60, 70, 1, "idc", n, 50, 0.5); near(n[6], 0, "curtailment wins over the cloud factor"); near(n[10], 1, "flagged curtailed");
  settleSlot(100, 110, 120, 50, 60, 70, 1, "idc", n, null, 1); o.forEach((v, i) => near(n[i], v, `kf 1, out[${i}]`));
  // 负电价：阈值 0 时不发电，避免「发电还倒贴」
  settleSlot(100, 110, 120, -20, 60, 70, 1, "imb", n, 0); near(n[10], 1, "negative price below 0"); near(n[6], 0, "no revenue"); near(n[8], 0, "no payment");
  settleSlot(100, 110, 120, -20, 60, 70, 1, "imb", n); near(n[8], -2.2, "without the strategy the plant is paid AH x P (negative price)"); near(n[10], 0, "off");
}
// Imbalance % = 不平衡费用 / 不平衡前收入（DA + IDC），即工作簿的 AQ / AF
{
  // 工作簿 Putnok 1–8 月：ΣAQ = −333,839，ΣAF = 2,441,198 -> −13.68%
  assert.ok(Math.abs(imbalancePct(2441198, 0, -333839) - (-13.675)) < 0.001, "workbook Putnok");
  assert.ok(Math.abs(profitPct(200, 5) - 2.5) < 1e-9, "profit / PV revenue");
  assert.ok(profitPct(200, -10) < 0, "negative profit -> negative ratio");
  assert.equal(profitPct(0, 5), null, "no PV revenue -> no ratio");
  assert.equal(imbalancePct(0, 0, -5), null, "no revenue before imbalance -> no percentage");
  assert.ok(imbalancePct(100, 20, 12) > 0, "positive imbalance cost (income) is positive");
  // 和 Python 结算脚本逐日一致：不平衡费用 / (DA 收入 + IDC 收入) = imbalance_fee_EUR / rev_before_imbalance_EUR
  for (const mode of ["idc", "imb"]) for (const pct of ["1.0", "0.8", "0.5"]) for (const [d, e] of Object.entries(fx.expected["60"][mode][pct])) {
    const want = e.imb / (e.da + e.idc) * 100, got = imbalancePct(e.da, e.idc, e.imb);
    assert.ok(Math.abs(got - want) < 1e-9, `${mode} ${pct} ${d}`); checks++;
  }
}
assert.equal(priceRepoCalls.length, 0, "prices come from R2, the price repository is not touched");
const okFetch2 = globalThis.fetch;
const fromR2 = await (await call("site=Putnok&start=2026-11-02&end=2026-11-04&lead=60")).json();

// R2 里没有价格表 -> 退回 hu-market-data 仓库，结果和读 R2 完全一样
r2Prices = false;
const fromRepo = await (await call("site=Putnok&start=2026-11-02&end=2026-11-04&lead=60")).json();
assert.ok(priceRepoCalls.length > 0, "fallback reads the price repository");
for (const k of ["t", "P", "AA", "PI", "H", "AH", "F"]) assert.deepEqual(fromRepo[k], fromR2[k], `R2 vs repository: ${k}`);

// 两边都读不到 -> 502，提示两条路。GitHub 对没有权限的私有仓库返回 404，不能当成「没有数据」静默跳过
for (const status of [403, 404]) {
  globalThis.fetch = async (u, init) => (String(u).includes("hu-market-data") ? { ok: false, status, text: async () => "" } : okFetch2(u, init));
  const r2 = await call("site=Putnok&start=2026-11-02&end=2026-11-04");
  assert.equal(r2.status, 502, `price table ${status}`);
  const msg = (await r2.json()).error; assert.match(msg, /market\/prices_HU_2026\.csv/); assert.match(msg, /hu-market-data/);
}
// PRICE_TOKEN 优先于 GITHUB_TOKEN，只用于价格仓库
const auth = [];
globalThis.fetch = async (u, init) => { auth.push([String(u).includes("hu-market-data"), init.headers.Authorization]); return okFetch2(u, init); };
env.PRICE_TOKEN = "price-token";
assert.equal((await call("site=Putnok&start=2026-11-02&end=2026-11-04")).status, 200);
assert.ok(auth.filter(([isPrice]) => isPrice).every(([, h]) => h === "Bearer price-token"), "price repo uses PRICE_TOKEN");
assert.ok(auth.filter(([isPrice]) => !isPrice).every(([, h]) => h === "Bearer x"), "other repos keep GITHUB_TOKEN");
delete env.PRICE_TOKEN;
globalThis.fetch = okFetch2; r2Prices = true;
// 校验
for (const [qs, st] of [["site=Nope&start=2026-11-02", 400], ["site=Putnok&start=2026-01-01&end=2026-12-31", 400], ["site=Putnok&start=2026-11-05&end=2026-11-01", 400], ["site=Putnok&start=2026-11-02&lead=50", 400]])
  assert.equal((await call(qs)).status, st, qs);
console.log(`全部通过（${checks} 项和 Python 结算脚本逐项一致：${slots} 个时段 × 16 档提前量 × {IDC 平仓, 留在不平衡} × 报量 100%/80%/50%；价格表优先读 R2、退回仓库结果一致、两边都读不到 -> 502、PRICE_TOKEN；参数校验）`);
