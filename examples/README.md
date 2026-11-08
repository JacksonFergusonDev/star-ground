# Example build pack

`build-pack/` is the build pack Star Ground makes for one Big Muff π, the circuit drawn on the website's hero board. Its parts list comes from that circuit's netlist (`web/src/lib/circuit-field/big-muff.cir`), so no publisher's document is needed.

- **Build book.pdf**: the shopping list, then the pedal's checklist in build order, lowest parts first.
- **Labels (Avery 5160).pdf**: a label for each part's bag or drawer, 30 to a sheet.
- **Shopping list.csv**: every part to buy, for a spreadsheet.
- **Supplier cart.csv**: the same parts for Mouser's or Digi-Key's BOM upload page.
- **Stock after these builds.csv**: the stock left once the order arrives and the pedal is built.

A real pack also holds `Source documents/`, the documents you gave. To make this one again after changing the exports:

```bash
just example-pack
```
