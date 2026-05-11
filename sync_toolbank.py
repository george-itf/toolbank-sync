#!/usr/bin/env python3
"""
Toolbank → Shopify Sync Script
Downloads data from Toolbank FTP and generates Matrixify-compatible CSV
"""

import os
import csv
import json
import ftplib
import re
import unicodedata
from datetime import datetime
from pathlib import Path
import openpyxl

# ============================================================================
# CONFIGURATION
# ============================================================================

FTP_HOST = "ftp1.toolbank.com"
FTP_USER = os.environ.get("TOOLBANK_FTP_USER", "Invictatools_9051")
FTP_PASS = os.environ.get("TOOLBANK_FTP_PASS", "")

# Cloudflare R2 image URL
IMAGE_BASE_URL = "https://pub-a85f523f346d43c1bec0c5fe4f1d0b4b.r2.dev/"

# Files to download from FTP
FTP_FILES = {
    "pricing": "Invictatools_9051.csv",
    "products": "Data/ToolbankDataExport.xlsx",
    "availability": "UnitData-01/Availability01D.csv",
}

# Output directory
OUTPUT_DIR = Path(__file__).parent
KNOWN_SKUS_FILE = OUTPUT_DIR / "known_skus.json"

# Toolbank's Availability01D.csv caps cstock at 100; real on-hand may be higher.
# We log how many SKUs hit the cap on each run but pass the value through unchanged.
CSTOCK_CAP = 100

# Safety gate: refuse to archive more than this fraction of known SKUs in one run.
# Protects against a malformed Toolbank export silently archiving the whole catalog.
GHOST_ARCHIVE_PCT_LIMIT = 10.0

# A valid Toolbank SKU starts with an uppercase letter/digit and contains only
# A-Z, 0-9, /, _, - (3-30 chars total). Derived from the live product feed; rejects
# the historic description-fragment garbage that polluted known_skus.json.
VALID_SKU_RE = re.compile(r'^[A-Z0-9][A-Z0-9/_\-]{2,29}$')

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def slugify(text):
    if not text:
        return ''
    # Strip branding marks first — NFKD would otherwise expand ™ to "TM" and
    # silently break ~1.3k existing Shopify handles that the old slugify dropped.
    text = re.sub(r'[™®©℠]', '', text)
    # NFKD + ASCII fold turns ⁰→0, ²→2, º→o, ü→u, etc.
    text = unicodedata.normalize('NFKD', text)
    text = text.encode('ascii', 'ignore').decode('ascii')
    text = text.lower().strip()
    text = re.sub(r'[^a-z0-9\s-]', '', text)
    text = re.sub(r'[-\s]+', '-', text)
    text = text.strip('-')
    return text[:200]


def is_valid_sku(s):
    return bool(s and VALID_SKU_RE.match(s))


def clean_tags(raw_tags):
    """Split each raw tag on control chars and commas (both would corrupt the
    Matrixify tag list), collapse whitespace, dedupe, cap each at 255 chars."""
    result, seen = [], set()
    for raw in raw_tags:
        if not raw:
            continue
        for part in re.split(r'[\x00-\x1f\x7f,]+', str(raw)):
            part = re.sub(r'\s+', ' ', part).strip()
            if not part:
                continue
            part = part[:255]
            if part not in seen:
                seen.add(part)
                result.append(part)
    return result


def clean_barcode(bc):
    """Return a valid GTIN string or '' if the input is junk.
    11-digit numerics are padded to 12 to recover UPC-A values stripped of their
    leading zero by Excel."""
    if not bc:
        return ''
    bc = str(bc).strip()
    if not bc.isdigit():
        return ''
    if len(bc) == 11:
        bc = '0' + bc
    if len(bc) not in (8, 12, 13, 14):
        return ''
    return bc


def require_columns(actual, required, source_label):
    """Fail loudly if expected columns are missing — protects ghost-archival from
    a malformed export that would otherwise silently zero out the catalog."""
    missing = set(required) - set(actual)
    if missing:
        raise RuntimeError(
            f"[FATAL] {source_label} missing required columns: {sorted(missing)}. "
            f"Found: {sorted(actual)}"
        )


def connect_ftp():
    print(f"[FTP] Connecting to {FTP_HOST}...")
    ftp = ftplib.FTP(FTP_HOST)
    ftp.login(FTP_USER, FTP_PASS)
    print(f"[FTP] Connected successfully")
    return ftp


def download_file(ftp, remote_path, local_path):
    print(f"[FTP] Downloading {remote_path}...")
    local_path = Path(local_path)
    local_path.parent.mkdir(parents=True, exist_ok=True)
    with open(local_path, 'wb') as f:
        ftp.retrbinary(f'RETR {remote_path}', f.write)
    print(f"[FTP] Saved to {local_path}")
    return local_path


# ============================================================================
# DATA PARSING
# ============================================================================

def parse_pricing_csv(file_path):
    pricing = {}
    with open(file_path, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        require_columns(reader.fieldnames or [], ['stock_no', 'price', 'rrp'], 'pricing CSV')
        for row in reader:
            sku = (row.get('stock_no') or '').strip()
            if not is_valid_sku(sku):
                continue
            try:
                price = float(row.get('price') or 0)
            except (TypeError, ValueError):
                price = 0.0
            try:
                rrp = float(row.get('rrp') or 0)
            except (TypeError, ValueError):
                rrp = 0.0
            pricing[sku] = {'trade_price': price, 'rrp': rrp}
    print(f"[DATA] Loaded {len(pricing)} pricing records")
    return pricing


def parse_availability_csv(file_path):
    stock = {}
    with open(file_path, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        require_columns(reader.fieldnames or [], ['stock_no', 'cstock'], 'availability CSV')
        for row in reader:
            sku = (row.get('stock_no') or '').strip()
            if not is_valid_sku(sku):
                continue
            qty_raw = (row.get('cstock') or '0').strip()
            try:
                stock[sku] = int(float(qty_raw)) if qty_raw else 0
            except (TypeError, ValueError):
                stock[sku] = 0
    print(f"[DATA] Loaded {len(stock)} stock records")
    return stock


def parse_products_xlsx(file_path):
    products = {}
    skipped_invalid_sku = 0
    print(f"[DATA] Loading Excel file...")
    wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
    sheet = wb.active
    headers = None
    required_cols = [
        'StockCode', 'Product Name', 'ProductDescription', 'Brand_Name',
        'RetailerBarcode', 'Weight', 'ImageRef', 'DiscontinuedFlag',
        'ClassAName', 'ClassBName', 'ClassCName',
    ]
    for i, row in enumerate(sheet.iter_rows(values_only=True)):
        if i == 0:
            headers = [str(h).strip() if h else f'col_{j}' for j, h in enumerate(row)]
            require_columns(headers, required_cols, 'products XLSX')
            continue
        row_dict = dict(zip(headers, row))
        sku = str(row_dict.get('StockCode') or '').strip()
        if not sku:
            continue
        if not is_valid_sku(sku):
            skipped_invalid_sku += 1
            continue
        try:
            weight = float(row_dict.get('Weight') or 0)
        except (TypeError, ValueError):
            weight = 0.0
        products[sku] = {
            'sku': sku,
            'title': str(row_dict.get('Product Name') or '').strip(),
            'description': str(row_dict.get('ProductDescription') or ''),
            'vendor': str(row_dict.get('Brand_Name') or '').strip(),
            'barcode': str(row_dict.get('RetailerBarcode') or '').strip(),
            'weight': weight,
            'image_ref': str(row_dict.get('ImageRef') or '').strip(),
            'discontinued': str(row_dict.get('DiscontinuedFlag') or '0').strip() == '1',
            'class_a': str(row_dict.get('ClassAName') or '').strip(),
            'class_b': str(row_dict.get('ClassBName') or '').strip(),
            'class_c': str(row_dict.get('ClassCName') or '').strip(),
        }
    wb.close()
    if skipped_invalid_sku:
        print(f"[DATA] Skipped {skipped_invalid_sku} rows with invalid StockCode")
    print(f"[DATA] Loaded {len(products)} products")
    return products


# ============================================================================
# KNOWN SKUs TRACKING
# ============================================================================

def load_known_skus():
    """Load known SKUs, filtering through the validator so historical garbage
    (description fragments, HTML, blanks) is dropped on every run."""
    if not KNOWN_SKUS_FILE.exists():
        return set(), set()
    with open(KNOWN_SKUS_FILE, 'r') as f:
        data = json.load(f)
    raw_known = data.get('skus', [])
    raw_archived = data.get('archived', [])
    clean_known = {s for s in raw_known if is_valid_sku(s)}
    clean_archived = {s for s in raw_archived if is_valid_sku(s)}
    dropped = len(raw_known) - len(clean_known) + len(raw_archived) - len(clean_archived)
    if dropped:
        print(f"[INIT] Filtered {dropped} invalid SKUs from known_skus.json")
    return clean_known, clean_archived


def save_known_skus(skus, archived):
    """Persist sorted, pretty-printed JSON so diffs are reviewable."""
    payload = {
        'updated': datetime.now().isoformat(),
        'skus': sorted(skus),
        'archived': sorted(archived),
    }
    with open(KNOWN_SKUS_FILE, 'w') as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
        f.write('\n')
    print(f"[DATA] Saved {len(skus)} known SKUs, {len(archived)} archived")


# ============================================================================
# CSV GENERATION
# ============================================================================

def generate_matrixify_csv(products, pricing, stock, known_skus, archived_skus, output_path):
    """Quantity-only sync.

    For each SKU that exists in BOTH the Toolbank feed AND the Shopify
    catalogue (known_skus) we emit a row that sets:
      - Variant Inventory Qty (from Toolbank's availability feed)
      - Status / Published   (active when stock > 0, draft when 0,
                              archived if DiscontinuedFlag=1)
    All other columns are left blank so Matrixify treats them as unchanged.

    Explicitly does NOT:
      - Create new products in Shopify (SKUs in feed but not in known_skus
        are skipped entirely — no row emitted).
      - Update price, title, description, vendor, type, tags, image,
        weight, or barcode on any product.
      - Archive products that have disappeared entirely from the feed
        (known_skus minus feed) — the safety gate logic is preserved in
        the JSON state (archived_skus) but no archive rows are emitted
        for orphans. Manage those manually.

    `pricing` is accepted but unused; kept in the signature so the rest
    of the pipeline (parsers, main) doesn't need to change.
    """
    feed_skus = set(products.keys())
    targets = feed_skus & known_skus
    skipped_not_in_shopify = feed_skus - known_skus
    shopify_orphans = known_skus - feed_skus

    print(f"[SYNC] Toolbank feed:                       {len(feed_skus)}")
    print(f"[SYNC] Shopify catalogue (known_skus):      {len(known_skus)}")
    print(f"[SYNC] Targets (in both, will be updated):  {len(targets)}")
    print(f"[SYNC] Skipped: in feed, not in Shopify:    {len(skipped_not_in_shopify)}")
    print(f"[SYNC] Skipped: in Shopify, not in feed:    {len(shopify_orphans)}")

    headers = [
        'Command', 'Handle', 'Title', 'Body (HTML)', 'Vendor', 'Type', 'Tags',
        'Published', 'Variant SKU', 'Variant Grams', 'Variant Inventory Tracker',
        'Variant Inventory Policy', 'Variant Fulfillment Service', 'Variant Price',
        'Variant Compare At Price', 'Variant Requires Shipping', 'Variant Taxable',
        'Variant Barcode', 'Image Src', 'Image Position', 'Status', 'Variant Inventory Qty'
    ]

    rows = []
    active_count = 0
    draft_count = 0
    archived_count = 0
    cstock_capped = 0

    for sku in sorted(targets):
        product = products[sku]
        qty = stock.get(sku, 0)
        if qty >= CSTOCK_CAP:
            cstock_capped += 1

        if product.get('discontinued', False):
            status, published = 'archived', 'FALSE'
            archived_count += 1
        elif qty > 0:
            status, published = 'active', 'TRUE'
            active_count += 1
        else:
            status, published = 'draft', 'FALSE'
            draft_count += 1

        rows.append({
            'Command': 'UPDATE',
            'Handle': '',
            'Title': '',
            'Body (HTML)': '',
            'Vendor': '',
            'Type': '',
            'Tags': '',
            'Published': published,
            'Variant SKU': sku,
            'Variant Grams': '',
            'Variant Inventory Tracker': '',
            'Variant Inventory Policy': '',
            'Variant Fulfillment Service': '',
            'Variant Price': '',
            'Variant Compare At Price': '',
            'Variant Requires Shipping': '',
            'Variant Taxable': '',
            'Variant Barcode': '',
            'Image Src': '',
            'Image Position': '',
            'Status': status,
            'Variant Inventory Qty': qty,
        })

    print(f"[SYNC] -> active:   {active_count}")
    print(f"[SYNC] -> draft:    {draft_count}")
    print(f"[SYNC] -> archived: {archived_count}")
    print(f"[SYNC] At cstock cap ({CSTOCK_CAP}): {cstock_capped}")

    csv_path = output_path / "toolbank_import.csv"
    with open(csv_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)

    print(f"[OUTPUT] Generated {csv_path} with {len(rows)} rows")

    # State unchanged: known_skus reflects what's in Shopify (seeded from a
    # Shopify export). We don't grow it from feed data because growing it
    # would re-introduce the drift problem this script was rebuilt to fix.
    # Re-seed known_skus.json from a fresh Shopify export whenever you add
    # products manually in Shopify.
    return csv_path, known_skus, archived_skus


# ============================================================================
# MAIN
# ============================================================================

def main():
    print("=" * 60)
    print("TOOLBANK → SHOPIFY SYNC")
    print(f"Started: {datetime.now().isoformat()}")
    print("=" * 60)
    
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    known_skus, archived_skus = load_known_skus()
    print(f"[INIT] Known SKUs: {len(known_skus)}, archived: {len(archived_skus)}")

    try:
        ftp = connect_ftp()
        pricing_file = OUTPUT_DIR / "temp_pricing.csv"
        download_file(ftp, FTP_FILES['pricing'], pricing_file)
        products_file = OUTPUT_DIR / "temp_products.xlsx"
        download_file(ftp, FTP_FILES['products'], products_file)
        availability_file = OUTPUT_DIR / "temp_availability.csv"
        download_file(ftp, FTP_FILES['availability'], availability_file)
        ftp.quit()
        print("[FTP] Disconnected")
    except Exception as e:
        print(f"[ERROR] FTP failed: {e}")
        raise

    pricing = parse_pricing_csv(pricing_file)
    stock = parse_availability_csv(availability_file)
    products = parse_products_xlsx(products_file)

    output_csv, updated_known, updated_archived = generate_matrixify_csv(
        products, pricing, stock, known_skus, archived_skus, OUTPUT_DIR
    )

    save_known_skus(updated_known, updated_archived)
    
    # Clean up temp files
    pricing_file.unlink(missing_ok=True)
    products_file.unlink(missing_ok=True)
    availability_file.unlink(missing_ok=True)
    
    print("=" * 60)
    print("SYNC COMPLETE")
    print("=" * 60)


if __name__ == "__main__":
    main()
