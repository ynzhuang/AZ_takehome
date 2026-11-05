# ECB reference exchange rates (2025)

- **File:** `ecb_exr_2025.csv` (raw, unmodified)
- **Source:** ECB Data Portal, dataset EXR, series `D.USD.EUR.SP00.A` and `D.GBP.EUR.SP00.A`
- **Request:** `https://data-api.ecb.europa.eu/service/data/EXR/D.USD+GBP.EUR.SP00.A?startPeriod=2025-01-01&endPeriod=2025-12-31&format=csvdata`
- **Retrieved:** 2026-11-05T02:43:48Z
- **Content:** 255 daily observations per currency, 2025-01-02 to 2025-12-31, all with status `A` (normal)
- **Convention:** `OBS_VALUE` is units of foreign currency per 1 EUR, so EUR amount = amount ÷ rate
- **No publication** on weekends or TARGET holidays (2025-01-01, 04-18, 04-21, 05-01, 12-25, 12-26). Use the most recent prior published rate (design §2.3).
