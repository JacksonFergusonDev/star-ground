<script lang="ts">
  // What to do when a finished pedal doesn't work: what each symptom points
  // to, what to look at first, how to measure its voltages and trace its
  // signal, and how to ask for help. Plain text, for the Users audience.
  import Icon from './Icon.svelte';

  // Beginners' first stops, then the older forums for harder questions.
  const FIRST = [
    { name: 'r/diypedals on Reddit', href: 'https://www.reddit.com/r/diypedals/' },
    { name: 'The PedalPCB forum', href: 'https://forum.pedalpcb.com/' },
  ];
  const MORE = [
    { name: 'The madbean forum', href: 'https://www.madbeanpedals.com/forum/' },
    { name: 'The DIYstompboxes forum', href: 'https://www.diystompboxes.com/smfforum/' },
    { name: 'freestompboxes.org', href: 'https://www.freestompboxes.org/' },
  ];
</script>

<div class="trouble">
  <section class="part" aria-labelledby="first">
    <h2 id="first">Start here</h2>
    <p class="note">
      Most pedals that don't work have one of a few problems, and you can find most of them by
      looking. Unplug the power, and in good light check:
    </p>
    <ul class="checks">
      <li>
        <strong>Every solder joint</strong>
        <p>
          A good joint is shiny and shaped like a small cone around the lead. A dull ball sitting on
          the pad, a lead with no solder, or solder bridging two pads all stop a pedal working.
        </p>
      </li>
      <li>
        <strong>Every part that has a way round</strong>
        <p>
          An electrolytic capacitor's stripe marks its negative leg. A diode's band matches the band
          drawn on the board. A chip's notch or dot matches the board's drawing. A transistor's flat
          face matches its outline.
        </p>
      </li>
      <li>
        <strong>Every value</strong>
        <p>
          Read each resistor's bands and each capacitor's code against the build document. Two
          swapped values are easy to miss. The <a href="/app/identify/">part identifier</a> reads them
          for you.
        </p>
      </li>
      <li>
        <strong>The power supply</strong>
        <p>
          Most pedals take 9 V DC with the centre pin negative, the usual pedal supply. A supply
          with the centre positive, or a higher voltage than the document allows, can damage the
          circuit.
        </p>
      </li>
      <li>
        <strong>The off-board wiring</strong>
        <p>
          Compare each wire from the board to the jacks, the footswitch and the DC jack with the
          document's wiring drawing.
        </p>
      </li>
    </ul>
  </section>

  <section class="part" aria-labelledby="symptoms">
    <h2 id="symptoms">What the symptom tells you</h2>
    <dl class="symptoms">
      <div>
        <dt>No light, no sound</dt>
        <dd>
          The pedal has no power. With a battery, plug a cable into the input jack: many pedals
          switch the battery on that way. Check the DC jack's wiring, and the reverse-polarity diode
          beside it, whose band must match the board's drawing. Check the LED's way round too: its
          longer leg is positive.
        </dd>
      </div>
      <div>
        <dt>The light works, but there's no sound either way</dt>
        <dd>
          The bypassed signal goes only through the jacks and the footswitch, so the fault is there.
          Check that the guitar cable is in the input jack, which is usually on the right as you
          look down at the pedal, and the wiring from each jack's tip and sleeve.
        </dd>
      </div>
      <div>
        <dt>Bypass works, but the effect is silent or weak</dt>
        <dd>
          The jacks and switch are fine, so the fault is on the board or in the wires between the
          board and the footswitch. Measure the voltages below, then trace the signal with an audio
          probe.
        </dd>
      </div>
      <div>
        <dt>It works, but hums, squeals or crackles</dt>
        <dd>
          A hum is often a missing ground connection: check every ground wire, and that the jacks
          touch the enclosure if the document says they should. A squeal at high gain can be wires
          running close together; shorten them and keep input and output wires apart. A crackle when
          you move a knob or tap the board is usually a cracked joint: reflow it.
        </dd>
      </div>
      <div>
        <dt>The LED is on all the time, or never</dt>
        <dd>
          Compare the footswitch's wiring with the document. A 3PDT footswitch has nine lugs, and
          its drawing is easy to read upside down.
        </dd>
      </div>
    </dl>
  </section>

  <section class="part" aria-labelledby="voltages">
    <h2 id="voltages">Measure the voltages</h2>
    <p class="note">
      Set a multimeter to DC volts, on the 20 V range if it asks. Power the pedal. Hold the black
      probe on ground (the sleeve of the input jack, or any ground pad) and touch the red probe to
      each point. If the document has a table of voltages, compare yours with it.
    </p>
    <ul class="checks">
      <li>
        <strong>The supply</strong>
        <p>
          About 9 V at the DC jack. After the reverse-polarity diode it's a little less: about 0.3 V
          less for a 1N5817, 0.7 V for a 1N4001. Nothing here means a power or wiring fault.
        </p>
      </li>
      <li>
        <strong>A dual op-amp chip</strong>
        <p>
          TL072, JRC4558, LM833 and the like, with eight pins: pin 8 at the supply, pin 4 at 0 V,
          and pins 1, 2, 3, 5, 6 and 7 at about half the supply, around 4.5 V. A pin far from that
          points to the parts around it.
        </p>
      </li>
      <li>
        <strong>A transistor</strong>
        <p>
          With a silicon transistor conducting, its base sits about 0.6 V from its emitter, and a
          germanium one about 0.2 V. A collector at the full supply or at 0 V usually means a wrong
          part, a wrong way round, or a bad joint near it.
        </p>
      </li>
    </ul>
  </section>

  <section class="part" aria-labelledby="probe">
    <h2 id="probe">Trace the signal with an audio probe</h2>
    <p class="note">
      An audio probe lets you hear the signal at any point on the board. Make one from a guitar
      cable plugged into an amp: put a 100n film capacitor in series with the cable's tip, and clip
      its sleeve to the pedal's ground. Turn the amp down, play into the pedal, and touch the
      capacitor's free leg to points along the signal path, starting at the input. Where the sound
      stops or changes for the worse, the fault is between that point and the last good one.
    </p>
    <a
      class="text-link"
      href="https://www.youtube.com/watch?v=WbDSaliGZeo"
      target="_blank"
      rel="noopener noreferrer"
    >
      Watch how to make and use an audio probe <Icon name="arrow-up-right" size={14} />
    </a>
  </section>

  <section class="part" aria-labelledby="help">
    <h2 id="help">Ask for help</h2>
    <p class="note">
      Pedal builders answer debugging questions every day, and these two places welcome beginners.
    </p>
    <ul class="links">
      {#each FIRST as forum (forum.href)}
        <li>
          <a class="text-link" href={forum.href} target="_blank" rel="noopener noreferrer">
            {forum.name}
            <Icon name="arrow-up-right" size={14} />
          </a>
        </li>
      {/each}
    </ul>
    <p class="note">You'll get an answer faster with:</p>
    <ul class="tips">
      <li>Which pedal and which build document, with a link.</li>
      <li>What happens, in the words of the symptoms above.</li>
      <li>Sharp, well-lit photos of both sides of the board and of the wiring.</li>
      <li>The voltages you measured, especially on the chips and transistors.</li>
    </ul>
    <p class="note">
      For deeper questions about a circuit, such as modifying it or designing your own, these
      longer-running forums have years of threads to search:
    </p>
    <ul class="links">
      {#each MORE as forum (forum.href)}
        <li>
          <a class="text-link" href={forum.href} target="_blank" rel="noopener noreferrer">
            {forum.name}
            <Icon name="arrow-up-right" size={14} />
          </a>
        </li>
      {/each}
    </ul>
  </section>
</div>

<style>
  .trouble {
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
  .tips {
    display: grid;
    gap: 8px;
    margin: 0;
    padding-left: 18px;
    color: var(--muted);
    font-size: var(--fs-ui);
    line-height: 1.6;
  }
  .checks {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(min(100%, 240px), 1fr));
    gap: 12px;
    margin: 0;
    padding: 0;
    list-style: none;
  }
  .checks p {
    margin: 0;
    color: var(--muted);
    font-size: var(--fs-ui);
    line-height: 1.6;
  }
  .checks a {
    text-decoration: underline;
    text-underline-offset: 3px;
  }
  .checks strong {
    font-size: var(--fs-ui);
  }
  .links {
    display: grid;
    gap: 8px;
    margin: 0;
    padding: 0;
    list-style: none;
  }
  .checks strong,
  dt {
    color: var(--text);
    font-weight: 500;
  }
  .symptoms {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(min(100%, 240px), 1fr));
    gap: 12px;
    margin: 0;
    font-size: var(--fs-ui);
    line-height: 1.6;
  }
  .checks li,
  .symptoms div {
    display: grid;
    gap: 6px;
    align-content: start;
    padding: 14px 16px;
    border: 1px solid var(--line);
    border-radius: 5px;
  }
  dd {
    margin: 0;
    color: var(--muted);
  }
  .text-link {
    display: inline-flex;
    gap: 6px;
    align-items: center;
    justify-self: start;
    font-size: var(--fs-ui);
  }
</style>
