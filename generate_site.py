#!/usr/bin/env python3
"""
Static site generator for the receipt archive.

Usage:
  python generate_site.py

Reads receipts.csv, copies all stored receipt files, and writes
site/index.html — a self-contained page ready to deploy to Vercel.

Deploy:
  vercel deploy site/
  -- or --
  drag the site/ folder into the Vercel dashboard
"""

import csv
import shutil
from datetime import datetime
from pathlib import Path

RECEIPTS_DIR = Path("receipts")
CSV_FILE = Path("receipts.csv")
SITE_DIR = Path("site")
SITE_RECEIPTS_DIR = SITE_DIR / "receipts"


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------

def load_receipts() -> list[dict]:
    if not CSV_FILE.exists():
        return []
    with open(CSV_FILE, "r", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    def sort_key(row):
        # Primary: receipt date descending; secondary: processed_date descending
        return (row.get("date", "") or "0000-00-00",
                row.get("processed_date", "") or "")

    rows.sort(key=sort_key, reverse=True)
    return rows


def format_amount(raw: str) -> str:
    if not raw:
        return ""
    # Strip any currency symbols the LLM may have left in
    cleaned = re.sub(r"[^\d.]", "", raw) if raw else ""
    try:
        return f"${float(cleaned):,.2f}"
    except (ValueError, TypeError):
        return raw


def calculate_total(receipts: list[dict]) -> float:
    total = 0.0
    for row in receipts:
        raw = row.get("amount", "") or ""
        cleaned = "".join(c for c in raw if c.isdigit() or c == ".")
        try:
            total += float(cleaned)
        except (ValueError, TypeError):
            pass
    return total


# ---------------------------------------------------------------------------
# HTML building blocks
# ---------------------------------------------------------------------------

def escape_html(text: str) -> str:
    return (
        text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
    )


def build_table_rows(receipts: list[dict]) -> str:
    parts = []
    for row in receipts:
        vendor   = escape_html(row.get("vendor",   "") or "")
        address  = escape_html(row.get("address",  "") or "")
        phone    = escape_html(row.get("phone",    "") or "")
        date     = escape_html(row.get("date",     "") or "")
        amount   = escape_html(format_amount(row.get("amount", "") or ""))
        category = escape_html(row.get("category", "") or "")
        filename = row.get("filename", "")

        # View link
        if filename:
            href = f"receipts/{filename}"
            view_cell = f'<a href="{href}" target="_blank" class="view-btn">View</a>'
        else:
            view_cell = '<span class="na">—</span>'

        # Category badge (skip if empty)
        if category:
            cat_cell = f'<span class="tag">{category}</span>'
        else:
            cat_cell = '<span class="na">—</span>'

        # Empty display values
        vendor_d  = vendor  or "<span class='na'>—</span>"
        address_d = address or "<span class='na'>—</span>"
        phone_d   = phone   or "<span class='na'>—</span>"
        date_d    = date    or "<span class='na'>—</span>"
        amount_d  = f'<span class="amount">{amount}</span>' if amount else "<span class='na'>—</span>"

        parts.append(
            f"      <tr>\n"
            f'        <td data-label="Date">{date_d}</td>\n'
            f'        <td data-label="Vendor">{vendor_d}</td>\n'
            f'        <td data-label="Category">{cat_cell}</td>\n'
            f'        <td data-label="Amount">{amount_d}</td>\n'
            f'        <td data-label="Address">{address_d}</td>\n'
            f'        <td data-label="Phone">{phone_d}</td>\n'
            f'        <td data-label="View">{view_cell}</td>\n'
            f"      </tr>"
        )
    return "\n".join(parts)


def build_category_options(receipts: list[dict]) -> str:
    cats = sorted({r.get("category", "") for r in receipts if r.get("category", "")})
    return "\n".join(
        f'      <option value="{escape_html(c)}">{escape_html(c)}</option>'
        for c in cats
    )


# ---------------------------------------------------------------------------
# Full HTML template
# ---------------------------------------------------------------------------

def build_html(receipts: list[dict]) -> str:
    total_count = len(receipts)
    total_amount = calculate_total(receipts)
    generated = datetime.now().strftime("%B %d, %Y at %I:%M %p")
    table_rows = build_table_rows(receipts)
    category_options = build_category_options(receipts)

    total_fmt = f"${total_amount:,.2f}"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Receipt Archive</title>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

    :root {{
      --bg:           #f4f6f8;
      --surface:      #ffffff;
      --border:       #e2e6ea;
      --primary:      #2563eb;
      --primary-dk:   #1d4ed8;
      --text:         #111827;
      --muted:        #6b7280;
      --accent-bg:    #eff6ff;
      --accent-border:#bfdbfe;
      --tag-bg:       #e0e7ff;
      --tag-text:     #3730a3;
      --green:        #059669;
      --shadow:       0 1px 3px rgba(0,0,0,.08), 0 1px 2px rgba(0,0,0,.04);
    }}

    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
                   "Helvetica Neue", Arial, sans-serif;
      background: var(--bg);
      color: var(--text);
      line-height: 1.5;
      min-height: 100vh;
    }}

    /* ---- Header ---- */
    header {{
      background: var(--surface);
      border-bottom: 1px solid var(--border);
      padding: 1.5rem 2rem;
      box-shadow: var(--shadow);
    }}
    header h1 {{
      font-size: 1.5rem;
      font-weight: 700;
      color: var(--primary);
    }}
    header p.subtitle {{
      font-size: .85rem;
      color: var(--muted);
      margin-top: .2rem;
    }}
    .stats {{
      display: flex;
      gap: 1rem;
      margin-top: 1rem;
      flex-wrap: wrap;
    }}
    .stat {{
      background: var(--accent-bg);
      border: 1px solid var(--accent-border);
      border-radius: 8px;
      padding: .45rem 1rem;
      font-size: .875rem;
      color: var(--text);
    }}
    .stat strong {{ color: var(--primary); }}

    /* ---- Main ---- */
    main {{
      max-width: 1280px;
      margin: 2rem auto;
      padding: 0 1.5rem;
    }}

    /* ---- Controls ---- */
    .controls {{
      display: flex;
      gap: .75rem;
      margin-bottom: 1rem;
      flex-wrap: wrap;
      align-items: center;
    }}
    .search-input {{
      flex: 1;
      min-width: 200px;
      padding: .5rem .875rem;
      border: 1px solid var(--border);
      border-radius: 6px;
      font-size: .9rem;
      background: var(--surface);
      color: var(--text);
      outline: none;
      transition: border-color .15s, box-shadow .15s;
    }}
    .search-input:focus {{
      border-color: var(--primary);
      box-shadow: 0 0 0 3px rgba(37,99,235,.12);
    }}
    .cat-select {{
      padding: .5rem .875rem;
      border: 1px solid var(--border);
      border-radius: 6px;
      font-size: .9rem;
      background: var(--surface);
      color: var(--text);
      cursor: pointer;
      outline: none;
    }}
    .cat-select:focus {{ border-color: var(--primary); }}
    .result-count {{
      font-size: .85rem;
      color: var(--muted);
      white-space: nowrap;
    }}

    /* ---- Table ---- */
    .table-card {{
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 10px;
      overflow: hidden;
      box-shadow: var(--shadow);
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: .875rem;
    }}
    thead tr {{
      background: var(--accent-bg);
      border-bottom: 2px solid var(--border);
    }}
    th {{
      padding: .7rem 1rem;
      text-align: left;
      font-size: .75rem;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: .05em;
      color: var(--muted);
      white-space: nowrap;
    }}
    tbody tr {{
      border-bottom: 1px solid var(--border);
      transition: background .1s;
    }}
    tbody tr:last-child {{ border-bottom: none; }}
    tbody tr:hover {{ background: #f9fafb; }}
    td {{
      padding: .7rem 1rem;
      vertical-align: middle;
    }}
    .amount {{ font-weight: 600; color: var(--green); white-space: nowrap; }}
    .tag {{
      display: inline-block;
      background: var(--tag-bg);
      color: var(--tag-text);
      border-radius: 4px;
      padding: .15rem .5rem;
      font-size: .75rem;
      font-weight: 500;
    }}
    .view-btn {{
      display: inline-block;
      background: var(--primary);
      color: #fff;
      padding: .3rem .7rem;
      border-radius: 5px;
      text-decoration: none;
      font-size: .78rem;
      font-weight: 500;
      white-space: nowrap;
      transition: background .15s;
    }}
    .view-btn:hover {{ background: var(--primary-dk); }}
    .na {{ color: var(--muted); }}

    /* ---- No results ---- */
    .no-results {{
      display: none;
      text-align: center;
      padding: 3rem 1rem;
      color: var(--muted);
      font-size: .95rem;
    }}
    .hidden {{ display: none !important; }}

    /* ---- Footer ---- */
    footer {{
      text-align: center;
      padding: 2rem 1rem;
      font-size: .78rem;
      color: var(--muted);
    }}

    /* ---- Mobile responsive ---- */
    @media (max-width: 720px) {{
      header {{ padding: 1rem; }}
      main {{ padding: 0 .75rem; }}
      table, thead, tbody, th, td, tr {{ display: block; }}
      thead tr {{ display: none; }}
      tbody tr {{
        margin-bottom: .75rem;
        border: 1px solid var(--border);
        border-radius: 8px;
        padding: .75rem;
      }}
      td {{
        display: flex;
        justify-content: space-between;
        align-items: center;
        border: none;
        padding: .3rem 0;
        font-size: .85rem;
      }}
      td::before {{
        content: attr(data-label);
        font-weight: 600;
        font-size: .72rem;
        text-transform: uppercase;
        letter-spacing: .04em;
        color: var(--muted);
        flex-shrink: 0;
        margin-right: .5rem;
      }}
    }}
  </style>
</head>
<body>

<header>
  <h1>Receipt Archive</h1>
  <p class="subtitle">Generated {generated}</p>
  <div class="stats">
    <div class="stat"><strong>{total_count}</strong> receipts stored</div>
    <div class="stat"><strong>{total_fmt}</strong> total tracked spending</div>
  </div>
</header>

<main>
  <div class="controls">
    <input
      type="search"
      id="searchInput"
      class="search-input"
      placeholder="Search by vendor, date, or category…"
      oninput="filterTable()"
      aria-label="Search receipts"
    >
    <select id="catFilter" class="cat-select" onchange="filterTable()" aria-label="Filter by category">
      <option value="">All categories</option>
{category_options}
    </select>
    <span class="result-count" id="resultCount" aria-live="polite">{total_count} receipts</span>
  </div>

  <div class="table-card">
    <table id="receiptsTable" aria-label="Receipts">
      <thead>
        <tr>
          <th>Date</th>
          <th>Vendor</th>
          <th>Category</th>
          <th>Amount</th>
          <th>Address</th>
          <th>Phone</th>
          <th>View</th>
        </tr>
      </thead>
      <tbody id="tableBody">
{table_rows}
      </tbody>
    </table>
    <div class="no-results" id="noResults" role="status">
      No receipts match your search.
    </div>
  </div>
</main>

<footer>Receipt Archive &mdash; {total_count} receipts</footer>

<script>
  function filterTable() {{
    var query    = document.getElementById('searchInput').value.toLowerCase();
    var category = document.getElementById('catFilter').value.toLowerCase();
    var rows     = document.querySelectorAll('#tableBody tr');
    var visible  = 0;

    rows.forEach(function(row) {{
      var text    = row.textContent.toLowerCase();
      var tagEl   = row.querySelector('.tag');
      var rowCat  = tagEl ? tagEl.textContent.toLowerCase() : '';

      var matchQ  = !query    || text.includes(query);
      var matchC  = !category || rowCat === category;

      if (matchQ && matchC) {{
        row.classList.remove('hidden');
        visible++;
      }} else {{
        row.classList.add('hidden');
      }}
    }});

    document.getElementById('resultCount').textContent =
      visible + ' receipt' + (visible !== 1 ? 's' : '');
    document.getElementById('noResults').style.display =
      visible === 0 ? 'block' : 'none';
  }}
</script>

</body>
</html>
"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

import re  # needed by format_amount — imported here to keep top of file clean


def main():
    print("Generating receipt website…")

    # Recreate site output directories fresh each run
    if SITE_RECEIPTS_DIR.exists():
        shutil.rmtree(SITE_RECEIPTS_DIR)
    SITE_DIR.mkdir(exist_ok=True)
    SITE_RECEIPTS_DIR.mkdir()

    receipts = load_receipts()
    print(f"Found {len(receipts)} receipts in {CSV_FILE}")

    # Copy receipt files
    copied = missing = 0
    for row in receipts:
        fname = row.get("filename", "")
        if fname:
            src = RECEIPTS_DIR / fname
            if src.exists():
                shutil.copy2(src, SITE_RECEIPTS_DIR / fname)
                copied += 1
            else:
                print(f"  Warning: file not found — {src}")
                missing += 1

    print(f"Copied {copied} receipt files to {SITE_RECEIPTS_DIR}/")
    if missing:
        print(f"Warning: {missing} files were missing from {RECEIPTS_DIR}/")

    # Generate HTML
    html = build_html(receipts)
    index = SITE_DIR / "index.html"
    index.write_text(html, encoding="utf-8")
    print(f"Generated: {index}")

    # Write vercel.json so PDFs open correctly in-browser
    vercel_json = """{
  "headers": [
    {
      "source": "/receipts/(.*\\\\.pdf)",
      "headers": [
        { "key": "Content-Type",        "value": "application/pdf" },
        { "key": "Content-Disposition", "value": "inline" }
      ]
    }
  ]
}
"""
    (SITE_DIR / "vercel.json").write_text(vercel_json, encoding="utf-8")
    print(f"Generated: {SITE_DIR / 'vercel.json'}")

    total = calculate_total(receipts)
    print(f"\nDone!  Deploy the '{SITE_DIR}/' folder to Vercel.")
    print(f"  Receipts : {len(receipts)}")
    print(f"  Tracked  : ${total:,.2f}")


if __name__ == "__main__":
    main()
