# Solada prefilter (zero AI)

`prefilter.py` is a numeric screen that runs in GitHub Actions (`.github/workflows/solada-prefilter.yml`) and wakes the Grok Bot desk routine only when something crosses a line. No model calls.

**Inputs:** `https://www.solada.fun/api/launches` and `/api/market` (market can go stale, so on-chain data wins), GeckoTerminal pools (DexScreener fallback), Blockfrost holders/flows (Koios fallback when `BLOCKFROST_PROJECT_ID` is missing or failing), optional Solana `getTokenLargestAccounts` via `SOLANA_RPC_URL` or `HELIUS_API_KEY` (shared deployer ignored).

**Thresholds** are parsed from `solada/paper.json` `rules[]` at run time: rule `k` (min holders, min ADA side), rule `y` (thin-pool ADA side), rule `aa` (zero-holder copycats = noise). Kill lines come from open tickets' `kill_line` numbers and `solada/log.json` `kill_lines` text. Watch-list stake prefixes come from `whale_watch` / `whale_watch_added` in the log and its history. Knobs that are not desk rules (whale thresholds, caps, pacing, units for non-Solada tickers) live in `config.json`.

**Triggers:** a new launch passing k and y; a tracked name newly crossing its kill line; a founder-whale move (2000+ ADA equivalent, 10%+ of a position, any ASCEND/STRIKE change, or a new position) between runs; a watch-list key in a new launch's first block. Each fires once (state in `desk-data:solada-prefilter/state.json`). The first run is a baseline: it records everything as seen and sends nothing.

**Webhook:** `POST $SOLADA_WEBHOOK_URL` with `Authorization: Bearer $SOLADA_WEBHOOK_KEY` and `{"reason":"threshold","names":[...],"metrics":{...}}`. Skipped when the URL is not set.

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
