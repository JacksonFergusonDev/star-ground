<script lang="ts">
  // Adds a project: a build found in the catalog, whose parts lists are
  // already read, or an uploaded document, a link or a pasted list.
  import type { CatalogItem } from '../../lib/catalog-search';
  import { searchCatalog } from '../../lib/catalog-search';
  import type { Added } from '../../lib/workspace.svelte';
  import Icon from './Icon.svelte';

  let { onAdd }: { onAdd: (added: Added) => void } = $props();

  const ways = [
    { id: 'catalog', label: 'Catalog' },
    { id: 'upload', label: 'Upload' },
    { id: 'url', label: 'Link' },
    { id: 'paste', label: 'Paste' },
  ] as const;
  let way = $state<(typeof ways)[number]['id']>('catalog');

  // The catalog, fetched when the search is first used.
  let catalog = $state<CatalogItem[] | 'loading' | 'failed' | null>(null);
  let query = $state('');
  const found = $derived(Array.isArray(catalog) ? searchCatalog(catalog, query) : []);

  function loadCatalog(): void {
    if (catalog !== null && catalog !== 'failed') return;
    catalog = 'loading';
    fetch('/catalog/index.json')
      .then((r) => (r.ok ? (r.json() as Promise<CatalogItem[]>) : Promise.reject(new Error())))
      .then((items) => (catalog = items))
      .catch(() => (catalog = 'failed'));
  }

  function addBuild(item: CatalogItem): void {
    onAdd({ kind: 'url', url: item.document, name: item.title, catalog: item.id });
    query = '';
  }

  let text = $state('');
  let name = $state('');
  let url = $state('');
  let fileInput = $state<HTMLInputElement>();

  const example = 'R1 10k\nC1 100n\nU1 TL072';

  function submit(event: SubmitEvent): void {
    event.preventDefault();
    switch (way) {
      case 'paste':
        onAdd({ kind: 'paste', name: name.trim() || 'Pasted list', text });
        text = name = '';
        break;
      case 'url':
        onAdd({ kind: 'url', url: url.trim() });
        url = '';
        break;
      case 'upload':
        for (const file of fileInput?.files ?? []) onAdd({ kind: 'upload', file });
        if (fileInput) fileInput.value = '';
        break;
      case 'catalog':
        // Enter adds the best match.
        if (found[0]) addBuild(found[0]);
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
    {#if way === 'catalog'}
      <label for="add-search">Search the catalog</label>
      <input
        id="add-search"
        type="search"
        bind:value={query}
        onfocus={loadCatalog}
        oninput={loadCatalog}
        autocomplete="off"
        placeholder="A pedal's name, or fuzz, delay…"
      />
      <div aria-live="polite">
        {#if catalog === 'failed'}
          <p class="hint">The catalog didn't load. Check your connection and search again.</p>
        {:else if query.trim() && Array.isArray(catalog) && !found.length}
          <p class="hint">
            Nothing in the catalog matches "{query.trim()}". Try another spelling, or add the build
            document by Upload or Link.
          </p>
        {:else if found.length}
          <ul class="found" aria-label="Catalog builds">
            {#each found as item (item.id)}
              <li>
                <button class="hover-card" type="button" onclick={() => addBuild(item)}>
                  <span class="title">{item.title}</span>
                  <span class="meta"
                    >{[item.publisher, ...item.effects].join(' · ')} · {item.parts} parts{item.first
                      ? ' · Good first build'
                      : ''}</span
                  >
                  <span class="hs-visually-hidden">: add it</span>
                </button>
              </li>
            {/each}
          </ul>
        {:else if !query.trim()}
          <p class="hint">
            The catalog's parts lists are already read and checked, so a build from it is ready at
            once.
          </p>
        {/if}
      </div>
    {:else if way === 'upload'}
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
    {#if way !== 'catalog'}
      <button class="hs-button hs-button--primary" type="submit">Add</button>
    {/if}
  </form>
  <a class="catalog" href="/catalog/"
    >Browse the whole catalog <Icon name="arrow-right" size={14} /></a
  >
</section>

<style>
  .add {
    display: grid;
    gap: 14px;
    padding: 18px 20px;
    border: 1px solid var(--line);
    border-radius: 5px;
    background: var(--panel);
  }
  h2 {
    max-width: none;
    letter-spacing: 0;
    line-height: 1.4;
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
  .ways button:hover {
    color: var(--text);
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
    background: var(--bg);
    font-family: var(--mono);
    font-size: var(--fs-ui);
  }
  textarea {
    line-height: 1.6;
    resize: vertical;
  }
  .catalog {
    display: inline-flex;
    gap: 6px;
    align-items: center;
    justify-self: start;
    color: var(--muted);
    font-size: var(--fs-ui);
  }
  .hint {
    margin: 0;
    color: var(--muted);
    font-size: var(--fs-ui);
    line-height: 1.6;
  }
  .found {
    display: grid;
    gap: 6px;
    margin: 0;
    padding: 0;
    list-style: none;
  }
  .found button {
    display: grid;
    gap: 2px;
    width: 100%;
    padding: 8px 12px;
    border: 1px solid var(--line);
    border-radius: 5px;
    color: var(--text);
    background: var(--bg);
    font: inherit;
    text-align: left;
    cursor: pointer;
  }
  .title {
    font-size: var(--fs-ui);
  }
  .meta {
    color: var(--muted);
    font-family: var(--mono);
    font-size: var(--fs-label);
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
  input[type='file'] {
    padding: 8px;
    border: 1px dashed var(--line);
    border-radius: 5px;
    color: var(--muted);
    background: var(--bg);
    font-family: var(--sans);
    font-size: var(--fs-ui);
    cursor: pointer;
  }
  input[type='file']::file-selector-button {
    margin-right: 12px;
    padding: 8px 14px;
    border: 1px solid var(--line);
    border-radius: 3px;
    color: var(--text);
    background: var(--panel);
    font: inherit;
    cursor: pointer;
  }
</style>
