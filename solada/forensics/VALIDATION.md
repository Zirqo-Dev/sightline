# sol_forensics validation

Runs below used the public mainnet endpoint (`SOLANA_RPC_URL` was unset) on 2026-10-19. Addresses are third-party on-chain accounts from the analysed mints.

## Calls per coin

| Mint | Symbol | calls_total | partial | history_too_deep | What the calls were |
|---|---|---:|---|---|---|
| `Ceuu7uQHwhtHuMhPWrWL9dgri3FaGJZFkTy6FoDow7NA` | QBITS | 121 | true | false | Supply 1, signatures 62, transactions 37, token accounts by owner 15, largest-accounts 6 (all HTTP 429). This is the corrected rerun. An earlier partial run used 147 calls before the funding cache existed. |
| `9mBi6hL2omjfeQ5JiNdLDf7zEAmXVuSBsSU3kNm9pump` | Echo | 427 | false | false | Supply 1, signatures 129, transactions 272, token accounts by owner 25. Holders step not requested. |
| `6C7hgz2RhinRFMyv8717DCnBRncqqsrkHUmii8Xkpump` | UNDINE | 211 | false | false | Supply 1, signatures 128, transactions 60, token accounts by owner 22. Holders step not requested. |
| `DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263` | Bonk | 51 | false | true | Supply 1, signatures 50. Steps 2 and 3 skipped. |

`index.jsonl` keeps every run, including the first QBITS line (147 calls). That line still has `DEV_HEAVY` from before creation-slot supply was split out of later receipts. The report file is the corrected run.

## QBITS headline

Corrected report, `run_at` 2026-10-19T03:37:32Z.

Summary:

- `pct_supply_slot0` 0.5321, `n_buyers_slot0` 1
- `pct_supply_window` 34.1687 across 15 wallets
- `fresh_wallet_ratio` 0.0667
- `largest_cluster_pct` 17.2745
- `largest_cluster_still_held_pct` 15.4519
- flags: `CLUSTERED`, `STILL_LOADED`, `DEV_PAID_BUY`
- `dev_pct_supply` 0.5321 (creation slot only), so `DEV_HEAVY` stays off
- `pump_time_matches` true (pump.fun `created_timestamp` 1792358369000 and create `blockTime` 1792358369)

Largest cluster `c1` is the Solada deployer `A1qaJWQDfC9T7AcSRNwfxgiJrHUzhpJ3f8qTDUMVVcXQ` and the Solada backing wallet `HCYvN1ukbRjWiBi1wXaU4fSRh8ottmA9KZCifJAPFi7Z`. The edge type is `direct_transfer` of the QBITS mint, weight 1.0. In the early window the deployer row sums to 8.6438% of supply and the backing wallet to 8.6307%. Still held is 15.4519%, of which the backing wallet's `getTokenAccountsByOwner` balance is 15.4388%.

The backing wallet's earliest signature in the window is `23asKsnQdXsv152CVTGAQKuYRV6LKPft5Ty551oYQc7gRN3bZEE9xehaz6NW7rWJUXFeoVFg5ThbHsLXsLeRMiHW` (slot 458581783, offset 72, about 16 seconds after creation). The fee payer is the deployer. That is the `DEV_PAID_BUY` evidence. The raw amount on that single transaction is 5,189,008,000,000 (0.519% of supply). Later transfers inside the first-30-buy window raise the backing wallet's window total.

`data_quality.partial` is true because `getTokenLargestAccounts` returned HTTP 429 (`Too many requests for a specific RPC call`, `Retry-After: 10`) on all 6 attempts. A later single attempt, after the other mints, still returned 429. Buyer balances from `getTokenAccountsByOwner` did complete. The top-holder list stays empty until `SOLANA_RPC_URL` is set. Eight early wallets are `unresolved_busy` (5 signature pages without reaching the oldest transaction) and two are `old_wallet`.

## Other mints

Echo (creator-side pump coin, graduated, scanned the same day) finished in 427 calls. Slot 0 holds 33.4451% of supply across 5 wallets, so `HIGH_SNIPE` fires. Largest cluster `c1` bought 14.6782% (`CLUSTERED`) and still held 1.2353%. The pump.fun creator `CgkeAmWEunFmhZyGXRii5fAikyWcnxvcw7jKVKTAYpZZ` is the 27.7317% slot-0 buyer. The create fee payer recorded by the tool is `5Z3ctYozREg7vWvoR6NLCkkoxe7NUz96thonupPv9BNm` at 0.181% in that slot.

UNDINE finished in 211 calls. Slot 0 holds 54.0738%. `HIGH_SNIPE` fires. The largest cluster bought 0.2386%, under the `CLUSTERED` threshold. `6wrBJjZqKpzZatgH7WqyUjwhvuQWcAZzrcVFpBjJd8X9` took 53.8843% in slot 0 and was funded less than 24 hours before the buy.

Three wallets appear in both Echo and UNDINE early-buyer lists: `HRy2iccTNUuZrFYW8uabc5C7wfKhoPh8AGPbABcXJ4ve`, `E2x1ewwXUwxABtFcssgxQLY6PrcRQuhLJRWPCR3WyeJb`, `4THgteoKKUXC162SJYiQu42KWV7UW513itTebbiZvuFC`. The tool clusters inside one mint only. This overlap is an observation from the two reports.

Bonk: pump.fun `coins-v2` returns bonding curve `3ne4mWqdYuNiYrYZC9TrA3FcfuFdErghH97vNPbjicr1` and `created_timestamp` 1670531612000, so the tool paged that curve. After 50 pages the oldest signature was still ahead. `history_too_deep` is true, `partial` is false, buyers is empty. No funder was invented from a capped page.

A launch that stayed under the flag thresholds was not in the pump.fun pages this environment could read (the public list only went back about an hour, and the two finished non-Solada coins were both slot-0 heavy). That quiet control stays pending.

## Spot checks

Solscan's public transaction page for these signatures returns HTTP 200. The HTML shell leaves `txInfo` null and fills the transfer list in the browser, so this environment could not read the payer from the HTML. `explorer.solana.com` returned HTTP 429. The five checks below are a fresh `getTransaction` (`jsonParsed`, `maxSupportedTransactionVersion` 1) against the public RPC, compared with the QBITS report. Pages a person can open:

- https://solscan.io/tx/5uNwRn3xZHP7pcC7zZYPBFfKqhUU9KcKCjapZ6SYvWiPRxiJVdr7R1CL3Wt2WGrzZ3J7H5HDKXmQusczVenbeyyx
- https://solscan.io/tx/23asKsnQdXsv152CVTGAQKuYRV6LKPft5Ty551oYQc7gRN3bZEE9xehaz6NW7rWJUXFeoVFg5ThbHsLXsLeRMiHW
- https://solscan.io/tx/2aAVnCokwj7m2u7JsEZ6g5fE6t7YmpBaFspPiqFnAK5HdhcaouHhs4YCivfJcMduKhaUQdKT4jvknWkZq4MXbZh6
- https://solscan.io/tx/v4bnNUse6ZPSY5LqF5d7T5bNo64n1h9nKS52eaJ83mgqhgwEYai7CHDYU74NWVKenfbpe4LWR7XoM5k1s4DHTk9
- https://solscan.io/tx/f9AK4xnuemwp3NoRdqi6Kk1LvD4e1S6csaQceKd8xBBK9zV9G8gQpSWmvj2xqUEn5sfH3n1dazAwVKe7ANkXtEj

1. Buyer `A1qaJWQDfC9T7AcSRNwfxgiJrHUzhpJ3f8qTDUMVVcXQ`, create signature `5uNwRn3xZHP7pcC7zZYPBFfKqhUU9KcKCjapZ6SYvWiPRxiJVdr7R1CL3Wt2WGrzZ3J7H5HDKXmQusczVenbeyyx`. Fresh read: version 0, slot 458581711, blockTime 1792358369, fee payer the deployer, QBITS delta `+5320514047134` (0.5321% of supply `1000000000000000`). The curve `DAkroVvy5Gvbost9F7uXogiGein2ikNyrJipmyxzyuj6` received the rest. Matches `dev_pct_supply` and the deployer's `amount_slot0_raw`. The report's window `pct_supply` of 8.6438 is the sum of later positive deltas inside the first 30 buy transactions.

2. Buyer `HCYvN1ukbRjWiBi1wXaU4fSRh8ottmA9KZCifJAPFi7Z`, signature `23asKsnQdXsv152CVTGAQKuYRV6LKPft5Ty551oYQc7gRN3bZEE9xehaz6NW7rWJUXFeoVFg5ThbHsLXsLeRMiHW`. Fresh read: version 0, slot 458581783, blockTime 1792358385, fee payer `A1qaJWQDfC9T7AcSRNwfxgiJrHUzhpJ3f8qTDUMVVcXQ`. Deployer delta `-5189008000000`, backing wallet delta `+5189008000000`. Matches `dev_paid_signature` and `DEV_PAID_BUY`. The run's `still_holding_pct` for this wallet is 15.4388 from `getTokenAccountsByOwner`.

3. Funder of buyer `9Uwz2gwYBnZLonJGLQhzFbU2XiJqBj4hcEsJU9pN39ik`. Signature `2aAVnCokwj7m2u7JsEZ6g5fE6t7YmpBaFspPiqFnAK5HdhcaouHhs4YCivfJcMduKhaUQdKT4jvknWkZq4MXbZh6`. Fresh read: legacy, slot 457827305, blockTime 1792194773, parsed `system.transfer` of 31,000,000,000 lamports from `is6MTRHEgyFLNTfYcuV4QBWLjrZBfmhVNYR6ccgr8KV`. Matches the report hop (asset SOL, kind `system.transfer`, age at buy 163633 seconds, status `traced`).

4. Funder of buyer `ExfMqYHCdyfDwgLy4xTVYdB1wG2Drs3Rf4jrYKseiUzp`. Signature `v4bnNUse6ZPSY5LqF5d7T5bNo64n1h9nKS52eaJ83mgqhgwEYai7CHDYU74NWVKenfbpe4LWR7XoM5k1s4DHTk9`. Fresh read: legacy, slot 446959082, blockTime 1789383370, parsed `spl-token.transferChecked` of 57,126,370 USDC from `DPqsobysNf5iA9w7zrQM8HLzCKZEDMkZsWbiidsAt1xo`. Matches the report (status `old_wallet`, age 2975969 seconds).

5. Funder of buyer `EYJYnVno4jTxjTVAv9zUWd1BxHsYSVFn55T7PRfZT4sk`. Signature `f9AK4xnuemwp3NoRdqi6Kk1LvD4e1S6csaQceKd8xBBK9zV9G8gQpSWmvj2xqUEn5sfH3n1dazAwVKe7ANkXtEj`. Fresh read: version 0, slot 442167552, blockTime 1787857315, fee payer `AgmLJBMDCqWynYnQiPCuj9ewsNNsBJXyzoUhD9LJzN51`, parsed `transferChecked` of 667,732 USDC from `A67JYbMwUhJRThDB6HeVZKQkiycKwpSHdWJRWCu33n9C` to the buyer. The report records that USDC source, which is the parsed transfer, and status `old_wallet` (age 4501091 seconds).

`8Fux3NDfxHxiK9W9ixzvQTw4GGgMazNwRvDS5Gvwejwk` (slot offset 15, 1.4445% in the window) stayed `unresolved_busy`. No funder was written for it.
