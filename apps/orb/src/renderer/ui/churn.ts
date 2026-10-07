function describe(el: Element | null): string {
  if (!el) return 'none';
  const cls = typeof el.className === 'string' && el.className ? `.${el.className.split(' ')[0]}` : '';
  let p: Element | null = el.parentElement;
  let host = '';
  while (p && !host) {
    if (p.classList.contains('tb') || p.classList.contains('chip') || p.classList.contains('cap') || p.classList.contains('col') ||
      p.classList.contains('band') || p.classList.contains('cal') || p.classList.contains('rails') || p.classList.contains('slot')) {
      host = `${p.classList[0]}>`;
    }
    p = p.parentElement;
  }
  return `${host}${el.tagName.toLowerCase()}${cls}`;
}

export function installChurnProbe(report: (line: string) => void): () => void {
  let count = 0;
  let seconds: number[] = [];
  let byTarget = new Map<string, number>();
  const mo = new MutationObserver((records) => {
    for (const r of records) {
      const node = r.target;
      const el = node instanceof Element ? node : node.parentElement;
      if (el && el.tagName === 'CANVAS') continue;
      count += 1;
      const key = `${describe(el)}:${r.type}${r.type === 'attributes' ? `(${r.attributeName ?? ''})` : ''}`;
      byTarget.set(key, (byTarget.get(key) ?? 0) + 1);
    }
  });
  mo.observe(document.body, { subtree: true, childList: true, attributes: true, characterData: true });
  const tick = window.setInterval(() => {
    seconds.push(count);
    count = 0;
    if (seconds.length >= 5) {
      const top = [...byTarget.entries()].sort((a, b) => b[1] - a[1]).slice(0, 12);
      report(`CHURN perSecond=[${seconds.join(',')}] top=${top.map(([k, v]) => `${k}=${v}`).join(' ')}`);
      seconds = [];
      byTarget = new Map();
    }
  }, 1000);
  report('CHURN probe installed (MutationObserver on body, canvas excluded)');
  return () => {
    mo.disconnect();
    window.clearInterval(tick);
  };
}
