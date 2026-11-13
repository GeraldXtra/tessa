export interface Tier {
  name: string;
  scale: number;
  points: number;
  glyphs: number;
  bloom: number;
  rays: boolean;
  aberration: boolean;
}

export const TIERS: readonly Tier[] = [
  { name: 'HIGH', scale: 1, points: 1, glyphs: 1, bloom: 5, rays: true, aberration: true },
  { name: 'HIGH-', scale: 1, points: 0.85, glyphs: 1, bloom: 5, rays: true, aberration: false },
  { name: 'MEDIUM', scale: 0.9, points: 0.75, glyphs: 0.8, bloom: 4, rays: true, aberration: false },
  { name: 'MEDIUM-', scale: 0.85, points: 0.65, glyphs: 0.7, bloom: 4, rays: false, aberration: false },
  { name: 'LOW', scale: 0.8, points: 0.55, glyphs: 0.6, bloom: 3, rays: false, aberration: false },
];

export const LOW = TIERS.length - 1;

export interface GovernorConfig {
  windowMs: number;
  downAfter: number;
  settleMs: number;
  upAfterMs: number;
  upMaxMs: number;
  probationMs: number;
  keepMs: number;
  dropFrac: number;
  gpuHigh: number;
  gpuLow: number;
  stallMs: number;
  warmupMs: number;
}

export const GOVERNOR: GovernorConfig = {
  windowMs: 1000,
  downAfter: 2,
  settleMs: 2500,
  upAfterMs: 10000,
  upMaxMs: 160000,
  probationMs: 6000,
  keepMs: 30000,
  dropFrac: 0.04,
  gpuHigh: 0.8,
  gpuLow: 0.55,
  stallMs: 250,
  warmupMs: 3000,
};

export type TierChange = (tier: number, why: string) => void;

export class QualityGovernor {
  tier = 0;
  private pinned: number | null = null;
  private battery = false;
  private startAt = -1;
  private winStart = -1;
  private frames = 0;
  private dropped = 0;
  private gpu: number[] = [];
  private slowRuns = 0;
  private settleUntil = 0;
  private roomySince = 0;
  private raisedAt = 0;
  private upAfter: number;
  changes = 0;

  constructor(private readonly cfg: GovernorConfig = GOVERNOR, private readonly onChange: TierChange = () => {}) {
    this.upAfter = cfg.upAfterMs;
  }

  pin(tier: number | null): void {
    this.pinned = tier === null ? null : Math.max(0, Math.min(LOW, Math.round(tier)));
    if (this.pinned !== null) this.set(this.pinned, 'pinned');
  }

  isPinned(): boolean {
    return this.pinned !== null;
  }

  onBattery(): boolean {
    return this.battery;
  }

  setBattery(on: boolean, now: number): void {
    if (on === this.battery) return;
    this.battery = on;
    if (this.pinned !== null) return;
    if (on) {
      this.set(LOW, 'on battery: held at LOW');
    } else {
      this.settleUntil = now + this.cfg.settleMs;
      this.roomySince = 0;
      this.raisedAt = 0;
      this.upAfter = this.cfg.upAfterMs;
    }
    this.resetWindow();
  }

  reset(now: number): void {
    this.resetWindow();
    this.slowRuns = 0;
    this.roomySince = 0;
    this.settleUntil = Math.max(this.settleUntil, now + this.cfg.settleMs);
  }

  private resetWindow(): void {
    this.winStart = -1;
    this.frames = 0;
    this.dropped = 0;
    this.gpu.length = 0;
  }

  private floor(): number {
    return this.battery ? LOW : 0;
  }

  private set(tier: number, why: string): void {
    const next = Math.max(0, Math.min(LOW, tier));
    if (next === this.tier) return;
    this.tier = next;
    this.changes += 1;
    this.onChange(next, why);
  }

  frame(intervalMs: number, gpuMs: number | null, now: number, budgetMs: number): void {
    if (this.pinned !== null) return;
    if (this.battery) {
      if (this.tier !== LOW) this.set(LOW, 'on battery: held at LOW');
      return;
    }
    if (this.startAt < 0) this.startAt = now;
    if (now - this.startAt < this.cfg.warmupMs || now < this.settleUntil) {
      this.resetWindow();
      return;
    }
    if (!(intervalMs > 0) || intervalMs > this.cfg.stallMs) return;
    if (this.winStart < 0) this.winStart = now;
    this.frames += 1;
    if (intervalMs > budgetMs * 1.5) this.dropped += 1;
    if (gpuMs !== null && Number.isFinite(gpuMs)) this.gpu.push(gpuMs);
    if (now - this.winStart < this.cfg.windowMs || this.frames < 10) return;
    this.judge(now, budgetMs);
    this.resetWindow();
  }

  private judge(now: number, budgetMs: number): void {
    const drops = this.dropped / this.frames;
    let gpu90: number | null = null;
    if (this.gpu.length >= 8) {
      const sorted = [...this.gpu].sort((a, b) => a - b);
      gpu90 = sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * 0.9))] ?? null;
    }
    const slow = drops > this.cfg.dropFrac || (gpu90 !== null && gpu90 > this.cfg.gpuHigh * budgetMs);
    const roomy = this.dropped === 0 && (gpu90 === null || gpu90 < this.cfg.gpuLow * budgetMs);
    if (this.raisedAt > 0 && now - this.raisedAt >= this.cfg.keepMs) {
      this.raisedAt = 0;
      this.upAfter = this.cfg.upAfterMs;
    }
    if (slow) {
      this.slowRuns += 1;
      this.roomySince = 0;
      if (this.raisedAt > 0 && now - this.raisedAt < this.cfg.probationMs && this.tier < LOW) {
        this.upAfter = Math.min(this.cfg.upMaxMs, this.upAfter * 2);
        this.raisedAt = 0;
        this.slowRuns = 0;
        this.settleUntil = now + this.cfg.settleMs;
        this.set(this.tier + 1, `a raise did not hold (drops ${(drops * 100).toFixed(1)}%), next try after ${Math.round(this.upAfter / 1000)} s`);
        return;
      }
      if (this.slowRuns >= this.cfg.downAfter && this.tier < LOW) {
        this.slowRuns = 0;
        this.settleUntil = now + this.cfg.settleMs;
        this.set(this.tier + 1, `GPU behind: drops ${(drops * 100).toFixed(1)}%${gpu90 !== null ? `, gpu p90 ${gpu90.toFixed(1)} ms` : ''}`);
      }
      return;
    }
    this.slowRuns = 0;
    if (!roomy) {
      this.roomySince = 0;
      return;
    }
    if (this.roomySince === 0) this.roomySince = now;
    if (this.tier > this.floor() && now - this.roomySince >= this.upAfter) {
      this.roomySince = 0;
      this.raisedAt = now;
      this.settleUntil = now + this.cfg.settleMs;
      this.set(this.tier - 1, `room again${gpu90 !== null ? `: gpu p90 ${gpu90.toFixed(1)} ms` : ''} for ${Math.round(this.upAfter / 1000)} s`);
    }
  }
}

export function tierFromName(value: string | null | undefined): number | null {
  if (!value) return null;
  const v = value.trim().toLowerCase();
  if (v === 'high') return 0;
  if (v === 'medium') return 2;
  if (v === 'low') return LOW;
  const n = Number.parseInt(v, 10);
  return Number.isFinite(n) && n >= 0 && n <= LOW ? n : null;
}
