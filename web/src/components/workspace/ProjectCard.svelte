<script lang="ts">
  // One project: its name, how many to build, where its parts list comes from,
  // and the choices its document asks for (a build, OCR) on the card itself.
  import type { ProjectResult } from '../../lib/api';
  import { FINISHES, PAINTS, faceOf, finishOf, paintOf } from '../../lib/enclosure';
  import type { Finish, Paint } from '../../lib/enclosure';
  import type { Card } from '../../lib/workspace.svelte';
  import { blocker, builds, count, placedOf } from '../../lib/workspace.svelte';
  import BugNotice from './BugNotice.svelte';
  import Enclosure from './Enclosure.svelte';
  import Icon from './Icon.svelte';
  import SignalPulse from './SignalPulse.svelte';

  let {
    card,
    project,
    presetName,
    onCount,
    onRemove,
    onRead,
    flags,
    checkLink,
    buildLink,
  }: {
    card: Card;
    /** Its parts as last listed; null until they are. */
    project: ProjectResult | null;
    presetName: (key: string) => string;
    onCount: (count: number) => void;
    onRemove: () => void;
    onRead: (ocr: boolean) => void;
    /** The parts Star Ground flagged that the builder hasn't checked yet. */
    flags: number;
    /** The page that checks the parts against the document; null until they're listed. */
    checkLink: string | null;
    /** Bench mode's page; null until the parts are listed. */
    buildLink: string | null;
  } = $props();

  const choices = $derived(builds(card));
  const paint = $derived(paintOf(card.id, card.paint));
  const face = $derived(faceOf(project));
  // The LED lights once bench mode has every part placed.
  const finished = $derived.by(() => {
    if (!project?.build_order.length) return false;
    const placed = new Set(placedOf(card));
    return project.build_order.every((step) =>
      [...step.refs, ...step.counted].every((ref) => placed.has(ref)),
    );
  });
  const paints = Object.entries(PAINTS) as [Paint, (typeof PAINTS)[Paint]][];
  const finish = $derived(finishOf(card.finish));
  const finishes = Object.entries(FINISHES) as [Finish, string][];
  const waiting = $derived(blocker(card));
  const ocr = $derived(card.reading?.ocr ?? null);
  // What a bug report names: the upload's file name or the link, never the file.
  const documentName = $derived(
    card.source.kind === 'upload'
      ? card.source.fileName
      : card.source.kind === 'url'
        ? card.source.url
        : card.name,
  );

  const source = $derived.by(() => {
    switch (card.source.kind) {
      case 'paste':
        return `Pasted list, ${count(card.source.text.trim().split('\n').length, 'line')}`;
      case 'preset':
        return `Preset: ${presetName(card.source.key)}`;
      case 'upload':
        return `Upload: ${card.source.fileName}`;
      case 'url':
        return card.source.catalog ? card.source.url : `Link: ${card.source.url}`;
    }
  });

  // A build chosen before OCR's parts were accepted (or dropped) may not exist
  // in the other list.
  $effect(() => {
    if (card.variant && !choices.includes(card.variant)) card.variant = null;
  });
</script>

<li class="card" id={`project-${card.id}`} aria-labelledby={`name-${card.id}`}>
  <div class="pedal">
    <Enclosure name={card.name} {face} {paint} {finish} lit={finished} />
    <details class="paint">
      <summary>Paint</summary>
      <fieldset>
        <legend class="visually-hidden">Paint for {card.name}</legend>
        {#each paints as [key, coat] (key)}
          <label class="swatch" title={coat.name}>
            <input
              type="radio"
              name={`paint-${card.id}`}
              value={key}
              checked={paint === key}
              onchange={() => (card.paint = key)}
            />
            <span style:background={coat.color}></span>
            <span class="visually-hidden">{coat.name}</span>
          </label>
        {/each}
      </fieldset>
      <fieldset class="finishes">
        <legend class="visually-hidden">Finish for {card.name}</legend>
        {#each finishes as [key, label] (key)}
          <label class="finish">
            <input
              type="radio"
              name={`finish-${card.id}`}
              value={key}
              checked={finish === key}
              onchange={() => (card.finish = key)}
            />
            <span>{label}</span>
          </label>
        {/each}
      </fieldset>
    </details>
  </div>
  <div class="body">
    <div class="head">
      <label class="visually-hidden" for={`name-${card.id}`}>Project name</label>
      <input id={`name-${card.id}`} class="name" bind:value={card.name} />
      <label class="count">
        <span>Pedals</span>
        <input
          type="number"
          min="1"
          max="100"
          value={card.count}
          onchange={(e) => onCount(e.currentTarget.valueAsNumber)}
        />
      </label>
      <button class="remove" type="button" onclick={onRemove} aria-label={`Remove ${card.name}`}>
        <Icon name="close" />
      </button>
    </div>
    <p class="source">
      {#if card.source.kind === 'url' && card.source.catalog}
        From the <a href={`/catalog/${card.source.catalog}/`}>catalog</a>: {source}
      {:else}
        {source}
      {/if}
    </p>

    {#if card.read.state === 'reading'}
      <SignalPulse label="Reading the document…" />
    {:else if card.read.state === 'queued'}
      <SignalPulse label={`Waiting for OCR: ${card.read.position} ahead.`} />
    {:else if card.read.state === 'failed'}
      <p class="status problem">{card.read.message}</p>
      {#if card.read.bugCode}
        <BugNotice code={card.read.bugCode} document={documentName} />
      {/if}
      <button class="text-button" type="button" onclick={() => onRead(card.useOcr)}>
        Read it again
      </button>
    {/if}

    {#if card.reading?.ocr_offered && card.read.state === 'ready'}
      <div class="offer">
        <p>
          This parts list is an image. Star Ground can read it by OCR, which takes a few seconds a
          page, and lists every part it read for you to check.
        </p>
        <button class="hs-button" type="button" onclick={() => onRead(true)}>Read it by OCR</button>
      </div>
    {/if}

    {#if ocr}
      <div class="offer">
        <p>{ocr.message}</p>
        {#if ocr.has_parts}
          <label class="check">
            <input type="checkbox" bind:checked={card.useOcr} />
            Add the {ocr.parts_read} parts OCR read
          </label>
        {/if}
      </div>
    {/if}

    {#if choices.length}
      <label class="build">
        <span>Build</span>
        <select bind:value={card.variant}>
          <option value={null} disabled>Choose a build</option>
          {#each choices as name (name)}
            <option value={name}>{name}</option>
          {/each}
        </select>
      </label>
    {/if}

    {#if waiting && card.read.state === 'ready'}
      <p class="status">{waiting}</p>
    {/if}

    {#if checkLink || buildLink}
      <div class="actions">
        {#if checkLink}
          <a class="action" href={checkLink}>
            Check the parts against the document
            {#if flags}<span class="flags">{flags} flagged</span>{/if}
            <Icon name="arrow-right" size={14} />
          </a>
        {/if}
        {#if buildLink}
          <a class="action" href={buildLink}>
            Build it, part by part <Icon name="arrow-right" size={14} />
          </a>
        {/if}
      </div>
    {/if}
  </div>
</li>

<style>
  .card {
    display: grid;
    grid-template-columns: auto minmax(0, 1fr);
    gap: 20px;
    padding: 18px 20px;
    border: 1px solid var(--line);
    border-radius: 5px;
    background: var(--panel);
  }
  .body {
    display: grid;
    align-content: start;
    gap: 10px;
    min-width: 0;
  }
  .pedal {
    display: grid;
    gap: 8px;
    justify-items: center;
    align-content: start;
  }
  .paint summary {
    color: var(--muted);
    font-family: var(--mono);
    font-size: var(--fs-label);
    letter-spacing: 0.08em;
    text-transform: uppercase;
    cursor: pointer;
    list-style: none;
  }
  .paint summary::-webkit-details-marker {
    display: none;
  }
  .paint[open] summary {
    color: var(--text);
  }
  .paint fieldset {
    display: grid;
    grid-template-columns: repeat(4, 14px);
    gap: 6px;
    margin: 8px 0 0;
    padding: 0;
    border: 0;
  }
  .swatch {
    position: relative;
    display: block;
    width: 14px;
    height: 14px;
    cursor: pointer;
  }
  .swatch input {
    position: absolute;
    inset: 0;
    margin: 0;
    opacity: 0;
    cursor: pointer;
  }
  .swatch span:not(.visually-hidden) {
    display: block;
    width: 14px;
    height: 14px;
    border: 1px solid var(--line);
    border-radius: 50%;
  }
  .swatch input:checked + span {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }
  .swatch:hover span:not(.visually-hidden) {
    border-color: var(--text);
  }
  .finishes {
    display: grid;
    gap: 2px;
    margin: 10px 0 0;
    padding: 0;
    border: 0;
  }
  .finish {
    position: relative;
    cursor: pointer;
  }
  .finish input {
    position: absolute;
    inset: 0;
    margin: 0;
    opacity: 0;
    cursor: pointer;
  }
  .finish span {
    display: block;
    padding: 2px 6px;
    border: 1px solid transparent;
    border-radius: 3px;
    color: var(--muted);
    font-family: var(--mono);
    font-size: var(--fs-label);
  }
  .finish:hover span {
    color: var(--text);
  }
  .finish input:checked + span {
    border-color: var(--accent-border);
    color: var(--text);
    background: var(--accent-soft);
  }
  .finish input:focus-visible + span {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }
  .swatch input:focus-visible + span {
    outline: 2px solid var(--text);
    outline-offset: 2px;
  }
  summary:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }
  @media (width <= 520px) {
    .card {
      grid-template-columns: minmax(0, 1fr);
    }
    .pedal {
      grid-template-columns: auto auto;
      justify-content: start;
      align-items: start;
      gap: 16px;
    }
  }
  .head {
    display: flex;
    align-items: center;
    gap: 12px;
  }
  .name {
    flex: 1;
    width: 0;
    min-width: 0;
    padding: 4px 0;
    border: 0;
    border-bottom: 1px solid transparent;
    color: var(--text);
    background: none;
    font: inherit;
    font-size: var(--fs-h4);
    font-weight: 500;
  }
  .name:hover,
  .name:focus-visible {
    border-bottom-color: var(--line);
  }
  .count,
  .build {
    display: flex;
    align-items: center;
    gap: 8px;
    color: var(--muted);
    font-family: var(--mono);
    font-size: var(--fs-label);
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }
  .count input {
    width: 4.5em;
  }
  input[type='number'],
  select {
    padding: 6px 8px;
    border: 1px solid var(--line);
    border-radius: 5px;
    color: var(--text);
    background: var(--bg);
    font-family: var(--mono);
    font-size: var(--fs-ui);
    letter-spacing: 0;
    text-transform: none;
  }
  select {
    flex: 1;
    min-width: 0;
  }
  .remove,
  .text-button {
    border: 0;
    color: var(--muted);
    background: none;
    cursor: pointer;
  }
  .remove {
    display: grid;
    padding: 6px;
  }
  .remove:hover,
  .text-button:hover {
    color: var(--accent);
  }
  .card:target {
    border-color: var(--accent-border);
  }
  .actions {
    display: flex;
    flex-wrap: wrap;
    gap: 8px 24px;
    margin-top: 4px;
    padding-top: 12px;
    border-top: 1px solid var(--line);
  }
  .action {
    display: inline-flex;
    gap: 8px;
    align-items: center;
    color: var(--text);
    font-size: var(--fs-ui);
  }
  .flags {
    padding: 1px 7px;
    border: 1px solid var(--accent-border);
    border-radius: 999px;
    color: var(--accent);
    font-family: var(--mono);
    font-size: var(--fs-label);
  }
  .text-button {
    justify-self: start;
    padding: 0;
    font: inherit;
    font-size: var(--fs-ui);
    text-decoration: underline;
  }
  .source,
  .status,
  .offer p {
    margin: 0;
    color: var(--muted);
    font-size: var(--fs-ui);
    line-height: 1.6;
    overflow-wrap: anywhere;
  }
  .source a {
    text-decoration: underline;
    text-underline-offset: 3px;
  }
  .problem {
    color: var(--text);
  }
  .offer {
    display: grid;
    gap: 10px;
    padding: 12px 14px;
    border: 1px solid var(--accent-border);
    border-radius: 5px;
    background: var(--accent-soft);
  }
  .offer .hs-button {
    justify-self: start;
  }
  .check {
    display: flex;
    gap: 8px;
    align-items: center;
    font-size: var(--fs-ui);
  }
  input:focus-visible,
  select:focus-visible,
  button:focus-visible,
  a:focus-visible {
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
</style>
