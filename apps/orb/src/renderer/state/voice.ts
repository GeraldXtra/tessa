import { DEFAULT_COMPANION, type AgentCancelReply, type TranscriptLine, type VoiceLevelPush } from '../../shared/ipc-contract.ts';
import { createStore } from './store.ts';

export const CAPTION_HOLD_MS = 6000;
export const CAPTION_FADE_MS = 450;

export type CaptionPhase = 'wait' | 'on' | 'fade';

export interface HerCaption {
  messageId: string;
  text: string;
  phase: CaptionPhase;
  arrivedAt: number;
  soundAt: number | null;
}

export interface Turn {
  messageId: string;
  sawBusy: boolean;
}

export const rawStateStore = createStore<string>('idle');
export const herCaptionStore = createStore<HerCaption | null>(null);
export const turnStore = createStore<Turn | null>(null);
export const soundStore = createStore<{ seq: number | null; level: number; at: number } | null>(null);
export const levelTrace: { on: boolean; rows: string[] } = { on: false, rows: [] };

export function takeLevelTrace(): string[] {
  const rows = levelTrace.rows;
  levelTrace.rows = [];
  return rows;
}

let holdTimer = 0;
let fadeTimer = 0;

function clearTimers(): void {
  window.clearTimeout(holdTimer);
  window.clearTimeout(fadeTimer);
  holdTimer = 0;
  fadeTimer = 0;
}

function report(line: string): void {
  window.tessa.reportMetrics(line);
}

function scheduleFade(): void {
  const cap = herCaptionStore.get();
  if (!cap || cap.phase !== 'on' || holdTimer) return;
  holdTimer = window.setTimeout(() => {
    holdTimer = 0;
    const now = herCaptionStore.get();
    if (!now || now.messageId !== cap.messageId) return;
    herCaptionStore.set({ ...now, phase: 'fade' });
    fadeTimer = window.setTimeout(() => {
      fadeTimer = 0;
      if (herCaptionStore.get()?.messageId === cap.messageId) herCaptionStore.set(null);
    }, CAPTION_FADE_MS);
  }, CAPTION_HOLD_MS);
}

function reveal(why: string): void {
  const cap = herCaptionStore.get();
  if (!cap || cap.phase !== 'wait') return;
  const at = performance.now();
  herCaptionStore.set({ ...cap, phase: 'on', soundAt: why === 'first level' ? at : null });
  report(`CAPTION shown messageId=${cap.messageId} why="${why}" textToShownMs=${(at - cap.arrivedAt).toFixed(1)} wall=${Date.now()}`);
  if (rawStateStore.get() !== 'speaking') scheduleFade();
}

export function noteRawState(state: string): void {
  const prev = rawStateStore.get();
  rawStateStore.set(state);
  const turn = turnStore.get();
  if (turn) {
    if (state === 'idle' || state === 'listening') {
      if (turn.sawBusy) turnStore.set(null);
    } else if (!turn.sawBusy) {
      turnStore.set({ ...turn, sawBusy: true });
    }
  }
  if (state === 'speaking') return;
  const cap = herCaptionStore.get();
  if (!cap) return;
  if (prev === 'speaking' && state === 'listening') {
    clearTimers();
    herCaptionStore.set(null);
    report(`CAPTION cleared: she went from speaking straight to listening (cut off to listen to him) messageId=${cap.messageId} wall=${Date.now()}`);
    return;
  }
  if (cap.phase === 'wait') reveal(`she left speaking (${state}) before a level arrived`);
  else scheduleFade();
}

export function noteLevel(push: VoiceLevelPush): void {
  const at = performance.now();
  if (levelTrace.on && levelTrace.rows.length < 20000) {
    const wall = Date.now();
    const ts = Date.parse(push.ts);
    levelTrace.rows.push(`${at.toFixed(1)},${push.seq ?? -1},${push.level.toFixed(3)},${Number.isFinite(ts) ? wall - ts : 'na'},${wall - push.mainAt}`);
  }
  soundStore.set({ seq: push.seq, level: push.level, at });
  if (herCaptionStore.get()?.phase === 'wait') reveal('first level');
}

export function noteHerLine(line: TranscriptLine): void {
  const turn = turnStore.get();
  if (line.role === 'user' && turn && line.messageId !== turn.messageId) turnStore.set(null);
  if (line.role !== 'assistant') return;
  clearTimers();
  const speaking = rawStateStore.get() === 'speaking';
  herCaptionStore.set({ messageId: line.messageId, text: line.text, phase: speaking ? 'wait' : 'on', arrivedAt: performance.now(), soundAt: null });
  report(`CAPTION text arrived messageId=${line.messageId} chars=${line.text.length} state=${rawStateStore.get()} ${speaking ? 'held until her first sound' : 'shown now (she is not speaking it)'} wall=${Date.now()}`);
  if (!speaking) scheduleFade();
}

export function noteAccepted(messageId: string): void {
  turnStore.set({ messageId, sawBusy: false });
}

export function noteLinkDown(): void {
  turnStore.set(null);
}

export function stoppable(connected: boolean, cardWaiting: boolean): boolean {
  if (!connected || cardWaiting) return false;
  const state = rawStateStore.get();
  return state === 'speaking' || (state === 'thinking' && turnStore.get() !== null);
}

export function escStops(connected: boolean): boolean {
  if (!connected) return false;
  const state = rawStateStore.get();
  return state === 'speaking' || (state === 'thinking' && turnStore.get() !== null);
}

export function stopHer(via: string): void {
  const at = performance.now();
  const turn = turnStore.get();
  const state = rawStateStore.get();
  const messageId = turn ? turn.messageId : null;
  clearTimers();
  const cap = herCaptionStore.get();
  herCaptionStore.set(null);
  window.tessa.agentCancel(DEFAULT_COMPANION.id, messageId);
  report(
    `STOP via=${via} state=${state} messageId=${messageId ?? '(none: her current speech)'} t=${at.toFixed(1)} wall=${Date.now()} ` +
      `caption=${cap ? `${cap.phase}, cleared at once` : 'none'}`,
  );
}

export function noteCancelReply(reply: AgentCancelReply): void {
  if (reply.ok && reply.cancelled !== false) turnStore.set(null);
  report(`CANCEL-RESULT messageId=${reply.messageId || '(none)'} ok=${reply.ok} type=${reply.type} cancelled=${reply.cancelled ?? 'n/a'} t=${performance.now().toFixed(1)} wall=${Date.now()}`);
  if (reply.ok && reply.cancelled === false && reply.messageId && rawStateStore.get() === 'speaking') {
    turnStore.set(null);
    window.tessa.agentCancel(DEFAULT_COMPANION.id, null);
    report(`STOP again without a messageId: core had nothing in flight for ${reply.messageId} while she is still speaking wall=${Date.now()}`);
  }
}
