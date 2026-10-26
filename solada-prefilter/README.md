# Solada prefilter (zero AI)

`prefilter.py` is a numeric screen that runs in GitHub Actions (`.github/workflows/solada-prefilter.yml`) and wakes the Grok Bot desk routine only when something crosses a line. No model calls.

**Inputs:** `https://www.solada.fun/api/launches` and `/api/market` (market can go stale, so on-chain data wins), GeckoTerminal pools (DexScreener fallback), Blockfrost holders/flows (Koios fallback when `BLOCKFROST_PROJECT_ID` is missing or failing), optional Solana `getTokenLargestAccounts` via `SOLANA_RPC_URL` or `HELIUS_API_KEY` (shared deployer ignored).

**Thresholds** are parsed from `solada/paper.json` `rules[]` at run time: rule `k` (min holders, min ADA side), rule `y` (thin-pool ADA side), rule `aa` (zero-holder copycats = noise). Kill lines come from open tickets' `kill_line` numbers and `solada/log.json` `kill_lines` text. Watch-list stake prefixes come from `whale_watch` / `whale_watch_added` in the log and its history. Knobs that are not desk rules (whale thresholds, caps, pacing, units for non-Solada tickers) live in `config.json`.

**Triggers:** a new launch passing k and y; a tracked name newly crossing its kill line; a founder-whale move (2000+ ADA equivalent, 10%+ of a position, any ASCEND/STRIKE change, or a new position) between runs; a watch-list key in a new launch's first block. Each fires once (state in `desk-data:solada-prefilter/state.json`). The first run is a baseline: it records everything as seen and sends nothing.

**Sizing (valuation.py):** on-chain quantities are raw units. A whale move is valued by the ADA (lovelace) leg of the whale's own txs since the last snapshot (handles aggregator buys); only without an ADA leg does it fall back to `raw / 10**decimals x pool price` (decimals from Koios `asset_info` token-registry metadata, else `tokens.cardano.org`; Solada curve launches are 6). Unknown decimals = not sized (never `raw x price`). Buy flags size buyers the same way. DEX LP tokens (Minswap V1/V2, SundaeSwap, WingRiders policies; extend via `config.json` `lp_policies`) are labelled `LP` in `whale.lp_moves` and never count as whale moves, buys, or size flags. Kill lines are deduped through `state.json` `kill_reported`: a kill line re-fires only when its crossed values change (a data gap is not a recovery).

**Webhook:** `POST $SOLADA_WEBHOOK_URL` with `Authorization: Bearer $SOLADA_WEBHOOK_KEY` and `{"reason":"threshold","names":[...],"metrics":{...}}`. Skipped when the URL is not set.

**Schedule:** cron-job.org only (job "Solada pre-filter": `workflow_dispatch` every 2h at :07). The workflow has no GitHub `schedule:`. Kill lines Snek has already marked `hit: ...` in `log.kill_lines` are recorded in state and never sent.

**Whale sizing (Oct 26 fixes):** a move's ADA leg is used as-is only when the legs' token qty matches the snapshot delta within 5%. Otherwise the delta is valued at the legs' ADA-per-token rate (`value_basis: ada_leg_rate`). Decimals are always resolved, so payloads carry `qty` (whole tokens) next to the raw `delta`. Dust moves (under 25 ADA or under 1 whole token, `config.json` `whale.dust`) get `"dust": true` and no `pct`, so they never trip the 10% position trigger. Curve launches carry `curve_pct`.

**GeckoTerminal:** the free API rate-limits shared runner IPs. Lookups are paced at 3 s, retried on 429 after 6 s and 15 s (Retry-After honoured, capped at 60 s), and cached per unit within a run. After 3 failed lookups the rest of the run uses DexScreener (`config.json` `geckoterminal`).

**Privacy:** the whale is resolved from its ADA Handle at run time; only a short hash tag (`w-xxxxxxxx`) is logged or stored. The full holdings snapshot needed for deltas lives only in the Actions cache (`--cache-dir`); committed state keeps just threshold-crossing moves (tag, asset, amount change).

**Flags (flag-only, `flags.py`):** heuristic whale/cabal signals written to `desk-data:solada-prefilter/flags.json` (`new` = this run, `recent` = last 300) for Snek's daily run. They never count toward rules k/y/aa or kill lines and never fire the webhook alone; when a threshold trigger is already firing, the run's new flags ride along as `metrics.flags`. Each flag fires once (hash in `state.json` `flags_seen`). Thresholds live in `config.json` `flags` (loose defaults); Snek's stake-prefix tags are the seed watchlist in `flags.snek_prefix_tags`, plus `whale_watch` keys from the log.

| kind | fires when (default) |
|---|---|
| `watch_tags_first_block` | 2+ watch tags among the launch's first-block buyers |
| `watch_tags_early_buyers` / `pool_preseeder_early` | any watch tag in the first 20 buyers (pre-seeder called out) |
| `known_launcher_deploy` | `launcherAddress` stake matches a launcher/pre-seeder/founder tag (free, every new launch) |
| `identical_size_buys` | 3+ wallets buy the same size (±1% or ±0.5 ADA) within 600 s |
| `coordinated_buys` | 4+ wallets buy within 120 s, starting within 15 min of launch |
| `stake_key_multi_address` | one stake key buys through 2+ addresses |
| `fresh_wallet_swarm` | 4+ early buyers whose first tx is <= 4320 blocks (~1 day) before the launch |
| `repeat_early_buyer` | same wallet in the first 20 buyers of 2+ launches |
| `sniper_full_exit` | a first-block buyer now holds 0 of that token |
| `koth_pile_in` | curve >= 80%: watch tags or 4+ fresh wallets in the latest 25 txs |
| `user_created_pool` | launch has a pool but no Solada curve |
| `ada_side_drop` | ADA side down >= 20% since last run |
| `holders_down_mcap_up` | holders fall while mcap rises |
| `holders_doubled_ada_up` | positive: holders x2 with a rising ADA side |
| `volume_without_holders` | 24h volume >= 3x ADA side with flat/falling holders (wash) |
| `holder_concentration` / `concentration_jump` | top stake cluster >= 25% of non-script supply / +10 pts (only where holder pages were already fetched) |
| `founder_whale_move` | whale moves that passed the whale trigger, minus COCK/SOLADA team-support noise |

Wallets appear only as `w-xxxxxxxx` (sha256 of the stake key) or as Snek's existing prefix tags. Stake keys are derived from base addresses locally (bech32, no API call). Full keys, wallet ages and sniper positions stay in `--cache-dir/flags-cache.json` (Actions cache). `flags.assert_public` aborts the run before writing any full address or stake key. Cost: at most ~40 Koios calls per run (8 launch scans x 2, 15 wallet-age lookups, 3 KotH scans x 2, 1-2 holdings batches), about 25 s at the configured pace; zero on baseline runs.

Tests (offline, synthetic fixtures, includes a stubbed `--dry-run`): `python3 -m unittest discover -s solada-prefilter/tests`

Local test: `python3 solada-prefilter/prefilter.py --data-dir /tmp/pf --dry-run`

**Big moves (SPD-43, phase 1, `big_moves.py`):** research rows for Snek, never triggers or the webhook. Each non-dry run writes `desk-data:solada-prefilter/big_moves.json` (`moves` kept 72h). A row is any stake key (or a key address with no stake key; DEX pool/script addresses are counterparties and skipped) whose moves into or out of one token within 24h add up to about 5,000 ADA or more, or 10%+ of that token's pool ADA side. Fields: `tag` (10 chars), `ticker` (`LP` for LP tokens), `direction` (`in`/`out`), `ada` (the mover's net ADA across the whole tx, all ADA legs summed), `qty` (whole tokens when decimals are known, else raw; see `qty_basis`), `tx_short`, `time` (UTC), `next_hop` (null; phase 2), plus `window_ada`, `pool_pct`, `lp`, `unit_short`. Tokens scanned: this run's tracked and candidate rows that have a unit, largest ADA side first (`big_moves.max_units`). Data: free Koios public tier, no key (5,000 requests/day, 100 per 10s, 30s query timeout, 1 KB POST body). It stays polite through shared pacing, 24h block windows only, at most `utxo_batch` (10) tx hashes per POST, a per-run `max_calls` budget (80), and a private Actions-cache file (`big-moves-cache.json`, never committed) that keeps per-token block heights so each tx is read once; a busy token is marked `capped` and resumes next run. It works at any cadence (hourly-capable); the trigger stays cron-job.org. The owner's wallet is never looked up; any owner matching the existing `PRIVACY_GUARD_PATTERNS` secret is dropped in memory before caching or writing, and the commit-time privacy guard still runs.
