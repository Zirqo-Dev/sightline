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

**Branches.** Since Oct 24 `main` only accepts PRs, so bots push these data files to the `desk-data` branch at the same paths. The dashboard reads desk data from `desk-data` first and falls back to `main`'s copy (see [dashboard/PLUGINS.md](../dashboard/PLUGINS.md#desk-data-comes-from-the-desk-data-branch)). `desks.json`, `panels.json` and these schemas are always read from `main`. Note: CI (`scripts/validate-desks.py` in the pages workflow) validates only `main`'s copies; files on `desk-data` are not schema-checked or privacy-guarded by CI today, so bots must validate before pushing.

Each desk may also have `<desk>/panels.json`, which chooses the panels on that desk's page. The panel format, built-in types, and how to add a component are in [dashboard/PLUGINS.md](../dashboard/PLUGINS.md).

Bots write daily briefs as `<desk>/briefs/YYYY-MM-DD.json` only. They do not maintain a brief index. The site build writes `<desk>/briefs/index.json` from those files when it publishes.

`solada/forensics/` is experimental Solana research (`sol_forensics`), not a paper book, run log, brief, or pulse. It is not listed in `desks.json` and this check does not validate it. The privacy guard still scans it. Flag codes in those reports are hypotheses, not pulse signals.

`solada-prefilter/vetoes.json` is research output on the desk-data branch (`schema/solada-vetoes.schema.json`). It is not a paper book. CI validates the fixture, and desk-data-guard validates the file when the branch has one. `buy_signal` is false. Evidence values are wallet tags only. Codes are `dev_sold`, `serial_launcher`, `lp_pulled`, and `ahead_of_dump`. `counts` is the per-code total. An `ahead_of_dump` veto carries a `reason` with amounts only. `fresh_funded` is a caution flag on `dev_checks.json`, not a veto code and not a dashboard panel field.

`x_buzz.json` (desk-data root) is a measured factor (`schema/x-buzz.schema.json`). It is not a paper book. CI validates `scripts/fixtures/x-buzz.json`. desk-data-guard validates `x_buzz.json` when the branch has one. A missing file is normal. `kind` is `x_buzz`. `veto` and `buy_signal` are false. Each `tokens[]` row has `chain` `solana` or `cardano`, `hourly_24h` of 24 integers, and `confidence` `high`, `medium`, or `low`. Sample counts and `velocity.ratio_1h_vs_prev_hr` may be null.

`x_buzz_history.jsonl` (desk-data root) is the append-only run log for that factor (`schema/x-buzz-history.schema.json` is one line). CI validates `scripts/fixtures/x-buzz-history.jsonl`. desk-data-guard validates the file when the branch has one. A missing file is normal. Each line is one token for one run: `asof`, `ticker`, `chain`, `id_short` (short id, 24 characters or fewer), mention counts, a nullable velocity ratio, nullable sample stats, `confidence`, `alert` (boolean), and `paper_position` (one book letter, or null). Blank lines are ignored.

`buzz_outcomes.jsonl` (desk-data root) is one spike from that history (`schema/buzz-outcomes.schema.json` is one line). `buzz_outcomes_summary.json` is the tally (`schema/buzz-outcomes-summary.schema.json`). CI validates `scripts/fixtures/buzz-outcomes.jsonl` and `scripts/fixtures/buzz-outcomes-summary.json`. desk-data-guard validates both when the branch has them, including a privacy check (no post text, no handles, no wallet ids). A missing file is normal. The outcomes file is rewritten each run: one line per spike, latest state wins. It is not an append log. `buy_signal` and `veto` are false. A null price, liquidity, or flag was not observed. `scripts/buzz_outcomes.py` writes both files. It is not on a schedule in this repo.

`solada-prefilter/liquidity_moves.json` is research output on the desk-data branch. It has no JSON schema of its own, so an extra field is allowed. The privacy guard checks `actor_tag` only. `tokens[]` stays in Snek rank order. Each token may include `last_checked`: UTC `YYYY-MM-DDTHH:MM:SSZ`, or null when that token has not been read. The value is a time. It is not a wallet, a unit, or a stake key. Which token is scanned first is least-recently-checked and is stored in the private cache, not in this list's order.

## Paper book
- **required:** `schema_version` (= 2), `desk`, `book` (book id, e.g. "solada", "pumpfun"), `open[]`, `closed[]`, `rules[]`
- optional: `chain`, `unit` (what `size`/`pnl` are in, e.g. "ADA" or "ADA-equivalent"), `ticket_size`, `disclaimer`, `default_window_hours`, `levels{}`, `summaries[]` (each `at`, `type`, `note`, plus anything else)
- **Ticket** (in `open` and `closed`). **Required:** `id`, `ticker`, `opened_at`, plus `closed_at` for closed tickets. Usual fields: `status`, `level`, `size`, `window_ends_at`, `entry{}`, `kill_line` (`{text, …numbers}`), `thesis[]`, `marks[]`. For closed tickets, also `exit{}`, `exit_reason`, `result{pnl, pct, unit}`, `review`.
- **Rule:** `rule` (**required**), `id`, `added_at`, `source`. Optional display fields: `title` (plain research title), `category` (`kill`, `entry`, `sybil`, `swarm`, `liq`, `review`), `evidence` (ticker strings), `mirrors` (another rule, written `book:id`, for example `solada:j`). A mirror that does not match a rule id yet is shown as that text. It does not fail the page or the schema check.

## Run log
- **required:** `schema_version` (= 2), `desk`, `asof`, `headline`
- usual: `levels{ticker: verdict}`, `kill_lines`, `paper_actions`, `rules_added`, `followups[]`, `sources`, `history[]` (past runs, same shape; each needs `asof`; keep about the last 20)
- optional: `risk_check` `{ticker: {risk, tags, source, note}}`. `risk` is a string or null. `source` is `scanner` or `dev_checks`. A retired top-level map with the same shape is still accepted. A coin ticker is not that map.
- optional: `verdicts[]` and `cardano_v2` for the Cardano tab. Each verdict may add `launchpad`, `kill_metric`, `kill_value`, `kill_limit`, `pct_from_line`, `why`, and `whale_line`. `cardano_v2` may add `entry_bar`, `launchpads`, `launches`, `rotation`, `swarms`, `snipers`, `hot`, `clusters`, and `heat`. All of it is optional. The field list and examples are in [dashboard/PLUGINS.md](../dashboard/PLUGINS.md#cardano-tab-v2). Extra fields stay allowed.

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
