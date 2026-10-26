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
COST = dict(haircut_per_side=0.005, network_fee_sol=0.00001)

MIN_F_CLOSES = 20
EARLY_STOP_EXP = -0.15
TRIM_PCT = 0.05
OUTAGE_VOID_H = 2
DATA_FAIL_VOID = 0.10
SESSION_LOSS_HALT_PCT = 0.30  # logged as would_halt only
