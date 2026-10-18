# Solada desk (Snek Sentinel)

Snek Sentinel's Solada/Cardano research desk, with a Pump.fun (Solana) comparison book.
Paper trades and research only. No real execution, no owner wallet data (see ../PRIVACY.md).
Files follow the shared format, version 2 (field guide and old-to-new names: ../schema/README.md).

| File | What it is |
|---|---|
| `paper.json` | Solada/Cardano paper book: open and closed tickets, kill lines, rules, summaries |
| `log.json` | Run log: latest run, plus `history[]` of past runs and `followups[]` |
| `pumpfun/paper.json` | Pump.fun comparison paper book (rules mirrored from `paper.json`) |
| `briefs/YYYY-MM-DD.json` | Daily briefs (from now on). The site build writes `briefs/index.json`; this bot does not. |
| `pulse.json` | Hourly pulse: per-name metric points (last 7 days; older points move to `archive/pulse-YYYY-MM.json`) plus the early-runner `radar[]`. Numbers only, no tickets. |
| `panels.json` | Which panels the desk page shows, in order (see ../dashboard/PLUGINS.md). |
