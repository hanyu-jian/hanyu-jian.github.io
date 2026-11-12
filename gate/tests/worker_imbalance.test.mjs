// /ops/api/imbalance 和 py/analysis/tokaj_da_idc_settlement.py（final_plan / compute / summarize）逐项一致：
// gate/tests/imbalance_parity.py 造随机数据（含缺失价格 / 缺失预测）并用 Python 算出每天 × 16 档提前量的期望值，这里喂给假 GitHub / 假 R2 再比。
// 运行：node gate/tests/worker_imbalance.test.mjs（需要 python3 + pandas）
import fs from "node:fs";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";

const fx = JSON.parse(execFileSync("python3", [new URL("./imbalance_parity.py", import.meta.url).pathname], { maxBuffer: 1 << 28 }).toString());
const src = fs.readFileSync(new URL("../worker.js", import.meta.url), "utf-8").replace(/^export default/m, "const __default =");
const { handleImbalance } = await import(new URL("data:text/javascript;base64," + Buffer.from(src + "\nexport { handleImbalance };").toString("base64")));

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
globalThis.fetch = async (u) => {
  const m = String(u).match(/repos\/(.+?)\/contents\/(.+)$/);
  const p = decodeURIComponent(m[1] + "/" + m[2]);
  return GH[p] !== undefined ? { ok: true, status: 200, text: async () => GH[p] } : { ok: false, status: 404, text: async () => "" };
};

const r = await handleImbalance(new URL("https://x/ops/api/imbalance?site=Putnok&start=2026-11-02&end=2026-11-04"), env);
assert.equal(r.status, 200);
const j = await r.json();
assert.deepEqual(j.days, ["2026-11-02", "2026-11-03", "2026-11-04"]);
const bi = (k) => j.base_fields.indexOf(k), TOL = 0.0051;
const close = (a, b, what) => assert.ok(Math.abs(a - b) <= TOL, `${what}: worker ${a} vs python ${b}`);
let checks = 0;
j.days.forEach((d, di) => {
  const e60 = fx.expected["60"][d];
  // 和提前量无关的量
  [["n", "cells"], ["actual", "actual_kWh"], ["da", "da_kWh"], ["q", "rev_da_EUR"], ["da_only", "result_da_only_EUR"],
   ["da_net", "da_imbalance_net_kWh"], ["da_abs", "da_imbalance_abs_kWh"], ["no_p", "cells_no_da_price"], ["no_pimb", "cells_no_imbalance_price"]]
    .forEach(([k, pk]) => { close(j.base[di][bi(k)], e60[pk], `${d} base ${k}`); checks++; });
  // 每一档提前量
  H.forEach((L, li) => {
    const e = fx.expected[String(L)][d];
    [["ust", "cells_with_ust"], ["idc_net", "idc_net_kWh"], ["idc_abs", "idc_abs_kWh"], ["ae", "rev_idc_EUR"], ["ai_net", "imbalance_net_kWh"],
     ["ai_abs", "imbalance_abs_kWh"], ["fee", "imbalance_fee_EUR"], ["result", "result_EUR"]]
      .forEach(([k, pk]) => { close(j.by_lead[k][di][li], e[pk], `${d} lead ${L} ${k}`); checks++; });
  });
});
// 价格表读不到（token 对 hu-market-data 没有权限）-> 502 且提示权限
const okFetch = globalThis.fetch;
globalThis.fetch = async (u) => (String(u).includes("hu-market-data") ? { ok: false, status: 403, text: async () => "" } : okFetch(u));
const r2 = await handleImbalance(new URL("https://x/ops/api/imbalance?site=Putnok&start=2026-11-02&end=2026-11-04"), env);
assert.equal(r2.status, 502); assert.match((await r2.json()).error, /hu-market-data/);
globalThis.fetch = okFetch;
// 校验
const bad = async (qs) => (await handleImbalance(new URL("https://x/ops/api/imbalance?" + qs), env)).status;
assert.equal(await bad("site=Nope&start=2026-11-02"), 400);
assert.equal(await bad("site=Putnok&start=2026-01-01&end=2026-12-31"), 400);
assert.equal(await bad("site=Putnok&start=2026-11-05&end=2026-11-01"), 400);
console.log(`全部通过（${checks} 项和 Python 结算脚本逐项一致：3 天 × 16 档提前量 × 8 个量 + 和提前量无关的 9 个量；价格表读不到 -> 502；参数校验）`);
