import os
import csv
import sys
from src.bom_lib import parse_with_verification, get_buy_details, get_residual_report, get_injection_warnings, sort_inventory

def load_text_files(folder="data"):
    texts = []
    
    if not os.path.exists(folder):
        print(f"❌ Missing folder: '{folder}'. Create it and drop your BOMs there.")
        sys.exit(1)

    files = [f for f in os.listdir(folder) if f.endswith(".txt")]
    
    if not files:
        print(f"⚠️  No .txt files in '{folder}'.")
        sys.exit(1)

    print(f"📂 Reading {len(files)} files from '{folder}'...")
    
    for filename in files:
        path = os.path.join(folder, filename)
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                texts.append(f.read())
                print(f"   ok: {filename}")
        except Exception as e:
            print(f"   fail: {filename} ({e})")
            
    return texts

if __name__ == "__main__":
    # 1. Ingest
    boms = load_text_files("data")
    inventory, stats = parse_with_verification(boms)
    
    # 2. Verify
    print("\n--- Stats ---")
    print(f"Lines: {stats['lines_read']} | Parts: {stats['parts_found']}")
    
    # Check for junk
    suspicious = get_residual_report(stats)
    if suspicious:
        print(f"\n⚠️  Skipped {len(suspicious)} lines (might be important):")
        for line in suspicious:
            print(f"   ? {line}")
    else:
        print("✅ Parse clean.")
    
    # Check for auto-injections
    warnings = get_injection_warnings(inventory)
    if warnings:
        print("\n💡 Logic Notes:")
        for w in warnings:
            print(f"   {w}")
    
    # 3. Build List
    final_data = []
    sorted_parts = sort_inventory(inventory)

    for part_key, count in sorted_parts:
        if " | " not in part_key: continue
        category, value = part_key.split(" | ", 1)
        
        buy_qty, note = get_buy_details(category, value, count)
        
        final_data.append({
            "Category": category, 
            "Part": value, 
            "BOM_Qty": count, 
            "Buy_Qty": buy_qty, 
            "Notes": note
        })

    # 4. Output
    out_dir = "output"
    os.makedirs(out_dir, exist_ok=True)
    
    csv_path = os.path.join(out_dir, "shopping_list.csv")
    md_path = os.path.join(out_dir, "checklist.md")

    # Save CSV
    try:
        with open(csv_path, "w", newline='', encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["Category", "Part Value", "BOM Qty", "Buy Qty", "Notes"])
            for row in final_data:
                writer.writerow([row["Category"], row["Part"], row["BOM_Qty"], row["Buy_Qty"], row["Notes"]])
        print(f"\n✅ CSV: {csv_path}")
    except PermissionError:
        print(f"\n❌ Error: Close {csv_path} first.")

    # Save Markdown
    try:
        with open(md_path, "w", encoding="utf-8") as f:
            f.write("# Shopping List\n\n")
            f.write("| Category | Part | BOM | Buy | Notes |\n")
            f.write("| --- | --- | :---: | :---: | --- |\n")
            for row in final_data:
                # Only wrap in italics if text exists
                note_str = f"*{row['Notes']}*" if row['Notes'] else ""
                f.write(f"| {row['Category']} | **{row['Part']}** | {row['BOM_Qty']} | **{row['Buy_Qty']}** | {note_str} |\n")
        print(f"✅ MD:  {md_path}")
    except PermissionError:
        print(f"\n❌ Error: Close {md_path} first.")

    print("\nDone.")