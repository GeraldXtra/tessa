import { cpus, freemem, totalmem } from 'node:os';

import type { MachineLoad } from '../shared/ipc-contract.ts';

interface CpuTotals {
  busy: number;
  total: number;
}

function cpuTotals(): CpuTotals {
  let busy = 0;
  let total = 0;
  for (const c of cpus()) {
    const t = c.times;
    const all = t.user + t.nice + t.sys + t.idle + t.irq;
    total += all;
    busy += all - t.idle;
  }
  return { busy, total };
}

export class MachineLoadSampler {
  private last: CpuTotals = cpuTotals();

  sample(): MachineLoad {
    const now = cpuTotals();
    const total = now.total - this.last.total;
    const busy = now.busy - this.last.busy;
    this.last = now;
    const cpu = total > 0 ? Math.max(0, Math.min(1, busy / total)) : 0;
    const all = totalmem();
    const mem = all > 0 ? Math.max(0, Math.min(1, 1 - freemem() / all)) : 0;
    return { cpu, mem };
  }
}
