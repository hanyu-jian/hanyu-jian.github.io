// DA 的功率换算（POA 预测 → kWh，含 AC 限值）：Worker 里的 JS 必须和 vcom 的 Python 公式（理论发电量用的那个）逐点一致。
// 期望值是用 vcom 仓库 py/calc_theoretical_production.py 的 production_mwh_15min 算出来的（[场站, 月份, POA W/m², kWh]）。
//    node gate/tests/worker_da.test.mjs
import worker from "../worker.js";

const CASES = [
  ["Putnok", 10, -5.0, 0.0],
  ["Putnok", 10, 0.0, 0.0],
  ["Putnok", 10, 0.7, 4.554],
  ["Putnok", 10, 150.0, 975.905],
  ["Putnok", 10, 400.0, 2602.413],
  ["Putnok", 10, 732.5, 4765.668],
  ["Putnok", 10, 1100.0, 5350.0],
  ["Putnok", 10, 1400.0, 5350.0],
  ["Putnok", 11, -5.0, 0.0],
  ["Putnok", 11, 0.0, 0.0],
  ["Putnok", 11, 0.7, 4.649],
  ["Putnok", 11, 150.0, 996.279],
  ["Putnok", 11, 400.0, 2656.743],
  ["Putnok", 11, 732.5, 4865.16],
  ["Putnok", 11, 1100.0, 5350.0],
  ["Putnok", 11, 1400.0, 5350.0],
  ["Putnok", 12, -5.0, 0.0],
  ["Putnok", 12, 0.0, 0.0],
  ["Putnok", 12, 0.7, 4.735],
  ["Putnok", 12, 150.0, 1014.615],
  ["Putnok", 12, 400.0, 2705.64],
  ["Putnok", 12, 732.5, 4954.703],
  ["Putnok", 12, 1100.0, 5350.0],
  ["Putnok", 12, 1400.0, 5350.0],
  ["Putnok", 6, -5.0, 0.0],
  ["Putnok", 6, 0.0, 0.0],
  ["Putnok", 6, 0.7, 4.212],
  ["Putnok", 6, 150.0, 902.559],
  ["Putnok", 6, 400.0, 2406.824],
  ["Putnok", 6, 732.5, 4407.497],
  ["Putnok", 6, 1100.0, 5350.0],
  ["Putnok", 6, 1400.0, 5350.0],
  ["Serenyfalva", 10, -5.0, 0.0],
  ["Serenyfalva", 10, 0.0, 0.0],
  ["Serenyfalva", 10, 0.7, 5.735],
  ["Serenyfalva", 10, 150.0, 1228.913],
  ["Serenyfalva", 10, 400.0, 3277.102],
  ["Serenyfalva", 10, 732.5, 6001.193],
  ["Serenyfalva", 10, 1100.0, 6850.0],
  ["Serenyfalva", 10, 1400.0, 6850.0],
  ["Serenyfalva", 11, -5.0, 0.0],
  ["Serenyfalva", 11, 0.0, 0.0],
  ["Serenyfalva", 11, 0.7, 5.866],
  ["Serenyfalva", 11, 150.0, 1257.075],
  ["Serenyfalva", 11, 400.0, 3352.201],
  ["Serenyfalva", 11, 732.5, 6138.718],
  ["Serenyfalva", 11, 1100.0, 6850.0],
  ["Serenyfalva", 11, 1400.0, 6850.0],
  ["Serenyfalva", 12, -5.0, 0.0],
  ["Serenyfalva", 12, 0.0, 0.0],
  ["Serenyfalva", 12, 0.7, 5.949],
  ["Serenyfalva", 12, 150.0, 1274.746],
  ["Serenyfalva", 12, 400.0, 3399.322],
  ["Serenyfalva", 12, 732.5, 6225.008],
  ["Serenyfalva", 12, 1100.0, 6850.0],
  ["Serenyfalva", 12, 1400.0, 6850.0],
  ["Serenyfalva", 6, -5.0, 0.0],
  ["Serenyfalva", 6, 0.0, 0.0],
  ["Serenyfalva", 6, 0.7, 5.38],
  ["Serenyfalva", 6, 150.0, 1152.848],
  ["Serenyfalva", 6, 400.0, 3074.262],
  ["Serenyfalva", 6, 732.5, 5629.742],
  ["Serenyfalva", 6, 1100.0, 6850.0],
  ["Serenyfalva", 6, 1400.0, 6850.0],
  ["Sajokaza", 10, -5.0, 0.0],
  ["Sajokaza", 10, 0.0, 0.0],
  ["Sajokaza", 10, 0.7, 8.162],
  ["Sajokaza", 10, 150.0, 1749.085],
  ["Sajokaza", 10, 400.0, 4664.226],
  ["Sajokaza", 10, 732.5, 8541.365],
  ["Sajokaza", 10, 1100.0, 9570.0],
  ["Sajokaza", 10, 1400.0, 9570.0],
  ["Sajokaza", 11, -5.0, 0.0],
  ["Sajokaza", 11, 0.0, 0.0],
  ["Sajokaza", 11, 0.7, 8.298],
  ["Sajokaza", 11, 150.0, 1778.16],
  ["Sajokaza", 11, 400.0, 4741.759],
  ["Sajokaza", 11, 732.5, 8683.347],
  ["Sajokaza", 11, 1100.0, 9570.0],
  ["Sajokaza", 11, 1400.0, 9570.0],
  ["Sajokaza", 12, -5.0, 0.0],
  ["Sajokaza", 12, 0.0, 0.0],
  ["Sajokaza", 12, 0.7, 8.485],
  ["Sajokaza", 12, 150.0, 1818.138],
  ["Sajokaza", 12, 400.0, 4848.367],
  ["Sajokaza", 12, 732.5, 8878.573],
  ["Sajokaza", 12, 1100.0, 9570.0],
  ["Sajokaza", 12, 1400.0, 9570.0],
  ["Sajokaza", 6, -5.0, 0.0],
  ["Sajokaza", 6, 0.0, 0.0],
  ["Sajokaza", 6, 0.7, 7.666],
  ["Sajokaza", 6, 150.0, 1642.732],
  ["Sajokaza", 6, 400.0, 4380.619],
  ["Sajokaza", 6, 732.5, 8022.008],
  ["Sajokaza", 6, 1100.0, 9570.0],
  ["Sajokaza", 6, 1400.0, 9570.0],
  ["Szuhakálló 1", 10, -5.0, 0.0],
  ["Szuhakálló 1", 10, 0.0, 0.0],
  ["Szuhakálló 1", 10, 0.7, 6.707],
  ["Szuhakálló 1", 10, 150.0, 1437.218],
  ["Szuhakálló 1", 10, 400.0, 3832.581],
  ["Szuhakálló 1", 10, 732.5, 7018.414],
  ["Szuhakálló 1", 10, 1100.0, 8100.0],
  ["Szuhakálló 1", 10, 1400.0, 8100.0],
  ["Szuhakálló 1", 11, -5.0, 0.0],
  ["Szuhakálló 1", 11, 0.0, 0.0],
  ["Szuhakálló 1", 11, 0.7, 6.85],
  ["Szuhakálló 1", 11, 150.0, 1467.945],
  ["Szuhakálló 1", 11, 400.0, 3914.52],
  ["Szuhakálló 1", 11, 732.5, 7168.464],
  ["Szuhakálló 1", 11, 1100.0, 8100.0],
  ["Szuhakálló 1", 11, 1400.0, 8100.0],
  ["Szuhakálló 1", 12, -5.0, 0.0],
  ["Szuhakálló 1", 12, 0.0, 0.0],
  ["Szuhakálló 1", 12, 0.7, 6.965],
  ["Szuhakálló 1", 12, 150.0, 1492.527],
  ["Szuhakálló 1", 12, 400.0, 3980.071],
  ["Szuhakálló 1", 12, 732.5, 7288.505],
  ["Szuhakálló 1", 12, 1100.0, 8100.0],
  ["Szuhakálló 1", 12, 1400.0, 8100.0],
  ["Szuhakálló 1", 6, -5.0, 0.0],
  ["Szuhakálló 1", 6, 0.0, 0.0],
  ["Szuhakálló 1", 6, 0.7, 6.262],
  ["Szuhakálló 1", 6, 150.0, 1341.964],
  ["Szuhakálló 1", 6, 400.0, 3578.571],
  ["Szuhakálló 1", 6, 732.5, 6553.257],
  ["Szuhakálló 1", 6, 1100.0, 8100.0],
  ["Szuhakálló 1", 6, 1400.0, 8100.0],
  ["Szuhakálló 2", 10, -5.0, 0.0],
  ["Szuhakálló 2", 10, 0.0, 0.0],
  ["Szuhakálló 2", 10, 0.7, 5.821],
  ["Szuhakálló 2", 10, 150.0, 1247.274],
  ["Szuhakálló 2", 10, 400.0, 3326.064],
  ["Szuhakálló 2", 10, 732.5, 6090.854],
  ["Szuhakálló 2", 10, 1100.0, 6850.0],
  ["Szuhakálló 2", 10, 1400.0, 6850.0],
  ["Szuhakálló 2", 11, -5.0, 0.0],
  ["Szuhakálló 2", 11, 0.0, 0.0],
  ["Szuhakálló 2", 11, 0.7, 5.921],
  ["Szuhakálló 2", 11, 150.0, 1268.81],
  ["Szuhakálló 2", 11, 400.0, 3383.492],
  ["Szuhakálló 2", 11, 732.5, 6196.02],
  ["Szuhakálló 2", 11, 1100.0, 6850.0],
  ["Szuhakálló 2", 11, 1400.0, 6850.0],
  ["Szuhakálló 2", 12, -5.0, 0.0],
  ["Szuhakálló 2", 12, 0.0, 0.0],
  ["Szuhakálló 2", 12, 0.7, 6.034],
  ["Szuhakálló 2", 12, 150.0, 1293.106],
  ["Szuhakálló 2", 12, 400.0, 3448.283],
  ["Szuhakálló 2", 12, 732.5, 6314.668],
  ["Szuhakálló 2", 12, 1100.0, 6850.0],
  ["Szuhakálló 2", 12, 1400.0, 6850.0],
  ["Szuhakálló 2", 6, -5.0, 0.0],
  ["Szuhakálló 2", 6, 0.0, 0.0],
  ["Szuhakálló 2", 6, 0.7, 5.515],
  ["Szuhakálló 2", 6, 150.0, 1181.701],
  ["Szuhakálló 2", 6, 400.0, 3151.201],
  ["Szuhakálló 2", 6, 732.5, 5770.637],
  ["Szuhakálló 2", 6, 1100.0, 6850.0],
  ["Szuhakálló 2", 6, 1400.0, 6850.0]
];

// 一个场站一个月：把这些 POA 当成当月 2 号中午的连续 15 分钟，读回 Worker 返回的 da_kwh
const GITHUB_FILES = {};
globalThis.fetch = async (u) => {
  u = decodeURIComponent(String(u));
  GITHUB_FILES.__urls.push(u);
  for (const [k, v] of Object.entries(GITHUB_FILES)) if (k !== "__urls" && u.includes(k)) return new Response(v, { status: 200 });
  return new Response("", { status: 404 });
};
const env = { OPS_TOKENS: "a:tok", GITHUB_TOKEN: "x", UST: { async get() { return null; } } };
const call = async (qs) => {
  const r = await worker.fetch(new Request("https://x/ops/api/ust?" + qs, { headers: { Cookie: "ops_token=tok" } }), env);
  return [r.status, await r.json()];
};
const THEO = "Date,POA avg [W/m²],Theoretical production [MWh],Theoretical production [kWh]\n";
const pad = (n) => String(n).padStart(2, "0");
let failures = 0, checked = 0;
const eq = (a, b, what) => { if (a !== b) { failures++; console.log("FAIL", what, "got", a, "expected", b); } };

for (const site of ["Putnok", "Serenyfalva", "Sajokaza", "Szuhakálló 1", "Szuhakálló 2"]) {
  for (const month of [10, 11, 12, 6]) {
    const cs = CASES.filter((c) => c[0] === site && c[1] === month);
    const day = `2026-${pad(month)}-02`;
    const lines = cs.map((c, i) => `${day} ${pad(12 + Math.floor(i / 4))}:${pad((i % 4) * 15)}:00,${c[2]},,,,,`);
    GITHUB_FILES.__urls = [];
    GITHUB_FILES["irradiance forecast/2026/15min/" + site] = "Date,POA fcst I (DA) [W/m²],J,K,L,M,N\n" + lines.join("\n") + "\n";
    GITHUB_FILES["theoretical production/2026/15min/" + site] = THEO;
    const [st, j] = await call(`site=${encodeURIComponent(site)}&start=${day}&end=${day}&resolution=15min`);
    eq(st, 200, `${site} ${month} status`);
    const byT = Object.fromEntries(j.rows.map((r) => [r[0], r]));
    cs.forEach((c, i) => {
      const t = `${day} ${pad(12 + Math.floor(i / 4))}:${pad((i % 4) * 15)}:00`;
      const got = byT[t] ? byT[t][4] : undefined;
      eq(got, c[3], `${site} 月${month} POA=${c[2]}`); checked++;
    });
    // 读的是 POA 文件，不再读乙方的 power 文件
    eq(GITHUB_FILES.__urls.some((u) => u.includes("production forecast")), false, `${site} 不应再读 production forecast`);
    eq(GITHUB_FILES.__urls.some((u) => u.includes("irradiance forecast")), true, `${site} 应读 irradiance forecast`);
  }
}

// 月份边界：10-31 23:45 用 10 月的 PR，11-01 00:00 用 11 月的 PR
{
  const p = CASES.find((c) => c[0] === "Putnok" && c[1] === 10 && c[2] === 400)[3], q = CASES.find((c) => c[0] === "Putnok" && c[1] === 11 && c[2] === 400)[3];
  GITHUB_FILES["irradiance forecast/2026/15min/Putnok"] = "Date,POA fcst I (DA) [W/m²]\n2026-10-31 23:45:00,400\n2026-11-01 00:00:00,400\n";
  const [, j] = await call("site=Putnok&start=2026-10-31&end=2026-11-01&resolution=15min");
  const by = Object.fromEntries(j.rows.map((r) => [r[0], r[4]]));
  eq(by["2026-10-31 23:45:00"], p, "10-31 23:45 用 10 月 PR"); eq(by["2026-11-01 00:00:00"], q, "11-01 00:00 用 11 月 PR");
  eq(p !== q, true, "10 月和 11 月的 PR 不同，这个用例才有意义");
}

// hourly：4 个 15 分钟齐全才算，起点标签
{
  GITHUB_FILES["irradiance forecast/2026/15min/Putnok"] = "Date,POA fcst I (DA) [W/m²]\n" +
    ["12:00", "12:15", "12:30", "12:45", "13:00", "13:15", "13:30"].map((t) => `2026-11-02 ${t}:00,400`).join("\n") + "\n";
  const [, j] = await call("site=Putnok&start=2026-11-02&end=2026-11-02&resolution=hourly");
  const by = Object.fromEntries(j.rows.map((r) => [r[0], r[4]]));
  const one = CASES.find((c) => c[0] === "Putnok" && c[1] === 11 && c[2] === 400)[3];
  eq(Math.abs(by["2026-11-02 12:00:00"] - one * 4) < 0.005, true, "hourly 12:00 = 4 个 15 分钟之和（舍入顺序不同，允许 0.005 kWh）");
  eq(by["2026-11-02 13:00:00"] ?? null, null, "hourly 13:00 只有 3 个 15 分钟，不算");
}

console.log(failures ? `\n${failures} 个检查失败` : `\n全部通过（${checked} 个 POA→kWh 换算与 Python 公式逐点一致）`);
process.exit(failures ? 1 : 0);
