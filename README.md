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
