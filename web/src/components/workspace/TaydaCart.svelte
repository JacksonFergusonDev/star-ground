<script lang="ts">
  // The parts Star Ground knows Tayda's SKU for, ready for Tayda's Quick Order
  // page: copied as `SKU,QTY` lines to paste, or downloaded as its CSV. Each
  // quantity is already rounded up to the multiple Tayda sells the part in.
  // The rest of the list keeps its Tayda search links.
  import type { ExportKind, ShoppingRow } from '../../lib/api';
  import type { Download, Failure } from '../../lib/server';
  import { bugOf } from '../../lib/server';
  import { count } from '../../lib/workspace.svelte';
  import BugNotice from './BugNotice.svelte';
  import Icon from './Icon.svelte';

  let {
    rows,
    onExport,
  }: {
    rows: ShoppingRow[];
    onExport: (kind: ExportKind, excel: boolean) => Promise<Download>;
  } = $props();

  const QUICK_ORDER = 'https://www.taydaelectronics.com/quick-order/';
  const COPY = 'Copy for Quick Order';

  const toBuy = $derived(rows.filter((r) => r.buy_qty > 0));
  // One line per SKU, as the server's cart file has it.
  const cart = $derived.by(() => {
    const qty: Record<string, number> = {};
    for (const row of toBuy) {
      if (row.tayda) qty[row.tayda.sku] = (qty[row.tayda.sku] ?? 0) + row.tayda.qty;
    }
    return Object.entries(qty);
  });
  const inCart = $derived(toBuy.filter((r) => r.tayda).length);
  const text = $derived(cart.map(([sku, qty]) => `${sku},${qty}`).join('\n'));

  let label = $state(COPY);
  // The lines, shown to copy by hand when the clipboard refuses them.
  let fallback = $state<string | null>(null);
  let busy = $state(false);
  let problem = $state<Failure | null>(null);

  async function copy(): Promise<void> {
    try {
      await navigator.clipboard.writeText(text);
      label = 'Copied!';
      fallback = null;
    } catch {
      label = 'Failed';
      fallback = text;
    }
    setTimeout(() => (label = COPY), 2000);
  }

  async function download(): Promise<void> {
    busy = true;
    problem = null;
    try {
      const { blob, filename } = await onExport('tayda-cart', false);
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = filename;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (e) {
      problem = { message: (e as Error).message, bugCode: bugOf(e) };
    } finally {
      busy = false;
    }
  }
</script>

{#if toBuy.length}
  <section class="cart" aria-labelledby="cart-title">
    <h2 id="cart-title">Order from Tayda</h2>
    {#if inCart}
      <p class="note">
        Star Ground knows Tayda's product for {inCart} of the {count(toBuy.length, 'part')} to buy. Paste
        or upload them on Tayda's Quick Order page to fill your cart in one go.
        {#if inCart < toBuy.length}
          Add the other {toBuy.length - inCart} with their Tayda links in the table below.
        {/if}
      </p>
      <div class="actions">
        <button class="hs-button hs-button--primary" type="button" onclick={copy}>{label}</button>
        <button class="hs-button" type="button" disabled={busy} onclick={download}>
          {busy ? 'Preparing…' : 'Download CSV'}
        </button>
        <a class="text-link" href={QUICK_ORDER} target="_blank" rel="noopener noreferrer">
          Open Quick Order <Icon name="arrow-up-right" size={14} />
        </a>
      </div>
      {#if fallback}
        <label class="fallback">
          Copy these lines by hand:
          <textarea readonly rows={Math.min(cart.length, 8)}>{fallback}</textarea>
        </label>
      {/if}
      {#if problem}
        <p class="problem" role="alert">{problem.message}</p>
        {#if problem.bugCode}
          <BugNotice code={problem.bugCode} document="Downloading the Tayda cart" />
        {/if}
      {/if}
    {:else}
      <p class="note">
        Star Ground doesn't know Tayda's product for any of these parts yet, so there's no cart to
        fill. Each part's Tayda link in the table below searches for it.
      </p>
    {/if}
  </section>
{/if}

<style>
  .cart {
    display: grid;
    gap: 12px;
    padding: 16px 18px;
    border: 1px solid var(--line);
    border-radius: 5px;
    background: var(--panel);
  }
  h2 {
    max-width: none;
    margin: 0;
    font-size: var(--fs-h4);
    font-weight: 500;
    letter-spacing: 0;
  }
  .note,
  .problem,
  .fallback {
    max-width: 72ch;
    margin: 0;
    color: var(--muted);
    font-size: var(--fs-ui);
    line-height: 1.6;
  }
  .problem {
    color: var(--text);
  }
  .actions {
    display: flex;
    flex-wrap: wrap;
    gap: 10px 16px;
    align-items: center;
  }
  .hs-button:disabled {
    opacity: 0.6;
    cursor: progress;
  }
  .fallback {
    display: grid;
    gap: 6px;
  }
  textarea {
    width: 100%;
    max-width: 24ch;
    font-family: var(--mono);
  }
</style>
