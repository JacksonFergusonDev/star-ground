"""The Streamlit app's session state.

Frontend state, not backend: nothing in the backend imports this module. It
lives outside the app script because Streamlit re-executes the script on every
rerun, and a class defined there never equals the last run's.
"""

import uuid
from dataclasses import dataclass, field

from src.api import Project, ProjectData, document_key
from src.bom_lib import InputMethod, ParseResult, PcbShop, parse_preset_key


@dataclass
class ProjectSlot:
    """One project's inputs on the page, kept between reruns.

    Attributes:
        id: Keys the slot's widgets.
        name: The project name typed in.
        method: How the parts list is given.
        count: Number of pedals.
        data: Pasted or preset text, a URL, or an uploaded file.
        last_loaded_preset: The preset last loaded, to rename the project
            only while its name is still the preset's.
        source_path: The loaded preset's typed parts list.
        source_url: The loaded preset's build document at its publisher.
        reading: The upload or link as `read_document` read it.
        reading_key: The `document_key` the reading was made from.
        variant: The build chosen from a multi-circuit document.
        use_ocr: Whether the builder accepted OCR's parts.
    """

    id: uuid.UUID = field(default_factory=uuid.uuid4)
    name: str = ""
    method: InputMethod = InputMethod.PASTE_TEXT
    count: int = 1
    data: ProjectData = None
    last_loaded_preset: str | None = None
    source_path: str | None = None
    source_url: str | None = None
    reading: ParseResult | None = None
    reading_key: str | None = None
    variant: str | None = None
    use_ocr: bool = False

    def project(self) -> Project:
        """The slot as the backend takes it.

        Returns:
            The project, with its reading if that matches the current input.
        """
        key = document_key(self.method, self.data)
        preset = parse_preset_key(self.last_loaded_preset or "")
        shop = (
            PcbShop(preset[0])
            if self.method == InputMethod.PRESET and preset and preset[0] in PcbShop
            else None
        )
        return Project(
            name=str(self.name or ""),
            count=self.count,
            method=self.method,
            data=self.data,
            variant=self.variant,
            use_ocr=self.use_ocr,
            reading=self.reading
            if key is not None and key == self.reading_key
            else None,
            source_path=self.source_path,
            source_url=self.source_url,
            pcb_shop=shop,
        )


@dataclass(frozen=True, slots=True)
class SlotInputs:
    """What one project contributes to the master list, as the builder set it.

    Attributes:
        name: The project name typed in.
        count: Number of pedals.
        method: How the parts list is given.
        source: The document's identity, or the pasted or preset text.
        variant: The build chosen from a multi-circuit document.
        use_ocr: Whether OCR's parts were accepted.
    """

    name: str
    count: int
    method: InputMethod
    source: str | None
    variant: str | None
    use_ocr: bool


@dataclass(frozen=True, slots=True)
class Inputs:
    """Everything the master list on screen was generated from.

    Attributes:
        slots: Each project's inputs, in order.
        stock: The uploaded stock file's identity, if any.
    """

    slots: tuple[SlotInputs, ...]
    stock: str | None
