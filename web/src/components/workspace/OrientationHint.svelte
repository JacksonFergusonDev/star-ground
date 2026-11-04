<script lang="ts">
  // How a polarized part goes in, drawn the way it looks from above: the
  // diode's stripe, the electrolytic's long leg and stripe, a chip's notch.
  // Putting one in backwards is the most common soldering mistake.
  import type { PartKind } from '../../lib/parts';

  let { kind }: { kind: PartKind } = $props();

  const HINTS: Partial<Record<PartKind, string>> = {
    diode: "Stripe matches the board's band",
    electrolytic: 'Long leg in the + hole',
    led: 'Long leg in the + hole',
    socket: "Notch matches the board's notch",
    chip: "Notch matches the board's notch",
  };
</script>

{#if HINTS[kind]}
  <span class="hint">
    <svg viewBox="0 0 28 14" width="28" height="14" aria-hidden="true" focusable="false">
      {#if kind === 'diode'}
        <path class="lead" d="M0 7h6M22 7h6" />
        <rect x="6" y="3.5" width="16" height="7" rx="2" />
        <rect class="mark" x="17" y="3.5" width="2.5" height="7" />
      {:else if kind === 'electrolytic'}
        <path class="lead" d="M10 12v2M18 12v1" />
        <rect x="6" y="1" width="16" height="11" rx="2" />
        <rect class="mark" x="17" y="1" width="3" height="11" />
        <path class="lead" d="M8.5 6.5h3M10 5v3" />
      {:else if kind === 'led'}
        <path class="lead" d="M11 11v3M17 11v2" />
        <path d="M8 11V6a6 6 0 0 1 12 0v5z" />
        <path class="lead" d="M3.5 6.5h3M5 5v3" />
      {:else}
        <rect x="3" y="2" width="22" height="10" rx="1" />
        <path class="mark-line" d="M3 4.8a2.2 2.2 0 0 1 0 4.4" />
      {/if}
    </svg>
    {HINTS[kind]}
  </span>
{/if}

<style>
  .hint {
    display: inline-flex;
    gap: 8px;
    align-items: center;
    color: var(--muted);
    font-size: var(--fs-label);
  }
  svg {
    flex-shrink: 0;
    fill: none;
    stroke: currentcolor;
    stroke-linecap: round;
    stroke-linejoin: round;
    stroke-width: 1.2;
  }
  .mark {
    fill: currentcolor;
    stroke: none;
  }
  .mark-line {
    stroke: var(--accent);
    stroke-width: 1.6;
  }
  .lead {
    stroke-width: 1;
  }
</style>
