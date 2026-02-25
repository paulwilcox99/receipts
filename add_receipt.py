#!/usr/bin/env python3
"""
Receipt ingestion script.

Usage:
  python add_receipt.py <file_or_url> [--category CATEGORY] [--reprocess]

Supported inputs:
  Images : .jpg .jpeg .png .gif .webp
  PDF    : .pdf  (text-based or scanned)
  Text   : .txt
  HTML   : .html .htm
  URL    : https://... or http://...

The receipt is copied to the receipts/ directory, scanned by GPT-4o,
and a row is appended to receipts.csv.

Requirements:
  pip install -r requirements.txt
  cp .env.example .env  # add your OPENAI_API_KEY

PDF image fallback requires poppler:
  Ubuntu/Debian: sudo apt install poppler-utils
  macOS:         brew install poppler
"""

import argparse
import base64
import csv
import io
import json
import mimetypes
import os
import re
import shutil
import sys
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from openai import OpenAI
from PIL import Image

load_dotenv()

# ---------------------------------------------------------------------------
# Paths and constants
# ---------------------------------------------------------------------------

RECEIPTS_DIR = Path("receipts")
CSV_FILE = Path("receipts.csv")

CSV_COLUMNS = [
    "id",
    "filename",
    "original_filename",
    "vendor",
    "address",
    "phone",
    "date",
    "amount",
    "category",
    "file_type",
    "processed_date",
]

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
MAX_IMAGE_BYTES = 19 * 1024 * 1024  # stay under 20 MB OpenAI limit

EXTRACTION_PROMPT = (
    "You are analyzing a receipt or invoice image/document. "
    "Extract the following fields and return them as a JSON object with exactly "
    "these keys: vendor, address, phone, date, amount.\n\n"
    "Rules:\n"
    "- vendor: business or merchant name\n"
    "- address: full mailing address if present, otherwise empty string\n"
    "- phone: phone number if present, otherwise empty string\n"
    "- date: receipt/invoice date in YYYY-MM-DD format, otherwise empty string\n"
    "- amount: total charged amount as a plain number with 2 decimal places "
    "(no currency symbols, no commas), otherwise empty string\n\n"
    "Return only valid JSON and nothing else."
)


# ---------------------------------------------------------------------------
# CSV helpers
# ---------------------------------------------------------------------------

def ensure_dirs():
    RECEIPTS_DIR.mkdir(exist_ok=True)


def ensure_csv():
    if not CSV_FILE.exists():
        with open(CSV_FILE, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
            writer.writeheader()


def load_existing_originals() -> set:
    """Return set of original_filenames already in the CSV (for dedup)."""
    if not CSV_FILE.exists():
        return set()
    with open(CSV_FILE, "r", newline="", encoding="utf-8") as f:
        return {row["original_filename"] for row in csv.DictReader(f)}


def append_to_csv(row: dict):
    file_exists = CSV_FILE.exists()
    with open(CSV_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)


def update_csv_row(receipt_id: str, extracted: dict):
    """Overwrite extracted fields for an existing row identified by id."""
    rows = []
    with open(CSV_FILE, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row["id"] == receipt_id:
                row.update(extracted)
                row["processed_date"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            rows.append(row)
    with open(CSV_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


# ---------------------------------------------------------------------------
# Filename helpers
# ---------------------------------------------------------------------------

def sanitize_stem(name: str, max_len: int = 40) -> str:
    return re.sub(r"[^\w\-]", "_", name)[:max_len]


def generate_stored_filename(original_name: str) -> str:
    """Return a timestamped unique filename preserving the original extension."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    p = Path(original_name)
    stem = sanitize_stem(p.stem)
    suffix = p.suffix or ""
    return f"{ts}_{stem}{suffix}"


# ---------------------------------------------------------------------------
# OpenAI extraction helpers
# ---------------------------------------------------------------------------

def parse_llm_response(content: str) -> dict:
    """Extract JSON dict from LLM response, tolerating markdown fences."""
    match = re.search(r"```(?:json)?\s*([\s\S]*?)```", content)
    if match:
        content = match.group(1)
    try:
        data = json.loads(content.strip())
        return {
            "vendor":  str(data.get("vendor",  "") or ""),
            "address": str(data.get("address", "") or ""),
            "phone":   str(data.get("phone",   "") or ""),
            "date":    str(data.get("date",     "") or ""),
            "amount":  str(data.get("amount",   "") or ""),
        }
    except json.JSONDecodeError:
        print(f"  Warning: could not parse LLM JSON response:\n    {content[:300]}")
        return {"vendor": "", "address": "", "phone": "", "date": "", "amount": ""}


def extract_via_vision(client: OpenAI, image_path: Path) -> dict:
    """Send a local image file to GPT-4o vision and return extracted fields."""
    with open(image_path, "rb") as f:
        raw = f.read()

    # Resize if too large
    if len(raw) > MAX_IMAGE_BYTES:
        print(f"  Image is large ({len(raw) // 1024} KB), resizing…")
        img = Image.open(io.BytesIO(raw))
        img.thumbnail((2048, 2048), Image.LANCZOS)
        buf = io.BytesIO()
        fmt = img.format or "JPEG"
        img.save(buf, format=fmt)
        raw = buf.getvalue()

    b64 = base64.standard_b64encode(raw).decode("utf-8")
    mime, _ = mimetypes.guess_type(str(image_path))
    mime = mime or "image/jpeg"

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": EXTRACTION_PROMPT},
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                ],
            }
        ],
        max_tokens=500,
    )
    return parse_llm_response(response.choices[0].message.content)


def extract_via_text(client: OpenAI, text: str) -> dict:
    """Send plain text to GPT-4o and return extracted fields."""
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {
                "role": "user",
                "content": f"{EXTRACTION_PROMPT}\n\nReceipt text:\n{text[:8000]}",
            }
        ],
        max_tokens=500,
    )
    return parse_llm_response(response.choices[0].message.content)


# ---------------------------------------------------------------------------
# Per-format processors  (each returns (extracted_dict, file_type_str))
# ---------------------------------------------------------------------------

def process_image(client: OpenAI, path: Path) -> tuple[dict, str]:
    print("  Processing as image…")
    return extract_via_vision(client, path), "image"


def process_pdf(client: OpenAI, path: Path) -> tuple[dict, str]:
    print("  Processing as PDF…")

    # Try text extraction first
    try:
        import pypdf
        reader = pypdf.PdfReader(str(path))
        text = "\n".join(page.extract_text() or "" for page in reader.pages).strip()
        if len(text) >= 100:
            print(f"  Extracted {len(text)} chars of text from PDF")
            return extract_via_text(client, text), "pdf"
        print("  PDF appears to be image-based, falling back to image conversion…")
    except Exception as exc:
        print(f"  PDF text extraction error: {exc}")

    # Fall back to converting first page to image
    try:
        from pdf2image import convert_from_path
        images = convert_from_path(str(path), dpi=150, first_page=1, last_page=1)
        if images:
            tmp = Path("/tmp") / f"{path.stem}_p1.jpg"
            images[0].save(str(tmp), "JPEG")
            result = extract_via_vision(client, tmp)
            tmp.unlink(missing_ok=True)
            return result, "pdf"
    except Exception as exc:
        raise RuntimeError(
            f"PDF processing failed. Ensure poppler is installed "
            f"(apt install poppler-utils / brew install poppler).\nError: {exc}"
        ) from exc

    raise RuntimeError("Could not extract content from PDF.")


def process_text(client: OpenAI, path: Path) -> tuple[dict, str]:
    print("  Processing as plain text…")
    text = path.read_text(errors="replace")
    return extract_via_text(client, text), "text"


def process_html_file(client: OpenAI, path: Path) -> tuple[dict, str]:
    print("  Processing as HTML file…")
    html = path.read_text(errors="replace")
    text = BeautifulSoup(html, "html.parser").get_text(separator="\n", strip=True)
    return extract_via_text(client, text), "html"


def process_url(client: OpenAI, url: str) -> tuple[dict, str, bytes, str]:
    """
    Fetch a URL, extract receipt data, and return
    (extracted_dict, file_type, raw_content_bytes, suggested_extension).
    """
    print(f"  Fetching URL: {url}")
    headers = {"User-Agent": "Mozilla/5.0 (compatible; ReceiptScanner/1.0)"}
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()

    content_type = resp.headers.get("content-type", "").lower()
    raw = resp.content

    if any(t in content_type for t in ("image/jpeg", "image/png", "image/gif", "image/webp")):
        # Image URL
        ext_map = {"image/jpeg": ".jpg", "image/png": ".png",
                   "image/gif": ".gif", "image/webp": ".webp"}
        ext = next((v for k, v in ext_map.items() if k in content_type), ".jpg")
        tmp = Path("/tmp") / f"receipt_url{ext}"
        tmp.write_bytes(raw)
        extracted = extract_via_vision(client, tmp)
        tmp.unlink(missing_ok=True)
        return extracted, "url", raw, ext

    # HTML or anything else — parse as text
    text = BeautifulSoup(raw, "html.parser").get_text(separator="\n", strip=True)
    extracted = extract_via_text(client, text)
    return extracted, "url", raw, ".html"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def print_extracted(extracted: dict, category: str = ""):
    print(f"  Vendor:   {extracted['vendor']  or '(not found)'}")
    print(f"  Address:  {extracted['address'] or '(not found)'}")
    print(f"  Phone:    {extracted['phone']   or '(not found)'}")
    print(f"  Date:     {extracted['date']    or '(not found)'}")
    print(f"  Amount:   {extracted['amount']  or '(not found)'}")
    if category:
        print(f"  Category: {category}")


def main():
    parser = argparse.ArgumentParser(
        description="Add a receipt to the archive.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python add_receipt.py receipt.jpg\n"
            "  python add_receipt.py invoice.pdf --category travel\n"
            "  python add_receipt.py https://example.com/receipt --category meals\n"
            "  python add_receipt.py receipt.jpg --reprocess\n"
        ),
    )
    parser.add_argument("input", help="File path or URL of the receipt")
    parser.add_argument(
        "--category",
        default="",
        help="Expense category (e.g. meals, travel, supplies)",
    )
    parser.add_argument(
        "--reprocess",
        action="store_true",
        help="Re-run GPT-4o on an already-stored receipt and update the CSV",
    )
    args = parser.parse_args()

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("Error: OPENAI_API_KEY not set.")
        print("Copy .env.example to .env and add your OpenAI API key.")
        sys.exit(1)

    client = OpenAI(api_key=api_key)
    ensure_dirs()
    ensure_csv()

    input_val: str = args.input
    is_url = input_val.startswith("http://") or input_val.startswith("https://")

    # ------------------------------------------------------------------
    # Reprocess mode: re-run GPT-4o on an already-stored file
    # ------------------------------------------------------------------
    if args.reprocess:
        if is_url:
            print("Error: --reprocess works with stored files only, not URLs.")
            sys.exit(1)

        target_name = Path(input_val).name
        found_row = None
        with open(CSV_FILE, "r", newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row["original_filename"] == target_name or row["filename"] == target_name:
                    found_row = row
                    break

        if not found_row:
            print(f"Error: no existing CSV entry for '{target_name}'.")
            print("Run without --reprocess to add it first.")
            sys.exit(1)

        stored = RECEIPTS_DIR / found_row["filename"]
        print(f"Reprocessing: {stored}")

        ft = found_row["file_type"]
        if ft == "image":
            extracted, _ = process_image(client, stored)
        elif ft == "pdf":
            extracted, _ = process_pdf(client, stored)
        elif ft == "text":
            extracted, _ = process_text(client, stored)
        elif ft in ("html", "url"):
            extracted, _ = process_html_file(client, stored)
        else:
            print(f"Error: unknown stored file type '{ft}'")
            sys.exit(1)

        update_csv_row(found_row["id"], extracted)
        print(f"\nUpdated entry in {CSV_FILE}:")
        print_extracted(extracted)
        return

    # ------------------------------------------------------------------
    # Normal add mode
    # ------------------------------------------------------------------
    if is_url:
        parsed_path = urlparse(input_val).path
        url_basename = parsed_path.rstrip("/").split("/")[-1] or "web_receipt"

        # Duplicate check using URL as the key
        if input_val in load_existing_originals():
            print(f"'{input_val}' was already processed.")
            print("Use --reprocess to re-extract data from the stored copy.")
            sys.exit(0)

        print(f"Adding receipt from URL: {input_val}")
        extracted, file_type, raw_content, ext = process_url(client, input_val)

        if not Path(url_basename).suffix:
            url_basename += ext

        stored_filename = generate_stored_filename(url_basename)
        stored_path = RECEIPTS_DIR / stored_filename
        stored_path.write_bytes(raw_content)
        original_name_key = input_val  # use URL as dedup key

    else:
        file_path = Path(input_val)
        if not file_path.exists():
            print(f"Error: file not found: {file_path}")
            sys.exit(1)

        original_name = file_path.name

        if original_name in load_existing_originals():
            print(f"'{original_name}' was already processed.")
            print("Use --reprocess to re-extract data from the stored copy.")
            sys.exit(0)

        suffix = file_path.suffix.lower()
        print(f"Adding receipt: {file_path}")

        if suffix in IMAGE_EXTENSIONS:
            extracted, file_type = process_image(client, file_path)
        elif suffix == ".pdf":
            extracted, file_type = process_pdf(client, file_path)
        elif suffix == ".txt":
            extracted, file_type = process_text(client, file_path)
        elif suffix in (".html", ".htm"):
            extracted, file_type = process_html_file(client, file_path)
        else:
            print(f"Error: unsupported file type '{suffix}'.")
            print("Supported: .jpg .jpeg .png .gif .webp .pdf .txt .html .htm, or a URL")
            sys.exit(1)

        stored_filename = generate_stored_filename(original_name)
        stored_path = RECEIPTS_DIR / stored_filename
        shutil.copy2(str(file_path), str(stored_path))
        original_name_key = original_name

    # Build and save CSV row
    row = {
        "id":                str(uuid.uuid4())[:8],
        "filename":          stored_filename,
        "original_filename": original_name_key,
        "vendor":            extracted["vendor"],
        "address":           extracted["address"],
        "phone":             extracted["phone"],
        "date":              extracted["date"],
        "amount":            extracted["amount"],
        "category":          args.category,
        "file_type":         file_type,
        "processed_date":    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    append_to_csv(row)

    print(f"\nSaved to: {stored_path}")
    print_extracted(extracted, args.category)
    print(f"\nCSV updated: {CSV_FILE}")


if __name__ == "__main__":
    main()
