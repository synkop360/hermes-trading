import os
import xml.etree.ElementTree as ET
from typing import Any

import httpx

from hermes_trading.adapters import require_schema

SCHEMA_VERSION = 1


def parse_rss_headlines(xml_text: str) -> list[str]:
    root = ET.fromstring(xml_text)
    headlines = []
    for item in root.findall(".//item"):
        title = item.findtext("title")
        if title:
            headlines.append(title.strip())
    return headlines


async def fetch() -> dict[str, Any]:
    api_key = os.getenv("NEWS_API_KEY", "")
    if api_key:
        url = "https://newsapi.org/v2/everything"
        params = {"q": "bitcoin OR ethereum OR crypto", "language": "en", "pageSize": 10, "apiKey": api_key}
        source = "newsapi"
    else:
        url = "https://www.coindesk.com/arc/outboundfeeds/rss/"
        params = {}
        source = "coindesk-rss"

    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        response = await client.get(url, params=params)
        response.raise_for_status()
        if api_key:
            data = response.json()
            items = data.get("articles", [])
            headlines = [item["title"] for item in items[:10] if item.get("title")]
        else:
            headlines = parse_rss_headlines(response.text)[:10]

    payload = {
        "schema_version": SCHEMA_VERSION,
        "source": source,
        "headline_count": len(headlines),
        "headlines": headlines,
    }
    return require_schema(payload, SCHEMA_VERSION)
