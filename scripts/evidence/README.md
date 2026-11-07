# Evidence packet (manual pilot)

Read-only packet for evidence that already exists in this repo. It prepares a compact record before any later reasoning. It does not scan, trade, alert, or push.

Schema version: **1**. Kind: `sightline_evidence_packet`. The shape is `packet.schema.json`. `coverage.full` is always false.

## What it reads

| Source | Where | As-of field |
|---|---|---|
| Solana scan | `desk-data` `solana-desk/health.json`, `trades.csv`, `tasks.csv`, `positions.json`. `scan_timing.jsonl` when present. | `health.json` `last_run_utc` / `run_times_utc`, or `scan_timing.jsonl` `started_utc` for that run |
| Solada prefilter | `desk-data` `solada-prefilter/last-run.json` | `at` |
| Daily status | `desk-data` `reports/status/daily_status_latest.json` | report `date`, compared with the cron slot |
| Forensics | Deferred. The source is present and unknown. Flag and panel detail is not copied. | none. Unknown is not healthy |
| Workflows | Optional Actions snapshot from `fetch_actions.py` | latest run `run_started_at` and `conclusion` |

The builder does not call the network and does not read the wall clock. `--as-of` is `YYYY-MM-DDTHH:MM:SSZ`. The same repo, data checkout, as-of, previous packet, and snapshot produce the same bytes.

Closes and fills are recomputed from `trades.csv` on every build and matched to `positions.json` ids. A previous packet is only a diff. A prior id that the new recompute does not contain is a dropped record. A missing file is `missing`, `failed`, or `unknown`. It is not healthy.

## Freshness

`freshness.json` is provisional. `approved_trading_rule` is false. The numbers are not desk rules.

- Scan: older than 3600 seconds (`scan_60min`) against `--as-of`.
- Prefilter: older than 9000 seconds (`prefilter_150min`) against `--as-of`. The prefilter has no GitHub cron. Its external slot is not parsed from a comment.
- Daily status: the single cron in `.github/workflows/daily-status.yml`, plus 7200 seconds (`daily_2h_after_scheduled_slot`). This uses the scheduled slot, not file modification time, and it is not the 19:00 America/Toronto rule inside `scripts/status/daily_status.py`.

Day-14 lines (`MIN_F_CLOSES`, `EARLY_STOP_EXP`, `TICKET_SOL`) and `DATA_FAIL_VOID` are read from the frozen config and `specs/day14_verdict.py`. If they disagree, the threshold is unknown. This packet does not issue finality.

Predicates that are not implemented stay `not_implemented`: a prefilter trading verdict, a forensics trading verdict, NFT and Solada desk workflow queries, Actions run-history drops, and alert replacement.

## Public text

The packet keeps counts, desk position ids, and short codes. It does not copy notes, mints, wallets, social ids, or secrets. Links are `https://github.com/<owner>/<repo>/blob/<commit>/<path>` or an Actions run URL built from the repository slug and a numeric run id. A remote URL's userinfo is ignored.

## Manual workflow

`.github/workflows/evidence-packet.yml` is `workflow_dispatch` only. Permissions are `contents: read` and `actions: read`. Output is an artifact. GitHub does not offer dispatch for a workflow file that is not on the default branch. This pilot does not merge itself to create that button. Until dispatch works, a local read-only replay is the evidence, and Actions acceptance stays gated.

Run duration is copied from the timing API when the snapshot has it. The billing-account lookup is deferred, so remaining quota is unknown. Duration is not billed minutes. There is no token-saving estimate.

Workflow health is the newest `schedule`, `push`, or `workflow_dispatch` run in a 20-run page. A skipped pull request stays recorded as the latest run and does not replace that health. If the page has no schedule, push, or workflow_dispatch run, the state is unknown.

Existing scan, prefilter, trading, forensics, guard, and alert workflows are not modified. Existing alerts stay in place.

The artifact contract is: snapshot + manifest + pinned SHAs; replay requires checkouts at recorded SHAs. It does not contain desk-data. `replay/replay.json` records the as-of, both commit SHAs, the Python version, the requirements file hash, and a sha256 for each retained input. Replay rejects a missing SHA, a HEAD that differs, tracked edits in either checkout, a different Python version, or a hash that does not match. A source timestamp after the captured as-of stays unknown. The privacy guard still scans every uploaded file and fails closed. Artifact retention is 7 days.

```bash
python3 scripts/evidence/packet.py --repo . --data /path/to/desk-data \
  --as-of 2026-11-07T21:30:00Z --repo-slug "<owner>/<repo>" --out /tmp/evidence
```
