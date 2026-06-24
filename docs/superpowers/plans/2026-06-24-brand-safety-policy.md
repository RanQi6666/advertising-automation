# Brand Safety Policy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a hard brand-safety gate that blocks ad packages related to gambling, betting, medicine, money, currency, discounts, low prices, free offers, and other price-advantage marketing terms before final return.

**Architecture:** Implement a focused backend policy module that scans text-like fields in ad generation payloads and returns a structured report. Reuse that module in prompt guardrails, final payload metadata, and `confirm_review` so the backend remains authoritative. Add a small frontend helper to summarize the report and disable/flag final return when a high-risk report is present.

**Tech Stack:** FastAPI, Pydantic, SQLAlchemy async sessions, pytest, React, TypeScript, Vite.

---

### Task 1: Backend Brand Safety Scanner

**Files:**
- Create: `backend/app/services/brand_safety_policy.py`
- Test: `tests/test_brand_safety_policy.py`

- [ ] **Step 1: Write failing scanner tests**

Add tests that call the intended scanner API directly:

```python
from backend.app.services.brand_safety_policy import (
    BRAND_SAFETY_PROMPT_GUARDRAILS,
    scan_brand_safety,
)


def test_brand_safety_scanner_blocks_price_promotion_terms() -> None:
    report = scan_brand_safety(
        {
            "creative_payload": {
                "message": "Get this low price deal today",
                "ads_name": "限时优惠",
            }
        }
    )

    assert report["status"] == "blocked"
    assert report["highest_severity"] == "high"
    assert {item["category"] for item in report["findings"]} >= {"price_promotion"}


def test_brand_safety_scanner_blocks_gambling_medicine_and_money_terms() -> None:
    report = scan_brand_safety(
        {
            "topic": "casino betting angle",
            "image_prompt": "show pills, cash, and a bank card",
        }
    )

    assert report["status"] == "blocked"
    assert {item["category"] for item in report["findings"]} >= {
        "gambling",
        "medicine",
        "money",
    }


def test_brand_safety_scanner_allows_neutral_value_language() -> None:
    report = scan_brand_safety(
        {
            "creative_payload": {
                "message": "A smooth daily experience with clear setup steps.",
                "ads_name": "Start with a simple guide",
                "description": "Learn more about daily use.",
            }
        }
    )

    assert report["status"] == "passed"
    assert report["findings"] == []


def test_brand_safety_prompt_guardrails_name_the_hard_bans() -> None:
    assert "gambling" in BRAND_SAFETY_PROMPT_GUARDRAILS
    assert "medicine" in BRAND_SAFETY_PROMPT_GUARDRAILS
    assert "discount" in BRAND_SAFETY_PROMPT_GUARDRAILS
    assert "low price" in BRAND_SAFETY_PROMPT_GUARDRAILS
```

- [ ] **Step 2: Verify scanner tests fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_brand_safety_policy.py -q`

Expected: FAIL because `backend.app.services.brand_safety_policy` does not exist.

- [ ] **Step 3: Implement the scanner**

Create `backend/app/services/brand_safety_policy.py` with:

```python
from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

BRAND_SAFETY_PROMPT_GUARDRAILS = """
Brand safety hard bans:
- Do not create or preserve gambling, betting, casino, lottery, jackpot, odds, poker, roulette, slot machine, or chip references.
- Do not create or preserve medicine, drug, pill, capsule, pharmacy, prescription, injection, treatment, cure, or medical-efficacy references.
- Do not create or preserve money, currency, cash, coin, bank card, wallet, payment, price, profit, income, investment, cashback, dollar, RMB, or currency-symbol references.
- Do not create or preserve price-advantage marketing language including discounts, low price, cheap, cheaper, free, sale, deal, promo, promotion, special price, save money, cost-effective, value-for-money, bargain, coupon, limited offer, or flash sale.
- For visual assets, do not show cash, coins, currency symbols, bank cards, wallets, price tags, discount stickers, sale badges, coupons, chips, casinos, slot machines, pills, capsules, medicine bottles, pharmacies, or injection devices.
- Rewrite risky operator input into neutral value language such as smoother experience, simpler setup, clearer content, daily use, quick start, stable and reliable, view details, try now, or learn more.
""".strip()

_POLICIES = {
    "gambling": (
        "赌博",
        "博彩",
        "下注",
        "投注",
        "赔率",
        "赌场",
        "老虎机",
        "轮盘",
        "扑克",
        "筹码",
        "彩票",
        "中奖",
        "casino",
        "betting",
        "jackpot",
        "lottery",
    ),
    "medicine": (
        "药品",
        "药物",
        "处方药",
        "药片",
        "胶囊",
        "注射器",
        "药瓶",
        "药房",
        "疗效",
        "治愈",
        "medicine",
        "drug",
        "pill",
        "capsule",
        "pharmacy",
    ),
    "money": (
        "现金",
        "货币",
        "硬币",
        "钞票",
        "银行卡",
        "钱包",
        "支付",
        "价格",
        "收益",
        "赚钱",
        "利润",
        "投资",
        "money",
        "cash",
        "coin",
        "price",
        "profit",
    ),
    "price_promotion": (
        "折扣",
        "低价",
        "优惠",
        "便宜",
        "促销",
        "特价",
        "免费",
        "低至",
        "仅需",
        "超值",
        "划算",
        "省钱",
        "性价比",
        "discount",
        "cheap",
        "free",
        "sale",
        "deal",
        "promo",
    ),
}


def scan_brand_safety(payload: Any) -> dict[str, Any]:
    findings = []
    for path, text in _walk_text(payload):
        for category, patterns in _COMPILED_POLICIES.items():
            for pattern in patterns:
                match = pattern.search(text)
                if match:
                    findings.append(
                        {
                            "category": category,
                            "severity": "high",
                            "field_path": path,
                            "matched_text": match.group(0),
                            "suggestion": _SUGGESTIONS[category],
                        }
                    )
                    break
    return {
        "status": "blocked" if findings else "passed",
        "highest_severity": "high" if findings else None,
        "findings": findings,
    }
```

Use concrete regex patterns for each category. Walk nested dict/list payloads and only scan strings, ints, floats, and bools as text; skip URL-like fields when the field path ends with `_url`, `url`, `link`, or contains `asset_url` so a URL path does not create false positives.

- [ ] **Step 4: Verify scanner tests pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_brand_safety_policy.py -q`

Expected: PASS.

### Task 2: Prompt Guardrails

**Files:**
- Modify: `backend/app/integrations/llm/compliance.py`
- Modify: `backend/app/integrations/image/volcengine_provider.py`
- Modify: `backend/app/services/video_service.py`
- Test: `tests/test_llm_compliance_prompt.py`
- Test: `tests/test_image_prompt_guardrails.py`
- Test: `tests/test_video_storyboard.py`

- [ ] **Step 1: Write failing prompt tests**

Extend tests to assert:

```python
from backend.app.services.brand_safety_policy import BRAND_SAFETY_PROMPT_GUARDRAILS


def test_meta_ad_compliance_prompt_includes_brand_safety_guardrails() -> None:
    assert BRAND_SAFETY_PROMPT_GUARDRAILS in META_AD_COMPLIANCE_SYSTEM_PROMPT
```

Add image prompt assertions that `_prompt_from_brief(brief)` includes `cash`, `bank card`, `discount`, `coupon`, `casino`, and `pills`.

Add video prompt assertions that `_storyboard_to_prompt(storyboard)` includes the same visual bans.

- [ ] **Step 2: Verify prompt tests fail**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_llm_compliance_prompt.py tests/test_image_prompt_guardrails.py tests/test_video_storyboard.py -q
```

Expected: FAIL because prompts do not include the new brand-safety guardrails yet.

- [ ] **Step 3: Add guardrails to prompts**

Import `BRAND_SAFETY_PROMPT_GUARDRAILS` into `compliance.py` and append it to `META_AD_COMPLIANCE_SYSTEM_PROMPT`.

Add the visual hard-ban sentence to image and video prompt construction:

```python
BRAND_SAFETY_VISUAL_BAN = (
    "Brand safety visual ban: no cash, coins, currency symbols, bank cards, wallets, "
    "price tags, discount stickers, sale badges, coupons, chips, casinos, slot machines, "
    "pills, capsules, medicine bottles, pharmacies, or injection devices."
)
```

- [ ] **Step 4: Verify prompt tests pass**

Run the same three test files.

Expected: PASS.

### Task 3: Backend Final Return Blocking

**Files:**
- Modify: `backend/app/services/ad_generation_service.py`
- Test: `tests/test_publishing_ad_generation.py`

- [ ] **Step 1: Write failing blocking tests**

Add a test that creates an ad generation job, tries to confirm a payload containing `低价优惠`, and expects `AppError` with a brand-safety message. Also assert the job is not marked `returned`.

Add a companion test that confirms a neutral payload and expects `returned`.

- [ ] **Step 2: Verify blocking test fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_publishing_ad_generation.py::test_confirm_review_blocks_brand_safety_risks -q`

Expected: FAIL because `confirm_review` currently accepts the risky payload.

- [ ] **Step 3: Implement blocking**

In `confirm_review`, after merging `payload.result_payload` and before setting `returned`, call `scan_brand_safety(result_payload)`.

If blocked:

```python
result_payload["review"] = {
    **review,
    "brand_safety": report,
}
job.result_payload = result_payload
job.metadata_json = {
    **(job.metadata_json or {}),
    "brand_safety_status": "blocked",
    "workflow_stage": "final_review",
}
await session.commit()
raise AppError("品牌安全检查未通过，请修改或重新生成后再确认回传。")
```

If passed, include `review.brand_safety = report` before marking `returned`.

- [ ] **Step 4: Verify blocking tests pass**

Run the targeted publishing tests.

Expected: PASS.

### Task 4: Frontend Report Display

**Files:**
- Create: `frontend/web-admin/src/lib/brandSafety.ts`
- Modify: `frontend/web-admin/src/App.tsx`
- Modify: `frontend/web-admin/src/styles.css`
- Test: `frontend/web-admin/tests/brandSafety.test.ts`

- [ ] **Step 1: Write frontend helper tests**

Add tests for:

```typescript
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import {
  brandSafetyReportFromPayload,
  brandSafetyBlocksReturn,
  brandSafetySummaryLabel,
} from "../src/lib/brandSafety.ts";

it("reads blocked brand safety reports from final payloads", () => {
  const report = brandSafetyReportFromPayload({
    review: {
      brand_safety: {
        status: "blocked",
        highest_severity: "high",
        findings: [{ category: "price_promotion", severity: "high", field_path: "creative_payload.message", matched_text: "优惠", suggestion: "强调体验" }],
      },
    },
  });
  assert.equal(brandSafetyBlocksReturn(report), true);
  assert.equal(brandSafetySummaryLabel(report), "品牌安全未通过");
});
```

- [ ] **Step 2: Verify frontend helper test fails**

Run: `npm test -- brandSafety.test.ts`

Expected: FAIL because the helper file does not exist.

- [ ] **Step 3: Implement frontend helper and UI**

Create helper functions that read `payload.review.brand_safety`, normalize findings, and tell the UI whether return should be blocked.

In `WorkflowView`, parse `finalPayloadDraft` when present; otherwise read `selectedJob.result_payload`. Display a compact brand-safety card inside the final package panel. Disable confirm return if a high-risk report exists.

- [ ] **Step 4: Verify frontend tests and build pass**

Run:

```powershell
npm test
npm run build
```

Expected: PASS.

### Task 5: Full Verification

**Files:**
- All files touched above.

- [ ] **Step 1: Run backend lint**

Run: `.venv\Scripts\python.exe -m ruff check backend tests`

Expected: PASS.

- [ ] **Step 2: Run backend tests**

Run: `.venv\Scripts\python.exe -m pytest`

Expected: PASS.

- [ ] **Step 3: Run frontend tests and build**

Run:

```powershell
cd frontend\web-admin
npm test
npm run build
```

Expected: PASS.

- [ ] **Step 4: Inspect Git status**

Run: `git status -sb`

Expected: only planned files modified/added; no `AGENTS.md`, `.env`, or `.env.production`.

---

## Self-Review

- Spec coverage: The plan covers generation prompt guardrails, text-layer scanning, confirm-return blocking, frontend display, and tests. It intentionally leaves real image/video multimodal review for the next phases.
- Empty-content scan: No unresolved gaps remain. The plan names the concrete policy categories and seed terms, while the implementation can add equivalent regex variants.
- Type consistency: The report shape is consistently `status`, `highest_severity`, and `findings`; frontend reads `review.brand_safety`; backend writes the same location.
