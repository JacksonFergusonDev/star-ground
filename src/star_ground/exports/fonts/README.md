# Fonts

The printed documents use house-style's two typefaces, embedded so every PDF looks the same on any machine. Both are under the SIL Open Font License (the `OFL-*.txt` files here).

- **DM Sans** (`DMSans-Regular.ttf`, `-Medium`, `-SemiBold`): static instances of Google Fonts' variable `DMSans[opsz,wght].ttf`, made with fontTools at the 14 pt optical size and weights 400, 500 and 600. Typst names the family `DM Sans 14pt`.
- **JetBrains Mono** (`JetBrainsMono-Regular.ttf`, `-Bold`): from JetBrains Mono 2.304's release.

house-style ships these as WOFF2, which Typst can't read, so the TTFs are kept here.

To remake the DM Sans instances:

```python
from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

for weight, name in [(400, "Regular"), (500, "Medium"), (600, "SemiBold")]:
    font = TTFont("DMSans[opsz,wght].ttf")
    static = instancer.instantiateVariableFont(
        font, {"wght": weight, "opsz": 14}, updateFontNames=True
    )
    static.save(f"DMSans-{name}.ttf")
```
