import copy
import io
import logging
import os
import tempfile
from typing import Any, cast

import streamlit as st

from src.bom_lib import (
    BOM_PRESETS,
    ProjectSlot,
    StatsDict,
    calculate_net_needs,
    create_empty_inventory,
    generate_pedalpcb_url,
    generate_search_term,
    generate_tayda_url,
    get_buy_details,
    get_clean_name,
    get_preset_metadata,
    get_residual_report,
    get_spec_type,
    get_standard_hardware,
    parse_user_inventory,
    process_input_data,
    rename_source_in_inventory,
    sort_inventory,
)
from src.exporters import generate_shopping_list_csv, generate_stock_update_csv
from src.feedback import save_feedback
from src.pdf_generator import generate_master_zip, generate_pdf_bundle

st.set_page_config(page_title="Star Ground", page_icon="⚡")

# Hide the native Streamlit dataframe toolbar (Search/Download)
st.markdown(
    """
<style>
    [data-testid="stElementToolbar"] {
        display: none;
    }
</style>
""",
    unsafe_allow_html=True,
)

# Initialize session state for logs
if "log_capture" not in st.session_state:
    st.session_state.log_capture = io.StringIO()


class StreamlitLogHandler(logging.Handler):
    """Custom handler to route logs to Streamlit session state."""

    def emit(self, record):
        try:
            msg = self.format(record)
            # Write to the StringIO buffer in session state
            st.session_state.log_capture.write(msg + "\n")
        except Exception:
            self.handleError(record)


# Setup Logger
logger = logging.getLogger()
# Only add handlers if they aren't already attached (prevents duplicates on rerun)
if not logger.handlers:
    logger.setLevel(logging.INFO)

    # 1. Console Handler (for Docker/Terminal logs)
    console_handler = logging.StreamHandler()
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # 2. UI Handler (for the Debug Console)
    st_handler = StreamlitLogHandler()
    st_handler.setFormatter(formatter)
    logger.addHandler(st_handler)


st.title("⚡ Star Ground")
st.markdown("""
**The Single Source of Truth for Analog Electronics.**

In circuit design, a **Star Ground** is the reference point where all signal paths converge to eliminate noise. 
This tool serves the same function for your logistics: it aggregates BOMs, subtracts inventory, and generates clean manufacturing data to eliminate the "noise" of disorganized spreadsheets.
""")

# Initialize session state for inventory and statistics
if "inventory" not in st.session_state:
    st.session_state.inventory = None
if "stats" not in st.session_state:
    st.session_state.stats = None

# Initialize session state for pedal slots
if "pedal_slots" not in st.session_state:
    st.session_state.pedal_slots = [ProjectSlot()]


def add_slot():
    """Appends a new empty pedal slot to the session state."""
    st.session_state.pedal_slots.append(ProjectSlot())


def remove_slot(idx):
    """
    Removes a pedal slot from the session state by index.

    Args:
        idx (int): The index of the slot to remove.
    """
    st.session_state.pedal_slots.pop(idx)


def render_preset_selector(slot, idx):
    """
    Renders a 3-stage smart selector widget for a specific slot.

    Includes filters for Source and Category to narrow down the main
    project selection list.

    Args:
        slot (dict): The slot dictionary to render controls for.
        idx (int): The index of the slot (unused in logic but useful for keys).

    Returns:
        Any: The Streamlit widget object for the main selector.
    """
    # Load metadata (cached)
    all_sources, cat_map, lookup = get_preset_metadata()

    # Layout: 2 small filter columns, 1 main selector
    c_filt1, c_filt2, c_main = st.columns([1, 1, 2])

    # --- 1. Source Filter ---
    # Use session state to persist the filter choice per slot
    src_key = f"filter_src_{slot['id']}"
    selected_src = c_filt1.selectbox(
        "Source",
        ["All"] + all_sources,
        key=src_key,
        label_visibility="collapsed",
        help="Filter by Vendor",
    )

    # --- 2. Category Filter ---
    # Dynamic options based on Source selection
    if selected_src != "All":
        cat_options = ["All"] + cat_map.get(selected_src, [])
    else:
        # If All sources, show all unique categories across everything
        # Flatten the list of lists
        flat_cats = sorted(
            list(set(cat for sublist in cat_map.values() for cat in sublist))
        )
        cat_options = ["All"] + flat_cats

    cat_key = f"filter_cat_{slot['id']}"
    selected_cat = c_filt2.selectbox(
        "Category",
        cat_options,
        key=cat_key,
        label_visibility="collapsed",
        help="Filter by Category",
    )

    # --- 3. Filter the Main List ---
    # Filter the lookup list based on choices
    filtered_items = lookup
    if selected_src != "All":
        filtered_items = [i for i in filtered_items if i["source"] == selected_src]
    if selected_cat != "All":
        filtered_items = [i for i in filtered_items if i["category"] == selected_cat]

    # Extract just the full keys for the widget
    option_keys = [i["full_key"] for i in filtered_items]

    # Handle Edge Case: If filter results in empty list
    if not option_keys:
        st.warning("No presets match filters.")
        return

    # Find current index (maintain selection if still valid after filter)
    current_val = slot.get("last_loaded_preset")
    try:
        current_idx = option_keys.index(current_val)
    except (ValueError, TypeError):
        current_idx = 0

    # --- 4. The Smart Selectbox ---
    def format_label(key):
        # Find the metadata for this key
        meta = next((i for i in filtered_items if i["full_key"] == key), None)
        if not meta:
            return key

        # Smart Labeling:
        # If Source is already filtered, don't show it in the label.
        # If Category is already filtered, don't show it.
        label = meta["name"]

        extras = []
        if selected_src == "All":
            extras.append(meta["source"])
        if selected_cat == "All" and meta["category"] != "Misc":
            extras.append(meta["category"])

        if extras:
            return f"{label}  ({', '.join(extras)})"
        return label

    # The actual widget
    selection = c_main.selectbox(
        "Select Project",
        options=option_keys,
        index=current_idx,
        format_func=format_label,
        key=f"preset_select_{slot['id']}",
        label_visibility="collapsed",
        on_change=update_from_preset,
        args=(slot["id"],),
    )

    return selection


def update_from_preset(slot_id):
    """
    Callback to update slot data and name when the preset selection changes.

    Args:
        slot_id (str): The unique identifier for the slot being updated.
    """
    # Find the specific slot by ID
    slot = next((s for s in st.session_state.pedal_slots if s["id"] == slot_id), None)
    if not slot:
        return

    # Get the new selection from session state
    key = f"preset_select_{slot_id}"
    new_preset = st.session_state.get(key)

    if new_preset:
        # Safety Check: Guard against race conditions where the session state
        # holds a key that is no longer valid or present in the master list.
        if new_preset not in BOM_PRESETS:
            return

        # 1. Update BOM Data
        preset_obj = BOM_PRESETS[new_preset]

        # Handle New Dict Format vs Legacy String
        if isinstance(preset_obj, dict):
            slot["data"] = preset_obj["bom_text"]
            slot["source_path"] = preset_obj.get("source_path")
            slot.pop("pdf_path", None)
        else:
            slot["data"] = preset_obj
            slot.pop("source_path", None)

        # Force the text area to reflect this new data
        st.session_state[f"text_preset_{slot_id}"] = slot["data"]

        # 2. Update Name (Only if empty or matches previous preset)
        last_loaded_key = slot.get("last_loaded_preset")

        current_name = slot["name"]

        # Safe helper for the name check
        last_clean = get_clean_name(last_loaded_key) if last_loaded_key else ""

        should_update = not current_name or current_name in (
            last_clean,
            last_loaded_key,
        )

        if should_update:
            clean_name = get_clean_name(new_preset)
            slot["name"] = clean_name
            st.session_state[f"name_{slot_id}"] = clean_name

        # 3. Update Tracking
        slot["last_loaded_preset"] = new_preset


def _reset_slot_state(slot: dict[str, Any], new_method: str) -> None:
    """
    Clears data and UI state for a slot when switching input methods.

    Args:
        slot: The slot dictionary to reset.
        new_method: The new method string being switched to.
    """
    slot_id = slot["id"]

    # 1. Clear Data Fields
    slot["data"] = None if new_method == "Upload File" else ""
    slot["name"] = ""

    # 2. Clear Metadata / Cache
    keys_to_pop = ["pdf_path", "source_path", "cached_pdf_bytes", "last_loaded_preset"]
    for k in keys_to_pop:
        slot.pop(k, None)

    # 3. Clear Streamlit Session Keys
    # We clear the UI widgets so they don't retain old values
    keys_to_clear = [
        f"name_{slot_id}",
        f"text_{slot_id}",
        f"url_{slot_id}",
        f"text_preset_{slot_id}",
        f"preset_select_{slot_id}",
        f"filter_src_{slot_id}",
        f"filter_cat_{slot_id}",
    ]
    for k in keys_to_clear:
        if k in st.session_state:
            del st.session_state[k]

    # 4. Update Method Tracker
    slot["method"] = new_method
    slot["last_method"] = new_method


def on_method_change(slot_id):
    """
    Callback to handle input method switches (Paste, Upload, URL, Preset).
    """
    slot = next((s for s in st.session_state.pedal_slots if s["id"] == slot_id), None)
    if not slot:
        return

    # Fix: Pylance error. .get() returns Any | None, but we need str.
    # We provide a default and wrap in str() to ensure type safety.
    new_method = str(st.session_state.get(f"method_{slot_id}", "Paste Text"))

    _reset_slot_state(slot, new_method)

    # Handle specific initialization for Presets
    if new_method == "Preset":
        first_preset = sorted(list(BOM_PRESETS.keys()))[0]
        preset_obj = BOM_PRESETS[first_preset]

        if isinstance(preset_obj, dict):
            slot["data"] = preset_obj["bom_text"]
            slot["source_path"] = preset_obj.get("source_path")
        else:
            slot["data"] = preset_obj

        slot["last_loaded_preset"] = first_preset

        # Auto-fill name
        clean_name = get_clean_name(first_preset)
        slot["name"] = clean_name
        st.session_state[f"name_{slot_id}"] = clean_name
        st.session_state[f"text_preset_{slot_id}"] = slot["data"]


st.divider()
st.subheader("1. Project Configuration")

# Placeholder Examples for UI hints
PLACEHOLDERS = [
    "Big Muff",
    "Pro Co RAT",
    "Fuzz Face",
    "Tube Screamer",
    "Klon Centaur",
    "Tone Bender",
]

for i, slot in enumerate(st.session_state.pedal_slots):
    with st.container():
        # Row 1: Metadata (Name, Qty, Method, Delete)
        # We give more space to Name now that Input is on its own row
        c1, c2, c3, c4 = st.columns([3, 1, 2, 0.5])

        # Project Name
        name_key = f"name_{slot.id}"
        # Initialize session state if missing to set default value
        if name_key not in st.session_state:
            st.session_state[name_key] = slot.name

        slot.name = c1.text_input(
            f"Project Name #{i + 1}",
            key=name_key,
            placeholder=f"e.g. {PLACEHOLDERS[i % len(PLACEHOLDERS)]}",
        )

        # Quantity
        slot.count = c2.number_input(
            "Qty",
            min_value=1,
            value=slot.count,
            key=f"qty_{slot.id}",
            label_visibility="visible",
        )

        # Input Method
        slot.method = c3.radio(
            "Input Method",
            ["Paste Text", "Upload File", "From URL", "Preset"],
            key=f"method_{slot.id}",
            horizontal=True,
            label_visibility="collapsed",
            on_change=on_method_change,
            args=(slot.id,),
        )

        # Remove Button (Top Right)
        if len(st.session_state.pedal_slots) > 1 and c4.button(
            "🗑️", key=f"del_{slot.id}"
        ):
            remove_slot(i)
            st.rerun()

        # Row 2: Data Input (Full Width)
        if slot.method == "Paste Text":
            text_key = f"text_{slot.id}"
            if text_key not in st.session_state:
                st.session_state[text_key] = slot.data or ""

            slot.data = st.text_area(
                "BOM Text",
                height=150,
                key=text_key,
                placeholder="Paste your BOM here...",
                help="Paste raw text like 'R1 10k', 'C1 100n', etc.",
            )

        elif slot.method == "Upload File":
            slot.data = st.file_uploader(
                "Upload BOM",
                type=["csv", "pdf"],
                key=f"file_{slot.id}",
            )

        elif slot.method == "From URL":
            url_key = f"url_{slot.id}"
            slot.data = st.text_input(
                "BOM URL",
                key=url_key,
                placeholder="https://raw.githubusercontent.com/...",
            )

        elif slot.method == "Preset":
            # 1. The Selector (Hierarchical)
            # Note: Ensure render_preset_selector is compatible with ProjectSlot objects
            render_preset_selector(slot, i)

            # 2. The Read-Only Preview
            text_key = f"text_preset_{slot.id}"
            if text_key not in st.session_state:
                st.session_state[text_key] = slot.data or ""

            slot.data = st.text_area(
                "Preview",
                height=150,
                key=text_key,
                disabled=True,
                label_visibility="collapsed",
            )

        st.divider()

st.button("➕ Add Another Pedal", on_click=add_slot)

st.divider()
st.subheader("2. Inventory Check (Optional)")
stock_file = st.file_uploader(
    "📂 Upload Stock CSV", type=["csv"], help="Columns: Category, Part, Qty"
)
st.divider()

active_names = [
    s.name for s in st.session_state.pedal_slots if s.name and s.name.strip()
]
if len(active_names) != len(set(active_names)):
    st.warning(
        "⚠️ Duplicate projects detected. Quantities will be summed, but source documents will only be included once."
    )

if st.button("Generate Master List", type="primary", width="stretch"):
    # REFACTOR: Use the factory to get the new Inventory Class
    from src.bom_lib.types import create_empty_inventory

    inventory = create_empty_inventory()

    stats: StatsDict = {
        "lines_read": 0,
        "parts_found": 0,
        "residuals": [],
        "extracted_title": None,
        "seen_refs": set(),
        "errors": [],
    }

    # Process Each Slot
    for i, slot in enumerate(st.session_state.pedal_slots):
        # Resolve Name
        current_name = str(slot.name or "")
        source = current_name if current_name.strip() else f"Project #{i + 1}"
        qty_multiplier = slot.count

        # Unified Processing
        p_inv, p_stats, detected_title, raw_content = process_input_data(
            slot.method, slot.data, source_name=source
        )

        # Store the raw content in the slot if it was returned (i.e. it was a PDF)
        if raw_content:
            slot.cached_pdf_bytes = raw_content

        # Auto-Rename Logic
        if detected_title and not current_name.strip():
            # Update Slot Name
            slot.name = str(detected_title)
            # Remap Inventory Keys
            rename_source_in_inventory(p_inv, source, str(detected_title))

        # REFACTOR: Use the .merge() method of the Inventory class
        inventory.merge(p_inv, qty_multiplier)

        stats["lines_read"] += p_stats.get("lines_read", 0)
        stats["parts_found"] += p_stats.get("parts_found", 0)
        stats["residuals"].extend(p_stats.get("residuals", []))

        # Final Fallback: If name is still empty, lock in the placeholder
        if not slot.name.strip():
            slot.name = source

        # Save the final resolved name
        slot.locked_name = slot.name

    # Process Stock if uploaded
    stock_inventory = None
    if stock_file:
        # Save temp to parse
        with tempfile.NamedTemporaryFile(delete=False, suffix=".csv") as tmp:
            tmp.write(stock_file.getvalue())
            tmp_path = tmp.name

        try:
            stock_inventory = parse_user_inventory(tmp_path)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    st.session_state.inventory = inventory
    st.session_state.stock = stock_inventory  # Save to session
    st.session_state.stats = stats

    # Validation Logic
    if stats["parts_found"] == 0:
        st.error("❌ No parts found! Check your BOM text or file.")
        if stats.get("errors"):
            with st.expander("Show Errors", expanded=True):
                for err in stats["errors"]:
                    st.error(f"❌ {err}")

    elif stats.get("errors"):
        # Case: We found parts, but some files/lines failed (Partial Success)
        st.warning("⚠️ Master List generated, but some inputs had errors.")
        for err in stats["errors"]:
            st.error(f"❌ {err}")

    else:
        st.toast("Master List Generated Successfully", icon="✅")

# Main Process Flow
if st.session_state.inventory and st.session_state.stats:
    inventory = copy.deepcopy(st.session_state.inventory)
    stats = cast(StatsDict, st.session_state.stats)

    # 1. Show Stats
    with st.container():
        c1, c2, c3 = st.columns(3)
        c1.metric("Lines Scanned", stats["lines_read"])
        c2.metric("Parts Found", stats["parts_found"])
        unique_parts = len(sort_inventory(inventory))
        c3.metric("Unique SKUs", unique_parts)

    st.divider()

    # Stop here if empty
    if stats["parts_found"] == 0:
        st.stop()

    # Check for junk / parsing residuals
    suspicious = get_residual_report(stats)
    if suspicious:
        st.warning(f"⚠️ Skipped {len(suspicious)} lines that looked important:")
        with st.expander("Show ignored lines"):
            for line in suspicious:
                st.code(line)
    else:
        st.success("✅ Clean parse. No weird leftovers.")

    # Phase 1.5 Refactor Filters
    with st.expander("⚙️ Advanced Options", expanded=False):
        c_filt1, c_filt2 = st.columns(2)
        show_hardware = c_filt1.checkbox("Include Hardware Kit", value=True)
        show_extras = c_filt2.checkbox("Include Sockets & Adapters", value=True)

    st.info("""
    **📋 Key:**
    * **Circuit Board:** Components found in your BOM.
    * **Hardware Kit:** Auto-injected enclosures, jacks, and switches.
    * **Extras:** Recommended add-ons (Sockets, Adapters).
    """)

    # 2. Build the Shopping List
    final_data = []

    # STEP A: Inject Hardware (Mutates Inventory In-Place)
    calc_pedal_count = sum(slot.count for slot in st.session_state.pedal_slots)
    # No return value, just mutation
    get_standard_hardware(inventory, calc_pedal_count)

    # STEP B: Process the Unified Inventory
    stock = st.session_state.get("stock")

    # If stock exists, we calculate net needs, otherwise net = gross
    if stock:
        # Calculate Net Needs (Deficit)
        net_inventory = calculate_net_needs(inventory, stock)
        display_source = inventory
    else:
        display_source = inventory
        net_inventory = inventory

    sorted_parts = sort_inventory(display_source)

    for part_key, item in sorted_parts:
        if " | " not in part_key:
            continue

        category, value = part_key.split(" | ", 1)

        gross_qty = item["qty"]

        # Lookup Net Need
        net_item = net_inventory.get(part_key)
        net_qty = net_item["qty"] if net_item else 0

        # Lookup Stock for Display
        in_stock = 0
        if stock:
            s_item = stock.get(part_key)
            in_stock = s_item["qty"] if s_item else 0

        sources = item["sources"]

        # --- FILTERING LOGIC ---

        # Check if this is PURELY an auto-injected item (no parsed sources)
        is_pure_hardware = len(sources) == 1 and "Auto-Inject" in sources

        if is_pure_hardware and not show_hardware:
            continue

        # Check for Extras (Sockets/Adapters)
        is_extra = "SOCKET" in value or "ADAPTER" in value
        if is_extra and not show_extras:
            continue

        # --- ORIGIN ASSIGNMENT ---
        if is_pure_hardware:
            origin = "Hardware Kit"
        elif is_extra:
            origin = "Extras"
        else:
            origin = "Circuit Board"

        # Calculate purchasing requirements
        buy_qty, note = get_buy_details(
            category, value, net_qty, fval=item.get("val_float")
        )

        # Append context from Auto-Inject if present
        auto_inject_notes = sources.get("Auto-Inject", [])

        if auto_inject_notes and origin != "Hardware Kit":
            formatted_notes = ", ".join(auto_inject_notes)
            note += f" | 🤖 Standard Part: {formatted_notes}"

        spec_type = get_spec_type(category, value)
        search_term = generate_search_term(category, value, spec_type)

        # Link Generation Logic
        is_pedalpcb_source = any("PedalPCB" in s for s in sources)
        is_tayda_source = any("Tayda" in s for s in sources)

        if category == "PCB":
            if is_pedalpcb_source and not is_tayda_source:
                url = generate_pedalpcb_url(search_term)
            else:
                url = generate_tayda_url(search_term)
        else:
            url = generate_tayda_url(search_term)

        final_data.append(
            {
                "Origin": origin,
                "Category": category,
                "Part": value,
                "BOM Qty": gross_qty,
                "In Stock": in_stock,
                "Net Need": net_qty,
                "Buy Qty": buy_qty,
                "Notes": note,
                "Search Term": search_term,
                "Tayda_Link": url,
            }
        )

    # 3. Render
    st.subheader("🛒 Master Shopping List")

    # Dynamic Columns (Conditionally add stock columns)
    display_cols = [
        "Category",
        "Part",
        "BOM Qty",
        "Buy Qty",
        "Notes",
        "Tayda_Link",
        "Origin",
    ]

    # Only add Stock columns if stock was actually provided
    if stock:
        # Insert them after BOM Qty
        display_cols[3:3] = ["In Stock", "Net Need"]

    # Configure the dataframe
    st.dataframe(
        final_data,
        column_order=display_cols,
        column_config={
            "Tayda_Link": st.column_config.LinkColumn(
                "Buy Link",
                display_text="🔍 Buy",
                help="Search on Tayda Electronics",
            ),
        },
        width="stretch",
        hide_index=True,  # Cleaner look
    )

    # 4. Downloads
    st.subheader("💾 Export")

    # Toggle for Link Formatting
    link_format = st.radio(
        "CSV Link Format:",
        ["Excel / Google Sheets (Formula)", "Standard (Raw URL)"],
        horizontal=True,
        help="Excel mode creates clickable 'Buy' links. Standard mode saves the full https:// URL.",
    )

    # Generate Exports
    is_excel = "Excel" in link_format
    csv_out = generate_shopping_list_csv(final_data, use_excel_formulas=is_excel)
    stock_update_csv = generate_stock_update_csv(final_data)

    # Row 1: Specific Downloads
    c_dwn1, c_dwn2, c_dwn3 = st.columns(3)

    with c_dwn1:
        st.download_button(
            "🛒 Download Shopping List",
            data=csv_out,
            file_name="shopping_list.csv",
            mime="text/csv",
            use_container_width=True,
        )

    with c_dwn2:
        st.download_button(
            "📦 Download Updated Inventory",
            data=stock_update_csv,
            file_name="my_inventory_updated.csv",
            mime="text/csv",
            help="Stock levels minus usage + new buys.",
            use_container_width=True,
        )

    with c_dwn3:
        st.download_button(
            "📖 Download Generated PDFs",
            data=generate_pdf_bundle(inventory, st.session_state.pedal_slots),
            file_name="Star_Ground_Docs.zip",
            mime="application/zip",
            help="ZIP containing Field Manuals and Sticker Sheets.",
            use_container_width=True,
        )

    # Row 2: Everything
    st.download_button(
        "📚 Download Complete Build Pack (ZIP)",
        data=generate_master_zip(
            inventory, st.session_state.pedal_slots, csv_out, stock_update_csv
        ),
        file_name="Star_Ground_Artifacts.zip",
        mime="application/zip",
        help="Includes: Shopping List, Inventory, Field Manuals, Stickers, and Source Files.",
        type="primary",
        use_container_width=True,
    )

st.divider()

# --- Debug & Feedback Section ---

# Checking if logs exist to decide whether to show the debug console and download button.
logs = st.session_state.log_capture.getvalue()

if "feedback_submitted" not in st.session_state:
    st.session_state.feedback_submitted = False

with st.expander("🐞 Found a bug? / 📢 Feedback"):
    if st.session_state.feedback_submitted:
        st.success("Feedback received. Thank you!")
    else:
        st.caption("Found a parsing error? Have a feature request? Let me know.")

        with st.form("feedback_form"):
            col1, col2 = st.columns([1, 4])
            with col1:
                rating = st.select_slider(
                    "Rating", options=["😡", "😕", "😐", "🙂", "🤩"], value="🤩"
                )
            with col2:
                comment = st.text_area(
                    "Details", height=80, placeholder="Describe the issue or idea..."
                )

            submitted = st.form_submit_button("Submit Feedback")

            if submitted:
                if not comment:
                    st.warning("Please enter a comment.")
                else:
                    try:
                        save_feedback(rating, comment)
                        st.session_state.feedback_submitted = True
                        st.rerun()
                    except Exception as e:
                        st.error(f"Error: {e}")

    if logs:
        st.divider()
        st.download_button(
            "📥 Download Debug Logs",
            data=logs,
            file_name="star_ground_debug.log",
            mime="text/plain",
            help="Attach this file when reporting bugs!",
            use_container_width=True,
        )

if logs:
    with st.expander("📟 Debug Console", expanded=False):
        st.caption("System logs for debugging parsing issues.")
        st.text_area(
            "Log Output",
            value=logs,
            height=300,
            disabled=True,
            label_visibility="collapsed",
        )

        if st.button("Clear Logs"):
            st.session_state.log_capture.truncate(0)
            st.session_state.log_capture.seek(0)
            st.rerun()
