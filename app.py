import csv
import datetime
import io
import os
import uuid
import re
import copy
import tempfile
from collections import defaultdict
from typing import cast, List, Dict, Any
from streamlit.runtime.uploaded_file_manager import UploadedFile
import streamlit as st
from src.presets import BOM_PRESETS

from src.bom_lib import (
    InventoryType,
    StatsDict,
    generate_search_term,
    generate_tayda_url,
    get_buy_details,
    get_residual_report,
    get_spec_type,
    get_standard_hardware,
    parse_csv_bom,
    parse_with_verification,
    sort_inventory,
    parse_pedalpcb_pdf,
    parse_user_inventory,
    calculate_net_needs,
)

st.set_page_config(page_title="Pedal BOM Manager", page_icon="🎸")


# ttl="1h" to prevent stale token issues
@st.cache_resource(ttl="1h")
def get_gsheet_client():
    """Establishes a persistent connection to Google Sheets."""
    # Lazy import to prevent 3-5s startup delay
    import gspread
    from google.oauth2.service_account import Credentials

    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]
    creds_dict = st.secrets["gcp_service_account"]
    creds = Credentials.from_service_account_info(creds_dict, scopes=scope)
    return gspread.authorize(creds)


def save_feedback(rating, text):
    """Appends feedback row using the cached client."""
    client = get_gsheet_client()
    sheet = client.open("Pedal BOM Feedback").sheet1

    # Append timestamp, rating, and comment
    row = [str(datetime.datetime.now()), rating, text]
    sheet.append_row(row)


st.title("🎸 Guitar Pedal BOM Manager")
st.markdown("""
**Automate your electronics shopping list.**

Paste your raw component lists (or upload a CSV). 
This tool cleans the data, handles ranges like `R1-R5`, and adds "Nerd Economics" (bulk buying buffers) to your final list.
""")

if "inventory" not in st.session_state:
    st.session_state.inventory = None
if "stats" not in st.session_state:
    st.session_state.stats = None

# Initialize Slots
if "pedal_slots" not in st.session_state:
    init_slots: List[Dict[str, Any]] = [
        {"id": str(uuid.uuid4()), "name": "", "method": "Paste Text"}
    ]
    st.session_state.pedal_slots = init_slots


def get_clean_name(raw_key):
    """Parses '[Source] [Category] Name' into 'Name - Source'."""
    if not raw_key:
        return ""
    match = re.match(r"^\[(.*?)\] (?:\[(.*?)\] )?(.*)$", raw_key)
    if match:
        src = match.group(1)
        name = match.group(3)
        return f"{name} - {src}"
    return raw_key


def add_slot():
    st.session_state.pedal_slots.append(
        {"id": str(uuid.uuid4()), "name": "", "method": "Paste Text"}
    )


def remove_slot(idx):
    st.session_state.pedal_slots.pop(idx)


def merge_inventory(master_inv, new_inv, multiplier):
    """Merges a parsed BOM into the master inventory with a quantity multiplier."""
    for key, data in new_inv.items():
        master_inv[key]["qty"] += data["qty"] * multiplier
        master_inv[key]["refs"].extend(data["refs"])
        for src, refs in data["sources"].items():
            master_inv[key]["sources"][src].extend(refs * multiplier)


@st.cache_data
def get_preset_metadata():
    """
    Parses BOM_PRESETS keys into a queryable structure.
    Returns:
        sources (list): Unique sources (e.g., 'PedalPCB', 'Tayda')
        categories (dict): Map of Source -> List of Categories
        lookup (list): List of dicts {'key', 'source', 'category', 'name'}
    """
    lookup = []
    sources = set()
    categories = defaultdict(set)

    # Regex to handle "[Source] [Category] Name" or "[Source] Name"
    # Matches: [Group1] (optional [Group2]) Remainder
    pattern = re.compile(r"^\[(.*?)\] (?:\[(.*?)\] )?(.*)$")

    for key in BOM_PRESETS.keys():
        match = pattern.match(key)
        if match:
            src = match.group(1)
            cat = match.group(2) or "Misc"
            name = match.group(3)

            sources.add(src)
            categories[src].add(cat)

            lookup.append(
                {
                    "full_key": key,
                    "source": src,
                    "category": cat,
                    "name": name,
                }
            )

    # Sort for UI consistency
    return (
        sorted(list(sources)),
        {k: sorted(list(v)) for k, v in categories.items()},
        lookup,
    )


def render_preset_selector(slot, idx):
    """
    Renders a 3-stage smart selector for a specific slot.
    """
    # Load metadata (cached)
    all_sources, cat_map, lookup = get_preset_metadata()

    # Layout: 2 small filter columns, 1 main selector
    c_filt1, c_filt2, c_main = st.columns([1, 1, 2])

    # --- 1. Source Filter ---
    # We use session state to remember the filter per slot
    src_key = f"filter_src_{slot['id']}"
    selected_src = c_filt1.selectbox(
        "Source",
        ["All"] + all_sources,
        key=src_key,
        label_visibility="collapsed",
        help="Filter by Vendor",
    )

    # --- 2. Category Filter ---
    # Dynamic options based on Source
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


# Callback to handle preset changes safely
def update_from_preset(slot_id):
    """Callback: Updates slot data and name when preset changes."""
    # Find the specific slot by ID
    slot = next((s for s in st.session_state.pedal_slots if s["id"] == slot_id), None)
    if not slot:
        return

    # Get the new selection from session state
    key = f"preset_select_{slot_id}"
    new_preset = st.session_state.get(key)

    if new_preset:
        # 1. Update BOM Data
        slot["data"] = BOM_PRESETS[new_preset]
        # Force the text area to reflect this new data
        st.session_state[f"text_preset_{slot_id}"] = slot["data"]

        # 2. Update Name (Only if empty or matches previous preset)
        last_loaded_key = slot.get("last_loaded_preset")

        # We compare against the formatted version of the LAST key to see if we should overwrite
        # (i.e. if the user hasn't manually changed it from the last auto-generated name)
        current_name = slot["name"]
        should_update = (
            not current_name
            or current_name == get_clean_name(last_loaded_key)
            or current_name == last_loaded_key
        )

        if should_update:
            clean_name = get_clean_name(new_preset)
            slot["name"] = clean_name
            st.session_state[f"name_{slot_id}"] = clean_name

        # 3. Update Tracking
        slot["last_loaded_preset"] = new_preset


def on_method_change(slot_id):
    """Callback: Handle input method switches (Paste/Upload/Preset)."""
    slot = next((s for s in st.session_state.pedal_slots if s["id"] == slot_id), None)
    if not slot:
        return

    # Get the new method from the widget state
    new_method = st.session_state.get(f"method_{slot_id}")

    # Case: Switch to Paste Text -> Clear Data
    if new_method == "Paste Text":
        slot["data"] = ""
        # Clear the text area widget state to ensure it shows empty
        if f"text_{slot_id}" in st.session_state:
            st.session_state[f"text_{slot_id}"] = ""

    # Case: Switch to Preset -> Load Default
    elif new_method == "Preset":
        first_preset = sorted(list(BOM_PRESETS.keys()))[0]
        slot["data"] = BOM_PRESETS[first_preset]
        slot["last_loaded_preset"] = first_preset

        # Auto-fill name if empty
        if not slot["name"]:
            clean = get_clean_name(first_preset)
            slot["name"] = clean
            # Update the widget key directly so it renders correctly immediately
            st.session_state[f"name_{slot_id}"] = clean

        # Ensure the preset text area is populated
        st.session_state[f"text_preset_{slot_id}"] = slot["data"]

    # Update slot tracking
    slot["method"] = new_method
    slot["last_method"] = new_method


st.divider()
st.subheader("1. Project Config")

# Placeholder Examples
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
        name_key = f"name_{slot['id']}"
        name_kwargs = (
            {"value": slot["name"]} if name_key not in st.session_state else {}
        )

        slot["name"] = c1.text_input(
            f"Project Name #{i + 1}",
            key=name_key,
            placeholder=f"e.g. {PLACEHOLDERS[i % len(PLACEHOLDERS)]}",
            **name_kwargs,
        )

        # Quantity
        slot["count"] = c2.number_input(
            "Qty",
            min_value=1,
            value=slot.get("count", 1),
            key=f"qty_{slot['id']}",
            label_visibility="visible",
        )

        # Input Method
        slot["method"] = c3.radio(
            "Input Method",
            ["Paste Text", "Upload File", "Preset"],
            key=f"method_{slot['id']}",
            horizontal=True,
            label_visibility="collapsed",
            on_change=on_method_change,
            args=(slot["id"],),
        )

        # Remove Button (Top Right)
        if len(st.session_state.pedal_slots) > 1:
            if c4.button("🗑️", key=f"del_{slot['id']}"):
                remove_slot(i)
                st.rerun()

        # Row 2: Data Input (Full Width)
        if slot["method"] == "Paste Text":
            text_key = f"text_{slot['id']}"
            area_kwargs = (
                {"value": slot.get("data", "")}
                if text_key not in st.session_state
                else {}
            )

            slot["data"] = st.text_area(
                "BOM Text",
                height=150,
                key=text_key,
                placeholder="Paste your BOM here...",
                help="Paste raw text like 'R1 10k', 'C1 100n', etc.",
                **area_kwargs,
            )

        elif slot["method"] == "Upload File":
            slot["data"] = st.file_uploader(
                "Upload BOM",
                type=["csv", "pdf"],
                key=f"file_{slot['id']}",
            )

        elif slot["method"] == "Preset":
            # 1. The Selector (Hierarchical)
            render_preset_selector(slot, i)

            # 2. The Read-Only Preview
            text_key = f"text_preset_{slot['id']}"
            area_kwargs = (
                {"value": slot.get("data", "")}
                if text_key not in st.session_state
                else {}
            )

            slot["data"] = st.text_area(
                "Preview",
                height=150,
                key=text_key,
                disabled=True,
                label_visibility="collapsed",
                **area_kwargs,
            )

        st.divider()

st.button("➕ Add Another Pedal", on_click=add_slot)

st.divider()
st.subheader("2. Inventory Check (Optional)")
stock_file = st.file_uploader(
    "📂 Upload Stock CSV", type=["csv"], help="Columns: Category, Part, Qty"
)
st.divider()

if st.button("Generate Master List", type="primary", use_container_width=True):
    inventory: InventoryType = defaultdict(
        lambda: {"qty": 0, "refs": [], "sources": defaultdict(list)}
    )
    stats: StatsDict = {
        "lines_read": 0,
        "parts_found": 0,
        "residuals": [],
        "extracted_title": None,
    }
    # Process Each Slot
    for slot in st.session_state.pedal_slots:
        source = slot["name"] if slot["name"].strip() else "Untitled Project"
        qty_multiplier = slot.get("count", 1)

        # A. Paste Text Mode (and Presets)
        if slot["method"] in ["Paste Text", "Preset"]:
            raw = slot.get("data", "")
            if raw:
                p_inv, p_stats = parse_with_verification([raw], source_name=source)

                # Merge
                merge_inventory(inventory, p_inv, qty_multiplier)

                stats["lines_read"] += p_stats["lines_read"]
                stats["parts_found"] += p_stats["parts_found"]
                stats["residuals"].extend(p_stats["residuals"])

        # B. File Upload Mode
        elif slot["method"] == "Upload File":
            # Cast to UploadedFile so Pylance knows it has .name and .getvalue()
            f = cast(UploadedFile, slot.get("data"))
            if f:
                # CRITICAL: Reset cursor to start.
                # If the file was read in a previous run, the cursor is at the end.
                f.seek(0)

                ext = os.path.splitext(f.name)[1].lower()
                with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
                    tmp.write(f.getvalue())
                    tmp_path = tmp.name

                try:
                    if ext == ".pdf":
                        p_inv, p_stats = parse_pedalpcb_pdf(
                            tmp_path, source_name=source
                        )
                    else:
                        p_inv, p_stats = parse_csv_bom(tmp_path, source_name=source)

                    # Merge
                    merge_inventory(inventory, p_inv, qty_multiplier)

                    stats["lines_read"] += p_stats["lines_read"]
                    stats["parts_found"] += p_stats["parts_found"]
                    stats["residuals"].extend(p_stats["residuals"])
                finally:
                    if os.path.exists(tmp_path):
                        os.remove(tmp_path)

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

    # Validation Logic (Change 3)
    if stats["parts_found"] == 0:
        st.error("❌ No parts found! Check your BOM text or file.")
    else:
        st.toast("Generated Master List!", icon="🎸")

# Main Process
if st.session_state.inventory and st.session_state.stats:
    inventory = copy.deepcopy(st.session_state.inventory)
    stats = cast(StatsDict, st.session_state.stats)

    # 1. Show Stats (Always show to help debug)
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

    # Check for junk
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

    # Explain the columns
    st.info("""
    **📋 List Key:**
    * **Circuit Board:** Components found directly in your uploaded BOM.
    * **Hardware Kit:** Enclosures, Jacks, and Switches auto-added based on your pedal count.
    * **Extras:** Optional useful parts like IC Sockets and SMD adapters.
    """)

    # 2. Build the Shopping List
    final_data = []

    # STEP A: Inject Hardware (Mutates Inventory In-Place)
    calc_pedal_count = sum(
        slot.get("count", 1) for slot in st.session_state.pedal_slots
    )
    # No return value, just mutation
    get_standard_hardware(inventory, calc_pedal_count)

    # STEP B: Process the Unified Inventory
    stock = st.session_state.get("stock")

    # If stock exists, we calculate net needs, otherwise net = gross
    if stock:
        # Calculate Net Needs (Deficit)
        net_inventory = calculate_net_needs(inventory, stock)
        # We iterate the ORIGINAL inventory to show everything,
        # but we pull purchasing math from net_inventory
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
        # Friendly names for the user
        if is_pure_hardware:
            origin = "Hardware Kit"
        elif is_extra:
            origin = "Extras"
        else:
            origin = "Circuit Board"

        # Nerd Economics applies to the NET need (Deficit)
        buy_qty, note = get_buy_details(category, value, net_qty)

        # Append context from Auto-Inject if present
        auto_inject_notes = sources.get("Auto-Inject", [])

        # Check against origin (using the new friendly label "Hardware Kit")
        if auto_inject_notes and origin != "Hardware Kit":
            # This handles "Smart Merge" cases (e.g. LED CLR merged into Resistors)
            # e.g. "🤖 Standard Part: LED CLR"
            formatted_notes = ", ".join(auto_inject_notes)
            note += f" | 🤖 Standard Part: {formatted_notes}"

        spec_type = get_spec_type(category, value)
        search_term = generate_search_term(category, value, spec_type)
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
    st.subheader("🛒 Master List")

    # Dynamic Columns (Change 1)
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
        use_container_width=True,
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

    # CSV Generation
    csv_buf = io.StringIO()

    # Dynamic Fields for CSV to match UI
    fields = [
        "Category",
        "Part",
        "BOM Qty",
        "Buy Qty",
        "Notes",
        "Search Term",
        "Tayda_Link",
        "Origin",
    ]
    if stock:
        fields[3:3] = ["In Stock", "Net Need"]

    writer = csv.DictWriter(csv_buf, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()

    # LOGIC: Conditional Formatting
    if "Excel" in link_format:
        # Transform for Excel
        csv_export_data = []
        for row in final_data:
            export_row = row.copy()
            row_link = export_row.get("Tayda_Link")
            if row_link:
                export_row["Tayda_Link"] = f'=HYPERLINK("{row_link}", "Buy")'
            csv_export_data.append(export_row)
        writer.writerows(csv_export_data)
    else:
        # Standard: Just write the raw data
        writer.writerows(final_data)

    csv_out = csv_buf.getvalue().encode("utf-8-sig")

    # Generate Updated Inventory (The Circular Economy)
    stock_update_buf = io.StringIO()
    # Matches the format expected by parse_user_inventory
    stock_fields = ["Category", "Part", "Qty"]
    stock_writer = csv.DictWriter(stock_update_buf, fieldnames=stock_fields)
    stock_writer.writeheader()

    for row in final_data:
        # Logic: New Stock = (Old Stock + Buy Qty) - Used Qty
        # Note: We must use the values from the final_data row we just calculated

        # safely get numbers, defaulting to 0
        current_stock = cast(int, row.get("In Stock", 0))
        buy_qty = cast(int, row.get("Buy Qty", 0))
        used_qty = cast(int, row.get("BOM Qty", 0))

        # The Math
        new_qty = (current_stock + buy_qty) - used_qty

        # Only save if we actually have stock left
        if new_qty > 0:
            stock_writer.writerow(
                {"Category": row["Category"], "Part": row["Part"], "Qty": new_qty}
            )

    stock_update_csv = stock_update_buf.getvalue().encode("utf-8-sig")

    c_dwn1, c_dwn2 = st.columns(2)

    with c_dwn1:
        st.download_button(
            "🛒 Download Shopping List",
            data=csv_out,
            file_name="pedal_shopping_list.csv",
            mime="text/csv",
            type="primary",
        )

    with c_dwn2:
        st.download_button(
            "📦 Download Updated Inventory",
            data=stock_update_csv,
            file_name="my_inventory_updated.csv",
            mime="text/csv",
            help="Upload this file next time! It contains your stock levels minus what you used here, plus what you bought.",
        )

st.divider()

if "feedback_submitted" not in st.session_state:
    st.session_state.feedback_submitted = False

with st.expander("🐞 Found a bug? / 📢 Feedback"):
    # Check if they have already submitted
    if st.session_state.feedback_submitted:
        st.success("Thanks for your feedback! Message received.")
    else:
        st.caption("Let me know if I missed a component or if the app exploded.")

        with st.form("feedback_form"):
            col1, col2 = st.columns([1, 4])
            with col1:
                rating = st.select_slider(
                    "Rating", options=["😡", "😕", "😐", "🙂", "🤩"], value="🤩"
                )
            with col2:
                comment = st.text_area("Details", height=80, placeholder="Details...")

            submitted = st.form_submit_button("Send Feedback")

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
