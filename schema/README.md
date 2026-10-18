# Shared desk format (version 2)

Every desk writes the same three kinds of files so one dashboard can read them all.
Paper and research data only: never owner wallet addresses, stake keys, real holdings, balances or real transactions (see ../PRIVACY.md).
Extra fields are always allowed, so adding a new field never breaks the check. Only the fields marked **required** must be present.

## Where files go
Each desk has a folder (e.g. `solada/`, `nft/`), listed in `/desks.json`:
- `<desk>/paper.json`: paper book (`schema/paper-book.schema.json`). Sub-books live in a subfolder, e.g. `solada/pumpfun/paper.json`.
- `<desk>/log.json`: run log (`schema/run-log.schema.json`)
- `<desk>/briefs/YYYY-MM-DD.json`: one daily brief per day (`schema/brief.schema.json`)
- `<desk>/pulse.json` (optional): hourly pulse, listed as `pulse` on the desk in `desks.json` when set (otherwise the dashboard reads `<desk>/pulse.json`). `schema_version` (= 2), `desk`, `updated_at`, `window_days`, `points[]` (`t`, `ticker`, `asset_id`, `metrics{}`), `radar[]` (`ticker`, `chain`, `asset_id`, `first_seen`, `score`, `signals[]`, `note`). Points older than `window_days` move to `<desk>/archive/pulse-YYYY-MM.json`. Schema: `schema/pulse.schema.json`. CI validates the file when it is present. A missing pulse is normal.
A new file or desk must be added to `desks.json`; CI validates every file listed there, plus a pulse file when one is present.

Each desk may also have `<desk>/panels.json`, which chooses the panels on that desk's page. The panel format, built-in types, and how to add a component are in [dashboard/PLUGINS.md](../dashboard/PLUGINS.md).

Bots write daily briefs as `<desk>/briefs/YYYY-MM-DD.json` only. They do not maintain a brief index. The site build writes `<desk>/briefs/index.json` from those files when it publishes.

## Paper book
- **required:** `schema_version` (= 2), `desk`, `book` (book id, e.g. "solada", "pumpfun"), `open[]`, `closed[]`, `rules[]`
- optional: `chain`, `unit` (what `size`/`pnl` are in, e.g. "ADA" or "ADA-equivalent"), `ticket_size`, `disclaimer`, `default_window_hours`, `levels{}`, `summaries[]` (each `at`, `type`, `note`, plus anything else)
- **Ticket** (in `open` and `closed`). **Required:** `id`, `ticker`, `opened_at`, plus `closed_at` for closed tickets. Usual fields: `status`, `level`, `size`, `window_ends_at`, `entry{}`, `kill_line` (`{text, …numbers}`), `thesis[]`, `marks[]`. For closed tickets, also `exit{}`, `exit_reason`, `result{pnl, pct, unit}`, `review`.
- **Rule:** `rule` (**required**), `id`, `added_at`, `source`. Optional display fields: `title` (plain research title), `category` (`kill`, `entry`, `sybil`, `swarm`, `liq`, `review`), `evidence` (ticker strings), `mirrors` (another rule, written `book:id`, for example `solada:j`). A mirror that does not match a rule id yet is shown as that text. It does not fail the page or the schema check.

## Run log
- **required:** `schema_version` (= 2), `desk`, `asof`, `headline`
- usual: `levels{ticker: verdict}`, `kill_lines`, `paper_actions`, `rules_added`, `followups[]`, `sources`, `history[]` (past runs, same shape; each needs `asof`; keep about the last 20)

## Daily brief
- **required:** `schema_version` (= 2), `desk`, `date` (YYYY-MM-DD), `headline`
- usual: `scoreboard{open, closed, wins, losses, pnl, unit}`, `highlights[]`, `rules_learned[]`, `next_watch[]`

## Hourly pulse
- **required:** `schema_version` (= 2), `desk`, `updated_at`
- optional: `window_days`, `points[]`, `radar[]`, and any other field
- **Point:** `t`, `ticker`. Optional `chain`, `asset_id`, `metrics` (any keys; numbers are charted)
- **Radar row:** `ticker`. Optional `chain`, `asset_id`, `first_seen`, `score`, `score_raw` (unscaled score, shown under the ring), `score_max` (desk scale, for example 40 on Solada or 6 on NFT; the page normalizes `score` to 0–100), `signals[]` (prose, shown on tap), `note`, `flags[]`, `factors{}`.
- **Factors** are all optional. Omit a key when it was not measured. A missing key is not zero: the page hides that tile. `0` is a real reading (flat holders, no buys) and is shown. Solada keys: `holders`, `holders_prev`, `holders_basis` (`onchain` or `native`), `holders_per_h`, `accel_per_h`, `curve_pct`, `curve_pts_per_h`, `graduated` (venue string such as `minswap-v2`, or null), `vol_h1`, `vol_x`, `vol_unit`, `buys_h1`, `sells_h1`, `price_h1_pct`, `price_h6_pct` (omit it, as SER does, when the 6h price was not read), `ada_side`, `ada_side_pct`, `swarm`, `swarm_keys`, `repeat_first_buyers` (`[n, m]`), `snipers`, `one_sided`, `airdrop`, `native_share_pct`, `top10_pct`, `top1_pct`. Names under an hour old often omit the velocity fields. Solana names often omit `swarm` / `swarm_keys`. Cardano names often omit `snipers` and `native_share_pct`. NFT keys: `floor`, `floor_unit`, `floor_pct_h`, `floor_pct_h_prev`, `floor_pct_h_basis`, `sales_h`, `sales_base_h`, `sales_base_basis`, `sales_x`, `buyers_h`, `buyers_base_h`, `buyers_x`, `listings_near_floor`, `listings_basis`, `listings_chg_pct`, `listings_window_h`, `sell_through_pct_h`, `sell_through_basis`, `top_offer`, `bid_units_10pct`, `bidders_10pct`, `repeat_wallets_24h`, `concentrated` (`{wallets, sales}` or null), `broad` (`{wallets, sales}` or null), `fresh`.
- **Flags:** `{code, tone}` with `tone` of `good`, `warn`, or `bad`. Short labels exist for `first_sighting`, `thin_floor`, `no_offer`, `concentrated_buyers`, `floor_falling`, `dust`, `sales_spike`, `broad_buyers`, `fresh`, and for the Solada codes the bot emits (`swarm-holding`, `volume-faded`, `holders-flat`, `above-kill-lines`, `holders-rising`, `volume-spike`, `sniper-flags`, `low-native-share`, `swarm-rotation`, `pre-seeded-pool`, `holders-falling`, `team-airdrop`, `below-rule-k-depth`, `new-launch`, `buyers-lead-h1`, `thin-pool`, `sellers-lead-h1`, `curve-reversing`, `swarm-first-buyers`, `native-majority`, `sellers-lead-h6`, `one-sided`). Any other code is shown as its own text, using the emitted tone.
- Older pulses omit `factors` and `flags`. The page then parses `signals[]` and, if that fails, shows the raw prose.

`desks.json` may also set `run.url` and `run.label` per desk. That URL is what the dashboard's Run now button opens. It is not a secret.

## Old to new field names (v1 to v2)
| Where | Old (v1) | New (v2) |
|---|---|---|
| book top level | `schema_version: 1` | `schema_version: 2` |
| book top level | *(none)* | `book`, `chain`, `unit` (new) |
| book top level | `paper_ticket_ada` / `paper_ticket_ada_equivalent` | `ticket_size` |
| ticket | `stop` | `kill_line` |
| ticket | `thesis_signals` | `thesis` |
| ticket | `ticket_ada` / `ticket_ada_equivalent` | `size` |
| ticket | `paper_pnl_ada` / `paper_pnl_ada_equiv` | `result.pnl` |
| ticket | `pct_change` | `result.pct` (and `result.unit`) |
| ticket | `signal_review` | `review` |
| run log | *(none)* | `schema_version: 2`, `desk` (new, at top) |
All other fields (e.g. `asset_id`, `custody`, `mint`, `pool`, `ticket_sol`, `entry`, `exit`, `marks`, `rules_applied`) are unchanged.

## Before committing
Run `bash scripts/privacy-guard.sh` and `python3 scripts/validate-desks.py` (needs `pip install jsonschema`). CI runs both on every push and PR to `main`. The Pages workflow runs them again and publishes only if both pass.
