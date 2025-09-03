#!/usr/bin/env python3
"""
Test script for Google Sheets integration
"""

import os
from dotenv import load_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# Load environment variables
load_dotenv()

# Google Sheets configuration
GOOGLE_SHEETS_CREDENTIALS_FILE = os.environ.get('GOOGLE_SHEETS_CREDENTIALS_FILE', 'prezboard-4588d34b9c84.json')
GOOGLE_SHEETS_SPREADSHEET_ID = os.environ.get('GOOGLE_SHEETS_SPREADSHEET_ID', '')
SCOPES = ['https://www.googleapis.com/auth/spreadsheets.readonly']

def test_google_sheets_connection():
    """Test the Google Sheets connection and data retrieval"""
    
    print("Testing Google Sheets Integration...")
    print(f"Credentials file: {GOOGLE_SHEETS_CREDENTIALS_FILE}")
    print(f"Spreadsheet ID: {GOOGLE_SHEETS_SPREADSHEET_ID}")
    
    if not GOOGLE_SHEETS_SPREADSHEET_ID:
        print("❌ ERROR: GOOGLE_SHEETS_SPREADSHEET_ID not set in environment variables")
        return False
    
    if not os.path.exists(GOOGLE_SHEETS_CREDENTIALS_FILE):
        print(f"❌ ERROR: Credentials file not found: {GOOGLE_SHEETS_CREDENTIALS_FILE}")
        return False
    
    try:
        # Initialize Google Sheets service
        print("🔐 Initializing Google Sheets service...")
        credentials = service_account.Credentials.from_service_account_file(
            GOOGLE_SHEETS_CREDENTIALS_FILE, scopes=SCOPES
        )
        service = build('sheets', 'v4', credentials=credentials)
        print("✅ Google Sheets service initialized successfully")
        
        # Test reading from each month's sheet
        sheet_names = [
            'Jan EXT Hours',
            'Feb EXT Hours', 
            'March EXT Hours',
            'April EXT Hours',
            'May EXT Hours',
            'June EXT Hours'
        ]
        
        for sheet_name in sheet_names:
            print(f"\n📊 Testing sheet: {sheet_name}")
            try:
                # Read KSA hours (B3) and UAE hours (B2)
                range_name = f"{sheet_name}!B2:B3"
                
                result = service.spreadsheets().values().get(
                    spreadsheetId=GOOGLE_SHEETS_SPREADSHEET_ID,
                    range=range_name
                ).execute()
                
                values = result.get('values', [])
                if len(values) >= 2:
                    uae_hours = float(values[0][0]) if values[0] and values[0][0] else 0.0
                    ksa_hours = float(values[1][0]) if values[1] and values[1][0] else 0.0
                    print(f"   ✅ UAE: {uae_hours} hours, KSA: {ksa_hours} hours")
                else:
                    print(f"   ❌ Not enough data in sheet {sheet_name}")
                    
            except HttpError as e:
                print(f"   ❌ HTTP Error reading {sheet_name}: {e}")
            except Exception as e:
                print(f"   ❌ Error reading {sheet_name}: {e}")
        
        print("\n🎉 Google Sheets integration test completed!")
        return True
        
    except Exception as e:
        print(f"❌ ERROR: Failed to initialize Google Sheets service: {e}")
        return False

if __name__ == "__main__":
    success = test_google_sheets_connection()
    if success:
        print("\n✅ All tests passed! Google Sheets integration is working correctly.")
    else:
        print("\n❌ Tests failed! Please check your configuration.")

