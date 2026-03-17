"""CoinDesk RSS feed fetcher — async."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime

import aiohttp
import structlog

log = structlog.get_logger()

_TIMEOUT = aiohttp.ClientTimeout(total=15, connect=10)
RSS_URL = "https://www.coindesk.com/arc/outboundfeeds/rss/"


async def fetch_crypto_news(limit: int = 12, hours: int = 6) -> list[dict]:
    """Fetch recent crypto news from CoinDesk RSS feed."""
    try:
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
            async with session.get(RSS_URL) as resp:
                resp.raise_for_status()
                content = await resp.read()

        root = ET.fromstring(content)
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        results = []

        for item in root.findall(".//item"):
            title = item.findtext("title", "")
            url = item.findtext("link", "")
            pub_raw = item.findtext("pubDate", "")
            try:
                pub_time = parsedate_to_datetime(pub_raw)
                if pub_time < cutoff:
                    continue
            except Exception:
                pass
            results.append({"title": title, "url": url, "source": "CoinDesk"})
            if len(results) >= limit:
                break

        return results
    except Exception as e:
        log.error("coindesk_fetch_error", error=str(e))
        return [{"title": f"News fetch error: {e}", "url": "", "source": ""}]
