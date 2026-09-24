/**
 * useShipState.js — R3 (this round), physical ship-UI unification.
 *
 * The ONE canonical ship/rollback state machine:
 *   not_started -> ready -> approved -> shipping -> shipped
 *                                            \-> failed
 *   shipped -> rolling_back -> rolled_back
 *                        \-> failed
 *
 * Each of the 3 ship entry points (ShipDialog inline / LoopLiveFeed's
 * ShippedRow / ShipConfirmModal) keeps its OWN async fetch code —
 * they genuinely call two different backends (loop-engine rollback vs
 * CTO-task rollback), and that plumbing is already correct and
 * tested. This hook is the SHARED translation + action layer every
 * surface routes through: it takes whatever raw local status string a
 * surface already tracks, maps it onto ONE canonical `SHIP_STATUS`
 * value (single source of truth, `lib/shipStatus.js`), and returns
 * the derived flags + the ONE shared "confirm, then rollback" helper
 * (wrapping the `RollbackConfirmModal` pattern all 3 surfaces already
 * share) so every surface's rollback goes through the exact same
 * confirm -> execute -> re-derive-status path.
 */
import { useState, useCallback } from "react";
import { SHIP_STATUS } from "./shipStatus";

// Map ANY surface's own raw status vocabulary onto the one canonical set.
const _RAW_TO_CANONICAL = {
  // ShipDialog / TaskProgressCard
  idle: SHIP_STATUS.PENDING,
  shipping: SHIP_STATUS.SHIPPING,
  shipped: SHIP_STATUS.LIVE,
  error: SHIP_STATUS.FAILED,
  reverted: SHIP_STATUS.ROLLED_BACK,
  reverting: SHIP_STATUS.ROLLING_BACK,
  // ShipConfirmModal
  confirm: SHIP_STATUS.PENDING,
  // LoopLiveFeed rollback sub-phase
  submitting: SHIP_STATUS.ROLLING_BACK,
  "handed-off": SHIP_STATUS.ROLLING_BACK,
  completed: SHIP_STATUS.ROLLED_BACK,
  failed: SHIP_STATUS.FAILED,
};

export function toCanonicalShipStatus(rawStatus) {
  return _RAW_TO_CANONICAL[rawStatus] || rawStatus;
}

/**
 * `useShipState(rawStatus)` — derive the canonical phase + the shared
 * action flags every "approve a change" surface needs to render its
 * button row consistently.
 */
export function useShipState(rawStatus) {
  const canonical = toCanonicalShipStatus(rawStatus);
  return {
    canonical,
    isPending: canonical === SHIP_STATUS.PENDING,
    isShipping: canonical === SHIP_STATUS.SHIPPING,
    isLive: canonical === SHIP_STATUS.LIVE,
    isRollingBack: canonical === SHIP_STATUS.ROLLING_BACK,
    isRolledBack: canonical === SHIP_STATUS.ROLLED_BACK,
    isFailed: canonical === SHIP_STATUS.FAILED,
    // A "live" ship can be rolled back; a failed ship can be retried.
    canApprove: canonical === SHIP_STATUS.PENDING,
    canRollback: canonical === SHIP_STATUS.LIVE,
    canRetry: canonical === SHIP_STATUS.FAILED,
    isBusy: canonical === SHIP_STATUS.SHIPPING || canonical === SHIP_STATUS.ROLLING_BACK,
  };
}

/**
 * `useRollbackConfirm(rollbackFn)` — the ONE shared "are you sure ->
 * execute rollback" helper. All 3 surfaces already render the same
 * `RollbackConfirmModal` component for the confirm step (P2-B, an
 * earlier round) — this hook formalizes that into one reusable
 * open/confirm/cancel state machine so the CODE path is provably
 * shared, not just incidentally similar.
 */
export function useRollbackConfirm(rollbackFn) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);

  const request = useCallback(() => setOpen(true), []);
  const cancel = useCallback(() => setOpen(false), []);
  const confirm = useCallback(async (...args) => {
    setBusy(true);
    try {
      await rollbackFn(...args);
    } finally {
      setBusy(false);
      setOpen(false);
    }
  }, [rollbackFn]);

  return { open, busy, request, cancel, confirm };
}
