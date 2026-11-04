"""What kind of effect each build is, so the catalog can be filtered by it.

Each publisher files its products under its own categories ('od-distortion-
fuzz', 'amp-in-a-box', Tonepad's 'tremolo'), so they're mapped onto one short
list (`Effect`). A build's effects come from, in order:

1. `catalog/effects.json`, set by hand: for builds nothing else places, and
   to correct one placed wrong.
2. The words of its title ('Acid Rain Fuzz', 'NostalgiTone Compressor
   Single'): a name says what a pedal is more surely than a shop's tags,
   which file GuitarPCB's compressor under EQ and Tonepad's fuzzes under
   distortion.
3. The publisher's categories for the product (`Listing.categories`).

A build none of them place has no effects, and the catalog lists it as other.
"""

import json
import re
from enum import StrEnum
from pathlib import Path


class Effect(StrEnum):
    """The kinds of effect the catalog can be filtered by."""

    FUZZ = "fuzz"
    OVERDRIVE = "overdrive"
    DISTORTION = "distortion"
    BOOST = "boost"
    DYNAMICS = "dynamics"
    MODULATION = "modulation"
    DELAY = "delay"
    REVERB = "reverb"
    FILTER = "filter"
    AMP = "amp"
    UTILITY = "utility"


# Whole category slugs whose words would mislead: a parent category over
# three kinds says nothing about which, and an amp in a box is a drive.
_CATEGORIES: dict[str, Effect | None] = {
    "od-distortion-fuzz": None,
    "amp-in-a-box": Effect.OVERDRIVE,
    "amp-emulated-drives": Effect.OVERDRIVE,
}

# A word in a category or a title, and the effect it names.
_WORDS: tuple[tuple[re.Pattern[str], Effect], ...] = tuple(
    (re.compile(rf"(?:{pattern})", re.IGNORECASE), effect)
    for pattern, effect in (
        (r"fuzz|phuzz|muff|bender|tonebender|fuzzface", Effect.FUZZ),
        (r"overdrive|drive|superdrive|od|screamer|klon", Effect.OVERDRIVE),
        (r"distortion|dist|rat|ratt", Effect.DISTORTION),
        (r"boosts?|booster", Effect.BOOST),
        (
            r"compressor|compression|comp|squeezer|sustainer|dynamics|gate|limiter",
            Effect.DYNAMICS,
        ),
        (
            r"modulation|modulator|chorus|phaser|phase|flanger|tremolo|trem"
            r"|vibe|vibrato|rotary",
            Effect.MODULATION,
        ),
        (r"delay|echo", Effect.DELAY),
        (r"reverb|verb", Effect.REVERB),
        (r"filter|wah|envelope|eq|equalizer", Effect.FILTER),
        (r"preamp|amp|amps", Effect.AMP),
        (
            r"utility|buffers?|splitter|switcher|switching|selector|blender|blend"
            r"|power|breakout|looper|tuner|volume",
            Effect.UTILITY,
        ),
    )
)


def _named(words: list[str]) -> list[Effect]:
    """The effects a list of words names, in `Effect`'s order."""
    found = {
        effect
        for word in words
        for pattern, effect in _WORDS
        if pattern.fullmatch(word)
    }
    return [effect for effect in Effect if effect in found]


def from_categories(categories: tuple[str, ...]) -> list[Effect]:
    """The effects a publisher's categories name.

    Args:
        categories: The publisher's categories, as its page names them.

    Returns:
        The effects, in `Effect`'s order; none for categories that say
        nothing about the sound ('diy-guitar-effects-pedal-pcbs').
    """
    found: set[Effect] = set()
    for category in categories:
        slug = category.lower()
        if slug in _CATEGORIES:
            whole = _CATEGORIES[slug]
            found |= {whole} if whole else set()
        else:
            found |= set(_named(re.split(r"[\s_-]+", slug)))
    return [effect for effect in Effect if effect in found]


# Words a category means but a title doesn't: 'Tower Power' is a drive,
# 'BMP Op Amp' a fuzz.
_CATEGORY_ONLY = frozenset({"power", "amp", "amps", "preamp"})


def from_title(title: str) -> list[Effect]:
    """The effects a build's title names ('Acid Rain Fuzz': fuzz)."""
    words = re.findall(r"[a-z]+", title.lower())
    return _named([w for w in words if w not in _CATEGORY_ONLY])


class Overrides:
    """The effects set by hand, by entry ('<publisher>/<id>')."""

    def __init__(self, effects: dict[str, list[Effect]]) -> None:
        """Holds the effects set by hand.

        Args:
            effects: Each entry's effects, by '<publisher>/<id>'.
        """
        self._effects = effects

    @classmethod
    def load(cls, path: Path) -> Overrides:
        """Reads `catalog/effects.json`, or sets nothing if it's missing."""
        if not path.exists():
            return cls({})
        raw: dict[str, list[str]] = json.loads(path.read_text())
        return cls({key: [Effect(e) for e in value] for key, value in raw.items()})

    def get(self, key: str) -> list[Effect] | None:
        """The effects set by hand for an entry, if any."""
        return self._effects.get(key)


def effects(
    key: str, title: str, categories: tuple[str, ...], overrides: Overrides
) -> list[Effect]:
    """A build's effects: set by hand, else its title's, else the publisher's.

    Args:
        key: The entry, as '<publisher>/<id>'.
        title: The build's title.
        categories: The publisher's categories for it.
        overrides: The effects set by hand.

    Returns:
        Its effects, in `Effect`'s order; none if nothing places it.
    """
    set_by_hand = overrides.get(key)
    if set_by_hand is not None:
        return set_by_hand
    return from_title(title) or from_categories(categories)
