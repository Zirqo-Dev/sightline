# NFT desk (KING)

KING's all-chain NFT research desk. Status: **pending**, no files yet.
Paper trades and research only. No real execution, and never owner wallet addresses, real holdings, balances or real transactions (see ../PRIVACY.md).

Files go here in the shared format (field guide: ../schema/README.md):
| File | What it is |
|---|---|
| `paper.json` | Paper book: open and closed tickets with kill lines, rules learned, summaries |
| `log.json` | Run log: latest run plus `history[]` |
| `briefs/YYYY-MM-DD.json` | One daily brief per day |

When KING starts writing, fill in its entry in `/desks.json` (log, briefs, books) and set `status` to `active`, so CI validates the files and the dashboard shows them.
The existing Sightline NFT site at the repo root stays where it is for now; it moves in a later step.
