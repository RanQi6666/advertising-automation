const LOCAL_HOSTNAMES = new Set(["localhost", "127.0.0.1", "0.0.0.0", "::1", "[::1]"]);

export function displayAssetUrl(url: string | null | undefined, pageHref = currentPageHref()): string {
  if (!url) return "";

  let parsedUrl: URL;
  try {
    parsedUrl = new URL(url);
  } catch {
    return url;
  }

  if (!parsedUrl.pathname.startsWith("/storage/")) {
    return url;
  }

  const pageUrl = pageHref ? safeUrl(pageHref) : null;
  if (!pageUrl || !isLocalHostname(pageUrl.hostname)) {
    return url;
  }

  const localOrigin =
    pageUrl.port === "5173" ? `${pageUrl.protocol}//${pageUrl.hostname}:8001` : pageUrl.origin;
  return `${localOrigin}${parsedUrl.pathname}${parsedUrl.search}${parsedUrl.hash}`;
}

function currentPageHref(): string | undefined {
  return globalThis.location?.href;
}

function safeUrl(url: string): URL | null {
  try {
    return new URL(url);
  } catch {
    return null;
  }
}

function isLocalHostname(hostname: string): boolean {
  return LOCAL_HOSTNAMES.has(hostname);
}
