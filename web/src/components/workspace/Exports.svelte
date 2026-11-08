<script lang="ts">
  // The master list's files: the build book, labels, the shopping list, a
  // supplier cart, the stock after this order, and the build pack of them all.
  import type { ExportKind } from '../../lib/api';
  import type { Download, Failure } from '../../lib/server';
  import { bugOf } from '../../lib/server';
  import BugNotice from './BugNotice.svelte';

  let { onExport }: { onExport: (kind: ExportKind, excel: boolean) => Promise<Download> } =
    $props();

  const files: { kind: ExportKind; label: string; detail: string }[] = [
    {
      kind: 'build-book',
      label: 'Build book',
      detail: "What to buy, then each project's parts in build order (PDF).",
    },
    {
      kind: 'labels',
      label: 'Labels',
      detail: 'A label per part for Avery 5160 sheets (PDF).',
    },
    { kind: 'shopping-list', label: 'Shopping list', detail: 'CSV of every part to buy.' },
    {
      kind: 'supplier-cart',
      label: 'Supplier cart',
      detail: "CSV for Mouser's or Digi-Key's BOM tool.",
    },
    {
      kind: 'stock-update',
      label: 'Updated stock',
      detail: 'Your stock, minus these builds, plus this order.',
    },
  ];

  let excel = $state(true);
  let busy = $state<ExportKind | null>(null);
  let problem = $state<Failure | null>(null);

  async function save(kind: ExportKind): Promise<void> {
    busy = kind;
    problem = null;
    try {
      const { blob, filename } = await onExport(kind, excel);
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = filename;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (e) {
      problem = { message: (e as Error).message, bugCode: bugOf(e) };
    } finally {
      busy = null;
    }
  }
</script>

<section class="exports" aria-labelledby="exports-title">
  <h2 id="exports-title">Download</h2>
  <button
    class="hs-button hs-button--primary"
    type="button"
    disabled={busy !== null}
    onclick={() => save('build-pack')}
  >
    {busy === 'build-pack' ? 'Preparing…' : 'Build pack (ZIP)'}
  </button>
  <p class="detail">
    Everything below in one folder, with each project's source document where you uploaded it.
  </p>
  <ul>
    {#each files as file (file.kind)}
      <li>
        <button
          class="hs-button"
          type="button"
          disabled={busy !== null}
          onclick={() => save(file.kind)}
        >
          {busy === file.kind ? 'Preparing…' : file.label}
        </button>
        <span class="detail">{file.detail}</span>
      </li>
    {/each}
  </ul>
  <label class="check">
    <input type="checkbox" bind:checked={excel} />
    Write supplier links as clickable Excel or Google Sheets links
  </label>
  {#if problem}
    <p class="problem" role="alert">{problem.message}</p>
    {#if problem.bugCode}
      <BugNotice code={problem.bugCode} document="Downloading an export" />
    {/if}
  {/if}
</section>

<style>
  .exports {
    display: grid;
    gap: 12px;
  }
  h2 {
    max-width: none;
    letter-spacing: 0;
    line-height: 1.4;
    margin: 0;
    font-size: var(--fs-h4);
    font-weight: 500;
  }
  ul {
    display: grid;
    gap: 10px;
    margin: 0;
    padding: 0;
    list-style: none;
  }
  li {
    display: flex;
    flex-wrap: wrap;
    gap: 6px 14px;
    align-items: center;
  }
  li .hs-button {
    min-width: 11em;
  }
  .hs-button {
    justify-self: start;
  }
  .hs-button:disabled {
    opacity: 0.6;
    cursor: progress;
  }
  .detail,
  .check,
  .problem {
    margin: 0;
    color: var(--muted);
    font-size: var(--fs-ui);
    line-height: 1.6;
  }
  .problem {
    color: var(--text);
  }
  .check {
    display: flex;
    gap: 8px;
    align-items: center;
  }
  input:focus-visible,
  button:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }
</style>
