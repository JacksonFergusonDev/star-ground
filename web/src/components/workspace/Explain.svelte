<script lang="ts">
  // An (i) beside a term that opens what it means. A pointer opens it by
  // hovering, and a tap or click keeps it open, since phones have no hover;
  // Escape, a click elsewhere or scrolling closes it. The explanation is
  // fixed to the window rather than the term, so a scrolling table or a
  // narrow card never clips it.
  import { GLOSSARY, type Term } from '../../lib/glossary';

  let { term }: { term: Term } = $props();

  const entry = $derived(GLOSSARY[term]);
  const id = $props.id();

  let button = $state<HTMLButtonElement>();
  let pinned = $state(false);
  let hovered = $state(false);
  const open = $derived(pinned || hovered);
  let place = $state({ top: 0, left: 0, above: false });

  // The explanation's width, and the gap it keeps from the window's edges.
  const WIDTH = 320;
  const EDGE = 16;

  /** Puts the explanation under the marker, or over it near the window's foot. */
  function position(): void {
    if (!button) return;
    const r = button.getBoundingClientRect();
    const width = Math.min(WIDTH, window.innerWidth - 2 * EDGE);
    const left = Math.min(
      Math.max(EDGE, r.left + r.width / 2 - width / 2),
      window.innerWidth - EDGE - width,
    );
    const above = r.bottom + 180 > window.innerHeight && r.top > 180;
    place = { top: above ? r.top - 8 : r.bottom + 8, left, above };
  }

  function close(): void {
    pinned = false;
    hovered = false;
  }

  $effect(() => {
    if (!open) return;
    position();
    const away = (e: Event) => {
      if (!(e.target instanceof Node) || !button?.contains(e.target)) close();
    };
    const key = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        close();
        button?.focus();
      }
    };
    document.addEventListener('pointerdown', away, true);
    document.addEventListener('keydown', key);
    window.addEventListener('scroll', close, true);
    window.addEventListener('resize', close);
    return () => {
      document.removeEventListener('pointerdown', away, true);
      document.removeEventListener('keydown', key);
      window.removeEventListener('scroll', close, true);
      window.removeEventListener('resize', close);
    };
  });
</script>

<span class="explain">
  <button
    bind:this={button}
    type="button"
    aria-label={`What "${entry.term}" means`}
    aria-expanded={open}
    aria-controls={id}
    onclick={() => (pinned ? close() : (pinned = true))}
    onpointerenter={(e) => {
      if (e.pointerType === 'mouse') hovered = true;
    }}
    onpointerleave={(e) => {
      if (e.pointerType === 'mouse') hovered = false;
    }}>i</button
  >
  <span
    {id}
    class="bubble"
    class:above={place.above}
    class:pinned
    role="note"
    hidden={!open}
    style:top={`${place.top}px`}
    style:left={`${place.left}px`}
    style:width={`min(${WIDTH}px, calc(100vw - ${2 * EDGE}px))`}>{entry.text}</span
  >
</span>

<style>
  .explain {
    display: inline-flex;
    vertical-align: middle;
  }
  button {
    display: inline-grid;
    place-items: center;
    width: 17px;
    height: 17px;
    margin: 0 4px;
    padding: 0;
    border: 1px solid var(--line);
    border-radius: 50%;
    color: var(--muted);
    background: transparent;
    font-family: var(--mono);
    font-size: var(--fs-label);
    font-weight: 500;
    line-height: 1;
    letter-spacing: 0;
    text-transform: none;
    cursor: help;
    position: relative;
  }
  /* The hit area reaches past the drawing, so a finger can find it. */
  button::after {
    position: absolute;
    inset: -12px;
    content: '';
  }
  button[aria-expanded='true'] {
    color: var(--text);
  }
  .bubble {
    position: fixed;
    z-index: 50;
    display: block;
    padding: 10px 12px;
    border: 1px solid var(--line);
    border-radius: 5px;
    color: var(--text);
    background: var(--panel);
    font-family: var(--sans);
    font-size: var(--fs-ui);
    font-weight: 400;
    line-height: 1.55;
    letter-spacing: 0;
    text-align: left;
    text-transform: none;
    white-space: normal;
  }
  /* Opened by hovering, it lets the pointer through to what's under it, so
     it never stands in the way of a click; kept open, it takes clicks itself. */
  .bubble:not(.pinned) {
    pointer-events: none;
  }
  .bubble.above {
    transform: translateY(-100%);
  }
  .bubble[hidden] {
    display: none;
  }
</style>
