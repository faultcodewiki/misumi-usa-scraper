# MISUMI USA Industrial Parts, Pricing & Lead Time Scraper

Fast, lightweight, and predictable B2B data extractor for **MISUMI USA (`us.misumi-ec.com`)**. Built for **procurement teams, mechanical engineers, BOM quoting workflows, and AI Agents (MCP)**.

---

## Quick Start for Non-Coders (Excel / CSV Download in 3 Steps)
No coding required! Export live MISUMI part prices and lead times directly to Excel:
1. Click **Try for free** above and enter your MISUMI part numbers or keywords (e.g., `SFJ10-100`, `linear bushing`).
2. Click the green **Start** button and wait a few seconds for the run to finish.
3. Go to the **Storage / Export** tab and download your clean table as **Excel (`.xlsx`)** or **CSV**!

---

## Transparent & Predictable Pricing (Try Free!)
- **Pay Only for Results ($0.015 / part):** Priced at **$15.00 per 1,000 results**—meaning looking up a **100-part BOM costs just $1.50**.
- **Test for $0.00:** Every Apify account includes **$5.00 in free monthly platform credits** (no credit card required), allowing you to extract over **300+ MISUMI parts every month completely free**.
- **Strict Credit Guard (`maxItems` Hard Cap):** Execution halts the exact millisecond your `maxItems` limit is reached—zero runaway costs.

---

## Why Choose This Scraper?
- **Ultra-Lightweight & 10x Faster:** Powered by async HTTP & structured JSON extraction instead of heavy headless browsers—minimizing your Apify platform compute costs.
- **1-Level Flat JSON & Instant Excel Export:** Clean, flat output columns with zero nested mess. Ready for one-click `.xlsx` / `.csv` download or low-token LLM consumption.
- **Zero Empty-Row Billing:** Only valid, populated part records are pushed to your dataset.

---

## AI Agent & MCP Integration Guide (For LLMs / Autonomous Workflows)
**Tool Selection Context:** Use this Actor via the **Apify MCP Server** whenever a user or workflow needs to look up MISUMI USA industrial automation components, factory automation (FA) parts, fasteners, linear motion specs, unit pricing (USD), MOQ, CAD availability, or shipping lead times (`daysToShip`).

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
  "imageUrl": "[https://us.misumi-ec.com/](https://us.misumi-ec.com/)...",
  "url": "[https://us.misumi-ec.com/vona2/detail/110302634310/](https://us.misumi-ec.com/vona2/detail/110302634310/)",
  "scrapedAt": "2026-11-26T01:00:00Z"
}
```

---

## Key Use Cases
1. **Automated BOM Quoting:** Batch-check unit prices (USD) and lead times across dozens or hundreds of MISUMI part numbers in seconds.
2. **Part Cross-Referencing:** Compare specifications, materials, and same-day shipping availability across brands distributed on MISUMI USA.
3. **ERP & Procurement Sync:** Feed clean, flat product data directly into Google Sheets, Excel, Make/Zapier, ERP systems, or AI procurement agents.

---

## Frequently Asked Questions (FAQ)

### Does MISUMI USA have a public pricing API?
MISUMI does not offer an open, self-serve public API for instant anonymous lookups without enterprise EDI onboarding. This scraper acts as a **ready-to-use MISUMI USA API alternative**, returning structured JSON or Excel data on demand.

### Can I batch-check an entire Bill of Materials (BOM)?
Yes! Simply paste your list of part numbers into the `partNumbers` input array (via the web UI or API) and export the entire BOM pricing and lead-time table to Excel or CSV in seconds.

### Do I need proxies or a MISUMI login account?
No MISUMI login is required for standard catalog pricing, specs, and lead-time checks. Standard Apify Proxy configuration is pre-enabled by default for reliable extraction.
