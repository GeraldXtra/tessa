import type { JobView, VoiceWords } from '../../shared/ipc-contract.ts';
import type { JobRingInput } from '../scene/plasma-engine.ts';
import { createStore } from './store.ts';

export const jobsStore = createStore<readonly JobView[]>([]);

export interface DevVisual {
  disconnected: boolean;
  muted: boolean;
  night: boolean;
  jobs: readonly JobRingInput[];
}

export const devVisualStore = createStore<DevVisual>({ disconnected: false, muted: false, night: false, jobs: [] });

export const sentinelFlareStore = createStore<boolean>(false);

export interface Caption {
  messageId: string;
  text: string;
  done: boolean;
  at: number;
  words: VoiceWords | null;
}

export const captionStore = createStore<Caption | null>(null);

export function captionText(messageId: string, text: string, done: boolean): void {
  const prev = captionStore.get();
  const words = prev && prev.messageId === messageId ? prev.words : null;
  captionStore.set({ messageId, text, done, at: Date.now(), words });
}

export function captionWords(words: VoiceWords): void {
  const prev = captionStore.get();
  const text = prev && prev.messageId === words.messageId && prev.text ? prev.text : words.words.map((w) => w.text).join(' ');
  captionStore.set({ messageId: words.messageId, text, done: prev?.done ?? false, at: Date.now(), words });
}

export function jobRings(real: readonly JobView[], dev: readonly JobRingInput[]): JobRingInput[] {
  const out: JobRingInput[] = real.map((j) => ({
    id: j.jobId,
    progress: j.progress,
    waiting: j.status === 'blocked',
    finished:
      j.status === 'succeeded'
        ? 'ok'
        : j.status === 'failed' || j.status === 'cancelled' || j.status === 'needsReview'
          ? 'cancelled'
          : 'none',
  }));
  return out.concat(dev);
}
