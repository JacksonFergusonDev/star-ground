// The app's hover rule, checked on whatever page is open: every link, button,
// field and label-drawn control answers the pointer with a change that fades
// in (a CSS transition, on itself, inside it, or on a card around it), and a
// text link's colour ends at the accent. The rules themselves live in one
// place, `src/styles/global.css`; this finds the element they missed.

import type { Page } from '@playwright/test';

/** Problems found on the page, one line per element: empty when every control reacts. */
export async function hoverProblems(page: Page): Promise<string[]> {
  // Controls are found once the page has stopped loading and redrawing.
  await page.waitForLoadState('networkidle');
  const count = await page.evaluate(() => {
    const shown = (el: Element): boolean => {
      const box = el.getBoundingClientRect();
      const style = getComputedStyle(el);
      return (
        box.width > 2 && box.height > 2 && style.visibility !== 'hidden' && style.opacity !== '0'
      );
    };
    // Elements alike in tag, classes and parent share their styles, so one of
    // each kind is checked: the catalog lists hundreds of the same link.
    const kinds = new Map<string, Element>();
    const kind = (el: Element): string =>
      [el.tagName, el.className, el.parentElement?.tagName, el.parentElement?.className].join('|');
    const controls = document.querySelectorAll(
      '.app :is(a[href], button, summary, select, textarea, input:not([type="hidden"]))',
    );
    for (const control of controls) {
      // A radio or checkbox is pointed at through its label, which takes the click.
      const label = control.closest('label');
      const target =
        control.matches('[type="checkbox"], [type="radio"]') && label ? label : control;
      // What's already chosen (a pressed tab, a checked option, the current
      // page) keeps its look under the pointer, as global.css says, and what's
      // disabled can't be used.
      const chosen = target.matches(
        '[aria-pressed="true"], [aria-current], :has(> input:checked), input:checked, :disabled',
      );
      if (shown(target) && !chosen && !target.closest('[inert], [aria-hidden="true"]'))
        if (!kinds.has(kind(target))) kinds.set(kind(target), target);
    }
    const targets = [...kinds.values()];
    document
      .querySelectorAll('[data-hover-probe]')
      .forEach((el) => el.removeAttribute('data-hover-probe'));
    targets.forEach((el, i) => el.setAttribute('data-hover-probe', String(i)));
    return targets.length;
  });

  const problems: string[] = [];
  for (let i = 0; i < count; i += 1) {
    const target = page.locator(`[data-hover-probe="${i}"]`);
    // The pointer rests on the sidebar's bare edge, on no control, and what
    // the last hover started is settled, so the styles read next are at rest.
    await page.mouse.move(1, 1);
    await page.evaluate(() => document.getAnimations().forEach((a) => a.finish()));
    let problem: string | null;
    try {
      await target.evaluate(snapshot, undefined, { timeout: 2000 });
      await target.hover({ timeout: 2000 });
      // Ending every fade at once makes the check the same on a slow machine.
      problem = await target.evaluate(compare, undefined, { timeout: 2000 });
    } catch {
      // Covered by something else, such as a sticky pane, or redrawn since it
      // was found: not pointed at here.
      continue;
    }
    if (problem) problems.push(problem);
  }
  return problems;
}

// Both functions below run in the page, so each lists the properties hover
// may change (all of which global.css fades) for itself.

/** Records the resting styles of the control, what's inside it, and a card around it. */
function snapshot(el: Element): void {
  const PROPS = [
    'color',
    'background-color',
    'border-top-color',
    'border-right-color',
    'border-bottom-color',
    'border-left-color',
    'outline-color',
    'fill',
    'stroke',
    'opacity',
  ];
  const card = el.closest('.hover-card');
  const scope = [el, ...el.querySelectorAll('*'), ...(card && card !== el ? [card] : [])];
  (window as unknown as { __hoverRest: Map<Element, string[]> }).__hoverRest = new Map(
    scope.map((node) => {
      const style = getComputedStyle(node);
      return [node, PROPS.map((p) => style.getPropertyValue(p))];
    }),
  );
}

/**
 * Compares the hovered styles, every fade ended, with the resting ones: the
 * control must change something, each change must be one a transition fades,
 * and a text link must end the accent green. Returns the problem, or null.
 */
function compare(el: Element): string | null {
  const PROPS = [
    'color',
    'background-color',
    'border-top-color',
    'border-right-color',
    'border-bottom-color',
    'border-left-color',
    'outline-color',
    'fill',
    'stroke',
    'opacity',
  ];
  document.getAnimations().forEach((a) => a.finish());
  const label =
    (el.textContent ?? '').trim().slice(0, 40) ||
    el.getAttribute('aria-label') ||
    el.getAttribute('placeholder') ||
    (el.id && document.querySelector(`label[for="${el.id}"]`)?.textContent?.trim()) ||
    el.className;
  const type = el.getAttribute('type');
  const name = `${el.tagName.toLowerCase()}${type ? `[type=${type}]` : ''} "${label}"`;

  const rest = (window as unknown as { __hoverRest: Map<Element, string[]> }).__hoverRest;
  let changed = false;
  let snaps: string | null = null;
  for (const [node, before] of rest) {
    const style = getComputedStyle(node);
    const faded = style.transitionProperty.split(',').map((p) => p.trim());
    const durations = style.transitionDuration.split(',').map((d) => parseFloat(d));
    PROPS.forEach((prop, k) => {
      if (style.getPropertyValue(prop) === before[k]) return;
      changed = true;
      // `border-color` fades each side's colour.
      const covers = (p: string): boolean =>
        p === prop || p === 'all' || (p === 'border-color' && /^border-\w+-color$/.test(prop));
      const at = faded.findIndex(covers);
      const duration = durations[at % durations.length] ?? 0;
      if ((at < 0 || duration <= 0) && !snaps) {
        const where = node === el ? '' : ` (on ${node.tagName.toLowerCase()}.${node.className})`;
        snaps = `${name} changes ${prop}${where} without a fade`;
      }
    });
  }
  if (!changed) return `${name} doesn't react when hovered`;
  if (snaps) return snaps;

  // A text link ends green; buttons and cards answer with their border.
  if (el instanceof HTMLAnchorElement && !el.matches('.hs-button, .hover-card')) {
    const probe = document.createElement('span');
    probe.style.color = 'var(--accent)';
    el.append(probe);
    const accent = getComputedStyle(probe).color;
    probe.remove();
    if (getComputedStyle(el).color !== accent)
      return `${name} doesn't turn the accent when hovered`;
  }
  return null;
}
