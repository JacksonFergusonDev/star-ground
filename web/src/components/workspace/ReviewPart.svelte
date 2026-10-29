<script lang="ts">
  // One part to check against the document: confirm it, or correct it and
  // tell Star Ground, which adds the document to the parser's regression
  // corpus. The report names the document by its link or file name; the
  // document itself is never sent.
  import type { ReadPartOut } from '../../lib/api';
  import { readPart, reportsUnavailable, sendPartReport } from '../../lib/server';
  import type { Check } from '../../lib/workspace.svelte';

  let {
    part,
    check,
    warnings,
    selected,
    document,
    onSelect,
    onCheck,
  }: {
    part: ReadPartOut;
    check: Check | null;
    warnings: string[];
    selected: boolean;
    /** What a report names: the link, or the upload's name and hash. */
    document: string;
    onSelect: () => void;
    onCheck: (check: Check | null) => void;
  } = $props();

  // The value as read, and as the list now has it.
  const read = $derived(check?.state === 'corrected' ? check.read : part.value);
  const value = $derived(check?.state === 'corrected' ? check.value : part.value);
  const confirmed = $derived(check?.state === 'confirmed' && check.value === value);

  let editing = $state(false);
  let typed = $state('');
  let note = $state('');
  let tell = $state(true);
  let saving = $state(false);
  let problem = $state('');
  let sent = $state<'sent' | 'unavailable' | null>(null);

  function edit(): void {
    typed = value;
    note = '';
    problem = '';
    sent = null;
    editing = true;
  }

  async function save(event: SubmitEvent): Promise<void> {
    event.preventDefault();
    saving = true;
    problem = '';
    try {
      const changed = typed.trim() !== value;
      if (changed) {
        const corrected = await readPart({ ref: part.ref, value: typed, category: part.category });
        onCheck(
          corrected.value === read
            ? null
            : { state: 'corrected', read, category: corrected.category, value: corrected.value },
        );
      }
      if (tell) {
        try {
          await sendPartReport({
            document,
            ref: part.ref,
            read,
            correction: changed ? typed.trim() : null,
            location: part.location,
            comment: note.trim(),
          });
          sent = 'sent';
        } catch (e) {
          if (!reportsUnavailable(e)) throw e;
          sent = 'unavailable';
        }
      }
      editing = false;
    } catch (e) {
      problem = (e as Error).message;
    } finally {
      saving = false;
    }
  }
</script>

<li class="part" data-ref={part.ref} class:selected class:flagged={warnings.length && !check}>
  <div class="row">
    <button class="show" type="button" onclick={onSelect} aria-pressed={selected}>
      <span class="ref">{part.ref}</span>
      <span class="value">{value}</span>
      {#if !part.location}
        <span class="where">Not found on the page</span>
      {/if}
    </button>
    <div class="marks">
      <button
        type="button"
        class="action"
        aria-pressed={confirmed}
        onclick={() => onCheck(confirmed ? null : { state: 'confirmed', value })}
      >
        {confirmed ? 'Checked' : 'Looks right'}
      </button>
      <button type="button" class="action" aria-expanded={editing} onclick={edit}>Wrong?</button>
    </div>
  </div>

  {#if check?.state === 'corrected'}
    <p class="note">
      Corrected; Star Ground read {read}.
      <button class="text-button" type="button" onclick={() => onCheck(null)}>Undo</button>
    </p>
  {/if}
  {#each warnings as warning, i (i)}
    <p class="note">{warning}</p>
  {/each}
  {#if sent === 'sent'}
    <p class="note" role="status">Report sent. Thank you: it helps the parser get this right.</p>
  {:else if sent === 'unavailable'}
    <p class="note" role="status">This server can't take reports, so only your copy changed.</p>
  {/if}

  {#if editing}
    <form class="edit" onsubmit={save}>
      <label>
        <span>What the document prints for {part.ref}</span>
        <input bind:value={typed} required autocomplete="off" />
      </label>
      <label>
        <span>Note (optional)</span>
        <textarea
          bind:value={note}
          rows="2"
          placeholder="For example: the value is on the next line"></textarea>
      </label>
      <label class="tell">
        <input type="checkbox" bind:checked={tell} />
        Tell Star Ground, so the parser reads it right next time
      </label>
      {#if tell}
        <p class="small">
          Sends the document's link or file name, this part, where it was read and your correction.
          Never the document itself.
        </p>
      {/if}
      {#if problem}
        <p class="note problem" role="alert">{problem}</p>
      {/if}
      <div class="buttons">
        <button class="hs-button hs-button--primary" type="submit" disabled={saving}>
          {saving ? 'Saving…' : 'Save'}
        </button>
        <button class="text-button" type="button" onclick={() => (editing = false)}>Cancel</button>
      </div>
    </form>
  {/if}
</li>

<style>
  .part {
    display: grid;
    gap: 6px;
    padding: 10px 12px;
    border: 1px solid var(--line);
    border-radius: 5px;
    background: var(--panel);
  }
  .part.flagged {
    border-style: dashed;
    border-color: var(--muted);
  }
  .part.selected {
    border: 1px solid var(--accent);
    background: var(--accent-soft);
  }
  .row {
    display: flex;
    align-items: center;
    gap: 12px;
  }
  .show {
    display: flex;
    flex: 1;
    flex-wrap: wrap;
    gap: 4px 14px;
    align-items: baseline;
    min-width: 0;
    padding: 0;
    border: 0;
    color: var(--text);
    background: none;
    font: inherit;
    text-align: left;
    cursor: pointer;
  }
  .ref {
    min-width: 5ch;
    font-family: var(--mono);
    font-size: var(--fs-ui);
  }
  .value {
    font-family: var(--mono);
    font-size: var(--fs-ui);
    overflow-wrap: anywhere;
  }
  .where {
    color: var(--muted);
    font-size: var(--fs-ui);
  }
  .marks {
    display: flex;
    gap: 6px;
  }
  .action {
    padding: 5px 9px;
    border: 1px solid var(--line);
    border-radius: 3px;
    color: var(--muted);
    background: var(--bg);
    font: inherit;
    font-size: var(--fs-ui);
    white-space: nowrap;
    cursor: pointer;
  }
  .action:hover,
  .action[aria-pressed='true'],
  .action[aria-expanded='true'] {
    border-color: var(--accent-border);
    color: var(--accent);
  }
  .note,
  .small {
    margin: 0;
    color: var(--muted);
    font-size: var(--fs-ui);
    line-height: 1.6;
  }
  .problem {
    color: var(--text);
  }
  .text-button {
    padding: 0;
    border: 0;
    color: var(--muted);
    background: none;
    font: inherit;
    text-decoration: underline;
    cursor: pointer;
  }
  .text-button:hover {
    color: var(--accent);
  }
  .edit {
    display: grid;
    gap: 10px;
    margin-top: 4px;
  }
  .edit label {
    display: grid;
    gap: 6px;
  }
  .edit label span {
    color: var(--muted);
    font-family: var(--mono);
    font-size: var(--fs-label);
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }
  .edit .tell {
    display: flex;
    gap: 8px;
    align-items: center;
    font-size: var(--fs-ui);
  }
  input:not([type='checkbox']),
  textarea {
    width: 100%;
    padding: 7px 10px;
    border: 1px solid var(--line);
    border-radius: 5px;
    color: var(--text);
    background: var(--bg);
    font-family: var(--mono);
    font-size: var(--fs-ui);
  }
  textarea {
    font-family: var(--sans);
    resize: vertical;
  }
  .buttons {
    display: flex;
    gap: 16px;
    align-items: center;
  }
  button:disabled {
    opacity: 0.6;
    cursor: progress;
  }
  button:focus-visible,
  input:focus-visible,
  textarea:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }
</style>
