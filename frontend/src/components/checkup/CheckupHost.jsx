/**
 * CheckupHost — mounts the checkup-first UI. Renders NOTHING unless the
 * owner has turned on `checkup_flow` for this user (default OFF), and any
 * failure collapses to rendering nothing — chat is never blocked (S11).
 */
import React, { useCallback, useEffect, useRef, useState } from "react";
import useCheckupFlags from "../../hooks/useCheckupFlags";
import {
  fetchLatest, streamCheckup, ignoreItem, answerQuestion, skipCheckup,
} from "../../lib/checkupApi";
import CheckupScreen from "./CheckupScreen";
import FixListPanel from "./FixListPanel";

const seenKey = (pid) => `aurem_checkup_seen_${pid}`;
const readSeen = (pid) => { try { return localStorage.getItem(seenKey(pid)) === "1"; } catch { return false; } };
const writeSeen = (pid) => { try { localStorage.setItem(seenKey(pid), "1"); } catch { /* optional */ } };

const IDLE = { phase: "idle", stages: {}, result: null, error: null };

export default function CheckupHost({ projectId }) {
  const flags = useCheckupFlags();
  const [state, setState] = useState(IDLE);
  const [screenOpen, setScreenOpen] = useState(false);
  const [fixOpen, setFixOpen] = useState(false);
  const [latest, setLatest] = useState(null);
  const abortRef = useRef(null);

  const run = useCallback(() => {
    if (!projectId) return;
    if (abortRef.current) abortRef.current.abort();
    const ctl = new AbortController();
    abortRef.current = ctl;
    setState({ phase: "running", stages: {}, result: null, error: null });
    setScreenOpen(true);
    streamCheckup(projectId, {
      signal: ctl.signal,
      onEvent: (ev) => {
        if (ev.type === "stage_started") {
          setState((s) => ({ ...s, stages: { ...s.stages, [ev.stage]: { running: true } } }));
        } else if (ev.type === "done") {
          const st = {};
          (ev.stages || []).forEach((x) => { st[x.stage] = x; });
          setState({ phase: "done", stages: st, result: ev, error: null });
          setLatest({ items: ev.items, score: ev.score, stages: ev.stages });
          writeSeen(projectId);
        } else if (ev.type === "error") {
          setState((s) => ({ ...s, phase: "error", error: ev.message }));
        }
      },
    });
  }, [projectId]);

  useEffect(() => () => { if (abortRef.current) abortRef.current.abort(); }, []);

  useEffect(() => {
    if (!projectId || !flags?.checkup_flow) return undefined;
    let on = true;
    setLatest(null);
    setState(IDLE);
    fetchLatest(projectId).then((d) => {
      if (!on) return;
      if (d?.checkup) setLatest(d);
      else if (!readSeen(projectId)) run();
    });
    return () => { on = false; };
  }, [projectId, flags?.checkup_flow, run]);

  if (!flags?.checkup_flow || !projectId) return null;

  const close = () => { setScreenOpen(false); writeSeen(projectId); if (state.phase !== "done") skipCheckup(projectId); };
  const items = latest?.items || [];
  const fixListEnabled = !!flags.fix_list_panel;

  const handleIgnore = async (id) => {
    if (await ignoreItem(projectId, id)) setLatest((l) => ({ ...l, items: l.items.filter((i) => i.id !== id) }));
  };
  const handleAnswer = async (table, answer) => {
    if (await answerQuestion(projectId, table, answer)) run();
  };

  return (
    <>
      {latest && !screenOpen && (
        <button type="button" data-testid="checkup-pinned-bar"
          onClick={() => (fixListEnabled ? setFixOpen(true) : setScreenOpen(true))}
          style={{ position: "fixed", left: "50%", transform: "translateX(-50%)", bottom: 12, zIndex: 9000,
                   padding: "6px 14px", borderRadius: 999, cursor: "pointer" }}>
          Checkup: {latest.score}/100 · {items.length === 0 ? "nothing to fix" : `${items.length} to fix`}
        </button>
      )}
      {screenOpen && (
        <CheckupScreen state={state} onClose={close} onAnswer={handleAnswer} onRerun={run}
          fixListEnabled={fixListEnabled} onOpenFixList={() => { setScreenOpen(false); setFixOpen(true); }} />
      )}
      {fixOpen && fixListEnabled && (
        <FixListPanel items={items} onClose={() => setFixOpen(false)} onIgnore={handleIgnore} />
      )}
    </>
  );
}
