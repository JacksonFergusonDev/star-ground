<script lang="ts">
  // The master list's files: the shopping list, a supplier cart, the stock
  // after this order, field manuals and sticker sheets, and the build pack.
  import type { ExportKind } from '../../lib/api';
  import type { Download } from '../../lib/server';

  let { onExport }: { onExport: (kind: ExportKind, excel: boolean) => Promise<Download> } =
    $props();

  const files: { kind: ExportKind; label: string; detail: string }[] = [
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
    {
      kind: 'field-manuals',
      label: 'Field manuals',
      detail: 'A build checklist and sticker sheet per project (PDF).',
    },
  ];

  let excel = $state(true);
  let busy = $state<ExportKind | null>(null);
  let problem = $state<string | null>(null);

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
      problem = (e as Error).message;
    } finally {
      busy = null;
    }
  }
</script>

<section class="exports" aria-labelledby="exports-title">
  <h3 id="exports-title">Download</h3>
  <button
    class="hs-button hs-button--primary"
    type="button"
    disabled={busy !== null}
    onclick={() => save('build-pack')}
  >
    {busy === 'build-pack' ? 'Preparing…' : 'Build pack (ZIP)'}
  </button>
  <p class="detail">
    Everything below in one ZIP, with each project's source document where you uploaded it.
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
    <p class="problem" role="alert">{problem}</p>
  {/if}
</section>

<style>
  .exports {
    display: grid;
    gap: 12px;
    margin-top: 20px;
  }
  h3 {
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
