# NFT desk (KING)

KING's all-chain NFT research desk. Status: **active** (paper book, run log, daily briefs).
Paper trades and research only. No real execution, and never owner wallet addresses, real holdings, balances or real transactions (see ../PRIVACY.md).

Files go here in the shared format (field guide: ../schema/README.md):
| File | What it is |
|---|---|
| `paper.json` | Paper book: open and closed tickets with kill lines, rules learned, summaries |
| `log.json` | Run log: latest run plus `history[]` |
| `briefs/YYYY-MM-DD.json` | One daily brief per day |

`panels.json` is the desk page layout (see ../dashboard/PLUGINS.md). KING can edit that file; the dashboard reads it as-is.

The classic Sightline NFT finder (watchlist, paper blotter, CIO tape) lives at `site/` and is linked from this desk's page. After deploy it is at `https://zirqo-dev.github.io/sightline/nft/site/`. It reads `site/watchlist.json` and `site/intel.json` next to its own page. Paste an OpenSea key in that page only. Never commit it.
