import csv
import datetime
import io
import os
import tempfile
from collections import defaultdict
from typing import cast
from src.bom_lib import StatsDict, InventoryType

import gspread
import streamlit as st
from google.oauth2.service_account import Credentials

from src.bom_lib import (
    get_buy_details,
    get_residual_report,
    parse_csv_bom,
    parse_with_verification,
    sort_inventory,
    get_standard_hardware,
    get_spec_type,
    generate_search_term,
    generate_tayda_url,
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

st.markdown("### 1. Project Config")
col1, col2 = st.columns([1, 2])
with col1:
    pedal_count = st.number_input(
        "How many pedals are you building?",
        min_value=1,
        value=1,
        help="Multiplies enclosures, jacks, and switches automatically.",
    )

# Setup Tabs
text_tab, csv_tab = st.tabs(["📋 Paste Text", "📂 Upload CSV"])

if "inventory" not in st.session_state:
    st.session_state.inventory = None
if "stats" not in st.session_state:
    st.session_state.stats = None

# Tab 1: Text Paste
with text_tab:
    raw_text = st.text_area("Paste BOM Text Here:", height=300)
    if st.button("Generate Shopping List", type="primary", key="text_submit"):
        if not raw_text:
            st.error("You gotta paste something first.")
        else:
            st.session_state.inventory, st.session_state.stats = (
                parse_with_verification([raw_text])
            )
            st.toast(
                f"Parsed {st.session_state.stats['lines_read']} lines successfully.",
                icon="✅",
            )

# Tab 2: CSV Upload
with csv_tab:
    st.caption("Expects columns like 'Ref' and 'Value'.")
    uploaded_files = st.file_uploader(
        "Upload CSVs", type=["csv"], accept_multiple_files=True
    )

    if st.button("Generate Shopping List", type="primary", key="csv_submit"):
        if not uploaded_files:
            st.error("Upload at least one file.")
        else:
            inventory: InventoryType = defaultdict(int)
            stats: StatsDict = {"lines_read": 0, "parts_found": 0, "residuals": []}

            try:
                for uploaded_file in uploaded_files:
                    with tempfile.NamedTemporaryFile(
                        delete=False, suffix=".csv"
                    ) as tmp:
                        tmp.write(uploaded_file.getvalue())
                        tmp_path = tmp.name

                    try:
                        # Process single file
                        file_inv, file_stats = parse_csv_bom(tmp_path)

                        # Merge Logic: Add this file's signal to the master stack
                        for part, count in file_inv.items():
                            inventory[part] += count

                        stats["lines_read"] += file_stats["lines_read"]
                        stats["parts_found"] += file_stats["parts_found"]
                        stats["residuals"].extend(file_stats["residuals"])

                    finally:
                        # Clean up the temp file immediately
                        if os.path.exists(tmp_path):
                            os.remove(tmp_path)

                st.session_state.inventory = inventory
                st.session_state.stats = stats
                st.toast(f"Parsed {stats['lines_read']} lines successfully.", icon="✅")

            except Exception as e:
                st.error(f"CSV explosion: {e}")

# Main Process
# Main Process
if st.session_state.inventory and st.session_state.stats:
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
    # We do this FIRST so that merged items (like 3.3k resistors)
    # get their counts updated in the inventory before we sort/loop.
    hardware_list = get_standard_hardware(inventory, pedal_count)

    # STEP B: Process the (now updated) Inventory
    sorted_parts = sort_inventory(inventory)

    for part_key, count in sorted_parts:
        if " | " not in part_key:
            continue
        category, value = part_key.split(" | ", 1)

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
        use_container_width=True,
    )

    # 4. Downloads
    # CSV
    csv_buf = io.StringIO()
    # Define all fields including the new ones
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
    writer.writerows(final_data)
    csv_out = csv_buf.getvalue().encode("utf-8-sig")

    st.download_button(
        "Download CSV", data=csv_out, file_name="pedal_parts.csv", mime="text/csv"
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
