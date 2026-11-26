from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

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
        return round(num, 4) if 0 < num < 1_000_000 else None
    except ValueError:
        return None


def extract_dollar_price(text: str) -> float | None:
    if not text:
        return None
    matches = re.findall(r"\$\s*([0-9,]+(?:\.[0-9]{1,4})?)", text)
    prices = [p for m in matches if (p := parse_price(m)) is not None]
    return prices[0] if prices else None


def parse_days(val: Any) -> int | None:
    if val is None or isinstance(val, bool):
        return None
    if isinstance(val, (int, float)):
        d = int(val)
        return d if 0 <= d < 120 else None
    s = str(val).strip().lower()
    if not s:
        return None
    if "same day" in s or "same-day" in s or s == "0":
        return 0
    m = re.search(r"(\d+)", s)
    if m:
        d = int(m.group(1))
        return d if 0 <= d < 120 else None
    return None


def extract_days_from_text(text: str) -> int | None:
    if not text:
        return None
    if re.search(r"same[\s-]*day", text, re.I):
        return 0
    for pat in (
        r"ships?\s*(?:as\s*early\s*as\s*|in\s*)?(\d+)\s*day",
        r"days?\s*to\s*ship\s*[:\-]?\s*(\d+)",
        r"(\d+)\s*days?\s*to\s*ship",
        r"(\d+)\s*business\s*days?",
    ):
        m = re.search(pat, text, re.I)
        if m:
            return parse_days(m.group(1))
    return None


def parse_int(val: Any, default: int = 1) -> int:
    if val is None or isinstance(val, bool):
        return default
    m = re.search(r"(\d+)", str(val))
    return max(1, int(m.group(1))) if m else default


def is_part_number_query(q: str) -> bool:
    s = q.strip()
    if not s or " " in s or s.startswith("http") or len(s) < 3 or len(s) > 45:
        return False
    return bool(re.search(r"\d", s) and re.search(r"[A-Za-z]", s))


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
    if depth > 7:
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


def parse_detail_page(
    soup: BeautifulSoup, current_url: str, query_source: str
) -> dict[str, Any] | None:
    m_code = re.search(r"/vona2/detail/(\d+)", current_url)
    series_code = m_code.group(1) if m_code else ""

    qs = parse_qs(urlparse(current_url).query)
    part_num = clean_text(
        (qs.get("HissuCode") or qs.get("PNSearch") or [""])[0]
    )
    if not part_num and is_part_number_query(query_source):
        part_num = query_source.strip()

    if not part_num:
        for sel in (
            '[data-part-number]',
            '[id*="partNumber" i]',
            '[class*="partNumber" i]',
            '[class*="part-number" i]',
        ):
            el = soup.select_one(sel)
            if el:
                cand = clean_text(el.get("data-part-number") or el.get_text(" ", strip=True))
                if 3 <= len(cand) <= 45 and re.search(r"\d", cand):
                    part_num = cand
                    break

    h1 = soup.select_one("h1")
    prod_name = clean_text(h1.get_text(" ", strip=True)) if h1 else ""
    if not prod_name and soup.title:
        prod_name = re.sub(r"\s*\|\s*MISUMI.*$", "", clean_text(soup.title.string), flags=re.I)

    if not prod_name:
        return None

    unit_price: float | None = None
    days: int | None = None
    brand = ""
    category = ""
    image_url = ""

    for script in soup.select('script[type="application/ld+json"]'):
        try:
            ld = json.loads(script.string or "")
            nodes = ld if isinstance(ld, list) else [ld]
            for node in nodes:
                if not isinstance(node, dict):
                    continue
                if node.get("@type") == "BreadcrumbList":
                    crumbs = [
                        clean_text(it.get("item", {}).get("name") or it.get("name"))
                        for it in (node.get("itemListElement") or [])
                        if isinstance(it, dict)
                    ]
                    crumbs = [c for c in crumbs if c and c.lower() not in ("home", "misumi top", "top")]
                    if crumbs:
                        category = " - ".join(crumbs[:-1] or crumbs)
                if node.get("@type") in ("Product", "IndividualProduct", "ProductModel"):
                    offers = node.get("offers") or {}
                    if isinstance(offers, list):
                        offers = offers[0] if offers else {}
                    unit_price = unit_price or parse_price(
                        offers.get("price") or offers.get("lowPrice")
                    )
                    b_obj = node.get("brand")
                    brand = brand or (
                        clean_text(b_obj.get("name"))
                        if isinstance(b_obj, dict)
                        else clean_text(b_obj)
                    )
                    image_url = image_url or normalize_url(node.get("image"))
        except Exception:
            continue

    if unit_price is None:
        for sel in (
            '[class*="unitPrice" i]',
            '[class*="unit-price" i]',
            '[id*="unitPrice" i]',
            '[class*="priceBox" i]',
            '[class*="price" i]',
        ):
            for el in soup.select(sel):
                txt = clean_text(el.get_text(" ", strip=True))
                p = extract_dollar_price(txt)
                if p is not None:
                    unit_price = p
                    break
            if unit_price is not None:
                break

    full_text = clean_text(soup.get_text(" ", strip=True))
    if unit_price is None:
        unit_price = extract_dollar_price(full_text)

    for sel in ('[class*="daysToShip" i]', '[class*="ship" i]', '[id*="ship" i]'):
        for el in soup.select(sel):
            d = extract_days_from_text(clean_text(el.get_text(" ", strip=True)))
            if d is not None:
                days = d
                break
        if days is not None:
            break

    if days is None:
        days = extract_days_from_text(full_text)

    if not brand:
        for sel in ('[class*="brandName" i]', '[class*="brand" i]', '[itemprop="brand"]'):
            el = soup.select_one(sel)
            if el:
                b_txt = clean_text(el.get_text(" ", strip=True))
                b_txt = re.sub(r"^brand\s*:\s*", "", b_txt, flags=re.I).strip()
                if 1 < len(b_txt) <= 40:
                    brand = b_txt
                    break

    spec_chunks: list[str] = []
    for row in soup.select("table tr, dl"):
        r_txt = clean_text(row.get_text(" ", strip=True))
        if 4 <= len(r_txt) <= 90 and "$" not in r_txt:
            spec_chunks.append(r_txt)
        if len(spec_chunks) >= 6:
            break

    return {
        "querySource": query_source,
        "partNumber": part_num or series_code or query_source,
        "seriesCode": series_code or part_num,
        "productName": prod_name,
        "brand": brand or "MISUMI",
        "category": category,
        "unitPriceUSD": unit_price,
        "currency": "USD",
        "daysToShip": days,
        "inStock": bool(days is not None and days <= 5),
        "minOrderQty": 1,
        "cadAvailable": bool(re.search(r"\b(?:2D|3D|CAD)\b", full_text)),
        "specsSummary": (" | ".join(spec_chunks) if spec_chunks else prod_name)[:450],
        "imageUrl": image_url,
        "url": current_url,
        "scrapedAt": now_iso(),
    }


def extract_brand_from_card(card: Any, card_text: str, title: str) -> str:
    for sel in ('[class*="brand" i]', '[class*="maker" i]'):
        el = card.select_one(sel)
        if el:
            b = clean_text(el.get_text(" ", strip=True))
            if 1 < len(b) <= 35 and b.lower() != title.lower():
                return b

    idx = card_text.find(title)
    if idx > 0:
        prefix = card_text[:idx].strip()
        prefix = re.sub(
            r"^(?:CAD|2D/3D|2D|3D|NEW|RoHS|Sale|Featured|Economy|\||\-)+\s*",
            "",
            prefix,
            flags=re.I,
        ).strip()
        if 1 < len(prefix) <= 35:
            return prefix

    return "MISUMI"


def parse_rendered_dom(html: str, current_url: str, query_source: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "lxml")
    items: list[dict[str, Any]] = []

    if "/vona2/detail/" in current_url:
        detail_item = parse_detail_page(soup, current_url, query_source)
        if detail_item:
            return [detail_item]

    grouped_links: dict[str, list[Any]] = {}
    for a_tag in soup.select('a[href*="/vona2/detail/"]'):
        href = a_tag.get("href") or ""
        m_code = re.search(r"/vona2/detail/(\d+)", href)
        if not m_code:
            continue
        grouped_links.setdefault(m_code.group(1), []).append(a_tag)

    for s_code, a_tags in grouped_links.items():
        best_a = max(a_tags, key=lambda a: len(clean_text(a.get_text(" ", strip=True))))
        title = clean_text(best_a.get_text(" ", strip=True))
        if len(title) < 3:
            continue

        href = best_a.get("href") or ""
        for a in a_tags:
            h = a.get("href") or ""
            if "HissuCode=" in h or "PNSearch=" in h:
                href = h
                break

        card = best_a
        for _ in range(8):
            parent = card.parent
            if not parent or parent.name in ("body", "html", "main", "ul", "ol"):
                break
            p_text = parent.get_text(" ", strip=True)
            if len(p_text) > 3500:
                break
            codes_in_parent = {
                m.group(1)
                for a in parent.select('a[href*="/vona2/detail/"]')
                if (m := re.search(r"/vona2/detail/(\d+)", a.get("href") or ""))
            }
            if len(codes_in_parent) > 1:
                break
            card = parent

        card_text = clean_text(card.get_text(" ", strip=True))
        unit_price = extract_dollar_price(card_text)
        days = extract_days_from_text(card_text)
        brand = extract_brand_from_card(card, card_text, title)

        pn_match = re.search(r"[?&](?:HissuCode|PNSearch)=([^&#]+)", href)
        part_num = unquote(pn_match.group(1)) if pn_match else ""
        if not part_num and is_part_number_query(query_source) and query_source.lower() in card_text.lower():
            part_num = query_source.strip()
        if not part_num:
            part_num = s_code

        clean_summary = card_text.replace(title, "").strip(" |-")
        items.append(
            {
                "querySource": query_source,
                "partNumber": part_num,
                "seriesCode": s_code,
                "productName": title,
                "brand": brand,
                "category": "",
                "unitPriceUSD": unit_price,
                "currency": "USD",
                "daysToShip": days,
                "inStock": bool(days is not None and days <= 5),
                "minOrderQty": 1,
                "cadAvailable": bool(re.search(r"\b(?:2D|3D|CAD)\b", card_text)),
                "specsSummary": (clean_summary or card_text)[:350],
                "imageUrl": "",
                "url": normalize_url(href),
                "scrapedAt": now_iso(),
            }
        )

    return items


async def wait_for_misumi_content(page: Page) -> None:
    for _ in range(18):
        title_now = await page.title()
        url_now = page.url
        if title_now and "access denied" not in title_now.lower():
            if "/vona2/detail/" in url_now:
                ready = await page.evaluate(
                    "() => /\\$\\s*\\d/.test(document.body?.innerText || '')"
                )
                if ready:
                    break
            else:
                ready = await page.evaluate(
                    "() => document.querySelectorAll('a[href*=\"/vona2/detail/\"]').length > 0"
                )
                if ready:
                    break
        await page.wait_for_timeout(500)


async def fetch_target_items(
    page: Page, target_url: str, query_label: str
) -> tuple[bool, list[dict[str, Any]]]:
    captured_jsons: list[Any] = []
    pending_tasks: list[asyncio.Task[Any]] = []

    async def read_json(resp: Response) -> None:
        try:
            url_l = resp.url.lower()
            if "misumi" not in url_l or resp.status != 200:
                return
            rtype = resp.request.resource_type
            ctype = (resp.headers.get("content-type") or "").lower()
            if rtype not in ("xhr", "fetch") and "json" not in ctype:
                if not any(k in url_l for k in ("api", "search", "price", "series", "part")):
                    return
            raw = await resp.text()
            if not raw or len(raw) > 2_000_000:
                return
            txt = raw.strip()
            if txt.startswith("{") or txt.startswith("["):
                captured_jsons.append(json.loads(txt))
            elif "(" in txt[:60] and txt.endswith((")", ");")):
                inner = re.sub(r"^[^(]+\(", "", txt)
                inner = re.sub(r"\);?\s*$", "", inner)
                if inner.startswith("{") or inner.startswith("["):
                    captured_jsons.append(json.loads(inner))
        except Exception:
            pass

    def on_response(resp: Response) -> None:
        pending_tasks.append(asyncio.create_task(read_json(resp)))

    page.on("response", on_response)
    try:
        resp = await page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
        status = resp.status if resp else 0
        await wait_for_misumi_content(page)

        title = await page.title()
        if not title and status != 403:
            Actor.log.info(f"Warm-reloading '{query_label}' after initial blank shell...")
            resp = await page.goto(target_url, wait_until="load", timeout=30000)
            status = resp.status if resp else status
            await wait_for_misumi_content(page)
            title = await page.title()

        Actor.log.info(
            f"Loaded '{query_label}' -> HTTP {status} | URL: {page.url[:75]} | Title: {title[:60]}"
        )

        if status == 403 or "access denied" in title.lower():
            return True, []

        try:
            await page.evaluate("() => window.scrollBy(0, 650)")
            await page.wait_for_timeout(1200)
            await page.evaluate("() => window.scrollTo(0, 0)")
        except Exception:
            pass

        await page.wait_for_timeout(1800)
        if pending_tasks:
            await asyncio.gather(*pending_tasks, return_exceptions=True)

        html = await page.content()
        if "access denied" in html[:1500].lower():
            return True, []

        dom_items = parse_rendered_dom(html, page.url, query_label)
        api_items = parse_captured_jsons(captured_jsons, query_label)

        api_by_key: dict[str, dict[str, Any]] = {}
        for a_it in api_items:
            if a_it.get("seriesCode"):
                api_by_key[a_it["seriesCode"]] = a_it
            if a_it.get("partNumber"):
                api_by_key[a_it["partNumber"]] = a_it

        items = dom_items if dom_items else api_items
        for it in items:
            match_api = api_by_key.get(it.get("partNumber") or "") or api_by_key.get(
                it.get("seriesCode") or ""
            )
            if match_api:
                if it.get("unitPriceUSD") is None and match_api.get("unitPriceUSD") is not None:
                    it["unitPriceUSD"] = match_api["unitPriceUSD"]
                if it.get("daysToShip") is None and match_api.get("daysToShip") is not None:
                    it["daysToShip"] = match_api["daysToShip"]
                if (not it.get("brand") or it["brand"] == "MISUMI") and match_api.get("brand"):
                    it["brand"] = match_api["brand"]
                if not it.get("category") and match_api.get("category"):
                    it["category"] = match_api["category"]

        if items and is_part_number_query(query_label) and items[0].get("unitPriceUSD") is None:
            first_url = items[0].get("url") or ""
            if "/vona2/detail/" in first_url:
                sep = "&" if "?" in first_url else "?"
                detail_target = (
                    first_url
                    if "HissuCode=" in first_url
                    else f"{first_url}{sep}HissuCode={quote_plus(query_label)}"
                )
                Actor.log.info(f"Fetching exact part unit price on detail page: {detail_target[:80]}")
                await page.goto(detail_target, wait_until="domcontentloaded", timeout=25000)
                await wait_for_misumi_content(page)
                await page.wait_for_timeout(2000)
                d_soup = BeautifulSoup(await page.content(), "lxml")
                d_item = parse_detail_page(d_soup, page.url, query_label)
                if d_item and d_item.get("unitPriceUSD") is not None:
                    items[0] = d_item

        return False, items
    finally:
        page.remove_listener("response", on_response)


async def build_connection_modes(
    proxy_cfg_input: dict[str, Any] | None,
) -> list[tuple[str, dict[str, str] | None]]:
    modes: list[tuple[str, dict[str, str] | None]] = []

    try:
        res_cfg = await Actor.create_proxy_configuration(
            groups=["RESIDENTIAL"], country_code="US"
        )
        if res_cfg:
            res_url = await res_cfg.new_url()
            pw_res = to_playwright_proxy(res_url)
            if pw_res:
                modes.append(("Residential-US", pw_res))
    except Exception:
        pass

    modes.append(("Direct-Browser", None))

    if proxy_cfg_input:
        try:
            std_cfg = await Actor.create_proxy_configuration(
                actor_proxy_input=proxy_cfg_input
            )
            if std_cfg:
                std_url = await std_cfg.new_url()
                pw_std = to_playwright_proxy(std_url)
                if pw_std:
                    modes.append(("Apify-Proxy", pw_std))
        except Exception:
            pass

    return modes


async def main() -> None:
    async with Actor:
        actor_input = await Actor.get_input() or {}
        part_numbers: list[str] = actor_input.get("partNumbers") or []
        start_urls_raw: list[Any] = actor_input.get("startUrls") or []
        max_items: int = max(1, int(actor_input.get("maxItems") or 25))
        proxy_input: dict[str, Any] | None = actor_input.get("proxyConfiguration")

        targets: list[tuple[str, str]] = []
        for u in start_urls_raw:
            url_str = u.get("url") if isinstance(u, dict) else str(u)
            url_str = clean_text(url_str)
            if url_str:
                targets.append((url_str, url_str))

        for kw in part_numbers:
            kw_clean = clean_text(kw)
            if kw_clean:
                search_url = f"{BASE_URL}/vona2/result/?Keyword={quote_plus(kw_clean)}"
                targets.append((kw_clean, search_url))

        if not targets:
            for default_kw in ("SFJ10-100", "linear bushing"):
                targets.append(
                    (default_kw, f"{BASE_URL}/vona2/result/?Keyword={quote_plus(default_kw)}")
                )

        modes = await build_connection_modes(proxy_input)
        total_saved = 0
        seen_keys: set[str] = set()

        async with async_playwright() as pw:
            try:
                browser = await pw.firefox.launch(headless=True)
                browser_name = "Firefox"
            except Exception:
                browser = await pw.chromium.launch(
                    headless=True,
                    args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
                )
                browser_name = "Chromium"

            Actor.log.info(f"Browser engine started: {browser_name} | Hard Cap: {max_items}")

            active_context = None
            active_page: Page | None = None

            async def open_stealth_page(proxy_dict: dict[str, str] | None) -> tuple[Any, Page]:
                ctx_kwargs: dict[str, Any] = {
                    "locale": "en-US",
                    "timezone_id": "America/Chicago",
                    "viewport": {"width": 1366, "height": 768},
                }
                if proxy_dict:
                    ctx_kwargs["proxy"] = proxy_dict
                ctx = await browser.new_context(**ctx_kwargs)
                await ctx.add_init_script(
                    "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
                )

                async def route_filter(route: Any) -> None:
                    req = route.request
                    if req.resource_type in BLOCKED_RESOURCE_TYPES or any(
                        d in req.url.lower() for d in BLOCKED_DOMAINS
                    ):
                        await route.abort()
                    else:
                        await route.continue_()

                await ctx.route("**/*", route_filter)
                pg = await ctx.new_page()
                return ctx, pg

            try:
                for label, target_url in targets:
                    if total_saved >= max_items:
                        break

                    Actor.log.info(
                        f"Searching MISUMI USA for: '{label}' (remaining cap: {max_items - total_saved})"
                    )
                    extracted: list[dict[str, Any]] = []

                    if active_page is not None:
                        try:
                            blocked, extracted = await fetch_target_items(
                                active_page, target_url, label
                            )
                            if blocked:
                                await active_context.close()
                                active_context, active_page = None, None
                        except Exception as exc:
                            Actor.log.warning(f"Active session error ({exc}), reconnecting...")
                            if active_context:
                                await active_context.close()
                            active_context, active_page = None, None

                    if active_page is None:
                        for mode_name, proxy_dict in modes:
                            ctx = None
                            try:
                                Actor.log.info(f"Connecting via [{mode_name}]...")
                                ctx, pg = await open_stealth_page(proxy_dict)
                                blocked, extracted = await fetch_target_items(
                                    pg, target_url, label
                                )
                                if blocked:
                                    Actor.log.warning(
                                        f"[{mode_name}] Blocked (403/Access Denied). Trying next mode..."
                                    )
                                    await ctx.close()
                                    continue
                                active_context, active_page = ctx, pg
                                break
                            except Exception as exc:
                                Actor.log.warning(
                                    f"[{mode_name}] Unavailable ({str(exc)[:120]}). Trying next mode..."
                                )
                                if ctx:
                                    await ctx.close()

                    for item in extracted:
                        if total_saved >= max_items:
                            break
                        if not item.get("productName") or not item.get("url"):
                            continue
                        dedup_key = f"{item.get('partNumber')}::{item.get('url')}"
                        if dedup_key in seen_keys:
                            continue
                        seen_keys.add(dedup_key)
                        await Actor.push_data(item)
                        total_saved += 1

                    if total_saved >= max_items:
                        Actor.log.info(f"Strict Hard Cap ({max_items}) reached. Stopping immediately.")
                        break
            finally:
                if active_context:
                    await active_context.close()
                await browser.close()

        Actor.log.info(
            f"Run finished cleanly. Total items saved: {total_saved} (Hard Cap: {max_items})"
        )


if __name__ == "__main__":
    asyncio.run(main())
