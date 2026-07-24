#!/usr/bin/env python3
"""Search multiple engines in parallel via stealthy-auto-browse.

Usage:
    python websearch.py "your search query"

Environment:
    STEALTHY_AUTO_BROWSE_URL  Base URL (default: http://localhost:8080)
    WEBSEARCH_ENGINES         Comma-separated engines (default: brave,google,bing)
    AUTH_TOKEN                   Bearer token if the server requires auth
    USER_AGENT                Custom User-Agent for direct HTTP requests
                              (default: real Chrome UA)

In cluster mode, each engine gets its own browser instance (true parallelism).
In single mode, the server serializes requests (runs sequentially).

Requirements: pip install requests beautifulsoup4
"""

from __future__ import annotations

import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup

BASE_URL = os.environ.get("STEALTHY_AUTO_BROWSE_URL", "http://localhost:8080")
ENGINES = [
    e.strip()
    for e in os.environ.get("WEBSEARCH_ENGINES", "brave,google,bing").split(",")
    if e.strip()
]
AUTH_TOKEN = os.environ.get("AUTH_TOKEN", "").strip() or None
USER_AGENT = os.environ.get(
    "USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
)

ENGINE_URLS = {
    "brave": "https://search.brave.com/search?q={q}",
    "google": "https://www.google.com/search?q={q}",
    "bing": "https://www.bing.com/search?q={q}",
}


def _headers() -> dict[str, str]:
    h: dict[str, str] = {"Content-Type": "application/json"}
    if AUTH_TOKEN:
        h["Authorization"] = f"Bearer {AUTH_TOKEN}"
    return h


def _post(data: dict, instance_id: str | None = None) -> dict:
    h = _headers()
    if instance_id:
        h["Cookie"] = f"INSTANCEID={instance_id}"
    resp = requests.post(BASE_URL, json=data, headers=h, timeout=60)
    return resp.json()


def _get_instance_id() -> str | None:
    """Get a sticky session ID (works in cluster mode)."""
    h = _headers()
    try:
        resp = requests.post(
            BASE_URL, json={"action": "ping"}, headers=h, timeout=30
        )
        for cookie in resp.cookies:
            if cookie.name == "INSTANCEID":
                return cookie.value
    except Exception:
        pass
    return None


def _parse_google_ai_overview(soup: BeautifulSoup) -> str | None:
    """Extract Google AI Overview text if present."""
    parts = []
    for el in soup.select(".Y3BBE"):
        text = el.get_text(strip=True)
        if text and len(text) > 20:
            parts.append(text)
    if not parts:
        return None
    return "\n\n".join(parts)


def _parse_google(soup: BeautifulSoup) -> list[dict]:
    results = []
    seen_urls: set[str] = set()
    for h3 in soup.select("a h3"):
        a = h3.find_parent("a")
        if not a:
            continue
        href = str(a.get("href", ""))
        if not href.startswith("http") or href in seen_urls:
            continue
        seen_urls.add(href)
        # Walk up to find a container with a snippet
        container = a.parent
        for _ in range(5):
            if not container or not container.parent:
                break
            container = container.parent
        snippet = ""
        if container:
            for sel in ["[data-sncf]", ".VwiC3b", "div > span > span"]:
                el = container.select_one(sel)
                if el and el.get_text(strip=True):
                    snippet = el.get_text(strip=True)
                    break
        results.append(
            {
                "title": h3.get_text(strip=True),
                "url": href,
                "snippet": snippet,
            }
        )
    return results


def _parse_brave_ai_overview(soup: BeautifulSoup) -> str | None:
    """Extract Brave AI summary text if present."""
    el = soup.select_one(".chatllm-content")
    if not el:
        return None
    text = el.get_text(strip=True)
    return text if len(text) > 20 else None


def _parse_brave(soup: BeautifulSoup) -> list[dict]:
    results = []
    for s in soup.select("[data-type='web']"):
        a = s.select_one("a[href]")
        if not a:
            continue
        href = str(a.get("href", ""))
        if not href.startswith("http"):
            continue
        title_el = s.select_one(".title.search-snippet-title")
        desc_el = s.select_one(".generic-snippet .content")
        results.append(
            {
                "title": title_el.get_text(strip=True) if title_el else a.get_text(strip=True),
                "url": href,
                "snippet": desc_el.get_text(strip=True) if desc_el else "",
            }
        )
    return results


def _parse_bing_ai_overview(soup: BeautifulSoup) -> str | None:
    """Extract Bing AI/Copilot summary if present."""
    # Bing's AI answers appear in various containers
    for sel in [".b_ans .b_paractl", "#b_results .b_ans .ac_content", ".rai_content"]:
        el = soup.select_one(sel)
        if el:
            text = el.get_text(strip=True)
            if len(text) > 20:
                return text
    return None


def _parse_bing(soup: BeautifulSoup) -> list[dict]:
    results = []
    for el in soup.select("#b_results .b_algo"):
        a = el.select_one("h2 a")
        if not a:
            continue
        href = str(a.get("href", ""))
        if not href.startswith("http"):
            continue
        p = el.select_one(".b_caption p, .b_lineclamp2")
        results.append(
            {
                "title": a.get_text(strip=True),
                "url": href,
                "snippet": p.get_text(strip=True) if p else "",
            }
        )
    return results


PARSERS = {
    "google": (_parse_google, _parse_google_ai_overview),
    "brave": (_parse_brave, _parse_brave_ai_overview),
    "bing": (_parse_bing, _parse_bing_ai_overview),
}


def search_engine(engine: str, query: str) -> dict:
    """Run a search on a single engine via the browser API."""
    if engine not in ENGINE_URLS:
        return {"engine": engine, "query": query, "ai_overview": None, "search_results": [], "error": "unknown engine"}

    url = ENGINE_URLS[engine].format(q=quote_plus(query))
    instance_id = _get_instance_id()

    try:
        # Run all steps as a single atomic script
        resp = _post(
            {
                "action": "run_script",
                "name": f"websearch_{engine}",
                "steps": [
                    {"action": "goto", "url": url, "wait_until": "domcontentloaded"},
                    {"action": "sleep", "duration": 2},
                    {"action": "get_html", "output_id": "html"},
                ],
            },
            instance_id,
        )
        if not resp.get("success"):
            return {"engine": engine, "query": query, "ai_overview": None, "search_results": [], "error": resp.get("error", "script failed")}

        html = resp.get("data", {}).get("outputs", {}).get("html", {}).get("html", "")
        if not html:
            return {"engine": engine, "query": query, "ai_overview": None, "search_results": [], "error": "no html returned"}
        soup = BeautifulSoup(html, "html.parser")

        # Parse results and AI overview
        entry = PARSERS.get(engine)
        if not entry:
            return {"engine": engine, "query": query, "ai_overview": None, "search_results": [], "error": "no parser"}

        parse_results, parse_ai = entry
        search_results = parse_results(soup)
        ai_overview = parse_ai(soup)

        return {"engine": engine, "query": query, "ai_overview": ai_overview, "search_results": search_results}

    except Exception as e:
        return {"engine": engine, "query": query, "ai_overview": None, "search_results": [], "error": str(e)}


def main() -> None:
    if len(sys.argv) < 2 or not sys.argv[1].strip():
        print("Usage: python websearch.py \"search query\"", file=sys.stderr)
        sys.exit(1)

    query = sys.argv[1].strip()

    print(f"Searching for: {query}", file=sys.stderr)
    print(f"Engines: {', '.join(ENGINES)}", file=sys.stderr)
    print(file=sys.stderr)

    all_results = []

    with ThreadPoolExecutor(max_workers=len(ENGINES)) as pool:
        futures = {
            pool.submit(search_engine, engine, query): engine for engine in ENGINES
        }
        for future in as_completed(futures):
            engine = futures[future]
            try:
                result = future.result()
            except Exception as e:
                result = {"engine": engine, "query": query, "results": [], "error": str(e)}
            count = len(result.get("search_results", []))
            ai = "yes" if result.get("ai_overview") else "no"
            err = result.get("error", "")
            if err:
                print(f"  {engine}: error — {err}", file=sys.stderr)
            else:
                print(f"  {engine}: {count} results, AI overview: {ai}", file=sys.stderr)
            all_results.append(result)

    # Sort by engine name for consistent output
    all_results.sort(key=lambda r: r["engine"])

    print(file=sys.stderr)
    print(json.dumps(all_results, indent=2))


if __name__ == "__main__":
    main()
