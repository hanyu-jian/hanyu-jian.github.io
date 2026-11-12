// /ops/api/ust 把 live / replay / hindcast 三个来源合成一条线：同一时段 live 优先，缺了依次用 replay、hindcast；
// 范围外的月份不读 R2。用假 R2 + 假 GitHub 验证。运行：node gate/tests/worker_ust_merge.test.mjs
import fs from "node:fs";
import assert from "node:assert/strict";

const src = fs.readFileSync(new URL("../worker.js", import.meta.url), "utf-8").replace(/^export default/m, "const __default =");
const mod = new URL("data:text/javascript;base64," + Buffer.from(src + "\nexport { handleUst };").toString("base64"));
const { handleUst } = await import(mod);

const H = [15, 30, 45, 60, 75, 90, 105, 120, 135, 150, 165, 180, 195, 210, 225, 240];
const cols = ["Date", ...H.map((h) => `POA h${h} [W/m²]`), ...H.map((h) => `kWh h${h}`), ...H.map((h) => `Lvl h${h}`)];
const view = (rows) => "﻿" + [cols.join(","), ...rows.map(([d, kwh60]) => [d, ...H.map(() => ""), ...H.map((h) => (h === 60 && kwh60 !== null ? kwh60 : "")), ...H.map(() => "")].join(","))].join("\n") + "\n";

const R2 = {
  "ust/view/2026-11/Putnok.csv": view([["2026-11-01 12:00:00", 111], ["2026-11-01 12:15:00", null]]),
  "ust-replay/view/2026-11/Putnok.csv": view([["2026-11-01 12:00:00", 222], ["2026-11-01 12:15:00", 333]]),
  "ust-hindcast/view/2026-10/Putnok.csv": view([["2026-10-20 12:00:00", 444]]),
  "ust-hindcast/view/2026-01/Putnok.csv": view([["2026-01-15 12:00:00", 555]]),
};
const reads = [];
const env = { UST: { get: async (k) => { reads.push(k); return R2[k] ? { text: async () => R2[k] } : null; } }, GITHUB_TOKEN: "x" };
globalThis.fetch = async () => ({ ok: false, status: 404, text: async () => "" });

async function run(start, end, source = "") {
  reads.length = 0;
  const r = await handleUst(new URL(`https://x/ops/api/ust?site=Putnok&start=${start}&end=${end}&horizon=60${source}`), env);
  const j = await r.json();
  if (!j.columns) return { status: r.status, vals: {}, reads: [] };
  const i = j.columns.indexOf("ust_kwh");
  return { status: r.status, vals: Object.fromEntries((j.rows || []).filter((x) => x[i] != null).map((x) => [x[0], x[i]])), reads: [...reads].filter((k) => k.includes("/view/")) };
}

let t = await run("2026-11-01", "2026-11-01");
assert.equal(t.vals["2026-11-01 12:00:00"], 111, "live 优先");
assert.equal(t.vals["2026-11-01 12:15:00"], 333, "live 缺 -> replay");
t = await run("2026-10-20", "2026-10-20");
assert.equal(t.vals["2026-10-20 12:00:00"], 444, "只有 hindcast 的时段");
t = await run("2026-01-15", "2026-01-15");
assert.equal(t.vals["2026-01-15 12:00:00"], 555);
assert.deepEqual(t.reads, ["ust-hindcast/view/2026-01/Putnok.csv"], "1 月只读 hindcast，不读 live / replay");
t = await run("2026-10-20", "2026-11-01");
assert.equal(t.vals["2026-10-20 12:00:00"], 444); assert.equal(t.vals["2026-11-01 12:00:00"], 111, "跨月合成一条线");
t = await run("2026-11-01", "2026-11-01", "&source=replay");
assert.equal(t.vals["2026-11-01 12:00:00"], 222, "可单独指定来源");
t = await run("2026-11-01", "2026-11-01", "&source=bad");
assert.equal(t.status, 400);
console.log("全部通过（live > replay > hindcast 合并、跨月、按月份少读 R2、来源参数校验）");
