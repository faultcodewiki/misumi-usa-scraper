import asyncio
import json
import re
from datetime import datetime, timezone
from urllib.parse import quote_plus, urljoin

import httpx
from apify import Actor
from bs4 import BeautifulSoup

BASE_URL = "https://us.misumi-ec.com"
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://us.misumi-ec.com/",
}


class StrictCreditGuard:
    """maxItems 초과 과금을 원천 차단하는 원자적 하드캡 카운터."""

    def __init__(self, max_items: int) -> None:
        self.max_items = max(1, max_items)
        self.count = 0
        self._lock = asyncio.Lock()

    @property
    def reached(self) -> bool:
        return self.count >= self.max_items

    async def try_push(self, item: dict) -> bool:
        """유효 부품 데이터만 카운트하며 maxItems 도달 시 즉시 중단 신호(False) 반환."""
        if not item or not (item.get("partNumber") or item.get("productName")):
            return False

        async with self._lock:
            if self.count >= self.max_items:
                return False
            await Actor.push_data(item)
            self.count += 1
            Actor.log.info(
                f"[{self.count}/{self.max_items}] Saved: {item.get('partNumber') or item.get('productName')}"
            )
            return True


def clean_float(val) -> float | None:
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return round(float(val), 4)
    cleaned = re.sub(r"[^\d.]", "", str(val))
    try:
        return round(float(cleaned), 4) if cleaned else None
    except ValueError:
        return None


def clean_int(val) -> int | None:
    if val is None:
        return None
    if isinstance(val, int):
        return val
    if isinstance(val, str) and "same" in val.lower():
        return 0
    match = re.search(r"\d+", str(val))
    return int(match.group(0)) if match else None


def build_flat_record(
    *,
    query_source: str,
    part_number: str | None = None,
    series_code: str | None = None,
    product_name: str | None = None,
    brand: str | None = None,
    category: str | None = None,
    unit_price_usd=None,
    days_to_ship=None,
    min_order_qty=1,
    cad_available: bool = False,
    specs_dict: dict | None = None,
    image_url: str | None = None,
    url: str | None = None,
) -> dict:
    """사람(Excel/CSV)과 로봇(MCP AI Agent) 모두가 즉시 쓸 수 있는 1단계 Flat JSON 생성."""
    price = clean_float(unit_price_usd)
    lead_days = clean_int(days_to_ship)
    specs_dict = specs_dict or {}
    specs_summary = " | ".join(f"{k}: {v}" for k, v in list(specs_dict.items())[:12] if v)

    return {
        "querySource": query_source,
        "partNumber": (part_number or series_code or "").strip() or None,
        "seriesCode": (series_code or "").strip() or None,
        "productName": (product_name or "").strip() or None,
        "brand": (brand or "MISUMI").strip(),
        "category": (category or "").strip() or None,
        "unitPriceUSD": price,
        "currency": "USD" if price is not None else None,
        "daysToShip": lead_days,
        "inStock": (lead_days == 0) if lead_days is not None else None,
        "minOrderQty": clean_int(min_order_qty) or 1,
        "cadAvailable": bool(cad_available),
        "specsSummary": specs_summary or None,
        "imageUrl": image_url,
        "url": url,
        "scrapedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


async def try_misumi_api_search(
    client: httpx.AsyncClient, keyword: str, remaining: int
) -> list[dict]:
    """1순위: MISUMI USA 내부 백엔드 JSON API 직접 조회."""
    api_endpoints = [
        f"{BASE_URL}/api/v1/series/search?keyword={quote_plus(keyword)}&page=1&pageSize={min(remaining, 40)}&lang=en",
        f"{BASE_URL}/vona2/api/search?Keyword={quote_plus(keyword)}&PageSize={min(remaining, 40)}",
    ]

    for endpoint in api_endpoints:
        try:
            resp = await client.get(endpoint, timeout=15.0)
            if resp.status_code != 200 or "json" not in resp.headers.get("content-type", "").lower():
                continue

            data = resp.json()
            series_list = (
                data.get("seriesList")
                or data.get("data", {}).get("seriesList")
                or data.get("partNumberList")
                or []
            )
            if not series_list:
                continue

            results = []
            for item in series_list[:remaining]:
                s_code = item.get("seriesCode") or item.get("code")
                p_num = item.get("partNumber") or item.get("completePartNumber") or s_code
                raw_url = item.get("url") or item.get("detailUrl") or (f"/vona2/detail/{s_code}/" if s_code else None)
                full_url = urljoin(BASE_URL, raw_url) if raw_url else None

                specs = {}
                for sp in item.get("specList", []) or []:
                    if isinstance(sp, dict) and sp.get("specName"):
                        specs[sp["specName"]] = sp.get("specValue")

                cad_types = item.get("cadTypeList") or item.get("cadIconList") or []

                results.append(
                    build_flat_record(
                        query_source=keyword,
                        part_number=p_num,
                        series_code=s_code,
                        product_name=item.get("seriesName") or item.get("productName"),
                        brand=item.get("brandName") or item.get("makerName") or "MISUMI",
                        category=item.get("categoryName"),
                        unit_price_usd=item.get("minStandardPrice") or item.get("unitPrice") or item.get("price"),
                        days_to_ship=item.get("minStandardDaysToShip") or item.get("daysToShip"),
                        min_order_qty=item.get("minOrderQty") or item.get("orderQty") or 1,
                        cad_available=bool(cad_types) or bool(item.get("hasCad")),
                        specs_dict=specs,
                        image_url=item.get("mainImageUrl") or item.get("imageUrl"),
                        url=full_url,
                    )
                )
            if results:
                return results
        except Exception as exc:
            Actor.log.debug(f"API fallback triggered for '{keyword}': {exc}")

    return []


def parse_html_page(html: str, page_url: str, query_source: str, limit: int) -> list[dict]:
    """2순위 & 3순위: HTML 내장 JSON-LD / __NEXT_DATA__ 추출 및 DOM 파싱."""
    soup = BeautifulSoup(html, "lxml")
    extracted: list[dict] = []

    # A. JSON-LD (Schema.org Product / ItemList) 추출
    for script in soup.find_all("script", type="application/ld+json"):
        if not script.string:
            continue
        try:
            payload = json.loads(script.string)
            blocks = payload if isinstance(payload, list) else [payload]
            for block in blocks:
                b_type = block.get("@type")
                if b_type == "Product":
                    offers = block.get("offers") or {}
                    if isinstance(offers, list):
                        offers = offers[0] if offers else {}
                    brand_obj = block.get("brand")
                    brand_name = brand_obj.get("name") if isinstance(brand_obj, dict) else brand_obj

                    extracted.append(
                        build_flat_record(
                            query_source=query_source,
                            part_number=block.get("sku") or block.get("mpn"),
                            product_name=block.get("name"),
                            brand=brand_name,
                            category=block.get("category"),
                            unit_price_usd=offers.get("price") or offers.get("lowPrice"),
                            days_to_ship=offers.get("deliveryLeadTime"),
                            cad_available="cad" in html.lower(),
                            image_url=block.get("image")[0] if isinstance(block.get("image"), list) else block.get("image"),
                            url=offers.get("url") or page_url,
                        )
                    )
                elif b_type == "ItemList":
                    for elem in block.get("itemListElement", [])[:limit]:
                        prod = elem.get("item") if isinstance(elem, dict) else {}
                        if isinstance(prod, dict) and prod.get("name"):
                            offers = prod.get("offers") or {}
                            extracted.append(
                                build_flat_record(
                                    query_source=query_source,
                                    part_number=prod.get("sku") or prod.get("mpn"),
                                    product_name=prod.get("name"),
                                    unit_price_usd=offers.get("price") or offers.get("lowPrice"),
                                    url=urljoin(BASE_URL, prod.get("url", "")) or page_url,
                                )
                            )
        except json.JSONDecodeError:
            continue

    if extracted:
        return extracted[:limit]

    # B. Next.js __NEXT_DATA__ 추출
    next_script = soup.find("script", id="__NEXT_DATA__")
    if next_script and next_script.string:
        try:
            next_data = json.loads(next_script.string)
            page_props = next_data.get("props", {}).get("pageProps", {})
            series_items = (
                page_props.get("seriesList")
                or page_props.get("searchResult", {}).get("seriesList")
                or []
            )
            for item in series_items[:limit]:
                s_code = item.get("seriesCode")
                extracted.append(
                    build_flat_record(
                        query_source=query_source,
                        part_number=item.get("partNumber") or s_code,
                        series_code=s_code,
                        product_name=item.get("seriesName"),
                        brand=item.get("brandName") or "MISUMI",
                        category=item.get("categoryName"),
                        unit_price_usd=item.get("minStandardPrice") or item.get("unitPrice"),
                        days_to_ship=item.get("minStandardDaysToShip") or item.get("daysToShip"),
                        cad_available=bool(item.get("cadTypeList")),
                        image_url=item.get("mainImageUrl"),
                        url=urljoin(BASE_URL, item.get("detailUrl", f"/vona2/detail/{s_code}/")) if s_code else page_url,
                    )
                )
            if extracted:
                return extracted[:limit]
        except json.JSONDecodeError:
            pass

    # C. DOM CSS 셀렉터 백업 파싱
    cards = soup.select(
        "[data-series-code], li[class*='SeriesList'], div[class*='ProductItem'], article[class*='product']"
    )
    for card in cards[:limit]:
        link_el = card.select_one("a[href*='/vona2/detail/'], a[href]")
        title_el = card.select_one("h2, h3, [class*='Title'], [class*='seriesName']")
        price_el = card.select_one("[class*='Price'], [class*='price']")
        ship_el = card.select_one("[class*='Days'], [class*='ship'], [class*='LeadTime']")
        brand_el = card.select_one("[class*='Brand'], [class*='maker']")

        href = link_el.get("href") if link_el else ""
        s_code = card.get("data-series-code")
        if not s_code and href:
            m = re.search(r"/detail/(\d+)/?", href)
            s_code = m.group(1) if m else None

        name_txt = title_el.get_text(" ", strip=True) if title_el else None
        if not name_txt and not s_code:
            continue

        extracted.append(
            build_flat_record(
                query_source=query_source,
                part_number=s_code,
                series_code=s_code,
                product_name=name_txt,
                brand=brand_el.get_text(" ", strip=True) if brand_el else "MISUMI",
                unit_price_usd=price_el.get_text(" ", strip=True) if price_el else None,
                days_to_ship=ship_el.get_text(" ", strip=True) if ship_el else None,
                cad_available="cad" in card.get_text(" ", strip=True).lower(),
                url=urljoin(BASE_URL, href) if href else page_url,
            )
        )

    # 단일 상세 페이지인 경우 메타 태그/타이틀 백업 추출
    if not extracted and "/detail/" in page_url:
        h1 = soup.select_one("h1")
        price_el = soup.select_one("[class*='Price'], [data-price]")
        ship_el = soup.select_one("[class*='Days'], [class*='ship']")
        code_match = re.search(r"HissuCode=([A-Za-z0-9_-]+)", page_url) or re.search(r"/detail/(\d+)", page_url)
        if h1:
            extracted.append(
                build_flat_record(
                    query_source=query_source,
                    part_number=code_match.group(1) if code_match else None,
                    product_name=h1.get_text(" ", strip=True),
                    unit_price_usd=price_el.get_text(" ", strip=True) if price_el else None,
                    days_to_ship=ship_el.get_text(" ", strip=True) if ship_el else None,
                    cad_available="cad" in html.lower(),
                    url=page_url,
                )
            )

    return extracted[:limit]


async def process_keyword(
    client: httpx.AsyncClient, keyword: str, guard: StrictCreditGuard
) -> None:
    if guard.reached:
        return

    remaining = guard.max_items - guard.count
    Actor.log.info(f"Searching MISUMI USA for: '{keyword}' (remaining cap: {remaining})")

    # 1차: 백엔드 JSON API 조회
    items = await try_misumi_api_search(client, keyword, remaining)

    # 2차: 웹 검색 결과 페이지 파싱
    if not items:
        search_url = f"{BASE_URL}/vona2/result/?Keyword={quote_plus(keyword)}"
        try:
            resp = await client.get(search_url, timeout=20.0)
            if resp.status_code == 200:
                items = parse_html_page(resp.text, str(resp.url), keyword, remaining)
            else:
                Actor.log.warning(f"HTTP {resp.status_code} on search URL for '{keyword}'")
        except Exception as exc:
            Actor.log.warning(f"Request failed for '{keyword}': {exc}")

    for item in items:
        if not await guard.try_push(item):
            break


async def process_direct_url(
    client: httpx.AsyncClient, target_url: str, guard: StrictCreditGuard
) -> None:
    if guard.reached:
        return

    remaining = guard.max_items - guard.count
    Actor.log.info(f"Fetching direct URL: {target_url}")

    try:
        resp = await client.get(target_url, timeout=20.0)
        if resp.status_code == 200:
            items = parse_html_page(resp.text, str(resp.url), target_url, remaining)
            for item in items:
                if not await guard.try_push(item):
                    break
        else:
            Actor.log.warning(f"HTTP {resp.status_code} on URL: {target_url}")
    except Exception as exc:
        Actor.log.warning(f"Direct URL fetch error ({target_url}): {exc}")


async def main() -> None:
    async with Actor:
        actor_input = await Actor.get_input() or {}

        part_numbers: list[str] = [
            str(q).strip() for q in actor_input.get("partNumbers", []) if str(q).strip()
        ]
        raw_urls = actor_input.get("startUrls", [])
        start_urls: list[str] = [
            (u.get("url") if isinstance(u, dict) else str(u)).strip()
            for u in raw_urls
            if u
        ]
        max_items = int(actor_input.get("maxItems", 25))

        if not part_numbers and not start_urls:
            part_numbers = ["SFJ10-100"]
            Actor.log.info("No input provided. Running default sample query: 'SFJ10-100'")

        guard = StrictCreditGuard(max_items=max_items)

        # 고객 계정의 프록시 설정 연동 (개발자 서버비/프록시비 0원 구조)
        proxy_url: str | None = None
        proxy_cfg_input = actor_input.get("proxyConfiguration")
        if proxy_cfg_input:
            try:
                proxy_cfg = await Actor.create_proxy_configuration(actor_proxy_input=proxy_cfg_input)
                if proxy_cfg:
                    proxy_url = await proxy_cfg.new_url()
            except Exception as exc:
                Actor.log.warning(f"Proceeding without proxy (local or free tier): {exc}")

        async with httpx.AsyncClient(
            headers=DEFAULT_HEADERS,
            proxy=proxy_url,
            follow_redirects=True,
            http2=False,
        ) as client:
            for url in start_urls:
                if guard.reached:
                    break
                await process_direct_url(client, url, guard)

            for kw in part_numbers:
                if guard.reached:
                    break
                await process_keyword(client, kw, guard)

        Actor.log.info(
            f"Run finished cleanly. Total items saved: {guard.count} (Hard Cap: {guard.max_items})"
        )


if __name__ == "__main__":
    asyncio.run(main())