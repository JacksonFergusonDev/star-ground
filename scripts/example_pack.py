"""Regenerates `examples/build-pack/`: a build pack for the Big Muff π.

The parts come from `web/src/lib/circuit-field/big-muff.cir`, the circuit the
website's hero draws, so the example is a real circuit and needs no
publisher's document. Run it with `just example-pack`.
"""

import io
import shutil
import zipfile
from datetime import date
from pathlib import Path

from star_ground import version
from star_ground.api import Project, generate_master_list
from star_ground.cli_output import CliOutput
from star_ground.exports.pack import PackContents, build_pack

ROOT = Path(__file__).parent.parent
NETLIST = ROOT / "web" / "src" / "lib" / "circuit-field" / "big-muff.cir"
OUT = ROOT / "examples" / "build-pack"

# The netlist names its pots by control; a parts list numbers them. The
# schematic it cites gives no tapers, so none is invented here.
POTS = {"XSUSTAIN": "VR1", "XTONE": "VR2", "XVOLUME": "VR3"}


def parts_list(netlist: str) -> str:
    """The board's parts as a pasted parts list: one 'R1 39k' line each.

    Args:
        netlist: The SPICE netlist, with the board between its markers.

    Returns:
        The parts list.
    """
    board = netlist.split("* --- board ---")[1].split("* --- end board ---")[0]
    lines = []
    for line in board.splitlines():
        words = line.split()
        if not words or words[0].startswith("*"):
            continue
        name = words[0]
        if name in POTS:
            value = next(w for w in words if w.startswith("R=")).removeprefix("R=")
            lines.append(f"{POTS[name]} {value}")
        else:
            lines.append(f"{name} {words[-1]}")
    return "\n".join(lines)


def main() -> None:
    """Writes the example pack's files into `examples/build-pack/`."""
    master = generate_master_list(
        [Project(name="Big Muff π", data=parts_list(NETLIST.read_text()))]
    )
    assert not master.stats.errors, master.stats.errors
    pack = build_pack(
        PackContents(
            master.kit,
            master.projects,
            master.shopping_list(),
            excel_links=False,
            made=date.today(),
            version=version(),
        )
    )
    shutil.rmtree(OUT, ignore_errors=True)
    with zipfile.ZipFile(io.BytesIO(pack)) as zf:
        for name in zf.namelist():
            # Drop the dated folder, so the example's paths stay the same.
            path = OUT / name.split("/", 1)[1]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(zf.read(name))
    CliOutput().success(f"Wrote {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
