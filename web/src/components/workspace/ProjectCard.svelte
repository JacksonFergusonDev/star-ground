<script lang="ts">
  // One project: its name, how many to build, where its parts list comes from,
  // and the choices its document asks for (a build, OCR) on the card itself.
  import type { Card } from '../../lib/workspace.svelte';
  import { blocker, builds, count } from '../../lib/workspace.svelte';
  import BugNotice from './BugNotice.svelte';
  import Icon from './Icon.svelte';

  let {
    card,
    presetName,
    onCount,
    onRemove,
    onRead,
  }: {
    card: Card;
    presetName: (key: string) => string;
    onCount: (count: number) => void;
    onRemove: () => void;
    onRead: (ocr: boolean) => void;
  } = $props();

  const choices = $derived(builds(card));
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
        return `Link: ${card.source.url}`;
    }
  });

  // A build chosen before OCR's parts were accepted (or dropped) may not exist
  // in the other list.
  $effect(() => {
    if (card.variant && !choices.includes(card.variant)) card.variant = null;
  });
</script>

<li class="card" aria-labelledby={`name-${card.id}`}>
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
  <p class="source">{source}</p>

  {#if card.read.state === 'reading'}
    <p class="status">Reading the document…</p>
  {:else if card.read.state === 'queued'}
    <p class="status">Waiting for OCR: {card.read.position} ahead.</p>
  {:else if card.read.state === 'failed'}
    <p class="status problem">{card.read.message}</p>
    {#if card.read.bug}
      <BugNotice bug={card.read.bug} document={documentName} />
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
</li>

<style>
  .card {
    display: grid;
    gap: 10px;
    padding: 18px 20px;
    border: 1px solid var(--line);
    border-radius: 5px;
    background: var(--panel);
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
</style>
