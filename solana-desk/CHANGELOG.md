# Solana Paper Desk changelog
Mirror of the Notion "Solana Paper Desk iteration log". Every plan or config change gets a row.

| Version | Date | Source | Summary | Cards |
|---|---|---|---|---|
| v1 | 2026-10-24 | Bousky | First Cardano paper desk plan reusing Sightline, KING and Snek. | |
| v2 | 2026-10-24 | Bousky | Cardano plan folded in the @0xTimQ article, cost ladder and cloud-only setup. | |
| v3 | 2026-10-24 | Bousky | Switched to Solana; own solana/ folder, KING daily routine, Snek on Solada only. | |
| v4 | 2026-10-24 | External review | Review 1: hypothesis, kill rule and random shadow book added. | |
| v5 | 2026-10-24 | External review | Review 2: execution checks on all books, stale-aware exits, Baseline run separately. | |
| v6 | 2026-10-24 | External review | Review 3: fixed 0.5 SOL ticket, min 20 F closes, day-3 freeze, Go gate, branch protection. | |
| v6.1 | 2026-10-24 | External review | Wiring fix: generated files on desk-data, main via PRs only, checkpoints, prediction log. | SPD-2, SPD-7, SPD-13 |
| v6.2 | 2026-10-24 | Bousky | Simpler rules: day-14 clear-failure stop, day-28 single test, trimmed as warning; daily no-AI health line; reviews closed. | SPD-3, SPD-5, SPD-11 |
| build-1 | 2026-10-24 | Test data | Day-1 API check (findings.md) plus draft desk code on box, 22 tests pass; heartbeat PID 39730 started. Proposed fixes: quote-side SOL liquidity floor, pool-aware LP rule, top-10 excl. pools, score_normalised. | SPD-1, SPD-10, SPD-18 |
| v6.3 (day-1 field fixes, looser start) | 2026-10-24 | Bousky + Test data | 10 SOL SOL-side pool floor replaces $50k USD; ticket cap 5% of pool SOL side (3% impact check kept); LP lock by pool type; top-10 excl. pool/curve/locker; score_normalised <= 40; holders from RugCheck; ungraduated = safety skip not_graduated. Snapshot: 9/14 graduated tokens pass 10 SOL. 25 tests pass. | SPD-1 |
| build-2 | 2026-10-25 | Grok Bot | Setup PR opened: desk code, tests (fixtures redacted), README, plan v6.3 under solana-desk/; desk-data branch ready. | SPD-13, SPD-7 |
| v6.4 (scanner on GitHub Actions) | 2026-10-25 | Bousky | v6.4: scanner moved to GitHub Actions every 30 min (box not awake 24/7: ~6h heartbeat gap). Scan-only by default; fill refused until config freeze. Data to desk-data only. Box heartbeat stopped. | SPD-10 |
