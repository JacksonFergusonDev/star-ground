<script lang="ts">
  // A bug in Star Ground, shown where it happened: the builder can send a
  // report in one click (no account needed), or open a GitHub issue. The
  // technical detail goes in the report, never on screen.
  import type { BugReport } from '../../lib/api';
  import { reportsUnavailable, sendReport } from '../../lib/server';
  import { links } from '../../lib/site';
  import Icon from './Icon.svelte';

  let { bug, document }: { bug: BugReport; document: string } = $props();

  let comment = $state('');
  let status = $state<'idle' | 'sending' | 'sent' | 'unavailable' | 'failed'>('idle');
  let problem = $state('');

  // GitHub caps a URL's length; the report carries the full detail.
  const MAX_DETAIL = 5000;

  const issue = $derived.by(() => {
    const title = `Bug ${bug.code} reading ${document}`;
    const body = [
      `Star Ground hit a bug while reading **${document}**.`,
      '',
      '**What I was doing**',
      comment.trim() || '_Describe what you did, if you can._',
      '',
      '<details><summary>Technical details</summary>',
      '',
      '```',
      bug.detail.slice(0, MAX_DETAIL),
      '```',
      '</details>',
    ].join('\n');
    const query = new URLSearchParams({ title, body });
    return `${links.github}/issues/new?${query}`;
  });

  async function send(): Promise<void> {
    status = 'sending';
    try {
      await sendReport({ code: bug.code, document, detail: bug.detail, comment: comment.trim() });
      status = 'sent';
    } catch (e) {
      if (reportsUnavailable(e)) {
        status = 'unavailable';
      } else {
        status = 'failed';
        problem = (e as Error).message;
      }
    }
  }
</script>

<div class="bug">
  {#if status === 'sent'}
    <p role="status">Report sent. Thank you: it helps get this fixed.</p>
  {:else}
    <label for={`comment-${bug.code}`}>What were you doing? (optional)</label>
    <textarea
      id={`comment-${bug.code}`}
      bind:value={comment}
      rows="2"
      placeholder="For example: added three documents at once"></textarea>
    <div class="actions">
      {#if status !== 'unavailable'}
        <button
          class="hs-button hs-button--primary"
          type="button"
          disabled={status === 'sending'}
          onclick={send}
        >
          {status === 'sending' ? 'Sending…' : 'Send report'}
        </button>
      {/if}
      <a class="issue" href={issue} target="_blank" rel="noopener noreferrer">
        Open a GitHub issue instead <Icon name="arrow-up-right" size={14} />
      </a>
    </div>
    {#if status === 'unavailable'}
      <p role="status">
        This server can't take reports. Opening a GitHub issue sends the same details.
      </p>
    {:else if status === 'failed'}
      <p role="alert">The report didn't send: {problem}</p>
    {/if}
    <p class="small">
      The report holds what went wrong, the document's name, and your note, never the document
      itself. Code {bug.code}.
    </p>
  {/if}
</div>

<style>
  .bug {
    display: grid;
    gap: 10px;
    padding: 12px 14px;
    border: 1px solid var(--line);
    border-radius: 5px;
    background: var(--panel);
  }
  label {
    color: var(--muted);
    font-family: var(--mono);
    font-size: var(--fs-label);
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }
  textarea {
    width: 100%;
    padding: 8px 10px;
    border: 1px solid var(--line);
    border-radius: 5px;
    color: var(--text);
    background: var(--bg);
    font: inherit;
    font-size: var(--fs-ui);
    resize: vertical;
  }
  .actions {
    display: flex;
    flex-wrap: wrap;
    gap: 10px 18px;
    align-items: center;
  }
  .issue {
    display: inline-flex;
    gap: 6px;
    align-items: center;
    color: var(--text);
    font-size: var(--fs-ui);
  }
  .issue:hover {
    color: var(--accent);
  }
  p {
    margin: 0;
    color: var(--text);
    font-size: var(--fs-ui);
    line-height: 1.6;
  }
  .small {
    color: var(--muted);
  }
  button:disabled {
    opacity: 0.6;
    cursor: progress;
  }
  textarea:focus-visible,
  button:focus-visible,
  a:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }
</style>
