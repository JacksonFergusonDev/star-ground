<script lang="ts">
  // The tools for a first build, recommended from the maintainer's own bench:
  // the least that will do, then what makes it easier. Each shows what was paid
  // on AliExpress in 2025 and links to an AliExpress search for it. Written in
  // the first person on purpose: it's one builder's advice, not a standard.
  // Plain text, for the Users audience.
  import { views } from '../../lib/router.svelte';
  import Icon from './Icon.svelte';
  import ToolDrawing, { type ToolKind } from './ToolDrawing.svelte';

  type Tool = {
    kind: ToolKind;
    name: string;
    /** What I paid, in US dollars; none for a tool I didn't price. */
    cost?: number;
    /** What the cost buys, when it's more than one: 'for two'. */
    per?: string;
    /** The one I bought, when the model matters. */
    pick?: string;
    why: string;
    /** Words for an AliExpress search. */
    search: string;
  };

  const IRON: Tool = {
    kind: 'iron',
    name: 'A soldering station',
    cost: 92,
    pick: 'GEEBOON TC22',
    why: 'Get one with a temperature control. I set mine to about 330 °C for leaded solder, or 360 °C for lead-free; an iron without a control runs too hot or too cold for small joints.',
    search: 'geeboon tc22',
  };

  const MINIMUM: Tool[] = [
    {
      kind: 'solder',
      name: 'Solder',
      why: 'Thin solder with a flux core, about 0.8 mm across. Leaded solder (63/37 or 60/40) melts lower and flows more easily, which helps on a first build. Wash your hands after using it.',
      search: '63 37 solder 0.8mm',
    },
    {
      kind: 'multimeter',
      name: 'A multimeter',
      cost: 38,
      pick: 'AN8009',
      why: "For checking resistor values, finding broken connections, and measuring voltages when a pedal doesn't work.",
      search: 'an8009 multimeter',
    },
    {
      kind: 'cutters',
      name: 'Flush cutters',
      cost: 2.87,
      per: 'for two',
      pick: 'PLATO 170',
      why: 'For trimming each lead close to the board after you solder it.',
      search: 'plato 170 cutters',
    },
    {
      kind: 'stripper',
      name: 'A wire stripper',
      cost: 15,
      why: 'For baring the ends of the wires between the board, the jacks and the footswitch. Mine is an automatic one, which grips and strips in one squeeze.',
      search: 'automatic wire stripper',
    },
    {
      kind: 'braid',
      name: 'Desoldering braid',
      cost: 3,
      why: 'Copper braid that soaks up solder, for fixing a joint or taking out a part you put in the wrong place.',
      search: 'desoldering braid 2.5mm',
    },
  ];

  const RECOMMENDED: Tool[] = [
    {
      kind: 'holder',
      name: 'A board holder',
      cost: 24.89,
      why: 'Holds the board steady and turns it over, so both hands are free for the iron and the solder.',
      search: 'pcb holder soldering',
    },
    {
      kind: 'flux',
      name: 'A flux pen',
      cost: 6,
      why: 'Extra flux helps solder flow onto a pad, and makes reworking an old joint much easier.',
      search: 'rosin flux pen',
    },
    {
      kind: 'sucker',
      name: 'A solder sucker',
      cost: 3.29,
      why: 'A spring-loaded pump that clears the solder from a hole faster than braid alone.',
      search: 'desoldering pump',
    },
    {
      kind: 'tweezers',
      name: 'Tweezers',
      cost: 4,
      per: 'for six',
      why: 'For holding small parts and bending leads. Mine are anti-static, which makes them safe near chips.',
      search: 'esd tweezers set',
    },
    {
      kind: 'fuses',
      name: 'Spare multimeter fuses',
      cost: 4,
      per: 'for ten',
      why: "Measuring current with the probe in the wrong socket blows one, and the meter stops reading until it's replaced. Mine takes 10 A glass fuses, 3.6 × 10 mm; check your meter's manual for its size.",
      search: 'multimeter fuse 10a 3.6x10mm',
    },
  ];

  const aliexpress = (search: string) =>
    `https://www.aliexpress.com/w/wholesale-${search.replaceAll(' ', '-')}.html`;

  const dollars = (cost: number) => `$${Math.round(cost)}`;

  const total = (tools: Tool[]) => dollars(tools.reduce((sum, tool) => sum + (tool.cost ?? 0), 0));

  const GUIDE =
    'https://www.reddit.com/r/soldering/comments/1n1f2hi/soldering_station_buying_mega_guide/';
</script>

{#snippet price(tool: Tool)}
  {#if tool.cost !== undefined}
    <span class="price">{dollars(tool.cost)}{tool.per ? ` ${tool.per}` : ''}</span>
  {/if}
{/snippet}

{#snippet find(tool: Tool, label = 'Find it on AliExpress')}
  <a class="text-link" href={aliexpress(tool.search)} target="_blank" rel="noopener noreferrer">
    {label}
    <Icon name="arrow-up-right" size={14} />
  </a>
{/snippet}

{#snippet card(tool: Tool)}
  <li class="tool">
    <div class="mat"><ToolDrawing kind={tool.kind} /></div>
    <div class="body">
      <div class="title">
        <h3>{tool.name}</h3>
        {@render price(tool)}
      </div>
      {#if tool.pick}<p class="pick">My pick: {tool.pick}</p>{/if}
      <p>{tool.why}</p>
      {@render find(tool)}
    </div>
  </li>
{/snippet}

<div class="kit">
  <section class="intro" aria-label="About these picks">
    <p>
      These are the tools on my own bench, with what I paid for each in 2025. It's what has worked
      for me, not the only way to set up.
    </p>
    <p>
      <strong>Where I buy them.</strong> I bought nearly everything here on AliExpress, because it was
      the cheapest for me, and I'd suggest the same to anyone starting out. A local electronics shop charges
      more, but its tools are more consistent in quality. Each tool links to an AliExpress search, so
      you can compare sellers and prices.
    </p>
  </section>

  <section class="list" aria-labelledby="minimum">
    <div class="head">
      <h2 id="minimum">The least you need</h2>
      <span class="sum">About {total([IRON, ...MINIMUM])} with my picks</span>
    </div>
    <p class="note">You can build a pedal with these and nothing else.</p>
    <ul class="tools">
      <li class="tool featured">
        <div class="mat"><ToolDrawing kind={IRON.kind} /></div>
        <div class="body">
          <div class="title">
            <h3>{IRON.name}</h3>
            {@render price(IRON)}
          </div>
          <p class="pick">My pick: {IRON.pick}</p>
          <p>
            I use the GEEBOON TC22 and highly recommend it. It sits between the cheapest stations
            and the expensive ones, and it's very capable for its price. A basic adjustable iron
            costs less and will still build a pedal.
          </p>
          <p>{IRON.why}</p>
          <p>The best buying guide I've found is a thread on r/soldering.</p>
          <div class="links">
            {@render find(IRON, 'Find the TC22 on AliExpress')}
            <a class="text-link" href={GUIDE} target="_blank" rel="noopener noreferrer">
              Read the soldering station buying guide
              <Icon name="arrow-up-right" size={14} />
            </a>
          </div>
        </div>
      </li>
      {#each MINIMUM as tool (tool.kind)}
        {@render card(tool)}
      {/each}
    </ul>
  </section>

  <section class="list" aria-labelledby="recommended">
    <div class="head">
      <h2 id="recommended">What makes it easier</h2>
      <span class="sum">About {total(RECOMMENDED)} more</span>
    </div>
    <p class="note">None of these are needed, but each one has saved me time or a mistake.</p>
    <ul class="tools">
      {#each RECOMMENDED as tool (tool.kind)}
        {@render card(tool)}
      {/each}
    </ul>
  </section>

  <section class="rest" aria-labelledby="rest">
    <h2 id="rest">The parts and the box</h2>
    <p>
      The <a href={views.list.path}>shopping list</a>'s hardware kit covers what goes in each pedal
      besides the circuit board: the footswitch, the jacks, the DC jack, the LED, hook-up wire and
      heat shrink.
    </p>
    <p>
      The metal enclosure needs holes for the jacks, the footswitch, the knobs and the LED. Drilling
      them takes a drill and a step bit. For a first build, many shops sell enclosures already
      drilled for a given circuit board, which saves buying both.
    </p>
  </section>
</div>

<style>
  .kit {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    gap: 36px;
    max-width: 960px;
  }
  .intro,
  .rest {
    display: grid;
    gap: 12px;
    padding: 18px 20px;
    border: 1px solid var(--line);
    border-radius: 5px;
    background: var(--panel);
  }
  .intro p,
  .rest p,
  .note {
    max-width: 68ch;
    margin: 0;
    color: var(--muted);
    font-size: var(--fs-ui);
    line-height: 1.6;
  }
  .intro strong {
    color: var(--text);
    font-weight: 500;
  }
  .rest a {
    text-decoration: underline;
    text-underline-offset: 3px;
  }
  .list {
    display: grid;
    gap: 12px;
    min-width: 0;
  }
  .head {
    display: flex;
    flex-wrap: wrap;
    gap: 4px 16px;
    align-items: baseline;
    justify-content: space-between;
  }
  h2 {
    max-width: none;
    margin: 0;
    font-size: var(--fs-h4);
    font-weight: 500;
    letter-spacing: 0;
  }
  .sum,
  .price,
  .pick {
    font-family: var(--mono);
    font-size: var(--fs-label);
    letter-spacing: 0.04em;
  }
  .sum {
    color: var(--text);
  }
  .tools {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(min(100%, 260px), 1fr));
    gap: 14px;
    margin: 4px 0 0;
    padding: 0;
    list-style: none;
    container-type: inline-size;
  }
  .tool {
    display: grid;
    grid-template-rows: auto 1fr;
    overflow: hidden;
    border: 1px solid var(--line);
    border-radius: 5px;
    background: var(--panel);
  }
  /* A cutting mat under each drawing: faint squares, like the one on a bench. */
  .mat {
    display: grid;
    place-items: center;
    padding: 18px 28px;
    border-bottom: 1px solid var(--line);
    background-color: var(--bg);
    background-image:
      linear-gradient(color-mix(in srgb, var(--line) 55%, transparent) 1px, transparent 1px),
      linear-gradient(90deg, color-mix(in srgb, var(--line) 55%, transparent) 1px, transparent 1px);
    background-position: -1px -1px;
    background-size: 16px 16px;
  }
  .mat :global(svg) {
    max-width: 220px;
  }
  .body {
    display: grid;
    gap: 8px;
    align-content: start;
    padding: 14px 16px 16px;
  }
  .body .text-link {
    align-self: end;
    margin-top: 4px;
  }
  .title {
    display: flex;
    flex-wrap: wrap;
    gap: 4px 12px;
    align-items: baseline;
    justify-content: space-between;
  }
  h3 {
    margin: 0;
    color: var(--text);
    font-size: var(--fs-ui);
    font-weight: 500;
    letter-spacing: 0;
  }
  .price {
    color: var(--text);
  }
  .pick {
    margin: 0;
    color: var(--text);
  }
  .body p:not(.pick) {
    margin: 0;
    color: var(--muted);
    font-size: var(--fs-ui);
    line-height: 1.6;
  }
  .links {
    display: flex;
    flex-wrap: wrap;
    gap: 8px 24px;
    margin-top: 4px;
  }
  .featured {
    grid-column: 1 / -1;
  }
  @container (min-width: 620px) {
    .featured {
      grid-template-rows: none;
      grid-template-columns: minmax(0, 2fr) minmax(0, 3fr);
    }
    .featured .mat {
      border-right: 1px solid var(--line);
      border-bottom: 0;
    }
    .featured .mat :global(svg) {
      max-width: 280px;
    }
  }
</style>
