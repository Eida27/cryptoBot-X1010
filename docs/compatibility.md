# Compatibility evidence

Checked 2026-10-04. Python 3.12.15 and uv 0.12.23 are used locally.
The lock pins Binance SDK 17.5.0, binance-common 4.5.0, FastAPI 0.142.2,
Uvicorn 0.54.0, Jinja2 3.1.6 and pydantic-settings 2.15.0; all support Python 3.12.

Primary sources: [S1 general information](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/general-info),
[S2 market data](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data),
[S3 trade APIs](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/trade),
[S4 maintained SDK](https://github.com/binance/binance-connector-python),
[S5 user streams](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/user-data-streams),
and [S6 templates](https://fastapi.tiangolo.com/advanced/templates/).

Production REST: `https://fapi.binance.com`; demo REST: `https://demo-fapi.binance.com`.
Market streams: `wss://fstream.binance.com` and `wss://demo-fstream.binance.com`.
Select environment explicitly; never use an SDK default to route demo mutations.

Conditional SL/TP use `/fapi/v1/algoOrder`, `algoType=CONDITIONAL`,
`STOP_MARKET`/`TAKE_PROFIT_MARKET`, `workingType=MARK_PRICE`, `closePosition=true`.
Close-all excludes quantity/reduceOnly. Queries and cancellation use the algo namespace.
Ordinary IOC and reduce-only market exits use `/fapi/v1/order`.

The SDK documents three retries, 1,000 ms backoff and a 1,000 ms REST timeout.
Our transport sets retries to zero and a bounded request timeout; application read
retries are separate. Every uncertain mutation remains journaled and is queried.

The Binance plugin's public metadata sample has BTC/ETH/SOL minimum notionals
50/20/5 USDT respectively. Its source timestamp predates retrieval; this is context,
not execution freshness or a frozen research dataset. Runtime must fetch fresh filters.

Live blockers: actual demo lifecycle, close-all races, partial fills, reconnect behavior,
account eligibility, funding and maintenance-bracket inputs require separate evidence.
No private exchange calls or performance history were used for this compatibility check.

Installed-source incompatibility: SDK 17.5.0 generic REST wrappers call
`send_request[T]`, which raises `TypeError` on Python 3.12. A characterization test
reproduces this without network access. The narrow transport invokes the same
`binance_common.utils.send_request` directly with the SDK-owned session/signer,
no response model and zero retries. This retains official signing and raw decimal
strings; it does not patch dependencies. Demo execution evidence remains required.
Also, the SDK TESTNET constant points to the older testnet host; DEMO explicitly
selects the official demo URL instead of that constant.
