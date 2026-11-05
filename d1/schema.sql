-- Cloudflare D1 数据库 sep 的表结构。只放结构，不放数据（数据是私有的，不要提交到这个仓库）。
-- 建表：npx wrangler d1 execute sep --remote --file=d1/schema.sql
-- 数据导入：见 vcom 仓库的 py/export_d1_meter.py。

-- 表计读数，15 分钟，kWh。
-- ts = 布达佩斯当地时间、区间起点，格式和 CSV 的 Date 一致（YYYY-MM-DD HH:MM:SS）。
-- 缺测不写入（没有这一行 = 没有数据），夏令时跳过的那一小时同样没有行。
CREATE TABLE IF NOT EXISTS meter (
  site TEXT NOT NULL,
  ts   TEXT NOT NULL,
  kwh  REAL NOT NULL,
  PRIMARY KEY (site, ts)
) WITHOUT ROWID;
