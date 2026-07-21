FROM node:22-bookworm-slim

ARG META_ADS_SCRAPER_REF=5a83208e4e69e66ba5ba1eb9817771043ac169ed
LABEL org.opencontainers.image.source="https://github.com/athm793/meta-ads-scraper" \
      org.opencontainers.image.revision="${META_ADS_SCRAPER_REF}"

RUN for attempt in 1 2 3; do \
      apt-get update && apt-get install -y --no-install-recommends git ca-certificates && break; \
      if [ "$attempt" = 3 ]; then exit 1; fi; \
      rm -rf /var/lib/apt/lists/* && sleep 3; \
    done \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/meta-ads-scraper
RUN git clone https://github.com/athm793/meta-ads-scraper.git . \
    && git checkout "${META_ADS_SCRAPER_REF}" \
    && npm ci

# Playwright installs OS packages through apt; transient Debian CDN 502s are retried.
RUN for attempt in 1 2 3; do \
      npx playwright install --with-deps chromium && break; \
      if [ "$attempt" = 3 ]; then exit 1; fi; \
      apt-get clean && rm -rf /var/lib/apt/lists/* && sleep 5; \
    done

COPY collector/meta_ads_bridge/package.json collector/meta_ads_bridge/package.json
COPY collector/meta_ads_bridge/contracts.ts collector/meta_ads_bridge/contracts.ts
COPY collector/meta_ads_bridge/server.ts collector/meta_ads_bridge/server.ts
RUN npm install --no-save --no-package-lock --prefix collector/meta_ads_bridge

ENV NODE_ENV=production \
    PORT=8090 \
    META_ADS_SCRAPER_REF=${META_ADS_SCRAPER_REF}
EXPOSE 8090
CMD ["./collector/meta_ads_bridge/node_modules/.bin/tsx", "collector/meta_ads_bridge/server.ts"]