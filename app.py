from flask import Flask, jsonify, request
from flask_cors import CORS
import xmlrpc.client
import os
from dotenv import load_dotenv
import datetime
from dateutil.relativedelta import relativedelta
from collections import defaultdict
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import psutil
import atexit
import re
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

# Google Sheets API imports
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# Load environment variables
load_dotenv()

# Enable holiday subtraction by default unless explicitly overridden
os.environ.setdefault('DISABLE_HOLIDAYS_FOR_DEBUG', 'false')

app = Flask(__name__)
CORS(app)
try:
    # Optional response compression to reduce payload sizes
    from flask_compress import Compress
    Compress(app)
except Exception:
    # Compression is optional; skip if dependency not installed
    pass

# Schedule periodic cache cleanup
import atexit
def cleanup_on_exit():
    """Clean up resources on application exit"""
    clear_expired_cache()

atexit.register(cleanup_on_exit)

# Odoo connection configuration
ODOO_URL = os.environ.get('ODOO_URL', 'https://prezlab-staging-23183574.dev.odoo.com')
ODOO_DB = os.environ.get('ODOO_DB', 'prezlab-staging-23183574')
ODOO_USERNAME = os.environ.get('ODOO_USERNAME', 'omar.elhasan@prezlab.com')
ODOO_PASSWORD = os.environ.get('ODOO_PASSWORD', 'Omar@@1998')

# Google Sheets configuration
GOOGLE_SHEETS_CREDENTIALS_FILE = os.environ.get('GOOGLE_SHEETS_CREDENTIALS_FILE', 'prezboard-4588d34b9c84.json')
GOOGLE_SHEETS_SPREADSHEET_ID = os.environ.get('GOOGLE_SHEETS_SPREADSHEET_ID', '')
SCOPES = ['https://www.googleapis.com/auth/spreadsheets.readonly']

# Performance configuration
ENABLE_PARALLEL_PROCESSING = True  # Re-enabled parallel processing
MAX_WORKERS = 3  # Number of parallel workers (match number of departments to avoid queuing)
REQUEST_TIMEOUT = 45  # Timeout in seconds for parallel requests
CONNECTION_HEALTH_CHECK_INTERVAL = 300  # Check connection health every 5 minutes
MAX_CONSECUTIVE_FAILURES = 2  # Max consecutive failures before marking connection as unhealthy

# Connection pool for Odoo
_odoo_connection_pool = {
    'models': None,
    'uid': None,
    'last_used': None,
    'lock': threading.Lock(),
    'connection_health': 'unknown',  # 'healthy', 'unhealthy', 'unknown'
    'last_health_check': None,
    'consecutive_failures': 0
}

# Cache for storing department data and holiday data
department_cache = {
    'creative': {},
    'creative_strategy': {},
    'cache_timestamps': {},  # Individual timestamps for each cache key
    'cache_duration': 300  # 5 minutes cache duration
}

# Dedicated cache for holiday data to prevent redundant fetching
holiday_cache = {
    'cache_data': {},  # Key format: "holidays_{company_id}_{start_date}_{end_date}"
    'cache_timestamps': {},
    'cache_duration': 3600  # 1 hour cache duration for holidays (they don't change frequently)
}

# Cache for employee data to avoid redundant employee fetching
employee_cache = {
    'cache_data': {},  # Key format: "employees_{department_name}"
    'cache_timestamps': {},
    'cache_duration': 900  # 15 minutes cache duration for employee data
}

cache_lock = threading.Lock()

def get_cache_key(prefix, *args):
    """Generate a cache key from prefix and arguments"""
    return f"{prefix}_{'_'.join(str(arg) for arg in args if arg is not None)}"

def is_cache_valid(cache_dict, key):
    """Check if cache entry is still valid"""
    if key not in cache_dict['cache_data']:
        return False
    
    timestamp = cache_dict['cache_timestamps'].get(key, 0)
    return (time.time() - timestamp) < cache_dict['cache_duration']

def get_from_cache(cache_dict, key):
    """Get data from cache if valid"""
    with cache_lock:
        if is_cache_valid(cache_dict, key):
            return cache_dict['cache_data'][key]
    return None

def set_cache(cache_dict, key, data):
    """Set data in cache with timestamp"""
    with cache_lock:
        cache_dict['cache_data'][key] = data
        cache_dict['cache_timestamps'][key] = time.time()

def clear_expired_cache():
    """Clear expired cache entries"""
    with cache_lock:
        current_time = time.time()
        
        # Clear expired department cache
        expired_keys = []
        for key, timestamp in department_cache['cache_timestamps'].items():
            if current_time - timestamp > department_cache['cache_duration']:
                expired_keys.append(key)
        
        for key in expired_keys:
            department_cache['cache_data'].pop(key, None)
            department_cache['cache_timestamps'].pop(key, None)
        
        # Clear expired holiday cache
        expired_keys = []
        for key, timestamp in holiday_cache['cache_timestamps'].items():
            if current_time - timestamp > holiday_cache['cache_duration']:
                expired_keys.append(key)
        
        for key in expired_keys:
            holiday_cache['cache_data'].pop(key, None)
            holiday_cache['cache_timestamps'].pop(key, None)
        
        # Clear expired employee cache
        expired_keys = []
        for key, timestamp in employee_cache['cache_timestamps'].items():
            if current_time - timestamp > employee_cache['cache_duration']:
                expired_keys.append(key)
        
        for key in expired_keys:
            employee_cache['cache_data'].pop(key, None)
            employee_cache['cache_timestamps'].pop(key, None)

# Helper functions for time formatting
def decimal_hours_to_hm_format(decimal_hours):
    """Convert decimal hours to hours:minutes format string (e.g., 1.5 -> '1h 30m')"""
    if decimal_hours == 0:
        return '0h'
    
    hours = int(decimal_hours)
    minutes = round((decimal_hours - hours) * 60)
    
    # Handle case where rounding minutes gives us 60
    if minutes == 60:
        hours += 1
        minutes = 0
    
    if minutes == 0:
        return f'{hours}h'
    elif hours == 0:
        return f'{minutes}m'
    else:
        return f'{hours}h {minutes}m'

def decimal_hours_to_hm_data(decimal_hours):
    """Convert decimal hours to structured data with hours and minutes"""
    hours = int(decimal_hours)
    minutes = round((decimal_hours - hours) * 60)
    
    # Handle case where rounding minutes gives us 60
    if minutes == 60:
        hours += 1
        minutes = 0
        
    return {
        'decimal': decimal_hours,
        'formatted': decimal_hours_to_hm_format(decimal_hours),
        'hours': hours,
        'minutes': minutes
    }


# Google Sheets helper functions
def get_google_sheets_service():
    """Initialize and return Google Sheets service.
    Supports either a file path via GOOGLE_SHEETS_CREDENTIALS_FILE or raw JSON via GOOGLE_SHEETS_CREDENTIALS_JSON.
    """
    try:
        creds = None
        raw_json = os.environ.get('GOOGLE_SHEETS_CREDENTIALS_JSON')
        if raw_json:
            try:
                info = json.loads(raw_json)
                creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
            except Exception as e:
                print(f"Invalid GOOGLE_SHEETS_CREDENTIALS_JSON: {e}")
        if creds is None:
            creds = service_account.Credentials.from_service_account_file(
                GOOGLE_SHEETS_CREDENTIALS_FILE, scopes=SCOPES
            )
        service = build('sheets', 'v4', credentials=creds)
        return service
    except Exception as e:
        print(f"Error initializing Google Sheets service: {e}")
        return None


def get_hours_from_google_sheets(month_name):
    """
    Get KSA and UAE hours from Google Sheets for a specific month
    Returns: {'ksa': float, 'uae': float} or None if error
    """
    try:
        if not GOOGLE_SHEETS_SPREADSHEET_ID:
            print("Google Sheets Spreadsheet ID not configured")
            return None
            
        service = get_google_sheets_service()
        if not service:
            return None
            
        # Map month names to sheet names
        sheet_name_mapping = {
            'jan': 'Jan EXT Hours',
            'feb': 'Feb EXT Hours', 
            'mar': 'March EXT Hours',
            'apr': 'April EXT Hours',
            'may': 'May EXT Hours',
            'jun': 'June EXT Hours'
        }
        
        sheet_name = sheet_name_mapping.get(month_name.lower())
        if not sheet_name:
            print(f"Invalid month name: {month_name}")
            return None
            
        # Read KSA hours (B3) and UAE hours (B2)
        range_name = f"{sheet_name}!B2:B3"
        
        result = service.spreadsheets().values().get(
            spreadsheetId=GOOGLE_SHEETS_SPREADSHEET_ID,
            range=range_name
        ).execute()
        
        values = result.get('values', [])
        if len(values) < 2:
            print(f"Not enough data in sheet {sheet_name}")
            return None
            
        # Extract UAE hours (B2) and KSA hours (B3)
        uae_hours = float(values[0][0]) if values[0] and values[0][0] else 0.0
        ksa_hours = float(values[1][0]) if values[1] and values[1][0] else 0.0
        
        print(f"Retrieved from Google Sheets - {month_name}: KSA={ksa_hours}, UAE={uae_hours}")
        
        return {
            'ksa': ksa_hours,
            'uae': uae_hours
        }
        
    except HttpError as e:
        print(f"Google Sheets API error: {e}")
        return None
    except Exception as e:
        print(f"Error reading from Google Sheets: {e}")
        return None


def should_use_google_sheets(period):
    """
    Check if we should use Google Sheets data for the given period
    Returns True for Jan-Jun 2025, False otherwise
    """
    try:
        if not period:
            return False
            
        # Parse period (format: YYYY-MM)
        year, month = period.split('-')
        year = int(year)
        month = int(month)
        
        # Return True for January to June 2025
        return year == 2025 and 1 <= month <= 6
        
    except Exception as e:
        print(f"Error parsing period {period}: {e}")
        return False



# Simple LRU/TTL cache for employee category names to avoid repeated reads
CATEGORY_CACHE_TTL_SECONDS = int(os.environ.get('CATEGORY_CACHE_TTL_SECONDS', '3600'))
_category_cache_lock = threading.Lock()
_category_cache = {}  # id -> { 'name': str, 'ts': float }

def get_category_names_cached(models, uid, category_ids):
    """Return mapping of category_id -> name using a process cache with TTL; fetch missing in one batch."""
    if not category_ids:
        return {}
    now_ts = time.time()
    missing_ids = []
    result = {}
    with _category_cache_lock:
        for cid in category_ids:
            cached = _category_cache.get(cid)
            if cached and (now_ts - cached['ts'] <= CATEGORY_CACHE_TTL_SECONDS) and cached.get('name'):
                result[cid] = cached['name']
            else:
                missing_ids.append(cid)
    if missing_ids:
        try:
            categories = models.execute_kw(
                ODOO_DB, uid, ODOO_PASSWORD,
                'hr.employee.category', 'read',
                [list(set(missing_ids))], {'fields': ['name']}
            )
            with _category_cache_lock:
                for cat in categories:
                    name = cat.get('name')
                    cid = cat.get('id')
                    if cid is None:
                        continue
                    _category_cache[cid] = {'name': name, 'ts': now_ts}
                    result[cid] = name
        except Exception as e:
            # If fetch fails, return what we have
            print(f"Category cache fetch failed: {e}")
    return result

# Background cache warmer to precompute hot datasets periodically
CACHE_WARM_INTERVAL_SECONDS = int(os.environ.get('CACHE_WARM_INTERVAL_SECONDS', '600'))
def _warm_cache_once():
    try:
        models, uid = connect_to_odoo()
        if not models or not uid:
            return
        # Warm current month and current week for all departments
        today = datetime.date.today()
        period_month = today.strftime('%Y-%m')
        # ISO week; ensure two digits
        iso_week = today.isocalendar()[1]
        period_week = f"{today.year}-{iso_week:02d}"
        for view_type, period in (('monthly', period_month), ('weekly', period_week)):
            for dept in ('Creative', 'Creative Strategy', 'Instructional Design'):
                try:
                    data = fetch_department_data_parallel(dept, period, view_type)
                    if data:
                        key = 'creative' if dept == 'Creative' else 'creative_strategy' if dept == 'Creative Strategy' else 'instructional_design'
                        set_cached_data(key, data, period, view_type)
                except Exception as _e:
                    continue
    except Exception:
        pass

_cache_warm_thread = None
_cache_warm_stop = threading.Event()

def _cache_warmer_loop():
    while not _cache_warm_stop.is_set():
        _warm_cache_once()
        _cache_warm_stop.wait(CACHE_WARM_INTERVAL_SECONDS)

def start_cache_warmer():
    global _cache_warm_thread
    if _cache_warm_thread is None or not _cache_warm_thread.is_alive():
        _cache_warm_thread = threading.Thread(target=_cache_warmer_loop, daemon=True)
        _cache_warm_thread.start()

def stop_cache_warmer():
    _cache_warm_stop.set()

# Start warmer on import and ensure it stops on exit
start_cache_warmer()
atexit.register(stop_cache_warmer)

# Cache: resource.calendar.id -> set of working weekdays (Mon=0..Sun=6)
calendar_weekdays_cache = {}

def get_cached_data(department, period=None, view_type='monthly'):
    """
    Get cached data for a specific department, period, and view type.
    
    Args:
        department (str): 'creative' or 'creative_strategy'
        period (str): Period in 'YYYY-MM' or 'YYYY-WW' format
        view_type (str): 'monthly', 'weekly', or 'daily'
    
    Returns:
        dict: Cached data if valid, None if expired or not found
    """
    with cache_lock:
        if department not in department_cache:
            return None
        
        cache_key = f"{period}_{view_type}" if period else f"default_{view_type}"
        if cache_key not in department_cache[department]:
            return None
        
        # Check if cache is still valid using individual timestamp
        if cache_key in department_cache['cache_timestamps']:
            cache_age = time.time() - department_cache['cache_timestamps'][cache_key]
            if cache_age > department_cache['cache_duration']:
                return None
        
        return department_cache[department][cache_key]

def set_cached_data(department, data, period=None, view_type='monthly'):
    """
    Store data in cache for a specific department, period, and view type.
    
    Args:
        department (str): 'creative' or 'creative_strategy'
        data (dict): Data to cache
        period (str): Period in 'YYYY-MM' or 'YYYY-WW' format
        view_type (str): 'monthly', 'weekly', or 'daily'
    """
    with cache_lock:
        if department not in department_cache:
            department_cache[department] = {}
        
        cache_key = f"{period}_{view_type}" if period else f"default_{view_type}"
        department_cache[department][cache_key] = data
        department_cache['cache_timestamps'][cache_key] = time.time()

def clear_cache():
    """Clear all cached data."""
    with cache_lock:
        department_cache['creative'] = {}
        department_cache['creative_strategy'] = {}
        department_cache['cache_timestamps'] = {}

def get_connection_status():
    """Get connection pool status information."""
    with _odoo_connection_pool['lock']:
        return {
            'connection_health': _odoo_connection_pool['connection_health'],
            'consecutive_failures': _odoo_connection_pool['consecutive_failures'],
            'last_health_check': _odoo_connection_pool['last_health_check'],
            'last_used': _odoo_connection_pool['last_used'],
            'has_models': _odoo_connection_pool['models'] is not None,
            'has_uid': _odoo_connection_pool['uid'] is not None
        }

def get_cache_status():
    """Get cache status information."""
    with cache_lock:
        return {
            'cache_timestamps': department_cache['cache_timestamps'],
            'cache_duration': department_cache['cache_duration'],
            'creative_periods': list(department_cache.get('creative', {}).keys()),
            'creative_strategy_periods': list(department_cache.get('creative_strategy', {}).keys())
        }

def fetch_department_data_parallel(department_name, period=None, view_type='monthly'):
    """
    Fetch all data for a department in parallel using ThreadPoolExecutor.
    This significantly speeds up data retrieval by making concurrent API calls.
    """
    try:
        models, uid = connect_to_odoo()
        if not models or not uid:
            print(f"Failed to connect to Odoo for {department_name}")
            return None
        
        start_date, end_date = get_date_range(view_type, period)
        
        # Get department employees
        try:
            department_ids = find_department_flexible(models, uid, department_name)
            
            if not department_ids:
                print(f"No department found for {department_name}")
                return None
            
            employee_ids = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'hr.employee', 'search', 
                                           [[('department_id', 'in', department_ids)]])
            
            if not employee_ids:
                print(f"No employees found for {department_name}")
                return None
            
            # Get employee details
            employees_data = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'hr.employee', 'read', 
                                             [employee_ids], {
                                                 'fields': ['id', 'name', 'job_title', 'category_ids']
                                             })
        except Exception as e:
            print(f"Error fetching employee data for {department_name}: {e}")
            return None
        
        # Batch fetch categories using LRU/TTL cache
        all_category_ids = set()
        for emp in employees_data:
            if emp.get('category_ids'):
                all_category_ids.update(emp['category_ids'])
        categories_dict = get_category_names_cached(models, uid, list(all_category_ids))
        
        # Prepare date strings
        start_str = start_date.strftime('%Y-%m-%d')
        end_str = end_date.strftime('%Y-%m-%d')
        start_planning_str = start_date.strftime('%Y-%m-%d 00:00:00')
        end_planning_str = end_date.strftime('%Y-%m-%d 23:59:59')
        
        def fetch_timesheets():
            """Fetch timesheet data with pagination for large datasets"""
            try:
                all_timesheets = []
                offset = 0
                limit = 500  # Reduced batch size for better reliability
                
                while True:
                    timesheets = models.execute_kw(
                        ODOO_DB, uid, ODOO_PASSWORD,
                        'account.analytic.line', 'search_read',
                        [[('employee_id', 'in', employee_ids),
                          ('date', '>=', start_str),
                          ('date', '<=', end_str),
                          ('task_id.name', '!=', 'Time Off')]],
                        {'fields': ['employee_id', 'unit_amount'], 'offset': offset, 'limit': limit}
                    )
                    
                    if not timesheets:
                        break
                        
                    all_timesheets.extend(timesheets)
                    offset += limit
                    
                    # Stop if we got fewer results than the limit (end of data)
                    if len(timesheets) < limit:
                        break
                
                return all_timesheets
            except Exception as e:
                print(f"Error fetching timesheets for {department_name}: {e}")
                return []
        
        def fetch_planning_slots():
            """Fetch planning slots data with pagination"""
            try:
                all_slots = []
                offset = 0
                limit = 500
                
                while True:
                    slots = models.execute_kw(
                        ODOO_DB, uid, ODOO_PASSWORD,
                        'planning.slot', 'search_read',
                        [[('resource_id', 'in', employee_ids),
                          ('start_datetime', '<=', end_planning_str),
                          ('end_datetime', '>=', start_planning_str)]],
                        {'fields': ['resource_id', 'allocated_hours', 'start_datetime', 'end_datetime'], 
                         'offset': offset, 'limit': limit}
                    )
                    
                    if not slots:
                        break
                        
                    all_slots.extend(slots)
                    offset += limit
                    
                    if len(slots) < limit:
                        break
                
                return all_slots
            except Exception as e:
                print(f"Error fetching planning slots for {department_name}: {e}")
                return []
        
        def fetch_time_off():
            """Fetch time off data with pagination"""
            try:
                all_time_off = []
                offset = 0
                limit = 500
                
                while True:
                    time_off = models.execute_kw(
                        ODOO_DB, uid, ODOO_PASSWORD,
                        'account.analytic.line', 'search_read',
                        [[('employee_id', 'in', employee_ids),
                          ('date', '>=', start_str),
                          ('date', '<=', end_str),
                          ('task_id.name', '=', 'Time Off')]],
                        {'fields': ['employee_id', 'unit_amount'], 'offset': offset, 'limit': limit}
                    )
                    
                    if not time_off:
                        break
                        
                    all_time_off.extend(time_off)
                    offset += limit
                    
                    if len(time_off) < limit:
                        break
                
                return all_time_off
            except Exception as e:
                print(f"Error fetching time off data for {department_name}: {e}")
                return []
        
        # Execute all API calls in parallel with better error handling
        try:
            with ThreadPoolExecutor(max_workers=2) as executor:  # Reduced workers for stability
                future_timesheets = executor.submit(fetch_timesheets)
                future_planning = executor.submit(fetch_planning_slots)
                future_time_off = executor.submit(fetch_time_off)
                
                # Wait for all results with timeout
                timesheets = future_timesheets.result(timeout=30)
                planning_slots = future_planning.result(timeout=30)
                time_off_timesheets = future_time_off.result(timeout=30)
                
        except Exception as e:
            print(f"Error in parallel execution for {department_name}: {e}")
            # Fallback to sequential execution
            print(f"Falling back to sequential execution for {department_name}")
            timesheets = fetch_timesheets()
            planning_slots = fetch_planning_slots()
            time_off_timesheets = fetch_time_off()
        
        # Process results (same logic as existing functions)
        employee_data = {}
        for emp in employees_data:
            emp_id = emp['id']
            tags = []
            if emp.get('category_ids'):
                tags = [categories_dict.get(cat_id) for cat_id in emp['category_ids'] if categories_dict.get(cat_id)]
            
            employee_data[emp_id] = {
                'name': emp.get('name', ''),
                'job_title': emp.get('job_title', ''),
                'tags': tags,
                'logged_hours': 0,
                'planned_hours': 0,
                'time_off_hours': 0
            }
        
        # Calculate time off hours
        for ts in time_off_timesheets:
            emp_field = ts.get('employee_id')
            if emp_field:
                emp_id = emp_field[0] if isinstance(emp_field, list) else emp_field
                if emp_id in employee_data:
                    employee_data[emp_id]['time_off_hours'] += float(ts.get('unit_amount', 0))
        
        # Calculate logged hours
        for ts in timesheets:
            emp_field = ts.get('employee_id')
            if emp_field:
                emp_id = emp_field[0] if isinstance(emp_field, list) else emp_field
                if emp_id in employee_data:
                    employee_data[emp_id]['logged_hours'] += float(ts.get('unit_amount', 0))
        
        # Calculate planned hours with edge case handling
        for slot in planning_slots:
            res_field = slot.get('resource_id')
            if res_field:
                emp_id = res_field[0] if isinstance(res_field, list) else res_field
                if emp_id in employee_data:
                    allocated_hours = slot.get('allocated_hours', 0)
                    
                    # Edge case handling
                    task_start = datetime.datetime.strptime(slot['start_datetime'], '%Y-%m-%d %H:%M:%S')
                    task_end = datetime.datetime.strptime(slot['end_datetime'], '%Y-%m-%d %H:%M:%S')
                    filter_start = datetime.datetime.strptime(start_planning_str, '%Y-%m-%d %H:%M:%S')
                    
                    # Calculate proportional hours for slots that span across the filter period
                    filter_end = datetime.datetime.strptime(end_planning_str, '%Y-%m-%d %H:%M:%S')
                    
                    if task_start < filter_start or task_end > filter_end:
                        # Calculate overlap between task and filter period
                        overlap_start = max(task_start, filter_start)
                        overlap_end = min(task_end, filter_end)
                        
                        if overlap_start < overlap_end:
                            # Calculate what proportion of the original task falls within the filter period
                            total_task_duration = (task_end - task_start).total_seconds()
                            overlap_duration = (overlap_end - overlap_start).total_seconds()
                            proportion = overlap_duration / total_task_duration if total_task_duration > 0 else 0
                            
                            # Apply proportion to the original allocated hours
                            original_hours = allocated_hours
                            allocated_hours = allocated_hours * proportion
                            print(f"Slot spans period: {slot['start_datetime']} to {slot['end_datetime']}, original {original_hours}h, proportional {allocated_hours:.2f}h")
                        else:
                            allocated_hours = 0
                    
                    employee_data[emp_id]['planned_hours'] += allocated_hours
        
        # Convert to list format
        employees_list = []
        for emp_id, data in employee_data.items():
            employees_list.append({
                'id': emp_id,
                'name': data['name'],
                'job_title': data['job_title'],
                'tags': data['tags'],
                'total_hours': data['logged_hours'],
                'timesheet_entries': [],  # Simplified for performance
                'period_start': start_date.isoformat(),
                'period_end': end_date.isoformat()
            })
        
        # Get team utilization data using the proper function with view_type parameter
        if department_name == 'Creative':
            team_stats = get_team_utilization_data(period, view_type)
            print(f"DEBUG: Creative team_stats type: {type(team_stats)}")
            print(f"DEBUG: Creative team_stats keys: {list(team_stats.keys()) if team_stats else 'None'}")
        elif department_name == 'Creative Strategy':
            team_stats = get_creative_strategy_team_utilization_data(period, view_type)
            print(f"DEBUG: Creative Strategy team_stats type: {type(team_stats)}")
            print(f"DEBUG: Creative Strategy team_stats keys: {list(team_stats.keys()) if team_stats else 'None'}")
        else:
            team_stats = {}  # Fallback
        
        # Get available resources using the proper function with view_type parameter
        if department_name == 'Creative':
            available_resources = get_available_creative_resources(view_type, period)
            print(f"DEBUG: Creative available_resources type: {type(available_resources)}")
            print(f"DEBUG: Creative available_resources length: {len(available_resources) if available_resources else 0}")
            if available_resources and len(available_resources) > 0:
                print(f"DEBUG: First Creative resource keys: {list(available_resources[0].keys())}")
                print(f"DEBUG: First Creative resource base_available_hours: {available_resources[0].get('base_available_hours', 'NOT_FOUND')}")
        elif department_name == 'Creative Strategy':
            available_resources = get_available_creative_strategy_resources(view_type, period)
        else:
            available_resources = employees_list  # Fallback
        
        # Get timesheet data using the proper function with view_type parameter
        if department_name == 'Creative':
            timesheet_data = get_creative_timesheet_data(period, view_type)
            print(f"DEBUG: Creative timesheet_data type: {type(timesheet_data)}")
            print(f"DEBUG: Creative timesheet_data length: {len(timesheet_data) if timesheet_data else 0}")
        elif department_name == 'Creative Strategy':
            timesheet_data = get_creative_strategy_timesheet_data(period, view_type)
            print(f"DEBUG: Creative Strategy timesheet_data type: {type(timesheet_data)}")
            print(f"DEBUG: Creative Strategy timesheet_data length: {len(timesheet_data) if timesheet_data else 0}")
        else:
            timesheet_data = employees_list  # Fallback
        
        return {
            'employees': employees_list,
            'team_utilization': team_stats,
            'timesheet_data': timesheet_data,
            'available_resources': available_resources
        }
        
    except Exception as e:
        print(f"Error in parallel fetch for {department_name}: {e}")
        return None

def fetch_department_data_sequential(department_name, period=None, view_type='monthly'):
    """
    Fetch department data sequentially as a reliable fallback.
    This is slower but more stable than parallel processing.
    """
    try:
        print(f"Starting sequential fetch for {department_name}...")
        models, uid = connect_to_odoo()
        if not models or not uid:
            print(f"Failed to connect to Odoo for {department_name}")
            return None
        
        print(f"Successfully connected to Odoo for {department_name}")
        start_date, end_date = get_date_range(view_type, period)
        
        # Get department employees
        print(f"Fetching department info for {department_name}...")
        department_ids = find_department_flexible(models, uid, department_name)
        
        if not department_ids:
            print(f"No department found for {department_name}")
            return None
        
        print(f"Found department IDs: {department_ids}")
        employee_ids = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'hr.employee', 'search', 
                                       [[('department_id', 'in', department_ids)]])
        
        if not employee_ids:
            print(f"No employees found for {department_name}")
            return None
        
        print(f"Found {len(employee_ids)} employees for {department_name}")
        
        # Get employee details
        print(f"Fetching employee details for {department_name}...")
        employees_data = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'hr.employee', 'read', 
                                         [employee_ids], {
                                             'fields': ['id', 'name', 'job_title', 'category_ids']
                                         })
        
        print(f"Successfully fetched {len(employees_data)} employee records")
        
        # Batch fetch categories
        all_category_ids = set()
        for emp in employees_data:
            if emp.get('category_ids'):
                all_category_ids.update(emp['category_ids'])
        
        categories_dict = {}
        if all_category_ids:
            print(f"Fetching {len(all_category_ids)} categories...")
            categories = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'hr.employee.category', 'read', 
                                         [list(all_category_ids)], {'fields': ['name']})
            categories_dict = {cat['id']: cat['name'] for cat in categories if cat.get('name')}
            print(f"Successfully fetched {len(categories_dict)} categories")
        
        # Prepare date strings
        start_str = start_date.strftime('%Y-%m-%d')
        end_str = end_date.strftime('%Y-%m-%d')
        start_planning_str = start_date.strftime('%Y-%m-%d 00:00:00')
        end_planning_str = end_date.strftime('%Y-%m-%d 23:59:59')
        
        # Fetch data sequentially
        print(f"Fetching timesheet data for {department_name}...")
        timesheets = models.execute_kw(
            ODOO_DB, uid, ODOO_PASSWORD,
            'account.analytic.line', 'search_read',
            [[('employee_id', 'in', employee_ids),
              ('date', '>=', start_str),
              ('date', '<=', end_str),
              ('task_id.name', '!=', 'Time Off')]],
            {'fields': ['employee_id', 'unit_amount']}
        )
        print(f"Found {len(timesheets)} timesheet entries")
        
        print(f"Fetching planning data for {department_name}...")
        planning_slots = models.execute_kw(
            ODOO_DB, uid, ODOO_PASSWORD,
            'planning.slot', 'search_read',
            [[('resource_id', 'in', employee_ids),
              ('start_datetime', '<=', end_planning_str),
              ('end_datetime', '>=', start_planning_str)]],
            {'fields': ['resource_id', 'allocated_hours', 'start_datetime', 'end_datetime']}
        )
        print(f"Found {len(planning_slots)} planning slots")
        
        print(f"Fetching time off data for {department_name}...")
        time_off_timesheets = models.execute_kw(
            ODOO_DB, uid, ODOO_PASSWORD,
            'account.analytic.line', 'search_read',
            [[('employee_id', 'in', employee_ids),
              ('date', '>=', start_str),
              ('date', '<=', end_str),
              ('task_id.name', '=', 'Time Off')]],
            {'fields': ['employee_id', 'unit_amount']}
        )
        print(f"Found {len(time_off_timesheets)} time off entries")
        
        # Process results (same logic as parallel function)
        employee_data = {}
        for emp in employees_data:
            emp_id = emp['id']
            tags = []
            if emp.get('category_ids'):
                tags = [categories_dict.get(cat_id) for cat_id in emp['category_ids'] if categories_dict.get(cat_id)]
            
            employee_data[emp_id] = {
                'name': emp.get('name', ''),
                'job_title': emp.get('job_title', ''),
                'tags': tags,
                'logged_hours': 0,
                'planned_hours': 0,
                'time_off_hours': 0
            }
        
        # Calculate time off hours
        for ts in time_off_timesheets:
            emp_field = ts.get('employee_id')
            if emp_field:
                emp_id = emp_field[0] if isinstance(emp_field, list) else emp_field
                if emp_id in employee_data:
                    employee_data[emp_id]['time_off_hours'] += float(ts.get('unit_amount', 0))
        
        # Calculate logged hours
        for ts in timesheets:
            emp_field = ts.get('employee_id')
            if emp_field:
                emp_id = emp_field[0] if isinstance(emp_field, list) else emp_field
                if emp_id in employee_data:
                    employee_data[emp_id]['logged_hours'] += float(ts.get('unit_amount', 0))
        
        # Calculate planned hours with edge case handling
        for slot in planning_slots:
            res_field = slot.get('resource_id')
            if res_field:
                emp_id = res_field[0] if isinstance(res_field, list) else res_field
                if emp_id in employee_data:
                    allocated_hours = slot.get('allocated_hours', 0)
                    
                    # Edge case handling
                    task_start = datetime.datetime.strptime(slot['start_datetime'], '%Y-%m-%d %H:%M:%S')
                    task_end = datetime.datetime.strptime(slot['end_datetime'], '%Y-%m-%d %H:%M:%S')
                    filter_start = datetime.datetime.strptime(start_planning_str, '%Y-%m-%d %H:%M:%S')
                    
                    # Calculate proportional hours for slots that span across the filter period
                    filter_end = datetime.datetime.strptime(end_planning_str, '%Y-%m-%d %H:%M:%S')
                    
                    if task_start < filter_start or task_end > filter_end:
                        # Calculate overlap between task and filter period
                        overlap_start = max(task_start, filter_start)
                        overlap_end = min(task_end, filter_end)
                        
                        if overlap_start < overlap_end:
                            # Calculate what proportion of the original task falls within the filter period
                            total_task_duration = (task_end - task_start).total_seconds()
                            overlap_duration = (overlap_end - overlap_start).total_seconds()
                            proportion = overlap_duration / total_task_duration if total_task_duration > 0 else 0
                            
                            # Apply proportion to the original allocated hours
                            original_hours = allocated_hours
                            allocated_hours = allocated_hours * proportion
                            print(f"Slot spans period: {slot['start_datetime']} to {slot['end_datetime']}, original {original_hours}h, proportional {allocated_hours:.2f}h")
                        else:
                            allocated_hours = 0
                    
                    employee_data[emp_id]['planned_hours'] += allocated_hours
        
        # Convert to list format
        employees_list = []
        for emp_id, data in employee_data.items():
            employees_list.append({
                'id': emp_id,
                'name': data['name'],
                'job_title': data['job_title'],
                'tags': data['tags'],
                'total_hours': data['logged_hours'],
                'timesheet_entries': [],
                'period_start': start_date.isoformat(),
                'period_end': end_date.isoformat()
            })
        
        print(f"Processed {len(employees_list)} employees for {department_name}")
        
        # Get team utilization data using the proper function with view_type parameter
        if department_name == 'Creative':
            team_stats = get_team_utilization_data(period, view_type)
            print(f"DEBUG: Creative team_stats type: {type(team_stats)}")
            print(f"DEBUG: Creative team_stats keys: {list(team_stats.keys()) if team_stats else 'None'}")
        elif department_name == 'Creative Strategy':
            team_stats = get_creative_strategy_team_utilization_data(period, view_type)
            print(f"DEBUG: Creative Strategy team_stats type: {type(team_stats)}")
            print(f"DEBUG: Creative Strategy team_stats keys: {list(team_stats.keys()) if team_stats else 'None'}")
        else:
            team_stats = {}  # Fallback
        
        # Get available resources using the proper function with view_type parameter
        if department_name == 'Creative':
            available_resources = get_available_creative_resources(view_type, period)
            print(f"DEBUG: Creative available_resources type: {type(available_resources)}")
            print(f"DEBUG: Creative available_resources length: {len(available_resources) if available_resources else 0}")
            if available_resources and len(available_resources) > 0:
                print(f"DEBUG: First Creative resource keys: {list(available_resources[0].keys())}")
                print(f"DEBUG: First Creative resource base_available_hours: {available_resources[0].get('base_available_hours', 'NOT_FOUND')}")
        elif department_name == 'Creative Strategy':
            available_resources = get_available_creative_strategy_resources(view_type, period)
        else:
            available_resources = employees_list  # Fallback
        
        # Get timesheet data using the proper function with view_type parameter
        if department_name == 'Creative':
            timesheet_data = get_creative_timesheet_data(period, view_type)
            print(f"DEBUG: Creative timesheet_data type: {type(timesheet_data)}")
            print(f"DEBUG: Creative timesheet_data length: {len(timesheet_data) if timesheet_data else 0}")
        elif department_name == 'Creative Strategy':
            timesheet_data = get_creative_strategy_timesheet_data(period, view_type)
            print(f"DEBUG: Creative Strategy timesheet_data type: {type(timesheet_data)}")
            print(f"DEBUG: Creative Strategy timesheet_data length: {len(timesheet_data) if timesheet_data else 0}")
        else:
            timesheet_data = employees_list  # Fallback
        
        result = {
            'employees': employees_list,
            'team_utilization': team_stats,
            'timesheet_data': timesheet_data,
            'available_resources': available_resources
        }
        
        print(f"Successfully completed sequential fetch for {department_name}")
        return result
        
    except Exception as e:
        print(f"Error in sequential fetch for {department_name}: {e}")
        import traceback
        traceback.print_exc()
        return None

def get_proper_department_name(department_key):
    """
    Convert department dictionary keys to proper department names.
    """
    mapping = {
        'creative': 'Creative',
        'creative_strategy': 'Creative Strategy', 
        'instructional_design': 'Instructional Design'
    }
    return mapping.get(department_key, department_key)

def find_department_flexible(models, uid, department_name):
    """
    Find a department using flexible search logic.
    Tries exact matches first, then partial matches.
    """
    print(f"find_department_flexible called with department_name: '{department_name}'")
    
    # Handle different formats of Instructional Design department name
    if department_name in ['Instructional Design', 'instructional_design', 'instructional-design']:
        # Try multiple possible names for Instructional Design
        possible_names = [
            'Instructional Design',
            'Instructional Design Department',
            'InstructionalDesign',
            'Instructional_Design',
            'ID',
            'ID Department'
        ]
        
        department_ids = []
        found_name = None
        
        for name in possible_names:
            department_ids = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'hr.department', 'search', 
                                             [[('name', '=', name)]])
            if department_ids:
                found_name = name
                break
        
        # If exact match not found, try partial match
        if not department_ids:
            print("No exact match found, trying partial search with 'Instructional'...")
            department_ids = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'hr.department', 'search', 
                                             [[('name', 'ilike', 'Instructional')]])
            if department_ids:
                # Get the department names to see what we found
                dept_details = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'hr.department', 'read', 
                                               [department_ids], {'fields': ['name']})
                found_names = [dept['name'] for dept in dept_details]
                print(f"Found departments with 'Instructional' in name: {found_names}")
                found_name = found_names[0] if found_names else None
            else:
                print("No departments found with 'Instructional' in name")
        
        if department_ids:
            print(f"Found {department_name} department with ID: {department_ids}, name: '{found_name}'")
        else:
            print(f"Failed to find {department_name} department")
        
        return department_ids
    else:
        # For other departments, use exact match
        return models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'hr.department', 'search', 
                               [[('name', '=', department_name)]])

def check_connection_health(models, uid):
    """Check if the current connection is healthy by making a simple test call"""
    try:
        if not models or not uid:
            return False
        
        # Make a simple, fast call to test connection
        test_result = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'res.users', 'search_count', [[]])
        return True
    except Exception:
        return False

def connect_to_odoo():
    """Establish connection to Odoo with connection pooling and retry logic"""
    with _odoo_connection_pool['lock']:
        current_time = time.time()
        
        # Check if we have a valid cached connection
        if (_odoo_connection_pool['models'] and 
            _odoo_connection_pool['uid'] and 
            _odoo_connection_pool['last_used']):
            
            # Check if connection is still healthy (every 5 minutes)
            if (_odoo_connection_pool['last_health_check'] is None or 
                current_time - _odoo_connection_pool['last_health_check'] > CONNECTION_HEALTH_CHECK_INTERVAL):
                
                # Perform health check
                if check_connection_health(_odoo_connection_pool['models'], _odoo_connection_pool['uid']):
                    _odoo_connection_pool['connection_health'] = 'healthy'
                    _odoo_connection_pool['consecutive_failures'] = 0
                else:
                    _odoo_connection_pool['connection_health'] = 'unhealthy'
                    _odoo_connection_pool['consecutive_failures'] += 1
                
                _odoo_connection_pool['last_health_check'] = current_time
            
            # If connection is healthy and recent, reuse it
            if (_odoo_connection_pool['connection_health'] == 'healthy' and
                current_time - _odoo_connection_pool['last_used'] < 300):  # 5 minutes
                
                _odoo_connection_pool['last_used'] = current_time
                return _odoo_connection_pool['models'], _odoo_connection_pool['uid']
            
            # If too many consecutive failures, force reconnection
            if _odoo_connection_pool['consecutive_failures'] >= MAX_CONSECUTIVE_FAILURES:
                print(f"Too many consecutive failures ({_odoo_connection_pool['consecutive_failures']}), forcing reconnection")
                _odoo_connection_pool['models'] = None
                _odoo_connection_pool['uid'] = None
                _odoo_connection_pool['last_used'] = None
                _odoo_connection_pool['connection_health'] = 'unknown'
                _odoo_connection_pool['consecutive_failures'] = 0
        
        # Retry logic for connection - reduced to 2 attempts for faster failure detection
        max_retries = 2
        for attempt in range(max_retries):
            try:
                # Clear any existing connection
                _odoo_connection_pool['models'] = None
                _odoo_connection_pool['uid'] = None
                _odoo_connection_pool['last_used'] = None
                
                # Create XML-RPC proxies with timeout and better connection handling
                import http.client
                # Configure longer timeout and disable connection reuse for stability
                timeout = 30  # 30 second timeout
                common = xmlrpc.client.ServerProxy(f'{ODOO_URL}/xmlrpc/2/common', 
                                                 allow_none=True, verbose=False)
                models = xmlrpc.client.ServerProxy(f'{ODOO_URL}/xmlrpc/2/object', 
                                                 allow_none=True, verbose=False)
                
                # Cross-platform timeout implementation
                import threading
                import queue
                
                def authenticate_with_timeout():
                    """Authenticate with timeout using threading"""
                    result_queue = queue.Queue()
                    
                    def auth_worker():
                        try:
                            uid = common.authenticate(ODOO_DB, ODOO_USERNAME, ODOO_PASSWORD, {})
                            result_queue.put(('success', uid))
                        except Exception as e:
                            result_queue.put(('error', str(e)))
                    
                    # Start authentication in a separate thread
                    auth_thread = threading.Thread(target=auth_worker)
                    auth_thread.daemon = True
                    auth_thread.start()
                    
                    # Wait for result with timeout
                    try:
                        result_type, result_data = result_queue.get(timeout=15)  # Increased timeout to 15 seconds
                        if result_type == 'success':
                            return result_data
                        else:
                            raise Exception(f"Authentication failed: {result_data}")
                    except queue.Empty:
                        raise Exception("Connection timeout during authentication")
                
                # Authenticate with timeout
                uid = authenticate_with_timeout()
                
                if not uid:
                    raise Exception("Authentication failed")
                
                # Test the connection with a simple call
                try:
                    # Test connection with a simple call
                    test_result = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'res.users', 'search_count', [[]])
                    print(f"Connection test successful: {test_result} users found")
                except Exception as test_error:
                    print(f"Connection test failed: {test_error}")
                    raise Exception(f"Connection test failed: {test_error}")
                
                # Cache the connection
                _odoo_connection_pool['models'] = models
                _odoo_connection_pool['uid'] = uid
                _odoo_connection_pool['last_used'] = current_time
                _odoo_connection_pool['connection_health'] = 'healthy'
                _odoo_connection_pool['consecutive_failures'] = 0
                _odoo_connection_pool['last_health_check'] = current_time
                
                print(f"Successfully connected to Odoo (attempt {attempt + 1})")
                return models, uid
                    
            except Exception as e:
                error_str = str(e)
                print(f"Error connecting to Odoo (attempt {attempt + 1}/{max_retries}): {error_str}")
                
                # Handle specific connection errors that require fresh connection
                if any(error_type in error_str for error_type in [
                    'Request-sent', 'CannotSendRequest', 'Connection aborted', 
                    'RemoteDisconnected', 'BadStatusLine', 'timeout', 'Idle'
                ]):
                    print(f"Connection error detected, attempting to reconnect...")
                
                # Clear cached connection on error
                _odoo_connection_pool['models'] = None
                _odoo_connection_pool['uid'] = None
                _odoo_connection_pool['last_used'] = None
                _odoo_connection_pool['connection_health'] = 'unhealthy'
                
                # Wait before retry with exponential backoff for connection errors
                if attempt < max_retries - 1:
                    wait_time = min(2 ** attempt, 5)  # Exponential backoff, max 5 seconds
                    print(f"Waiting {wait_time} second(s) before retry...")
                    time.sleep(wait_time)
        
        print("All connection attempts failed")
        return None, None

def update_connection_health(success=True):
    """Update connection health status after API calls"""
    with _odoo_connection_pool['lock']:
        if success:
            _odoo_connection_pool['connection_health'] = 'healthy'
            _odoo_connection_pool['consecutive_failures'] = 0
        else:
            _odoo_connection_pool['consecutive_failures'] += 1
            if _odoo_connection_pool['consecutive_failures'] >= MAX_CONSECUTIVE_FAILURES:
                _odoo_connection_pool['connection_health'] = 'unhealthy'

def execute_odoo_call_with_retry(models, uid, model_name, method, args, kwargs=None, max_retries=2):
    """
    Execute Odoo XML-RPC call with retry logic and timeout handling
    
    Args:
        models: Odoo models proxy
        uid: User ID
        model_name: Odoo model name
        method: Method to call
        args: Arguments for the method
        kwargs: Keyword arguments for the method
        max_retries: Maximum number of retry attempts
    
    Returns:
        Result of the Odoo call
    """
    if kwargs is None:
        kwargs = {}
    
    for attempt in range(max_retries):
        try:
            # Add timeout wrapper for the call
            import threading
            import queue
            
            result_queue = queue.Queue()
            
            def call_worker():
                try:
                    if method == 'search_read':
                        result = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, model_name, method, args, kwargs)
                    elif method == 'read':
                        result = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, model_name, method, args, kwargs)
                    elif method == 'search':
                        result = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, model_name, method, args, kwargs)
                    elif method == 'search_count':
                        result = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, model_name, method, args, kwargs)
                    else:
                        result = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, model_name, method, args, kwargs)
                    result_queue.put(('success', result))
                except Exception as e:
                    result_queue.put(('error', str(e)))
            
            # Start call in a separate thread
            call_thread = threading.Thread(target=call_worker)
            call_thread.daemon = True
            call_thread.start()
            
            # Wait for result with timeout
            try:
                result_type, result_data = result_queue.get(timeout=60)  # Increased timeout to 60 seconds
                if result_type == 'success':
                    update_connection_health(True)  # Mark as successful
                    return result_data
                else:
                    update_connection_health(False)  # Mark as failed
                    raise Exception(f"Odoo call failed: {result_data}")
            except queue.Empty:
                update_connection_health(False)  # Mark as failed
                raise Exception("Odoo call timeout")
                
        except Exception as e:
            print(f"Error in Odoo call (attempt {attempt + 1}/{max_retries}): {e}")
            
            # If it's a connection error, try to reconnect
            if any(error_type in str(e).lower() for error_type in ['timeout', 'connection', 'request-sent', 'idle']):
                print("Connection error detected, attempting to reconnect...")
                # Clear the connection pool to force reconnection
                with _odoo_connection_pool['lock']:
                    _odoo_connection_pool['models'] = None
                    _odoo_connection_pool['uid'] = None
                    _odoo_connection_pool['last_used'] = None
                
                # Wait before retry (reduced backoff for faster recovery)
                if attempt < max_retries - 1:
                    wait_time = 1  # Fixed 1 second wait instead of exponential backoff
                    print(f"Waiting {wait_time} second before retry...")
                    time.sleep(wait_time)
            else:
                # For non-connection errors, don't retry
                raise e
    
    raise Exception(f"All {max_retries} attempts failed for {model_name}.{method}. Last error: {str(e) if 'e' in locals() else 'Unknown error'}")

def get_date_range(view_type='monthly', period=None):
    """
    Returns (start_date, end_date) for a specific view type and period.
    
    Args:
        view_type (str): 'monthly' or 'weekly'
        period (str): For monthly: 'YYYY-MM' format (e.g., '2025-01')
                     For weekly: 'YYYY-WW' format (e.g., '2025-01' for week 1)
    
    Returns:
        tuple: (start_date, end_date) as datetime.date objects
    """
    if view_type == 'monthly':
        if period:
            try:
                # Parse the month-year string
                year, month = map(int, period.split('-'))
                start_of_month = datetime.date(year, month, 1)
                
                # Calculate end of month
                if month == 12:
                    end_of_month = datetime.date(year + 1, 1, 1) - datetime.timedelta(days=1)
                else:
                    end_of_month = datetime.date(year, month + 1, 1) - datetime.timedelta(days=1)
                
                return start_of_month, end_of_month
            except ValueError:
                # If parsing fails, default to January 2025
                return datetime.date(2025, 1, 1), datetime.date(2025, 1, 31)
        else:
            # Default to January 2025
            return datetime.date(2025, 1, 1), datetime.date(2025, 1, 31)
    
    elif view_type == 'weekly':
        if period:
            try:
                # Parse the year-week string
                year, week = map(int, period.split('-'))
                
                # Calculate the start date of the year
                start_of_year = datetime.date(year, 1, 1)
                
                # Find the first Sunday of the year (or previous Sunday if Jan 1 is not Sunday)
                # Sunday = 6, Monday = 0, Tuesday = 1, etc.
                days_until_sunday = (6 - start_of_year.weekday()) % 7
                first_sunday = start_of_year + datetime.timedelta(days=days_until_sunday)
                
                # Calculate the start of the requested week (Sunday)
                week_start = first_sunday + datetime.timedelta(weeks=week-1)
                
                # Calculate the end of the week (Saturday)
                week_end = week_start + datetime.timedelta(days=6)
                
                return week_start, week_end
            except ValueError:
                # If parsing fails, default to first week of 2025
                return datetime.date(2025, 1, 5), datetime.date(2025, 1, 11)
        else:
            # Default to first week of 2025 (Jan 5-11)
            return datetime.date(2025, 1, 5), datetime.date(2025, 1, 11)
    
    elif view_type == 'daily':
        if period:
            try:
                # Parse the year-day string (e.g., '2025-001' for day 1)
                year, day = period.split('-')
                year = int(year)
                day = int(day)
                
                # Validate day number (should be between 1 and 365/366)
                if day < 1 or day > 366:
                    print(f"Warning: Invalid day number {day} received for daily view. Defaulting to day 1.")
                    day = 1
                
                # Calculate the date for the given day number (1-based)
                start_of_year = datetime.date(year, 1, 1)
                target_date = start_of_year + datetime.timedelta(days=day-1)
                
                # Validate that the calculated date is within the year
                if target_date.year != year:
                    print(f"Warning: Calculated date {target_date} is outside year {year}. Defaulting to day 1.")
                    target_date = datetime.date(year, 1, 1)
                
                print(f"Daily view: Processing day {day} of {year} = {target_date}")
                
                # For daily view, start and end date are the same
                return target_date, target_date
            except ValueError as e:
                print(f"Error parsing daily period '{period}': {e}. Defaulting to first day of 2025.")
                # If parsing fails, default to first day of 2025
                return datetime.date(2025, 1, 1), datetime.date(2025, 1, 1)
        else:
            # Default to first day of 2025
            return datetime.date(2025, 1, 1), datetime.date(2025, 1, 1)
    
    else:
        # Default to monthly view
        return get_date_range('monthly', period)

# === Shareholders utilities and weekly email helpers ===
EMAIL_REGEX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
SHAREHOLDERS_FILE = os.environ.get('SHAREHOLDERS_FILE', 'shareholders.json')

def _get_shareholders_file_path():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_dir, SHAREHOLDERS_FILE)

def load_shareholders():
    try:
        path = _get_shareholders_file_path()
        if not os.path.exists(path):
            return []
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            if isinstance(data, list):
                # Normalize and deduplicate
                normalized = sorted({(e or '').strip().lower() for e in data if isinstance(e, str)})
                return normalized
            return []
    except Exception as e:
        print(f"Error loading shareholders: {e}")
        return []

def _save_shareholders(emails):
    path = _get_shareholders_file_path()
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(emails, f, indent=2)
        return True
    except Exception as e:
        print(f"Error saving shareholders: {e}")
        return False

def add_shareholder_email(email):
    try:
        candidate = (email or '').strip().lower()
        if not candidate or not EMAIL_REGEX.match(candidate):
            return False
        emails = load_shareholders()
        if candidate not in emails:
            emails.append(candidate)
            emails = sorted(set(emails))
            if not _save_shareholders(emails):
                return False
        return True
    except Exception as e:
        print(f"Error adding shareholder email '{email}': {e}")
        return False

def remove_shareholder_email(email):
    try:
        candidate = (email or '').strip().lower()
        emails = load_shareholders()
        if candidate in emails:
            emails = [e for e in emails if e != candidate]
            if not _save_shareholders(emails):
                return False
        return True
    except Exception as e:
        print(f"Error removing shareholder email '{email}': {e}")
        return False

def _get_last_week_period():
    # Determine last week based on Sunday-Saturday weeks to match get_date_range
    today = datetime.date.today()
    last_week_day = today - datetime.timedelta(days=7)
    # Find first Sunday of the year for that date's year
    start_of_year = datetime.date(last_week_day.year, 1, 1)
    days_until_sunday = (6 - start_of_year.weekday()) % 7
    first_sunday = start_of_year + datetime.timedelta(days=days_until_sunday)
    # Compute the Sunday of last_week_day's week
    days_since_sunday = (last_week_day.weekday() - 6) % 7
    week_start = last_week_day - datetime.timedelta(days=days_since_sunday)
    # Week number offset from first_sunday
    delta_days = (week_start - first_sunday).days
    week_number = (delta_days // 7) + 1
    if week_number < 1:
        week_number = 1
    return f"{week_start.year}-{week_number:02d}"

def _get_last_month_period():
    """Get the previous month's period in YYYY-MM format"""
    today = datetime.date.today()
    if today.month == 1:
        last_month = datetime.date(today.year - 1, 12, 1)
    else:
        last_month = datetime.date(today.year, today.month - 1, 1)
    return last_month.strftime('%Y-%m')

def get_dashboard_data(period=None, view_type='monthly'):
    """
    Get comprehensive dashboard data for all departments.
    This function replicates the logic from /api/all-departments-data endpoint.
    """
    try:
        # Helper to map department names to keys
        def dept_key_name(name):
            return 'creative' if name == 'Creative' else 'creative_strategy' if name == 'Creative Strategy' else 'instructional_design'

        # Valid departments
        valid_departments = ('Creative', 'Creative Strategy', 'Instructional Design')
        
        # Check cache first
        cached_creative = get_cached_data('creative', period, view_type)
        cached_creative_strategy = get_cached_data('creative_strategy', period, view_type)
        cached_instructional_design = get_cached_data('instructional_design', period, view_type)
        
        # If all are cached and valid, return cached data
        if cached_creative is not None and cached_creative_strategy is not None and cached_instructional_design is not None:
            return {
                'creative': cached_creative,
                'creative_strategy': cached_creative_strategy,
                'instructional_design': cached_instructional_design,
                'team_utilization': cached_creative.get('team_utilization', {}),  # For backward compatibility
                'cached': True,
                'cache_timestamp': time.time()
            }
        
        # Fetch data for departments that aren't cached
        result = {}
        
        # Fetch Creative department data
        if cached_creative is None:
            creative_data = fetch_department_data_sequential('Creative', period, view_type)
            if creative_data:
                set_cached_data('creative', creative_data, period, view_type)
                result['creative'] = creative_data
            else:
                # Fallback to original methods
                fallback_data = {
                    'employees': get_creative_employees(),
                    'team_utilization': get_team_utilization_data(period, view_type),
                    'timesheet_data': get_creative_timesheet_data(period, view_type),
                    'available_resources': get_available_creative_resources(view_type, period)
                }
                set_cached_data('creative', fallback_data, period, view_type)
                result['creative'] = fallback_data
        else:
            result['creative'] = cached_creative
            
        # Fetch Creative Strategy department data
        if cached_creative_strategy is None:
            creative_strategy_data = fetch_department_data_sequential('Creative Strategy', period, view_type)
            if creative_strategy_data:
                set_cached_data('creative_strategy', creative_strategy_data, period, view_type)
                result['creative_strategy'] = creative_strategy_data
            else:
                # Fallback to original methods
                fallback_data = {
                    'employees': get_creative_strategy_employees(),
                    'team_utilization': get_creative_strategy_team_utilization_data(period, view_type),
                    'timesheet_data': get_creative_strategy_timesheet_data(period, view_type),
                    'available_resources': get_available_creative_strategy_resources(view_type, period)
                }
                set_cached_data('creative_strategy', fallback_data, period, view_type)
                result['creative_strategy'] = fallback_data
        else:
            result['creative_strategy'] = cached_creative_strategy
            
        # Fetch Instructional Design department data
        if cached_instructional_design is None:
            instructional_design_data = fetch_department_data_sequential('Instructional Design', period, view_type)
            if instructional_design_data:
                set_cached_data('instructional_design', instructional_design_data, period, view_type)
                result['instructional_design'] = instructional_design_data
            else:
                # Fallback to original methods
                fallback_data = {
                    'employees': get_instructional_design_employees(),
                    'team_utilization': get_instructional_design_team_utilization_data(period, view_type),
                    'timesheet_data': get_instructional_design_timesheet_data(period, view_type),
                    'available_resources': get_available_instructional_design_resources(view_type, period)
                }
                set_cached_data('instructional_design', fallback_data, period, view_type)
                result['instructional_design'] = fallback_data
        else:
            result['instructional_design'] = cached_instructional_design
        
        # Add team_utilization at root level for backward compatibility
        if result.get('creative') and result['creative'].get('team_utilization'):
            result['team_utilization'] = result['creative']['team_utilization']
        
        result['cached'] = False
        result['cache_timestamp'] = time.time()
        
        return result
        
    except Exception as e:
        print(f"Error in get_dashboard_data: {e}")
        # Return minimal fallback structure
        return {
            'creative': {},
            'creative_strategy': {},
            'instructional_design': {},
            'team_utilization': {},
            'cached': False,
            'cache_timestamp': time.time(),
            'error': str(e)
        }

def build_weekly_utilization_email_html(period, team_data, start_date, end_date, external_hours_data=None):
    try:
        title = f"Weekly Utilization Summary - Week {period} ({start_date.strftime('%b %d')}–{end_date.strftime('%b %d, %Y')})"

        # Resolve external hours for pools (KSA/UAE) to compute efficiency metrics
        ksa_external = 0.0
        uae_external = 0.0
        if isinstance(external_hours_data, dict):
            try:
                ksa_external = float(external_hours_data.get('ksa', {}).get('totalHours', 0) or 0)
            except Exception:
                ksa_external = 0.0
            try:
                uae_external = float(external_hours_data.get('uae', {}).get('totalHours', 0) or 0)
            except Exception:
                uae_external = 0.0

        def external_for_team(team_name: str) -> float:
            name = (team_name or '').strip().lower()
            # Exclude Nightshift from external-hour-driven metrics
            if 'nightshift' in name:
                return 0.0
            if 'ksa' in name:
                return ksa_external
            if 'uae' in name:
                return uae_external
            # default combined external hours
            return ksa_external + uae_external

        # Professional email styles
        styles = """
        <style>
          body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; color: #2c3e50; margin: 0; padding: 0; background: #f5f7fb; }
          .container { max-width: 940px; margin: 24px auto; background: #fff; border-radius: 10px; box-shadow: 0 6px 20px rgba(0,0,0,0.08); overflow: hidden; }
          .header { background: linear-gradient(135deg, #3b82f6 0%, #6366f1 100%); color: #fff; padding: 28px 32px; }
          .header h1 { margin: 0; font-weight: 600; font-size: 22px; }
          .header p { margin: 6px 0 0 0; opacity: .95; font-size: 13px; }
          .content { padding: 26px 32px 30px; }
          .section { margin: 0 0 22px 0; }
          .section h2 { margin: 0 0 12px 0; font-size: 16px; font-weight: 700; color: #111827; }
          .table { width: 100%; border-collapse: collapse; border: 1px solid #e5e7eb; border-radius: 8px; overflow: hidden; }
          .table thead { background: #111827; color: #fff; }
          .table th, .table td { padding: 10px 12px; font-size: 13px; border-bottom: 1px solid #e5e7eb; }
          .right { text-align: right; }
          .muted { color: #6b7280; }
          .badge { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 12px; font-weight: 600; }
          .ok { background: #ecfdf5; color: #065f46; }
          .warn { background: #fffbeb; color: #92400e; }
          .risk { background: #fef2f2; color: #991b1b; }
          .footer { padding: 16px 24px; background: #f3f4f6; color: #6b7280; font-size: 12px; text-align: center; }
        </style>
        """

        # Build rows per pool/team with requested metrics
        rows_html = []
        if isinstance(team_data, dict) and team_data:
            for team_name, stats in team_data.items():
                try:
                    creatives = int(stats.get('total_creatives', 0) or 0)
                except Exception:
                    creatives = 0
                planned = float(stats.get('planned_hours', 0) or 0)
                available = float(stats.get('available_hours', 0) or 0)
                logged = float(stats.get('logged_hours', 0) or 0)
                variance = float(stats.get('variance', 0) or (logged - planned))
                # Weekly Utilization Rate should be based on Logged / Available
                util_rate = (logged / available * 100) if available > 0 else 0.0

                ext = external_for_team(team_name)
                efficiency_ratio = (logged / ext * 100) if ext > 0 else 0.0
                billable_util = (ext / available * 100) if available > 0 else 0.0
                scope_health = (ext / planned * 100) if planned > 0 else 0.0

                # Status badges for quick visual cues
                def badge_for(val: float) -> str:
                    if val >= 90:
                        return "<span class='badge ok'>Good</span>"
                    if val >= 70:
                        return "<span class='badge warn'>Watch</span>"
                    return "<span class='badge risk'>Risk</span>"

                # For Nightshift: show N/A for external-hour-driven metrics and remove status
                is_nightshift = ('nightshift' in (team_name or '').strip().lower())
                eff_display = '--' if is_nightshift else f"{efficiency_ratio:.1f}%"
                billable_display = '--' if is_nightshift else f"{billable_util:.1f}%"
                scope_display = '--' if is_nightshift else f"{scope_health:.1f}%"
                status_display = '' if is_nightshift else badge_for(scope_health)

                # Prepare display values (Nightshift keeps utilization visible; only external-hour-driven metrics are hidden)
                util_display = f"{util_rate:.1f}%" if available > 0 else '--'

                rows_html.append(
                    f"<tr>"
                    f"<td>{team_name}</td>"
                    f"<td class='right'>{creatives}</td>"
                    f"<td class='right'>{util_display}</td>"
                    f"<td class='right'>{available:.1f}</td>"
                    f"<td class='right'>{planned:.1f}</td>"
                    f"<td class='right'>{logged:.1f}</td>"
                    f"<td class='right'>{variance:+.1f}</td>"
                    f"<td class='right'>{eff_display}</td>"
                    f"<td class='right'>{billable_display}</td>"
                    f"<td class='right'>{scope_display}</td>"
                    f"<td class='right'>{status_display}</td>"
                    f"</tr>"
                )

        body_rows = "".join(rows_html) if rows_html else "<tr><td colspan='10' class='muted' style='text-align:center;padding:14px'>No data available for this period.</td></tr>"

        html = f"""
        <!DOCTYPE html>
        <html>
          <head>
            <meta charset=\"utf-8\" />
            <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
            <title>{title}</title>
            {styles}
          </head>
          <body>
            <div class=\"container\"> 
              <div class=\"header\">
                <h1>{title}</h1>
                <p>Summary only — pool-level overview for the week</p>
              </div>
              <div class=\"content\">
                <div class=\"section\">
                  <h2>Pool Overview</h2>
                  <table class=\"table\">
                    <thead>
                      <tr>
                        <th>Pool</th>
                        <th class=\"right\">Creatives</th>
                        <th class=\"right\">Utilization Rate</th>
                        <th class=\"right\">Available (h)</th>
                        <th class=\"right\">Planned (h)</th>
                        <th class=\"right\">Logged (h)</th>
                        <th class=\"right\">Variance (h)</th>
                        <th class=\"right\">Efficiency Ratio</th>
                        <th class=\"right\">Billable Utilization</th>
                        <th class=\"right\">Scope Health</th>
                        <th class=\"right\">Status</th>
                      </tr>
                    </thead>
                    <tbody>{body_rows}</tbody>
                  </table>
                </div>
                <div class=\"section muted\" style=\"font-size:12px\">Utilization Rate = Logged / Available, Efficiency Ratio = Logged / External, Billable Utilization = External / Available, Scope Health = External / Planned</div>
              </div>
              <div class=\"footer\">This automated summary was generated {datetime.datetime.now().strftime('%b %d, %Y at %I:%M %p')}.</div>
            </div>
          </body>
        </html>
        """
        return html
    except Exception as e:
        print(f"Error building weekly email HTML: {e}")
        return "<div>Error generating email preview.</div>"

def build_monthly_utilization_email_html(period, dashboard_data, start_date, end_date):
    """Build comprehensive monthly utilization email with all dashboard data"""
    try:
        month_year = start_date.strftime('%B %Y')
        title = f"Monthly Utilization Report - {month_year}"
        
        # Professional email styles
        email_styles = """
        <style>
            body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; line-height: 1.6; color: #333; margin: 0; padding: 20px; background-color: #f5f5f5; }
            .container { max-width: 900px; margin: 0 auto; background: white; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); overflow: hidden; }
            .header { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 30px; text-align: center; }
            .header h1 { margin: 0; font-size: 28px; font-weight: 300; }
            .header p { margin: 10px 0 0 0; opacity: 0.9; }
            .content { padding: 30px; }
            .section { margin-bottom: 35px; }
            .section h2 { color: #2c3e50; border-bottom: 2px solid #3498db; padding-bottom: 8px; margin-bottom: 20px; font-size: 20px; }
            .metrics-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 20px; margin-bottom: 25px; }
            .metric-card { background: #f8f9fa; border-left: 4px solid #3498db; padding: 20px; border-radius: 4px; }
            .metric-value { font-size: 28px; font-weight: bold; color: #2c3e50; margin-bottom: 5px; }
            .metric-label { color: #7f8c8d; font-size: 14px; text-transform: uppercase; letter-spacing: 0.5px; }
            .table { width: 100%; border-collapse: collapse; margin-bottom: 20px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); border-radius: 4px; overflow: hidden; }
            .table thead { background: #34495e; color: white; }
            .table th, .table td { padding: 12px 15px; text-align: left; border-bottom: 1px solid #ecf0f1; }
            .table th { font-weight: 600; text-transform: uppercase; font-size: 12px; letter-spacing: 0.5px; }
            .table tbody tr:hover { background-color: #f8f9fa; }
            .table .number { text-align: right; font-weight: 500; }
            .utilization-high { color: #27ae60; font-weight: bold; }
            .utilization-medium { color: #f39c12; font-weight: bold; }
            .utilization-low { color: #e74c3c; font-weight: bold; }
            .footer { background: #ecf0f1; padding: 20px; text-align: center; color: #7f8c8d; font-size: 12px; }
            .department-summary { background: #fff; border: 1px solid #e1e8ed; border-radius: 8px; padding: 20px; margin-bottom: 20px; }
        </style>
        """
        
        # Extract team utilization data
        team_data = dashboard_data.get('team_utilization', {})
        
        # Calculate overall metrics - handle nested dictionary structure
        total_planned = 0
        total_logged = 0
        
        if team_data:
            for team_name, team_info in team_data.items():
                if isinstance(team_info, dict):
                    # Handle nested structure like {'KSA': {...}, 'UAE': {...}, ...}
                    if 'planned_hours' in team_info and 'logged_hours' in team_info:
                        # Direct team stats
                        total_planned += float(team_info.get('planned_hours', 0))
                        total_logged += float(team_info.get('logged_hours', 0))
                    else:
                        # Nested team structure - sum up sub-teams
                        for subteam_name, subteam_stats in team_info.items():
                            if isinstance(subteam_stats, dict):
                                total_planned += float(subteam_stats.get('planned_hours', 0))
                                total_logged += float(subteam_stats.get('logged_hours', 0))
        
        overall_utilization = (total_logged / total_planned * 100) if total_planned > 0 else 0
        total_variance = total_logged - total_planned
        
        # Build team utilization rows - handle nested structure
        team_rows = []
        if team_data:
            for team_name, team_info in team_data.items():
                if isinstance(team_info, dict):
                    # Check if this is direct team stats or nested structure
                    if 'planned_hours' in team_info and 'logged_hours' in team_info:
                        # Direct team stats
                        util = float(team_info.get('utilization_rate', 0.0))
                        planned = float(team_info.get('planned_hours', 0.0))
                        logged = float(team_info.get('logged_hours', 0.0))
                        variance = float(team_info.get('variance', 0.0))
                        
                        # Color code utilization
                        util_class = 'utilization-high' if util >= 80 else 'utilization-medium' if util >= 60 else 'utilization-low'
                        
                        team_rows.append(f"""
                        <tr>
                            <td>{team_name}</td>
                            <td class="number {util_class}">{util:.1f}%</td>
                            <td class="number">{planned:.1f}</td>
                            <td class="number">{logged:.1f}</td>
                            <td class="number">{variance:+.1f}</td>
                        </tr>
                        """)
                    else:
                        # Nested team structure - create rows for each sub-team
                        for subteam_name, subteam_stats in team_info.items():
                            if isinstance(subteam_stats, dict) and 'planned_hours' in subteam_stats:
                                util = float(subteam_stats.get('utilization_rate', 0.0))
                                planned = float(subteam_stats.get('planned_hours', 0.0))
                                logged = float(subteam_stats.get('logged_hours', 0.0))
                                variance = float(subteam_stats.get('variance', 0.0))
                                
                                # Color code utilization
                                util_class = 'utilization-high' if util >= 80 else 'utilization-medium' if util >= 60 else 'utilization-low'
                                
                                team_rows.append(f"""
                                <tr>
                                    <td>{team_name} - {subteam_name}</td>
                                    <td class="number {util_class}">{util:.1f}%</td>
                                    <td class="number">{planned:.1f}</td>
                                    <td class="number">{logged:.1f}</td>
                                    <td class="number">{variance:+.1f}</td>
                                </tr>
                                """)
        
        team_rows_html = "".join(team_rows) if team_rows else "<tr><td colspan='5' style='text-align:center;color:#7f8c8d;padding:20px;'>No team utilization data available for this period.</td></tr>"
        
        # Build department summaries
        department_sections = ""
        for dept_key in ['creative', 'creative_strategy', 'instructional_design']:
            dept_data = dashboard_data.get(dept_key, {})
            if not dept_data:
                continue
                
            dept_name = dept_key.replace('_', ' ').title()
            employees = dept_data.get('employees', [])
            resources = dept_data.get('available_resources', [])
            timesheet_data = dept_data.get('timesheet_data', [])
            
            # Calculate department metrics
            dept_total_hours = 0
            for emp in timesheet_data:
                hours_val = emp.get('logged_hours', 0)
                if isinstance(hours_val, (int, float, str)):
                    try:
                        dept_total_hours += float(hours_val)
                    except (ValueError, TypeError):
                        continue
            
            dept_available_hours = 0
            for res in resources:
                avail_val = res.get('available_hours', 0)
                if isinstance(avail_val, (int, float, str)):
                    try:
                        dept_available_hours += float(avail_val)
                    except (ValueError, TypeError):
                        continue
            
            dept_utilization = (dept_total_hours / dept_available_hours * 100) if dept_available_hours > 0 else 0
            
            # Build employee rows
            employee_rows = []
            for emp in timesheet_data[:10]:  # Show top 10 employees
                name = emp.get('name', 'Unknown')
                hours_val = emp.get('logged_hours', 0)
                
                # Safely convert to float
                try:
                    if isinstance(hours_val, (int, float, str)):
                        hours = float(hours_val)
                        employee_rows.append(f"<tr><td>{name}</td><td class='number'>{hours:.1f}h</td></tr>")
                except (ValueError, TypeError):
                    continue
            
            employee_table = ""
            if employee_rows:
                employee_table = f"""
                <table class="table">
                    <thead>
                        <tr><th>Employee</th><th>Logged Hours</th></tr>
                    </thead>
                    <tbody>{''.join(employee_rows)}</tbody>
                </table>
                """
            
            department_sections += f"""
            <div class="department-summary">
                <h3 style="color: #2c3e50; margin-top: 0;">{dept_name} Department</h3>
                <div class="metrics-grid">
                    <div class="metric-card">
                        <div class="metric-value">{len(employees)}</div>
                        <div class="metric-label">Total Employees</div>
                    </div>
                    <div class="metric-card">
                        <div class="metric-value">{dept_total_hours:.0f}h</div>
                        <div class="metric-label">Total Logged Hours</div>
                    </div>
                    <div class="metric-card">
                        <div class="metric-value">{dept_utilization:.1f}%</div>
                        <div class="metric-label">Department Utilization</div>
                    </div>
                </div>
                {employee_table}
            </div>
            """
        
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>{title}</title>
            {email_styles}
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>{title}</h1>
                    <p>{start_date.strftime('%B %d')} - {end_date.strftime('%B %d, %Y')}</p>
                </div>
                
                <div class="content">
                    <!-- Executive Summary -->
                    <div class="section">
                        <h2>📊 Executive Summary</h2>
                        <div class="metrics-grid">
                            <div class="metric-card">
                                <div class="metric-value">{overall_utilization:.1f}%</div>
                                <div class="metric-label">Overall Utilization</div>
                            </div>
                            <div class="metric-card">
                                <div class="metric-value">{total_planned:.0f}h</div>
                                <div class="metric-label">Total Planned Hours</div>
                            </div>
                            <div class="metric-card">
                                <div class="metric-value">{total_logged:.0f}h</div>
                                <div class="metric-label">Total Logged Hours</div>
                            </div>
                            <div class="metric-card">
                                <div class="metric-value">{total_variance:+.0f}h</div>
                                <div class="metric-label">Variance</div>
                            </div>
                        </div>
                    </div>
                    
                    <!-- Team Utilization -->
                    <div class="section">
                        <h2>👥 Team Utilization Overview</h2>
                        <table class="table">
                            <thead>
                                <tr>
                                    <th>Team</th>
                                    <th>Utilization Rate</th>
                                    <th>Planned Hours</th>
                                    <th>Logged Hours</th>
                                    <th>Variance</th>
                                </tr>
                            </thead>
                            <tbody>
                                {team_rows_html}
                            </tbody>
                        </table>
                    </div>
                    
                    <!-- Department Details -->
                    <div class="section">
                        <h2>🏢 Department Breakdown</h2>
                        {department_sections}
                    </div>
                </div>
                
                <div class="footer">
                    <p>This is an automated monthly report from the Utilization Dashboard</p>
                    <p>Generated on {datetime.datetime.now().strftime('%B %d, %Y at %I:%M %p')}</p>
                </div>
            </div>
        </body>
        </html>
        """
        
        return html
    except Exception as e:
        print(f"Error building monthly email HTML: {e}")
        return "<div>Error generating monthly email preview.</div>"

def send_html_email_via_smtp(to_email, subject, html):
    try:
        host = os.environ.get('SMTP_HOST')
        port = int(os.environ.get('SMTP_PORT', '587'))
        user = os.environ.get('SMTP_USER')
        password = os.environ.get('SMTP_PASS')
        from_email = os.environ.get('FROM_EMAIL', user)
        use_tls = (os.environ.get('SMTP_USE_TLS', 'true').lower() == 'true')
        if not host or not from_email:
            print("SMTP not configured (missing SMTP_HOST or FROM_EMAIL)")
            return False
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From'] = from_email
        msg['To'] = to_email
        msg.attach(MIMEText(html, 'html', 'utf-8'))
        server = smtplib.SMTP(host, port, timeout=20)
        try:
            if use_tls:
                server.starttls()
            if user and password:
                server.login(user, password)
            server.sendmail(from_email, [to_email], msg.as_string())
        finally:
            server.quit()
        return True
    except Exception as e:
        print(f"Error sending email to {to_email}: {e}")
        return False
def calculate_working_days_and_hours(start_date, end_date):
    """
    Calculate the number of working days and base available hours for a given period.
    Working days are Sunday through Thursday (excluding Friday and Saturday).
    
    Args:
        start_date (datetime.date): Start date of the period
        end_date (datetime.date): End date of the period
    
    Returns:
        tuple: (working_days, base_available_hours)
    """
    working_days = 0
    current_date = start_date
    
    while current_date <= end_date:
        # In Python: Monday=0, Tuesday=1, Wednesday=2, Thursday=3, Friday=4, Saturday=5, Sunday=6
        # Working days: Sunday(6), Monday(0), Tuesday(1), Wednesday(2), Thursday(3)
        if current_date.weekday() in [6, 0, 1, 2, 3]:  # Sunday to Thursday
            working_days += 1
        current_date += datetime.timedelta(days=1)
    
    # 8 hours per working day
    base_available_hours = working_days * 8
    
    print(f"Period {start_date} to {end_date}: {working_days} working days = {base_available_hours} base hours")
    return working_days, base_available_hours

def get_employee_working_weekdays(models, uid, employee_id):
    """
    Return a set of Python weekday integers (Mon=0..Sun=6) that the employee
    is scheduled to work based on hr.employee.resource_calendar_id.
    Falls back to Sunday-Thursday if calendar or attendances are missing.
    """
    try:
        emp = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'hr.employee', 'read',
                                [[employee_id]], {'fields': ['resource_calendar_id']})
        if not emp:
            return {6, 0, 1, 2, 3}  # Sun-Thu fallback
        emp = emp[0]
        cal_field = emp.get('resource_calendar_id')
        if not cal_field:
            return {6, 0, 1, 2, 3}
        cal_id = cal_field[0] if isinstance(cal_field, (list, tuple)) else cal_field
        # Use cache for calendar weekdays to avoid repeated reads
        if cal_id in calendar_weekdays_cache:
            return calendar_weekdays_cache[cal_id]
        calendars = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'resource.calendar', 'read',
                                      [[cal_id]], {'fields': ['attendance_ids', 'name']})
        if not calendars:
            return {6, 0, 1, 2, 3}
        calendar = calendars[0]
        attendance_ids = calendar.get('attendance_ids') or []
        if not attendance_ids:
            return {6, 0, 1, 2, 3}
        attendances = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'resource.calendar.attendance', 'read',
                                        [attendance_ids], {'fields': ['dayofweek']})
        weekdays = set()
        for att in attendances:
            # Odoo stores dayofweek as string '0'..'6' with Mon=0
            d = att.get('dayofweek')
            try:
                d_int = int(d)
            except Exception:
                continue
            if 0 <= d_int <= 6:
                weekdays.add(d_int)
        if not weekdays:
            weekdays = {6, 0, 1, 2, 3}
        # Cache and return
        calendar_weekdays_cache[cal_id] = weekdays
        return weekdays
    except Exception as e:
        print(f"Error fetching working weekdays for employee {employee_id}: {e}")
        return {6, 0, 1, 2, 3}

def calculate_employee_working_days_and_hours(models, uid, employee_id, start_date, end_date):
    """
    Calculate working days and base hours for one employee using their resource calendar.
    """
    weekdays = get_employee_working_weekdays(models, uid, employee_id)
    working_days = 0
    current_date = start_date
    while current_date <= end_date:
        if current_date.weekday() in weekdays:
            working_days += 1
        current_date += datetime.timedelta(days=1)
    base_hours = working_days * 8
    return working_days, base_hours

def get_public_holidays(models, uid, start_date, end_date, company_id=None):
    """
    Fetch public holidays from Odoo resource.calendar.leaves within the date range.
    Only fetches company-wide public holidays, not individual employee time-offs.
    
    Args:
        models: Odoo models proxy
        uid: User ID
        start_date (datetime.date): Start date of the period
        end_date (datetime.date): End date of the period
        company_id (int, optional): Company ID to filter holidays
    
    Returns:
        list: List of public holiday dictionaries with name, date_from, date_to
        Note: Individual employee time-offs are excluded (employee_id=False filter)
    """
    # Check cache first
    cache_key = get_cache_key("holidays", company_id, start_date, end_date)
    cached_holidays = get_from_cache(holiday_cache, cache_key)
    if cached_holidays is not None:
        print(f"Using cached holiday data for {start_date} to {end_date} (company: {company_id})")
        return cached_holidays
    
    try:
        print(f"Fetching public holidays from {start_date} to {end_date}")
        
        # Optional: allow disabling holidays via env for debugging
        # Default to enabled (set DISABLE_HOLIDAYS_FOR_DEBUG=true to turn off)
        disable_holidays = os.environ.get('DISABLE_HOLIDAYS_FOR_DEBUG', 'false').lower() in ('1', 'true', 'yes')
        if disable_holidays:
            # Log this warning only once per process
            if not getattr(get_public_holidays, '_warned_once', False):
                print("WARNING: Holiday calculation temporarily disabled via DISABLE_HOLIDAYS_FOR_DEBUG")
                get_public_holidays._warned_once = True
            return []
        
        # Optional debug mode for detailed holiday analysis
        debug_holidays = os.environ.get('DEBUG_HOLIDAYS', 'false').lower() in ('1', 'true', 'yes')
        if debug_holidays:
            # Debug: First check what holiday records exist in the system
            all_holidays_count = models.execute_kw(
                ODOO_DB, uid, ODOO_PASSWORD,
                'resource.calendar.leaves', 'search_count', [[]]
            )
            print(f"Total holiday records in system: {all_holidays_count}")

            # Check for different types of leaves to understand the data structure
            wide_start = f"{start_date.year - 1}-01-01 00:00:00"
            wide_end = f"{start_date.year + 1}-12-31 23:59:59"
            
            # Count public holidays (resource_id = False)
            public_holidays_count = models.execute_kw(
                ODOO_DB, uid, ODOO_PASSWORD,
                'resource.calendar.leaves', 'search_count',
                [[('date_from', '<=', wide_end), ('date_to', '>=', wide_start), ('resource_id', '=', False)]]
            )
            print(f"Public holidays (resource_id=False) in {start_date.year - 1}-{start_date.year + 1}: {public_holidays_count}")
            
            # Count individual time-offs (resource_id != False)
            individual_leaves_count = models.execute_kw(
                ODOO_DB, uid, ODOO_PASSWORD,
                'resource.calendar.leaves', 'search_count',
                [[('date_from', '<=', wide_end), ('date_to', '>=', wide_start), ('resource_id', '!=', False)]]
            )
            print(f"Individual time-offs (resource_id!=False) in {start_date.year - 1}-{start_date.year + 1}: {individual_leaves_count}")
            
            # Sample some public holidays
            sample_public_holidays = models.execute_kw(
                ODOO_DB, uid, ODOO_PASSWORD,
                'resource.calendar.leaves', 'search_read',
                [[('date_from', '<=', wide_end), ('date_to', '>=', wide_start), ('resource_id', '=', False)]],
                {'fields': ['name', 'date_from', 'date_to', 'company_id'], 'limit': 10}
            )
            print(f"Sample public holidays (resource_id=False):")
            for h in sample_public_holidays:
                print(f"  - {h.get('name')}: {h.get('date_from')} to {h.get('date_to')} (company: {h.get('company_id')})")
                
            # Sample some individual time-offs for comparison
            sample_individual_leaves = models.execute_kw(
                ODOO_DB, uid, ODOO_PASSWORD,
                'resource.calendar.leaves', 'search_read',
                [[('date_from', '<=', wide_end), ('date_to', '>=', wide_start), ('resource_id', '!=', False)]],
                {'fields': ['name', 'date_from', 'date_to', 'company_id', 'resource_id'], 'limit': 3}
            )
            print(f"Sample individual time-offs (resource_id!=False) for comparison:")
            for h in sample_individual_leaves:
                print(f"  - {h.get('name')}: {h.get('date_from')} to {h.get('date_to')} (company: {h.get('company_id')}, resource: {h.get('resource_id')})")

        # Convert dates to datetime strings for Odoo query
        start_datetime = f"{start_date} 00:00:00"
        end_datetime = f"{end_date} 23:59:59"
        
        # Search for PUBLIC holidays that overlap with our date range
        # Key distinction: Public holidays have resource_id = False (not tied to specific employees)
        # Individual employee time-offs have resource_id pointing to specific employees
        domain = [
            ('date_from', '<=', end_datetime),
            ('date_to', '>=', start_datetime),
            ('resource_id', '=', False)  # Critical: Only company-wide holidays, not individual employee time-offs
        ]
        if company_id:
            # Limit to company-specific holidays if specified
            domain.append(('company_id', '=', company_id))

        if debug_holidays:
            print(f"Public holiday search domain (resource_id=False for company-wide holidays): {domain}")

        holiday_ids = models.execute_kw(
            ODOO_DB, uid, ODOO_PASSWORD,
            'resource.calendar.leaves', 'search',
            [domain]
        )
        
        if not holiday_ids:
            print("No public holidays found in the specified date range")
            return []
        
        # Fetch holiday details
        holidays = models.execute_kw(
            ODOO_DB, uid, ODOO_PASSWORD,
            'resource.calendar.leaves', 'read',
            [holiday_ids],
            {'fields': ['name', 'date_from', 'date_to', 'company_id']}
        )
        
        print(f"Found {len(holidays)} public holidays for {start_date} to {end_date}")
        if debug_holidays:
            for holiday in holidays:
                print(f"  - {holiday['name']}: {holiday['date_from']} to {holiday['date_to']}")
        
        # Cache the result before returning
        set_cache(holiday_cache, cache_key, holidays)
        
        return holidays
        
    except Exception as e:
        print(f"Error fetching public holidays: {e}")
        return []

def calculate_holiday_hours_in_period(holidays, start_date, end_date, view_type='monthly', working_weekdays: set = None):
    """
    Calculate total holiday hours that fall within the specified period.
    
    Args:
        holidays (list): List of holiday dictionaries from Odoo
        start_date (datetime.date): Start date of the period
        end_date (datetime.date): End date of the period
        view_type (str): 'monthly', 'weekly', or 'daily'
    
    Returns:
        float: Total holiday hours in the period (per employee)
    """
    total_holiday_hours = 0
    
    # Standard working hours per day (8 hours)
    WORKING_HOURS_PER_DAY = 8
    
    print(f"Calculating holiday hours for {view_type} view from {start_date} to {end_date}")
    
    # Safety check - if no holidays, return 0
    if not holidays:
        print("No holidays found for this period")
        return 0
    
    for holiday in holidays:
        try:
            # Parse holiday dates (they come as datetime strings from Odoo)
            if isinstance(holiday['date_from'], str):
                # Handle different datetime formats
                date_str = holiday['date_from'].replace('Z', '+00:00').replace(' ', 'T')
                if 'T' not in date_str:
                    date_str += 'T00:00:00'
                holiday_start = datetime.datetime.fromisoformat(date_str).date()
            else:
                holiday_start = holiday['date_from'].date() if hasattr(holiday['date_from'], 'date') else holiday['date_from']
                
            if isinstance(holiday['date_to'], str):
                # Handle different datetime formats
                date_str = holiday['date_to'].replace('Z', '+00:00').replace(' ', 'T')
                if 'T' not in date_str:
                    date_str += 'T23:59:59'
                holiday_end = datetime.datetime.fromisoformat(date_str).date()
            else:
                holiday_end = holiday['date_to'].date() if hasattr(holiday['date_to'], 'date') else holiday['date_to']
            
            # Work with datetimes for precise partial-day handling
            holiday_start_dt = None
            holiday_end_dt = None
            # Parse full datetime strings from original fields for precision
            # Safe parsing: support 'Z' and space
            def _parse_dt(value, is_end=False):
                if isinstance(value, str):
                    s = value.replace('Z', '+00:00').replace(' ', 'T')
                    if 'T' not in s:
                        s += 'T23:59:59' if is_end else 'T00:00:00'
                    return datetime.datetime.fromisoformat(s)
                return value if isinstance(value, datetime.datetime) else (
                    datetime.datetime.combine(value, datetime.time(23, 59, 59)) if is_end else datetime.datetime.combine(value, datetime.time(0, 0, 0))
                )

            holiday_start_dt = _parse_dt(holiday['date_from'], is_end=False)
            holiday_end_dt = _parse_dt(holiday['date_to'], is_end=True)

            # Period boundaries as datetimes
            period_start_dt = datetime.datetime.combine(start_date, datetime.time(0, 0, 0))
            period_end_dt = datetime.datetime.combine(end_date, datetime.time(23, 59, 59))

            # Overlap between holiday and period
            overlap_start_dt = max(holiday_start_dt, period_start_dt)
            overlap_end_dt = min(holiday_end_dt, period_end_dt)
            
            # If there's an overlap, calculate effective holiday days considering working days and partial coverage
            if overlap_start_dt <= overlap_end_dt:
                # For daily view, we only count if the overlap includes the specific day
                if view_type == 'daily':
                    if start_date == end_date and overlap_start_dt.date() <= start_date <= overlap_end_dt.date():
                        # Check if this specific day is a working day
                        allowed_weekdays = working_weekdays if working_weekdays is not None else {6, 0, 1, 2, 3}
                        if start_date.weekday() in allowed_weekdays:
                            holiday_hours = WORKING_HOURS_PER_DAY
                            total_holiday_hours += holiday_hours
                            print(f"  Holiday '{holiday['name']}' covers the working day: {holiday_hours}h")
                        else:
                            print(f"  Holiday '{holiday['name']}' falls on non-working day, no hours deducted")
                else:
                    # For monthly and weekly views, evaluate per-day overlap and only count full-day equivalents (>=8h)
                    holiday_hours = 0
                    allowed_weekdays = working_weekdays if working_weekdays is not None else {6, 0, 1, 2, 3}

                    day_cursor = overlap_start_dt.date()
                    last_day = overlap_end_dt.date()
                    max_iterations = 62  # safety for two months span
                    iter_count = 0
                    while day_cursor <= last_day and iter_count < max_iterations:
                        if day_cursor.weekday() in allowed_weekdays:
                            day_start_dt = datetime.datetime.combine(day_cursor, datetime.time(0, 0, 0))
                            day_end_dt = datetime.datetime.combine(day_cursor, datetime.time(23, 59, 59))
                            seg_start = max(overlap_start_dt, day_start_dt)
                            seg_end = min(overlap_end_dt, day_end_dt)
                            if seg_end > seg_start:
                                seg_hours = (seg_end - seg_start).total_seconds() / 3600.0
                                if seg_hours >= WORKING_HOURS_PER_DAY - 0.1:  # count only if ~full-day coverage
                                    holiday_hours += WORKING_HOURS_PER_DAY
                        day_cursor += datetime.timedelta(days=1)
                        iter_count += 1
                    
                    total_holiday_hours += holiday_hours
                    print(f"  Holiday '{holiday['name']}' ({overlap_start_dt} to {overlap_end_dt}): {holiday_hours}h (full-day equivalents)")
                    
                    # Safety check - warn if holiday hours seem too high
                    if holiday_hours > 200:  # More than 25 working days
                        print(f"  WARNING: Holiday hours seem very high ({holiday_hours}h) - please check holiday dates")
            
        except Exception as e:
            print(f"Error processing holiday '{holiday.get('name', 'Unknown')}': {e}")
            import traceback
            traceback.print_exc()
            continue
    
    # Final safety check - cap holiday hours to reasonable limits
    max_reasonable_hours = {
        'daily': 8,      # Max 1 day
        'weekly': 40,    # Max 1 week
        'monthly': 200   # Max ~25 working days
    }
    
    max_hours = max_reasonable_hours.get(view_type, 200)
    if total_holiday_hours > max_hours:
        print(f"WARNING: Holiday hours ({total_holiday_hours}h) exceed reasonable limit for {view_type} view ({max_hours}h). Capping to {max_hours}h")
        total_holiday_hours = max_hours
    
    print(f"Total holiday hours in period: {total_holiday_hours}h")
    return total_holiday_hours

def get_designer_ids_from_planning(models, uid, start_date, end_date):
    """Queries planning.slot for the given date range and returns IDs of designers."""
    slots = models.execute_kw(
        ODOO_DB, uid, ODOO_PASSWORD,
        'planning.slot', 'search_read',
        [[('start_datetime', '>=', start_date), ('end_datetime', '<=', end_date)]],
        {'fields': ['resource_id']}
    )
    resource_ids = {
        slot['resource_id'][0] if isinstance(slot.get('resource_id'), list) else slot.get('resource_id')
        for slot in slots if slot.get('resource_id')
    }
    if not resource_ids:
        return []
    employees = models.execute_kw(
        ODOO_DB, uid, ODOO_PASSWORD,
        'hr.employee', 'search_read',
        [[('id', 'in', list(resource_ids))]],
        {'fields': ['id', 'name', 'job_title']}
    )
    designer_ids = [emp['id'] for emp in employees if 'designer' in (emp.get('job_title') or '').lower()]
    return designer_ids

def read_employee_info(models, uid, employee_ids):
    """Retrieves full employee records for the given IDs."""
    if not employee_ids:
        return []
    return models.execute_kw(
        ODOO_DB, uid, ODOO_PASSWORD,
        'hr.employee', 'search_read',
        [[('id', 'in', employee_ids)]],
        {'fields': ['id', 'name', 'job_title', 'user_id']}
    )

def get_all_timesheet_hours(models, uid, designer_ids, start_date, end_date):
    """Retrieves timesheet hours for the given designer IDs."""
    if not designer_ids:
        return {}
    
    # Convert dates to string format for Odoo
    start_str = start_date.strftime('%Y-%m-%d')
    end_str = end_date.strftime('%Y-%m-%d')
    
    print(f"Searching timesheets from {start_str} to {end_str}")
    
    timesheets = models.execute_kw(
        ODOO_DB, uid, ODOO_PASSWORD,
        'account.analytic.line', 'search_read',
        [[('employee_id', 'in', designer_ids),
          ('date', '>=', start_str),
          ('date', '<=', end_str)]],
        {'fields': ['employee_id', 'unit_amount']}
    )
    
    print(f"Found {len(timesheets)} timesheet entries")
    
    timesheet_dict = defaultdict(float)
    for ts in timesheets:
        emp_field = ts.get('employee_id')
        if emp_field:
            emp_id = emp_field[0] if isinstance(emp_field, list) else emp_field
            timesheet_dict[emp_id] += float(ts.get('unit_amount', 0))
    
    return dict(timesheet_dict)

def get_all_scheduled_data(models, uid, designer_ids, start_date, end_date):
    """Retrieves scheduling data (hours and projects) from planning.slot."""
    if not designer_ids:
        return {}
    
    # Convert dates to string format for Odoo
    start_str = start_date.strftime('%Y-%m-%d 00:00:00')
    end_str = end_date.strftime('%Y-%m-%d 23:59:59')
    
    print(f"Searching planning slots from {start_str} to {end_str}")
    
    slots = models.execute_kw(
        ODOO_DB, uid, ODOO_PASSWORD,
        'planning.slot', 'search_read',
        [[('resource_id', 'in', designer_ids),
          ('start_datetime', '>=', start_str),
          ('end_datetime', '<=', end_str)]],
        {'fields': ['resource_id', 'start_datetime', 'end_datetime', 'project_id', 'allocated_hours']}
    )
    
    print(f"Found {len(slots)} planning slots")
    
    scheduled_data = {}
    for slot in slots:
        res_field = slot.get('resource_id')
        if not res_field:
            continue
        emp_id = res_field[0] if isinstance(res_field, list) else res_field
        if emp_id not in scheduled_data:
            scheduled_data[emp_id] = {'hours': 0.0, 'projects': set()}
        
        # Use allocated_hours if available, otherwise calculate from datetime
        allocated_hours = slot.get('allocated_hours', 0)
        if allocated_hours:
            scheduled_data[emp_id]['hours'] += allocated_hours
        else:
            # Fallback: calculate from datetime difference
            try:
                from datetime import datetime
                start = datetime.strptime(slot['start_datetime'], '%Y-%m-%d %H:%M:%S')
                end = datetime.strptime(slot['end_datetime'], '%Y-%m-%d %H:%M:%S')
                hours = (end - start).total_seconds() / 3600.0
                scheduled_data[emp_id]['hours'] += hours
            except Exception as e:
                print(f"Error calculating hours from datetime: {e}")
        
        project_field = slot.get('project_id')
        if project_field:
            project_name = project_field[1] if isinstance(project_field, list) else str(project_field)
            scheduled_data[emp_id]['projects'].add(project_name)
    
    return scheduled_data

def get_subtask_service_categories(models, uid, designer_ids, start_date, end_date):
    """For planning slots with subtask references, retrieves the service category."""
    if not designer_ids:
        return {}
    main_slots = models.execute_kw(
        ODOO_DB, uid, ODOO_PASSWORD,
        'planning.slot', 'search_read',
        [[('resource_id', 'in', designer_ids),
          ('start_datetime', '>=', start_date),
          ('end_datetime', '<=', end_date),
          ('x_studio_sub_task_1', '!=', False)]],
        {'fields': ['resource_id', 'x_studio_sub_task_1']}
    )
    emp_task_pairs = []
    for slot in main_slots:
        res_field = slot.get('resource_id')
        if not res_field:
            continue
        emp_id = res_field[0] if isinstance(res_field, list) else res_field
        subtask_field = slot.get('x_studio_sub_task_1')
        if not subtask_field:
            continue
        task_id = subtask_field[0] if isinstance(subtask_field, list) else subtask_field
        emp_task_pairs.append((emp_id, task_id))
    unique_task_ids = list({task_id for (_, task_id) in emp_task_pairs})
    if not unique_task_ids:
        return {}
    tasks_data = models.execute_kw(
        ODOO_DB, uid, ODOO_PASSWORD,
        'project.task', 'read',
        [unique_task_ids],
        {'fields': ['x_studio_service_category_1']}
    )
    task_cat_map = {}
    for task in tasks_data:
        cat_field = task.get('x_studio_service_category_1')
        if cat_field:
            cat_name = cat_field[1] if isinstance(cat_field, list) else str(cat_field)
            task_cat_map[task['id']] = cat_name
    categories_dict = {}
    for emp_id, task_id in emp_task_pairs:
        cat_name = task_cat_map.get(task_id)
        if cat_name:
            if emp_id not in categories_dict:
                categories_dict[emp_id] = set()
            categories_dict[emp_id].add(cat_name)
    return categories_dict

def get_creative_employees():
    """Fetch employees from creative department"""
    try:
        models, uid = connect_to_odoo()
        
        if not models or not uid:
            return []
        
        # First, let's see what departments exist with "creative" in the name
        all_departments = execute_odoo_call_with_retry(models, uid, 'hr.department', 'search_read', 
                                          [[('name', 'ilike', 'creative')]], {'fields': ['name', 'id']})
        
        print(f"Found departments with 'creative' in name: {[dept['name'] for dept in all_departments]}")
        
        # Find the exact "Creative" department
        department_ids = execute_odoo_call_with_retry(models, uid, 'hr.department', 'search', 
                                         [[('name', '=', 'Creative')]])
        
        if not department_ids:
            print("No exact 'Creative' department found")
            return []
        
        print(f"Found Creative department with ID: {department_ids}")
        
        # Get employees in the creative department
        employee_ids = execute_odoo_call_with_retry(models, uid, 'hr.employee', 'search', 
                                       [[('department_id', 'in', department_ids)]])
        
        if not employee_ids:
            print("No employees found in creative department")
            return []
        
        print(f"Found {len(employee_ids)} employees in Creative department")
        
        # First, let's get the fields available for hr.employee model
        try:
            fields_info = execute_odoo_call_with_retry(models, uid, 'hr.employee', 'fields_get', [])
            print("Available fields in hr.employee:")
            for field_name, field_info in fields_info.items():
                if field_name.startswith('x_') or 'tag' in field_name.lower() or 'location' in field_name.lower():
                    print(f"  - {field_name}: {field_info.get('string', 'No description')}")
        except Exception as e:
            print(f"Could not get fields info: {e}")
        
        # Get employee data with categories/tags
        employees = execute_odoo_call_with_retry(models, uid, 'hr.employee', 'read', 
                                    [employee_ids], {
                                        'fields': [
                                            'name', 
                                            'job_title', 
                                            'work_email',
                                            'category_ids'  # This is the many2many field for categories/tags
                                        ]
                                    })
        
        # Batch fetch all unique category IDs
        all_category_ids = set()
        for emp in employees:
            if emp.get('category_ids'):
                all_category_ids.update(emp['category_ids'])
        
        # Fetch all categories in one batch call
        categories_dict = {}
        if all_category_ids:
            try:
                categories = execute_odoo_call_with_retry(models, uid, 'hr.employee.category', 'read', 
                                             [list(all_category_ids)], {'fields': ['name']})
                
                # Create a dictionary for fast lookup
                categories_dict = {cat['id']: cat['name'] for cat in categories if cat.get('name')}
                print(f"Fetched {len(categories_dict)} unique categories in batch")
                
            except Exception as e:
                print(f"Error fetching categories in batch: {e}")
                # Fallback to individual calls if batch fails
                print("Falling back to individual category fetching...")
                for emp in employees:
                    if emp.get('category_ids'):
                        try:
                            categories = execute_odoo_call_with_retry(models, uid, 'hr.employee.category', 'read', 
                                                         [emp['category_ids']], {'fields': ['name']})
                            categories_dict.update({cat['id']: cat['name'] for cat in categories if cat.get('name')})
                        except Exception as e2:
                            print(f"Error fetching categories for employee {emp.get('name')}: {e2}")
        
        # Process employees using the cached categories
        processed_employees = []
        for emp in employees:
            employee_data = {
                'name': emp.get('name', ''),
                'job_title': emp.get('job_title', ''),
                'email': emp.get('work_email', ''),
                'tags': []
            }
            
            # Get tags from cached categories
            if emp.get('category_ids'):
                tags = [categories_dict.get(cat_id) for cat_id in emp['category_ids'] if categories_dict.get(cat_id)]
                employee_data['tags'] = tags
                
                if tags:
                    print(f"Employee {emp.get('name')} has tags: {tags}")
            
            processed_employees.append(employee_data)
        
        return processed_employees
        
    except Exception as e:
        print(f"Error fetching employees: {e}")
        return []

def get_team_utilization_data(period=None, view_type='monthly'):
    """
    Fetch team utilization data for KSA, UAE, and Nightshift teams.
    
    Args:
        period (str): Period in format based on view_type (e.g., '2025-01' for monthly, '2025-W01' for weekly, '2025-001' for daily)
        view_type (str): View type - 'monthly', 'weekly', or 'daily'
    
    Returns:
        dict: Team utilization data with stats for each team
    """
    try:
        models, uid = connect_to_odoo()
        
        if not models or not uid:
            return {}
        
        # Get date range for the selected period and view type
        start_date, end_date = get_date_range(view_type, period)
        print(f"Fetching team utilization data for {view_type} view: {start_date} to {end_date}")
        
        # Find the Creative department first
        department_ids = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'hr.department', 'search', 
                                         [[('name', '=', 'Creative')]])
        
        if not department_ids:
            print("No Creative department found")
            return {}
        
        # Get all creative department employees with their tags
        creative_employee_ids = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'hr.employee', 'search', 
                                                [[('department_id', 'in', department_ids)]])
        
        if not creative_employee_ids:
            print("No creative employees found")
            return {}
        
        # Fetch employee company and compute per-employee holidays for utilization stats
        employee_company = {}
        try:
            emp_company_data = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'hr.employee', 'read', 
                                              [creative_employee_ids], {'fields': ['company_id']})
            for emp in emp_company_data:
                cid = None
                if emp.get('company_id'):
                    cid = emp['company_id'][0] if isinstance(emp['company_id'], (list, tuple)) else emp['company_id']
                employee_company[emp['id']] = cid
        except Exception as _e:
            pass

        company_holidays_cache = {}
        company_holiday_hours_cache = {}
        employee_holiday_hours = {}
        for emp_id in creative_employee_ids:
            cid = employee_company.get(emp_id)
            if cid not in company_holidays_cache:
                holidays = get_public_holidays(models, uid, start_date, end_date, company_id=cid)
                company_holidays_cache[cid] = holidays
                # Calculate holiday hours once per company and cache it
                company_holiday_hours_cache[cid] = calculate_holiday_hours_in_period(holidays, start_date, end_date, view_type)
            employee_holiday_hours[emp_id] = company_holiday_hours_cache[cid]
        
        # Get employee details with tags
        employees_data = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'hr.employee', 'read', 
                                         [creative_employee_ids], {
                                             'fields': ['id', 'name', 'job_title', 'category_ids']
                                         })
        
        # Batch fetch all unique category IDs
        all_category_ids = set()
        for emp in employees_data:
            if emp.get('category_ids'):
                all_category_ids.update(emp['category_ids'])
        
        # Fetch all categories in one batch call
        categories_dict = {}
        if all_category_ids:
            try:
                categories = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'hr.employee.category', 'read', 
                                             [list(all_category_ids)], {'fields': ['name']})
                categories_dict = {cat['id']: cat['name'] for cat in categories if cat.get('name')}
            except Exception as e:
                print(f"Error fetching categories: {e}")
        
        # Convert dates to string format for Odoo
        start_str = start_date.strftime('%Y-%m-%d')
        end_str = end_date.strftime('%Y-%m-%d')
        
        # Fetch timesheet data for creative employees
        timesheets = models.execute_kw(
            ODOO_DB, uid, ODOO_PASSWORD,
            'account.analytic.line', 'search_read',
            [[('employee_id', 'in', creative_employee_ids),
              ('date', '>=', start_str),
              ('date', '<=', end_str),
              ('task_id.name', '!=', 'Time Off')]],
            {'fields': ['employee_id', 'unit_amount']}
        )
        
        # Fetch planning data for creative employees
        start_planning_str = start_date.strftime('%Y-%m-%d 00:00:00')
        end_planning_str = end_date.strftime('%Y-%m-%d 23:59:59')
        
        planning_slots = models.execute_kw(
            ODOO_DB, uid, ODOO_PASSWORD,
            'planning.slot', 'search_read',
            [[('resource_id', 'in', creative_employee_ids),
              ('start_datetime', '<=', end_planning_str),
              ('end_datetime', '>=', start_planning_str)]],
            {'fields': ['resource_id', 'allocated_hours', 'start_datetime', 'end_datetime']}
        )
        
        # Group timesheet and planning data by employee
        employee_data = {}
        for emp in employees_data:
            emp_id = emp['id']
            employee_data[emp_id] = {
                'name': emp.get('name', ''),
                'job_title': emp.get('job_title', ''),
                'tags': [],
                'logged_hours': 0,
                'planned_hours': 0
            }
            
            # Get tags from categories
            if emp.get('category_ids'):
                tags = [categories_dict.get(cat_id) for cat_id in emp['category_ids'] if categories_dict.get(cat_id)]
                employee_data[emp_id]['tags'] = tags
        
        # Calculate Time Off hours for each employee
        print("Calculating Time Off hours for utilization data...")
        
        # Fetch timesheet entries for Time Off tasks
        time_off_timesheets = models.execute_kw(
            ODOO_DB, uid, ODOO_PASSWORD,
            'account.analytic.line', 'search_read',
            [[('employee_id', 'in', creative_employee_ids),
              ('date', '>=', start_str),
              ('date', '<=', end_str),
              ('task_id.name', '=', 'Time Off')]],
            {'fields': ['employee_id', 'unit_amount']}
        )
        
        print(f"Found {len(time_off_timesheets)} Time Off timesheet entries for utilization data")
        
        # Calculate total Time Off hours per employee
        employee_time_off = {}
        for ts in time_off_timesheets:
            emp_field = ts.get('employee_id')
            if emp_field:
                emp_id = emp_field[0] if isinstance(emp_field, list) else emp_field
                if emp_id not in employee_time_off:
                    employee_time_off[emp_id] = 0
                employee_time_off[emp_id] += float(ts.get('unit_amount', 0))
        
        # Add Time Off hours to employee data
        for emp_id in employee_data:
            time_off_hours = employee_time_off.get(emp_id, 0)
            employee_data[emp_id]['time_off_hours'] = time_off_hours
            if time_off_hours > 0:
                print(f"Employee {employee_data[emp_id]['name']}: {time_off_hours:.1f}h Time Off")
        
        # Calculate logged hours
        for ts in timesheets:
            emp_field = ts.get('employee_id')
            if emp_field:
                emp_id = emp_field[0] if isinstance(emp_field, list) else emp_field
                if emp_id in employee_data:
                    employee_data[emp_id]['logged_hours'] += float(ts.get('unit_amount', 0))
        
        # Get planned hours from available resources (same source as Available Creatives tab)
        print("Getting planned hours from available resources...")
        available_resources = get_available_creative_resources(view_type, period)
        
        # Create a mapping of employee names to planned hours from available resources (numeric)
        resource_planned_hours = {}
        for resource in available_resources:
            try:
                planned_val = resource.get('planned_hours', 0)
                # Handle structured planned_hours from decimal_hours_to_hm_data
                if isinstance(planned_val, dict):
                    planned_val = float(planned_val.get('decimal') or 0)
                else:
                    planned_val = float(planned_val or 0)
                name_key = resource.get('name')
                if name_key:
                    resource_planned_hours[name_key] = planned_val
            except Exception:
                continue
        
        # Update employee data with planned hours from available resources
        for emp_id, emp_data in employee_data.items():
            emp_name = emp_data['name']
            if emp_name in resource_planned_hours:
                emp_data['planned_hours'] = float(resource_planned_hours[emp_name])
                print(f"Employee {emp_name}: {emp_data['planned_hours']:.1f}h planned (from available resources)")
            else:
                print(f"Employee {emp_name}: No planned hours found in available resources")
        
        # Define teams and their tag mappings
        teams = {
            'KSA': ['KSA'],
            'UAE': ['UAE'],
            'Nightshift': ['Nightshift']
        }
        
        # Calculate team statistics
        team_stats = {}
        for team_name, team_tags in teams.items():
            team_employees = []
            
            # Find employees belonging to this team
            for emp_id, emp_data in employee_data.items():
                if any(tag in emp_data['tags'] for tag in team_tags):
                    team_employees.append(emp_data)
            
            # Calculate team statistics
            total_creatives = len(team_employees)
            active_creatives = len([emp for emp in team_employees if emp['logged_hours'] > 0])
            
            # Calculate available hours using the same formula as Available Creatives tab
            # Base Available Hours based on view type - Time Off Hours - Holiday Hours
            if view_type == 'monthly':
                base_available_hours_per_employee = 184  # 184 hours per month
            elif view_type == 'weekly':
                base_available_hours_per_employee = 40   # 40 hours per week
            else:  # daily
                base_available_hours_per_employee = 8    # 8 hours per day
            
            base_available_hours = base_available_hours_per_employee * total_creatives  # Base hours for all employees
            total_time_off_hours = sum(emp.get('time_off_hours', 0) for emp in team_employees)
            
            # Calculate total holiday hours for this team based on each employee's company-specific holidays
            total_holiday_hours_for_team = 0
            for emp_data in team_employees:
                # Find the employee ID in our data - search by matching names
                for emp_id, emp_details in employee_data.items():
                    if emp_details.get('name') == emp_data['name']:
                        # Get holiday hours for this employee
                        holiday_hours = employee_holiday_hours.get(emp_id, 0)
                        total_holiday_hours_for_team += holiday_hours
                        break
            
            available_hours = base_available_hours - total_time_off_hours - total_holiday_hours_for_team
            
            planned_hours = sum(emp['planned_hours'] for emp in team_employees)  # Include all employees, not just active ones
            logged_hours = sum(emp['logged_hours'] for emp in team_employees if emp['logged_hours'] > 0)
            
            # Calculate utilization rate
            utilization_rate = (logged_hours / available_hours * 100) if available_hours > 0 else 0
            
            # Calculate variance
            variance = ((logged_hours - planned_hours) / planned_hours * 100) if planned_hours > 0 else 0
            
            team_stats[team_name] = {
                'total_creatives': total_creatives,
                'active_creatives': active_creatives,
                'available_hours': available_hours,
                'planned_hours': planned_hours,
                'logged_hours': logged_hours,
                'utilization_rate': utilization_rate,
                'variance': variance,
                'employees': team_employees
            }
        
        print(f"Processed team utilization data for {len(teams)} teams")
        # Fallback: if no stats were produced, return a simple tag-based aggregation so UI isn't empty
        if not team_stats:
            print("No team utilization stats produced; using simple tag-based fallback")
            return _compute_simple_team_utilization(period, view_type)
        return team_stats
        
    except Exception as e:
        print(f"Error fetching team utilization data: {e}")
        # Last-resort fallback
        return _compute_simple_team_utilization(period, view_type)

def _compute_simple_team_utilization(period=None, view_type='monthly'):
    """Lightweight fallback that aggregates by tags without timesheets/planning.
    Provides non-empty structure for UI even when detailed queries fail or no data.
    """
    try:
        start_date, end_date = get_date_range(view_type, period)
        employees = get_creative_employees() or []
        teams = {
            'KSA': ['KSA'],
            'UAE': ['UAE'],
            'Nightshift': ['Nightshift']
        }
        if view_type == 'monthly':
            base_per_employee = 184
        elif view_type == 'weekly':
            base_per_employee = 40
        else:
            base_per_employee = 8
        team_stats = {}
        for team_name, tags in teams.items():
            team_employees = [
                {
                    'name': emp.get('name'),
                    'job_title': emp.get('job_title', ''),
                    'tags': emp.get('tags') or [],
                    'logged_hours': 0.0,
                    'planned_hours': 0.0
                }
                for emp in employees if any(tag in (emp.get('tags') or []) for tag in tags)
            ]
            total_creatives = len(team_employees)
            if total_creatives == 0:
                continue
            base_available_hours = total_creatives * base_per_employee
            team_stats[team_name] = {
                'total_creatives': total_creatives,
                'active_creatives': 0,
                'available_hours': base_available_hours,
                'planned_hours': 0.0,
                'logged_hours': 0.0,
                'utilization_rate': 0.0,
                'variance': 0.0,
                'employees': team_employees
            }
        print(f"Fallback utilization computed for {len(team_stats)} teams from tags only")
        return team_stats
    except Exception as _e:
        print(f"Error in simple utilization fallback: {_e}")
        return {}

def get_creative_timesheet_data(period=None, view_type='monthly'):
    """
    Fetch timesheet data for creative employees for a specific period.
    
    Args:
        period (str): Period in format based on view_type:
                     - 'monthly': 'YYYY-MM' format (e.g., '2025-01')
                     - 'weekly': 'YYYY-WW' format (e.g., '2025-01')
                     - 'daily': 'YYYY-DDD' format (e.g., '2025-001')
        view_type (str): 'monthly', 'weekly', or 'daily'
    
    Returns:
        list: List of creative employees with their timesheet hours
    """
    try:
        models, uid = connect_to_odoo()
        
        if not models or not uid:
            return []
        
        # Get date range for the selected period and view type
        start_date, end_date = get_date_range(view_type, period)
        print(f"Fetching timesheet data for {view_type} view: {start_date} to {end_date}")
        
        # Find the Creative department
        department_ids = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'hr.department', 'search', 
                                         [[('name', '=', 'Creative')]])
        
        if not department_ids:
            print("No Creative department found")
            return []
        
        # Get all creative department employees
        creative_employee_ids = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'hr.employee', 'search', 
                                                [[('department_id', 'in', department_ids)]])
        
        if not creative_employee_ids:
            print("No creative employees found")
            return []
        
        # Get employee details with tags
        employees_data = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'hr.employee', 'read', 
                                         [creative_employee_ids], {
                                             'fields': ['id', 'name', 'job_title', 'category_ids']
                                         })
        
        # Batch fetch all unique category IDs for tags
        all_category_ids = set()
        for emp in employees_data:
            if emp.get('category_ids'):
                all_category_ids.update(emp['category_ids'])
        
        # Fetch all categories in one batch call
        categories_dict = {}
        if all_category_ids:
            try:
                categories = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'hr.employee.category', 'read', 
                                             [list(all_category_ids)], {'fields': ['name']})
                categories_dict = {cat['id']: cat['name'] for cat in categories if cat.get('name')}
                print(f"Fetched {len(categories_dict)} unique categories for timesheet data")
            except Exception as e:
                print(f"Error fetching categories for timesheet data: {e}")
        
        # Convert dates to string format for Odoo
        start_str = start_date.strftime('%Y-%m-%d')
        end_str = end_date.strftime('%Y-%m-%d')
        
        print(f"Searching timesheets from {start_str} to {end_str}")
        
        # Fetch timesheet data for creative employees
        timesheets = models.execute_kw(
            ODOO_DB, uid, ODOO_PASSWORD,
            'account.analytic.line', 'search_read',
            [[('employee_id', 'in', creative_employee_ids),
              ('date', '>=', start_str),
              ('date', '<=', end_str),
              ('task_id.name', '!=', 'Time Off')]],  # Exclude "Time Off" tasks
            {'fields': ['employee_id', 'unit_amount', 'task_id', 'date', 'project_id']}
        )
        
        print(f"Found {len(timesheets)} timesheet entries for creative employees")
        
        # Compute set of unbilled project IDs based on Agreement Type on project.project
        # Unbilled criteria: Agreement Type (x_studio_agreement_type_1) is missing/False or equals "Internal"
        project_ids = set()
        for ts in timesheets:
            proj = ts.get('project_id')
            if proj:
                try:
                    project_ids.add(proj[0] if isinstance(proj, (list, tuple)) else int(proj))
                except Exception:
                    pass
        unbilled_project_ids = set()
        if project_ids:
            try:
                projects = models.execute_kw(
                    ODOO_DB, uid, ODOO_PASSWORD,
                    'project.project', 'read',
                    [list(project_ids)],
                    {'fields': ['display_name', 'x_studio_agreement_type_1']}
                )
                for proj in projects:
                    agreement = proj.get('x_studio_agreement_type_1')
                    is_unbilled = False
                    if not agreement:
                        is_unbilled = True
                    elif isinstance(agreement, str):
                        is_unbilled = agreement.strip().lower() == 'internal'
                    elif isinstance(agreement, (list, tuple)) and len(agreement) > 1:
                        try:
                            is_unbilled = str(agreement[1]).strip().lower() == 'internal'
                        except Exception:
                            is_unbilled = False
                    if is_unbilled and proj.get('id') is not None:
                        unbilled_project_ids.add(proj['id'])
            except Exception as _e:
                # If project lookup fails, treat all as billed (safe)
                unbilled_project_ids = set()
        
        # Group timesheet data by employee
        employee_timesheets = {}
        for ts in timesheets:
            emp_field = ts.get('employee_id')
            if emp_field:
                emp_id = emp_field[0] if isinstance(emp_field, list) else emp_field
                if emp_id not in employee_timesheets:
                    employee_timesheets[emp_id] = {
                        'total_hours': 0,
                        'entries': [],
                        'unbilled_hours': 0
                    }
                
                # Add hours to total
                hours = float(ts.get('unit_amount', 0))
                employee_timesheets[emp_id]['total_hours'] += hours
                # Accumulate unbilled hours if the timesheet's project qualifies
                proj = ts.get('project_id')
                if proj:
                    try:
                        pid = proj[0] if isinstance(proj, (list, tuple)) else int(proj)
                        if pid in unbilled_project_ids:
                            employee_timesheets[emp_id]['unbilled_hours'] += hours
                    except Exception:
                        pass
                
                # Store entry details
                task_field = ts.get('task_id')
                task_name = task_field[1] if isinstance(task_field, list) else str(task_field) if task_field else 'No Task'
                
                employee_timesheets[emp_id]['entries'].append({
                    'hours': hours,
                    'task': task_name,
                    'date': ts.get('date', '')
                })
        
        # Create final result with employee details and timesheet data
        result = []
        for employee in employees_data:
            emp_id = employee['id']
            timesheet_data = employee_timesheets.get(emp_id, {'total_hours': 0, 'entries': []})
            
            # Get tags from cached categories
            tags = []
            if employee.get('category_ids'):
                tags = [categories_dict.get(cat_id) for cat_id in employee['category_ids'] if categories_dict.get(cat_id)]
            
            # Format hours data for display
            total_hours_raw = timesheet_data['total_hours']
            unbilled_hours_raw = timesheet_data.get('unbilled_hours', 0)
            
            # Format timesheet entries with proper time formatting
            formatted_entries = []
            for entry in timesheet_data['entries']:
                formatted_entries.append({
                    'hours': decimal_hours_to_hm_data(entry['hours']),
                    'task': entry['task'],
                    'date': entry['date']
                })
            
            result.append({
                'id': emp_id,
                'name': employee.get('name', ''),
                'job_title': employee.get('job_title', ''),
                'tags': tags,
                'total_hours': decimal_hours_to_hm_data(total_hours_raw),
                'unbilled_hours': decimal_hours_to_hm_data(unbilled_hours_raw),
                'timesheet_entries': formatted_entries,
                'period_start': start_date.isoformat(),
                'period_end': end_date.isoformat()
            })
        
        # Sort by total hours (descending)
        result.sort(key=lambda x: x['total_hours']['decimal'], reverse=True)
        
        print(f"Processed timesheet data for {len(result)} creative employees")
        return result
        
    except Exception as e:
        print(f"Error fetching timesheet data: {e}")
        return []

def get_available_creative_resources(view_type='monthly', period=None):
    """
    Fetch available creative resources from Planning > Resources with accurate utilization
    
    Args:
        view_type (str): 'monthly' or 'weekly'
        period (str): For monthly: 'YYYY-MM' format (e.g., '2025-01')
                     For weekly: 'YYYY-WW' format (e.g., '2025-01' for week 1)
    """
    try:
        models, uid = connect_to_odoo()
        
        if not models or not uid:
            return []
        
        # Get date range for the selected view type and period
        start_date, end_date = get_date_range(view_type, period)
        print(f"Analyzing utilization for {view_type} view: {start_date} to {end_date}")
        
        # Find the Creative department
        department_ids = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'hr.department', 'search', 
                                         [[('name', '=', 'Creative')]])
        
        if not department_ids:
            print("No Creative department found for resources")
            return []
        
        print(f"Creative department IDs: {department_ids}")
        
        # Get all creative department employees
        creative_employee_ids = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'hr.employee', 'search', 
                                                [[('department_id', 'in', department_ids)]])
        
        print(f"Found {len(creative_employee_ids)} creative employees")
        
        if not creative_employee_ids:
            print("No creative employees found")
            return []
        
        # Get employee details for all creative employees (include resource_id and resource_calendar_id)
        employees_data = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'hr.employee', 'read', 
                                         [creative_employee_ids], {
                                             'fields': ['name', 'job_title', 'category_ids', 'resource_id', 'resource_calendar_id']
                                         })
        
        print(f"Processing {len(employees_data)} employees...")
        
        # Batch fetch all unique category IDs for tags
        all_category_ids = set()
        for emp in employees_data:
            if emp.get('category_ids'):
                all_category_ids.update(emp['category_ids'])
        
        # Fetch all categories in one batch call
        categories_dict = {}
        if all_category_ids:
            try:
                categories = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'hr.employee.category', 'read', 
                                             [list(all_category_ids)], {'fields': ['name']})
                categories_dict = {cat['id']: cat['name'] for cat in categories if cat.get('name')}
                print(f"Fetched {len(categories_dict)} unique categories for available resources")
            except Exception as e:
                print(f"Error fetching categories for available resources: {e}")
        
        # Build mapping between hr.employee and planning/resource IDs
        employee_availability = {}
        employee_to_resource_id = {}
        resource_id_to_employee = {}
        
        # Calculate base available hours PER EMPLOYEE using their resource calendars
        # We initialize with placeholders; will compute per-employee below
        _, default_base_available_hours = calculate_working_days_and_hours(start_date, end_date)
        print(f"Default base available hours for {view_type} view ({start_date} to {end_date}): {default_base_available_hours} hours (fallback)")
        
        # Initialize all employees with 0% allocation
        for employee in employees_data:
            # Get tags from cached categories
            tags = []
            if employee.get('category_ids'):
                tags = [categories_dict.get(cat_id) for cat_id in employee['category_ids'] if categories_dict.get(cat_id)]
            
            # Map resource_id if available
            if employee.get('resource_id'):
                # resource_id returns [id, display_name]
                res_id = employee['resource_id'][0] if isinstance(employee['resource_id'], (list, tuple)) else employee['resource_id']
                if res_id:
                    employee_to_resource_id[employee['id']] = res_id
                    resource_id_to_employee[res_id] = employee['id']

            emp_id = employee['id']
            # Compute per-employee working days from calendar
            emp_working_days, emp_base_hours = calculate_employee_working_days_and_hours(models, uid, emp_id, start_date, end_date)
            employee_availability[emp_id] = {
                'name': employee.get('name', ''),
                'job_title': employee.get('job_title', ''),
                'tags': tags,
                'allocated_percentage': 0,
                'planned_hours': 0,
                'base_available_hours': emp_base_hours,
                'time_off_hours': 0,
                'available_hours': emp_base_hours,  # Will be updated after time off calculation
                'start_datetime': start_date,
                'end_datetime': end_date
            }
        
        # Calculate Time Off hours for each employee
        print("Calculating Time Off hours...")
        
        # Convert dates to string format for Odoo timesheet queries
        start_str = start_date.strftime('%Y-%m-%d')
        end_str = end_date.strftime('%Y-%m-%d')
        
        # Fetch timesheet entries for Time Off tasks
        time_off_timesheets = models.execute_kw(
            ODOO_DB, uid, ODOO_PASSWORD,
            'account.analytic.line', 'search_read',
            [[('employee_id', 'in', creative_employee_ids),
              ('date', '>=', start_str),
              ('date', '<=', end_str),
              ('task_id.name', '=', 'Time Off')]],
            {'fields': ['employee_id', 'unit_amount']}
        )
        
        print(f"Found {len(time_off_timesheets)} Time Off timesheet entries")
        
        # Calculate total Time Off hours per employee
        employee_time_off = {}
        for ts in time_off_timesheets:
            emp_field = ts.get('employee_id')
            if emp_field:
                emp_id = emp_field[0] if isinstance(emp_field, list) else emp_field
                if emp_id not in employee_time_off:
                    employee_time_off[emp_id] = 0
                employee_time_off[emp_id] += float(ts.get('unit_amount', 0))
        
        # Fetch public holidays for each employee's company and compute per-employee holiday hours
        # Build employee -> company_id mapping
        employee_company = {}
        try:
            emp_company_data = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'hr.employee', 'read', 
                                              [creative_employee_ids], {'fields': ['company_id']})
            for emp in emp_company_data:
                cid = None
                if emp.get('company_id'):
                    cid = emp['company_id'][0] if isinstance(emp['company_id'], (list, tuple)) else emp['company_id']
                employee_company[emp['id']] = cid
        except Exception as _e:
            pass

        # Cache holidays per company to avoid repeated reads
        company_holidays_cache = {}
        # Cache holiday hours by (company_id, weekdays_tuple) to avoid recalculating for employees with same company and weekdays
        holiday_hours_cache = {}
        employee_holiday_hours = {}
        for emp_id in creative_employee_ids:
            cid = employee_company.get(emp_id)
            if cid not in company_holidays_cache:
                holidays = get_public_holidays(models, uid, start_date, end_date, company_id=cid)
                company_holidays_cache[cid] = holidays
            holidays = company_holidays_cache.get(cid) or []
            
            # Use employee-specific working weekdays for accuracy
            emp_weekdays = get_employee_working_weekdays(models, uid, emp_id)
            weekdays_key = tuple(sorted(emp_weekdays)) if emp_weekdays else None
            cache_key = (cid, weekdays_key)
            
            if cache_key not in holiday_hours_cache:
                holiday_hours_cache[cache_key] = calculate_holiday_hours_in_period(holidays, start_date, end_date, view_type, working_weekdays=emp_weekdays)
            
            employee_holiday_hours[emp_id] = holiday_hours_cache[cache_key]
        
        # Update employee availability with Time Off hours and per-employee public holidays using per-employee base
        for emp_id, time_off_hours in employee_time_off.items():
            if emp_id in employee_availability:
                employee_availability[emp_id]['time_off_hours'] = time_off_hours
                emp_base = employee_availability[emp_id]['base_available_hours']
                emp_holiday = float(employee_holiday_hours.get(emp_id) or 0.0)
                employee_availability[emp_id]['available_hours'] = emp_base - time_off_hours - emp_holiday
                print(f"Employee {employee_availability[emp_id]['name']}: {time_off_hours:.1f}h Time Off, {emp_holiday:.1f}h Public Holidays, {employee_availability[emp_id]['available_hours']:.1f}h available")
        
        # For employees without time off, still deduct per-employee public holidays
        for emp_id in employee_availability:
            if emp_id not in employee_time_off:
                emp_base = employee_availability[emp_id]['base_available_hours']
                emp_holiday = float(employee_holiday_hours.get(emp_id) or 0.0)
                employee_availability[emp_id]['available_hours'] = emp_base - emp_holiday
                print(f"Employee {employee_availability[emp_id]['name']}: 0h Time Off, {emp_holiday:.1f}h Public Holidays, {employee_availability[emp_id]['available_hours']:.1f}h available")
        
        # Get planning slots for creative employees using resource_id field
        # Convert dates to string format for Odoo
        start_str = start_date.strftime('%Y-%m-%d 00:00:00')
        end_str = end_date.strftime('%Y-%m-%d 23:59:59')
        
        print(f"Searching planning slots from {start_str} to {end_str}")
        print(f"Looking for planning slots for {len(employee_to_resource_id)} creative employees (via resource IDs)")
        
        # First, let's check if there are any planning slots at all for these employees
        # Use planning.resource/resource_id values rather than hr.employee IDs
        resource_ids_for_all = list(employee_to_resource_id.values())
        all_slots = execute_odoo_call_with_retry(
            models,
            uid,
            'planning.slot',
            'search',
            [['|', ('resource_id', 'in', resource_ids_for_all), ('employee_id', 'in', creative_employee_ids)]],
            {'limit': 1000}
        )
        print(f"Total planning slots found for creative employees (any date) using resource or employee IDs: {len(all_slots)}")
        
        if all_slots:
            pass
        
        # Search for planning slots overlapping the filter range for all Creative employees/resources
        # Paginate to avoid the global 1000 results cap
        resource_ids = []
        try:
            offset = 0
            page_limit = 1000
            while True:
                ids_page = execute_odoo_call_with_retry(
                    models,
                    uid,
                    'planning.slot',
                    'search',
                    [['&', '&',
                      '|', ('resource_id', 'in', resource_ids_for_all), ('employee_id', 'in', creative_employee_ids),
                      ('start_datetime', '<=', end_str),
                      ('end_datetime', '>=', start_str)]],
                    {'limit': page_limit, 'offset': offset}
                )
                if not ids_page:
                    break
                resource_ids.extend(ids_page)
                if len(ids_page) < page_limit:
                    break
                offset += page_limit
            print(f"Successfully found {len(resource_ids)} planning slots (paginated)")
        except Exception as e:
            print(f"Error fetching planning slots for Creative (paginated): {e}")
            resource_ids = []
        
        if resource_ids:
            print(f"Found {len(resource_ids)} planning slots for creative employees (resource mapped)")
            
            # Get resource data with employee information
            # Read resources in chunks to avoid payload limits
            resources = []
            # Page in smaller chunks to reduce payload pressure
            read_batch_size = 250
            for i in range(0, len(resource_ids), read_batch_size):
                batch_ids = resource_ids[i:i+read_batch_size]
                batch = execute_odoo_call_with_retry(models, uid, 'planning.slot', 'read', 
                                            [batch_ids], {
                                                'fields': [
                                                    'resource_id',
                                                    'employee_id',
                                                    'start_datetime',
                                                    'end_datetime',
                                                    'allocated_hours',
                                                    'allocated_percentage'
                                                ]
                                            })
                resources.extend(batch or [])
            
            print(f"Sample resource data: {resources[0] if resources else 'No resources'}")
            
            # Group resources by employee and calculate allocation per-slot using proportional overlap
            # Overtime preserved by summing all slots; no artificial 8h cap
            employee_resources = {}
            for resource in resources:
                employee_id = None
                # Prefer mapping via planning.resource/resource_id if present
                if resource.get('resource_id'):
                    res_id = resource['resource_id'][0] if isinstance(resource['resource_id'], (list, tuple)) else resource['resource_id']
                    if res_id in resource_id_to_employee:
                        employee_id = resource_id_to_employee[res_id]
                # Fallback to employee_id on the slot
                if employee_id is None and resource.get('employee_id'):
                    possible_emp_id = resource['employee_id'][0] if isinstance(resource['employee_id'], (list, tuple)) else resource['employee_id']
                    if possible_emp_id in employee_availability:
                        employee_id = possible_emp_id
                if employee_id is None:
                    continue

                if employee_id not in employee_resources:
                    employee_resources[employee_id] = {
                        'total_hours': 0.0,
                        'slot_count': 0
                    }

                # Parse times
                task_start = datetime.datetime.strptime(resource['start_datetime'], '%Y-%m-%d %H:%M:%S')
                task_end = datetime.datetime.strptime(resource['end_datetime'], '%Y-%m-%d %H:%M:%S')
                filter_start = datetime.datetime.strptime(start_str, '%Y-%m-%d %H:%M:%S')
                filter_end = datetime.datetime.strptime(end_str, '%Y-%m-%d %H:%M:%S')
                # Overlap
                overlap_start = max(task_start, filter_start)
                overlap_end = min(task_end, filter_end)
                if overlap_end <= overlap_start:
                    continue
                # Count this slot
                employee_resources[employee_id]['slot_count'] += 1
                # Proportional hours
                slot_total_seconds = max((task_end - task_start).total_seconds(), 0)
                overlap_seconds = max((overlap_end - overlap_start).total_seconds(), 0)
                slot_allocated_hours = float(resource.get('allocated_hours', 0) or 0)
                seg_hours = (slot_allocated_hours * (overlap_seconds / slot_total_seconds)) if slot_total_seconds > 0 else 0.0
                employee_resources[employee_id]['total_hours'] += seg_hours

            
            # Update employee availability with calculated data
            for employee_id, resource_data in employee_resources.items():
                allocated_hours = resource_data['total_hours']
                available_hours = employee_availability[employee_id]['available_hours']
                

                
                # Calculate allocated percentage based on available hours (after time off deduction)
                allocated_percentage = min((allocated_hours / available_hours) * 100, 100) if available_hours > 0 else 0
                
                # Update the employee's allocation data
                employee_availability[employee_id]['allocated_percentage'] = allocated_percentage
                employee_availability[employee_id]['planned_hours'] = allocated_hours
                
                slot_count = int(resource_data.get('slot_count') or 0)
                employee_name = employee_availability[employee_id]['name']
                print(f"Employee {employee_name}: {allocated_hours:.1f}h allocated ({allocated_percentage:.1f}%) from {slot_count} slots")
        else:
            print("No planning slots found for the current week - showing all employees as 100% available")
        
        # Convert to list and calculate availability
        available_resources = []
        for employee_id, data in employee_availability.items():
            # Calculate availability percentage (100% - allocated_percentage)
            availability_percentage = 100 - data['allocated_percentage']
            
            available_resources.append({
                'name': data['name'],
                'job_title': data['job_title'],
                'tags': data['tags'],
                'allocated_percentage': data['allocated_percentage'],
                'planned_hours': decimal_hours_to_hm_data(data['planned_hours']),
                'availability_percentage': availability_percentage,
                'base_available_hours': decimal_hours_to_hm_data(data['base_available_hours']),
                'time_off_hours': decimal_hours_to_hm_data(data['time_off_hours']),
                'available_hours': decimal_hours_to_hm_data(data['available_hours']),
                'start_datetime': data['start_datetime'],
                'end_datetime': data['end_datetime']
            })
        
        print(f"Found {len(available_resources)} creative resources with accurate utilization data")
        return available_resources
        
    except Exception as e:
        print(f"Error fetching available resources: {e}")
        return []

def get_creative_strategy_employees():
    """Fetch employees from Creative Strategy department"""
    try:
        models, uid = connect_to_odoo()
        
        if not models or not uid:
            return []
        
        # Find the "Creative Strategy" department
        department_ids = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'hr.department', 'search', 
                                         [[('name', '=', 'Creative Strategy')]])
        
        if not department_ids:
            print("No 'Creative Strategy' department found")
            return []
        
        print(f"Found Creative Strategy department with ID: {department_ids}")
        
        # Get employees in the Creative Strategy department
        employee_ids = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'hr.employee', 'search', 
                                       [[('department_id', 'in', department_ids)]])
        
        if not employee_ids:
            print("No employees found in Creative Strategy department")
            return []
        
        print(f"Found {len(employee_ids)} employees in Creative Strategy department")
        
        # Get employee data with categories/tags
        employees = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'hr.employee', 'read', 
                                    [employee_ids], {
                                        'fields': [
                                            'name', 
                                            'job_title', 
                                            'work_email',
                                            'category_ids'  # This is the many2many field for categories/tags
                                        ]
                                    })
        
        # Batch fetch all unique category IDs
        all_category_ids = set()
        for emp in employees:
            if emp.get('category_ids'):
                all_category_ids.update(emp['category_ids'])
        
        # Fetch all categories in one batch call
        categories_dict = {}
        if all_category_ids:
            try:
                categories = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'hr.employee.category', 'read', 
                                             [list(all_category_ids)], {'fields': ['name']})
                
                # Create a dictionary for fast lookup
                categories_dict = {cat['id']: cat['name'] for cat in categories if cat.get('name')}
                print(f"Fetched {len(categories_dict)} unique categories in batch for Creative Strategy")
                
            except Exception as e:
                print(f"Error fetching categories in batch for Creative Strategy: {e}")
                # Fallback to individual calls if batch fails
                print("Falling back to individual category fetching for Creative Strategy...")
                for emp in employees:
                    if emp.get('category_ids'):
                        try:
                            categories = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'hr.employee.category', 'read', 
                                                         [emp['category_ids']], {'fields': ['name']})
                            categories_dict.update({cat['id']: cat['name'] for cat in categories if cat.get('name')})
                        except Exception as e2:
                            print(f"Error fetching categories for Creative Strategy employee {emp.get('name')}: {e2}")
        
        # Process employees using the cached categories
        processed_employees = []
        for emp in employees:
            employee_data = {
                'name': emp.get('name', ''),
                'job_title': emp.get('job_title', ''),
                'email': emp.get('work_email', ''),
                'tags': []
            }
            
            # Get tags from cached categories
            if emp.get('category_ids'):
                tags = [categories_dict.get(cat_id) for cat_id in emp['category_ids'] if categories_dict.get(cat_id)]
                employee_data['tags'] = tags
                
                if tags:
                    print(f"Creative Strategy employee {emp.get('name')} has tags: {tags}")
            
            processed_employees.append(employee_data)
        
        return processed_employees
        
    except Exception as e:
        print(f"Error fetching Creative Strategy employees: {e}")
        return []

def get_creative_strategy_team_utilization_data(period=None, view_type='monthly'):
    """
    Fetch team utilization data for Creative Strategy department teams (KSA, UAE, and Nightshift).
    
    Args:
        period (str): Period in format based on view_type (e.g., '2025-01' for monthly, '2025-W01' for weekly, '2025-001' for daily)
        view_type (str): View type - 'monthly', 'weekly', or 'daily'
    
    Returns:
        dict: Team utilization data with stats for each team
    """
    try:
        models, uid = connect_to_odoo()
        
        if not models or not uid:
            return {}
        
        # Get date range for the selected period and view type
        start_date, end_date = get_date_range(view_type, period)
        print(f"Fetching Creative Strategy team utilization data for {view_type} view: {start_date} to {end_date}")
        
        # Fetch per-employee company and compute individual holiday hours
        employee_company = {}
        try:
            emp_company_data = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'hr.employee', 'read', 
                                              [creative_employee_ids], {'fields': ['company_id']})
            for emp in emp_company_data:
                cid = None
                if emp.get('company_id'):
                    cid = emp['company_id'][0] if isinstance(emp['company_id'], (list, tuple)) else emp['company_id']
                employee_company[emp['id']] = cid
        except Exception as _e:
            pass

        company_holidays_cache = {}
        company_holiday_hours_cache = {}
        employee_holiday_hours = {}
        for emp_id in creative_employee_ids:
            cid = employee_company.get(emp_id)
            if cid not in company_holidays_cache:
                holidays = get_public_holidays(models, uid, start_date, end_date, company_id=cid)
                company_holidays_cache[cid] = holidays
                # Calculate holiday hours once per company and cache it
                company_holiday_hours_cache[cid] = calculate_holiday_hours_in_period(holidays, start_date, end_date, view_type)
            employee_holiday_hours[emp_id] = company_holiday_hours_cache[cid]
        
        # Find the Creative Strategy department
        department_ids = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'hr.department', 'search', 
                                         [[('name', '=', 'Creative Strategy')]])
        
        if not department_ids:
            print("No Creative Strategy department found")
            return {}
        
        # Get all Creative Strategy department employees with their tags
        creative_strategy_employee_ids = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'hr.employee', 'search', 
                                                        [[('department_id', 'in', department_ids)]])
        
        if not creative_strategy_employee_ids:
            print("No Creative Strategy employees found")
            return {}
        
        # Get employee details with tags
        employees_data = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'hr.employee', 'read', 
                                         [creative_strategy_employee_ids], {
                                             'fields': ['id', 'name', 'job_title', 'category_ids']
                                         })
        
        # Batch fetch all unique category IDs
        all_category_ids = set()
        for emp in employees_data:
            if emp.get('category_ids'):
                all_category_ids.update(emp['category_ids'])
        
        # Fetch all categories in one batch call
        categories_dict = {}
        if all_category_ids:
            try:
                categories = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'hr.employee.category', 'read', 
                                             [list(all_category_ids)], {'fields': ['name']})
                categories_dict = {cat['id']: cat['name'] for cat in categories if cat.get('name')}
            except Exception as e:
                print(f"Error fetching categories for Creative Strategy: {e}")
        
        # Convert dates to string format for Odoo
        start_str = start_date.strftime('%Y-%m-%d')
        end_str = end_date.strftime('%Y-%m-%d')
        
        # Fetch timesheet data for Creative Strategy employees
        timesheets = models.execute_kw(
            ODOO_DB, uid, ODOO_PASSWORD,
            'account.analytic.line', 'search_read',
            [[('employee_id', 'in', creative_strategy_employee_ids),
              ('date', '>=', start_str),
              ('date', '<=', end_str),
              ('task_id.name', '!=', 'Time Off')]],
            {'fields': ['employee_id', 'unit_amount']}
        )
        
        # Fetch planning data for Creative Strategy employees
        start_planning_str = start_date.strftime('%Y-%m-%d 00:00:00')
        end_planning_str = end_date.strftime('%Y-%m-%d 23:59:59')
        
        planning_slots = models.execute_kw(
            ODOO_DB, uid, ODOO_PASSWORD,
            'planning.slot', 'search_read',
            [[('resource_id', 'in', creative_strategy_employee_ids),
              ('start_datetime', '<=', end_planning_str),
              ('end_datetime', '>=', start_planning_str)]],
            {'fields': ['resource_id', 'allocated_hours', 'start_datetime', 'end_datetime']}
        )
        
        # Group timesheet and planning data by employee
        employee_data = {}
        for emp in employees_data:
            emp_id = emp['id']
            employee_data[emp_id] = {
                'name': emp.get('name', ''),
                'job_title': emp.get('job_title', ''),
                'tags': [],
                'logged_hours': 0,
                'planned_hours': 0
            }
            
            # Get tags from categories
            if emp.get('category_ids'):
                tags = [categories_dict.get(cat_id) for cat_id in emp['category_ids'] if categories_dict.get(cat_id)]
                employee_data[emp_id]['tags'] = tags
        
        # Calculate Time Off hours for each employee
        print("Calculating Time Off hours for Creative Strategy utilization data...")
        
        # Fetch timesheet entries for Time Off tasks
        time_off_timesheets = models.execute_kw(
            ODOO_DB, uid, ODOO_PASSWORD,
            'account.analytic.line', 'search_read',
            [[('employee_id', 'in', creative_strategy_employee_ids),
              ('date', '>=', start_str),
              ('date', '<=', end_str),
              ('task_id.name', '=', 'Time Off')]],
            {'fields': ['employee_id', 'unit_amount']}
        )
        
        print(f"Found {len(time_off_timesheets)} Time Off timesheet entries for Creative Strategy utilization data")
        
        # Calculate total Time Off hours per employee
        employee_time_off = {}
        for ts in time_off_timesheets:
            emp_field = ts.get('employee_id')
            if emp_field:
                emp_id = emp_field[0] if isinstance(emp_field, list) else emp_field
                if emp_id not in employee_time_off:
                    employee_time_off[emp_id] = 0
                employee_time_off[emp_id] += float(ts.get('unit_amount', 0))
        
        # Add Time Off hours to employee data
        for emp_id in employee_data:
            time_off_hours = employee_time_off.get(emp_id, 0)
            employee_data[emp_id]['time_off_hours'] = time_off_hours
            if time_off_hours > 0:
                print(f"Creative Strategy employee {employee_data[emp_id]['name']}: {time_off_hours:.1f}h Time Off")
        
        # Calculate logged hours
        for ts in timesheets:
            emp_field = ts.get('employee_id')
            if emp_field:
                emp_id = emp_field[0] if isinstance(emp_field, list) else emp_field
                if emp_id in employee_data:
                    employee_data[emp_id]['logged_hours'] += float(ts.get('unit_amount', 0))
        
        # Get planned hours from available resources (same source as Available Creatives tab)
        print("Getting planned hours from available resources for Creative Strategy...")
        available_resources = get_available_creative_strategy_resources(view_type, period)
        
        # Create a mapping of employee names to planned hours from available resources (numeric)
        resource_planned_hours = {}
        for resource in available_resources:
            try:
                planned_val = resource.get('planned_hours', 0)
                if isinstance(planned_val, dict):
                    planned_val = float(planned_val.get('decimal') or 0)
                else:
                    planned_val = float(planned_val or 0)
                name_key = resource.get('name')
                if name_key:
                    resource_planned_hours[name_key] = planned_val
            except Exception:
                continue
        
        # Update employee data with planned hours from available resources
        for emp_id, emp_data in employee_data.items():
            emp_name = emp_data['name']
            if emp_name in resource_planned_hours:
                emp_data['planned_hours'] = float(resource_planned_hours[emp_name])
                print(f"Creative Strategy employee {emp_name}: {emp_data['planned_hours']:.1f}h planned (from available resources)")
            else:
                print(f"Creative Strategy employee {emp_name}: No planned hours found in available resources")
        
        # Calculate team statistics - Treat Creative Strategy as one unified department
        team_stats = {}
        
        # For Creative Strategy, treat all employees as one unified team
        all_employees = list(employee_data.values())
        
        # Calculate overall department statistics
        total_creatives = len(all_employees)
        active_creatives = len([emp for emp in all_employees if emp['logged_hours'] > 0])
        
        # Calculate available hours using the same formula as Available Creatives tab
        # Base Available Hours based on view type - Time Off Hours
        working_days, base_available_hours_per_employee = calculate_working_days_and_hours(start_date, end_date)
        print(f"Utilization calculation - base hours per employee: {base_available_hours_per_employee} hours ({working_days} working days)")
        
        base_available_hours = base_available_hours_per_employee * total_creatives  # Base hours for all employees
        total_time_off_hours = sum(emp.get('time_off_hours', 0) for emp in all_employees)
        total_holiday_hours_for_team = 0  # TODO: Calculate holiday hours per employee properly * total_creatives  # Public holidays affect all employees
        available_hours = base_available_hours - total_time_off_hours - total_holiday_hours_for_team
        
        planned_hours = sum(emp['planned_hours'] for emp in all_employees)  # Include all employees, not just active ones
        logged_hours = sum(emp['logged_hours'] for emp in all_employees if emp['logged_hours'] > 0)
        
        # Calculate utilization rate
        utilization_rate = (logged_hours / available_hours * 100) if available_hours > 0 else 0
        
        # Calculate variance
        variance = ((logged_hours - planned_hours) / planned_hours * 100) if planned_hours > 0 else 0
        
        # Create one unified team entry
        team_stats['Creative Strategy'] = {
            'total_creatives': total_creatives,
            'active_creatives': active_creatives,
            'available_hours': available_hours,
            'planned_hours': planned_hours,
            'logged_hours': logged_hours,
            'utilization_rate': utilization_rate,
            'variance': variance,
            'employees': all_employees
        }
        
        print(f"Processed Creative Strategy team utilization data for unified department with {total_creatives} employees")
        return team_stats
        
    except Exception as e:
        print(f"Error fetching Creative Strategy team utilization data: {e}")
        return {}

def get_creative_strategy_timesheet_data(period=None, view_type='monthly'):
    """
    Fetch timesheet data for Creative Strategy employees for a specific period.
    
    Args:
        period (str): Period in format based on view_type:
                     - 'monthly': 'YYYY-MM' format (e.g., '2025-01')
                     - 'weekly': 'YYYY-WW' format (e.g., '2025-01')
                     - 'daily': 'YYYY-DDD' format (e.g., '2025-001')
        view_type (str): 'monthly', 'weekly', or 'daily'
    
    Returns:
        list: List of Creative Strategy employees with their timesheet hours
    """
    try:
        models, uid = connect_to_odoo()
        
        if not models or not uid:
            return []
        
        # Get date range for the selected period and view type
        start_date, end_date = get_date_range(view_type, period)
        print(f"Fetching Creative Strategy timesheet data for {view_type} view: {start_date} to {end_date}")
        
        # Find the Creative Strategy department
        department_ids = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'hr.department', 'search', 
                                         [[('name', '=', 'Creative Strategy')]])
        
        if not department_ids:
            print("No Creative Strategy department found")
            return []
        
        # Get all Creative Strategy department employees
        creative_strategy_employee_ids = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'hr.employee', 'search', 
                                                        [[('department_id', 'in', department_ids)]])
        
        if not creative_strategy_employee_ids:
            print("No Creative Strategy employees found")
            return []
        
        # Get employee details with tags
        employees_data = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'hr.employee', 'read', 
                                         [creative_strategy_employee_ids], {
                                             'fields': ['id', 'name', 'job_title', 'category_ids']
                                         })
        
        # Batch fetch all unique category IDs for tags
        all_category_ids = set()
        for emp in employees_data:
            if emp.get('category_ids'):
                all_category_ids.update(emp['category_ids'])
        
        # Fetch all categories in one batch call
        categories_dict = {}
        if all_category_ids:
            try:
                categories = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'hr.employee.category', 'read', 
                                             [list(all_category_ids)], {'fields': ['name']})
                categories_dict = {cat['id']: cat['name'] for cat in categories if cat.get('name')}
                print(f"Fetched {len(categories_dict)} unique categories for Creative Strategy timesheet data")
            except Exception as e:
                print(f"Error fetching categories for Creative Strategy timesheet data: {e}")
        
        # Convert dates to string format for Odoo
        start_str = start_date.strftime('%Y-%m-%d')
        end_str = end_date.strftime('%Y-%m-%d')
        
        print(f"Searching Creative Strategy timesheets from {start_str} to {end_str}")
        
        # Fetch timesheet data for Creative Strategy employees
        timesheets = models.execute_kw(
            ODOO_DB, uid, ODOO_PASSWORD,
            'account.analytic.line', 'search_read',
            [[('employee_id', 'in', creative_strategy_employee_ids),
              ('date', '>=', start_str),
              ('date', '<=', end_str),
              ('task_id.name', '!=', 'Time Off')]],  # Exclude "Time Off" tasks
            {'fields': ['employee_id', 'unit_amount', 'task_id', 'date', 'project_id']}
        )
        
        print(f"Found {len(timesheets)} timesheet entries for Creative Strategy employees")
        
        # Compute set of unbilled project IDs based on Agreement Type on project.project
        # Unbilled criteria: Agreement Type (x_studio_agreement_type_1) is missing/False or equals "Internal"
        project_ids = set()
        for ts in timesheets:
            proj = ts.get('project_id')
            if proj:
                try:
                    project_ids.add(proj[0] if isinstance(proj, (list, tuple)) else int(proj))
                except Exception:
                    pass
        unbilled_project_ids = set()
        if project_ids:
            try:
                projects = models.execute_kw(
                    ODOO_DB, uid, ODOO_PASSWORD,
                    'project.project', 'read',
                    [list(project_ids)],
                    {'fields': ['display_name', 'x_studio_agreement_type_1']}
                )
                for proj in projects:
                    agreement = proj.get('x_studio_agreement_type_1')
                    is_unbilled = False
                    if not agreement:
                        is_unbilled = True
                    elif isinstance(agreement, str):
                        is_unbilled = agreement.strip().lower() == 'internal'
                    elif isinstance(agreement, (list, tuple)) and len(agreement) > 1:
                        try:
                            is_unbilled = str(agreement[1]).strip().lower() == 'internal'
                        except Exception:
                            is_unbilled = False
                    if is_unbilled and proj.get('id') is not None:
                        unbilled_project_ids.add(proj['id'])
            except Exception as _e:
                # If project lookup fails, treat all as billed (safe)
                unbilled_project_ids = set()
        
        # Group timesheet data by employee
        employee_timesheets = {}
        for ts in timesheets:
            emp_field = ts.get('employee_id')
            if emp_field:
                emp_id = emp_field[0] if isinstance(emp_field, list) else emp_field
                if emp_id not in employee_timesheets:
                    employee_timesheets[emp_id] = {
                        'total_hours': 0,
                        'entries': [],
                        'unbilled_hours': 0
                    }
                
                # Add hours to total
                hours = float(ts.get('unit_amount', 0))
                employee_timesheets[emp_id]['total_hours'] += hours
                # Accumulate unbilled hours if the timesheet's project qualifies
                proj = ts.get('project_id')
                if proj:
                    try:
                        pid = proj[0] if isinstance(proj, (list, tuple)) else int(proj)
                        if pid in unbilled_project_ids:
                            employee_timesheets[emp_id]['unbilled_hours'] += hours
                    except Exception:
                        pass
                
                # Store entry details
                task_field = ts.get('task_id')
                task_name = task_field[1] if isinstance(task_field, list) else str(task_field) if task_field else 'No Task'
                
                employee_timesheets[emp_id]['entries'].append({
                    'hours': hours,
                    'task': task_name,
                    'date': ts.get('date', '')
                })
        
        # Create final result with employee details and timesheet data
        result = []
        for employee in employees_data:
            emp_id = employee['id']
            timesheet_data = employee_timesheets.get(emp_id, {'total_hours': 0, 'entries': []})
            
            # Get tags from cached categories
            tags = []
            if employee.get('category_ids'):
                tags = [categories_dict.get(cat_id) for cat_id in employee['category_ids'] if categories_dict.get(cat_id)]
            
            # Format hours data for display
            total_hours_raw = timesheet_data['total_hours']
            unbilled_hours_raw = timesheet_data.get('unbilled_hours', 0)
            
            # Format timesheet entries with proper time formatting
            formatted_entries = []
            for entry in timesheet_data['entries']:
                formatted_entries.append({
                    'hours': decimal_hours_to_hm_data(entry['hours']),
                    'task': entry['task'],
                    'date': entry['date']
                })
            
            result.append({
                'id': emp_id,
                'name': employee.get('name', ''),
                'job_title': employee.get('job_title', ''),
                'tags': tags,
                'total_hours': decimal_hours_to_hm_data(total_hours_raw),
                'unbilled_hours': decimal_hours_to_hm_data(unbilled_hours_raw),
                'timesheet_entries': formatted_entries,
                'period_start': start_date.isoformat(),
                'period_end': end_date.isoformat()
            })
        
        # Sort by total hours (descending)
        result.sort(key=lambda x: x['total_hours']['decimal'], reverse=True)
        
        print(f"Processed timesheet data for {len(result)} Creative Strategy employees")
        return result
        
    except Exception as e:
        print(f"Error fetching Creative Strategy timesheet data: {e}")
        return []

def get_available_creative_strategy_resources(view_type='monthly', period=None):
    """
    Fetch available Creative Strategy resources from Planning > Resources with accurate utilization
    
    Args:
        view_type (str): 'monthly' or 'weekly'
        period (str): For monthly: 'YYYY-MM' format (e.g., '2025-01')
                     For weekly: 'YYYY-WW' format (e.g., '2025-01' for week 1)
    """
    try:
        models, uid = connect_to_odoo()
        
        if not models or not uid:
            return []
        
        # Get date range for the selected view type and period
        start_date, end_date = get_date_range(view_type, period)
        print(f"Analyzing Creative Strategy utilization for {view_type} view: {start_date} to {end_date}")
        
        # Fetch public holidays for the period (needed for available hours calculation)
        public_holidays = get_public_holidays(models, uid, start_date, end_date)
        total_holiday_hours = calculate_holiday_hours_in_period(public_holidays, start_date, end_date, view_type)
        
        # Find the Creative Strategy department
        department_ids = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'hr.department', 'search', 
                                         [[('name', '=', 'Creative Strategy')]])
        
        if not department_ids:
            print("No Creative Strategy department found for resources")
            return []
        
        print(f"Creative Strategy department IDs: {department_ids}")
        
        # Get all Creative Strategy department employees
        creative_strategy_employee_ids = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'hr.employee', 'search', 
                                                        [[('department_id', 'in', department_ids)]])
        
        print(f"Found {len(creative_strategy_employee_ids)} Creative Strategy employees")
        
        if not creative_strategy_employee_ids:
            print("No Creative Strategy employees found")
            return []
        
        # Get employee details for all Creative Strategy employees
        employees_data = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'hr.employee', 'read', 
                                         [creative_strategy_employee_ids], {
                                             'fields': ['name', 'job_title', 'category_ids']
                                         })
        
        print(f"Processing {len(employees_data)} Creative Strategy employees...")
        
        # Batch fetch all unique category IDs for tags
        all_category_ids = set()
        for emp in employees_data:
            if emp.get('category_ids'):
                all_category_ids.update(emp['category_ids'])
        
        # Fetch all categories in one batch call
        categories_dict = {}
        if all_category_ids:
            try:
                categories = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'hr.employee.category', 'read', 
                                             [list(all_category_ids)], {'fields': ['name']})
                categories_dict = {cat['id']: cat['name'] for cat in categories if cat.get('name')}
                print(f"Fetched {len(categories_dict)} unique categories for Creative Strategy available resources")
            except Exception as e:
                print(f"Error fetching categories for Creative Strategy available resources: {e}")
        
        # Create a dictionary to store employee availability data
        employee_availability = {}
        
        # Calculate base available hours based on actual working days in the period
        working_days, base_available_hours = calculate_working_days_and_hours(start_date, end_date)
        print(f"Base available hours for Creative Strategy {view_type} view ({start_date} to {end_date}): {base_available_hours} hours ({working_days} working days)")
        
        # Initialize all employees with 0% allocation
        for employee in employees_data:
            # Get tags from cached categories
            tags = []
            if employee.get('category_ids'):
                tags = [categories_dict.get(cat_id) for cat_id in employee['category_ids'] if categories_dict.get(cat_id)]
            
            employee_availability[employee['id']] = {
                'name': employee.get('name', ''),
                'job_title': employee.get('job_title', ''),
                'tags': tags,
                'allocated_percentage': 0,
                'planned_hours': 0,
                'base_available_hours': base_available_hours,
                'time_off_hours': 0,
                'available_hours': base_available_hours,  # Will be updated after time off and holidays
                'start_datetime': start_date,
                'end_datetime': end_date
            }
        
        # Calculate Time Off hours for each employee
        print("Calculating Time Off hours for Creative Strategy...")
        
        # Convert dates to string format for Odoo timesheet queries
        start_str = start_date.strftime('%Y-%m-%d')
        end_str = end_date.strftime('%Y-%m-%d')
        
        # Fetch timesheet entries for Time Off tasks
        time_off_timesheets = models.execute_kw(
            ODOO_DB, uid, ODOO_PASSWORD,
            'account.analytic.line', 'search_read',
            [[('employee_id', 'in', creative_strategy_employee_ids),
              ('date', '>=', start_str),
              ('date', '<=', end_str),
              ('task_id.name', '=', 'Time Off')]],
            {'fields': ['employee_id', 'unit_amount']}
        )
        
        print(f"Found {len(time_off_timesheets)} Time Off timesheet entries for Creative Strategy")
        
        # Calculate total Time Off hours per employee
        employee_time_off = {}
        for ts in time_off_timesheets:
            emp_field = ts.get('employee_id')
            if emp_field:
                emp_id = emp_field[0] if isinstance(emp_field, list) else emp_field
                if emp_id not in employee_time_off:
                    employee_time_off[emp_id] = 0
                employee_time_off[emp_id] += float(ts.get('unit_amount', 0))
        
        # Fetch employee company and calculate individual holiday hours
        employee_company = {}
        try:
            emp_company_data = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'hr.employee', 'read', 
                                              [creative_strategy_employee_ids], {'fields': ['company_id']})
            for emp in emp_company_data:
                cid = None
                if emp.get('company_id'):
                    cid = emp['company_id'][0] if isinstance(emp['company_id'], (list, tuple)) else emp['company_id']
                employee_company[emp['id']] = cid
        except Exception as _e:
            pass

        company_holidays_cache = {}
        company_holiday_hours_cache = {}
        employee_holiday_hours = {}
        for emp_id in creative_strategy_employee_ids:
            cid = employee_company.get(emp_id)
            if cid not in company_holidays_cache:
                holidays = get_public_holidays(models, uid, start_date, end_date, company_id=cid)
                company_holidays_cache[cid] = holidays
                # Calculate holiday hours once per company and cache it
                company_holiday_hours_cache[cid] = calculate_holiday_hours_in_period(holidays, start_date, end_date, view_type)
            employee_holiday_hours[emp_id] = company_holiday_hours_cache[cid]

        # Update employee availability with Time Off hours and per-employee public holidays
        for emp_id, time_off_hours in employee_time_off.items():
            if emp_id in employee_availability:
                employee_availability[emp_id]['time_off_hours'] = time_off_hours
                emp_holiday = float(employee_holiday_hours.get(emp_id) or 0.0)
                employee_availability[emp_id]['available_hours'] = base_available_hours - time_off_hours - emp_holiday
                print(f"Creative Strategy employee {employee_availability[emp_id]['name']}: {time_off_hours:.1f}h Time Off, {emp_holiday:.1f}h Public Holidays, {employee_availability[emp_id]['available_hours']:.1f}h available")
        
        # For employees without time off, still deduct public holidays
        for emp_id in employee_availability:
            if emp_id not in employee_time_off:
                emp_holiday = float(employee_holiday_hours.get(emp_id) or 0.0)
                employee_availability[emp_id]['available_hours'] = base_available_hours - emp_holiday
                print(f"Creative Strategy employee {employee_availability[emp_id]['name']}: 0h Time Off, {emp_holiday:.1f}h Public Holidays, {employee_availability[emp_id]['available_hours']:.1f}h available")
        
        # Get planning slots for Creative Strategy employees using resource_id field
        # Convert dates to string format for Odoo
        start_str = start_date.strftime('%Y-%m-%d 00:00:00')
        end_str = end_date.strftime('%Y-%m-%d 23:59:59')
        
        print(f"Searching Creative Strategy planning slots from {start_str} to {end_str}")
        print(f"Looking for planning slots for {len(creative_strategy_employee_ids)} Creative Strategy employees")
        
        # First, let's check if there are any planning slots at all for these employees
        all_slots = execute_odoo_call_with_retry(models, uid, 'planning.slot', 'search', 
                                       [[('resource_id', 'in', creative_strategy_employee_ids)]], 
                                       {'limit': 1000})
        print(f"Total planning slots found for Creative Strategy employees (any date): {len(all_slots)}")
        
        if all_slots:
            # Get a sample of slots to see what dates they have
            sample_slots = execute_odoo_call_with_retry(models, uid, 'planning.slot', 'read', 
                                          [all_slots[:5]], {
                                              'fields': [
                                                  'resource_id',
                                                  'start_datetime',
                                                  'end_datetime',
                                                  'allocated_hours'
                                              ]
                                          })
            print(f"Sample Creative Strategy planning slots (first 5):")
            for slot in sample_slots:
                print(f"  - {slot.get('start_datetime')} to {slot.get('end_datetime')} ({slot.get('allocated_hours', 0)}h)")
        
        # Search for planning slots where resource_id is in Creative Strategy employees
        # Include slots that overlap with the filter range (start before but end within, or start within but end after)
        resource_ids = execute_odoo_call_with_retry(models, uid, 'planning.slot', 'search', 
                                       [[('resource_id', 'in', creative_strategy_employee_ids),
                                         ('start_datetime', '<=', end_str),
                                         ('end_datetime', '>=', start_str)]], 
                                       {'limit': 1000})
        
        if resource_ids:
            print(f"Found {len(resource_ids)} planning slots for Creative Strategy employees")
            
            # Get resource data with employee information
            resources = execute_odoo_call_with_retry(models, uid, 'planning.slot', 'read', 
                                        [resource_ids], {
                                            'fields': [
                                                'resource_id',
                                                'start_datetime',
                                                'end_datetime',
                                                'allocated_hours'
                                            ]
                                        })
            
            print(f"Sample Creative Strategy resource data: {resources[0] if resources else 'No resources'}")
            
            # Group resources by employee and calculate allocation based on view type
            employee_resources = {}
            for resource in resources:
                if resource.get('resource_id') and resource['resource_id'][0] in employee_availability:
                    employee_id = resource['resource_id'][0]
                    
                    if employee_id not in employee_resources:
                        employee_resources[employee_id] = {
                            'total_hours': 0,
                            'slots': []
                        }
                    
                    # Parse task start and end times
                    task_start = datetime.datetime.strptime(resource['start_datetime'], '%Y-%m-%d %H:%M:%S')
                    task_end = datetime.datetime.strptime(resource['end_datetime'], '%Y-%m-%d %H:%M:%S')
                    filter_start = datetime.datetime.strptime(start_str, '%Y-%m-%d %H:%M:%S')
                    filter_end = datetime.datetime.strptime(end_str, '%Y-%m-%d %H:%M:%S')
                    
                    # Calculate allocated hours based on view type
                    if view_type == 'daily':
                        # For daily view, calculate only the hours for this specific day
                        allocated_hours = 0
                        
                        # Check if the task overlaps with the specific day
                        if task_start.date() <= filter_start.date() <= task_end.date():
                            # Task overlaps with this day
                            if task_start.date() == task_end.date():
                                # Task is entirely within this day
                                allocated_hours = resource.get('allocated_hours', 0)
                            else:
                                # Task spans multiple days, calculate proportion for this day
                                total_task_days = (task_end.date() - task_start.date()).days + 1
                                total_task_hours = resource.get('allocated_hours', 0)
                                
                                # For daily view, assume 8 hours per day
                                if total_task_days > 1:
                                    # Distribute hours evenly across days
                                    allocated_hours = min(8, total_task_hours / total_task_days)
                                else:
                                    allocated_hours = min(8, total_task_hours)
                                
                                print(f"Creative Strategy daily view: Task spans {total_task_days} days, {total_task_hours}h total, {allocated_hours:.1f}h for this day")
                        else:
                            # Task doesn't overlap with this day
                            allocated_hours = 0
                    else:
                        # For weekly/monthly view, use existing logic
                        allocated_hours = resource.get('allocated_hours', 0)
                        
                        # Edge case: Task starts before filter but ends within filter
                        if task_start < filter_start and task_end >= filter_start:
                            # Calculate days from filter start to task end
                            days_in_filter = (task_end.date() - filter_start.date()).days + 1
                            # Calculate hours: days × 8 hours per day
                            allocated_hours = days_in_filter * 8
                            print(f"Creative Strategy edge case detected: Task {resource['start_datetime']} to {resource['end_datetime']} - {days_in_filter} days in filter = {allocated_hours} hours")
                    
                    # Add to employee's total
                    employee_resources[employee_id]['total_hours'] += allocated_hours
                    employee_resources[employee_id]['slots'].append(resource)
            
            # Update employee availability with calculated data
            for employee_id, resource_data in employee_resources.items():
                allocated_hours = resource_data['total_hours']
                available_hours = employee_availability[employee_id]['available_hours']
                
                # Calculate allocated percentage based on available hours (after time off deduction)
                allocated_percentage = min((allocated_hours / available_hours) * 100, 100) if available_hours > 0 else 0
                
                # Update the employee's allocation data
                employee_availability[employee_id]['allocated_percentage'] = allocated_percentage
                employee_availability[employee_id]['planned_hours'] = allocated_hours
                
                print(f"Creative Strategy employee {employee_availability[employee_id]['name']}: {allocated_hours:.1f}h allocated ({allocated_percentage:.1f}%) from {len(resource_data['slots'])} slots")
        else:
            print("No planning slots found for Creative Strategy for the current week - showing all employees as 100% available")
        
        # Convert to list and calculate availability
        available_resources = []
        for employee_id, data in employee_availability.items():
            # Calculate availability percentage (100% - allocated_percentage)
            availability_percentage = 100 - data['allocated_percentage']
            
            available_resources.append({
                'name': data['name'],
                'job_title': data['job_title'],
                'tags': data['tags'],
                'allocated_percentage': data['allocated_percentage'],
                'planned_hours': decimal_hours_to_hm_data(data['planned_hours']),
                'availability_percentage': availability_percentage,
                'base_available_hours': decimal_hours_to_hm_data(data['base_available_hours']),
                'time_off_hours': decimal_hours_to_hm_data(data['time_off_hours']),
                'available_hours': decimal_hours_to_hm_data(data['available_hours']),
                'start_datetime': data['start_datetime'],
                'end_datetime': data['end_datetime']
            })
        
        print(f"Found {len(available_resources)} Creative Strategy resources with accurate utilization data")
        return available_resources
        
    except Exception as e:
        print(f"Error fetching Creative Strategy available resources: {e}")
        return []

def get_instructional_design_employees():
    """Fetch employees from Instructional Design department"""
    try:
        models, uid = connect_to_odoo()
        
        if not models or not uid:
            return []
        
        # Find the "Instructional Design" department - try multiple possible names
        possible_names = [
            'Instructional Design',
            'Instructional Design Department',
            'InstructionalDesign',
            'Instructional_Design',
            'ID',
            'ID Department'
        ]
        
        department_ids = []
        found_name = None
        
        for name in possible_names:
            department_ids = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'hr.department', 'search', 
                                             [[('name', '=', name)]])
            if department_ids:
                found_name = name
                break
        
        # If exact match not found, try partial match
        if not department_ids:
            department_ids = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'hr.department', 'search', 
                                             [[('name', 'ilike', 'Instructional')]])
            if department_ids:
                # Get the department names to see what we found
                dept_details = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'hr.department', 'read', 
                                               [department_ids], {'fields': ['name']})
                found_names = [dept['name'] for dept in dept_details]
                print(f"Found departments with 'Instructional' in name: {found_names}")
                found_name = found_names[0] if found_names else None
        
        if not department_ids:
            print("No 'Instructional Design' department found with any of the possible names")
            print(f"Tried names: {possible_names}")
            return []
        
        print(f"Found Instructional Design department with ID: {department_ids}, name: '{found_name}'")
        
        # Get employees in the Instructional Design department
        employee_ids = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'hr.employee', 'search', 
                                       [[('department_id', 'in', department_ids)]])
        
        if not employee_ids:
            print("No employees found in Instructional Design department")
            return []
        
        print(f"Found {len(employee_ids)} employees in Instructional Design department")
        
        # Get employee data with categories/tags
        employees = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'hr.employee', 'read', 
                                    [employee_ids], {
                                        'fields': [
                                            'name', 
                                            'job_title', 
                                            'work_email',
                                            'category_ids'  # This is the many2many field for categories/tags
                                        ]
                                    })
        
        # Batch fetch all unique category IDs
        all_category_ids = set()
        for emp in employees:
            if emp.get('category_ids'):
                all_category_ids.update(emp['category_ids'])
        
        # Fetch all categories in one batch call
        categories_dict = {}
        if all_category_ids:
            try:
                categories = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'hr.employee.category', 'read', 
                                             [list(all_category_ids)], {'fields': ['name']})
                
                # Create a dictionary for fast lookup
                categories_dict = {cat['id']: cat['name'] for cat in categories if cat.get('name')}
                print(f"Fetched {len(categories_dict)} unique categories in batch for Instructional Design")
                
            except Exception as e:
                print(f"Error fetching categories in batch for Instructional Design: {e}")
                # Fallback to individual calls if batch fails
                print("Falling back to individual category fetching for Instructional Design...")
                for emp in employees:
                    if emp.get('category_ids'):
                        try:
                            categories = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'hr.employee.category', 'read', 
                                                         [emp['category_ids']], {'fields': ['name']})
                            categories_dict.update({cat['id']: cat['name'] for cat in categories if cat.get('name')})
                        except Exception as e2:
                            print(f"Error fetching categories for Instructional Design employee {emp.get('name')}: {e2}")
        
        # Process employees using the cached categories
        processed_employees = []
        for emp in employees:
            employee_data = {
                'name': emp.get('name', ''),
                'job_title': emp.get('job_title', ''),
                'email': emp.get('work_email', ''),
                'tags': []
            }
            
            # Get tags from cached categories
            if emp.get('category_ids'):
                tags = [categories_dict.get(cat_id) for cat_id in emp['category_ids'] if categories_dict.get(cat_id)]
                employee_data['tags'] = tags
                
                if tags:
                    print(f"Instructional Design employee {emp.get('name')} has tags: {tags}")
            
            processed_employees.append(employee_data)
        
        return processed_employees
        
    except Exception as e:
        print(f"Error fetching Instructional Design employees: {e}")
        return []

def get_instructional_design_team_utilization_data(period=None, view_type='monthly'):
    """
    Fetch team utilization data for Instructional Design department teams (KSA, UAE, and Nightshift).
    
    Args:
        period (str): Period in format based on view_type:
                     - 'monthly': 'YYYY-MM' format (e.g., '2025-01')
                     - 'weekly': 'YYYY-WW' format (e.g., '2025-01')
                     - 'daily': 'YYYY-DDD' format (e.g., '2025-001')
        view_type (str): 'monthly', 'weekly', or 'daily'
    
    Returns:
        dict: Team utilization data for Instructional Design department
    """
    try:
        models, uid = connect_to_odoo()
        
        if not models or not uid:
            return {}
        
        # Get date range for the selected period and view type
        start_date, end_date = get_date_range(view_type, period)
        print(f"Fetching Instructional Design team utilization data for {view_type} view: {start_date} to {end_date}")
        
        # Fetch public holidays for the period (needed for available hours calculation)
        public_holidays = get_public_holidays(models, uid, start_date, end_date)
        0  # TODO: Calculate holiday hours per employee properly = calculate_holiday_hours_in_period(public_holidays, start_date, end_date, view_type)
        
        # Find the Instructional Design department - try multiple possible names
        possible_names = [
            'Instructional Design',
            'Instructional Design Department',
            'InstructionalDesign',
            'Instructional_Design',
            'ID',
            'ID Department'
        ]
        
        department_ids = []
        found_name = None
        
        for name in possible_names:
            department_ids = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'hr.department', 'search', 
                                             [[('name', '=', name)]])
            if department_ids:
                found_name = name
                break
        
        # If exact match not found, try partial match
        if not department_ids:
            department_ids = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'hr.department', 'search', 
                                             [[('name', 'ilike', 'Instructional')]])
            if department_ids:
                # Get the department names to see what we found
                dept_details = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'hr.department', 'read', 
                                               [department_ids], {'fields': ['name']})
                found_names = [dept['name'] for dept in dept_details]
                print(f"Found departments with 'Instructional' in name: {found_names}")
                found_name = found_names[0] if found_names else None
        
        if not department_ids:
            print("No Instructional Design department found with any of the possible names")
            print(f"Tried names: {possible_names}")
            return {}
        
        print(f"Found Instructional Design department with ID: {department_ids}, name: '{found_name}'")
        
        # Get all Instructional Design department employees
        instructional_design_employee_ids = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'hr.employee', 'search', 
                                                           [[('department_id', 'in', department_ids)]])
        
        if not instructional_design_employee_ids:
            print("No Instructional Design employees found")
            return {}
        
        # Get employee details with tags
        employees_data = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'hr.employee', 'read', 
                                         [instructional_design_employee_ids], {
                                             'fields': ['id', 'name', 'job_title', 'category_ids']
                                         })
        
        # Batch fetch all unique category IDs for tags
        all_category_ids = set()
        for emp in employees_data:
            if emp.get('category_ids'):
                all_category_ids.update(emp['category_ids'])
        
        # Fetch all categories in one batch call
        categories_dict = {}
        if all_category_ids:
            try:
                categories = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'hr.employee.category', 'read', 
                                             [list(all_category_ids)], {'fields': ['name']})
                categories_dict = {cat['id']: cat['name'] for cat in categories if cat.get('name')}
                print(f"Fetched {len(categories_dict)} unique categories for Instructional Design team utilization data")
            except Exception as e:
                print(f"Error fetching categories for Instructional Design team utilization data: {e}")
        
        # Convert dates to string format for Odoo
        start_str = start_date.strftime('%Y-%m-%d')
        end_str = end_date.strftime('%Y-%m-%d')
        
        print(f"Searching Instructional Design timesheets from {start_str} to {end_str}")
        
        # Fetch timesheet data for Instructional Design employees
        timesheets = models.execute_kw(
            ODOO_DB, uid, ODOO_PASSWORD,
            'account.analytic.line', 'search_read',
            [[('employee_id', 'in', instructional_design_employee_ids),
              ('date', '>=', start_str),
              ('date', '<=', end_str),
              ('task_id.name', '!=', 'Time Off')]],  # Exclude "Time Off" tasks
            {'fields': ['employee_id', 'unit_amount', 'task_id', 'date']}
        )
        
        print(f"Found {len(timesheets)} timesheet entries for Instructional Design employees")
        
        # Calculate total hours per employee
        employee_hours = {}
        for ts in timesheets:
            emp_field = ts.get('employee_id')
            if emp_field:
                emp_id = emp_field[0] if isinstance(emp_field, list) else emp_field
                if emp_id not in employee_hours:
                    employee_hours[emp_id] = 0
                employee_hours[emp_id] += float(ts.get('unit_amount', 0))
        
        # Fetch time off data for Instructional Design employees
        time_off_timesheets = models.execute_kw(
            ODOO_DB, uid, ODOO_PASSWORD,
            'account.analytic.line', 'search_read',
            [[('employee_id', 'in', instructional_design_employee_ids),
              ('date', '>=', start_str),
              ('date', '<=', end_str),
              ('task_id.name', '=', 'Time Off')]],
            {'fields': ['employee_id', 'unit_amount']}
        )
        
        # Calculate time off hours per employee
        employee_time_off = {}
        for ts in time_off_timesheets:
            emp_field = ts.get('employee_id')
            if emp_field:
                emp_id = emp_field[0] if isinstance(emp_field, list) else emp_field
                if emp_id not in employee_time_off:
                    employee_time_off[emp_id] = 0
                employee_time_off[emp_id] += float(ts.get('unit_amount', 0))
        
        # Create employee data structure
        employee_data = {}
        for emp in employees_data:
            emp_id = emp['id']
            tags = []
            if emp.get('category_ids'):
                tags = [categories_dict.get(cat_id) for cat_id in emp['category_ids'] if categories_dict.get(cat_id)]
            
            employee_data[emp_id] = {
                'name': emp.get('name', ''),
                'job_title': emp.get('job_title', ''),
                'tags': tags,
                'logged_hours': employee_hours.get(emp_id, 0),
                'time_off_hours': employee_time_off.get(emp_id, 0),
                'planned_hours': 0  # Will be populated from available resources
            }
        
        # Get planned hours from available resources
        available_resources = get_available_instructional_design_resources(view_type, period)
        resource_planned_hours = {}
        for resource in available_resources:
            try:
                planned_val = resource.get('planned_hours', 0)
                if isinstance(planned_val, dict):
                    planned_val = float(planned_val.get('decimal') or 0)
                else:
                    planned_val = float(planned_val or 0)
                name_key = resource.get('name')
                if name_key:
                    resource_planned_hours[name_key] = planned_val
            except Exception:
                continue
        
        for emp_id, emp_data in employee_data.items():
            emp_name = emp_data['name']
            if emp_name in resource_planned_hours:
                emp_data['planned_hours'] = float(resource_planned_hours[emp_name])
                print(f"Instructional Design employee {emp_name}: {emp_data['planned_hours']:.1f}h planned (from available resources)")
            else:
                print(f"Instructional Design employee {emp_name}: No planned hours found in available resources")
        
        # Define teams based on tags (KSA, UAE, Nightshift)
        teams = {
            'KSA': ['KSA', 'ksa'],
            'UAE': ['UAE', 'uae'],
            'Nightshift': ['Nightshift', 'nightshift', 'Night Shift', 'night shift']
        }
        
        team_stats = {}
        
        # Process each team
        for team_name, team_tags in teams.items():
            team_employees = []
            
            # Find employees belonging to this team
            for emp_id, emp_data in employee_data.items():
                if any(tag in emp_data['tags'] for tag in team_tags):
                    team_employees.append(emp_data)
            
            # Calculate team statistics
            total_creatives = len(team_employees)
            active_creatives = len([emp for emp in team_employees if emp['logged_hours'] > 0])
            
            # Calculate available hours using the same formula as Available Creatives tab
            # Base Available Hours based on view type - Time Off Hours
            if view_type == 'monthly':
                base_available_hours_per_employee = 184  # 184 hours per month
            elif view_type == 'weekly':
                base_available_hours_per_employee = 40   # 40 hours per week
            else:  # daily
                base_available_hours_per_employee = 8    # 8 hours per day
            
            base_available_hours = base_available_hours_per_employee * total_creatives  # Base hours for all employees
            total_time_off_hours = sum(emp.get('time_off_hours', 0) for emp in team_employees)
            total_holiday_hours_for_team = 0  # TODO: Calculate holiday hours per employee properly * total_creatives  # Public holidays affect all employees
            available_hours = base_available_hours - total_time_off_hours - total_holiday_hours_for_team
            
            planned_hours = sum(emp['planned_hours'] for emp in team_employees)  # Include all employees, not just active ones
            logged_hours = sum(emp['logged_hours'] for emp in team_employees if emp['logged_hours'] > 0)
            
            # Calculate utilization rate
            utilization_rate = (logged_hours / available_hours * 100) if available_hours > 0 else 0
            
            # Calculate variance
            variance = ((logged_hours - planned_hours) / planned_hours * 100) if planned_hours > 0 else 0
            
            team_stats[team_name] = {
                'total_creatives': total_creatives,
                'active_creatives': active_creatives,
                'available_hours': available_hours,
                'planned_hours': planned_hours,
                'logged_hours': logged_hours,
                'utilization_rate': utilization_rate,
                'variance': variance,
                'employees': team_employees
            }
        
        print(f"Processed Instructional Design team utilization data for {len(teams)} teams")
        return team_stats
        
    except Exception as e:
        print(f"Error fetching Instructional Design team utilization data: {e}")
        return {}

def get_instructional_design_timesheet_data(period=None, view_type='monthly'):
    """
    Fetch timesheet data for Instructional Design employees for a specific period.
    
    Args:
        period (str): Period in format based on view_type:
                     - 'monthly': 'YYYY-MM' format (e.g., '2025-01')
                     - 'weekly': 'YYYY-WW' format (e.g., '2025-01')
                     - 'daily': 'YYYY-DDD' format (e.g., '2025-001')
        view_type (str): 'monthly', 'weekly', or 'daily'
    
    Returns:
        list: List of Instructional Design employees with their timesheet hours
    """
    try:
        models, uid = connect_to_odoo()
        
        if not models or not uid:
            return []
        
        # Get date range for the selected period and view type
        start_date, end_date = get_date_range(view_type, period)
        print(f"Fetching Instructional Design timesheet data for {view_type} view: {start_date} to {end_date}")
        
        # Find the Instructional Design department - try multiple possible names
        possible_names = [
            'Instructional Design',
            'Instructional Design Department',
            'InstructionalDesign',
            'Instructional_Design',
            'ID',
            'ID Department'
        ]
        
        department_ids = []
        found_name = None
        
        for name in possible_names:
            department_ids = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'hr.department', 'search', 
                                             [[('name', '=', name)]])
            if department_ids:
                found_name = name
                break
        
        # If exact match not found, try partial match
        if not department_ids:
            department_ids = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'hr.department', 'search', 
                                             [[('name', 'ilike', 'Instructional')]])
            if department_ids:
                # Get the department names to see what we found
                dept_details = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'hr.department', 'read', 
                                               [department_ids], {'fields': ['name']})
                found_names = [dept['name'] for dept in dept_details]
                print(f"Found departments with 'Instructional' in name: {found_names}")
                found_name = found_names[0] if found_names else None
        
        if not department_ids:
            print("No Instructional Design department found with any of the possible names")
            print(f"Tried names: {possible_names}")
            return []
        
        print(f"Found Instructional Design department with ID: {department_ids}, name: '{found_name}'")
        
        # Get all Instructional Design department employees
        instructional_design_employee_ids = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'hr.employee', 'search', 
                                                           [[('department_id', 'in', department_ids)]])
        
        if not instructional_design_employee_ids:
            print("No Instructional Design employees found")
            return []
        
        # Get employee details with tags
        employees_data = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'hr.employee', 'read', 
                                         [instructional_design_employee_ids], {
                                             'fields': ['id', 'name', 'job_title', 'category_ids']
                                         })
        
        # Batch fetch all unique category IDs for tags
        all_category_ids = set()
        for emp in employees_data:
            if emp.get('category_ids'):
                all_category_ids.update(emp['category_ids'])
        
        # Fetch all categories in one batch call
        categories_dict = {}
        if all_category_ids:
            try:
                categories = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'hr.employee.category', 'read', 
                                             [list(all_category_ids)], {'fields': ['name']})
                categories_dict = {cat['id']: cat['name'] for cat in categories if cat.get('name')}
                print(f"Fetched {len(categories_dict)} unique categories for Instructional Design timesheet data")
            except Exception as e:
                print(f"Error fetching categories for Instructional Design timesheet data: {e}")
        
        # Convert dates to string format for Odoo
        start_str = start_date.strftime('%Y-%m-%d')
        end_str = end_date.strftime('%Y-%m-%d')
        
        print(f"Searching Instructional Design timesheets from {start_str} to {end_str}")
        
        # Fetch timesheet data for Instructional Design employees
        timesheets = models.execute_kw(
            ODOO_DB, uid, ODOO_PASSWORD,
            'account.analytic.line', 'search_read',
            [[('employee_id', 'in', instructional_design_employee_ids),
              ('date', '>=', start_str),
              ('date', '<=', end_str),
              ('task_id.name', '!=', 'Time Off')]],  # Exclude "Time Off" tasks
            {'fields': ['employee_id', 'unit_amount', 'task_id', 'date']}
        )
        
        print(f"Found {len(timesheets)} timesheet entries for Instructional Design employees")
        
        # Calculate total hours per employee
        employee_hours = {}
        employee_timesheet_entries = {}
        
        for ts in timesheets:
            emp_field = ts.get('employee_id')
            if emp_field:
                emp_id = emp_field[0] if isinstance(emp_field, list) else emp_field
                if emp_id not in employee_hours:
                    employee_hours[emp_id] = 0
                    employee_timesheet_entries[emp_id] = []
                
                employee_hours[emp_id] += float(ts.get('unit_amount', 0))
                
                # Store timesheet entry details
                entry = {
                    'task': ts.get('task_id', [None, ''])[1] if ts.get('task_id') else 'No Task',
                    'hours': float(ts.get('unit_amount', 0)),
                    'date': ts.get('date', 'No Date')
                }
                employee_timesheet_entries[emp_id].append(entry)
        
        # Process employees and add timesheet data
        processed_employees = []
        for emp in employees_data:
            emp_id = emp['id']
            tags = []
            if emp.get('category_ids'):
                tags = [categories_dict.get(cat_id) for cat_id in emp['category_ids'] if categories_dict.get(cat_id)]
            
            # Format hours data for display
            total_hours_raw = employee_hours.get(emp_id, 0)
            raw_entries = employee_timesheet_entries.get(emp_id, [])
            
            # Format timesheet entries with proper time formatting
            formatted_entries = []
            for entry in raw_entries:
                formatted_entries.append({
                    'hours': decimal_hours_to_hm_data(entry['hours']),
                    'task': entry['task'],
                    'date': entry['date']
                })
            
            employee_data = {
                'name': emp.get('name', ''),
                'job_title': emp.get('job_title', ''),
                'tags': tags,
                'total_hours': decimal_hours_to_hm_data(total_hours_raw),
                'timesheet_entries': formatted_entries
            }
            
            processed_employees.append(employee_data)
        
        return processed_employees
        
    except Exception as e:
        print(f"Error fetching Instructional Design timesheet data: {e}")
        return []

def get_available_instructional_design_resources(view_type='monthly', period=None):
    """
    Fetch available resources data for Instructional Design department employees.
    
    Args:
        view_type (str): 'monthly', 'weekly', or 'daily'
        period (str): Period in format based on view_type:
                     - 'monthly': 'YYYY-MM' format (e.g., '2025-01')
                     - 'weekly': 'YYYY-WW' format (e.g., '2025-01')
                     - 'daily': 'YYYY-DDD' format (e.g., '2025-001')
    
    Returns:
        list: List of Instructional Design employees with their availability data
    """
    try:
        models, uid = connect_to_odoo()
        
        if not models or not uid:
            return []
        
        # Get date range for the selected period and view type
        start_date, end_date = get_date_range(view_type, period)
        print(f"Fetching Instructional Design available resources for {view_type} view: {start_date} to {end_date}")
        
        # Fetch public holidays for the period (needed for available hours calculation)
        public_holidays = get_public_holidays(models, uid, start_date, end_date)
        total_holiday_hours = calculate_holiday_hours_in_period(public_holidays, start_date, end_date, view_type)
        
        # Find the Instructional Design department - try multiple possible names
        possible_names = [
            'Instructional Design',
            'Instructional Design Department',
            'InstructionalDesign',
            'Instructional_Design',
            'ID',
            'ID Department'
        ]
        
        department_ids = []
        found_name = None
        
        for name in possible_names:
            department_ids = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'hr.department', 'search', 
                                             [[('name', '=', name)]])
            if department_ids:
                found_name = name
                break
        
        # If exact match not found, try partial match
        if not department_ids:
            department_ids = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'hr.department', 'search', 
                                             [[('name', 'ilike', 'Instructional')]])
            if department_ids:
                # Get the department names to see what we found
                dept_details = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'hr.department', 'read', 
                                               [department_ids], {'fields': ['name']})
                found_names = [dept['name'] for dept in dept_details]
                print(f"Found departments with 'Instructional' in name: {found_names}")
                found_name = found_names[0] if found_names else None
        
        if not department_ids:
            print("No Instructional Design department found with any of the possible names")
            print(f"Tried names: {possible_names}")
            return []
        
        print(f"Found Instructional Design department with ID: {department_ids}, name: '{found_name}'")
        
        # Get all Instructional Design department employees
        instructional_design_employee_ids = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'hr.employee', 'search', 
                                                           [[('department_id', 'in', department_ids)]])
        
        if not instructional_design_employee_ids:
            print("No Instructional Design employees found")
            return []
        
        # Get employee details with tags
        employees_data = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'hr.employee', 'read', 
                                         [instructional_design_employee_ids], {
                                             'fields': [
                                                 'id', 
                                                 'name', 
                                                 'job_title', 
                                                 'category_ids'
                                             ]
                                         })
        
        # Batch fetch all unique category IDs for tags
        all_category_ids = set()
        for emp in employees_data:
            if emp.get('category_ids'):
                all_category_ids.update(emp['category_ids'])
        
        # Fetch all categories in one batch call
        categories_dict = {}
        if all_category_ids:
            try:
                categories = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'hr.employee.category', 'read', 
                                             [list(all_category_ids)], {'fields': ['name']})
                categories_dict = {cat['id']: cat['name'] for cat in categories if cat.get('name')}
                print(f"Fetched {len(categories_dict)} unique categories for Instructional Design available resources")
            except Exception as e:
                print(f"Error fetching categories for Instructional Design available resources: {e}")
        
        # Create a dictionary to store employee availability data
        employee_availability = {}
        
        # Calculate base available hours based on actual working days in the period
        working_days, base_available_hours = calculate_working_days_and_hours(start_date, end_date)
        print(f"Base available hours for Instructional Design {view_type} view ({start_date} to {end_date}): {base_available_hours} hours ({working_days} working days)")
        
        # Initialize all employees with 0% allocation
        for employee in employees_data:
            # Get tags from cached categories
            tags = []
            if employee.get('category_ids'):
                tags = [categories_dict.get(cat_id) for cat_id in employee['category_ids'] if categories_dict.get(cat_id)]
            
            employee_availability[employee['id']] = {
                'name': employee.get('name', ''),
                'job_title': employee.get('job_title', ''),
                'tags': tags,
                'allocated_percentage': 0,
                'planned_hours': 0,
                'base_available_hours': base_available_hours,
                'time_off_hours': 0,
                'available_hours': base_available_hours,  # Will be updated after time off calculation
                'start_datetime': start_date,
                'end_datetime': end_date
            }
        
        # Calculate Time Off hours for each employee
        print("Calculating Time Off hours for Instructional Design...")
        
        # Convert dates to string format for Odoo timesheet queries
        start_str = start_date.strftime('%Y-%m-%d')
        end_str = end_date.strftime('%Y-%m-%d')
        
        # Fetch timesheet entries for Time Off tasks
        time_off_timesheets = models.execute_kw(
            ODOO_DB, uid, ODOO_PASSWORD,
            'account.analytic.line', 'search_read',
            [[('employee_id', 'in', instructional_design_employee_ids),
              ('date', '>=', start_str),
              ('date', '<=', end_str),
              ('task_id.name', '=', 'Time Off')]],
            {'fields': ['employee_id', 'unit_amount']}
        )
        
        print(f"Found {len(time_off_timesheets)} Time Off timesheet entries for Instructional Design")
        
        # Calculate total Time Off hours per employee
        employee_time_off = {}
        for ts in time_off_timesheets:
            emp_field = ts.get('employee_id')
            if emp_field:
                emp_id = emp_field[0] if isinstance(emp_field, list) else emp_field
                if emp_id not in employee_time_off:
                    employee_time_off[emp_id] = 0
                employee_time_off[emp_id] += float(ts.get('unit_amount', 0))
        
        # Update employee availability with Time Off hours and per-employee public holidays
        for emp_id, time_off_hours in employee_time_off.items():
            if emp_id in employee_availability:
                employee_availability[emp_id]['time_off_hours'] = time_off_hours
                # Deduct both time off and per-employee public holidays from available hours
                emp_holiday = float(employee_holiday_hours.get(emp_id) or 0.0)
                employee_availability[emp_id]['available_hours'] = base_available_hours - time_off_hours - emp_holiday
                print(f"Instructional Design employee {employee_availability[emp_id]['name']}: {time_off_hours:.1f}h Time Off, {total_holiday_hours:.1f}h Public Holidays, {employee_availability[emp_id]['available_hours']:.1f}h available")
        
        # For employees without time off, still deduct public holidays
        for emp_id in employee_availability:
            if emp_id not in employee_time_off:
                employee_availability[emp_id]['available_hours'] = base_available_hours - total_holiday_hours
                print(f"Instructional Design employee {employee_availability[emp_id]['name']}: 0h Time Off, {total_holiday_hours:.1f}h Public Holidays, {employee_availability[emp_id]['available_hours']:.1f}h available")
        
        # Get planning slots for Instructional Design employees using resource_id field
        # Convert dates to string format for Odoo
        start_str = start_date.strftime('%Y-%m-%d 00:00:00')
        end_str = end_date.strftime('%Y-%m-%d 23:59:59')
        
        print(f"Searching planning slots for Instructional Design from {start_str} to {end_str}")
        print(f"Looking for planning slots for {len(instructional_design_employee_ids)} Instructional Design employees")
        
        # First, let's check if there are any planning slots at all for these employees
        all_slots = execute_odoo_call_with_retry(models, uid, 'planning.slot', 'search', 
                                       [[('resource_id', 'in', instructional_design_employee_ids)]], 
                                       {'limit': 1000})
        print(f"Total planning slots found for Instructional Design employees (any date): {len(all_slots)}")
        
        if all_slots:
            # Fetch planning slots for the specific date range
            planning_slots = execute_odoo_call_with_retry(models, uid, 'planning.slot', 'search_read',
                                               [[('resource_id', 'in', instructional_design_employee_ids),
                                                 ('start_datetime', '>=', start_str),
                                                 ('end_datetime', '<=', end_str)]],
                                               {'fields': ['resource_id', 'allocated_hours', 'allocated_percentage']})
            
            print(f"Found {len(planning_slots)} planning slots for Instructional Design in date range")
            
            # Calculate allocation per employee
            for slot in planning_slots:
                resource_field = slot.get('resource_id')
                if resource_field:
                    emp_id = resource_field[0] if isinstance(resource_field, list) else resource_field
                    if emp_id in employee_availability:
                        allocated_hours = float(slot.get('allocated_hours', 0))
                        allocated_percentage = float(slot.get('allocated_percentage', 0))
                        
                        employee_availability[emp_id]['planned_hours'] += allocated_hours
                        employee_availability[emp_id]['allocated_percentage'] += allocated_percentage
                        
                        print(f"Instructional Design employee {employee_availability[emp_id]['name']}: +{allocated_hours:.1f}h planned, +{allocated_percentage:.1f}% allocated")
        else:
            print("No planning slots found for Instructional Design employees")
        
        # Convert to list format for frontend
        available_resources = []
        for emp_id, emp_data in employee_availability.items():
            available_resources.append({
                'id': emp_id,
                'name': emp_data['name'],
                'job_title': emp_data['job_title'],
                'tags': emp_data['tags'],
                'allocated_percentage': emp_data['allocated_percentage'],
                'planned_hours': decimal_hours_to_hm_data(emp_data['planned_hours']),
                'base_available_hours': decimal_hours_to_hm_data(emp_data['base_available_hours']),
                'time_off_hours': decimal_hours_to_hm_data(emp_data['time_off_hours']),
                'available_hours': decimal_hours_to_hm_data(emp_data['available_hours']),
                'period_start': start_date.strftime('%Y-%m-%d'),
                'period_end': end_date.strftime('%Y-%m-%d')
            })
        
        print(f"Processed {len(available_resources)} Instructional Design available resources")
        return available_resources
        
    except Exception as e:
        print(f"Error fetching Instructional Design available resources: {e}")
        return []

@app.route('/api/creative-employees', methods=['GET'])
def creative_employees():
    """API endpoint to get creative department employees"""
    employees = get_creative_employees()
    return jsonify({
        'success': True,
        'employees': employees,
        'count': len(employees)
    })

@app.route('/api/team-utilization-data', methods=['GET'])
def team_utilization_data():
    """API endpoint to get team utilization data"""
    from flask import request
    
    # Get query parameters
    view_type = request.args.get('view_type', 'monthly')  # Default to monthly
    period = request.args.get('period', '2025-01')  # Default to January 2025
    
    # Validate view_type
    if view_type not in ['monthly', 'weekly', 'daily']:
        view_type = 'monthly'
    
    team_data = get_team_utilization_data(period, view_type)
    
    # Get the actual date range used for this request
    actual_start, actual_end = get_date_range(view_type, period)
    
    return jsonify({
        'success': True,
        'team_data': team_data,
        'view_type': view_type,
        'selected_period': period,
        'start_date': actual_start.isoformat(),
        'end_date': actual_end.isoformat()
    })

@app.route('/api/shareholders', methods=['GET', 'POST', 'DELETE'])
def shareholders_api():
    """Manage shareholder emails (list/add/remove)."""
    try:
        if request.method == 'GET':
            return jsonify({'success': True, 'shareholders': load_shareholders()})
        data = request.get_json(force=True, silent=True) or {}
        email = (data.get('email') or '').strip()
        if not email:
            return jsonify({'success': False, 'error': 'Email is required'}), 400
        if request.method == 'POST':
            ok = add_shareholder_email(email)
            return jsonify({'success': ok, 'shareholders': load_shareholders()})
        if request.method == 'DELETE':
            ok = remove_shareholder_email(email)
            return jsonify({'success': ok, 'shareholders': load_shareholders()})
        return jsonify({'success': False, 'error': 'Unsupported method'}), 405
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/shareholders/preview-weekly', methods=['GET'])
def shareholders_preview_weekly():
    """Return HTML preview for last week's utilization email (or provided period)."""
    try:
        view_type = 'weekly'
        period = request.args.get('period') or _get_last_week_period()
        start_date, end_date = get_date_range(view_type, period)
        team_data = get_team_utilization_data(period, view_type)
        external_hours = get_sales_order_hours_data(period, view_type)
        html = build_weekly_utilization_email_html(period, team_data, start_date, end_date, external_hours_data=external_hours)
        return jsonify({'success': True, 'period': period, 'start_date': start_date.isoformat(), 'end_date': end_date.isoformat(), 'html': html})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/shareholders/send-test', methods=['POST'])
def shareholders_send_test():
    """Send a test weekly utilization email to a specified address (last week)."""
    try:
        data = request.get_json(force=True, silent=True) or {}
        to_email = (data.get('email') or '').strip()
        if not to_email:
            return jsonify({'success': False, 'error': 'Email is required'}), 400
        period = _get_last_week_period()
        start_date, end_date = get_date_range('weekly', period)
        team_data = get_team_utilization_data(period, 'weekly')
        external_hours = get_sales_order_hours_data(period, 'weekly')
        html = build_weekly_utilization_email_html(period, team_data, start_date, end_date, external_hours_data=external_hours)
        subject = f"[Weekly Utilization] Week {period} ({start_date.strftime('%b %d')}–{end_date.strftime('%b %d, %Y')})"
        ok = send_html_email_via_smtp(to_email, subject, html)
        return jsonify({'success': ok})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/shareholders/send-weekly', methods=['POST'])
def shareholders_send_weekly():
    """Send last week's utilization summary to all stored shareholders."""
    try:
        recipients = load_shareholders()
        if not recipients:
            return jsonify({'success': False, 'error': 'No shareholders found'}), 400
        period = _get_last_week_period()
        start_date, end_date = get_date_range('weekly', period)
        team_data = get_team_utilization_data(period, 'weekly')
        external_hours = get_sales_order_hours_data(period, 'weekly')
        html = build_weekly_utilization_email_html(period, team_data, start_date, end_date, external_hours_data=external_hours)
        subject = f"[Weekly Utilization] Week {period} ({start_date.strftime('%b %d')}–{end_date.strftime('%b %d, %Y')})"
        results = {}
        sent_count = 0
        for addr in recipients:
            ok = send_html_email_via_smtp(addr, subject, html)
            results[addr] = ok
            if ok:
                sent_count += 1
        return jsonify({'success': True, 'sent': sent_count, 'total': len(recipients), 'results': results, 'period': period})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/shareholders/preview-monthly', methods=['GET'])
def shareholders_preview_monthly():
    """Return HTML preview for last month's comprehensive utilization email."""
    try:
        view_type = 'monthly'
        period = request.args.get('period') or _get_last_month_period()
        start_date, end_date = get_date_range(view_type, period)
        
        # Get comprehensive dashboard data
        dashboard_data = get_dashboard_data(period=period, view_type=view_type)
        
        html = build_monthly_utilization_email_html(period, dashboard_data, start_date, end_date)
        return jsonify({
            'success': True, 
            'period': period, 
            'start_date': start_date.isoformat(), 
            'end_date': end_date.isoformat(), 
            'html': html
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/shareholders/send-test-monthly', methods=['POST'])
def shareholders_send_test_monthly():
    """Send a test monthly utilization email to a specified address."""
    try:
        data = request.get_json(force=True, silent=True) or {}
        to_email = (data.get('email') or '').strip()
        if not to_email:
            return jsonify({'success': False, 'error': 'Email is required'}), 400
            
        period = data.get('period') or _get_last_month_period()
        start_date, end_date = get_date_range('monthly', period)
        
        # Get comprehensive dashboard data
        dashboard_data = get_dashboard_data(period=period, view_type='monthly')
        
        html = build_monthly_utilization_email_html(period, dashboard_data, start_date, end_date)
        subject = f"[Monthly Utilization Report] {start_date.strftime('%B %Y')}"
        
        ok = send_html_email_via_smtp(to_email, subject, html)
        return jsonify({'success': ok, 'period': period})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/shareholders/send-monthly', methods=['POST'])
def shareholders_send_monthly():
    """Send last month's comprehensive utilization report to all stored shareholders."""
    try:
        recipients = load_shareholders()
        if not recipients:
            return jsonify({'success': False, 'error': 'No shareholders found'}), 400
            
        period = _get_last_month_period()
        start_date, end_date = get_date_range('monthly', period)
        
        # Get comprehensive dashboard data
        dashboard_data = get_dashboard_data(period=period, view_type='monthly')
        
        html = build_monthly_utilization_email_html(period, dashboard_data, start_date, end_date)
        subject = f"[Monthly Utilization Report] {start_date.strftime('%B %Y')}"
        
        results = {}
        sent_count = 0
        for addr in recipients:
            ok = send_html_email_via_smtp(addr, subject, html)
            results[addr] = ok
            if ok:
                sent_count += 1
                
        return jsonify({
            'success': True, 
            'sent': sent_count, 
            'total': len(recipients), 
            'results': results, 
            'period': period
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/creative-timesheet-data', methods=['GET'])
def creative_timesheet_data():
    """API endpoint to get timesheet data for creative employees"""
    from flask import request
    
    # Get query parameters
    view_type = request.args.get('view_type', 'monthly')  # Default to monthly
    period = request.args.get('period', '2025-01')  # Default to January 2025
    
    # Validate view_type
    if view_type not in ['monthly', 'weekly', 'daily']:
        view_type = 'monthly'
    
    timesheet_data = get_creative_timesheet_data(period, view_type)
    
    # Get the actual date range used for this request
    actual_start, actual_end = get_date_range(view_type, period)
    
    return jsonify({
        'success': True,
        'timesheet_data': timesheet_data,
        'count': len(timesheet_data),
        'view_type': view_type,
        'selected_period': period,
        'start_date': actual_start.isoformat(),
        'end_date': actual_end.isoformat()
    })

@app.route('/api/available-creative-resources', methods=['GET'])
def available_creative_resources():
    """API endpoint to get available creative resources"""
    from flask import request
    
    # Get query parameters
    view_type = request.args.get('view_type', 'monthly')  # Default to monthly
    period = request.args.get('period', '2025-01')  # Default to January 2025
    
    # Validate view_type
    if view_type not in ['monthly', 'weekly', 'daily']:
        view_type = 'monthly'
    
    resources = get_available_creative_resources(view_type, period)
    
    # Get the actual date range used for this request
    actual_start, actual_end = get_date_range(view_type, period)
    
    return jsonify({
        'success': True,
        'resources': resources,
        'count': len(resources),
        'view_type': view_type,
        'selected_period': period,
        'start_date': actual_start.isoformat(),
        'end_date': actual_end.isoformat()
    })

@app.route('/api/available-periods', methods=['GET'])
def available_periods():
    """API endpoint to get available periods (months and weeks) for the dropdown"""
    from flask import request
    
    view_type = request.args.get('view_type', 'monthly')
    
    if view_type == 'monthly':
        # Generate months from January 2025 to December 2025
        periods = []
        for month in range(1, 13):
            month_date = datetime.date(2025, month, 1)
            period_name = month_date.strftime('%B %Y')  # e.g., "January 2025"
            period_value = month_date.strftime('%Y-%m')  # e.g., "2025-01"
            
            periods.append({
                'value': period_value,
                'label': period_name
            })
        
        return jsonify({
            'success': True,
            'view_type': 'monthly',
            'periods': periods,
            'default_period': '2025-01'
        })
    
    elif view_type == 'weekly':
        # Generate weeks from January 5, 2025 through the end of the year
        periods = []
        
        # Start with the first week (Jan 5-11, 2025)
        start_date = datetime.date(2025, 1, 5)
        week_number = 1
        
        while start_date.year == 2025:
            end_date = start_date + datetime.timedelta(days=6)
            
            # Format the period name and value
            period_name = f"Week {week_number} ({start_date.strftime('%b %d')} - {end_date.strftime('%b %d, %Y')})"
            period_value = f"2025-{week_number:02d}"
            
            periods.append({
                'value': period_value,
                'label': period_name
            })
            
            # Move to next week
            start_date += datetime.timedelta(days=7)
            week_number += 1
            
            # Stop if we've gone past the end of 2025
            if start_date.year > 2025:
                break
        
        return jsonify({
            'success': True,
            'view_type': 'weekly',
            'periods': periods,
            'default_period': '2025-01'
        })
    
    elif view_type == 'daily':
        # Generate days for 2025
        periods = []
        start_date = datetime.date(2025, 1, 1)
        end_date = datetime.date(2025, 12, 31)
        day_number = 1
        
        current_date = start_date
        while current_date <= end_date:
            # Format the period name and value
            period_name = current_date.strftime('%a, %b %d, %Y')  # e.g., "Mon, Jan 01, 2025"
            period_value = f"2025-{day_number:03d}"
            
            periods.append({
                'value': period_value,
                'label': period_name
            })
            
            # Move to next day
            current_date += datetime.timedelta(days=1)
            day_number += 1
        
        return jsonify({
            'success': True,
            'view_type': 'daily',
            'periods': periods,
            'default_period': '2025-001'
        })
    
    else:
        # Default to monthly
        return available_periods()

@app.route('/api/instructional-design-employees', methods=['GET'])
def instructional_design_employees():
    """API endpoint to get Instructional Design employees"""
    try:
        employees = get_instructional_design_employees()
        return jsonify({'success': True, 'employees': employees, 'count': len(employees)})
    except Exception as e:
        print(f"Error in instructional_design_employees API: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/instructional-design-team-utilization-data', methods=['GET'])
def instructional_design_team_utilization_data():
    """API endpoint to get Instructional Design team utilization data"""
    try:
        view_type = request.args.get('view_type', 'monthly')
        period = request.args.get('period', '2025-01')
        if view_type not in ['monthly', 'weekly', 'daily']:
            view_type = 'monthly'
        team_data = get_instructional_design_team_utilization_data(period, view_type)
        actual_start, actual_end = get_date_range(view_type, period)
        return jsonify({
            'success': True, 'team_data': team_data, 'count': len(team_data),
            'view_type': view_type, 'selected_period': period,
            'start_date': actual_start.isoformat(), 'end_date': actual_end.isoformat()
        })
    except Exception as e:
        print(f"Error in instructional_design_team_utilization_data API: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/instructional-design-timesheet-data', methods=['GET'])
def instructional_design_timesheet_data():
    """API endpoint to get Instructional Design timesheet data"""
    try:
        view_type = request.args.get('view_type', 'monthly')
        period = request.args.get('period', '2025-01')
        if view_type not in ['monthly', 'weekly', 'daily']:
            view_type = 'monthly'
        timesheet_data = get_instructional_design_timesheet_data(period, view_type)
        actual_start, actual_end = get_date_range(view_type, period)
        return jsonify({
            'success': True, 'timesheet_data': timesheet_data, 'count': len(timesheet_data),
            'view_type': view_type, 'selected_period': period,
            'start_date': actual_start.isoformat(), 'end_date': actual_end.isoformat()
        })
    except Exception as e:
        print(f"Error in instructional_design_timesheet_data API: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/available-instructional-design-resources', methods=['GET'])
def available_instructional_design_resources():
    """API endpoint to get Instructional Design available resources"""
    try:
        view_type = request.args.get('view_type', 'monthly')
        period = request.args.get('period', '2025-01')
        if view_type not in ['monthly', 'weekly', 'daily']:
            view_type = 'monthly'
        available_resources = get_available_instructional_design_resources(view_type, period)
        actual_start, actual_end = get_date_range(view_type, period)
        return jsonify({
            'success': True, 'available_resources': available_resources, 'count': len(available_resources),
            'view_type': view_type, 'selected_period': period,
            'start_date': actual_start.isoformat(), 'end_date': actual_end.isoformat()
        })
    except Exception as e:
        print(f"Error in available_instructional_design_resources API: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({'status': 'healthy', 'message': 'Odoo Dashboard API is running'})

@app.route('/api/creative-strategy-employees', methods=['GET'])
def creative_strategy_employees():
    """API endpoint to get Creative Strategy department employees"""
    employees = get_creative_strategy_employees()
    return jsonify({
        'success': True,
        'employees': employees,
        'count': len(employees)
    })

@app.route('/api/creative-strategy-team-utilization-data', methods=['GET'])
def creative_strategy_team_utilization_data():
    """API endpoint to get Creative Strategy team utilization data"""
    from flask import request
    
    # Get query parameters
    view_type = request.args.get('view_type', 'monthly')  # Default to monthly
    period = request.args.get('period', '2025-01')  # Default to January 2025
    
    # Validate view_type
    if view_type not in ['monthly', 'weekly', 'daily']:
        view_type = 'monthly'
    
    team_data = get_creative_strategy_team_utilization_data(period, view_type)
    
    # Get the actual date range used for this request
    actual_start, actual_end = get_date_range(view_type, period)
    
    return jsonify({
        'success': True,
        'team_data': team_data,
        'view_type': view_type,
        'selected_period': period,
        'start_date': actual_start.isoformat(),
        'end_date': actual_end.isoformat()
    })

@app.route('/api/creative-strategy-timesheet-data', methods=['GET'])
def creative_strategy_timesheet_data():
    """API endpoint to get timesheet data for Creative Strategy employees"""
    from flask import request
    
    # Get query parameters
    view_type = request.args.get('view_type', 'monthly')  # Default to monthly
    period = request.args.get('period', '2025-01')  # Default to January 2025
    
    # Validate view_type
    if view_type not in ['monthly', 'weekly', 'daily']:
        view_type = 'monthly'
    
    timesheet_data = get_creative_strategy_timesheet_data(period, view_type)
    
    # Get the actual date range used for this request
    actual_start, actual_end = get_date_range(view_type, period)
    
    return jsonify({
        'success': True,
        'timesheet_data': timesheet_data,
        'count': len(timesheet_data),
        'view_type': view_type,
        'selected_period': period,
        'start_date': actual_start.isoformat(),
        'end_date': actual_end.isoformat()
    })

@app.route('/api/available-creative-strategy-resources', methods=['GET'])
def available_creative_strategy_resources():
    """API endpoint to get available Creative Strategy resources"""
    from flask import request
    
    # Get query parameters
    view_type = request.args.get('view_type', 'monthly')  # Default to monthly
    period = request.args.get('period', '2025-01')  # Default to January 2025
    
    # Validate view_type
    if view_type not in ['monthly', 'weekly', 'daily']:
        view_type = 'monthly'
    
    resources = get_available_creative_strategy_resources(view_type, period)
    
    # Get the actual date range used for this request
    actual_start, actual_end = get_date_range(view_type, period)
    
    return jsonify({
        'success': True,
        'resources': resources,
        'count': len(resources),
        'view_type': view_type,
        'selected_period': period,
        'start_date': actual_start.isoformat(),
        'end_date': actual_end.isoformat()
    })

@app.route('/api/all-departments-data', methods=['GET'])
def all_departments_data():
    """
    Fetch data for all departments (Creative and Creative Strategy) in a single call.
    Uses caching and parallel processing to optimize performance.
    """
    try:
        period = request.args.get('period')
        view_type = request.args.get('view_type', 'monthly')  # Default to monthly if not specified
        
        print(f"=== API Request Debug ===")
        print(f"Request period: {period}")
        print(f"Request view_type: {view_type}")
        print(f"Request args: {dict(request.args)}")
        
        # Parse include list (which sections to return)
        include_param = request.args.get('include')
        if include_param:
            include = set([part.strip() for part in include_param.split(',') if part.strip()])
        else:
            # Default lean set for first paint
            include = {'employees', 'team_utilization'}

        selected_department = request.args.get('selected_department')
        valid_departments = ('Creative', 'Creative Strategy', 'Instructional Design')
        selected_department = selected_department if selected_department in valid_departments else None

        # Helper to map department names to keys and functions
        def dept_key_name(name):
            return 'creative' if name == 'Creative' else 'creative_strategy' if name == 'Creative Strategy' else 'instructional_design'

        def dept_functions(name):
            if name == 'Creative':
                return {
                    'employees': lambda: get_creative_employees(),
                    'team_utilization': lambda: get_team_utilization_data(period, view_type),
                    'timesheet_data': lambda: get_creative_timesheet_data(period, view_type),
                    'available_resources': lambda: get_available_creative_resources(view_type, period)
                }
            if name == 'Creative Strategy':
                return {
                    'employees': lambda: get_creative_strategy_employees(),
                    'team_utilization': lambda: get_creative_strategy_team_utilization_data(period, view_type),
                    'timesheet_data': lambda: get_creative_strategy_timesheet_data(period, view_type),
                    'available_resources': lambda: get_available_creative_strategy_resources(view_type, period)
                }
            # Instructional Design
            return {
                'employees': lambda: get_instructional_design_employees(),
                'team_utilization': lambda: get_instructional_design_team_utilization_data(period, view_type),
                'timesheet_data': lambda: get_instructional_design_timesheet_data(period, view_type),
                'available_resources': lambda: get_available_instructional_design_resources(view_type, period)
            }

        # If a selected_department is specified OR include is lean, build result by department using targeted functions
        if selected_department or include != {'employees', 'team_utilization'}:
            print(f"Optimized path: departments={selected_department or 'ALL (lazy)'}, include={sorted(list(include))}")
            result = {}
            departments_to_process = [selected_department] if selected_department else list(valid_departments)
            cache_only = True
            for dept_name in departments_to_process:
                key = dept_key_name(dept_name)
                cached_obj = get_cached_data(key, period, view_type) or {}
                out_obj = dict(cached_obj) if cached_obj else {}
                funcs = dept_functions(dept_name)
                for part in include:
                    existing = out_obj.get(part)
                    missing = (existing is None) or (isinstance(existing, (list, dict)) and len(existing) == 0)
                    if missing:
                        try:
                            out_obj[part] = funcs[part]()
                            cache_only = False
                        except Exception as e:
                            print(f"Error computing {part} for {dept_name}: {e}")
                # Ensure team_utilization is populated; if empty, fall back to sequential aggregator
                if 'team_utilization' in include and not bool(out_obj.get('team_utilization')):
                    try:
                        fallback = fetch_department_data_sequential(dept_name, period, view_type)
                        if fallback and bool(fallback.get('team_utilization')):
                            out_obj['team_utilization'] = fallback['team_utilization']
                            cache_only = False
                    except Exception as _e:
                        pass
                if out_obj:
                    set_cached_data(key, out_obj, period, view_type)
                    result[key] = out_obj
            result['cached'] = cache_only
            result['cache_timestamp'] = time.time()
            return jsonify(result)

        # Check cache first (full payload path)
        cached_creative = get_cached_data('creative', period, view_type)
        cached_creative_strategy = get_cached_data('creative_strategy', period, view_type)
        cached_instructional_design = get_cached_data('instructional_design', period, view_type)
        
        print(f"Cache check - Creative: {'cached' if cached_creative else 'not cached'}")
        print(f"Cache check - Creative Strategy: {'cached' if cached_creative_strategy else 'not cached'}")
        print(f"Cache check - Instructional Design: {'cached' if cached_instructional_design else 'not cached'}")
        
        # If all are cached and valid, return cached data
        if cached_creative is not None and cached_creative_strategy is not None and cached_instructional_design is not None:
            print(f"Returning cached data for period: {period}, view_type: {view_type}")
            # Get the cache timestamp for this specific request
            cache_key = f"{period}_{view_type}" if period else f"default_{view_type}"
            cache_timestamp = department_cache['cache_timestamps'].get(cache_key, time.time())
            
            # Safety: ensure core fields are populated for each department
            def _ensure_department_fields(dept_key, dept_name, data_obj):
                try:
                    if not data_obj:
                        return data_obj
                    needs_employees = len(data_obj.get('employees') or []) == 0
                    needs_resources = len(data_obj.get('available_resources') or []) == 0
                    needs_timesheets = len(data_obj.get('timesheet_data') or []) == 0
                    needs_util = not bool(data_obj.get('team_utilization'))
                    if not (needs_employees or needs_resources or needs_timesheets or needs_util):
                        return data_obj
                    fetched = fetch_department_data_sequential(dept_name, period, view_type)
                    if not fetched:
                        return data_obj
                    if needs_employees and len(fetched.get('employees') or []) > 0:
                        data_obj['employees'] = fetched['employees']
                    if needs_resources and len(fetched.get('available_resources') or []) > 0:
                        data_obj['available_resources'] = fetched['available_resources']
                    if needs_timesheets and len(fetched.get('timesheet_data') or []) > 0:
                        data_obj['timesheet_data'] = fetched['timesheet_data']
                    if needs_util and bool(fetched.get('team_utilization')):
                        data_obj['team_utilization'] = fetched['team_utilization']
                    set_cached_data(dept_key, data_obj, period, view_type)
                except Exception:
                    pass
                return data_obj

            cached_creative = _ensure_department_fields('creative', 'Creative', cached_creative)
            cached_creative_strategy = _ensure_department_fields('creative_strategy', 'Creative Strategy', cached_creative_strategy)
            cached_instructional_design = _ensure_department_fields('instructional_design', 'Instructional Design', cached_instructional_design)
            
            return jsonify({
                'creative': cached_creative,
                'creative_strategy': cached_creative_strategy,
                'instructional_design': cached_instructional_design,
                'cached': True,
                'cache_timestamp': cache_timestamp
            })
        
        # Fetch data for departments that aren't cached using parallel processing
        result = {}
        
        # Use parallel processing for departments that need fresh data
        if not ENABLE_PARALLEL_PROCESSING:
            print("Parallel processing disabled, using sequential method")
            # Fetch data sequentially
            if cached_creative is None:
                print(f"Fetching Creative department data for period: {period}")
                creative_data = fetch_department_data_sequential('Creative', period, view_type)
                if creative_data:
                    set_cached_data('creative', creative_data, period, view_type)
                    result['creative'] = creative_data
                else:
                    # Fallback to original methods
                    fallback_data = {
                        'employees': get_creative_employees(),
                        'team_utilization': get_team_utilization_data(period, view_type),
                        'timesheet_data': get_creative_timesheet_data(period, view_type),
                        'available_resources': get_available_creative_resources(view_type, period)
                    }
                    set_cached_data('creative', fallback_data, period, view_type)
                    result['creative'] = fallback_data
            
            if cached_creative_strategy is None:
                print(f"Fetching Creative Strategy department data for period: {period}")
                creative_strategy_data = fetch_department_data_sequential('Creative Strategy', period, view_type)
                if creative_strategy_data:
                    set_cached_data('creative_strategy', creative_strategy_data, period, view_type)
                    result['creative_strategy'] = creative_strategy_data
                else:
                    # Fallback to original methods
                    fallback_data = {
                        'employees': get_creative_strategy_employees(),
                        'team_utilization': get_creative_strategy_team_utilization_data(period, view_type),
                        'timesheet_data': get_creative_strategy_timesheet_data(period, view_type),
                        'available_resources': get_available_creative_strategy_resources(view_type, period)
                    }
                    set_cached_data('creative_strategy', fallback_data, period, view_type)
                    result['creative_strategy'] = fallback_data
            
            if cached_instructional_design is None:
                print(f"Fetching Instructional Design department data for period: {period}")
                instructional_design_data = fetch_department_data_sequential('Instructional Design', period, view_type)
                if instructional_design_data:
                    set_cached_data('instructional_design', instructional_design_data, period, view_type)
                    result['instructional_design'] = instructional_design_data
                else:
                    # Fallback to original methods
                    fallback_data = {
                        'employees': get_instructional_design_employees(),
                        'team_utilization': get_instructional_design_team_utilization_data(period, view_type),
                        'timesheet_data': get_instructional_design_timesheet_data(period, view_type),
                        'available_resources': get_available_instructional_design_resources(view_type, period)
                    }
                    set_cached_data('instructional_design', fallback_data, period, view_type)
                    result['instructional_design'] = fallback_data
        else:
            # Use parallel processing
            # If the frontend passes a selected department, prioritize it first
            selected_department = request.args.get('selected_department')
            prioritized = []
            if selected_department in ('Creative', 'Creative Strategy', 'Instructional Design'):
                prioritized = [selected_department]
            # Prepare executor
            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                futures = {}
                department_order = ['Creative', 'Creative Strategy', 'Instructional Design']
                # Move prioritized department to front if present
                if prioritized:
                    department_order = prioritized + [d for d in department_order if d not in prioritized]

                for dept_name in department_order:
                    key = 'creative' if dept_name == 'Creative' else 'creative_strategy' if dept_name == 'Creative Strategy' else 'instructional_design'
                    if locals().get(f"cached_{key}") is None:
                        print(f"Fetching {dept_name} department data for period: {period}")
                        futures[key] = executor.submit(fetch_department_data_parallel, dept_name, period, view_type)
                
                # Wait for all parallel operations to complete
                for department, future in futures.items():
                    try:
                        data = future.result(timeout=REQUEST_TIMEOUT)
                        if data:
                            set_cached_data(department, data, period, view_type)
                            result[department] = data
                            print(f"Successfully fetched {department} data using parallel processing")
                        else:
                            # Fallback to sequential method if parallel fetch fails
                            print(f"Parallel fetch failed for {department}, falling back to sequential method")
                            proper_department_name = get_proper_department_name(department)
                            fallback_data = fetch_department_data_sequential(proper_department_name, period, view_type)
                            if fallback_data:
                                set_cached_data(department, fallback_data, period, view_type)
                                result[department] = fallback_data
                                print(f"Successfully fetched {department} data using sequential method")
                            else:
                                # Final fallback to original methods
                                print(f"Sequential fetch also failed for {department}, using original methods")
                                if department == 'creative':
                                    fallback_data = {
                                        'employees': get_creative_employees(),
                                        'team_utilization': get_team_utilization_data(period, view_type),
                                        'timesheet_data': get_creative_timesheet_data(period, view_type),
                                        'available_resources': get_available_creative_resources(view_type, period)
                                    }
                                else:
                                    proper_department_name = get_proper_department_name(department)
                                    # Use sequential aggregator for all non-creative departments to keep logic unified
                                    fallback_data = fetch_department_data_sequential(proper_department_name, period, view_type) or {
                                        'employees': get_creative_strategy_employees(),
                                        'team_utilization': get_creative_strategy_team_utilization_data(period, view_type),
                                        'timesheet_data': get_creative_strategy_timesheet_data(period, view_type),
                                        'available_resources': get_available_creative_strategy_resources(view_type, period)
                                    }
                                set_cached_data(department, fallback_data, period, view_type)
                                result[department] = fallback_data
                    except Exception as e:
                        print(f"Error in parallel fetch for {department}: {e}")
                        # Fallback to sequential method
                        print(f"Trying sequential method for {department}")
                        proper_department_name = get_proper_department_name(department)
                        fallback_data = fetch_department_data_sequential(proper_department_name, period, view_type)
                        if fallback_data:
                            set_cached_data(department, fallback_data, period, view_type)
                            result[department] = fallback_data
                            print(f"Successfully fetched {department} data using sequential method")
                        else:
                            # Final fallback to original methods
                            print(f"Sequential fetch also failed for {department}, using original methods")
                            if department == 'creative':
                                fallback_data = {
                                    'employees': get_creative_employees(),
                                    'team_utilization': get_team_utilization_data(period, view_type),
                                    'timesheet_data': get_creative_timesheet_data(period, view_type),
                                    'available_resources': get_available_creative_resources(view_type, period)
                                }
                            elif department == 'creative_strategy':
                                fallback_data = {
                                    'employees': get_creative_strategy_employees(),
                                    'team_utilization': get_creative_strategy_team_utilization_data(period, view_type),
                                    'timesheet_data': get_creative_strategy_timesheet_data(period, view_type),
                                    'available_resources': get_available_creative_strategy_resources(view_type, period)
                                }
                            elif department == 'instructional_design':
                                fallback_data = {
                                    'employees': get_instructional_design_employees(),
                                    'team_utilization': get_instructional_design_team_utilization_data(period, view_type),
                                    'timesheet_data': get_instructional_design_timesheet_data(period, view_type),
                                    'available_resources': get_available_instructional_design_resources(view_type, period)
                                }
                            else:
                                fallback_data = {
                                    'employees': get_creative_strategy_employees(),
                                    'team_utilization': get_creative_strategy_team_utilization_data(period, view_type),
                                    'timesheet_data': get_creative_strategy_timesheet_data(period, view_type),
                                    'available_resources': get_available_creative_strategy_resources(view_type, period)
                                }
                            set_cached_data(department, fallback_data, period, view_type)
                            result[department] = fallback_data
        
        # Add cached data for departments that were already cached
        if cached_creative is not None:
            result['creative'] = cached_creative
        
        if cached_creative_strategy is not None:
            result['creative_strategy'] = cached_creative_strategy
        
        if cached_instructional_design is not None:
            result['instructional_design'] = cached_instructional_design
        
        # Safety: ensure each result has core fields populated
        def _ensure_fields_result(dept_key, dept_name):
            try:
                data_obj = result.get(dept_key)
                if data_obj is None:
                    return
                needs_employees = len(data_obj.get('employees') or []) == 0
                needs_resources = len(data_obj.get('available_resources') or []) == 0
                needs_timesheets = len(data_obj.get('timesheet_data') or []) == 0
                needs_util = not bool(data_obj.get('team_utilization'))
                if not (needs_employees or needs_resources or needs_timesheets or needs_util):
                    return
                fetched = fetch_department_data_sequential(dept_name, period, view_type)
                if not fetched:
                    return
                if needs_employees and len(fetched.get('employees') or []) > 0:
                    data_obj['employees'] = fetched['employees']
                if needs_resources and len(fetched.get('available_resources') or []) > 0:
                    data_obj['available_resources'] = fetched['available_resources']
                if needs_timesheets and len(fetched.get('timesheet_data') or []) > 0:
                    data_obj['timesheet_data'] = fetched['timesheet_data']
                if needs_util and bool(fetched.get('team_utilization')):
                    data_obj['team_utilization'] = fetched['team_utilization']
                set_cached_data(dept_key, data_obj, period, view_type)
                result[dept_key] = data_obj
            except Exception:
                pass

        _ensure_fields_result('creative', 'Creative')
        _ensure_fields_result('creative_strategy', 'Creative Strategy')
        _ensure_fields_result('instructional_design', 'Instructional Design')
        
        result['cached'] = False
        result['cache_timestamp'] = time.time()
        
        print(f"=== API Response Debug ===")
        print(f"Returning fresh data for period: {period}, view_type: {view_type}")
        print(f"Response cached: {result['cached']}")
        print(f"Creative data available: {'yes' if 'creative' in result else 'no'}")
        print(f"Creative Strategy data available: {'yes' if 'creative_strategy' in result else 'no'}")
        print(f"Instructional Design data available: {'yes' if 'instructional_design' in result else 'no'}")
        
        return jsonify(result)
        
    except Exception as e:
        print(f"Error fetching all departments data: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/refresh-cache', methods=['POST'])
def refresh_cache():
    """
    Manually refresh the cache for all departments.
    """
    try:
        period = request.json.get('period') if request.is_json else None
        view_type = request.json.get('view_type', 'monthly') if request.is_json else 'monthly'
        
        print("Manually refreshing cache...")
        clear_cache()
        
        # Fetch fresh data for both departments
        creative_data = {
            'employees': get_creative_employees(),
            'team_utilization': get_team_utilization_data(period, view_type),
            'timesheet_data': get_creative_timesheet_data(period, view_type),
            'available_resources': get_available_creative_resources(view_type, period)
        }
        set_cached_data('creative', creative_data, period, view_type)
        
        creative_strategy_data = {
            'employees': get_creative_strategy_employees(),
            'team_utilization': get_creative_strategy_team_utilization_data(period, view_type),
            'timesheet_data': get_creative_strategy_timesheet_data(period, view_type),
            'available_resources': get_available_creative_strategy_resources(view_type, period)
        }
        set_cached_data('creative_strategy', creative_strategy_data, period, view_type)
        
        instructional_design_data = {
            'employees': get_instructional_design_employees(),
            'team_utilization': get_instructional_design_team_utilization_data(period, view_type),
            'timesheet_data': get_instructional_design_timesheet_data(period, view_type),
            'available_resources': get_available_instructional_design_resources(view_type, period)
        }
        set_cached_data('instructional_design', instructional_design_data, period, view_type)
        
        # Get the cache timestamp for this specific request
        cache_key = f"{period}_{view_type}" if period else f"default_{view_type}"
        cache_timestamp = department_cache['cache_timestamps'].get(cache_key, time.time())
        
        return jsonify({
            'message': 'Cache refreshed successfully',
            'cache_timestamp': cache_timestamp
        })
        
    except Exception as e:
        print(f"Error refreshing cache: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/connection-status', methods=['GET'])
def connection_status():
    """
    Get connection pool status information.
    """
    try:
        return jsonify({
            'success': True,
            'connection_status': get_connection_status(),
            'timestamp': datetime.datetime.now().isoformat()
        })
    except Exception as e:
        print(f"Error getting connection status: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/cache-status', methods=['GET'])
def cache_status():
    """
    Get cache status information.
    """
    try:
        return jsonify(get_cache_status())
    except Exception as e:
        print(f"Error getting cache status: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/performance-metrics', methods=['GET'])
def performance_metrics():
    """
    Get performance metrics and optimization status.
    """
    try:
        # Get system metrics
        cpu_percent = psutil.cpu_percent(interval=1)
        memory = psutil.virtual_memory()
        
        # Get cache metrics
        cache_info = get_cache_status()
        
        # Get connection pool status
        connection_status = {
            'has_cached_connection': _odoo_connection_pool['models'] is not None,
            'last_used': _odoo_connection_pool['last_used'],
            'connection_age': time.time() - _odoo_connection_pool['last_used'] if _odoo_connection_pool['last_used'] else None
        }
        
        return jsonify({
            'system': {
                'cpu_percent': cpu_percent,
                'memory_percent': memory.percent,
                'memory_available': memory.available // (1024 * 1024),  # MB
                'memory_total': memory.total // (1024 * 1024)  # MB
            },
            'cache': cache_info,
            'connection_pool': connection_status,
            'optimizations': {
                'parallel_processing': ENABLE_PARALLEL_PROCESSING,
                'connection_pooling': True,
                'pagination': True,
                'field_optimization': True
            }
        })
        
    except Exception as e:
        print(f"Error getting performance metrics: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/toggle-parallel-processing', methods=['POST'])
def toggle_parallel_processing():
    """
    Toggle parallel processing on/off.
    """
    global ENABLE_PARALLEL_PROCESSING
    
    try:
        data = request.get_json()
        if data and 'enabled' in data:
            ENABLE_PARALLEL_PROCESSING = bool(data['enabled'])
            return jsonify({
                'success': True,
                'parallel_processing_enabled': ENABLE_PARALLEL_PROCESSING,
                'message': f"Parallel processing {'enabled' if ENABLE_PARALLEL_PROCESSING else 'disabled'}"
            })
        else:
            return jsonify({'error': 'Missing "enabled" parameter'}), 400
            
    except Exception as e:
        print(f"Error toggling parallel processing: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/debug-departments', methods=['GET'])
def debug_departments():
    """Debug endpoint to check available departments in Odoo"""
    try:
        models, uid = connect_to_odoo()
        
        if not models or not uid:
            return jsonify({'error': 'Failed to connect to Odoo'}), 500
        
        # Get all departments
        department_ids = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'hr.department', 'search', [[]])
        
        if not department_ids:
            return jsonify({'error': 'No departments found'}), 404
        
        # Get department details
        departments = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'hr.department', 'read', 
                                      [department_ids], {
                                          'fields': ['name', 'id', 'employee_count']
                                      })
        
        # Also check for Instructional Design specifically
        instructional_design_ids = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'hr.department', 'search', 
                                                   [[('name', 'ilike', 'Instructional')]])
        
        instructional_design_departments = []
        if instructional_design_ids:
            instructional_design_departments = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'hr.department', 'read', 
                                                               [instructional_design_ids], {
                                                                   'fields': ['name', 'id', 'employee_count']
                                                               })
        
        return jsonify({
            'all_departments': departments,
            'instructional_design_matches': instructional_design_departments,
            'total_departments': len(departments)
        })
        
    except Exception as e:
        return jsonify({'error': f'Error fetching departments: {str(e)}'}), 500

def get_sales_order_hours_data(period=None, view_type='monthly'):
    """
    Fetch and calculate external hours from sales orders for a selected period
    (monthly by default), filtered by sale order date (date_order).
    For Jan-Jun 2025, uses Google Sheets data instead of Odoo.
    """
    try:
        # Check if we should use Google Sheets for this period (monthly only)
        if view_type == 'monthly' and should_use_google_sheets(period):
            print(f"Using Google Sheets data for period: {period}")
            
            # Extract month name from period (YYYY-MM format)
            year, month = period.split('-')
            month_num = int(month)
            
            # Map month number to month name
            month_names = {
                1: 'jan', 2: 'feb', 3: 'mar', 4: 'apr', 5: 'may', 6: 'jun'
            }
            
            month_name = month_names.get(month_num)
            if not month_name:
                return {'error': f'Invalid month for Google Sheets: {month}'}
            
            # Get hours from Google Sheets
            sheets_data = get_hours_from_google_sheets(month_name)
            if sheets_data is None:
                return {'error': f'Failed to retrieve data from Google Sheets for {month_name}'}
            
            # Calculate the last day of the month
            if month_num in [4, 6, 9, 11]:  # April, June, September, November
                last_day = 30
            elif month_num == 2:  # February
                if int(year) % 4 == 0 and (int(year) % 100 != 0 or int(year) % 400 == 0):  # Leap year
                    last_day = 29
                else:
                    last_day = 28
            else:  # January, March, May, July, August, October, December
                last_day = 31
            
            # Return data in the same format as Odoo function
            return {
                'ksa': {
                    'totalHours': sheets_data['ksa'],
                    'orders': []  # Empty array since we don't have detailed order data from sheets
                },
                'uae': {
                    'totalHours': sheets_data['uae'],
                    'orders': []  # Empty array since we don't have detailed order data from sheets
                },
                'view_type': view_type,
                'selected_period': period,
                'period_start': f"{year}-{month_num:02d}-01",
                'period_end': f"{year}-{month_num:02d}-{last_day}",
                'source': 'google_sheets'
            }
        
        # For other periods, use the original Odoo logic
        print(f"Using Odoo data for period: {period}")
        
        models, uid = connect_to_odoo()
        
        if not models or not uid:
            return {'error': 'Failed to connect to Odoo'}
        
        # Resolve date range
        try:
            # Allow monthly, weekly, and daily; default to monthly otherwise
            if view_type not in ('monthly', 'weekly', 'daily'):
                view_type = 'monthly'
            period_start, period_end = get_date_range(view_type, period)
        except Exception:
            # Fallback to current implementation defaults if parsing fails
            period_start, period_end = get_date_range('monthly', period or '2025-07')

        start_str = f"{period_start} 00:00:00"
        end_str = f"{period_end} 23:59:59"

        print(f"Starting external hours (sales orders) fetch for {period_start} to {period_end}...")
        
        # Step 1: Get all sales orders within selected month by date_order
        print("Fetching sales orders within selected month by date_order...")
        
        try:
            # First test with basic search to check connectivity
            print("Testing basic sale.order search connectivity...")
            test_search = execute_odoo_call_with_retry(
                models, uid, 'sale.order', 'search', 
                [[]], {'limit': 5}
            )
            print(f"Basic connectivity test: found {len(test_search)} orders (limited to 5)")
            
            # Check date format by reading a few sample orders
            if test_search:
                print("Checking date formats in sample orders...")
                sample_orders = execute_odoo_call_with_retry(
                    models, uid, 'sale.order', 'read',
                    [test_search, ['name', 'date_order']]
                )
                for order in sample_orders[:3]:
                    print(f"Sample order {order.get('name')}: date_order = {order.get('date_order')}")
            
            # Try different date formats for Odoo compatibility
            print("Attempting search with date filter...")
            try:
                # Preferred: filter by month range using datetime strings
                sales_order_ids = execute_odoo_call_with_retry(
                    models, uid, 'sale.order', 'search', 
                    [[('date_order', '>=', start_str), ('date_order', '<=', end_str)]]
                )
                print(f"Found {len(sales_order_ids)} sales orders with {start_str} <= date_order <= {end_str}")
            except Exception as date_error:
                print(f"Simple date format failed: {date_error}")
                # Try with datetime format
                try:
                    sales_order_ids = execute_odoo_call_with_retry(
                        models, uid, 'sale.order', 'search', 
                        [[('date_order', '>=', start_str)]]
                    )
                    print(f"Found {len(sales_order_ids)} sales orders with date_order >= {start_str}")
                except Exception as datetime_error:
                    print(f"Datetime format also failed: {datetime_error}")
                    print("Attempting fallback: fetch all orders and filter manually...")
                    
                    # Fallback: get all orders and filter manually
                    try:
                        all_order_ids = execute_odoo_call_with_retry(
                            models, uid, 'sale.order', 'search', 
                            [[]]
                        )
                        print(f"Retrieved {len(all_order_ids)} total orders for manual filtering")
                        
                        # Read all orders with date_order field
                        all_orders = execute_odoo_call_with_retry(
                            models, uid, 'sale.order', 'read',
                            [all_order_ids, ['name', 'date_order']]
                        )
                        
                        # Filter manually for orders inside [period_start, period_end]
                        from datetime import datetime
                        target_start = datetime.combine(period_start, datetime.min.time())
                        target_end = datetime.combine(period_end, datetime.max.time())
                        sales_order_ids = []
                        
                        for order in all_orders:
                            order_date_str = order.get('date_order')
                            if order_date_str:
                                # Handle various date formats
                                try:
                                    if isinstance(order_date_str, str):
                                        # Try parsing different date formats
                                        if 'T' in order_date_str:
                                            order_date = datetime.fromisoformat(order_date_str.replace('Z', '+00:00'))
                                        else:
                                            # Accept date or datetime without 'T'
                                            try:
                                                order_date = datetime.strptime(order_date_str, '%Y-%m-%d %H:%M:%S')
                                            except Exception:
                                                order_date = datetime.strptime(order_date_str, '%Y-%m-%d')
                                    else:
                                        continue
                                        
                                    if target_start <= order_date <= target_end:
                                        sales_order_ids.append(order['id'])
                                except Exception as parse_error:
                                    print(f"Could not parse date {order_date_str}: {parse_error}")
                                    continue
                        
                        print(f"Manual filtering found {len(sales_order_ids)} orders within selected month")
                        
                    except Exception as fallback_error:
                        print(f"Fallback method also failed: {fallback_error}")
                        raise fallback_error
            
            if not sales_order_ids:
                print("No sales orders found matching the date criteria")
                return {
                    'ksa': {'totalHours': 0, 'orders': []},
                    'uae': {'totalHours': 0, 'orders': []}
                }
            
        except Exception as e:
            print(f"Error searching for sales orders: {e}")
            print(f"Error type: {type(e).__name__}")
            return {'error': f'Failed to search sales orders: {str(e)}'}
        
        # Step 2: Read sales order data
        print("Reading sales order details...")
        try:
            sales_orders = execute_odoo_call_with_retry(
                models, uid, 'sale.order', 'read',
                [sales_order_ids, ['name', 'date_order', 'project_id', 'partner_id']]
            )
        except Exception as e:
            print(f"Error reading sales orders: {e}")
            return {'error': f'Failed to read sales orders: {str(e)}'}
        
        # Step 3: Get project IDs to fetch market information
        project_ids = []
        order_project_map = {}
        
        for order in sales_orders:
            if order.get('project_id'):
                project_id = order['project_id'][0] if isinstance(order['project_id'], list) else order['project_id']
                project_ids.append(project_id)
                order_project_map[order['id']] = project_id
        
        print(f"Found {len(project_ids)} unique projects linked to sales orders")
        
        # Step 4: Read project data to get market information
        project_markets = {}
        if project_ids:
            try:
                projects = execute_odoo_call_with_retry(
                    models, uid, 'project.project', 'read',
                    [project_ids, ['x_studio_market_2']]
                )
                
                for project in projects:
                    project_markets[project['id']] = project.get('x_studio_market_2', [None, 'Unknown'])[1] if project.get('x_studio_market_2') else 'Unknown'
                
                print(f"Retrieved market information for {len(project_markets)} projects")
                
            except Exception as e:
                print(f"Error reading project markets: {e}")
                return {'error': f'Failed to read project markets: {str(e)}'}
        
        # Step 5: Get order lines to calculate hours
        print("Fetching order lines for quantity calculations...")
        try:
            order_line_ids = execute_odoo_call_with_retry(
                models, uid, 'sale.order.line', 'search',
                [[('order_id', 'in', sales_order_ids)]]
            )
            
            order_lines = execute_odoo_call_with_retry(
                models, uid, 'sale.order.line', 'read',
                [order_line_ids, ['order_id', 'product_uom_qty']]
            )
            
            print(f"Found {len(order_lines)} order lines")
            
        except Exception as e:
            print(f"Error reading order lines: {e}")
            return {'error': f'Failed to read order lines: {str(e)}'}
        
        # Step 6: Group order lines by order ID
        order_hours_map = {}
        for line in order_lines:
            order_id = line['order_id'][0] if isinstance(line['order_id'], list) else line['order_id']
            if order_id not in order_hours_map:
                order_hours_map[order_id] = 0
            order_hours_map[order_id] += line.get('product_uom_qty', 0)
        
        # Step 7: Categorize by market (KSA/UAE) and aggregate by customer
        ksa_customers = {}
        uae_customers = {}
        ksa_total_hours = 0
        uae_total_hours = 0
        
        for order in sales_orders:
            order_id = order['id']
            project_id = order_project_map.get(order_id)
            market = project_markets.get(project_id, 'Unknown') if project_id else 'Unknown'
            total_hours = order_hours_map.get(order_id, 0)
            
            # Get customer information
            partner_id = order.get('partner_id')
            customer_name = 'Unknown Customer'
            customer_id = None
            
            if partner_id:
                if isinstance(partner_id, list) and len(partner_id) >= 2:
                    customer_id = partner_id[0]
                    customer_name = partner_id[1]
                elif isinstance(partner_id, (int, str)):
                    customer_id = partner_id
            
            order_data = {
                'order_name': order.get('name', 'Unknown'),
                'order_date': order.get('date_order', 'Unknown'),
                'market': market,
                'total_hours': total_hours
            }
            
            if market and market.upper() == 'KSA':
                ksa_total_hours += total_hours
                
                # Aggregate by customer for KSA
                if customer_name not in ksa_customers:
                    ksa_customers[customer_name] = {
                        'customer_id': customer_id,
                        'customer_name': customer_name,
                        'total_hours': 0,
                        'orders': []
                    }
                
                ksa_customers[customer_name]['total_hours'] += total_hours
                ksa_customers[customer_name]['orders'].append(order_data)
                
            elif market and market.upper() == 'UAE':
                uae_total_hours += total_hours
                
                # Aggregate by customer for UAE
                if customer_name not in uae_customers:
                    uae_customers[customer_name] = {
                        'customer_id': customer_id,
                        'customer_name': customer_name,
                        'total_hours': 0,
                        'orders': []
                    }
                
                uae_customers[customer_name]['total_hours'] += total_hours
                uae_customers[customer_name]['orders'].append(order_data)
        
        # Convert customer dictionaries to lists
        ksa_orders = list(ksa_customers.values())
        uae_orders = list(uae_customers.values())
        
        print(f"KSA: {len(ksa_orders)} orders, {ksa_total_hours} total hours")
        print(f"UAE: {len(uae_orders)} orders, {uae_total_hours} total hours")
        
        return {
            'ksa': {
                'totalHours': ksa_total_hours,
                'orders': ksa_orders
            },
            'uae': {
                'totalHours': uae_total_hours,
                'orders': uae_orders
            },
            'view_type': view_type,
            'selected_period': period,
            'period_start': period_start.isoformat(),
            'period_end': period_end.isoformat(),
            'source': 'odoo'
        }
        
    except Exception as e:
        print(f"Error in get_sales_order_hours_data: {e}")
        return {'error': str(e)}

def get_contract_sold_hours_data(period=None, view_type='monthly'):
    """
    Fetch and calculate contract hours for KSA and UAE pools from retainer contracts.
    When view_type is 'monthly' and period is 'YYYY-MM', allocate each contract's sold hours
    to that month using full or proportional coverage rules.
    """
    try:
        # Default to monthly view allocation
        if view_type not in ('monthly',):
            view_type = 'monthly'
        try:
            period_start, period_end = get_date_range(view_type, period)
        except Exception:
            # Fallback to a safe default month if parsing fails
            period_start, period_end = get_date_range('monthly', period or '2025-01')

        def _parse_date(date_value):
            """Parse various Odoo date/datetime formats to date, return None if unknown."""
            if not date_value or date_value == 'Unknown' or date_value is False:
                return None
            try:
                if isinstance(date_value, str):
                    if 'T' in date_value:
                        # ISO datetime
                        return datetime.datetime.fromisoformat(date_value.replace('Z', '+00:00')).date()
                    # Try date only
                    return datetime.datetime.strptime(date_value[:10], '%Y-%m-%d').date()
                if isinstance(date_value, (datetime.date, datetime.datetime)):
                    return date_value.date() if isinstance(date_value, datetime.datetime) else date_value
            except Exception:
                return None
            return None

        def _allocate_monthly_hours(start_date_val, end_date_val, monthly_hours_base):
            """
            Allocate hours for the month [period_start, period_end] following rules:
            - Full month coverage => full hours
            - Partial coverage => proportion = active_days_in_month / total_days_in_month
            - Open-ended start/end are treated as covering before/after respectively
            """
            if not monthly_hours_base or monthly_hours_base <= 0:
                return 0.0

            contract_start = _parse_date(start_date_val)
            contract_end = _parse_date(end_date_val)

            # If both dates are unknown, assume full coverage for safety
            if contract_start is None and contract_end is None:
                return float(monthly_hours_base)

            # Compute overlap
            coverage_start = period_start if contract_start is None else max(period_start, contract_start)
            coverage_end = period_end if contract_end is None else min(period_end, contract_end)

            if coverage_end < coverage_start:
                return 0.0

            total_days_in_month = (period_end - period_start).days + 1
            active_days = (coverage_end - coverage_start).days + 1
            proportion = min(1.0, max(0.0, active_days / total_days_in_month))
            return float(monthly_hours_base) * proportion

        models, uid = connect_to_odoo()
        
        if not models or not uid:
            return {'error': 'Failed to connect to Odoo'}
        
        print("Starting external hours data fetch...")
        
        # Debug: Check available fields in sale.order model
        try:
            sale_order_model = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'sale.order', 'fields_get', [], {})
            print("Available fields in sale.order:")
            subscription_fields = []
            for field_name, field_info in sale_order_model.items():
                if 'subscription' in field_name.lower() or 'state' in field_name.lower():
                    subscription_fields.append(f"  - {field_name}: {field_info.get('string', 'No description')}")
            for field in subscription_fields[:10]:  # Show first 10 matches
                print(field)
        except Exception as e:
            print(f"Error getting sale.order fields: {e}")
        
        # Step 1: Get all retainer contracts (sales.order) regardless of subscription status
        print("Fetching all retainer contracts with their data...")
        
        try:
            # First try to test basic connectivity with a simple search
            test_search = execute_odoo_call_with_retry(
                models, uid, 'sale.order', 'search', 
                [[]], {'limit': 1}
            )
            print(f"Basic sale.order search test: found {len(test_search)} orders")
            
            # Get all sale orders to show all subscription data
            retainer_contracts = execute_odoo_call_with_retry(
                models, uid, 'sale.order', 'search_read',
                [[]],
                {
                    'fields': [
                        'partner_id',  # Customer name 
                        'subscription_state',  # Subscription status
                        'x_studio_external_billable_hours_monthly',  # External billable hours
                        'project_ids',  # To get project details for market information
                        'date_order',  # Order date
                        'start_date',  # Start date (subscriptions)
                        'commitment_date',  # Delivery/commitment date (non-subscription fallback)
                        'validity_date',  # Quotation validity (non-subscription fallback)
                        'plan_id',  # Recurring plan
                        'next_invoice_date',  # Date of next invoice
                        'end_date'  # End date (subscriptions)
                    ],
                    'limit': 500,  # Limit for performance
                    'order': 'id desc'  # Get newest records first
                }
            )
        except Exception as e:
            print(f"Error in sale.order search: {e}")
            # Try fallback with basic search_read
            try:
                print("Trying fallback with all sale orders...")
                retainer_contracts = execute_odoo_call_with_retry(
                    models, uid, 'sale.order', 'search_read',
                    [[]],
                    {
                        'fields': ['partner_id', 'subscription_state', 'x_studio_external_billable_hours_monthly', 
                                 'project_ids', 'date_order', 'start_date', 'commitment_date', 'validity_date', 'plan_id', 'next_invoice_date', 'end_date'],
                        'limit': 100  # Even smaller limit for fallback
                    }
                )
            except Exception as e2:
                print(f"Fallback search also failed: {e2}")
                return {
                    'error': f'Failed to search sale.order: {str(e)}. Fallback search: {str(e2)}'
                }
        
        if not retainer_contracts:
            print("No retainer contracts found in the system")
            
            # Debug: Let's see what subscription states actually exist
            try:
                print("Checking what subscription_state values exist in the system...")
                all_orders = execute_odoo_call_with_retry(
                    models, uid, 'sale.order', 'search_read',
                    [[]],  # No filter - get all orders
                    {'fields': ['subscription_state'], 'limit': 50}
                )
                
                if all_orders:
                    subscription_states = set()
                    for order in all_orders:
                        state = order.get('subscription_state')
                        if state:
                            subscription_states.add(str(state))
                    
                    print(f"Found subscription states in system: {sorted(list(subscription_states))}")
                else:
                    print("No sale orders found in system")
                    
            except Exception as e:
                print(f"Error checking subscription states: {e}")
            
            return {
                'ksa': {'totalHours': 0, 'contracts': []},
                'uae': {'totalHours': 0, 'contracts': []}
            }
        
        print(f"Retrieved {len(retainer_contracts)} retainer contracts using search_read")
        
        # Debug: Log the subscription states we actually got
        if retainer_contracts:
            states_found = set()
            for contract in retainer_contracts:
                state = contract.get('subscription_state')
                if state:
                    states_found.add(str(state))
            print(f"Subscription states found in returned contracts: {sorted(list(states_found))}")
        
        # Step 2: Compute fallback hours for non-subscription contracts from order lines (batch)
        order_line_hours_map = {}
        try:
            order_ids = [c.get('id') for c in retainer_contracts if c.get('id')]
            if order_ids:
                order_line_ids = execute_odoo_call_with_retry(
                    models, uid, 'sale.order.line', 'search',
                    [[('order_id', 'in', order_ids)]]
                )
                order_lines = execute_odoo_call_with_retry(
                    models, uid, 'sale.order.line', 'read',
                    [order_line_ids, ['order_id', 'product_uom_qty', 'product_uom']]
                )
                for line in order_lines or []:
                    order_ref = line.get('order_id')
                    if isinstance(order_ref, (list, tuple)) and len(order_ref) >= 1:
                        order_id = order_ref[0]
                    else:
                        continue
                    uom = line.get('product_uom')
                    if isinstance(uom, (list, tuple)) and len(uom) >= 2:
                        uom_name = str(uom[1])
                    else:
                        uom_name = str(uom) if uom else ''
                    qty = line.get('product_uom_qty', 0) or 0
                    # Only count quantities that are measured in hours
                    if uom_name and 'hour' in uom_name.lower():
                        order_line_hours_map[order_id] = order_line_hours_map.get(order_id, 0) + qty
                print(f"Computed fallback hours from order lines for {len(order_line_hours_map)} orders")
        except Exception as e:
            print(f"Error computing fallback hours from order lines: {e}")

        # Step 3: Batch process all project IDs to get market information
        all_project_ids = []
        contract_project_map = {}
        
        for contract in retainer_contracts:
            if contract.get('project_ids'):
                project_ids = contract['project_ids']
                if project_ids:
                    first_project_id = project_ids[0]
                    all_project_ids.append(first_project_id)
                    contract_project_map[contract['id']] = first_project_id
        
        # Remove duplicates while preserving order
        unique_project_ids = list(dict.fromkeys(all_project_ids))
        print(f"Fetching market data for {len(unique_project_ids)} unique projects...")
        
        # Batch fetch all project market data in one call
        project_markets = {}
        if unique_project_ids:
            try:
                projects_data = execute_odoo_call_with_retry(
                    models, uid, 'project.project', 'read',
                    [unique_project_ids], {
                        'fields': ['x_studio_market_2']
                    }
                )
                for project in projects_data:
                    project_markets[project['id']] = project.get('x_studio_market_2', 'Unknown')
                print(f"Retrieved market data for {len(projects_data)} projects")
            except Exception as e:
                print(f"Error batch fetching project markets: {e}")
        
        # Step 4: Process contracts and group by market, allocating to selected month
        ksa_contracts = []
        uae_contracts = []
        ksa_total_hours = 0.0
        uae_total_hours = 0.0
        
        for contract in retainer_contracts:
            # Get partner name
            partner_name = contract.get('partner_id', [False, 'Unknown'])[1] if contract.get('partner_id') else 'Unknown'
            
            # Get external hours (monthly). Fallback to order line hours if custom field is missing
            external_hours = contract.get('x_studio_external_billable_hours_monthly', 0) or 0
            if not external_hours:
                contract_id = contract.get('id')
                if contract_id in order_line_hours_map:
                    external_hours = order_line_hours_map.get(contract_id, 0)
            
            # Get start date - use subscription start_date, else fall back to date_order
            start_date_raw = contract.get('start_date') or contract.get('date_order')
            if start_date_raw == False or start_date_raw is None:
                start_date_raw = 'Unknown'
            
            # Get end date - use subscription end_date, else fall back to commitment/validity/next_invoice
            end_date_raw = (
                contract.get('end_date') or
                contract.get('commitment_date') or
                contract.get('validity_date') or
                contract.get('next_invoice_date')
            )
            if end_date_raw == False or end_date_raw is None:
                end_date_raw = 'Unknown'
            
            # Get plan information - handle both subscription and non-subscription contracts
            plan_name = 'Unknown'
            subscription_state_raw = contract.get('subscription_state')
            
            if contract.get('plan_id'):
                try:
                    plan_name = contract['plan_id'][1] if isinstance(contract['plan_id'], list) else 'Unknown'
                except Exception as e:
                    print(f"Error getting plan name for contract {contract.get('id')}: {e}")
            else:
                # For non-subscription contracts, show a more descriptive label
                if subscription_state_raw == False or subscription_state_raw is None:
                    plan_name = 'Non-Subscription Contract'
            
            # Get market from batched project data
            market = 'Unknown'
            contract_id = contract.get('id')
            if contract_id in contract_project_map:
                project_id = contract_project_map[contract_id]
                market = project_markets.get(project_id, 'Unknown')
            
            # Convert subscription state to readable format
            if subscription_state_raw == '3_progress':
                subscription_status = 'In Progress'
            elif subscription_state_raw == '4_closed':
                subscription_status = 'Closed'
            elif subscription_state_raw == '5_cancelled':
                subscription_status = 'Cancelled'
            elif subscription_state_raw == '2_in_progress':
                subscription_status = 'In Progress' 
            elif subscription_state_raw == '1_draft':
                subscription_status = 'Draft'
            elif subscription_state_raw == False or subscription_state_raw is None:
                subscription_status = 'No Subscription'
            else:
                subscription_status = str(subscription_state_raw) if subscription_state_raw else 'No Subscription'

            # Format dates properly  
            formatted_order_date = 'Unknown'
            if start_date_raw and start_date_raw != False and start_date_raw != 'Unknown':
                try:
                    if isinstance(start_date_raw, str) and start_date_raw:
                        formatted_order_date = start_date_raw
                    else:
                        formatted_order_date = str(start_date_raw)
                except:
                    formatted_order_date = 'Unknown'
            
            formatted_end_date = 'Unknown'  
            if end_date_raw and end_date_raw != False and end_date_raw != 'Unknown':
                try:
                    if isinstance(end_date_raw, str) and end_date_raw:
                        formatted_end_date = end_date_raw
                    else:
                        formatted_end_date = str(end_date_raw)
                except:
                    formatted_end_date = 'Unknown'

            # Extract market name if it's a tuple/array
            formatted_market = 'Unknown'
            if market and market != 'Unknown':
                if isinstance(market, (list, tuple)) and len(market) >= 2:
                    formatted_market = market[1]  # Get the readable name
                else:
                    formatted_market = str(market)

            # Allocate hours for the selected month per rules
            monthly_allocated = _allocate_monthly_hours(start_date_raw, end_date_raw, external_hours)

            # Create contract data structure
            contract_data = {
                'partner_name': partner_name,
                'subscription_status': subscription_status,
                'external_hours': external_hours,
                'monthly_allocated_hours': monthly_allocated,
                'market': formatted_market,
                'order_date': formatted_order_date,
                'plan_name': plan_name,
                'next_invoice_date': formatted_end_date
            }
            
            # Group by market (KSA or UAE) - use formatted market for filtering
            if formatted_market and 'KSA' in str(formatted_market).upper():
                ksa_contracts.append(contract_data)
                ksa_total_hours += monthly_allocated
                print(f"Added KSA contract: {partner_name} - {external_hours} hours (Status: {subscription_status})")
            elif formatted_market and 'UAE' in str(formatted_market).upper():
                uae_contracts.append(contract_data)
                uae_total_hours += monthly_allocated
                print(f"Added UAE contract: {partner_name} - {external_hours} hours (Status: {subscription_status})")
            else:
                # For debugging: show contracts that don't match KSA or UAE
                print(f"Contract {partner_name} has market '{formatted_market}' (raw: {market}) - not matched to KSA or UAE (Status: {subscription_status})")
        
        print(f"Final results: KSA = {len(ksa_contracts)} contracts, {ksa_total_hours} hours")
        print(f"Final results: UAE = {len(uae_contracts)} contracts, {uae_total_hours} hours")
        
        return {
            'ksa': {'totalHours': ksa_total_hours, 'contracts': ksa_contracts},
            'uae': {'totalHours': uae_total_hours, 'contracts': uae_contracts},
            'view_type': view_type,
            'selected_period': period,
            'period_start': period_start.isoformat(),
            'period_end': period_end.isoformat()
        }
        
    except Exception as e:
        print(f"Error fetching contract sold hours data: {e}")
        return {'error': str(e)}

@app.route('/api/debug-subscription-states', methods=['GET'])
def debug_subscription_states():
    """
    Debug endpoint to check what subscription states exist in sale.order
    """
    try:
        models, uid = connect_to_odoo()
        
        if not models or not uid:
            return jsonify({'error': 'Failed to connect to Odoo'}), 500
        
        # Get all sale orders and their subscription states
        all_orders = execute_odoo_call_with_retry(
            models, uid, 'sale.order', 'search_read',
            [[]],  # No filter - get all orders
            {'fields': ['subscription_state', 'partner_id'], 'limit': 100}
        )
        
        if all_orders:
            subscription_states = {}
            for order in all_orders:
                state = order.get('subscription_state')
                partner = order.get('partner_id', [False, 'Unknown'])[1] if order.get('partner_id') else 'Unknown'
                
                if state:
                    state_str = str(state)
                    if state_str not in subscription_states:
                        subscription_states[state_str] = []
                    subscription_states[state_str].append(partner)
            
            return jsonify({
                'success': True,
                'total_orders': len(all_orders),
                'subscription_states': {k: list(set(v))[:5] for k, v in subscription_states.items()},  # First 5 unique partners per state
                'unique_states': list(subscription_states.keys())
            })
        else:
            return jsonify({
                'success': True,
                'message': 'No sale orders found in system'
            })
            
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/external-hours', methods=['GET'])
def external_hours():
    """
    API endpoint to get sold hours data for KSA and UAE pools for a selected month.
    Uses sales order hours data for accurate Efficiency Ratio calculations.
    """
    try:
        view_type = request.args.get('view_type', 'monthly')
        period = request.args.get('period')
        external_hours_data = get_sales_order_hours_data(period, view_type)
        
        if 'error' in external_hours_data:
            return jsonify({'error': external_hours_data['error']}), 500
        
        return jsonify({
            'success': True,
            'data': external_hours_data,
            'timestamp': datetime.datetime.now().isoformat(),
            'view_type': external_hours_data.get('view_type', view_type),
            'selected_period': external_hours_data.get('selected_period', period),
            'start_date': external_hours_data.get('period_start'),
            'end_date': external_hours_data.get('period_end')
        })
        
    except Exception as e:
        print(f"Error in external hours endpoint: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/sales-order-hours', methods=['GET'])
def sales_order_hours():
    """
    API endpoint to get external hours from sales orders after July 1st
    """
    try:
        view_type = request.args.get('view_type', 'monthly')
        period = request.args.get('period')
        external_hours_data = get_sales_order_hours_data(period, view_type)
        
        if 'error' in external_hours_data:
            return jsonify({'error': external_hours_data['error']}), 500
        
        return jsonify({
            'success': True,
            'data': external_hours_data,
            'timestamp': datetime.datetime.now().isoformat(),
            'view_type': external_hours_data.get('view_type', view_type),
            'selected_period': external_hours_data.get('selected_period', period),
            'start_date': external_hours_data.get('period_start'),
            'end_date': external_hours_data.get('period_end')
        })
        
    except Exception as e:
        print(f"Error in external hours endpoint: {e}")
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=False, host='0.0.0.0', port=5000) 