from flask import Flask, jsonify, request
from flask_cors import CORS
import xmlrpc.client
import os
from dotenv import load_dotenv
import datetime
from collections import defaultdict
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import psutil

# Load environment variables
load_dotenv()

app = Flask(__name__)
CORS(app)

# Odoo connection configuration
ODOO_URL = "https://prezlab-staging-22061821.dev.odoo.com"
ODOO_DB = "prezlab-staging-22061821"
ODOO_USERNAME = "omar.elhasan@prezlab.com"
ODOO_PASSWORD = "Omar@@1998"

# Performance configuration
ENABLE_PARALLEL_PROCESSING = True  # Re-enabled parallel processing
MAX_WORKERS = 2  # Number of parallel workers
REQUEST_TIMEOUT = 45  # Timeout in seconds for parallel requests

# Connection pool for Odoo
_odoo_connection_pool = {
    'models': None,
    'uid': None,
    'last_used': None,
    'lock': threading.Lock()
}

# Cache for storing department data
department_cache = {
    'creative': {},
    'creative_strategy': {},
    'cache_timestamps': {},  # Individual timestamps for each cache key
    'cache_duration': 300  # 5 minutes cache duration
}

cache_lock = threading.Lock()

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
        
        # Batch fetch categories
        all_category_ids = set()
        for emp in employees_data:
            if emp.get('category_ids'):
                all_category_ids.update(emp['category_ids'])
        
        categories_dict = {}
        if all_category_ids:
            try:
                categories = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'hr.employee.category', 'read', 
                                             [list(all_category_ids)], {'fields': ['name']})
                categories_dict = {cat['id']: cat['name'] for cat in categories if cat.get('name')}
            except Exception as e:
                print(f"Error fetching categories for {department_name}: {e}")
                # Continue without categories if they fail
        
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
                    
                    if task_start < filter_start and task_end >= filter_start:
                        days_in_filter = (task_end.date() - filter_start.date()).days + 1
                        allocated_hours = days_in_filter * 8
                    
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
                    
                    if task_start < filter_start and task_end >= filter_start:
                        days_in_filter = (task_end.date() - filter_start.date()).days + 1
                        allocated_hours = days_in_filter * 8
                    
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

def connect_to_odoo():
    """Establish connection to Odoo with connection pooling and retry logic"""
    with _odoo_connection_pool['lock']:
        current_time = time.time()
        
        # Check if we have a valid cached connection (reuse for 1 minute to avoid stale connections)
        if (_odoo_connection_pool['models'] and 
            _odoo_connection_pool['uid'] and 
            _odoo_connection_pool['last_used'] and 
            current_time - _odoo_connection_pool['last_used'] < 60):
            
            _odoo_connection_pool['last_used'] = current_time
            return _odoo_connection_pool['models'], _odoo_connection_pool['uid']
        
        # Retry logic for connection
        max_retries = 3
        for attempt in range(max_retries):
            try:
                # Clear any existing connection
                _odoo_connection_pool['models'] = None
                _odoo_connection_pool['uid'] = None
                _odoo_connection_pool['last_used'] = None
                
                # Create XML-RPC proxies with timeout
                common = xmlrpc.client.ServerProxy(f'{ODOO_URL}/xmlrpc/2/common', allow_none=True)
                models = xmlrpc.client.ServerProxy(f'{ODOO_URL}/xmlrpc/2/object', allow_none=True)
                
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
                
                print(f"Successfully connected to Odoo (attempt {attempt + 1})")
                return models, uid
                    
            except Exception as e:
                print(f"Error connecting to Odoo (attempt {attempt + 1}/{max_retries}): {e}")
                # Clear cached connection on error
                _odoo_connection_pool['models'] = None
                _odoo_connection_pool['uid'] = None
                _odoo_connection_pool['last_used'] = None
                
                # Wait before retry (exponential backoff)
                if attempt < max_retries - 1:
                    wait_time = (2 ** attempt) * 1  # 1, 2, 4 seconds
                    print(f"Waiting {wait_time} seconds before retry...")
                    time.sleep(wait_time)
        
        print("All connection attempts failed")
        return None, None

def execute_odoo_call_with_retry(models, uid, model_name, method, args, kwargs=None, max_retries=3):
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
                    return result_data
                else:
                    raise Exception(f"Odoo call failed: {result_data}")
            except queue.Empty:
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
                
                # Wait before retry (exponential backoff)
                if attempt < max_retries - 1:
                    wait_time = (2 ** attempt) * 2  # 2, 4, 8 seconds
                    print(f"Waiting {wait_time} seconds before retry...")
                    time.sleep(wait_time)
            else:
                # For non-connection errors, don't retry
                raise e
    
    raise Exception(f"All {max_retries} attempts failed for {model_name}.{method}")

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
        
        # Find the Creative department
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
        
        # Create a mapping of employee names to planned hours from available resources
        resource_planned_hours = {}
        for resource in available_resources:
            resource_planned_hours[resource['name']] = resource['planned_hours']
        
        # Update employee data with planned hours from available resources
        for emp_id, emp_data in employee_data.items():
            emp_name = emp_data['name']
            if emp_name in resource_planned_hours:
                emp_data['planned_hours'] = resource_planned_hours[emp_name]
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
            # Base Available Hours based on view type - Time Off Hours
            if view_type == 'monthly':
                base_available_hours_per_employee = 184  # 184 hours per month
            elif view_type == 'weekly':
                base_available_hours_per_employee = 40   # 40 hours per week
            else:  # daily
                base_available_hours_per_employee = 8    # 8 hours per day
            
            base_available_hours = base_available_hours_per_employee * total_creatives  # Base hours for all employees
            total_time_off_hours = sum(emp.get('time_off_hours', 0) for emp in team_employees)
            available_hours = base_available_hours - total_time_off_hours
            
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
        return team_stats
        
    except Exception as e:
        print(f"Error fetching team utilization data: {e}")
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
            {'fields': ['employee_id', 'unit_amount', 'task_id', 'date']}
        )
        
        print(f"Found {len(timesheets)} timesheet entries for creative employees")
        
        # Group timesheet data by employee
        employee_timesheets = {}
        for ts in timesheets:
            emp_field = ts.get('employee_id')
            if emp_field:
                emp_id = emp_field[0] if isinstance(emp_field, list) else emp_field
                if emp_id not in employee_timesheets:
                    employee_timesheets[emp_id] = {
                        'total_hours': 0,
                        'entries': []
                    }
                
                # Add hours to total
                hours = float(ts.get('unit_amount', 0))
                employee_timesheets[emp_id]['total_hours'] += hours
                
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
            
            result.append({
                'id': emp_id,
                'name': employee.get('name', ''),
                'job_title': employee.get('job_title', ''),
                'tags': tags,
                'total_hours': timesheet_data['total_hours'],
                'timesheet_entries': timesheet_data['entries'],
                'period_start': start_date.isoformat(),
                'period_end': end_date.isoformat()
            })
        
        # Sort by total hours (descending)
        result.sort(key=lambda x: x['total_hours'], reverse=True)
        
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
        
        # Get employee details for all creative employees
        employees_data = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'hr.employee', 'read', 
                                         [creative_employee_ids], {
                                             'fields': ['name', 'job_title', 'category_ids']
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
        
        # Create a dictionary to store employee availability data
        employee_availability = {}
        
        # Calculate base available hours based on view type
        if view_type == 'monthly':
            base_available_hours = 184  # 184 hours per month
        elif view_type == 'weekly':
            base_available_hours = 40   # 40 hours per week
        else:  # daily
            base_available_hours = 8    # 8 hours per day
        
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
        
        # Update employee availability with Time Off hours
        for emp_id, time_off_hours in employee_time_off.items():
            if emp_id in employee_availability:
                employee_availability[emp_id]['time_off_hours'] = time_off_hours
                employee_availability[emp_id]['available_hours'] = base_available_hours - time_off_hours
                print(f"Employee {employee_availability[emp_id]['name']}: {time_off_hours:.1f}h Time Off, {employee_availability[emp_id]['available_hours']:.1f}h available")
        
        # Get planning slots for creative employees using resource_id field
        # Convert dates to string format for Odoo
        start_str = start_date.strftime('%Y-%m-%d 00:00:00')
        end_str = end_date.strftime('%Y-%m-%d 23:59:59')
        
        print(f"Searching planning slots from {start_str} to {end_str}")
        print(f"Looking for planning slots for {len(creative_employee_ids)} creative employees")
        
        # First, let's check if there are any planning slots at all for these employees
        all_slots = execute_odoo_call_with_retry(models, uid, 'planning.slot', 'search', 
                                       [[('resource_id', 'in', creative_employee_ids)]], 
                                       {'limit': 1000})
        print(f"Total planning slots found for creative employees (any date): {len(all_slots)}")
        
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
            print(f"Sample planning slots (first 5):")
            for slot in sample_slots:
                print(f"  - {slot.get('start_datetime')} to {slot.get('end_datetime')} ({slot.get('allocated_hours', 0)}h)")
        
        # Search for planning slots where resource_id is in creative employees
        # Include slots that overlap with the filter range (start before but end within, or start within but end after)
        resource_ids = []
        max_retries = 3
        
        for attempt in range(max_retries):
            try:
                resource_ids = execute_odoo_call_with_retry(models, uid, 'planning.slot', 'search', 
                                           [[('resource_id', 'in', creative_employee_ids),
                                             ('start_datetime', '<=', end_str),
                                             ('end_datetime', '>=', start_str)]], 
                                           {'limit': 1000})
                if resource_ids:
                    print(f"Successfully found {len(resource_ids)} planning slots on attempt {attempt + 1}")
                    break
                else:
                    print(f"No planning slots found on attempt {attempt + 1}")
            except Exception as e:
                print(f"Error fetching planning slots for Creative (attempt {attempt + 1}): {e}")
                if attempt < max_retries - 1:
                    print(f"Retrying in 2 seconds...")
                    time.sleep(2)
                else:
                    print("All attempts failed, proceeding with empty planning slots")
                    resource_ids = []
        
        if resource_ids:
            print(f"Found {len(resource_ids)} planning slots for creative employees")
            
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
            
            print(f"Sample resource data: {resources[0] if resources else 'No resources'}")
            
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
                                
                                print(f"Daily view: Task spans {total_task_days} days, {total_task_hours}h total, {allocated_hours:.1f}h for this day")
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
                            print(f"Edge case detected: Task {resource['start_datetime']} to {resource['end_datetime']} - {days_in_filter} days in filter = {allocated_hours} hours")
                    
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
                
                print(f"Employee {employee_availability[employee_id]['name']}: {allocated_hours:.1f}h allocated ({allocated_percentage:.1f}%) from {len(resource_data['slots'])} slots")
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
                'planned_hours': data['planned_hours'],
                'availability_percentage': availability_percentage,
                'base_available_hours': data['base_available_hours'],
                'time_off_hours': data['time_off_hours'],
                'available_hours': data['available_hours'],
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
        
        # Create a mapping of employee names to planned hours from available resources
        resource_planned_hours = {}
        for resource in available_resources:
            resource_planned_hours[resource['name']] = resource['planned_hours']
        
        # Update employee data with planned hours from available resources
        for emp_id, emp_data in employee_data.items():
            emp_name = emp_data['name']
            if emp_name in resource_planned_hours:
                emp_data['planned_hours'] = resource_planned_hours[emp_name]
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
        if view_type == 'monthly':
            base_available_hours_per_employee = 184  # 184 hours per month
        elif view_type == 'weekly':
            base_available_hours_per_employee = 40   # 40 hours per week
        else:  # daily
            base_available_hours_per_employee = 8    # 8 hours per day
        
        base_available_hours = base_available_hours_per_employee * total_creatives  # Base hours for all employees
        total_time_off_hours = sum(emp.get('time_off_hours', 0) for emp in all_employees)
        available_hours = base_available_hours - total_time_off_hours
        
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
            {'fields': ['employee_id', 'unit_amount', 'task_id', 'date']}
        )
        
        print(f"Found {len(timesheets)} timesheet entries for Creative Strategy employees")
        
        # Group timesheet data by employee
        employee_timesheets = {}
        for ts in timesheets:
            emp_field = ts.get('employee_id')
            if emp_field:
                emp_id = emp_field[0] if isinstance(emp_field, list) else emp_field
                if emp_id not in employee_timesheets:
                    employee_timesheets[emp_id] = {
                        'total_hours': 0,
                        'entries': []
                    }
                
                # Add hours to total
                hours = float(ts.get('unit_amount', 0))
                employee_timesheets[emp_id]['total_hours'] += hours
                
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
            
            result.append({
                'id': emp_id,
                'name': employee.get('name', ''),
                'job_title': employee.get('job_title', ''),
                'tags': tags,
                'total_hours': timesheet_data['total_hours'],
                'timesheet_entries': timesheet_data['entries'],
                'period_start': start_date.isoformat(),
                'period_end': end_date.isoformat()
            })
        
        # Sort by total hours (descending)
        result.sort(key=lambda x: x['total_hours'], reverse=True)
        
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
        
        # Calculate base available hours based on view type
        if view_type == 'monthly':
            base_available_hours = 184  # 184 hours per month
        elif view_type == 'weekly':
            base_available_hours = 40   # 40 hours per week
        else:  # daily
            base_available_hours = 8    # 8 hours per day
        
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
        
        # Update employee availability with Time Off hours
        for emp_id, time_off_hours in employee_time_off.items():
            if emp_id in employee_availability:
                employee_availability[emp_id]['time_off_hours'] = time_off_hours
                employee_availability[emp_id]['available_hours'] = base_available_hours - time_off_hours
                print(f"Creative Strategy employee {employee_availability[emp_id]['name']}: {time_off_hours:.1f}h Time Off, {employee_availability[emp_id]['available_hours']:.1f}h available")
        
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
                'planned_hours': data['planned_hours'],
                'availability_percentage': availability_percentage,
                'base_available_hours': data['base_available_hours'],
                'time_off_hours': data['time_off_hours'],
                'available_hours': data['available_hours'],
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
            resource_planned_hours[resource['name']] = resource['planned_hours']
        
        for emp_id, emp_data in employee_data.items():
            emp_name = emp_data['name']
            if emp_name in resource_planned_hours:
                emp_data['planned_hours'] = resource_planned_hours[emp_name]
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
            available_hours = base_available_hours - total_time_off_hours
            
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
            
            employee_data = {
                'name': emp.get('name', ''),
                'job_title': emp.get('job_title', ''),
                'tags': tags,
                'total_hours': employee_hours.get(emp_id, 0),
                'timesheet_entries': employee_timesheet_entries.get(emp_id, [])
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
        
        # Calculate base available hours based on view type
        if view_type == 'monthly':
            base_available_hours = 184  # 184 hours per month
        elif view_type == 'weekly':
            base_available_hours = 40   # 40 hours per week
        else:  # daily
            base_available_hours = 8    # 8 hours per day
        
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
        
        # Update employee availability with Time Off hours
        for emp_id, time_off_hours in employee_time_off.items():
            if emp_id in employee_availability:
                employee_availability[emp_id]['time_off_hours'] = time_off_hours
                employee_availability[emp_id]['available_hours'] = base_available_hours - time_off_hours
                print(f"Instructional Design employee {employee_availability[emp_id]['name']}: {time_off_hours:.1f}h Time Off, {employee_availability[emp_id]['available_hours']:.1f}h available")
        
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
                'planned_hours': emp_data['planned_hours'],
                'base_available_hours': emp_data['base_available_hours'],
                'time_off_hours': emp_data['time_off_hours'],
                'available_hours': emp_data['available_hours'],
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
        
        # Check cache first
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
            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                futures = {}
                
                if cached_creative is None:
                    print(f"Fetching Creative department data for period: {period}")
                    futures['creative'] = executor.submit(fetch_department_data_parallel, 'Creative', period, view_type)
                
                if cached_creative_strategy is None:
                    print(f"Fetching Creative Strategy department data for period: {period}")
                    futures['creative_strategy'] = executor.submit(fetch_department_data_parallel, 'Creative Strategy', period, view_type)
                
                if cached_instructional_design is None:
                    print(f"Fetching Instructional Design department data for period: {period}")
                    futures['instructional_design'] = executor.submit(fetch_department_data_parallel, 'Instructional Design', period, view_type)
                
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
                                    fallback_data = {
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

if __name__ == '__main__':
    app.run(debug=False, host='0.0.0.0', port=5000) 