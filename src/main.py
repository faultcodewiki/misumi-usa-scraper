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
                                "minOrderQty": 1,
                                "cadAvailable": "2d" in html.lower() or "3d" in html.lower(),
                                "specsSummary": clean_text(node.get("description"))[:400],
                                "imageUrl": normalize_url(node.get("image")),
                                "url": normalize_url(node.get("url") or current_url),
                                "scrapedAt": now_iso(),
                            }
                        )
        except Exception:
            continue

    for a_tag in soup.select('a[href*="/vona2/detail/"]'):
        href = a_tag.get("href") or ""
        m_code = re.search(r"/vona2/detail/(\d+)", href)
        if not m_code:
            continue
        s_code = m_code.group(1)
        if s_code in seen_codes:
            continue

        title = clean_text(a_tag.get_text(" ", strip=True))
        if len(title) < 4:
            continue

        card = a_tag
        for _ in range(4):
            if card.parent and len(card.parent.get_text(" ", strip=True)) < 1600:
                card = card.parent
            else:
                break

        card_text = clean_text(card.get_text(" ", strip=True))
        price_match = re.search(r"\$\s*([0-9,]+(?:\.[0-9]+)?)", card_text)
        unit_price = parse_price(price_match.group(1)) if price_match else None

        days: int | None = None
        if re.search(r"same[\s-]*day", card_text, re.I):
            days = 0
        else:
            d_match = re.search(r"(\d+)\s*day", card_text, re.I)
            if d_match:
                days = parse_days(d_match.group(1))

        pn_match = re.search(r"[?&](?:HissuCode|PNSearch)=([^&#]+)", href)
        part_num = unquote(pn_match.group(1)) if pn_match else s_code

        seen_codes.add(s_code)
        items.append(
            {
                "querySource": query_source,
                "partNumber": part_num,
                "seriesCode": s_code,
                "productName": title,
                "brand": "MISUMI",
                "category": "",
                "unitPriceUSD": unit_price,
                "currency": "USD",
                "daysToShip": days,
                "inStock": bool(days is not None and days <= 5),
                "minOrderQty": 1,
                "cadAvailable": bool(re.search(r"\b(?:2D|3D|CAD)\b", card_text)),
                "specsSummary": card_text[:350],
                "imageUrl": "",
                "url": normalize_url(href),
                "scrapedAt": now_iso(),
            }
        )

    return items


async def fetch_target_items(
    page: Page, target_url: str, query_label: str
) -> tuple[bool, list[dict[str, Any]]]:
    captured_jsons: list[Any] = []
    pending_tasks: list[asyncio.Task[Any]] = []

    async def read_json(resp: Response) -> None:
        try:
            ctype = (resp.headers.get("content-type") or "").lower()
            if "json" in ctype and "misumi" in resp.url.lower() and resp.status == 200:
                data = await resp.json()
                captured_jsons.append(data)
        except Exception:
            pass

    def on_response(resp: Response) -> None:
        pending_tasks.append(asyncio.create_task(read_json(resp)))

    page.on("response", on_response)
    try:
        resp = await page.goto(target_url, wait_until="domcontentloaded", timeout=25000)
        status = resp.status if resp else 0
        title = await page.title()
        Actor.log.info(f"Loaded '{query_label}' -> HTTP {status} | Title: {title[:70]}")

        if status == 403 or "access denied" in title.lower():
            return True, []

        try:
            await page.wait_for_selector('a[href*="/vona2/detail/"]', timeout=8000)
        except Exception:
            pass

        await page.wait_for_timeout(2500)
        if pending_tasks:
            await asyncio.gather(*pending_tasks, return_exceptions=True)

        items = parse_captured_jsons(captured_jsons, query_label)
        if not items:
            html = await page.content()
            if "access denied" in html[:1500].lower():
                return True, []
            items = parse_rendered_dom(html, page.url, query_label)

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
