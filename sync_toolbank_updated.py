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
from datetime import datetime
from pathlib import Path
import openpyxl
from io import BytesIO

# ============================================================================
# CONFIGURATION
# ============================================================================

FTP_HOST = "ftp1.toolbank.com"
FTP_USER = os.environ.get("TOOLBANK_FTP_USER", "Invictatools_9051")
FTP_PASS = os.environ.get("TOOLBANK_FTP_PASS", "")

# Your Cloudflare R2 image URL
IMAGE_BASE_URL = "https://pub-a85f523f346d43c1bec0c5fe4f1d0b4b.r2.dev/"

# Files to download from FTP
FTP_FILES = {
    "pricing": "Invictatools_9051.csv",
    "products": "Data/ToolbankDataExport.xlsx",
    "availability": "UnitData-01/Availability01D.csv",
}

# Output directory (same folder as script for simplicity)
OUTPUT_DIR = Path(__file__).parent
KNOWN_SKUS_FILE = OUTPUT_DIR / "known_skus.json"

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def slugify(text):
    """Convert text to URL-safe handle"""
    text = text.lower().strip()
    text = re.sub(r'[^\w\s-]', '', text)
    text = re.sub(r'[-\s]+', '-', text)
    return text[:200]


def connect_ftp():
    """Connect to Toolbank FTP server"""
    print(f"[FTP] Connecting to {FTP_HOST}...")
    ftp = ftplib.FTP(FTP_HOST)
    ftp.login(FTP_USER, FTP_PASS)
    print(f"[FTP] Connected successfully")
    return ftp


def download_file(ftp, remote_path, local_path):
    """Download a file from FTP"""
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
    """Parse pricing file"""
    pricing = {}
    with open(file_path, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            sku = row.get('stock_no', '').strip()
            if sku:
                pricing[sku] = {
                    'trade_price': float(row.get('price', 0) or 0),
                    'rrp': float(row.get('rrp', 0) or 0),
                }
    print(f"[DATA] Loaded {len(pricing)} pricing records")
    return pricing


def parse_availability_csv(file_path):
    """Parse stock levels"""
    stock = {}
    with open(file_path, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            sku = row.get('stock_no', '').strip()
            if sku:
                qty = row.get('cstock', '0').strip()
                stock[sku] = int(float(qty)) if qty else 0
    print(f"[DATA] Loaded {len(stock)} stock records")
    return stock


def parse_products_xlsx(file_path):
    """Parse product data from Excel"""
    products = {}
    
    print(f"[DATA] Loading Excel file...")
    wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
    sheet = wb.active
    
    headers = None
    for i, row in enumerate(sheet.iter_rows(values_only=True)):
        if i == 0:
            headers = [str(h).strip() if h else f'col_{j}' for j, h in enumerate(row)]
            continue
        
        row_dict = dict(zip(headers, row))
        sku = str(row_dict.get('StockCode', '')).strip()
        
        if not sku:
            continue
        
        products[sku] = {
            'sku': sku,
            'title': str(row_dict.get('Product Name', '')).strip(),
            'description': str(row_dict.get('ProductDescription', '') or ''),
            'vendor': str(row_dict.get('Brand_Name', '')).strip(),
            'barcode': str(row_dict.get('RetailerBarcode', '')).strip(),
            'weight': float(row_dict.get('Weight', 0) or 0),
            'image_ref': str(row_dict.get('ImageRef', '')).strip(),
            'discontinued': str(row_dict.get('DiscontinuedFlag', '0')).strip() == '1',
            'class_a': str(row_dict.get('ClassAName', '')).strip(),
            'class_b': str(row_dict.get('ClassBName', '')).strip(),
            'class_c': str(row_dict.get('ClassCName', '')).strip(),
        }
    
    wb.close()
    print(f"[DATA] Loaded {len(products)} products")
    return products


# ============================================================================
# KNOWN SKUs TRACKING
# ============================================================================

def load_known_skus():
    """Load list of SKUs already in Shopify"""
    if KNOWN_SKUS_FILE.exists():
        with open(KNOWN_SKUS_FILE, 'r') as f:
            data = json.load(f)
            return set(data.get('skus', []))
    return set()


def save_known_skus(skus):
    """Save updated list of known SKUs"""
    with open(KNOWN_SKUS_FILE, 'w') as f:
        json.dump({
            'skus': list(skus),
            'updated': datetime.now().isoformat()
        }, f)
    print(f"[DATA] Saved {len(skus)} known SKUs")


# ============================================================================
# CSV GENERATION
# ============================================================================

def generate_matrixify_csv(products, pricing, stock, known_skus, output_path):
    """Generate Matrixify-compatible CSV"""
    
    all_skus = set(products.keys())
    new_skus = all_skus - known_skus
    existing_skus = all_skus & known_skus
    
    print(f"[SYNC] Total products: {len(all_skus)}")
    print(f"[SYNC] New products: {len(new_skus)}")
    print(f"[SYNC] Existing products: {len(existing_skus)}")
    
    discontinued_skus = {sku for sku, p in products.items() if p.get('discontinued')}
    print(f"[SYNC] Discontinued: {len(discontinued_skus)}")
    
    headers = [
        'Command', 'Handle', 'Title', 'Body (HTML)', 'Vendor', 'Type', 'Tags',
        'Published', 'Variant SKU', 'Variant Grams', 'Variant Inventory Tracker',
        'Variant Inventory Policy', 'Variant Fulfillment Service', 'Variant Price',
        'Variant Compare At Price', 'Variant Requires Shipping', 'Variant Taxable',
        'Variant Barcode', 'Image Src', 'Image Position', 'Status', 'Variant Inventory Qty'
    ]
    
    rows = []
    
    for sku, product in products.items():
        price_data = pricing.get(sku, {})
        stock_qty = stock.get(sku, 0)
        
        is_new = sku in new_skus
        is_discontinued = product.get('discontinued', False)
        
        if is_discontinued:
            command = 'DELETE'
            status = 'archived'
        elif is_new:
            command = 'MERGE'
            status = 'active'
        else:
            command = 'UPDATE'
            status = 'active'
        
        # Price: RRP for new products, empty for existing (preserves your prices)
        if is_new:
            price = round(price_data.get('rrp', 0), 2)
        else:
            price = ''  # Empty = don't update
        
        # Tags
        tags = [t for t in [product['class_a'], product['class_b'], product['class_c']] if t]
        tags.append('Toolbank')
        if is_new:
            tags.append('New-Import')
        
        # Handle
        handle = slugify(f"{product['title']}-{sku}")
        
        # Image URL - using your Cloudflare R2 bucket
        image_ref = product['image_ref'].strip() or sku
        image_url = f"{IMAGE_BASE_URL}{image_ref}.JPG"
        
        row = {
            'Command': command,
            'Handle': handle,
            'Title': product['title'],
            'Body (HTML)': product['description'],
            'Vendor': product['vendor'],
            'Type': product.get('class_b', ''),
            'Tags': ', '.join(tags),
            'Published': 'TRUE' if status == 'active' else 'FALSE',
            'Variant SKU': sku,
            'Variant Grams': int(product.get('weight', 0) * 1000),
            'Variant Inventory Tracker': 'shopify',
            'Variant Inventory Policy': 'deny',
            'Variant Fulfillment Service': 'manual',
            'Variant Price': price,
            'Variant Compare At Price': '',
            'Variant Requires Shipping': 'TRUE',
            'Variant Taxable': 'TRUE',
            'Variant Barcode': product.get('barcode', ''),
            'Image Src': image_url,
            'Image Position': '1',
            'Status': status,
            'Variant Inventory Qty': stock_qty,
        }
        
        rows.append(row)
    
    # Write CSV
    csv_path = output_path / "toolbank_import.csv"
    with open(csv_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)
    
    print(f"[OUTPUT] Generated {csv_path} with {len(rows)} products")
    
    # Update known SKUs
    updated_known = known_skus | (all_skus - discontinued_skus)
    
    return csv_path, updated_known


# ============================================================================
# MAIN
# ============================================================================

def main():
    print("=" * 60)
    print("TOOLBANK → SHOPIFY SYNC")
    print(f"Started: {datetime.now().isoformat()}")
    print("=" * 60)
    
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    known_skus = load_known_skus()
    print(f"[INIT] Known SKUs: {len(known_skus)}")
    
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
    
    output_csv, updated_known = generate_matrixify_csv(
        products, pricing, stock, known_skus, OUTPUT_DIR
    )
    
    save_known_skus(updated_known)
    
    # Clean up temp files
    pricing_file.unlink(missing_ok=True)
    products_file.unlink(missing_ok=True)
    availability_file.unlink(missing_ok=True)
    
    print("=" * 60)
    print("SYNC COMPLETE")
    print("=" * 60)


if __name__ == "__main__":
    main()
