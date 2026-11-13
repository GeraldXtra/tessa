export interface LoopHost {
  request(cb: (ts: number) => void): number;
  cancel(id: number): void;
  hidden(): boolean;
}

export class FrameLoop {
  private id = 0;
  private running = false;
  private idle = false;
  ticks = 0;
  skippedHidden = 0;

  constructor(
    private readonly host: LoopHost,
    private readonly tick: (ts: number) => boolean,
  ) {}

  start(): void {
    this.running = true;
    this.idle = false;
    this.schedule();
  }

  stop(): void {
    this.running = false;
    if (this.id) this.host.cancel(this.id);
    this.id = 0;
  }

  park(): void {
    this.idle = true;
    if (this.id) this.host.cancel(this.id);
    this.id = 0;
  }

  poke(): void {
    if (!this.running) return;
    this.idle = false;
    this.schedule();
  }

  isRunning(): boolean {
    return this.running && !this.idle;
  }

  pending(): boolean {
    return this.id !== 0;
  }

  private schedule(): void {
    if (this.id || !this.running || this.idle) return;
    if (this.host.hidden()) {
      this.skippedHidden += 1;
      return;
    }
    this.id = this.host.request((ts) => this.fire(ts));
  }

  private fire(ts: number): void {
    this.id = 0;
    if (!this.running || this.idle) return;
    if (this.host.hidden()) {
      this.skippedHidden += 1;
      return;
    }
    this.ticks += 1;
    const more = this.tick(ts);
    if (more) this.schedule();
    else this.idle = true;
  }
}
