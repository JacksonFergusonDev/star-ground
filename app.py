import csv
import datetime
import io
import os
import uuid
import tempfile
from collections import defaultdict
from typing import cast, List, Dict, Any
from streamlit.runtime.uploaded_file_manager import UploadedFile
import gspread
import streamlit as st
from google.oauth2.service_account import Credentials

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
)


def save_feedback(rating, text):
    """Authenticates with Secrets and appends row to Google Sheets."""
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]

    # Load credentials from Streamlit secrets
    creds_dict = st.secrets["gcp_service_account"]
    creds = Credentials.from_service_account_info(creds_dict, scopes=scope)
    client = gspread.authorize(creds)

    # Open the Sheet
    sheet = client.open("Pedal BOM Feedback").sheet1

    # Append timestamp, rating, and comment
    row = [str(datetime.datetime.now()), rating, text]
    sheet.append_row(row)


st.set_page_config(page_title="Pedal BOM Manager", page_icon="🎸")

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
        {"id": str(uuid.uuid4()), "name": "My Pedal Project", "method": "Paste Text"}
    ]
    st.session_state.pedal_slots = init_slots


def add_slot():
    st.session_state.pedal_slots.append(
        {"id": str(uuid.uuid4()), "name": "", "method": "Paste Text"}
    )


def remove_slot(idx):
    st.session_state.pedal_slots.pop(idx)


st.divider()
st.subheader("1. Project Config")

# Dynamic Slot UI
for i, slot in enumerate(st.session_state.pedal_slots):
    with st.container():
        c1, c2, c3, c4, c5 = st.columns([3, 1, 2, 4, 1])

        # Project Name
        slot["name"] = c1.text_input(
            f"Project Name #{i + 1}",
            value=slot["name"],
            key=f"name_{slot['id']}",
            placeholder="e.g. Big Muff",
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
            ["Paste Text", "Upload File"],
            key=f"method_{slot['id']}",
            horizontal=True,
            label_visibility="collapsed",
        )

        # Data Input
        if slot["method"] == "Paste Text":
            slot["data"] = c4.text_area(
                "BOM Text",
                height=100,
                key=f"text_{slot['id']}",
                label_visibility="collapsed",
                placeholder="Paste your BOM here...",
            )
        else:
            slot["data"] = c4.file_uploader(
                "Upload BOM",
                type=["csv", "pdf"],
                key=f"file_{slot['id']}",
                label_visibility="collapsed",
            )

        # Remove Button
        if len(st.session_state.pedal_slots) > 1:
            if c5.button("🗑️", key=f"del_{slot['id']}"):
                remove_slot(i)
                st.rerun()

st.button("➕ Add Another Pedal", on_click=add_slot)

st.divider()

if st.button("Generate Master List", type="primary", use_container_width=True):
    inventory: InventoryType = defaultdict(
        lambda: {"qty": 0, "refs": [], "sources": defaultdict(list)}
    )
    stats: StatsDict = {"lines_read": 0, "parts_found": 0, "residuals": []}

    # Process Each Slot
    for slot in st.session_state.pedal_slots:
        source = slot["name"] if slot["name"].strip() else "Untitled Project"
        qty_multiplier = slot.get("count", 1)

        # A. Paste Text Mode
        if slot["method"] == "Paste Text":
            raw = slot.get("data", "")
            if raw:
                p_inv, p_stats = parse_with_verification([raw], source_name=source)

                # Merge
                for key, data in p_inv.items():
                    # Multiply quantity by the slot's pedal count
                    inventory[key]["qty"] += data["qty"] * qty_multiplier
                    inventory[key]["refs"].extend(data["refs"])
                    for src, refs in data["sources"].items():
                        inventory[key]["sources"][src].extend(refs)

                stats["lines_read"] += p_stats["lines_read"]
                stats["parts_found"] += p_stats["parts_found"]
                stats["residuals"].extend(p_stats["residuals"])

        # B. File Upload Mode
        elif slot["method"] == "Upload File":
            # Cast to UploadedFile so Pylance knows it has .name and .getvalue()
            f = cast(UploadedFile, slot.get("data"))
            if f:
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
                    for key, data in p_inv.items():
                        # Multiply quantity by the slot's pedal count
                        inventory[key]["qty"] += data["qty"] * qty_multiplier
                        inventory[key]["refs"].extend(data["refs"])
                        for src, refs in data["sources"].items():
                            inventory[key]["sources"][src].extend(refs)

                    stats["lines_read"] += p_stats["lines_read"]
                    stats["parts_found"] += p_stats["parts_found"]
                    stats["residuals"].extend(p_stats["residuals"])
                finally:
                    if os.path.exists(tmp_path):
                        os.remove(tmp_path)

    st.session_state.inventory = inventory
    st.session_state.stats = stats
    st.toast("Generated Master List!", icon="🎸")

# Main Process
if st.session_state.inventory:
    inventory = cast(InventoryType, st.session_state.inventory)
    stats = cast(StatsDict, st.session_state.stats)

    # 1. Show Stats
    with st.container():
        c1, c2, c3 = st.columns(3)
        c1.metric("Lines Scanned", stats["lines_read"])
        c2.metric("Parts Found", stats["parts_found"])
        unique_parts = len(sort_inventory(inventory))
        c3.metric("Unique SKUs", unique_parts)

    st.divider()

    # Check for junk
    suspicious = get_residual_report(stats)
    if suspicious:
        st.warning(f"⚠️ Skipped {len(suspicious)} lines that looked important:")
        with st.expander("Show ignored lines"):
            for line in suspicious:
                st.code(line)
    else:
        st.success("✅ Clean parse. No weird leftovers.")

    # Explain the sections
    st.info("""
    **📋 List Key:**
    * **Parsed BOM:** Components found directly in your text/CSV.
    * **Recommended Extras:** IC Sockets, SMD adapters, and optional build aids.
    * **Missing/Critical:** Essential hardware (Enclosures, Jacks, Switches) auto-injected based on your Pedal Count.
    """)

    # 2. Build the Shopping List
    final_data = []

    # STEP A: Inject Hardware & Smart Merge
    # Calculate total pedal count by summing the Qty of all slots
    calc_pedal_count = sum(
        slot.get("count", 1) for slot in st.session_state.pedal_slots
    )
    hardware_list = get_standard_hardware(inventory, calc_pedal_count)

    # STEP B: Process the (now updated) Inventory
    sorted_parts = sort_inventory(inventory)

    for part_key, item in sorted_parts:
        if " | " not in part_key:
            continue
        category, value = part_key.split(" | ", 1)
        count = item["qty"]

        # Determine Section
        section = "Parsed BOM"
        # Move auto-injected sockets/adapters to Extras
        if "SOCKET" in value or "ADAPTER" in value:
            section = "Recommended Extras"

        buy_qty, note = get_buy_details(category, value, count)

        spec_type = get_spec_type(category, value)
        search_term = generate_search_term(category, value, spec_type)
        url = generate_tayda_url(search_term)

        final_data.append(
            {
                "Section": section,
                "Category": category,
                "Part": value,
                "BOM Qty": count,
                "Buy Qty": buy_qty,
                "Notes": note,
                "Search Term": search_term,
                "Tayda_Link": url,
            }
        )

    # STEP C: Append the Missing Hardware List
    final_data.extend(hardware_list)

    # STEP D: Group by Section
    section_map = {"Parsed BOM": 0, "Recommended Extras": 1, "Missing/Critical": 2}

    final_data.sort(key=lambda row: section_map.get(str(row["Section"]), 99))

    # 3. Render
    st.subheader("🛒 Master List")

    # Configure the dataframe to show Section first
    st.dataframe(
        final_data,
        column_order=[
            "Section",
            "Category",
            "Part",
            "BOM Qty",
            "Buy Qty",
            "Notes",
            "Tayda_Link",
        ],
        column_config={
            "Tayda_Link": st.column_config.LinkColumn(
                "Buy Link",  # Column Header
                display_text="🔍 Buy",  # Cell Text (Hides the URL)
                help="Search on Tayda Electronics",
            ),
        },
        use_container_width=True,
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
    fields = [
        "Section",
        "Category",
        "Part",
        "BOM Qty",
        "Buy Qty",
        "Notes",
        "Search Term",
        "Tayda_Link",
    ]
    writer = csv.DictWriter(csv_buf, fieldnames=fields)
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

    st.download_button(
        "Download CSV",
        data=csv_out,
        file_name="pedal_parts.csv",
        mime="text/csv",
        type="primary",
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
