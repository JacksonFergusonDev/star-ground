<script lang="ts">
  // Bench mode: building one project, part by part, lowest parts first (the
  // field manual's order), with the document beside the checklist when there
  // is one, lit up where a catalog document's board draws the part at hand. Made for a laptop at the bench: Space places the current part and
  // moves to the next, the arrow keys move between parts. Progress is kept
  // on the card. A part the document counts by value ('2 - 47') is one row,
  // placed all at once and keyed '#<category> <value>' (its `key`).
  import { onMount } from 'svelte';
  import type { BuildStep, ProjectResult } from '../../lib/api';
  import type { BoardMarks } from '../../lib/boards';
  import { boardBoxes, boardMarks, rowRef } from '../../lib/boards';
  import type { Card } from '../../lib/workspace.svelte';
  import { count, placedOf } from '../../lib/workspace.svelte';
  import type { Box } from './DocumentViewer.svelte';
  import DocumentViewer from './DocumentViewer.svelte';
  import Icon from './Icon.svelte';

  let {
    card,
    project,
    load,
    onPlace,
    onStartOver,
    onBack,
  }: {
    card: Card;
    project: ProjectResult;
    /** The document's bytes; null for a pasted list or a preset. */
    load: (() => Promise<ArrayBuffer>) | null;
    onPlace: (ref: string, placed: boolean) => void;
    onStartOver: () => void;
    onBack: () => void;
  } = $props();

  type Row = { label: string; value: string; count: number };

  const placed = $derived(new Set(placedOf(card)));
  // Each row by its designator, or a counted part's key.
  const rows = $derived(
    new Map<string, Row>([
      ...project.parts.map((p) => [p.ref, { label: p.ref, value: p.value, count: 1 }] as const),
      ...project.counted.map(
        (c) => [c.key, { label: `${c.count} ×`, value: c.value, count: c.count }] as const,
      ),
    ]),
  );
  const stepRows = (step: BuildStep): string[] => [...step.refs, ...step.counted];
  // Every row in build order, one after another.
  const order = $derived(project.build_order.flatMap(stepRows));
  const sum = (keys: string[]): number =>
    keys.reduce((n, key) => n + (rows.get(key)?.count ?? 1), 0);
  const total = $derived(sum(order));
  const done = $derived(sum(order.filter((ref) => placed.has(ref))));

  // The part at hand: the first not yet placed, until the builder moves.
  // svelte-ignore state_referenced_locally
  let current = $state<string | null>(order.find((ref) => !placed.has(ref)) ?? order[0] ?? null);

  // Where a catalog document's board draws each part; none for other files.
  let marks = $state<BoardMarks | null>(null);
  let hovered = $state<string | null>(null);
  // Hashing copies the bytes before pdf.js takes them, so it runs alongside.
  function loadAndFind(from: () => Promise<ArrayBuffer>): Promise<ArrayBuffer> {
    return from().then((bytes) => {
      void boardMarks(bytes).then((found) => (marks = found));
      return bytes;
    });
  }

  const tone = (ref: string): Box['tone'] => (placed.has(ref) ? 'checked' : 'plain');
  const boxes = $derived<Box[]>([
    ...boardBoxes(marks, tone),
    ...project.parts.flatMap((p) =>
      p.location ? [{ ref: p.ref, location: p.location, tone: tone(p.ref) }] : [],
    ),
  ]);

  let list = $state<HTMLOListElement>();
  function show(ref: string | null): void {
    current = ref;
    if (!ref) return;
    const row = list?.querySelector(`[data-ref="${CSS.escape(ref)}"]`);
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    row?.scrollIntoView({ block: 'nearest', behavior: reduced ? 'auto' : 'smooth' });
  }

  function move(by: number): void {
    const at = current ? order.indexOf(current) : -1;
    show(order[Math.min(order.length - 1, Math.max(0, at + by))] ?? null);
  }

  // Places the current part (or takes it off), then moves to the next part
  // still to place.
  function toggle(): void {
    if (!current) return;
    const placing = !placed.has(current);
    onPlace(current, placing);
    if (!placing) return;
    const at = order.indexOf(current);
    const next = [...order.slice(at + 1), ...order.slice(0, at)].find(
      (ref) => ref !== current && !placed.has(ref),
    );
    if (next) show(next);
  }

  function keydown(event: KeyboardEvent): void {
    const target = event.target as HTMLElement;
    // Typing in a field, or pressing a button, keeps its own keys.
    if (target.closest('input, select, textarea, button, a')) return;
    if (event.altKey || event.ctrlKey || event.metaKey) return;
    if (event.key === ' ') toggle();
    else if (event.key === 'ArrowDown' || event.key === 'ArrowRight') move(1);
    else if (event.key === 'ArrowUp' || event.key === 'ArrowLeft') move(-1);
    else return;
    event.preventDefault();
  }

  let section = $state<HTMLElement>();
  onMount(() => {
    section?.scrollIntoView({ block: 'start' });
    show(current);
  });
</script>

<svelte:window onkeydown={keydown} />

<section class="bench" aria-labelledby="bench-title" bind:this={section}>
  <div class="head">
    <button class="back" type="button" onclick={onBack}>
      Back to the workspace <Icon name="arrow-left" size={14} />
    </button>
    <h2 id="bench-title">Build {project.label}</h2>
    <p class="note">
      Lowest parts first, so the board sits flat as you go. Press <kbd>Space</kbd> to place the current
      part and move to the next, and the arrow keys to move between parts.
    </p>
    <div class="progress">
      <p class="note" role="status">
        {done === total && total
          ? `All ${count(total, 'part')} placed.`
          : `${done} of ${count(total, 'part')} placed.`}
      </p>
      <meter min="0" max={total || 1} value={done} aria-label="Parts placed"></meter>
      {#if done}
        <button class="text-button" type="button" onclick={onStartOver}>Start over</button>
      {/if}
    </div>
  </div>

  <div class:panes={load !== null}>
    {#if load}
      <div class="document">
        <DocumentViewer
          load={() => loadAndFind(load)}
          {boxes}
          selected={current}
          onSelect={show}
          {hovered}
          onHover={(ref) => (hovered = ref)}
          prefer="board"
        />
      </div>
    {/if}

    <ol
      class="order"
      bind:this={list}
      onmouseover={(e) => (hovered = rowRef(e))}
      onfocusin={(e) => (hovered = rowRef(e))}
      onmouseleave={() => (hovered = null)}
    >
      {#each project.build_order as step (step.stage)}
        <li class="stage">
          <h3>{step.stage}</h3>
          <ul>
            {#each stepRows(step) as ref (ref)}
              {@const row = rows.get(ref)}
              <li
                data-ref={ref}
                class:current={current === ref}
                class:hovered={hovered === ref}
                class:placed={placed.has(ref)}
                aria-current={current === ref ? 'step' : undefined}
              >
                <label>
                  <input
                    type="checkbox"
                    checked={placed.has(ref)}
                    onchange={(e) => {
                      current = ref;
                      onPlace(ref, e.currentTarget.checked);
                    }}
                  />
                  <span class="ref">{row?.label ?? ref}</span>
                  <span class="value">{row?.value ?? ''}</span>
                </label>
              </li>
            {/each}
          </ul>
        </li>
      {/each}
    </ol>
  </div>
</section>

<style>
  .bench {
    display: grid;
    gap: 20px;
    margin-top: 40px;
    scroll-margin-top: calc(var(--hs-bar-height, 0px) + 16px);
  }
  .head {
    display: grid;
    gap: 10px;
  }
  .back,
  .text-button {
    display: inline-flex;
    gap: 8px;
    align-items: center;
    justify-self: start;
    padding: 0;
    border: 0;
    color: var(--muted);
    background: none;
    font: inherit;
    font-size: var(--fs-ui);
    cursor: pointer;
  }
  .back:hover,
  .text-button:hover {
    color: var(--accent);
  }
  h2 {
    margin: 0;
    font-size: var(--fs-h3);
    font-weight: 500;
    letter-spacing: -0.8px;
  }
  .note {
    margin: 0;
    color: var(--muted);
    font-size: var(--fs-ui);
    line-height: 1.7;
  }
  kbd {
    padding: 0.1em 0.4em;
    border: 1px solid var(--line);
    border-radius: 3px;
    color: var(--text);
    font-family: var(--mono);
    font-size: 0.85em;
  }
  .progress {
    display: flex;
    flex-wrap: wrap;
    gap: 8px 18px;
    align-items: center;
  }
  meter {
    flex: 1 1 160px;
    max-width: 320px;
    accent-color: var(--accent);
  }
  .panes {
    display: grid;
    grid-template-columns: minmax(0, 7fr) minmax(0, 5fr);
    gap: 24px;
    align-items: start;
  }
  .document {
    position: sticky;
    top: calc(var(--hs-bar-height, 0px) + 16px);
    height: calc(100vh - var(--hs-bar-height, 0px) - 32px);
  }
  .order {
    display: grid;
    gap: 22px;
    max-width: 640px;
    margin: 0;
    padding: 0;
    list-style: none;
  }
  h3 {
    margin: 0 0 8px;
    color: var(--muted);
    font-family: var(--mono);
    font-size: var(--fs-label);
    font-weight: 400;
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }
  .stage ul {
    display: grid;
    gap: 4px;
    margin: 0;
    padding: 0;
    list-style: none;
  }
  .stage li {
    border: 1px solid var(--line);
    border-radius: 5px;
    background: var(--panel);
    scroll-margin: 80px;
  }
  .stage li.hovered {
    border-color: var(--accent-border);
  }
  .stage li.current {
    border-color: var(--accent-border);
    background: var(--accent-soft);
  }
  .stage li.placed .ref,
  .stage li.placed .value {
    color: var(--muted);
    text-decoration: line-through;
  }
  label {
    display: grid;
    grid-template-columns: auto 5em minmax(0, 1fr);
    gap: 12px;
    align-items: center;
    padding: 9px 12px;
    cursor: pointer;
  }
  input {
    accent-color: var(--accent);
  }
  .ref,
  .value {
    font-family: var(--mono);
    font-size: var(--fs-ui);
    overflow-wrap: anywhere;
  }
  button:focus-visible,
  input:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }
  @media (width <= 900px) {
    .panes {
      grid-template-columns: minmax(0, 1fr);
    }
    /* Pinned above the list, so the part at hand shows on the page. */
    .document {
      z-index: 1;
      height: 42vh;
      background: var(--bg);
    }
    .stage li {
      scroll-margin-top: calc(var(--hs-bar-height, 0px) + 42vh + 32px);
    }
  }
</style>
