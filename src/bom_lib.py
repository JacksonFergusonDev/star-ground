import re
import csv
from collections import defaultdict

# Chip substitution recommendations
# Keys are the chips found in BOM, values are fun alternatives to try.
IC_ALTS = {
    # Dual Op-Amps
    "TL072": [("JRC4558", "Vintage warmth"), ("NE5532", "Low noise/Hi-Fi")],
    "JRC4558": [("TL072", "Modern/Clear"), ("NE5532", "Hi-Fi")],
    # Single Op-Amps (RAT style)
    "LM308": [("OP07", "Modern stable equiv"), ("TL071", "High fidelity (changes tone)")],
    "OP07": [("LM308", "Vintage original"), ("TL071", "Bright mod")]
}

def categorize_part(ref, val):
    """
    Decides what a part is based on its Ref (Name) and Value.
    """
    ref = ref.upper().strip()
    val = val.upper().strip()

    # 1. Known Potentiometer Labels
    # If the ref matches these, it's definitely a knob.
    pot_labels = {
        'POT', 'TRIM', 'VR', 'VOL', 'VOLUME', 'TONE', 'GAIN', 'DRIVE', 'DIST', 
        'FUZZ', 'DIRT', 'LEVEL', 'MIX', 'BLEND', 'DRY', 'WET', 'SPEED', 'RATE', 
        'DEPTH', 'INTENSITY', 'WIDTH', 'DECAY', 'ATTACK', 'RELEASE', 'SUSTAIN', 
        'COMP', 'THRESH', 'TREBLE', 'BASS', 'MID', 'MIDS', 'PRESENCE', 'CONTOUR', 
        'EQ', 'BODY', 'BIAS', 'BOOST', 'MASTER', 'PRE', 'POST', 'FILTER', 'RANGE', 'SENS'
    }

    # 2. Standard Component Prefixes
    # Note: 'P' or 'POT' are handled above.
    valid_prefixes = ('R', 'C', 'D', 'Q', 'U', 'IC', 'SW', 'OP', 'TL')
    
    # 3. Taper Check (The "Smart" Check)
    # Looks for "B100k", "10k-A" to identify pots by value.
    is_pot_value = False
    if re.search(r'[0-9]+.*[ABCWG]$', val) or re.search(r'^[ABCWG][0-9]+', val):
        is_pot_value = True

    # Validity Check
    is_valid = (
        any(ref.startswith(p) for p in valid_prefixes) or 
        ref in pot_labels or 
        any(ref.startswith(l) for l in pot_labels) or
        is_pot_value
    )

    if not is_valid:
        return None, None, None

    # Classification
    category = "Unknown"
    injection = None
    
    # Check Pots first (avoids collisions like 'RANGE' starting with 'R')
    if ref in pot_labels or any(ref.startswith(l) for l in pot_labels) or is_pot_value:
        category = "Potentiometers"
        
    elif ref.startswith('R') and not ref.startswith('RANGE'): category = "Resistors"
    elif ref.startswith('C'): category = "Capacitors"
    elif ref.startswith('D'): category = "Diodes"
    elif ref.startswith('Q'): category = "Transistors"
    elif ref.startswith('SW'): category = "Switches"
    
    # ICs -> Inject Socket
    elif ref.startswith(('U', 'IC', 'OP', 'TL')): 
        category = "ICs"
        injection = "Hardware/Misc | 8_PIN_DIP_SOCKET"

    # SMD Substitution Logic (J201/2N5457 -> SMD Adapter)
    if "2N5457" in val:
        val = "MMBF5457"
        injection = "Hardware/Misc | SMD_ADAPTER_BOARD"
    elif "MMBF5457" in val:
        injection = "Hardware/Misc | SMD_ADAPTER_BOARD"
        
    return category, val, injection

def parse_with_verification(bom_list):
    """
    Parses raw text BOMs. Handles commas and ranges (R1-R4).
    """
    inventory = defaultdict(int)
    stats = { "lines_read": 0, "parts_found": 0, "residuals": [] }

    # Regex: Matches Ref + Separator + Value.
    # Separator can be whitespace or comma.
    pattern = re.compile(r"^([a-zA-Z0-9_\-]+)[\s,]+([0-9a-zA-Z\.\-\/]+).*")

    for raw_text in bom_list:
        lines = raw_text.strip().split('\n')
        pcb_mode = False
        
        for line in lines:
            line = line.strip()
            if not line: continue
            stats["lines_read"] += 1
            
            # Catch "PCB" header lines
            if line.upper() == "PCB":
                pcb_mode = True
                continue 
            if pcb_mode:
                clean_name = re.sub(r'^PCB\s+', '', line, flags=re.IGNORECASE).strip()
                inventory[f"PCB | {clean_name}"] += 1
                stats["parts_found"] += 1
                pcb_mode = False
                continue

            match = pattern.match(line)
            success = False
            
            if match:
                ref_raw = match.group(1).upper()
                val_raw = match.group(2).upper()
                
                refs = []

                # Handle Ranges (R1-R5)
                if '-' in ref_raw:
                    try:
                        # Matches R1-R4 or R1-4
                        m = re.match(r"([A-Z]+)(\d+)-([A-Z]+)?(\d+)", ref_raw)
                        if m:
                            prefix = m.group(1)
                            start = int(m.group(2))
                            end = int(m.group(4))
                            
                            if (end - start) < 50: # Sanity check
                                for i in range(start, end + 1):
                                    refs.append(f"{prefix}{i}")
                            else:
                                refs.append(ref_raw)
                        else:
                            refs.append(ref_raw)
                    except:
                        refs.append(ref_raw)
                else:
                    refs.append(ref_raw)

                # Process all refs found on this line
                line_has_part = False
                for r in refs:
                    cat, val, inj = categorize_part(r, val_raw)

                    if cat:
                        inventory[f"{cat} | {val}"] += 1
                        if inj: inventory[inj] += 1
                        stats["parts_found"] += 1
                        line_has_part = True
                
                if line_has_part:
                    success = True

            if not success:
                stats["residuals"].append(line)

    return inventory, stats

def parse_csv_bom(filepath):
    """
    Parses a CSV file. Expects columns vaguely named 'Ref' and 'Value'.
    """
    inventory = defaultdict(int)
    stats = {"lines_read": 0, "parts_found": 0, "residuals": []}
    
    with open(filepath, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f) 
        for row in reader:
            stats["lines_read"] += 1
            # Lowercase keys to find columns easier
            row_clean = {k.lower(): v for k, v in row.items() if k}
            
            ref = row_clean.get("ref") or row_clean.get("designator") or row_clean.get("part")
            val = row_clean.get("value") or row_clean.get("val") or row_clean.get("description")
            
            success = False
            if ref and val:
                cat, clean_val, inj = categorize_part(ref, val)
                if cat:
                    inventory[f"{cat} | {clean_val}"] += 1
                    if inj: inventory[inj] += 1
                    stats["parts_found"] += 1
                    success = True
            
            if not success:
                stats["residuals"].append(str(row))
                 
    return inventory, stats

def get_residual_report(stats):
    """Returns lines that look like parts but were skipped."""
    safe_words = ["RESISTORS", "CAPACITORS", "TRANSISTORS", "DIODES", "POTENTIOMETERS", "PCB", "COMPONENT LIST", "SOCKET"]
    suspicious = []
    
    for line in stats["residuals"]:
        upper = line.upper()
        is_header = any(w in upper for w in safe_words)
        
        # If it's not a header but has numbers, it might be a missed part
        if not is_header and any(c.isdigit() for c in line):
            suspicious.append(line)
            
    return suspicious

def get_injection_warnings(inventory):
    """Warns user if we made assumptions (SMD adapters, Sockets)."""
    warnings = []
    if inventory.get("Hardware/Misc | SMD_ADAPTER_BOARD", 0) > 0:
        warnings.append("⚠️  SMD ADAPTERS: Added for MMBF5457. Check if your PCB has SOT-23 pads first.")
    if inventory.get("Hardware/Misc | 8_PIN_DIP_SOCKET", 0) > 0:
        warnings.append("ℹ️  IC SOCKETS: Added sockets for chips. Optional but recommended.")
    return warnings

def get_buy_details(category, val, count):
    """Applies 'Nerd Economics' to calculate buy quantity."""
    buy = count
    note = ""
    
    if category == "Resistors": 
        buy = max(10, count + 5)
    elif category == "Capacitors":
        if "100N" in val or "0.1U" in val: 
            buy = count + 10
            note = "Power filtering (buy bulk)."
        else: 
            buy = count + 3
    elif category == "Diodes": 
        buy = max(10, count + 5)
    elif category == "Transistors":
        if "MMBF" in val: 
            buy = count + 5
            note = "SMD SUB for 2N5457. Needs adapter!"
        else: 
            buy = count + 3
            
    elif category == "ICs": 
        buy = count + 1
        note = "Audio Chip (Socket added)"
        # Suggest mods
        clean = re.sub(r'(CP|CN|P|N)$', '', val)
        if clean in IC_ALTS:
            alts = IC_ALTS[clean]
            txt = ", ".join([f"{c} ({d})" for c, d in alts])
            note += f" | 💡 TRY: {txt}"
            
    elif category == "Hardware/Misc": 
        if "ADAPTER" in val: 
            buy = count + 4
            note = "[AUTO] Verify PCB pads."
        elif "SOCKET" in val:
            buy = count + 2
            note = "[AUTO] For chip safety."
        else: 
            buy = count + 1
            
    elif category == "PCB":
        note = "Main Board"

    return buy, note

def sort_inventory(inventory):
    """Sorts parts in logical build order."""
    order = ["PCB", "ICs", "Transistors", "Diodes", "Potentiometers", "Switches", "Hardware/Misc", "Capacitors", "Resistors"]
    # Map name to index for sorting
    pmap = {name: i for i, name in enumerate(order)}
    
    def sort_key(item):
        key = item[0] 
        if " | " not in key: return (999, key)
        cat, val = key.split(" | ", 1)
        rank = pmap.get(cat, 100)
        return (rank, val)
        
    return sorted(inventory.items(), key=sort_key)