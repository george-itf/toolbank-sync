#!/usr/bin/env python3
"""
Toolbank → Shopify Sync Script
Downloads data from Toolbank FTP and generates Matrixify-compatible CSV

Author: Built for Invicta Tools
"""

import os
import csv
import json
import ftplib
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

# Files to download from FTP
FTP_FILES = {
    "pricing": "Invictatools_9051.csv",
    "products": "Data/ToolbankDataExport.csv",  # CSV version
    "products_xlsx": "Data/ToolbankDataExport.xlsx",  # Excel version (backup)
    "availability": "UnitData-01/Availability01D.csv",
}

# Output directory
OUTPUT_DIR = Path(__file__).parent.parent / "output"
KNOWN_SKUS_FILE = Path(__file__).parent.parent / "known_skus.json"

# Image base URL (Toolbank CDN or your own)
IMAGE_BASE_URL = "https://www.toolbank.com/productimages/"  # Update if needed

# ============================================================================
# FTP DOWNLOAD FUNCTIONS
# ============================================================================

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


def download_to_memory(ftp, remote_path):
    """Download a file from FTP to memory (BytesIO)"""
    print(f"[FTP] Downloading {remote_path} to memory...")
    buffer = BytesIO()
    ftp.retrbinary(f'RETR {remote_path}', buffer.write)
    buffer.seek(0)
    return buffer


# ============================================================================
# DATA PARSING FUNCTIONS
# ============================================================================

def parse_pricing_csv(file_path):
    """Parse Invictatools_9051.csv pricing file"""
    pricing = {}
    with open(file_path, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            sku = row.get('stock_no', '').strip()
            if sku:
                pricing[sku] = {
                    'trade_price': float(row.get('price', 0) or 0),
                    'rrp': float(row.get('rrp', 0) or 0),
                    'sell_dis_1': float(row.get('sell_dis_1', 0) or 0),
                    'nett_price': float(row.get('nett_price', 0) or 0),
                    'rebate_flg': row.get('rebate_flg', 'N'),
                    'prom_no': row.get('prom_no', ''),
                    'prom_end_date': row.get('prom_end_date', ''),
                }
    print(f"[DATA] Loaded {len(pricing)} pricing records")
    return pricing


def parse_availability_csv(file_path):
    """Parse Availability01D.csv stock levels"""
    stock = {}
    with open(file_path, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            sku = row.get('stock_no', '').strip()
            if sku:
                # cstock = central stock
                qty = row.get('cstock', '0').strip()
                stock[sku] = int(float(qty)) if qty else 0
    print(f"[DATA] Loaded {len(stock)} stock records")
    return stock


def parse_products_xlsx(file_path):
    """Parse ToolbankDataExport.xlsx product data"""
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
        
        # Clean up description HTML
        description = row_dict.get('ProductDescription', '') or ''
        
        products[sku] = {
            'sku': sku,
            'title': str(row_dict.get('Product Name', '')).strip(),
            'description': description,
            'vendor': str(row_dict.get('Brand_Name', '')).strip(),
            'barcode': str(row_dict.get('RetailerBarcode', '')).strip(),
            'weight': float(row_dict.get('Weight', 0) or 0),
            'brand_part_number': str(row_dict.get('BrandPartNumber', '')).strip(),
            'image_ref': str(row_dict.get('ImageRef', '')).strip(),
            'discontinued': str(row_dict.get('DiscontinuedFlag', '0')).strip() == '1',
            'rrp': float(row_dict.get('CurrentListPrice', 0) or 0),
            'trade_discount': float(row_dict.get('TradeDiscount', 0) or 0),
            # Categories
            'class_a': str(row_dict.get('ClassAName', '')).strip(),
            'class_b': str(row_dict.get('ClassBName', '')).strip(),
            'class_c': str(row_dict.get('ClassCName', '')).strip(),
            # Dimensions
            'dim_1': float(row_dict.get('Dimension1', 0) or 0),
            'dim_2': float(row_dict.get('Dimension2', 0) or 0),
            'dim_3': float(row_dict.get('Dimension3', 0) or 0),
            'pack_qty': int(float(row_dict.get('PackQTY', 1) or 1)),
        }
    
    wb.close()
    print(f"[DATA] Loaded {len(products)} products from Excel")
    return products


def parse_products_csv(file_path):
    """Parse ToolbankDataExport.csv (if CSV version exists)"""
    products = {}
    
    with open(file_path, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            sku = row.get('StockCode', '').strip()
            if not sku:
                continue
            
            products[sku] = {
                'sku': sku,
                'title': row.get('Product Name', '').strip(),
                'description': row.get('ProductDescription', ''),
                'vendor': row.get('Brand_Name', '').strip(),
                'barcode': row.get('RetailerBarcode', '').strip(),
                'weight': float(row.get('Weight', 0) or 0),
                'brand_part_number': row.get('BrandPartNumber', '').strip(),
                'image_ref': row.get('ImageRef', '').strip(),
                'discontinued': row.get('DiscontinuedFlag', '0').strip() == '1',
                'rrp': float(row.get('CurrentListPrice', 0) or 0),
                'trade_discount': float(row.get('TradeDiscount', 0) or 0),
                'class_a': row.get('ClassAName', '').strip(),
                'class_b': row.get('ClassBName', '').strip(),
                'class_c': row.get('ClassCName', '').strip(),
                'dim_1': float(row.get('Dimension1', 0) or 0),
                'dim_2': float(row.get('Dimension2', 0) or 0),
                'dim_3': float(row.get('Dimension3', 0) or 0),
                'pack_qty': int(float(row.get('PackQTY', 1) or 1)),
            }
    
    print(f"[DATA] Loaded {len(products)} products from CSV")
    return products


# ============================================================================
# KNOWN SKUs TRACKING (for new vs existing products)
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
# SHOPIFY/MATRIXIFY CSV GENERATION
# ============================================================================

def slugify(text):
    """Convert text to URL-safe handle"""
    import re
    text = text.lower().strip()
    text = re.sub(r'[^\w\s-]', '', text)
    text = re.sub(r'[-\s]+', '-', text)
    return text[:200]  # Shopify handle limit


def generate_image_url(sku, image_ref):
    """Generate image URL from SKU or image reference"""
    ref = (image_ref or sku).strip()
    if ref:
        return f"{IMAGE_BASE_URL}{ref}.jpg"
    return ""


def generate_matrixify_csv(products, pricing, stock, known_skus, output_path):
    """
    Generate Matrixify-compatible CSV for Shopify import
    
    Strategy:
    - For NEW products: Include price = RRP, status = active
    - For EXISTING products: NO price columns (preserves current price)
    - For DISCONTINUED products: Command = DELETE (archives in Shopify)
    """
    
    # Matrixify columns
    # We generate TWO files:
    # 1. update_existing.csv - updates without price changes
    # 2. create_new.csv - new products with RRP as price
    
    all_skus = set(products.keys())
    new_skus = all_skus - known_skus
    existing_skus = all_skus & known_skus
    
    print(f"[SYNC] Total products in feed: {len(all_skus)}")
    print(f"[SYNC] New products: {len(new_skus)}")
    print(f"[SYNC] Existing products: {len(existing_skus)}")
    
    # Count discontinued
    discontinued_skus = {sku for sku, p in products.items() if p.get('discontinued')}
    print(f"[SYNC] Discontinued products: {len(discontinued_skus)}")
    
    # -------------------------------------------------------------------------
    # Generate MAIN import file (updates existing, creates new)
    # -------------------------------------------------------------------------
    
    main_csv_path = output_path / "toolbank_import.csv"
    
    headers = [
        'Command',
        'Handle',
        'Title',
        'Body (HTML)',
        'Vendor',
        'Product Category',
        'Type',
        'Tags',
        'Published',
        'Variant SKU',
        'Variant Grams',
        'Variant Inventory Tracker',
        'Variant Inventory Policy',
        'Variant Fulfillment Service',
        'Variant Price',
        'Variant Compare At Price',
        'Variant Requires Shipping',
        'Variant Taxable',
        'Variant Barcode',
        'Image Src',
        'Image Position',
        'Status',
        'Variant Inventory Qty',
    ]
    
    rows = []
    
    for sku, product in products.items():
        # Get related data
        price_data = pricing.get(sku, {})
        stock_qty = stock.get(sku, 0)
        
        # Determine command and pricing
        is_new = sku in new_skus
        is_discontinued = product.get('discontinued', False)
        
        if is_discontinued:
            command = 'DELETE'  # Archives the product
            status = 'archived'
        elif is_new:
            command = 'MERGE'
            status = 'active'
        else:
            command = 'UPDATE'
            status = 'active'
        
        # Price logic:
        # - New products: RRP as starting price
        # - Existing products: Leave empty (Matrixify skips empty cells)
        # - Discontinued: Doesn't matter
        
        if is_new:
            rrp = price_data.get('rrp') or product.get('rrp') or 0
            price = round(rrp, 2)
            compare_at = ''  # No compare-at for new products initially
        else:
            price = ''  # Empty = don't update
            compare_at = ''
        
        # Build tags
        tags = []
        if product.get('class_a'):
            tags.append(product['class_a'])
        if product.get('class_b'):
            tags.append(product['class_b'])
        if product.get('class_c'):
            tags.append(product['class_c'])
        tags.append('Toolbank')  # Source tag
        if is_new:
            tags.append('New-Import')  # Flag for review
        
        # Handle (URL slug)
        handle = slugify(f"{product['title']}-{sku}")
        
        # Build row
        row = {
            'Command': command,
            'Handle': handle,
            'Title': product['title'],
            'Body (HTML)': product['description'],
            'Vendor': product['vendor'],
            'Product Category': '',  # Let Shopify auto-categorize
            'Type': product.get('class_b', ''),  # Use ClassB as product type
            'Tags': ', '.join(tags),
            'Published': 'TRUE' if status == 'active' else 'FALSE',
            'Variant SKU': sku,
            'Variant Grams': int(product.get('weight', 0) * 1000),  # kg to grams
            'Variant Inventory Tracker': 'shopify',
            'Variant Inventory Policy': 'deny',
            'Variant Fulfillment Service': 'manual',
            'Variant Price': price,
            'Variant Compare At Price': compare_at,
            'Variant Requires Shipping': 'TRUE',
            'Variant Taxable': 'TRUE',
            'Variant Barcode': product.get('barcode', ''),
            'Image Src': generate_image_url(sku, product.get('image_ref')),
            'Image Position': '1',
            'Status': status,
            'Variant Inventory Qty': stock_qty,
        }
        
        rows.append(row)
    
    # Write CSV
    with open(main_csv_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)
    
    print(f"[OUTPUT] Generated {main_csv_path} with {len(rows)} products")
    
    # Update known SKUs (add all non-discontinued)
    updated_known = known_skus | (all_skus - discontinued_skus)
    
    return main_csv_path, updated_known


# ============================================================================
# MAIN SYNC FUNCTION
# ============================================================================

def main():
    """Main sync process"""
    print("=" * 60)
    print("TOOLBANK → SHOPIFY SYNC")
    print(f"Started: {datetime.now().isoformat()}")
    print("=" * 60)
    
    # Ensure output directory exists
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    
    # Load known SKUs
    known_skus = load_known_skus()
    print(f"[INIT] Known SKUs in Shopify: {len(known_skus)}")
    
    # Connect to FTP and download files
    try:
        ftp = connect_ftp()
        
        # Download pricing file
        pricing_file = OUTPUT_DIR / "pricing.csv"
        download_file(ftp, FTP_FILES['pricing'], pricing_file)
        
        # Download products Excel file
        products_file = OUTPUT_DIR / "products.xlsx"
        download_file(ftp, FTP_FILES['products_xlsx'], products_file)
        
        # Download availability file
        availability_file = OUTPUT_DIR / "availability.csv"
        download_file(ftp, FTP_FILES['availability'], availability_file)
        
        ftp.quit()
        print("[FTP] Disconnected")
        
    except Exception as e:
        print(f"[ERROR] FTP failed: {e}")
        raise
    
    # Parse data files
    pricing = parse_pricing_csv(pricing_file)
    stock = parse_availability_csv(availability_file)
    products = parse_products_xlsx(products_file)
    
    # Generate Matrixify CSV
    output_csv, updated_known = generate_matrixify_csv(
        products, pricing, stock, known_skus, OUTPUT_DIR
    )
    
    # Save updated known SKUs
    save_known_skus(updated_known)
    
    print("=" * 60)
    print("SYNC COMPLETE")
    print(f"Output file: {output_csv}")
    print(f"Finished: {datetime.now().isoformat()}")
    print("=" * 60)


if __name__ == "__main__":
    main()
