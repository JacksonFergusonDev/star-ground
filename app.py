import streamlit as st
import csv
import io
import os
import tempfile
from collections import defaultdict
from src.bom_lib import parse_with_verification, parse_csv_bom, get_buy_details, get_residual_report, get_injection_warnings, sort_inventory

st.set_page_config(page_title="Pedal BOM Manager", page_icon="🎸")

st.title("🎸 Guitar Pedal BOM Manager")
st.markdown("""
**Automate your electronics shopping list.**

Paste your raw component lists (or upload a CSV). 
This tool cleans the data, handles ranges like `R1-R5`, and adds "Nerd Economics" (bulk buying buffers) to your final list.
""")

# Setup Tabs
text_tab, csv_tab = st.tabs(["📋 Paste Text", "📂 Upload CSV"])

inventory = None
stats = None
ready = False

# Tab 1: Text Paste
with text_tab:
    raw_text = st.text_area("Paste BOM Text Here:", height=300)
    if st.button("Generate Shopping List", type="primary", key="text_submit"):
        if not raw_text:
            st.error("You gotta paste something first.")
        else:
            inventory, stats = parse_with_verification([raw_text])
            ready = True

# Tab 2: CSV Upload
with csv_tab:
    st.caption("Expects columns like 'Ref' and 'Value'.")
    uploaded_files = st.file_uploader("Upload CSVs", type=["csv"], accept_multiple_files=True)
    
    if st.button("Generate Shopping List", type="primary", key="csv_submit"):
        if not uploaded_files:
            st.error("Upload at least one file.")
        else:
            inventory = defaultdict(int)
            stats = {"lines_read": 0, "parts_found": 0, "residuals": []}
            
            try:
                for uploaded_file in uploaded_files:
                    with tempfile.NamedTemporaryFile(delete=False, suffix=".csv") as tmp:
                        tmp.write(uploaded_file.getvalue())
                        tmp_path = tmp.name
                    
                    try:
                        # Process single file
                        file_inv, file_stats = parse_csv_bom(tmp_path)
                        
                        # Merge Logic: Add this file's signal to the master stack
                        for part, count in file_inv.items():
                            inventory[part] += count
                        
                        stats['lines_read'] += file_stats['lines_read']
                        stats['parts_found'] += file_stats['parts_found']
                        stats['residuals'].extend(file_stats['residuals'])
                        
                    finally:
                        # Clean up the temp file immediately
                        if os.path.exists(tmp_path):
                            os.remove(tmp_path)
                
                ready = True
                
            except Exception as e:
                st.error(f"CSV explosion: {e}")

# Main Process
if ready and inventory and stats:
    
    st.toast(f"Parsed {stats['lines_read']} lines successfully.", icon='✅')

    # 1. Show Stats
    with st.container():
        c1, c2, c3 = st.columns(3)
        c1.metric("Lines Scanned", stats['lines_read'])
        c2.metric("Parts Found", stats['parts_found'])
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
        
    # Check for auto-injections
    warnings = get_injection_warnings(inventory)
    if warnings:
        # Join the list into one block of text
        warning_msg = "\n\n".join(warnings)
        st.info(f"**💡 Assumptions Made:**\n\n{warning_msg}")

    # 2. Build the Shopping List
    final_data = []
    
    # Sort by 'Nerd Priority'
    sorted_parts = sort_inventory(inventory)
    
    for part_key, count in sorted_parts:
        if " | " not in part_key: continue
        category, value = part_key.split(" | ", 1)
        
        buy_qty, note = get_buy_details(category, value, count)
        
        final_data.append({
            "Category": category, 
            "Part": value, 
            "BOM Qty": count, 
            "Buy Qty": buy_qty, 
            "Notes": note
        })
        
    # 3. Render
    st.subheader("🛒 Master List")
    st.dataframe(final_data, use_container_width=True)
    
    # 4. Downloads
    # CSV
    csv_buf = io.StringIO()
    writer = csv.DictWriter(csv_buf, fieldnames=["Category", "Part", "BOM Qty", "Buy Qty", "Notes"])
    writer.writeheader()
    writer.writerows(final_data)
    csv_out = csv_buf.getvalue()
    
    # Markdown
    md_out = "# Shopping List\n\n| Category | Part | Buy | Notes |\n|---|---|---|---|\n"
    for row in final_data:
        md_out += f"| {row['Category']} | **{row['Part']}** | **{row['Buy Qty']}** | *{row['Notes']}* |\n"

    d1, d2 = st.columns(2)
    
    d1.download_button("Download CSV", data=csv_out, file_name="pedal_parts.csv", mime="text/csv")
    d2.download_button("Download Markdown", data=md_out, file_name="pedal_checklist.md", mime="text/markdown")