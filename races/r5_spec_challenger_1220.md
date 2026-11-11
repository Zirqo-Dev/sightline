# R5 "Established breakout" — Challenger spec, Nov 11 2026 ~12:20 PM EST (display only, untested, evidence WEAK)
Label: close variant of killed Trend/momentum (time-series breakout); NOT C. Cannot revive Trend/momentum.
Data: Jupiter lite-api Tokens v2 (/tokens/v2/search, toptraded 24h + toporganicscore 24h, limit 100 each). CoinGecko cross-check only.
Universe frozen Mondays 00:12 UTC: isVerified; mcap>=$25M; mcap/fdv>=0.30; liquidity>=$1M; firstPool age>=90d; organicScore>=50; not tagged meme/stable/lst/original-lst/xstocks/stocks/prestocks/equities/rwa/yb/jup-lend-earn; denylist SOL,wSOL,ETH,cbBTC,WBTC,ZEC,HYPE,JLP,CASH, any symbol containing USD/EUR. <8 tokens => cash that week.
Entry (each :12/:42 run): 24h priceChange +12%..+60%; 6h priceChange >0; 24h volumeChange >=+100%; organic buy vol/buy vol >=5%. Fill at logged Jupiter quote + 1% round trip.
Exits: -15% hard stop; trailing -15% from peak once +10%; 10-day time stop; no TP; dead/no route/3 failed sells/liq<$500k => 0.
Limits: 1 entry/day; max 5 open @20% each; 7-day re-entry cooldown.
Budget: 5 calls/run (+2 Monday); total cap 40->45. 429/retired => skip+log, no paid key.
Gate adds: frozen denylist/tags in config, Monday universe snapshot file, per-run R5 call count, unit test rejecting meme-tagged and 60-day-old tokens.
Expected ~0-4 entries/month; today 0 qualifying.

## AMENDMENT — owner yes WITH memes (12:28 PM EST) + Challenger PASS (12:30 PM EST) — SUPERSEDES conflicting lines above
- Meme exclusion DROPPED. One rule for all tokens; no meme-specific threshold.
- Common rules: cost rule (d) (60 bps + 0.25 x impact, +150 bps on both hard and trailing stops, cap 600); fixed 0.5 SOL ticket from 5.0 SOL equity; max 5 open; max 1 new entry/day; 7-day cooldown; Monday universe calls inside the 5-call cap (entry quotes deferred to the next run).
- Keep the +60% blow-off cap and the 5% organic-buy check.
- Caps: 14/14/7/3/2 + R5 5 = 45.
- Board: split R5 results meme vs non-meme (n, net %), using the Jupiter 'meme' tag at the Monday freeze. R3 overlap is accepted and labelled: "R5 holdings also in R3: k of n" + "R3 and R5 will move together in a meme rally; two leads there are one bet, not two."
- Board label: "R5 Established breakout (memes included) — close variant of killed Trend/momentum (time-series breakout); display only, untested". Evidence WEAK. Not C.
- Live snapshot 12:30: 18 tokens (10 non-meme, 8 meme). STONK and CATE need the manual tag check at freeze.
- Gate tests: 60-day-old rejected; stable-tagged and denylisted (cbBTC) rejected; mcap/fdv 0.28 rejected; meme-tagged passing token ACCEPTED; +65% in 24h no entry.
