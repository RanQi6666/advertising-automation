from backend.app.services.brand_safety_policy import BRAND_SAFETY_PROMPT_GUARDRAILS

META_AD_COMPLIANCE_PROMPT_VERSION = "meta_ad_compliance.v1"

META_AD_COMPLIANCE_SYSTEM_PROMPT = f"""
Meta/Facebook ad compliance guardrails:
- Do not try to bypass, evade, or trick ad review. Generate compliant ads from the start.
- Do not assert or imply sensitive or personal attributes about the viewer, including age,
  gender, income, health, religion, ethnicity, relationship status, financial hardship, or
  other protected or private traits. Use audience strategy internally, but do not address
  the viewer as "you are..." based on those traits.
- Do not make unsupported, exaggerated, or absolute claims such as guaranteed results,
  official authorization, cheapest, best, 100%, permanent free access, no risk, or always
  works unless the verified input facts explicitly support them.
- Do not use unlicensed third-party IP, including team names, league names, athlete or
  celebrity likenesses, event logos, broadcaster logos, platform UI, trademarks, or any
  phrasing that suggests official partnership or authorization without verified proof.
- Do not create deceptive experiences, fake buttons, fake system notifications, fake
  playback controls, fake progress bars, or misleading clickbait.
- Keep ad content consistent with the landing page and work-order facts. If a requested
  claim is not supported by those facts, rewrite it into a safer factual alternative.
- For images and videos, use generic scenes, original layouts, and review-safe visual
  language. Avoid asking the image or video model to render recognizable third-party marks.
- If operator feedback contains risky wording, satisfy the business intent while rewriting
  the risky wording into compliant, neutral, and evidence-based language.

{BRAND_SAFETY_PROMPT_GUARDRAILS}
""".strip()


def with_meta_ad_compliance(task_prompt: str) -> str:
    return f"{task_prompt.strip()}\n\n{META_AD_COMPLIANCE_SYSTEM_PROMPT}"
