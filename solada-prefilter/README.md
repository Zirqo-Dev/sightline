# Solada prefilter (zero AI)

`prefilter.py` is a numeric screen that runs in GitHub Actions (`.github/workflows/solada-prefilter.yml`) and wakes the Grok Bot desk routine only when something crosses a line. No model calls.

**Inputs:** `https://www.solada.fun/api/launches` and `/api/market` (market can go stale, so on-chain data wins), GeckoTerminal pools (DexScreener fallback), Blockfrost holders/flows (Koios fallback when `BLOCKFROST_PROJECT_ID` is missing or failing), optional Solana `getTokenLargestAccounts` via `SOLANA_RPC_URL` or `HELIUS_API_KEY` (shared deployer ignored).

**Thresholds** are parsed from `solada/paper.json` `rules[]` at run time: rule `k` (min holders, min ADA side), rule `y` (thin-pool ADA side), rule `aa` (zero-holder copycats = noise). Kill lines come from open tickets' `kill_line` numbers and `solada/log.json` `kill_lines` text. Watch-list stake prefixes come from `whale_watch` / `whale_watch_added` in the log and its history. Knobs that are not desk rules (whale thresholds, caps, pacing, units for non-Solada tickers) live in `config.json`.

**Triggers:** a new launch passing k and y; a tracked name newly crossing its kill line; a founder-whale move (2000+ ADA equivalent, 10%+ of a position, any ASCEND/STRIKE change, or a new position) between runs; a watch-list key in a new launch's first block. Each fires once (state in `desk-data:solada-prefilter/state.json`). The first run is a baseline: it records everything as seen and sends nothing.

**Webhook:** `POST $SOLADA_WEBHOOK_URL` with `Authorization: Bearer $SOLADA_WEBHOOK_KEY` and `{"reason":"threshold","names":[...],"metrics":{...}}`. Skipped when the URL is not set.

**Privacy:** the whale is resolved from its ADA Handle at run time; only a short hash tag (`w-xxxxxxxx`) is logged or stored. The full holdings snapshot needed for deltas lives only in the Actions cache (`--cache-dir`); committed state keeps just threshold-crossing moves (tag, asset, amount change).

Local test: `python3 solada-prefilter/prefilter.py --data-dir /tmp/pf --dry-run`
