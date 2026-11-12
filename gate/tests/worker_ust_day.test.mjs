// /ops/api/ust-day：某一天的理论发电量 + DA + 16 档提前量。每个 (时段, 提前量) 单元格独立按 live > replay > hindcast 取值，
// 不做「退到更大提前量」的回退。用假 GitHub + 假 R2 验证。运行：node gate/tests/worker_ust_day.test.mjs
import fs from "node:fs";
import assert from "node:assert/strict";

const src = fs.readFileSync(new URL("../worker.js", import.meta.url), "utf-8").replace(/^export default/m, "const __default =");
const { handleUstDay } = await import(new URL("data:text/javascript;base64," + Buffer.from(src + "\nexport { handleUstDay };").toString("base64")));

const H = [15, 30, 45, 60, 75, 90, 105, 120, 135, 150, 165, 180, 195, 210, 225, 240];
const cols = ["Date", ...H.map((h) => `POA h${h} [W/m²]`), ...H.map((h) => `kWh h${h}`), ...H.map((h) => `Lvl h${h}`)];
// rows: [Date, {h: kwh}]
const view = (rows) => "﻿" + [cols.join(","), ...rows.map(([d, m]) => [d, ...H.map(() => ""), ...H.map((h) => (m[h] ?? "")), ...H.map(() => "")].join(","))].join("\n") + "\n";

const R2 = {
  "ust/view/2026-11/Putnok.csv": view([["2026-11-05 12:00:00", { 15: 11, 60: 61 }], ["2026-11-05 12:15:00", { 15: 12 }]]),
  "ust-replay/view/2026-11/Putnok.csv": view([["2026-11-05 12:00:00", { 15: 99, 30: 31, 60: 99 }], ["2026-11-05 12:15:00", { 240: 241 }]]),
  "ust-hindcast/view/2026-10/Putnok.csv": view([["2026-10-20 12:00:00", { 60: 6 }]]),
};
const GH = {
  "data/theoretical production/2026/15min/Putnok.csv": "Date,POA avg [W/m²],Theoretical production [MWh],Theoretical production [kWh]\n2026-11-05 12:00:00,300,0.1,100\n2026-11-05 12:15:00,310,0.1,110\n2026-10-20 12:00:00,300,0.1,50\n",
  "data/production forecast/2026/15min/Putnok.csv": "Date,I,J,K,L,M,N\n2026-11-05 12:00:00,90,,,,,\n2026-11-05 12:15:00,95,,,,,\n",
};
const reads = [];
const env = { UST: { get: async (k) => { reads.push(k); return R2[k] ? { text: async () => R2[k] } : null; } }, GITHUB_TOKEN: "x" };
globalThis.fetch = async (u) => {
  const p = decodeURIComponent(String(u).split("/contents/")[1]);
  return GH[p] ? { ok: true, status: 200, text: async () => GH[p] } : { ok: false, status: 404, text: async () => "" };
};
const get = async (qs) => { const r = await handleUstDay(new URL("https://x/ops/api/ust-day?" + qs), env); return [r.status, await r.json()]; };

let [st, j] = await get("site=Putnok&date=2026-11-05");
assert.equal(st, 200);
assert.deepEqual(j.columns.slice(0, 5), ["Date", "theoretical_kwh", "da_kwh", "h15", "h30"]);
assert.equal(j.columns.length, 1 + 2 + 16);
const row = (d) => j.rows.find((r) => r[0] === d);
const c = (d, k) => row(d)[j.columns.indexOf(k)];
assert.equal(c("2026-11-05 12:00:00", "theoretical_kwh"), 100);
assert.equal(c("2026-11-05 12:00:00", "da_kwh"), 90);
assert.equal(c("2026-11-05 12:00:00", "h15"), 11, "live 优先");
assert.equal(c("2026-11-05 12:00:00", "h30"), 31, "live 缺这一档 -> replay");
assert.equal(c("2026-11-05 12:00:00", "h60"), 61, "同一档 live 有值就不被 replay 覆盖");
assert.equal(c("2026-11-05 12:00:00", "h45"), null, "没有回退到更大提前量");
assert.equal(c("2026-11-05 12:15:00", "h240"), 241);
[st, j] = await get("site=Putnok&date=2026-10-20");
assert.equal(c("2026-10-20 12:00:00", "h60"), 6, "只有 hindcast 的日子");
assert.ok(reads.filter((k) => k.includes("hindcast")).length >= 1);
[st] = await get("site=Putnok&date=2026-01-02"); assert.equal(st, 404, "没有任何数据 -> 404");
[st] = await get("site=Nope&date=2026-11-05"); assert.equal(st, 400);
[st] = await get("site=Putnok&date=2026-13-45"); assert.equal(st, 400);
console.log("全部通过（单日 19 列、逐单元格 live > replay > hindcast、无提前量回退、参数校验）");
