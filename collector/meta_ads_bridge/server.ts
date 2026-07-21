import { createServer } from "node:http";
import { scrapeAds } from "../../src/lib/scraper";
import type { Ad } from "../../src/types/ads";
import type { CollectRequest, CollectorAd } from "./contracts";

const MAX_LIMIT = 50;
const port = Number(process.env.PORT ?? "8090");

function normalize(ad: Ad): CollectorAd {
  return {
    ad_library_id: ad.id,
    advertiser_name: ad.advertiser_name || null,
    status: ad.status || null,
    days_running: typeof ad.days_running === "number" ? ad.days_running : null,
    text_variants: Array.isArray(ad.body_variants) ? ad.body_variants : [],
    headline: ad.headline || null,
    cta_text: ad.cta_text || null,
    landing_url: ad.link_url || null,
    video_url: ad.video_urls?.[0] || null,
    thumbnail_url: ad.media_urls?.[0] || null,
    duration_seconds: null,
    platforms: Array.isArray(ad.platforms) ? ad.platforms : [],
    ad_snapshot_url: ad.ad_snapshot_url || null,
    reported_spend_range: {
      min: ad.spend_min ?? null,
      max: ad.spend_max ?? null,
      currency: ad.spend_currency ?? null,
    },
  };
}

function sendLine(response: import("node:http").ServerResponse, value: unknown): void {
  response.write(`${JSON.stringify(value)}\n`);
}

function parseRequest(body: unknown): CollectRequest | null {
  if (!body || typeof body !== "object") return null;
  const value = body as Record<string, unknown>;
  const request_id = String(value.request_id || "").trim();
  const query = String(value.query || "").trim();
  const country = String(value.country || "").trim().toUpperCase();
  const limit = Math.min(Math.max(Number(value.limit) || MAX_LIMIT, 1), MAX_LIMIT);
  if (!request_id || !query || !country) return null;
  return { request_id, query, country, limit, active_only: true, video_only: true };
}

createServer(async (request, response) => {
  if (request.method === "GET" && request.url === "/health") {
    response.writeHead(200, { "content-type": "application/json" });
    response.end(JSON.stringify({ status: "ok", upstream_commit: process.env.META_ADS_SCRAPER_REF }));
    return;
  }
  if (request.method !== "POST" || request.url !== "/collect") {
    response.writeHead(404).end();
    return;
  }
  const chunks: Buffer[] = [];
  for await (const chunk of request) chunks.push(Buffer.from(chunk));
  let input: CollectRequest | null = null;
  try { input = parseRequest(JSON.parse(Buffer.concat(chunks).toString("utf8"))); } catch { input = null; }
  if (!input) {
    response.writeHead(400, { "content-type": "application/json" });
    response.end(JSON.stringify({ error: "invalid collect request" }));
    return;
  }
  response.writeHead(200, {
    "content-type": "application/x-ndjson; charset=utf-8",
    "cache-control": "no-store",
  });
  let count = 0;
  try {
    for await (const batch of scrapeAds({
      keyword: input.query,
      country: input.country,
      status: "ACTIVE",
      ad_type: "video",
      limit: input.limit,
      fetch_details: false,
    }, input.request_id)) {
      for (const ad of batch) {
        sendLine(response, { type: "ad", ad: normalize(ad) });
        count += 1;
      }
    }
    sendLine(response, { type: "done", collected_count: count });
  } catch (error) {
    sendLine(response, { type: "error", message: error instanceof Error ? error.message.slice(0, 300) : "collector failed" });
  } finally {
    response.end();
  }
}).listen(port, "0.0.0.0", () => console.log(`meta ads collector bridge listening on ${port}`));
