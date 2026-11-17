<script lang="ts">
  // The tools and supplies for a first build: the least that will do, what
  // makes it easier, and what every build uses up. Prices are what one builder
  // paid on AliExpress in 2025, so a beginner can see roughly what each costs.
  // Plain text, for the Users audience.
  import { views } from '../../lib/router.svelte';

  type Tool = { name: string; price?: string; why: string };

  const MINIMUM: Tool[] = [
    {
      name: 'A soldering iron with a temperature control',
      price: 'about $92 for a 220 W station; a basic adjustable iron costs less',
      why: 'Set it to about 330 °C for leaded solder, or 360 °C for lead-free. An iron without a temperature control runs too hot or too cold for small joints.',
    },
    {
      name: 'Solder',
      why: 'Thin solder with a flux core, about 0.8 mm across. Leaded solder (63/37 or 60/40) melts lower and flows more easily, which helps on a first build; wash your hands after using it.',
    },
    {
      name: 'A multimeter',
      price: 'about $38',
      why: "For checking resistor values, finding broken connections, and measuring voltages if the pedal doesn't work.",
    },
    {
      name: 'Flush cutters',
      price: 'about $3 for two',
      why: 'For trimming each lead close to the board after you solder it.',
    },
    {
      name: 'A wire stripper',
      price: 'about $15 for an automatic one',
      why: 'For baring the ends of the wires between the board, the jacks and the footswitch.',
    },
    {
      name: 'Desoldering braid',
      price: 'about $3',
      why: 'Copper braid that soaks up solder, for fixing a joint or taking out a part you put in the wrong place.',
    },
  ];

  const RECOMMENDED: Tool[] = [
    {
      name: 'A board holder',
      price: 'about $25',
      why: 'Holds the board steady and turns it over, so both hands are free for the iron and the solder.',
    },
    {
      name: 'A flux pen',
      price: 'about $6',
      why: 'Extra flux helps solder flow onto a pad, and makes reworking an old joint much easier.',
    },
    {
      name: 'A solder sucker',
      price: 'about $3',
      why: 'A spring-loaded pump that clears the solder from a hole faster than braid alone.',
    },
    {
      name: 'Tweezers',
      price: 'about $4 for a set',
      why: 'For holding small parts and bending leads. Anti-static ones are safe near chips.',
    },
    {
      name: 'Spare multimeter fuses',
      price: 'about $4 for ten',
      why: "Only if your meter takes them. Measuring current in the wrong socket blows one, and the meter stops reading until it's replaced.",
    },
  ];

  const SUPPLIES: Tool[] = [
    {
      name: 'Hook-up wire',
      why: 'Stranded 24 AWG wire, in a few colours so the power, ground and signal wires are easy to tell apart.',
    },
    {
      name: 'Heat shrink tubing',
      why: 'About 2.5 mm across, to cover bare joints on the jacks and the footswitch.',
    },
  ];
</script>

{#snippet tools(list: Tool[])}
  <ul class="tools">
    {#each list as tool (tool.name)}
      <li>
        <strong>{tool.name}</strong>
        {#if tool.price}<span class="price">{tool.price}</span>{/if}
        <p>{tool.why}</p>
      </li>
    {/each}
  </ul>
{/snippet}

<div class="kit">
  <section class="part" aria-labelledby="minimum">
    <h2 id="minimum">The least you need</h2>
    <p class="note">
      You can build a pedal with these and nothing else. Prices are what one builder paid online in
      2025; yours will differ.
    </p>
    {@render tools(MINIMUM)}
  </section>

  <section class="part" aria-labelledby="recommended">
    <h2 id="recommended">What makes it easier</h2>
    <p class="note">None of these are needed, but each saves time or a mistake.</p>
    {@render tools(RECOMMENDED)}
  </section>

  <section class="part" aria-labelledby="supplies">
    <h2 id="supplies">What every build uses</h2>
    <p class="note">
      The <a href={views.list.path}>shopping list</a> already includes each pedal's footswitch, jacks,
      DC jack and LED. These go between them:
    </p>
    {@render tools(SUPPLIES)}
  </section>

  <section class="part" aria-labelledby="enclosure">
    <h2 id="enclosure">The enclosure</h2>
    <p class="note">
      A pedal's metal box needs holes for its jacks, footswitch, knobs and LED. Drilling them takes
      a drill and a step bit. For a first build, many shops sell enclosures already drilled for a
      given circuit board, which saves buying both.
    </p>
  </section>
</div>

<style>
  .kit {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    gap: 20px;
    max-width: 900px;
  }
  .part {
    display: grid;
    gap: 14px;
    min-width: 0;
    padding: 18px 20px;
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
  .note {
    margin: 0;
    color: var(--muted);
    font-size: var(--fs-ui);
    line-height: 1.6;
  }
  .note a {
    text-decoration: underline;
    text-underline-offset: 3px;
  }
  .tools {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(min(100%, 240px), 1fr));
    gap: 12px;
    margin: 0;
    padding: 0;
    list-style: none;
  }
  .tools li {
    display: grid;
    gap: 6px;
    align-content: start;
    padding: 14px 16px;
    border: 1px solid var(--line);
    border-radius: 5px;
  }
  .tools strong {
    color: var(--text);
    font-size: var(--fs-ui);
    font-weight: 500;
  }
  .price {
    color: var(--muted);
    font-size: var(--fs-label);
  }
  .tools p {
    margin: 0;
    color: var(--muted);
    font-size: var(--fs-ui);
    line-height: 1.6;
  }
</style>
