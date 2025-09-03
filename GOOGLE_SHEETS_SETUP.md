# Google Sheets Integration Setup

This document explains how to set up the Google Sheets integration for the dashboard to use external hours data for January to June 2025.

## Overview

The dashboard now supports two data sources for external hours:
- **Google Sheets**: For January to June 2025 (corrected data)
- **Odoo**: For all other months (original calculation)

## Setup Instructions

### 1. Google Cloud Setup (Already Done)

✅ Google Cloud account created  
✅ Service account created  
✅ Credentials file downloaded (`prezboard-4588d34b9c84.json`)

### 2. Environment Configuration

Add the following variables to your `.env` file:

```bash
# Google Sheets Configuration
GOOGLE_SHEETS_CREDENTIALS_FILE=prezboard-4588d34b9c84.json
GOOGLE_SHEETS_SPREADSHEET_ID=your_google_sheets_spreadsheet_id_here
```

**Important**: Replace `your_google_sheets_spreadsheet_id_here` with your actual Google Sheets spreadsheet ID.

### 3. Google Sheets Structure

Your Google Sheets should have the following structure:

| Sheet Name | KSA Hours (B3) | UAE Hours (B2) |
|------------|----------------|----------------|
| Jan EXT Hours | [KSA hours] | [UAE hours] |
| Feb EXT Hours | [KSA hours] | [UAE hours] |
| March EXT Hours | [KSA hours] | [UAE hours] |
| April EXT Hours | [KSA hours] | [UAE hours] |
| May EXT Hours | [KSA hours] | [UAE hours] |
| June EXT Hours | [KSA hours] | [UAE hours] |

### 4. Install Dependencies

Install the required Google Sheets API dependencies:

```bash
pip install -r requirements.txt
```

### 5. Test the Integration

Run the test script to verify everything is working:

```bash
python test_google_sheets.py
```

This will test:
- ✅ Credentials file access
- ✅ Google Sheets API connection
- ✅ Data retrieval from all 6 sheets
- ✅ Correct cell reading (B2 for UAE, B3 for KSA)

## How It Works

### Data Source Selection

The system automatically chooses the data source based on the selected period:

- **Jan-Jun 2025**: Uses Google Sheets data
- **All other months**: Uses Odoo calculation

### Function Flow

1. **`get_sales_order_hours_data()`** function checks the period
2. If period is Jan-Jun 2025, calls **`get_hours_from_google_sheets()`**
3. Otherwise, uses the original Odoo logic

### UI Indicators

The dashboard now shows a data source indicator:
- 🔵 **Google Sheets**: Blue badge for Google Sheets data
- 🟠 **Odoo**: Orange badge for Odoo data

## Troubleshooting

### Common Issues

1. **"Credentials file not found"**
   - Ensure `prezboard-4588d34b9c84.json` is in the project root
   - Check the file path in `.env`

2. **"Spreadsheet ID not configured"**
   - Add `GOOGLE_SHEETS_SPREADSHEET_ID` to your `.env` file
   - Get the ID from your Google Sheets URL

3. **"Permission denied"**
   - Ensure the service account has access to the Google Sheets
   - Share the spreadsheet with the service account email

4. **"Sheet not found"**
   - Verify sheet names match exactly: "Jan EXT Hours", "Feb EXT Hours", etc.
   - Check for extra spaces or typos

### Testing

Use the test script to diagnose issues:

```bash
python test_google_sheets.py
```

The script will provide detailed error messages for each step.

## API Endpoints

The integration affects these endpoints:

- **`/api/sales-order-hours`**: Now uses Google Sheets for Jan-Jun 2025
- **`/api/external-hours`**: Still uses Odoo (retainer contracts)

## Data Format

Google Sheets data is returned in the same format as Odoo:

```json
{
  "ksa": {
    "totalHours": 1234.5,
    "orders": []
  },
  "uae": {
    "totalHours": 567.8,
    "orders": []
  },
  "source": "google_sheets",
  "view_type": "monthly",
  "selected_period": "2025-01"
}
```

Note: Google Sheets data doesn't include detailed order information, so the `orders` array is empty.

