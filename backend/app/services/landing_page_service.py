import re
from html import unescape
from html.parser import HTMLParser

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.errors import AppError
from backend.app.db.models.campaign import Campaign
from backend.app.db.models.landing_page_snapshot import LandingPageSnapshot
from backend.app.schemas.landing_page import LandingPageAnalyzeRequest
from backend.app.services.utils import get_required


class LandingPageService:
    async def analyze_campaign_landing_page(
        self,
        session: AsyncSession,
        campaign_id: str,
        payload: LandingPageAnalyzeRequest | None = None,
    ) -> LandingPageSnapshot:
        payload = payload or LandingPageAnalyzeRequest()
        campaign = await get_required(session, Campaign, campaign_id)
        url = str(payload.url) if payload.url else _landing_url_from_campaign(campaign)
        if not url:
            raise AppError("No landing page URL found for this campaign.")

        if not payload.force_refresh:
            latest = await self.get_latest_snapshot(session, campaign_id)
            if latest and latest.url == url and latest.status == "fetched":
                return latest

        snapshot = await self._fetch_snapshot(
            campaign=campaign,  # type: ignore[arg-type]
            url=url,
            metadata=payload.metadata_json,
        )
        session.add(snapshot)

        campaign.metadata_json = {
            **campaign.metadata_json,
            "landing_page": snapshot_to_context(snapshot),
        }
        await session.commit()
        await session.refresh(snapshot)
        return snapshot

    async def get_latest_snapshot(
        self,
        session: AsyncSession,
        campaign_id: str,
    ) -> LandingPageSnapshot | None:
        result = await session.execute(
            select(LandingPageSnapshot)
            .where(LandingPageSnapshot.campaign_id == campaign_id)
            .order_by(LandingPageSnapshot.created_at.desc())
            .limit(1)
        )
        return result.scalars().first()

    async def list_snapshots(
        self,
        session: AsyncSession,
        campaign_id: str,
        limit: int,
        offset: int,
    ) -> list[LandingPageSnapshot]:
        result = await session.execute(
            select(LandingPageSnapshot)
            .where(LandingPageSnapshot.campaign_id == campaign_id)
            .order_by(LandingPageSnapshot.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    async def _fetch_snapshot(
        self,
        campaign: Campaign,
        url: str,
        metadata: dict,
    ) -> LandingPageSnapshot:
        try:
            async with httpx.AsyncClient(
                follow_redirects=True,
                timeout=20,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (compatible; AdvertisingAutomationBot/0.1; +https://localhost)"
                    )
                },
            ) as client:
                response = await client.get(url)
            parser = LandingPageHTMLParser()
            parser.feed(response.text[:1_000_000])
            text_content = normalize_text(parser.text_content)
            extracted_data = {
                "canonical_url": parser.canonical_url,
                "headings": parser.headings[:20],
                "links": parser.links[:50],
                "text_excerpt": text_content[:3000],
            }
            return LandingPageSnapshot(
                campaign_id=campaign.id,
                work_order_id=campaign.work_order_id,
                url=str(response.url),
                status="fetched" if not response.is_error else "failed",
                http_status=response.status_code,
                title=normalize_text(parser.title)[:512] if parser.title else None,
                description=normalize_text(parser.description),
                text_content=text_content[:20000],
                extracted_data=extracted_data,
                error_message=response.text[:1000] if response.is_error else None,
                metadata_json=metadata,
            )
        except httpx.HTTPError as exc:
            return LandingPageSnapshot(
                campaign_id=campaign.id,
                work_order_id=campaign.work_order_id,
                url=url,
                status="failed",
                error_message=str(exc),
                metadata_json=metadata,
            )


class LandingPageHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title: str | None = None
        self.description: str | None = None
        self.canonical_url: str | None = None
        self.headings: list[str] = []
        self.links: list[dict[str, str | None]] = []
        self.text_parts: list[str] = []
        self._current_tag: str | None = None
        self._skip_depth = 0
        self._current_link: dict[str, str | None] | None = None
        self._current_link_text: list[str] = []

    @property
    def text_content(self) -> str:
        return " ".join(self.text_parts)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = dict(attrs)
        if tag in {"script", "style", "noscript"}:
            self._skip_depth += 1
        self._current_tag = tag

        if tag == "meta":
            name = (attrs_dict.get("name") or attrs_dict.get("property") or "").lower()
            content = attrs_dict.get("content")
            if name in {"description", "og:description"} and content and not self.description:
                self.description = content

        if tag == "link" and (attrs_dict.get("rel") or "").lower() == "canonical":
            self.canonical_url = attrs_dict.get("href")

        if tag == "a":
            self._current_link = {"href": attrs_dict.get("href"), "text": None}
            self._current_link_text = []

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self._skip_depth:
            self._skip_depth -= 1
        if tag == "a" and self._current_link is not None:
            self._current_link["text"] = normalize_text(" ".join(self._current_link_text))[:200]
            if self._current_link.get("href") or self._current_link.get("text"):
                self.links.append(self._current_link)
            self._current_link = None
            self._current_link_text = []
        self._current_tag = None

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        text = normalize_text(data)
        if not text:
            return

        if self._current_tag == "title" and not self.title:
            self.title = text
        elif self._current_tag in {"h1", "h2", "h3"}:
            self.headings.append(text[:300])

        if self._current_link is not None:
            self._current_link_text.append(text)

        self.text_parts.append(text)


def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", unescape(value)).strip()


def _landing_url_from_campaign(campaign: Campaign) -> str | None:
    metadata = campaign.metadata_json or {}
    landing_page = metadata.get("landing_page") or {}
    if landing_page.get("url"):
        return landing_page["url"]

    work_order = metadata.get("work_order") or {}
    parsed_fields = work_order.get("parsed_fields") or {}
    return work_order.get("landing_url") or parsed_fields.get("landing_url")


def snapshot_to_context(snapshot: LandingPageSnapshot) -> dict:
    return {
        "snapshot_id": snapshot.id,
        "url": snapshot.url,
        "status": snapshot.status,
        "http_status": snapshot.http_status,
        "title": snapshot.title,
        "description": snapshot.description,
        "text_excerpt": (snapshot.text_content or "")[:3000],
        "extracted_data": snapshot.extracted_data,
        "fetched_at": snapshot.fetched_at.isoformat() if snapshot.fetched_at else None,
    }
