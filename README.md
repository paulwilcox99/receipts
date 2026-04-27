# Receipt Archive

Store and browse business receipts. Receipts are scanned by GPT-4o to extract vendor, date, amount, and more, then browsable via a static website you can deploy to Vercel.

## Requirements

- Python 3.10+
- An [OpenAI API key](https://platform.openai.com/api-keys)
- For scanned PDFs only: `poppler` system package

## Installation

```bash
# Clone or download this repo, then create and activate a virtual environment:
python3 -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate

pip install -r requirements.txt

# Copy the example env file and add your OpenAI key
cp .env.example .env
```

Edit `.env`:
```
OPENAI_API_KEY=sk-...
```

**Scanned PDF support** (optional — only needed if your PDFs don't contain selectable text):
```bash
# Ubuntu/Debian
sudo apt install poppler-utils

# macOS
brew install poppler
```

> **Each session:** activate the venv before running any scripts:
> ```bash
> source .venv/bin/activate      # Windows: .venv\Scripts\activate
> ```

## Adding Receipts

```bash
python add_receipt.py <file_or_url> [--category CATEGORY]
```

**Supported input types:**

| Type | Extensions |
|------|-----------|
| Image | `.jpg` `.jpeg` `.png` `.gif` `.webp` |
| PDF | `.pdf` |
| Text | `.txt` |
| HTML | `.html` `.htm` |
| URL | `http://…` or `https://…` |

**Examples:**

```bash
# Add an image receipt
python add_receipt.py receipt.jpg

# Add a PDF with a category tag
python add_receipt.py invoice.pdf --category travel

# Add a receipt from a URL
python add_receipt.py https://example.com/receipt --category meals

# Re-run extraction on an already-stored receipt
python add_receipt.py receipt.jpg --reprocess
```

Each receipt is:
1. Copied to `receipts/` with a timestamped filename
2. Scanned by GPT-4o to extract vendor, address, phone, date, and amount
3. Appended as a row in `receipts.csv`

Duplicate receipts (same original filename or URL) are detected and skipped unless you pass `--reprocess`.

## Generating the Website

```bash
python generate_site.py
```

This reads `receipts.csv`, copies all receipt files, and writes a self-contained `site/index.html`. The site includes:

- Receipts sorted newest-first
- Live search by vendor, date, or category
- Category filter dropdown
- Total tracked spending summary
- Direct links to view each receipt file

## Deploying to Vercel

```bash
vercel deploy site/
```

Or drag the `site/` folder into the [Vercel dashboard](https://vercel.com).

## File Layout

```
receipts/          # stored receipt files (timestamped copies)
receipts.csv       # extracted metadata for all receipts
site/              # generated website — deploy this folder
add_receipt.py     # ingestion script
generate_site.py   # site generator
.env               # your OpenAI API key (not committed)
```
