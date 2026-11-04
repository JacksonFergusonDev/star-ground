<script lang="ts">
  // Bench mode: building one project, part by part, lowest parts first (the
  // field manual's order), with the document beside the checklist when there
  // is one, lit up where a catalog document's board draws the part at hand.
  // Made for a laptop at the bench: Space places the current part and moves
  // to the next, the arrow keys move between parts. Progress is kept on the
  // card. A part the document counts by value ('2 - 47') is one row, placed
  // all at once and keyed '#<category> <value>' (its `key`).
  //
  // It looks like the board it builds: each part's pad fills with solder as
  // it's placed, designators are lettered like silkscreen, resistors show
  // their bands, and the progress is a trace that ends at a star ground.
  import { onMount } from 'svelte';
  import type { BuildStage, BuildStep, ComponentCategory, ProjectResult } from '../../lib/api';
  import type { BoardMarks } from '../../lib/boards';
  import { boardBoxes, boardMarks, rowRef } from '../../lib/boards';
  import { formatValue, kindOfStage, readPot } from '../../lib/parts';
  import type { Card, Display } from '../../lib/workspace.svelte';
  import { count, placedOf } from '../../lib/workspace.svelte';
  import Choice from './Choice.svelte';
  import type { Box } from './DocumentViewer.svelte';
  import DocumentViewer from './DocumentViewer.svelte';
  import HeightProfile from './HeightProfile.svelte';
  import OrientationHint from './OrientationHint.svelte';
  import PartSymbol from './PartSymbol.svelte';
  import ResistorBands from './ResistorBands.svelte';
  import TaperCurve from './TaperCurve.svelte';
  import TraceProgress from './TraceProgress.svelte';

  let {
    card,
    project,
    display,
    load,
    onPlace,
    onStartOver,
  }: {
    card: Card;
    project: ProjectResult;
    display: Display;
    /** The document's bytes; null for a pasted list or a preset. */
    load: (() => Promise<ArrayBuffer>) | null;
    onPlace: (ref: string, placed: boolean) => void;
    onStartOver: () => void;
  } = $props();

  type Row = { label: string; value: string; category: ComponentCategory; count: number };

  // Stages named as builders say them.
  const STAGE_NAMES: Partial<Record<BuildStage, string>> = { Hardware: 'Off-board wiring' };
  const stageName = (stage: BuildStage): string => STAGE_NAMES[stage] ?? stage;

  const placed = $derived(new Set(placedOf(card)));
  // Each row by its designator, or a counted part's key.
  const rows = $derived(
    new Map<string, Row>([
      ...project.parts.map(
        (p) => [p.ref, { label: p.ref, value: p.value, category: p.category, count: 1 }] as const,
      ),
      ...project.counted.map(
        (c) =>
          [
            c.key,
            { label: `${c.count} ×`, value: c.value, category: c.category, count: c.count },
          ] as const,
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
  const stages = $derived(
    project.build_order.map((step) => ({
      name: stageName(step.stage),
      total: sum(stepRows(step)),
      done: sum(stepRows(step).filter((ref) => placed.has(ref))),
    })),
  );
  const kinds = $derived(project.build_order.map((step) => kindOfStage(step.stage)));
  const socketed = $derived(project.build_order.some((step) => step.stage === 'Sockets'));

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

  onMount(() => show(current));
</script>

<svelte:window onkeydown={keydown} />

<div class="bench">
  <div class="head">
    <p class="note">
      Lowest parts first, so the board sits flat as you go. Press <kbd>Space</kbd> to place the current
      part and move to the next, and the arrow keys to move between parts.
    </p>
    <div class="progress">
      <p class="note count" role="status">
        {done === total && total
          ? `All ${count(total, 'part')} placed. Ready to box.`
          : `${done} of ${count(total, 'part')} placed.`}
      </p>
      <TraceProgress {stages} {done} {total} />
      <meter class="visually-hidden" min="0" max={total || 1} value={done} aria-label="Parts placed"
      ></meter>
      {#if done}
        <button class="text-button" type="button" onclick={onStartOver}>Start over</button>
      {/if}
    </div>
    <div class="choices">
      <Choice
        legend="Values"
        name="notation"
        options={[
          { value: 'decimal', label: '4.7k' },
          { value: 'rkm', label: '4k7' },
        ]}
        bind:value={display.notation}
      />
      <Choice
        legend="Bands"
        name="bands"
        options={[
          { value: 4, label: '4' },
          { value: 5, label: '5' },
        ]}
        bind:value={display.bands}
      />
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
      {#each project.build_order as step, i (step.stage)}
        {@const kind = kinds[i] ?? 'other'}
        {@const tally = stages[i]}
        <li class="stage" class:complete={tally && tally.done === tally.total}>
          <div class="stage-head">
            <h3><PartSymbol {kind} size={21} />{stageName(step.stage)}</h3>
            <span class="tally">{tally?.done ?? 0}/{tally?.total ?? 0}</span>
            <span class="climb"><HeightProfile {kinds} at={i} /></span>
          </div>
          <OrientationHint {kind} />
          {#if kind === 'chip' && socketed}
            <p class="tip">Press each chip into its socket; don't solder it.</p>
          {/if}
          <ul>
            {#each stepRows(step) as ref (ref)}
              {@const row = rows.get(ref)}
              {@const category = row?.category ?? 'Unknown'}
              {@const taper =
                category === 'Potentiometers' ? readPot(row?.value ?? '').taper : null}
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
                  <span class="pad" aria-hidden="true"></span>
                  <span class="ref">{row?.label ?? ref}</span>
                  <span class="value"
                    >{formatValue(row?.value ?? '', category, display.notation)}</span
                  >
                  <span class="extra">
                    {#if category === 'Resistors'}
                      <ResistorBands value={row?.value ?? ''} count={display.bands} />
                    {:else if taper}
                      <TaperCurve {taper} />
                    {/if}
                  </span>
                </label>
              </li>
            {/each}
          </ul>
        </li>
      {/each}
    </ol>
  </div>
</div>

<style>
  .bench {
    display: grid;
    gap: 20px;
  }
  .head {
    display: grid;
    gap: 12px;
  }
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
  .text-button:hover {
    color: var(--accent);
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
  .count {
    color: var(--text);
    font-variant-numeric: tabular-nums;
  }
  .choices {
    display: flex;
    flex-wrap: wrap;
    gap: 8px 22px;
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
    gap: 18px;
    max-width: 640px;
    margin: 0;
    padding: 0;
    list-style: none;
  }
  .stage {
    display: grid;
    gap: 6px;
  }
  .stage-head {
    display: flex;
    gap: 10px;
    align-items: flex-end;
  }
  h3 {
    display: inline-flex;
    gap: 8px;
    align-items: center;
    margin: 0;
    color: var(--muted);
    font-family: var(--mono);
    font-size: var(--fs-label);
    font-weight: 400;
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }
  .complete h3 {
    color: var(--accent);
  }
  .tally {
    color: var(--muted);
    font-family: var(--mono);
    font-size: var(--fs-label);
    font-variant-numeric: tabular-nums;
  }
  .climb {
    margin-left: auto;
  }
  .tip {
    margin: 0;
    color: var(--muted);
    font-size: var(--fs-label);
  }
  .stage ul {
    margin: 0;
    padding: 0;
    overflow: hidden;
    border: 1px solid var(--line);
    border-radius: 5px;
    background: var(--panel);
    list-style: none;
  }
  .stage ul li {
    position: relative;
    scroll-margin: 80px;
  }
  .stage ul li + li {
    border-top: 1px solid var(--line);
  }
  .stage ul li.hovered {
    background: color-mix(in srgb, var(--muted) 7%, transparent);
  }
  .stage ul li.current {
    background: var(--accent-soft);
  }
  .stage ul li.current::before {
    position: absolute;
    top: 0;
    bottom: 0;
    left: 0;
    width: 2px;
    background: var(--accent);
    content: '';
  }
  label {
    display: grid;
    grid-template-columns: 16px 8.5em minmax(5em, auto) minmax(0, 1fr);
    gap: 14px;
    align-items: center;
    padding: 6px 12px;
    cursor: pointer;
  }
  /* The checkbox stays for the keyboard and screen readers; the pad shows it. */
  input {
    position: absolute;
    width: 1px;
    height: 1px;
    margin: 0;
    opacity: 0;
  }
  /* A bare pad, then a solder joint once the part is placed. */
  .pad {
    width: 16px;
    height: 16px;
    border: 3px solid color-mix(in srgb, var(--muted) 70%, transparent);
    border-radius: 50%;
    background: var(--bg);
    transition:
      background-color 150ms,
      border-color 150ms,
      transform 150ms;
  }
  .current .pad {
    border-color: var(--accent);
  }
  /* The pad under the pointer brightens: a click solders this part. */
  li:not(.current, .placed) label:hover .pad {
    border-color: var(--text);
  }
  .placed .pad {
    border-color: color-mix(in srgb, var(--text) 60%, var(--bg));
    background: radial-gradient(
      circle at 35% 32%,
      var(--text) 0 18%,
      color-mix(in srgb, var(--text) 62%, var(--bg)) 55%,
      color-mix(in srgb, var(--text) 35%, var(--bg)) 100%
    );
    transform: scale(1.06);
  }
  input:focus-visible + .pad {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }
  /* Designators lettered like a board's silkscreen. */
  .ref {
    color: var(--text);
    font-family: var(--mono);
    font-size: var(--fs-ui);
    font-weight: 600;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    overflow-wrap: anywhere;
  }
  .value {
    font-family: var(--mono);
    font-size: var(--fs-ui);
    overflow-wrap: anywhere;
  }
  .extra {
    display: flex;
    align-items: center;
    min-height: 16px;
  }
  .placed .ref,
  .placed .value {
    color: var(--muted);
  }
  .placed .value {
    text-decoration: line-through;
  }
  .placed .extra {
    opacity: 0.45;
  }
  button:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }
  .visually-hidden {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip-path: inset(50%);
    white-space: nowrap;
  }
  @media (prefers-reduced-motion: reduce) {
    .pad {
      transition: none;
    }
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
    .stage ul li {
      scroll-margin-top: calc(var(--hs-bar-height, 0px) + 42vh + 32px);
    }
  }
  @media (width <= 600px) {
    .climb {
      display: none;
    }
  }
</style>
