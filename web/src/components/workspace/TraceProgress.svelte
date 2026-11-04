<script lang="ts">
  // Bench progress as copper being laid: the trace runs as far as the parts
  // placed, a pad marks the end of each stage and fills once its parts are
  // in, and the trace ends at a star ground, the point every ground return on
  // a pedal's board meets, which lights when the last part goes in. The
  // count beside it says the same in words.
  let {
    stages,
    done,
    total,
  }: {
    /** Each stage's name, part count and parts placed, in build order. */
    stages: { name: string; total: number; done: number }[];
    done: number;
    total: number;
  } = $props();

  const share = (n: number): number => (total ? (n / total) * 100 : 0);
  // Pads closer than this (in percent of the trace) to the last one drawn, or
  // to the star, would overlap, so a run of small stages shares one pad.
  const GAP = 4;
  // Where each stage ends along the trace; the last ends at the star. A pad
  // shared by small stages fills once all of them are done.
  const ends = $derived.by(() => {
    const drawn: { name: string; at: number; filled: boolean }[] = [];
    let before = 0;
    let filled = true;
    for (const stage of stages.slice(0, -1)) {
      before += stage.total;
      filled &&= stage.done === stage.total;
      const at = share(before);
      const last = drawn.at(-1)?.at ?? 0;
      if (at - last < GAP || 100 - at < GAP) continue;
      drawn.push({ name: stage.name, at, filled });
      filled = true;
    }
    return drawn;
  });
  const finished = $derived(total > 0 && done === total);
</script>

<div class="trace" aria-hidden="true">
  <div class="copper"></div>
  <div class="laid" style:width={`${share(done)}%`}></div>
  {#each ends as stage (stage.name)}
    <span class="pad" class:filled={stage.filled} style:left={`${stage.at}%`} title={stage.name}
    ></span>
  {/each}
  <svg class="star" class:lit={finished} viewBox="0 0 20 20" width="20" height="20">
    <circle cx="10" cy="10" r="8.5" />
    <path d="M10 4v5M5.5 9h9M7.3 12h5.4M9 15h2" />
  </svg>
</div>

<style>
  .trace {
    position: relative;
    flex: 1 1 200px;
    max-width: 420px;
    height: 20px;
    margin-right: 10px;
  }
  .copper,
  .laid {
    position: absolute;
    top: 8.5px;
    left: 0;
    height: 3px;
    border-radius: 2px;
  }
  .copper {
    right: 0;
    background: var(--line);
  }
  .laid {
    max-width: 100%;
    background: var(--accent);
    transition: width 300ms ease-out;
  }
  .pad {
    position: absolute;
    top: 4px;
    width: 12px;
    height: 12px;
    margin-left: -6px;
    border: 2.5px solid var(--line);
    border-radius: 50%;
    background: var(--bg);
    transition:
      background-color 300ms,
      border-color 300ms;
  }
  .pad.filled {
    border-color: var(--accent);
    background: var(--accent);
  }
  .star {
    position: absolute;
    top: 0;
    right: -10px;
    fill: var(--bg);
    stroke: var(--line);
    stroke-linecap: round;
    stroke-width: 1.5;
    transition: stroke 300ms;
  }
  .star.lit {
    fill: var(--accent-soft);
    stroke: var(--accent);
  }
  @media (prefers-reduced-motion: reduce) {
    .laid,
    .pad,
    .star {
      transition: none;
    }
  }
</style>
