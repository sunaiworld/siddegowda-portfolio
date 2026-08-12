import sys
import os
import json

# Add src to python path to import sheet_writer
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))
from sheet_writer import get_gspread_client

def get_cell_format(grid_data, row_idx, col_idx):
    try:
        row_data = grid_data['rowData'][row_idx]
        cell_data = row_data['values'][col_idx]
        
        val = cell_data.get('formattedValue', '')
        fmt = cell_data.get('userEnteredFormat', {})
        efmt = cell_data.get('effectiveFormat', {})
        
        bg = fmt.get('backgroundColor', {})
        ebg = efmt.get('backgroundColor', {})
        bg_hex = f"#{int(bg.get('red', 0)*255):02x}{int(bg.get('green', 0)*255):02x}{int(bg.get('blue', 0)*255):02x}" if bg else "None"
        ebg_hex = f"#{int(ebg.get('red', 0)*255):02x}{int(ebg.get('green', 0)*255):02x}{int(ebg.get('blue', 0)*255):02x}" if ebg else "None"
        
        text_fmt = fmt.get('textFormat', {})
        fg = text_fmt.get('foregroundColor', {})
        fg_hex = f"#{int(fg.get('red', 0)*255):02x}{int(fg.get('green', 0)*255):02x}{int(fg.get('blue', 0)*255):02x}" if fg else "None"
        
        return {
            'value': val,
            'bg_hex': bg_hex,
            'effective_bg': ebg_hex,
            'fg_hex': fg_hex,
            'bold': text_fmt.get('bold', False),
            'number_format': fmt.get('numberFormat', {}).get('pattern', 'None'),
            'h_align': fmt.get('horizontalAlignment', 'None'),
            'v_align': fmt.get('verticalAlignment', 'None'),
            'wrap': fmt.get('wrapStrategy', 'None')
        }
    except Exception as e:
        return {'error': str(e)}

def main():
    print("========== DIVIDENDS LIVE FORMAT DIAGNOSTIC ==========")
    try:
        gc = get_gspread_client()
        sh = gc.open("siddegowda-portfolio")
        print("Authentication: SUCCESS")
    except Exception as e:
        print(f"Authentication: FAILED ({e})")
        return

    # Fetch metadata with grid data for A1:J10
    try:
        params = {
            "includeGridData": True,
            "ranges": ["Dividends!A1:J10", "GITHUB DATA!A1:J10"]
        }
        data = sh.fetch_sheet_metadata(params)
    except Exception as e:
        print(f"Failed to fetch grid data: {e}")
        return

    sheets_data = {}
    for s in data.get('sheets', []):
        title = s['properties']['title']
        sheets_data[title] = s

    div_sheet = sheets_data.get('Dividends')
    gh_sheet = sheets_data.get('GITHUB DATA')

    if not div_sheet:
        print("Dividends sheet not found in response!")
        return

    div_props = div_sheet['properties']
    print(f"\nDividends rows: {div_props['gridProperties']['rowCount']}")
    print(f"Dividends columns: {div_props['gridProperties']['columnCount']}")

    # Get full metadata for conditional formatting rules (needs another call without ranges)
    try:
        full_meta = sh.fetch_sheet_metadata({"includeGridData": False})
        full_div_sheet = next((s for s in full_meta.get('sheets', []) if s['properties']['title'] == 'Dividends'), {})
        cond_formats = full_div_sheet.get('conditionalFormats', [])
    except Exception as e:
        print(f"Failed to fetch full metadata for conditional formats: {e}")
        cond_formats = []

    print(f"\nConditional formatting rules: {len(cond_formats)}")
    for i, rule in enumerate(cond_formats):
        rng = rule.get('ranges', [{}])[0]
        r_type = list(rule.get('booleanRule', {}).get('condition', {}).keys())
        r_type = r_type[0] if r_type else list(rule.get('gradientRule', {}).keys())
        r_type = r_type[0] if isinstance(r_type, list) and r_type else str(r_type)
        print(f"  - Rule {i}: Range R{rng.get('startRowIndex')}C{rng.get('startColumnIndex')}:R{rng.get('endRowIndex')}C{rng.get('endColumnIndex')} | Type: {r_type}")

    print("\nRepresentative cell formatting:")
    cells_to_check = [
        (0, 0, 'A1'), (0, 1, 'B1'), (0, 6, 'G1'), (0, 8, 'I1'), (0, 9, 'J1'),
        (1, 1, 'B2'), (1, 6, 'G2'), (1, 8, 'I2'), (1, 9, 'J2'),
        (2, 1, 'B3'), (2, 6, 'G3'), (2, 8, 'I3'), (2, 9, 'J3'),
        (4, 1, 'B5'), (4, 6, 'G5'), (4, 8, 'I5'), (4, 9, 'J5')
    ]

    div_grid = div_sheet.get('data', [{}])[0]
    
    for r, c, label in cells_to_check:
        fmt = get_cell_format(div_grid, r, c)
        print(f"\n[DIVIDENDS CELL]")
        print(f"{label}")
        if 'error' in fmt:
            print(f"Error reading cell: {fmt['error']}")
            continue
        print(f"Value: {fmt['value']}")
        print(f"Background: {fmt['bg_hex']} (Effective: {fmt['effective_bg']})")
        print(f"Font: {fmt['fg_hex']}")
        print(f"Bold: {fmt['bold']}")
        print(f"Number format: {fmt['number_format']}")
        print(f"Alignment: {fmt['h_align']} / {fmt['v_align']}")
        print(f"Wrap: {fmt['wrap']}")

    print("\nGITHUB DATA comparison:")
    if gh_sheet:
        gh_grid = gh_sheet.get('data', [{}])[0]
        print(f"{'Property':<20} | {'GITHUB DATA':<25} | {'Dividends':<25}")
        print("-" * 75)
        
        gh_a1 = get_cell_format(gh_grid, 0, 0)
        div_a1 = get_cell_format(div_grid, 0, 0)
        print(f"{'Header background':<20} | {gh_a1.get('effective_bg', 'N/A'):<25} | {div_a1.get('effective_bg', 'N/A'):<25}")
        print(f"{'Header font':<20} | {gh_a1.get('fg_hex', 'N/A'):<25} | {div_a1.get('fg_hex', 'N/A'):<25}")
        print(f"{'Header bold':<20} | {str(gh_a1.get('bold', 'N/A')):<25} | {str(div_a1.get('bold', 'N/A')):<25}")
        
        gh_b2 = get_cell_format(gh_grid, 1, 1)
        div_b2 = get_cell_format(div_grid, 1, 1)
        print(f"{'Row 2 background':<20} | {gh_b2.get('effective_bg', 'N/A'):<25} | {div_b2.get('effective_bg', 'N/A'):<25}")
        print(f"{'Row 2 font':<20} | {gh_b2.get('fg_hex', 'N/A'):<25} | {div_b2.get('fg_hex', 'N/A'):<25}")
        
        gh_b3 = get_cell_format(gh_grid, 2, 1)
        div_b3 = get_cell_format(div_grid, 2, 1)
        print(f"{'Row 3 background':<20} | {gh_b3.get('effective_bg', 'N/A'):<25} | {div_b3.get('effective_bg', 'N/A'):<25}")
    else:
        print("GITHUB DATA sheet not found for comparison.")

if __name__ == '__main__':
    main()
