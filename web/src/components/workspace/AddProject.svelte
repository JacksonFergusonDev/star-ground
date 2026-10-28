<script lang="ts">
  // Adds a project from a pasted list, a preset, an uploaded document or a link.
  import type { PresetCatalog } from '../../lib/api';
  import type { Added } from '../../lib/workspace.svelte';

  let { catalog, onAdd }: { catalog: PresetCatalog | null; onAdd: (added: Added) => void } =
    $props();

  const ways = [
    { id: 'upload', label: 'Upload' },
    { id: 'url', label: 'Link' },
    { id: 'preset', label: 'Preset' },
    { id: 'paste', label: 'Paste' },
  ] as const;
  let way = $state<(typeof ways)[number]['id']>('upload');

  let text = $state('');
  let name = $state('');
  let preset = $state('');
  let url = $state('');
  let fileInput = $state<HTMLInputElement>();

  const example = 'R1 10k\nC1 100n\nU1 TL072';

  // Presets grouped by source, then category, as the library lists them.
  const groups = $derived(
    catalog
      ? catalog.sources.flatMap((source) =>
          (catalog.categories[source] ?? []).map((category) => ({
            label: `${source} · ${category}`,
            presets: catalog.presets.filter((p) => p.source === source && p.category === category),
          })),
        )
      : [],
  );

  function submit(event: SubmitEvent): void {
    event.preventDefault();
    switch (way) {
      case 'paste':
        onAdd({ kind: 'paste', name: name.trim() || 'Pasted list', text });
        text = name = '';
        break;
      case 'preset':
        if (preset) onAdd({ kind: 'preset', key: preset });
        preset = '';
        break;
      case 'url':
        onAdd({ kind: 'url', url: url.trim() });
        url = '';
        break;
      case 'upload':
        for (const file of fileInput?.files ?? []) onAdd({ kind: 'upload', file });
        if (fileInput) fileInput.value = '';
        break;
    }
  }
</script>

<section class="add" aria-labelledby="add-title">
  <h2 id="add-title">Add a project</h2>
  <div class="ways" role="group" aria-label="Where the parts list comes from">
    {#each ways as w (w.id)}
      <button type="button" aria-pressed={way === w.id} onclick={() => (way = w.id)}>
        {w.label}
      </button>
    {/each}
  </div>

  <form onsubmit={submit}>
    {#if way === 'upload'}
      <label for="add-file">Build documents (PDF)</label>
      <input
        id="add-file"
        bind:this={fileInput}
        type="file"
        accept=".pdf,application/pdf"
        multiple
        required
      />
    {:else if way === 'url'}
      <label for="add-url">Link to a build document</label>
      <input
        id="add-url"
        type="url"
        bind:value={url}
        placeholder="https://docs.pedalpcb.com/…"
        required
      />
    {:else if way === 'preset'}
      <label for="add-preset">Preset</label>
      <select id="add-preset" bind:value={preset} required disabled={!catalog}>
        <option value="" disabled>{catalog ? 'Choose a circuit' : 'Loading presets…'}</option>
        {#each groups as group (group.label)}
          <optgroup label={group.label}>
            {#each group.presets as p (p.key)}
              <option value={p.key}>{p.name}</option>
            {/each}
          </optgroup>
        {/each}
      </select>
    {:else}
      <label for="add-name">Name</label>
      <input id="add-name" bind:value={name} placeholder="Pasted list" />
      <label for="add-text">Parts list</label>
      <textarea
        id="add-text"
        bind:value={text}
        rows="6"
        spellcheck="false"
        placeholder={example}
        required></textarea>
    {/if}
    <button class="hs-button hs-button--primary" type="submit">Add</button>
  </form>
</section>

<style>
  .add {
    display: grid;
    gap: 14px;
    padding: 18px 20px;
    border: 1px dashed var(--line);
    border-radius: 5px;
  }
  h2 {
    margin: 0;
    font-size: var(--fs-h4);
    font-weight: 500;
  }
  .ways {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
  }
  .ways button {
    padding: 6px 12px;
    border: 1px solid var(--line);
    border-radius: 5px;
    color: var(--muted);
    background: none;
    font-family: var(--mono);
    font-size: var(--fs-label);
    letter-spacing: 0.08em;
    text-transform: uppercase;
    cursor: pointer;
  }
  .ways button[aria-pressed='true'] {
    border-color: var(--accent-border);
    color: var(--text);
    background: var(--accent-soft);
  }
  form {
    display: grid;
    gap: 8px;
  }
  label {
    color: var(--muted);
    font-family: var(--mono);
    font-size: var(--fs-label);
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }
  input,
  select,
  textarea {
    width: 100%;
    padding: 10px 12px;
    border: 1px solid var(--line);
    border-radius: 5px;
    color: var(--text);
    background: var(--panel);
    font-family: var(--mono);
    font-size: var(--fs-ui);
  }
  textarea {
    line-height: 1.6;
    resize: vertical;
  }
  .hs-button {
    justify-self: start;
    margin-top: 6px;
  }
  input:focus-visible,
  select:focus-visible,
  textarea:focus-visible,
  button:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }
</style>
