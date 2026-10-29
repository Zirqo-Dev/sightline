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
# Jupiter quote (the quote's own curve impact is already inside outAmount) and network_fee_sol.
# verified impact is constant-product dx/(pool+dx), not Jupiter priceImpactPct. See COST_NOTE.
COST_MODEL = "v3-exit-aware"  # stamped on each new fill, with the model's start time; older rows stay blank
COST_NOTE = (
    "Assumption, not a measured constant. Verified impact is the constant-product move of this fill's SOL "
    "into the pool's SOL reserve: 10000 * dx / (pool + dx). Jupiter priceImpactPct is stored as impact_pct and is not used. "
    "On 28 Oct 2026 the open pools of 148.27, 179.79 and 257.14 SOL carried Jupiter impacts of 2.49%, 1.24% and 1.09%. "
    "The same 0.5 SOL ticket against those reserves is 0.336%, 0.277% and 0.194%. "
    "Quoted outAmount across three sizes sits on that curve: the mid size predicted from the other two matched within 0.02% "
    "on the pools whose vault did not move during the read. The Jupiter field is a gap versus a cached USD price. "
    "Per side: base_bps + impact_weight * verified_impact_bps, plus stress_exit_bps on hard_stop, trailing_stop and liquidity_pulled, "
    "capped at max_bps. A missing reserve adds no impact term. "
    "Charged on entry and on exit. Skip, exit, kill, void, and the v6.5 freeze are unchanged."
)
COST = dict(
    network_fee_sol=0.00001,         # base network-fee assumption per swap; unchanged
    legacy_haircut_per_side=0.005,   # entry cost already deducted for a position opened before entry_cost was stored
    base_bps=60,                      # per side, before the verified-impact term
    min_bps=60,                       # impact and the stress add-on only increase the haircut
    max_bps=600,                      # cap on the whole per-side haircut
    impact_weight=0.25,               # times verified curve impact in bps; the quote already contains its own curve move
    stress_exit_bps=150,              # added on exits whose reason is in stress_exit_reasons
    stress_exit_reasons=("hard_stop", "trailing_stop", "liquidity_pulled"),
    haircut_per_side=0.006,           # base_bps/10000, the per-side fraction with no impact term
)

MIN_F_CLOSES = 20
EARLY_STOP_EXP = -0.15
TRIM_PCT = 0.05
OUTAGE_VOID_H = 2
DATA_FAIL_VOID = 0.10
SESSION_LOSS_HALT_PCT = 0.30  # logged as would_halt only
# Not a v6.5 skip, entry, exit, or kill line. Full sha256 hex of shared funders.
# Empty until those hashes are added. Raw wallets do not go here.
RUGGER_EXCLUSION_SHA256 = ()
