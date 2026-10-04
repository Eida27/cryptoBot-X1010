# Real DEMO contract rehearsal

Normal tests never submit exchange orders. This suite is skipped unless `RUN_BINANCE_DEMO_TESTS=1` and separate local `CBOT_DEMO_API_KEY` / `CBOT_DEMO_API_SECRET` variables exist. Running it is an explicit DEMO trading action requiring operator authorization. No implementation run sets that flag automatically.

Use a dedicated, initially flat demo wallet whose balance matches the configured declared allocation (16.00 USDT within 0.01), isolated/one-way/single-asset settings, leverage 2, auto-add-margin and BNB fees off. The test does not change those settings or increase capital. Missing feasibility produces a skip, which cannot qualify G4. Verify the official demo endpoint before every mutation; production credentials are never loaded.

After authorization, run `uv run pytest tests/demo -q`. The suite uses the actual application coordinator/SDK to submit a bounded IOC, confirm native MARK_PRICE close-position stop and target, recover through a fresh coordinator, reduce only remaining owned exposure, and verify flatness plus sibling cleanup. It opens public streams twice. It records private observations under ignored `reports/demo/` only after actual venue assertions.

This test may not naturally produce partial or zero fills and does not force unsafe thin-market orders to obtain them. Real partial/zero-fill and private reconnect/accepted-timeout behavior must be observed and documented on supported demo infrastructure before full G4 can pass. A syntax-only test order or skipped case is insufficient. If clean-up remains uncertain, inspect the demo account and retained client IDs; do not repeat entry to solve ambiguity. Keep G4 blocked when any required semantics remain unverified.
