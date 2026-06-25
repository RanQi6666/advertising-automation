import assert from "node:assert/strict";
import { test } from "node:test";

import { displayAssetUrl } from "../src/lib/assetUrls.ts";

test("rewrites public storage URLs to the current local origin for previews", () => {
  const url = displayAssetUrl(
    "https://ai.ggcss.xyz/storage/images/campaign/example.jpeg",
    "http://127.0.0.1/work-orders/new",
  );

  assert.equal(url, "http://127.0.0.1/storage/images/campaign/example.jpeg");
});

test("keeps non-local and non-storage URLs unchanged", () => {
  assert.equal(
    displayAssetUrl(
      "https://ai.ggcss.xyz/storage/images/campaign/example.jpeg",
      "https://ai.ggcss.xyz/work-orders/new",
    ),
    "https://ai.ggcss.xyz/storage/images/campaign/example.jpeg",
  );
  assert.equal(
    displayAssetUrl("https://cdn.example.com/assets/example.jpeg", "http://127.0.0.1/work-orders/new"),
    "https://cdn.example.com/assets/example.jpeg",
  );
});
