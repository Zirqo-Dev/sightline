"""Desk config (plan v6.5, config v1). Bousky edits; bots never do. Frozen Oct 26, 2026 (end of day 3): any change starts a new book."""
CFG_VERSION = "v1-v6.5"
FROZEN = True
PAPER = True  # always

START_BALANCE_SOL = 10.0      # per book (F, R, S)
TICKET_SOL = 0.5              # fixed, every book
MAX_OPEN_F = 4
R_MATCH_CAP = 2               # R and S capped at 2x F's daily fills
MAX_NEW_F_PER_DAY = 8
LEADS_PER_RUN = 12

SKIP_IF = dict(
    min_liquidity_usd=None,        # v6.3: removed (DexScreener usd counts the token side)
    min_quote_liquidity_sol=10,    # v6.3: SOL-side pool floor (looser start), approved by Bousky
    min_lp_locked_pct=90,          # applied only to pool types where LP lock is meaningful
    max_dev_pct=10,
    max_top10_pct=35,              # pool/curve/locker accounts excluded
    max_risk_score_norm=40,        # RugCheck score_normalised (higher = worse); NOT raw score
    min_age_min=15,
    max_age_h=48,
)
BONDING_DEX_IDS = {"pumpfun", "meteoradbc"}  # DexScreener dexIds for ungraduated tokens
HOLDER_SOURCE = "rugcheck"  # v6.3: public RPC holder calls are rate-limited
# LP-lock handling by RugCheck marketType (day-1 finding)
LP_LOCK_RULE = {
    "pump_fun_amm": "structural",   # always 100% -> check is a no-op, do not count as signal
    "pump_fun": "bonding_curve",    # not graduated -> out of scope, safety skip "not_graduated"
    "meteoraDlmm": "not_applicable",# lpTotalSupply 0 -> lpLockedPct 0 structurally; skip check
    "meteora_dbc": "bonding_curve",
    "raydium_launchlab": "bonding_curve",  # v6.5: Raydium LaunchLab curve
    "meteora_damm_v2": "check",
    "raydium": "check",
    "orca": "check",
}
EXEC = dict(max_impact_pct=3.0, max_ticket_share=0.05, min_sells_1h=1)  # v6.3: ticket <= 5% of pool SOL side

EXIT = dict(hard_stop=-0.25, ladder=[(0.40, 1/3), (1.00, 1/3)], trail=0.25,
            liq_drop=0.40, time_stop_min=120, time_stop_keep_if_up=0.10,
            no_route_kill_checks=3)
# PnL accounting only. Skip, exit, kill, void, ticket, CFG_VERSION and FROZEN are unchanged;
# this block does not start a new book. Exits still key off the raw quote price, not net PnL.
#
# Slippage + sandwich-MEV haircut, applied on the entry and again on the exit, on top of the
# Jupiter quote (that quote's price impact is already inside the fill price) and network_fee_sol.
# COST_NOTE is the assumption. Nothing cited there is the bps for this 0.5 SOL paper ticket.
#
# On a pool at or above liq_ref_sol the haircut is base_bps plus impact_weight times the quoted
# impact in bps, and it reaches max_bps exactly when quoted impact hits the 3% execution check
# (150 + 0.5*300 = 300). Pools thinner than liq_ref_sol scale the base by ref/pool, capped at
# liq_scale_cap, and the total still cannot exceed max_bps (the slippageBps=300 window in run.quote).
# Missing liquidity or impact does not invent an extra penalty: scale 1, impact term 0.
COST_NOTE = (
    "Assumption, not a measured constant. No source states the bps for a 0.5 SOL memecoin paper ticket. "
    "Jito IMC 2025 (Gerzon et al.) counted 521,903 sandwich bundles, at least $7.71M of victim losses, "
    "median about $5 on a sandwiched swap, and sandwiches were 0.038% of bundles (a lower bound: 28% had no SOL leg). "
    "Helius, Solana MEV Report (Jan 2025): memecoin flow sets a wide slippage tolerance and is filled at the worst price in that window; "
    "one bot averaged 0.0425 SOL profit per sandwich over 1.55M attacks. Those figures are not this haircut. "
    "The old flat 50 bps/side guess sat well inside the 300 bps slippage window the quote already requests. "
    "The default is half that window (150 bps/side) on a pool at or above 25 SOL with no extra impact term, "
    "rising to the full 300 bps window as quoted impact reaches the 3% execution check or the pool thins toward the 10 SOL floor. "
    "Charged on entry and on exit. Skip, exit, kill, void, and the v6.5 freeze are unchanged."
)
COST = dict(
    network_fee_sol=0.00001,         # base network-fee assumption per swap; unchanged
    legacy_haircut_per_side=0.005,   # entry cost already deducted for a position opened before entry_cost was stored
    base_bps=150,                     # half of the 300 bps quote slippage window
    min_bps=150,                      # liquidity and impact only increase the haircut
    max_bps=300,                      # cap = run.quote slippageBps; do not charge more than the window
    liq_ref_sol=25.0,                 # day-1 note's suggested floor; the frozen skip floor stays 10 SOL
    liq_scale_cap=1.5,                # 10 SOL pool => 25/10, capped here so liquidity alone stays under max_bps
    impact_weight=0.5,                # extra bps on top of the quote; the quote already contains its own impact
    haircut_per_side=0.015,           # base_bps/10000, the unscaled per-side fraction (old key, reference case)
)

MIN_F_CLOSES = 20
EARLY_STOP_EXP = -0.15
TRIM_PCT = 0.05
OUTAGE_VOID_H = 2
DATA_FAIL_VOID = 0.10
SESSION_LOSS_HALT_PCT = 0.30  # logged as would_halt only
