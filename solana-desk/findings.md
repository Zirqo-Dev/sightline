# SPD-1 Day-1 threshold check (draft, 2026-10-24 ~14:15 EDT)

Sample: one pull of the DexScreener paid feed (30 profiles + 30 boosts; 23 and 25 Solana), pairs for the first 30 unique Solana mints, RugCheck full reports for 4 tokens (PumpSwap, Pump.fun bonding curve, Meteora DAMM v2/DLMM, mixed), Jupiter quotes, public RPC. One snapshot only: pass rates below are indicative, not a measured rate. Raw responses are in `raw/`.

## Endpoints
| API | Result |
|---|---|
| DexScreener profiles / boosts / `tokens/v1/solana/{mints}` | 200, no key. Multi-chain; filter `chainId=solana` |
| Jupiter `lite-api.jup.ag/swap/v1/quote` | **Still answers (200)** today; `api.jup.ag` keyless also 200. Plan still says to use the free key, since lite-api is being retired |
| RugCheck `/report/summary` and `/report` | 200, no key |
| Solana public RPC | `getSlot` 200; **`getTokenLargestAccounts` 429 twice** (method-level rate limit). Can't be relied on for holder math → use RugCheck `topHolders` + `knownAccounts` |

## Checks
1. **LP lock, PumpSwap (`pump_fun_amm`)**: `lpLockedPct` = 100 structurally (LP burned at graduation). The 90% rule always passes, so it carries no signal there. Keep it, but don't treat it as evidence.
2. **LP lock, Meteora DLMM (`meteoraDlmm`)**: `lpTotalSupply` = 0, so `lpLockedPct` = 0 structurally. The 90% rule would fail **every** DLMM pool → broken field. Fix: pool-type-aware rule. Mark DLMM as not applicable, and rely on the liquidity-drop exit and the execution checks. DAMM v2, Orca and Raydium keep the 90% rule (the SNS DAMM pool correctly shows 0% locked).
3. **Pump.fun bonding curve (`pump_fun`)**: 16/30 feed tokens are still on a bonding curve (pumpfun or meteoradbc). DexScreener liquidity is 0 there and there's no LP. Treated as `too_fresh` (not graduated).
4. **Top-10 holders**: RugCheck `topHolders` includes the pool or curve account (98.6% on one PumpSwap token, 55% on a bonding-curve token). Raw top-10 82.5% becomes **28.7% after excluding `knownAccounts` of type AMM/LOCKER** → the 35% cap is workable only with that exclusion (implemented).
5. **RugCheck score**: higher = worse. `score` is raw and unbounded (1 to 17,425 seen); `score_normalised` is roughly 1–100 (1, 1, 33, 58 seen). **"max 40" must apply to `score_normalised`**, not to `score`. Caveat: a token with a "Low Liquidity: danger" flag still scored 33 (passes); liquidity is policed separately.
6. **Liquidity field**: DexScreener `liquidity.usd` counts the base-token side at market price. One PumpSwap pair showed $83k "liquidity" with only **6.3 SOL** on the quote side; RugCheck called the same pool low-liquidity. The usd floor is therefore a broken field for thin memecoin pools.
7. **Jupiter `priceImpactPct`** is a fraction string (0.049 = 4.9%). 0.5 SOL into that 6.3-SOL pool = 4.9% impact → fails the 3% execution check. That's correct behaviour.

## Thresholds vs v6.2
| Threshold | Verdict |
|---|---|
| Liquidity floor $50k (usd) | **Needs a broken-field fix**: switch to a quote-side SOL floor. Snapshot: usd ≥ $50k passed 2/30, while quote ≥ 25 SOL passed 7/30 (and the usd passers included a 6.3-SOL pool). **Suggested `min_quote_liquidity_sol = 25`**, which is the same bar the existing 1%-of-pool ticket cap already implies for 0.5 SOL. Bousky decides |
| LP lock ≥ 90% | Holds for DAMM/Orca/Raydium; **pool-type-aware fix** for DLMM (N/A) and PumpSwap (structural) |
| Top-10 ≤ 35% | Holds **only** with pool/curve/locker exclusion (implemented) |
| Dev ≤ 10% | Holds (creator from RugCheck) |
| Risk score ≤ 40 | Holds **on `score_normalised`**; field choice fixed |
| Mint/freeze authority open = skip | Holds; null on all samples |
| Age 15 min–48 h | Holds; many feed tokens are under 1 h, so `too_fresh` will be common |
| Execution: impact ≤ 3%, ticket ≤ 1% pool, ≥1 sell/h | Holds. Impact read as fraction × 100 |
| Holder data from public RPC | **Replace** with RugCheck `topHolders` (RPC 429) |

Honest read: on this snapshot the funnel is very narrow (about half the feed isn't graduated, and few pools have ≥ 25 SOL). Expect low F counts; "filters too strict" at day 28 is plausible, as the plan says. Re-measure pass rates over days 2-3 before the freeze.

## Quoted impact vs the reserve curve (2026-10-28)

Checked the three open paper positions whose SOL reserves were still on `positions.json` (`liq0`) against the buy rows in `trades.csv`. Ticket is 0.5 SOL. No mints are repeated here.

| Pool SOL at entry | Jupiter `impact_pct` | Constant-product `0.5 / (pool + 0.5)` | Haircut then (150 + 0.5 × Jupiter bps, cap 300) | Haircut now (60 + 0.25 × curve bps) |
|---|---|---|---|---|
| 148.27 | 2.4927% | 0.3361% | 2.7463% | 0.6840% |
| 179.79 | 1.2446% | 0.2773% | 2.1223% | 0.6693% |
| 257.14 | 1.0882% | 0.1941% | 2.0441% | 0.6485% |

The 2.0–2.7% band is not what these three pools reported. One of them is in it (2.49%). The other two are 1.09% and 1.24%. The constant-product band 0.19–0.34% matches the reserve sizes.

A fresh Jupiter quote for 0.5 SOL, 0.05 SOL and 0.001 SOL on the same route was fit to one constant-product curve. On the two pools whose vault did not move during the read, the mid-size `outAmount` predicted from the other two sizes matched the quoted `outAmount` within 0.02%. That curve's impact for 0.5 SOL was 0.22% and 0.07%, against Jupiter fields of 0.28% and 0.64% on those same quotes. The third pool's vault was moving, so it was not used.

`priceImpactPct` is the gap between the input's USD value and the output marked at a cached USD price. It is not `dx / (pool + dx)`, and it is not how far `outAmount` sits from the reserve curve. The haircut uses the curve. The Jupiter field stays on the row as `impact_pct`.
