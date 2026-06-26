import assert from "node:assert/strict";
import { test } from "node:test";

import {
  brandSafetyAllowsReturn,
  brandSafetyBlocksReturn,
  brandSafetyReportFromPayload,
  brandSafetySummaryLabel,
} from "../src/lib/brandSafety.ts";

test("reads blocked brand safety reports from final payloads", () => {
  const report = brandSafetyReportFromPayload({
    review: {
      brand_safety: {
        status: "blocked",
        highest_severity: "high",
        findings: [
          {
            category: "price_promotion",
            severity: "high",
            field_path: "creative_payload.message",
            matched_text: "优惠",
            suggestion: "强调体验",
          },
        ],
      },
    },
  });

  assert.equal(brandSafetyBlocksReturn(report), true);
  assert.equal(brandSafetyAllowsReturn(report), false);
  assert.equal(brandSafetySummaryLabel(report), "品牌安全未通过");
  assert.equal(report?.findings[0]?.field_path, "creative_payload.message");
});

test("treats passed or missing brand safety reports as returnable", () => {
  assert.equal(
    brandSafetyBlocksReturn(
      brandSafetyReportFromPayload({
        review: {
          brand_safety: {
            status: "passed",
            highest_severity: null,
            findings: [],
          },
        },
      }),
    ),
    false,
  );
  assert.equal(brandSafetyBlocksReturn(brandSafetyReportFromPayload({})), false);
  assert.equal(brandSafetySummaryLabel(null), "待检查");
});
