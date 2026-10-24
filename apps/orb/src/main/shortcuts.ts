import { existsSync, mkdirSync } from 'node:fs';
import { dirname, join } from 'node:path';

import { app, shell } from 'electron';

export const SHORTCUT_NAME = 'Tessa Orb.lnk';

export interface ShortcutPlace {
  startup: string;
  menu: string;
  real: boolean;
}

export function shortcutPlace(override: string | null): ShortcutPlace {
  const programs = override ?? join(app.getPath('appData'), 'Microsoft', 'Windows', 'Start Menu', 'Programs');
  return { startup: join(programs, 'Startup', SHORTCUT_NAME), menu: join(programs, SHORTCUT_NAME), real: override === null };
}

export function shortcutTarget(): { target: string; args: string; cwd: string; description: string } {
  return { target: process.execPath, args: `"${app.getAppPath()}"`, cwd: app.getAppPath(), description: 'Tessa Orb' };
}

export function startupExists(place: ShortcutPlace): boolean {
  return existsSync(place.startup);
}

function writeLink(path: string, log: (line: string) => void): boolean {
  mkdirSync(dirname(path), { recursive: true });
  if (!shell.writeShortcutLink(path, 'create', shortcutTarget())) {
    log(`!! shortcut NOT written: ${path}`);
    return false;
  }
  const back = shell.readShortcutLink(path);
  log(`shortcut written: ${path} -> "${back.target}" ${back.args ?? ''} in "${back.cwd ?? ''}"`);
  return true;
}

export async function setStartAtSignIn(place: ShortcutPlace, on: boolean, log: (line: string) => void): Promise<boolean> {
  if (on) {
    if (writeLink(place.startup, log) && !existsSync(place.menu)) writeLink(place.menu, log);
    return startupExists(place);
  }
  if (existsSync(place.startup)) {
    try {
      await shell.trashItem(place.startup);
      log(`shortcut moved to the Recycle Bin: ${place.startup}`);
    } catch (err) {
      log(`!! could not move ${place.startup} to the Recycle Bin: ${String(err)}`);
    }
  }
  return startupExists(place);
}
