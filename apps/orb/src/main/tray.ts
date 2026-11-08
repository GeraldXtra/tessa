import { Menu, nativeImage, Tray, type BrowserWindow, type MenuItemConstructorOptions, type NativeImage } from 'electron';

import tokens from '@tessa/tokens';

import { DRAWER_LIST, MUTE_WHY, PANIC_WHY, STOP_HER_LABEL, type ConnectionPhase, type OrbMode } from '../shared/ipc-contract.ts';

export interface TrayView {
  theme: string;
  phase: ConnectionPhase;
  agentState: string | null;
  waiting: boolean;
  mode: OrbMode;
  stoppable: boolean;
  startup: boolean | null;
  startupWhy: string;
}

export interface TrayActions {
  click: () => void;
  show: () => void;
  mode: (mode: 'full' | 'compact') => void;
  open: (id: string) => void;
  stop: () => void;
  startup: (on: boolean) => void;
  restart: () => void;
  quit: () => void;
}

const LINK_WORD: Readonly<Record<ConnectionPhase, string>> = {
  offline: 'OFFLINE',
  connecting: 'CONNECTING',
  connected: 'CONNECTED',
  authRejected: 'AUTH REJECTED',
  protocolMismatch: 'PROTOCOL MISMATCH',
  reconnecting: 'OFFLINE',
};

type Rgb = readonly [number, number, number];

function tokenRgb(name: string): Rgb {
  const table = tokens.color as Record<string, { value: string } | undefined>;
  const raw = table[name]?.value ?? '';
  const digits = raw.startsWith('#') && raw.length === 7 ? raw.slice(1) : '';
  if (!digits) throw new Error(`token ${name} is not a six-digit colour: ${JSON.stringify(raw)}`);
  return [Number.parseInt(digits.slice(0, 2), 16), Number.parseInt(digits.slice(2, 4), 16), Number.parseInt(digits.slice(4, 6), 16)];
}

export function trayColourToken(view: Pick<TrayView, 'phase' | 'waiting' | 'theme'>): string {
  if (view.phase !== 'connected') return 'text-muted';
  if (view.waiting) return 'blocked-mid';
  return `theme-${view.theme}-mid`;
}

export function trayWord(view: Pick<TrayView, 'phase' | 'waiting' | 'agentState'>): string {
  if (view.phase !== 'connected') return LINK_WORD[view.phase];
  if (view.waiting) return 'BLOCKED, WAITING ON YOU';
  if (view.agentState) return view.agentState.toUpperCase();
  return 'CONNECTED, NO STATE SENT YET';
}

function disc(size: number, rgb: Rgb): Buffer {
  const out = Buffer.alloc(size * size * 4);
  const r = size / 2 - size / 16;
  const c = size / 2;
  const n = 4;
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      let inside = 0;
      for (let sy = 0; sy < n; sy++) {
        for (let sx = 0; sx < n; sx++) {
          const dx = x + (sx + 0.5) / n - c;
          const dy = y + (sy + 0.5) / n - c;
          if (dx * dx + dy * dy <= r * r) inside += 1;
        }
      }
      const a = inside / (n * n);
      const i = (y * size + x) * 4;
      out[i] = Math.round(rgb[2] * a);
      out[i + 1] = Math.round(rgb[1] * a);
      out[i + 2] = Math.round(rgb[0] * a);
      out[i + 3] = Math.round(255 * a);
    }
  }
  return out;
}

function discImage(size: number, token: string): NativeImage {
  return nativeImage.createFromBitmap(disc(size, tokenRgb(token)), { width: size, height: size, scaleFactor: 1 });
}

export class OrbTray {
  private tray: Tray | null = null;
  private menu: Menu | null = null;
  private colourKey = '';
  private menuKey = '';
  private readonly images = new Map<string, { tray: NativeImage; window: NativeImage }>();

  constructor(
    private readonly actions: TrayActions,
    private readonly log: (line: string) => void,
    private readonly scale: number,
  ) {}

  private imagesFor(token: string): { tray: NativeImage; window: NativeImage } {
    const hit = this.images.get(token);
    if (hit) return hit;
    const made = { tray: discImage(Math.round(16 * this.scale), token), window: discImage(Math.round(32 * this.scale), token) };
    this.images.set(token, made);
    return made;
  }

  windowIcon(view: TrayView): NativeImage {
    return this.imagesFor(trayColourToken(view)).window;
  }

  get alive(): boolean {
    return this.tray !== null && !this.tray.isDestroyed();
  }

  create(view: TrayView, windows: () => BrowserWindow[]): void {
    this.tray = new Tray(this.imagesFor(trayColourToken(view)).tray);
    this.tray.on('click', () => this.actions.click());
    this.tray.on('balloon-show', () => this.log(`TRAY note shown by Windows at wall=${Date.now()}`));
    this.tray.on('balloon-closed', () => this.log(`TRAY note closed by Windows at wall=${Date.now()}`));
    this.tray.on('balloon-click', () => this.log(`TRAY note clicked at wall=${Date.now()}`));
    this.colourKey = '';
    this.menuKey = '';
    this.update(view, windows);
  }

  update(view: TrayView, windows: () => BrowserWindow[]): void {
    if (!this.tray || this.tray.isDestroyed()) return;
    const token = trayColourToken(view);
    const word = trayWord(view);
    if (token !== this.colourKey) {
      const imgs = this.imagesFor(token);
      this.tray.setImage(imgs.tray);
      for (const w of windows()) if (!w.isDestroyed()) w.setIcon(imgs.window);
      this.log(`TRAY icon ${this.colourKey || '(new)'} -> ${token}, tray ${imgs.tray.getSize().width} px, window ${imgs.window.getSize().width} px`);
      this.colourKey = token;
    }
    const key = JSON.stringify([word, view.mode, view.stoppable, view.startup, view.startupWhy]);
    if (key === this.menuKey) return;
    this.menuKey = key;
    this.tray.setToolTip(`Tessa: ${word}`);
    this.menu = Menu.buildFromTemplate(this.template(view, word));
    this.tray.setContextMenu(this.menu);
    this.log(`TRAY menu: state "${word}", mode ${view.mode}, "${STOP_HER_LABEL}" ${view.stoppable ? 'enabled' : 'disabled'}, start at sign-in ${view.startup === null ? `unavailable (${view.startupWhy})` : view.startup ? 'on' : 'off'}, tooltip "Tessa: ${word}"`);
  }

  private template(view: TrayView, word: string): MenuItemConstructorOptions[] {
    const a = this.actions;
    return [
      { id: 'state', label: word, enabled: false },
      { id: 'stop', label: STOP_HER_LABEL, enabled: view.stoppable, click: () => a.stop() },
      { type: 'separator' },
      { id: 'show', label: 'Show', click: () => a.show() },
      { type: 'separator' },
      { id: 'full', label: 'Full', type: 'checkbox', checked: view.mode === 'full', click: () => a.mode('full') },
      { id: 'compact', label: 'Compact', type: 'checkbox', checked: view.mode === 'compact', click: () => a.mode('compact') },
      { id: 'open', label: 'Open', submenu: DRAWER_LIST.map((d) => ({ id: `open:${d.id}`, label: d.name, click: () => a.open(d.id) })) },
      { type: 'separator' },
      { id: 'mute', label: `Mute the mic. ${MUTE_WHY}`, enabled: false },
      { id: 'panic', label: `Panic stop. ${PANIC_WHY}`, enabled: false },
      { type: 'separator' },
      view.startup === null
        ? { id: 'signin', label: `Start at sign-in. ${view.startupWhy}`, enabled: false }
        : { id: 'signin', label: 'Start at sign-in', type: 'checkbox', checked: view.startup, click: () => a.startup(!view.startup) },
      { id: 'restart', label: 'Restart', click: () => a.restart() },
      { id: 'quit', label: 'Quit', click: () => a.quit() },
    ];
  }

  note(text: string): boolean {
    if (!this.tray || this.tray.isDestroyed()) return false;
    this.tray.displayBalloon({ title: '', content: text, iconType: 'none', noSound: true });
    this.log(`TRAY note asked of Windows at wall=${Date.now()}: "${text}"`);
    return true;
  }

  hook(id: string): string {
    if (!this.tray || this.tray.isDestroyed()) return 'no tray';
    if (id === 'click') {
      this.tray.emit('click');
      return "ran the tray's own click listener";
    }
    const item = this.menu?.getMenuItemById(id);
    if (!item) return `no menu item "${id}"`;
    if (!item.enabled) return `menu item "${id}" is disabled ("${item.label}"), nothing ran`;
    item.click();
    return `ran menu item "${id}" ("${item.label}") click handler`;
  }

  destroy(): void {
    if (this.tray && !this.tray.isDestroyed()) this.tray.destroy();
    this.tray = null;
  }
}
