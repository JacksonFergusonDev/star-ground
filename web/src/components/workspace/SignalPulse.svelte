<script lang="ts">
  // Waiting, drawn as the hero draws a played note: a pulse running along a
  // trace from the input pad to the output pad. Reading a document by OCR can
  // take most of a minute, so the wait gets a drawing of its own. Motion that
  // runs on its own needs a pause control past a few seconds (house-style's
  // motion rules), so the pulse runs three times and rests, and runs again
  // each time the words change (queued, then reading). Readers who asked for
  // reduced motion see the trace still; the words say what's happening. It
  // isn't a live region itself: where a wait should be announced, the page
  // puts it in one.
  let { label }: { label: string } = $props();
</script>

<span class="pulse">
  {#key label}
    <svg viewBox="0 0 64 12" width="64" height="12" aria-hidden="true" focusable="false">
      <path class="copper" d="M4 6h14l6-4h16l6 4h14" />
      <path class="signal" d="M4 6h14l6-4h16l6 4h14" pathLength="100" />
      <circle class="pad" cx="4" cy="6" r="2.6" />
      <circle class="pad" cx="60" cy="6" r="2.6" />
    </svg>
  {/key}
  <span>{label}</span>
</span>

<style>
  .pulse {
    display: inline-flex;
    gap: 10px;
    align-items: center;
    color: var(--muted);
    font-size: var(--fs-ui);
  }
  svg {
    flex-shrink: 0;
    fill: none;
    stroke-linecap: round;
    stroke-linejoin: round;
  }
  .copper {
    stroke: var(--line);
    stroke-width: 2;
  }
  .signal {
    stroke: var(--accent);
    stroke-dasharray: 14 86;
    stroke-dashoffset: 14;
    stroke-width: 2;
    animation: run 1.4s linear 3;
  }
  .pad {
    fill: var(--bg);
    stroke: var(--muted);
    stroke-width: 1.3;
  }
  @keyframes run {
    to {
      stroke-dashoffset: -86;
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .signal {
      animation: none;
      stroke-dasharray: none;
      opacity: 0.35;
    }
  }
</style>
