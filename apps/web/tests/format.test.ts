import assert from "node:assert/strict";
import test from "node:test";

import { formatCost, formatDurationMs, formatTokenCount } from "../lib/format";

test("formats durations with stable units and readable transitions", () => {
  assert.equal(formatDurationMs(26), "26 毫秒");
  assert.equal(formatDurationMs(1_600), "1.6 秒");
  assert.equal(formatDurationMs(62_156), "1 分 02 秒");
  assert.equal(formatDurationMs(408_000), "6 分 48 秒");
  assert.equal(formatDurationMs(3_661_000), "1 小时 1 分");
  assert.equal(formatDurationMs(null), "—");
});

test("formats token counts and costs without dropping useful precision", () => {
  assert.equal(formatTokenCount(214_315), "214,315");
  assert.equal(formatTokenCount(null), "—");
  assert.equal(formatCost("0"), "0");
  assert.equal(formatCost("3.00"), "3");
  assert.equal(formatCost("0.0123"), "0.0123");
  assert.equal(formatCost(undefined), "—");
});
