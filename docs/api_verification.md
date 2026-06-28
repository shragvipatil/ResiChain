# ResiChain API Verification
Date: 27 June 2026
Verified by: Person B

## Essential APIs (Required for MVP)

### Gemini
- Status: ✅ Working
- Key starts with: AQ.
- Stored in: .env as GEMINI_API_KEY
- Used for: All 8 AI agents

### EIA
- Status: ✅ Working
- Endpoint tested: https://api.eia.gov/v2/
- Stored in: .env as EIA_API_KEY
- Used for: Brent crude price, supply data

## Free APIs (No Key Needed)

### GDELT
- Status: ✅ Working
- Endpoint: http://data.gdeltproject.org/gdeltv2/lastupdate.txt
- Used for: News event monitoring (Agent 1)

### UKMTO RSS
- Status: ✅ Working
- Endpoint: https://www.ukmto.org/rss
- Used for: Maritime security alerts (Agent 1)

### OFAC Sanctions List
- Status: ✅ Working
- Endpoint: https://www.treasury.gov/ofac/downloads/sdnlist.xml
- Used for: Supplier sanctions check (Agent 7)

### OpenStreetMap
- Status: No
- Endpoint: https://overpass-api.de/api/interpreter
- Used for: Port coordinates

### UN Comtrade
- Status: No
- Endpoint: https://comtradeapi.un.org/public/v1/preview
- Used for: Import share data (already hardcoded in KG)

### PPAC
- Status: ✅ Confirmed — manual download available
- URL: https://ppac.gov.in/sector/crude
- Used for: SPR levels, refinery data (already hardcoded in KG)

## Skipped APIs

### AISHub
- Status: ⚠️ Skipped
- Reason: Requires physical AIS receiver for free access
- Workaround: Tanker positions hardcoded for demo

### Alpha Vantage
- Status: ⚠️ Skipped
- Reason: yfinance Python library does the same thing with no API key
- Workaround: yfinance used instead in Agent 6

### ReliefWeb
- Status: ⚠️ Skipped
- Reason: App registration now mandatory
- Workaround: GDELT covers all required geopolitical data