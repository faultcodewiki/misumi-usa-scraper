from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote_plus, unquote, urlparse

from apify import Actor
from bs4 import BeautifulSoup
from playwright.async_api import Page, Response, async_playwright

BASE_URL = "https://us.misumi-ec.com"
BLOCKED_RESOURCE_TYPES = {"image", "media", "font"}
BLOCKED_DOMAINS = (
    "googletagmanager.com",
    "google-analytics.com",
    "doubleclick.net",
    "facebook.net",
    "criteo.com",
    "bing.com",
    "clarity.ms",
    "hotjar.com",
)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def clean_text(val: Any) -> str:
    if val is None:
        return ""
    raw = str(val)
    if "<" in raw and ">" in raw:
        raw = BeautifulSoup(raw, "lxml").get_text(" ", strip=True)
    return re.sub(r"\s+", " ", raw).strip()


def normalize_url(url: Any) -> str:
    s = clean_text(url)
    if not s:
        return ""
    if s.startswith("//"):
        return f"https:{s}"
    if s.startswith("/"):
        return f"{BASE_URL}{s}"
    return s


def parse_price(val: Any) -> float | None:
    if val is None or isinstance(val, bool):
        return None
    if isinstance(val, (int, float)):
        return round(float(val), 4) if float(val) > 0 else None
    m = re.search(r"([0-9,]+(?:\.[0-9]+)?)", str(val))
    if not m:
        return None
    try:
        num = float(m.group(1).replace(",", ""))
        return round(num, 4) if num > 0 else None
    except ValueError:
        return None


def parse_days(val: Any) -> int | None:
    if val is None or isinstance(val, bool):
        return None
    if isinstance(val, (int, float)):
        d = int(val)
        return d if 0 <= d < 90 else None
    s = str(val).strip().lower()
    if not s:
        return None
    if "same day" in s or "same-day" in s or s == "0":
        return 0
    m = re.search(r"(\d+)", s)
    if m:
        d = int(m.group(1))
        return d if 0 <= d < 90 else None
    return None


def parse_int(val: Any, default: int = 1) -> int:
    if val is None or isinstance(val, bool):
        return default
    m = re.search(r"(\d+)", str(val))
    return max(1, int(m.group(1))) if m else default


def to_playwright_proxy(proxy_url: str | None) -> dict[str, str] | None:
    if not proxy_url:
        return None
    parsed = urlparse(proxy_url)
    if not parsed.hostname or not parsed.port:
        return None
    cfg: dict[str, str] = {"server": f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"}
    if parsed.username:
        cfg["username"] = unquote(parsed.username)
    if parsed.password:
        cfg["password"] = unquote(parsed.password)
    return cfg


def walk_find_lists(obj: Any, target_key: str, depth: int = 0) -> list[list[Any]]:
    found: list[list[Any]] = []
    if depth > 6:
        return found
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == target_key and isinstance(v, list):
                found.append(v)
            elif isinstance(v, (dict, list)):
                found.extend(walk_find_lists(v, target_key, depth + 1))
    elif isinstance(obj, list):
        for item in obj:
            if isinstance(item, (dict, list)):
                found.extend(walk_find_lists(item, target_key, depth + 1))
    return found


def build_specs_summary(entry: dict[str, Any]) -> str:
    parts: list[str] = []
    catch_copy = clean_text(entry.get("catchCopy"))
    if catch_copy:
        parts.append(catch_copy)
    for key in ("specList", "specValueList", "mainSpecList"):
        spec_list = entry.get(key)
        if isinstance(spec_list, list):
            for sp in spec_list[:8]:
                if isinstance(sp, dict):
                    s_name = clean_text(sp.get("specName") or sp.get("name"))
                    s_val = clean_text(
                        sp.get("specValueDisp") or sp.get("specValue") or sp.get("value")
                    )
                    if s_name and s_val:
                        parts.append(f"{s_name}: {s_val}")
                    elif s_val:
                        parts.append(s_val)
    return " | ".join(parts)[:500]


def extract_category(entry: dict[str, Any]) -> str:
    cat_list = entry.get("categoryList")
    if isinstance(cat_list, list) and cat_list:
        names = [
            clean_text(c.get("categoryName") if isinstance(c, dict) else c)
            for c in cat_list
        ]
        names = [n for n in names if n]
        if names:
            return " - ".join(names)
    return clean_text(entry.get("categoryName") or entry.get("largeCategoryName"))


def extract_image(entry: dict[str, Any]) -> str:
    for key in ("mainImageUrl", "productImageUrl", "imageUrl", "seriesImageUrl"):
        val = entry.get(key)
        if isinstance(val, str) and val.strip():
            return normalize_url(val)
        if isinstance(val, list) and val:
            first = val[0]
            if isinstance(first, str):
                return normalize_url(first)
            if isinstance(first, dict):
                return normalize_url(first.get("url") or first.get("imageUrl"))
    return ""


def parse_captured_jsons(captured: list[Any], query_source: str) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    price_map: dict[str, dict[str, Any]] = {}

    for payload in captured:
        for plist in walk_find_lists(payload, "priceList"):
            for p in plist:
                if isinstance(p, dict) and p.get("partNumber"):
                    price_map[clean_text(p["partNumber"])] = p

    for payload in captured:
        for pn_list in walk_find_lists(payload, "partNumberList"):
            for entry in pn_list:
                if not isinstance(entry, dict):
                    continue
                part_num = clean_text(entry.get("partNumber"))
                series_code = clean_text(entry.get("seriesCode"))
                p_info = price_map.get(part_num, {})
                prod_name = clean_text(
                    entry.get("seriesName") or entry.get("productName") or part_num
                )
                if not prod_name or not (part_num or series_code):
                    continue

                unit_price = (
                    parse_price(p_info.get("campaignUnitPrice"))
                    or parse_price(p_info.get("unitPrice"))
                    or parse_price(p_info.get("standardUnitPrice"))
                    or parse_price(entry.get("campaignUnitPrice"))
                    or parse_price(entry.get("unitPrice"))
                    or parse_price(entry.get("standardUnitPrice"))
                    or parse_price(entry.get("minStandardUnitPrice"))
                )
                days = parse_days(
                    p_info.get("daysToShip")
                    if p_info.get("daysToShip") is not None
                    else p_info.get("standardDaysToShip")
                    if p_info.get("standardDaysToShip") is not None
                    else entry.get("daysToShip")
                    if entry.get("daysToShip") is not None
                    else entry.get("standardDaysToShip")
                )
                moq = parse_int(
                    p_info.get("minOrderQty") or entry.get("minOrderQty"), default=1
                )
                detail_url = (
                    f"{BASE_URL}/vona2/detail/{series_code}/?HissuCode={quote_plus(part_num)}"
                    if series_code and part_num
                    else normalize_url(entry.get("url") or f"/vona2/detail/{series_code}/")
                )
                results.append(
                    {
                        "querySource": query_source,
                        "partNumber": part_num or series_code,
                        "seriesCode": series_code,
                        "productName": prod_name,
                        "brand": clean_text(
                            entry.get("brandName") or entry.get("makerName") or "MISUMI"
                        ),
                        "category": extract_category(entry),
                        "unitPriceUSD": unit_price,
                        "currency": "USD",
                        "daysToShip": days,
                        "inStock": bool(days is not None and days <= 5),
                        "minOrderQty": moq,
                        "cadAvailable": bool(
                            entry.get("cadTypeList") or entry.get("cadType")
                        ),
                        "specsSummary": build_specs_summary(entry),
                        "imageUrl": extract_image(entry),
                        "url": detail_url,
                        "scrapedAt": now_iso(),
                    }
                )

        for s_list in walk_find_lists(payload, "seriesList"):
            for entry in s_list:
                if not isinstance(entry, dict):
                    continue
                series_code = clean_text(entry.get("seriesCode"))
                prod_name = clean_text(entry.get("seriesName") or entry.get("productName"))
                if not series_code or not prod_name:
                    continue
                part_num = clean_text(
                    entry.get("partNumber")
                    or entry.get("partNumberPrefix")
                    or series_code
                )
                unit_price = (
                    parse_price(entry.get("minCampaignUnitPrice"))
                    or parse_price(entry.get("minStandardUnitPrice"))
                    or parse_price(entry.get("standardUnitPrice"))
                    or parse_price(entry.get("unitPrice"))
                )
                days = parse_days(
                    entry.get("minStandardDaysToShip")
                    if entry.get("minStandardDaysToShip") is not None
                    else entry.get("standardDaysToShip")
                    if entry.get("standardDaysToShip") is not None
                    else entry.get("daysToShip")
                )
                results.append(
                    {
                        "querySource": query_source,
                        "partNumber": part_num,
                        "seriesCode": series_code,
                        "productName": prod_name,
                        "brand": clean_text(
                            entry.get("brandName") or entry.get("makerName") or "MISUMI"
                        ),
                        "category": extract_category(entry),
                        "unitPriceUSD": unit_price,
                        "currency": "USD",
                        "daysToShip": days,
                        "inStock": bool(days is not None and days <= 5),
                        "minOrderQty": parse_int(entry.get("minOrderQty"), default=1),
                        "cadAvailable": bool(
                            entry.get("cadTypeList") or entry.get("cadType")
                        ),
                        "specsSummary": build_specs_summary(entry),
                        "imageUrl": extract_image(entry),
                        "url": f"{BASE_URL}/vona2/detail/{series_code}/",
                        "scrapedAt": now_iso(),
                    }
                )
    return results


def parse_rendered_dom(html: str, current_url: str, query_source: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "lxml")
    items: list[dict[str, Any]] = []
    seen_codes: set[str] = set()

    for script in soup.select('script[type="application/ld+json"]'):
        try:
            ld = json.loads(script.string or "")
            nodes = ld if isinstance(ld, list) else [ld]
            for node in nodes:
                if isinstance(node, dict) and node.get("@type") == "Product":
                    name = clean_text(node.get("name"))
                    offers = node.get("offers") or {}
                    if isinstance(offers, list):
                        offers = offers[0] if offers else {}
                    price = parse_price(offers.get("price") or offers.get("lowPrice"))
                    sku = clean_text(node.get("sku") or node.get("mpn") or query_source)
                    brand_obj = node.get("brand")
                    brand = (
                        clean_text(brand_obj.get("name"))
                        if isinstance(brand_obj, dict)
                        else clean_text(brand_obj or "MISUMI")
                    )
                    if name:
                        items.append(
                            {
                                "querySource": query_source,
                                "partNumber": sku,
                                "seriesCode": sku,
                                "productName": name,
                                "brand": brand or "MISUMI",
                                "category": "",
                                "unitPriceUSD": price,
                                "currency": "USD",
                                "daysToShip": None,
                                "inStock": "instock" in str(offers.get("availability", "")).lower(),
