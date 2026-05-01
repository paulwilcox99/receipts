#!/usr/bin/env python3
"""
Installation verification test for the receipt archive.

Checks:
  1. Python version
  2. All required packages importable
  3. .env / OPENAI_API_KEY present
  4. poppler available (pdf2image fallback)
  5. End-to-end: add a text receipt → generate site (runs in a temp dir)

Usage:
  python test_install.py
"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
WARN = "\033[33mWARN\033[0m"
SKIP = "\033[36mSKIP\033[0m"

results: list[tuple[str, str, str]] = []  # (label, status, detail)


def check(label: str, ok: bool, detail: str = "", warn_only: bool = False):
    status = (WARN if warn_only else FAIL) if not ok else PASS
    results.append((label, status, detail))
    badge = "WARN" if (not ok and warn_only) else ("PASS" if ok else "FAIL")
    color = WARN if badge == "WARN" else (PASS if ok else FAIL)
    print(f"  [{color}] {label}" + (f"  — {detail}" if detail else ""))
    return ok


def section(title: str):
    print(f"\n{title}")
    print("-" * len(title))


# ---------------------------------------------------------------------------
# 1. Python version
# ---------------------------------------------------------------------------

section("1. Python version")
major, minor = sys.version_info[:2]
check(
    f"Python {major}.{minor}",
    major == 3 and minor >= 10,
    "" if (major == 3 and minor >= 10) else f"need 3.10+, got {major}.{minor}",
)

# ---------------------------------------------------------------------------
# 2. Package imports
# ---------------------------------------------------------------------------

section("2. Required packages")

PACKAGES = [
    ("openai",        "openai"),
    ("dotenv",        "python-dotenv"),
    ("PIL",           "Pillow"),
    ("pypdf",         "pypdf"),
    ("pdf2image",     "pdf2image"),
    ("requests",      "requests"),
    ("bs4",           "beautifulsoup4"),
]

all_imports_ok = True
for module, pkg in PACKAGES:
    try:
        __import__(module)
        check(f"import {module}", True)
    except ImportError as e:
        check(f"import {module}", False, f"pip install {pkg}")
        all_imports_ok = False

# ---------------------------------------------------------------------------
# 3. .env / API key
# ---------------------------------------------------------------------------

section("3. Environment / API key")

project_root = Path(__file__).parent
dotenv_path  = project_root / ".env"
env_example  = project_root / ".env.example"

dotenv_exists = check(
    ".env file present",
    dotenv_path.exists(),
    "" if dotenv_path.exists() else "copy .env.example to .env and add your OPENAI_API_KEY",
)

if dotenv_exists:
    try:
        from dotenv import dotenv_values
        env_vals = dotenv_values(dotenv_path)
        api_key = env_vals.get("OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY", "")
    except ImportError:
        api_key = os.environ.get("OPENAI_API_KEY", "")
else:
    api_key = os.environ.get("OPENAI_API_KEY", "")

api_key_ok = check(
    "OPENAI_API_KEY set",
    bool(api_key),
    "" if api_key else "add OPENAI_API_KEY=sk-... to .env",
)

# ---------------------------------------------------------------------------
# 4. poppler (optional — needed only for scanned PDFs)
# ---------------------------------------------------------------------------

section("4. System dependencies")

poppler_ok = shutil.which("pdftoppm") is not None
check(
    "poppler (pdftoppm)",
    poppler_ok,
    "" if poppler_ok else "optional; needed for scanned PDFs — apt install poppler-utils",
    warn_only=True,
)

# ---------------------------------------------------------------------------
# 5. End-to-end smoke test
# ---------------------------------------------------------------------------

section("5. End-to-end smoke test")

if not (all_imports_ok and api_key_ok):
    print(f"  [{SKIP}] Skipping end-to-end test (fix errors above first)")
    results.append(("end-to-end", SKIP, "prerequisites not met"))
else:
    TEST_RECEIPT_TEXT = """\
ACME Test Store
123 Main St, Springfield, IL 62701
Tel: (555) 867-5309

Receipt #TEST-0001
Date: 2024-06-15

  Widget A        $12.50
  Widget B        $7.25
  ─────────────────────
  Subtotal        $19.75
  Tax (8%)         $1.58
  TOTAL           $21.33

Thank you for your purchase!
"""

    tmpdir = Path(tempfile.mkdtemp(prefix="receipt_test_"))
    try:
        # Mirror the project structure inside the temp dir
        (tmpdir / "receipts").mkdir()

        test_file = tmpdir / "test_receipt.txt"
        test_file.write_text(TEST_RECEIPT_TEXT, encoding="utf-8")

        add_script     = project_root / "add_receipt.py"
        generate_script = project_root / "generate_site.py"
        env_file        = dotenv_path if dotenv_path.exists() else None

        env = os.environ.copy()
        if env_file:
            from dotenv import dotenv_values
            env.update({k: v for k, v in dotenv_values(env_file).items() if v})

        # --- add_receipt.py ---
        print(f"  Running add_receipt.py on test text receipt…")
        result = subprocess.run(
            [sys.executable, str(add_script), str(test_file), "--category", "test"],
            cwd=tmpdir,
            capture_output=True,
            text=True,
            env=env,
        )

        add_ok = result.returncode == 0
        if not add_ok:
            check("add_receipt.py", False, result.stderr.strip().splitlines()[-1] if result.stderr else "non-zero exit")
        else:
            check("add_receipt.py", True)

            # Verify CSV was written with a row
            csv_file = tmpdir / "receipts.csv"
            csv_exists = csv_file.exists()
            check("receipts.csv created", csv_exists)

            if csv_exists:
                import csv as csv_mod
                with open(csv_file, newline="", encoding="utf-8") as f:
                    rows = list(csv_mod.DictReader(f))
                row_written = len(rows) == 1
                check("one row written to CSV", row_written, f"found {len(rows)} rows")

                if row_written:
                    row = rows[0]
                    vendor_found = bool(row.get("vendor"))
                    amount_found = bool(row.get("amount"))
                    check(
                        "vendor extracted",
                        vendor_found,
                        row.get("vendor", "(empty)"),
                        warn_only=not vendor_found,
                    )
                    check(
                        "amount extracted",
                        amount_found,
                        row.get("amount", "(empty)"),
                        warn_only=not amount_found,
                    )
                    if vendor_found:
                        print(f"    vendor : {row['vendor']}")
                    if amount_found:
                        print(f"    amount : {row['amount']}")

        # --- generate_site.py ---
        print(f"  Running generate_site.py…")
        result2 = subprocess.run(
            [sys.executable, str(generate_script)],
            cwd=tmpdir,
            capture_output=True,
            text=True,
            env=env,
        )

        gen_ok = result2.returncode == 0
        check("generate_site.py", gen_ok, result2.stderr.strip().splitlines()[-1] if (not gen_ok and result2.stderr) else "")

        if gen_ok:
            index = tmpdir / "site" / "index.html"
            html_ok = index.exists() and index.stat().st_size > 1000
            check("site/index.html generated", html_ok, f"{index.stat().st_size} bytes" if index.exists() else "file missing")

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

section("Summary")

passed = sum(1 for _, s, _ in results if "PASS" in s)
warned = sum(1 for _, s, _ in results if "WARN" in s)
failed = sum(1 for _, s, _ in results if "FAIL" in s)
skipped = sum(1 for _, s, _ in results if "SKIP" in s)

print(f"  {passed} passed  |  {warned} warnings  |  {failed} failed  |  {skipped} skipped")

if failed:
    print(f"\n  Installation incomplete — fix the FAIL items above.")
    sys.exit(1)
elif warned:
    print(f"\n  Installation OK (some optional features unavailable — see warnings).")
else:
    print(f"\n  Installation complete and working.")
