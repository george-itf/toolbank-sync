# Toolbank → Shopify Sync

Automated daily sync from Toolbank FTP to Shopify via Matrixify.

## 🔄 How It Works

1. **GitHub Action** runs daily at 6am UK time
2. Downloads data from Toolbank FTP (products, pricing, stock)
3. Generates Matrixify-compatible CSV
4. Commits to this repo
5. **Matrixify** picks up the CSV and imports to Shopify

## 📁 File Structure

```
├── .github/workflows/sync.yml    # GitHub Action
├── scripts/
│   ├── sync_toolbank.py          # Main sync script
│   └── requirements.txt          # Python dependencies
├── output/
│   └── toolbank_import.csv       # Generated Matrixify CSV
├── known_skus.json               # Tracks existing products
└── README.md
```

## ⚙️ Setup

### 1. GitHub Secrets

Add these secrets to your repo (Settings → Secrets → Actions):

| Secret | Value |
|--------|-------|
| `TOOLBANK_FTP_USER` | `Invictatools_9051` |
| `TOOLBANK_FTP_PASS` | `(your password)` |

### 2. Matrixify Configuration

1. Go to **Matrixify → Imports → Scheduled**
2. Create new scheduled import
3. **Source URL:**
   ```
   https://raw.githubusercontent.com/YOUR_USERNAME/toolbank-sync/main/output/toolbank_import.csv
   ```
4. **Schedule:** Daily at 7am UK (1 hour after GitHub sync)
5. **Options:**
   - ✅ Check if items already exist
   - ✅ Ignore ID
   - ❌ Dry Run (OFF)

### 3. Enable GitHub Actions

1. Go to **Actions** tab in your repo
2. Enable workflows if prompted
3. Run manually to test: **Actions → Toolbank Sync → Run workflow**

## 🛒 Sync Behaviour

| Scenario | Action |
|----------|--------|
| **New product** | Created as Active, price = RRP |
| **Existing product** | Updated (stock, description, images) — **price unchanged** |
| **Discontinued product** | Archived in Shopify |

## 📝 Manual Run

Click **Actions → Toolbank Sync → Run workflow** to trigger manually.

## 🔧 Customisation

Edit `scripts/sync_toolbank.py` to change:

- Image URL format
- Tag generation
- Handle format
- Product type mapping

## 📊 Monitoring

Check the **Actions** tab for sync history and logs.

---

Built for Invicta Tools 🔧
