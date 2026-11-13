import type { Box } from '../scene/ultron/callouts.ts';
import { createStore } from './store.ts';

export const stageStore = createStore<Box | null>(null);

export interface SceneHooks {
  push(x: number, y: number, strength: number): void;
}

export const sceneHookStore = createStore<SceneHooks | null>(null);
