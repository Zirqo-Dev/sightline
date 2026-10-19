# sol_forensics validation

Addresses below are third-party on-chain accounts from the analysed mints.

## Helius run (2026-10-19, Actions run 39243993172)

The smoke step passed: `scheme=https`, `host=mainnet.helius-rpc.com`, `api_key_len=36`, `api_key_form=uuid`, slot `458814741`, QBITS supply amount `1000000000000000` (ui `1000000000`, decimals 6). That step is 2 calls.

Acceptance calls, each report's own `calls_used`:

| Mint | Symbol | calls_total | partial | history_too_deep | calls_used |
|---|---|---:|---|---|---|
| `Ceuu7uQHwhtHuMhPWrWL9dgri3FaGJZFkTy6FoDow7NA` | QBITS | 166 | false | false | supply 1, signatures 82, transactions 43, token accounts by owner 20, largest accounts 1, account info 19 |
| `DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263` | Bonk | 216 | false | true | supply 1, signatures 128, largest accounts 1, account info 40, token accounts by owner 20, transactions 26 |
| `2m2m1dpqQPa97MU1voKG2p7G9ts4eR7GjmYNeXiTpump` | Tesla | 109 | false | false | supply 1, signatures 40, transactions 56, token accounts by owner 8, largest accounts 1, account info 3 |

Smoke plus these three reports is 493 JSON-RPC calls. Helius meters these standard methods at 1 credit each, so this run is 493 credits. The usage dashboard was not opened.

## QBITS headline

Report `run_at` 2026-10-19T11:21:13Z. `partial` is false. Nine holder rows were written. `getTokenLargestAccounts` succeeded in one call.

Summary:

- `pct_supply_slot0` 0.5321, `n_buyers_slot0` 1
- `pct_supply_window` 34.1687 across 15 wallets
- `fresh_wallet_ratio` 0.0667
- `largest_cluster_pct` 17.2745
- `largest_cluster_still_held_pct` 14.9346
- flags: `CLUSTERED`, `STILL_LOADED`, `DEV_PAID_BUY`
- `dev_pct_supply` 0.5321, so `DEV_HEAVY` stays off
- `pump_time_matches` true

Largest cluster `c1` is the Solada deployer `A1qaJWQDfC9T7AcSRNwfxgiJrHUzhpJ3f8qTDUMVVcXQ` and the backing wallet `HCYvN1ukbRjWiBi1wXaU4fSRh8ottmA9KZCifJAPFi7Z`, joined by direct QBITS transfers (8 edges, weight 1.0). Window amounts are 8.6438% and 8.6307%. Still held is 14.9346%: backing wallet 14.9215% (`149214658000000` raw) plus deployer 0.0132%. The earlier public-RPC read of the backing wallet was 15.4388%; the balance moved between the two runs. The fee-paid transfer itself is unchanged.

`dev_paid_signature` is still `23asKsnQdXsv152CVTGAQKuYRV6LKPft5Ty551oYQc7gRN3bZEE9xehaz6NW7rWJUXFeoVFg5ThbHsLXsLeRMiHW` (slot 458581783, offset 72). Fee payer is the deployer. That transaction moved 5,189,008,000,000 raw tokens.

## Bonk

`run_at` 2026-10-19T11:22:52Z. pump.fun still returns a bonding curve, so the tool paged that account. After 50 pages the oldest signature was still ahead. `history_too_deep` is true, `partial` is false, buyers is empty, and no funder was taken from the capped page. `--holders` then ran: 20 holder rows, no flags, largest cluster 0. Eight of those holder wallets are `unresolved_busy`.

## Tesla

`2m2m1dpqQPa97MU1voKG2p7G9ts4eR7GjmYNeXiTpump`, `run_at` 2026-10-19T11:24:50Z. This was the non-Solada pump mint in the Helius acceptance job. It is not a quiet launch. Slot 0 holds 50.4316% of supply across 2 wallets, so `HIGH_SNIPE` fires. `BwWK17cbHxwWBKZkUYvzxLcNQ1YVyaFezduWbtm2de6s` received 50% in slot 0 and the create fee payer `DpnHfEwVeH7ByPqieojtUQxrcUTmUxwXet6HBvXEmyhg` paid that fee (`DEV_PAID_BUY`). The dev's own slot-0 share is 0.4316%. No cluster reached the merge threshold. `fresh_wallet_ratio` is 0. `pump_time_matches` is true.

## Earlier public-endpoint runs

| Mint | Symbol | calls_total | partial | history_too_deep |
|---|---|---:|---|---|
| QBITS | QBITS | 121 | true | false |
| Echo | Echo | 427 | false | false |
| UNDINE | UNDINE | 211 | false | false |
| Bonk | Bonk | 51 | false | true |

The first QBITS index line (147 calls) still has `DEV_HEAVY` from before creation-slot supply was split from later receipts. Echo slot 0 was 33.4451% (`HIGH_SNIPE`, `CLUSTERED`). UNDINE slot 0 was 54.0738% (`HIGH_SNIPE`). Three wallets appear in both of those early-buyer lists: `HRy2iccTNUuZrFYW8uabc5C7wfKhoPh8AGPbABcXJ4ve`, `E2x1ewwXUwxABtFcssgxQLY6PrcRQuhLJRWPCR3WyeJb`, `4THgteoKKUXC162SJYiQu42KWV7UW513itTebbiZvuFC`. The tool clusters inside one mint only.

## Spot checks

Solscan's public transaction page for these signatures returns HTTP 200. The HTML shell leaves `txInfo` null and fills the transfer list in the browser, so this environment could not read the payer from the HTML. `explorer.solana.com` returned HTTP 429. The five checks below are a fresh `getTransaction` (`jsonParsed`, `maxSupportedTransactionVersion` 1) against the public RPC, compared with the QBITS report. Pages a person can open:

- https://solscan.io/tx/5uNwRn3xZHP7pcC7zZYPBFfKqhUU9KcKCjapZ6SYvWiPRxiJVdr7R1CL3Wt2WGrzZ3J7H5HDKXmQusczVenbeyyx
- https://solscan.io/tx/23asKsnQdXsv152CVTGAQKuYRV6LKPft5Ty551oYQc7gRN3bZEE9xehaz6NW7rWJUXFeoVFg5ThbHsLXsLeRMiHW
- https://solscan.io/tx/2aAVnCokwj7m2u7JsEZ6g5fE6t7YmpBaFspPiqFnAK5HdhcaouHhs4YCivfJcMduKhaUQdKT4jvknWkZq4MXbZh6
- https://solscan.io/tx/v4bnNUse6ZPSY5LqF5d7T5bNo64n1h9nKS52eaJ83mgqhgwEYai7CHDYU74NWVKenfbpe4LWR7XoM5k1s4DHTk9
- https://solscan.io/tx/f9AK4xnuemwp3NoRdqi6Kk1LvD4e1S6csaQceKd8xBBK9zV9G8gQpSWmvj2xqUEn5sfH3n1dazAwVKe7ANkXtEj

1. Buyer `A1qaJWQDfC9T7AcSRNwfxgiJrHUzhpJ3f8qTDUMVVcXQ`, create signature `5uNwRn3xZHP7pcC7zZYPBFfKqhUU9KcKCjapZ6SYvWiPRxiJVdr7R1CL3Wt2WGrzZ3J7H5HDKXmQusczVenbeyyx`. Fresh read: version 0, slot 458581711, blockTime 1792358369, fee payer the deployer, QBITS delta `+5320514047134` (0.5321% of supply `1000000000000000`). The curve `DAkroVvy5Gvbost9F7uXogiGein2ikNyrJipmyxzyuj6` received the rest. Matches `dev_pct_supply` and the deployer's `amount_slot0_raw`. The report's window `pct_supply` of 8.6438 is the sum of later positive deltas inside the first 30 buy transactions.

2. Buyer `HCYvN1ukbRjWiBi1wXaU4fSRh8ottmA9KZCifJAPFi7Z`, signature `23asKsnQdXsv152CVTGAQKuYRV6LKPft5Ty551oYQc7gRN3bZEE9xehaz6NW7rWJUXFeoVFg5ThbHsLXsLeRMiHW`. Fresh read: version 0, slot 458581783, blockTime 1792358385, fee payer `A1qaJWQDfC9T7AcSRNwfxgiJrHUzhpJ3f8qTDUMVVcXQ`. Deployer delta `-5189008000000`, backing wallet delta `+5189008000000`. Matches `dev_paid_signature` and `DEV_PAID_BUY` on the Helius report as well. `still_holding_pct` on that report is 14.9215. The earlier public-RPC balance read was 15.4388.

3. Funder of buyer `9Uwz2gwYBnZLonJGLQhzFbU2XiJqBj4hcEsJU9pN39ik`. Signature `2aAVnCokwj7m2u7JsEZ6g5fE6t7YmpBaFspPiqFnAK5HdhcaouHhs4YCivfJcMduKhaUQdKT4jvknWkZq4MXbZh6`. Fresh read: legacy, slot 457827305, blockTime 1792194773, parsed `system.transfer` of 31,000,000,000 lamports from `is6MTRHEgyFLNTfYcuV4QBWLjrZBfmhVNYR6ccgr8KV`. Matches the report hop (asset SOL, kind `system.transfer`, age at buy 163633 seconds, status `traced`).

4. Funder of buyer `ExfMqYHCdyfDwgLy4xTVYdB1wG2Drs3Rf4jrYKseiUzp`. Signature `v4bnNUse6ZPSY5LqF5d7T5bNo64n1h9nKS52eaJ83mgqhgwEYai7CHDYU74NWVKenfbpe4LWR7XoM5k1s4DHTk9`. Fresh read: legacy, slot 446959082, blockTime 1789383370, parsed `spl-token.transferChecked` of 57,126,370 USDC from `DPqsobysNf5iA9w7zrQM8HLzCKZEDMkZsWbiidsAt1xo`. Matches the report (status `old_wallet`, age 2975969 seconds).

5. Funder of buyer `EYJYnVno4jTxjTVAv9zUWd1BxHsYSVFn55T7PRfZT4sk`. Signature `f9AK4xnuemwp3NoRdqi6Kk1LvD4e1S6csaQceKd8xBBK9zV9G8gQpSWmvj2xqUEn5sfH3n1dazAwVKe7ANkXtEj`. Fresh read: version 0, slot 442167552, blockTime 1787857315, fee payer `AgmLJBMDCqWynYnQiPCuj9ewsNNsBJXyzoUhD9LJzN51`, parsed `transferChecked` of 667,732 USDC from `A67JYbMwUhJRThDB6HeVZKQkiycKwpSHdWJRWCu33n9C` to the buyer. The report records that USDC source, which is the parsed transfer, and status `old_wallet` (age 4501091 seconds).

`8Fux3NDfxHxiK9W9ixzvQTw4GGgMazNwRvDS5Gvwejwk` (slot offset 15, 1.4445% in the window) stayed `unresolved_busy`. No funder was written for it.
