<script lang="ts">
  // Identifying a part in the hand: the resistor colour code, the codes on
  // capacitors and pots, and where the other parts print what they are. Each
  // calculator can add what it read to the stock.
  import type { ComponentCategory } from '../../lib/api';
  import type { BandCount } from '../../lib/parts';
  import { CAP_TOLERANCES, VOLTAGE_CODES } from '../../lib/identify';
  import CapacitorCode from './CapacitorCode.svelte';
  import ResistorCalculator from './ResistorCalculator.svelte';

  let {
    bands = $bindable(),
    onAdd,
  }: {
    bands: BandCount;
    onAdd: (category: ComponentCategory, value: string, qty: number) => void;
  } = $props();

  let added = $state<string | null>(null);
  function add(category: ComponentCategory, value: string, qty: number): void {
    onAdd(category, value, qty);
    added = `Added ${qty} × ${value} to your stock.`;
  }
</script>

<div class="identify">
  {#if added}<p class="status" role="status">{added}</p>{/if}

  <section class="part" aria-labelledby="resistors">
    <h2 id="resistors">Resistors</h2>
    <p class="note">
      Pick each band's colour, left to right. Each cell shows what the colour means in that band.
    </p>
    <ResistorCalculator
      name="identify"
      bind:count={bands}
      onAdd={(v, n) => add('Resistors', v, n)}
    />
  </section>

  <section class="part" aria-labelledby="capacitors">
    <h2 id="capacitors">Capacitors</h2>
    <CapacitorCode id="identify-cap" onAdd={(v, n) => add('Capacitors', v, n)} />
    <ul class="facts">
      <li>
        <strong>Three digits</strong> are the first two digits of the value in picofarads, then how many
        zeros follow: 104 is 10 and four zeros, 100,000 pF, which is 100 nF.
      </li>
      <li>
        <strong>A letter in place of the decimal point</strong> gives the unit: 4n7 is 4.7 nF, u1 is 0.1
        µF, 1n0 is 1 nF.
      </li>
      <li>
        <strong>A decimal with no unit</strong>, such as .1 or .047 on a film box, is in
        microfarads.
      </li>
      <li>
        <strong>A letter after the code</strong> is the tolerance:
        {Object.entries(CAP_TOLERANCES)
          .filter(([letter]) => 'JKM'.includes(letter))
          .map(([letter, t]) => `${letter} ${t}`)
          .join(', ')}.
      </li>
      <li>
        <strong>A digit and letter before it</strong>, as in 2A104J, is the voltage rating: 1H is
        {VOLTAGE_CODES['1H']} V, 2A is {VOLTAGE_CODES['2A']} V.
      </li>
      <li>
        <strong>Electrolytics</strong> print the value and voltage plainly (100µF 25V). The stripe of
        minus signs marks the negative leg, and the longer leg is positive. Use one rated at least as
        high as the build document asks.
      </li>
    </ul>
  </section>

  <section class="part" aria-labelledby="pots">
    <h2 id="pots">Pots</h2>
    <ul class="facts">
      <li>
        <strong>The code</strong> is on the back or the side. The letter is the taper and the rest the
        value: B100K is a 100k linear pot.
      </li>
      <li>
        <strong>Three digits</strong> work as on a capacitor, in ohms: B104 is 10 and four zeros, so also
        a 100k linear pot. A504 is a 500k audio pot.
      </li>
      <li>
        <strong>A is audio taper</strong> (logarithmic), used for volume.
        <strong>B is linear</strong>, used for most other controls. C is reverse audio.
      </li>
    </ul>
  </section>

  <section class="part" aria-labelledby="semis">
    <h2 id="semis">Diodes, LEDs, transistors and chips</h2>
    <ul class="facts">
      <li>
        <strong>Diodes</strong> print their number on the body, often without the 1N: a small glass diode
        reading 4148 is a 1N4148. The stripe marks the cathode, and goes on the board's band.
      </li>
      <li>
        <strong>LEDs</strong> have a longer leg on the positive side (anode), and a flat edge on the rim
        on the negative side.
      </li>
      <li>
        <strong>Transistors</strong> print their number on the flat face. Parts with different numbers
        can have their legs in a different order, so follow the board's outline for each.
      </li>
      <li>
        <strong>Chips</strong> print their number on top, with the maker's letters around it: TL072CP
        and TL072IP are both a TL072, and JRC4558D is a 4558. A notch or dot marks pin 1.
      </li>
    </ul>
  </section>
</div>

<style>
  .identify {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    gap: 20px;
    max-width: 900px;
  }
  .status {
    margin: 0;
    color: var(--accent);
    font-size: var(--fs-ui);
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
  .facts {
    display: grid;
    gap: 8px;
    margin: 0;
    padding-left: 18px;
    color: var(--muted);
    font-size: var(--fs-ui);
    line-height: 1.6;
  }
  .facts strong {
    color: var(--text);
    font-weight: 500;
  }
</style>
