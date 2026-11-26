# MISUMI USA Industrial Parts, Pricing & Lead Time Scraper

Fast, lightweight, and predictable B2B data extractor for **MISUMI USA (`us.misumi-ec.com`)**. Built for **procurement teams, mechanical engineers, BOM quoting workflows, and AI Agents (MCP)**.

---

## Why Choose This Scraper?

* **Strict Credit Guard (`maxItems` Hard Cap):** Never worry about runaway credit burn. Execution halts the exact millisecond your `maxItems` limit is reached.
* **Ultra-Lightweight & 10x Faster:** Powered by async HTTP & structured JSON extraction instead of heavy headless browsers—minimizing your Apify platform compute costs.
* **1-Level Flat JSON & Instant Excel Export:** Clean, flat output columns with zero nested mess. Ready for one-click `.xlsx` / `.csv` download or low-token LLM consumption.
* **Zero Empty-Row Billing:** Only valid, populated part records are pushed to your dataset.

---

## AI Agent & MCP Integration Guide (For LLMs / Autonomous Workflows)

**Tool Selection Context:** Use this Actor via the Apify MCP Server whenever a user or workflow needs to look up **MISUMI USA industrial automation components, factory automation (FA) parts, fasteners, linear motion specs, unit pricing (USD), MOQ, CAD availability, or shipping lead times (`daysToShip`)**.

### Recommended Input Payload (JSON)
```json
{
  "partNumbers": ["SFJ10-100", "linear bushing"],
  "maxItems": 25,
  "proxyConfiguration": {
    "useApifyProxy": true
  }
}
```

### Flat Output Schema Sample (JSON)
```json
{
  "querySource": "SFJ10-100",
  "partNumber": "SFJ10-100",
  "seriesCode": "110302634310",
  "productName": "Linear Shafts - Straight, Precision",
  "brand": "MISUMI",
  "category": "Linear Motion",
  "unitPriceUSD": 14.5,
  "currency": "USD",
  "daysToShip": 0,
  "inStock": true,
  "minOrderQty": 1,
  "cadAvailable": true,
  "specsSummary": "Shaft Dia. D(mm): 10 | Overall Length L(mm): 100 | Material: Equivalent to SUJ2",
  "imageUrl": "https://us.misumi-ec.com/...",
  "url": "https://us.misumi-ec.com/vona2/detail/110302634310/",
  "scrapedAt": "2026-11-26T01:00:00Z"
}
```

---

## Key Use Cases
1. **Automated BOM Quoting:** Batch-check unit prices (USD) and lead times across dozens of MISUMI part numbers in seconds.
2. **Part Cross-Referencing:** Compare specifications, materials, and same-day shipping availability across brands distributed on MISUMI USA.
3. **ERP & Procurement Sync:** Feed clean, flat product data directly into Google Sheets, Excel, ERP systems, or AI procurement agents.