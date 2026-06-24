import assert from "node:assert/strict";
import test from "node:test";

import {
  formatManualAdPerformanceJson,
  parseManualAdPerformanceJson,
} from "../src/lib/adPerformanceManualJson.ts";

test("parseManualAdPerformanceJson accepts a pasted analysis object", () => {
  const payload = parseManualAdPerformanceJson(
    JSON.stringify({
      source_type: "manual",
      campaign: { name: "Campaign A" },
      adset: { name: "Adset A" },
      creative: { name: "Creative A" },
      insight: { impressions: "100", clicks: "12" },
    }),
  );

  assert.equal(payload.source_type, "manual");
  assert.deepEqual(payload.creative, { name: "Creative A" });
});

test("parseManualAdPerformanceJson marks pasted data as manual when source is omitted", () => {
  const payload = parseManualAdPerformanceJson(
    JSON.stringify({
      creative: { name: "Creative A" },
      insight: { clicks: "12" },
    }),
  );

  assert.equal(payload.source_type, "manual");
});

test("parseManualAdPerformanceJson rejects invalid and non-object JSON", () => {
  assert.throws(
    () => parseManualAdPerformanceJson("{"),
    /JSON 格式不正确/,
  );
  assert.throws(
    () => parseManualAdPerformanceJson("[]"),
    /必须是一个 JSON 对象/,
  );
  assert.throws(
    () => parseManualAdPerformanceJson("{}"),
    /至少包含 campaign、adset、creative 或 insight/,
  );
});

test("formatManualAdPerformanceJson produces stable pretty JSON", () => {
  assert.equal(
    formatManualAdPerformanceJson({ creative: { name: "Creative A" } }),
    '{\n  "creative": {\n    "name": "Creative A"\n  }\n}',
  );
});
