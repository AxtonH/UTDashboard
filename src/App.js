import React, { useState, useEffect, useMemo, useCallback } from 'react';
import Login from './components/Login';
import axios from 'axios';
import './App.css';

function DashboardApp({ onLogout }) {
  const [selectedDepartment, setSelectedDepartment] = useState('Creative');
  const [selectedPeriod, setSelectedPeriod] = useState(() => {
    const now = new Date();
    const year = now.getFullYear();
    const month = String(now.getMonth() + 1).padStart(2, '0');
    return `${year}-${month}`;
  });
  const [viewType, setViewType] = useState('monthly');
  const [activeTab, setActiveTab] = useState('employees');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [employees, setEmployees] = useState([]);
  const [teamUtilizationData, setTeamUtilizationData] = useState({});
  const [timesheetData, setTimesheetData] = useState([]);
  const [availableResources, setAvailableResources] = useState([]);
  const [showDetailedView, setShowDetailedView] = useState(false);
  const [allDepartmentsData, setAllDepartmentsData] = useState({});
  const [cacheStatus, setCacheStatus] = useState({});
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [selectedPoolFilters, setSelectedPoolFilters] = useState([]); // Array of selected pool names: ['KSA', 'UAE', etc.]
  const [selectedResourcePoolFilter, setSelectedResourcePoolFilter] = useState(null); // null, 'KSA', 'UAE', or 'Nightshift'
  const [selectedTimesheetPoolFilter, setSelectedTimesheetPoolFilter] = useState(null); // null, 'KSA', 'UAE', or 'Nightshift'
  const [selectedTeamForDetail, setSelectedTeamForDetail] = useState(null);
  const [detailedTeamData, setDetailedTeamData] = useState(null);
  const [fetchProgress, setFetchProgress] = useState(0);
  const [shareholders, setShareholders] = useState([]);
  const [newShareholderEmail, setNewShareholderEmail] = useState('');
  const [isSending, setIsSending] = useState(false);
  const [previewHtml, setPreviewHtml] = useState('');
  const [showPreview, setShowPreview] = useState(false);
  const [externalHoursData, setExternalHoursData] = useState({
    ksa: { totalHours: 0, contracts: [], orders: [] },
    uae: { totalHours: 0, contracts: [], orders: [] }
  });
  const [externalHoursCache, setExternalHoursCache] = useState({
    cached: false,
    last_updated: null,
    data: null
  });
  const [salesOrderHoursData, setSalesOrderHoursData] = useState({
    ksa: { totalHours: 0, orders: [] },
    uae: { totalHours: 0, orders: [] }
  });
  const [collapsedInvoiceAddresses, setCollapsedInvoiceAddresses] = useState({});

  // Function to toggle collapse state for invoice addresses
  const toggleInvoiceAddressCollapse = (addressKey) => {
    setCollapsedInvoiceAddresses(prev => ({
      ...prev,
      [addressKey]: !prev[addressKey]
    }));
  };

  // Function to collapse/expand all invoice addresses in a region
  const toggleRegionCollapse = (region) => {
    const regionData = salesOrderHoursData[region];
    if (!regionData || !regionData.orders) return;

    // Check if all addresses in this region are collapsed
    const addressKeys = regionData.orders.map((_, index) => `${region}-${index}`);
    const allCollapsed = addressKeys.every(key => collapsedInvoiceAddresses[key]);
    
    // Toggle all addresses in this region
    const newState = {};
    addressKeys.forEach(key => {
      newState[key] = !allCollapsed; // If all collapsed, expand all; otherwise collapse all
    });
    
    setCollapsedInvoiceAddresses(prev => ({
      ...prev,
      ...newState
    }));
  };
  const [salesOrderHoursCache, setSalesOrderHoursCache] = useState({
    cached: false,
    last_updated: null,
    data: null
  });

  // Creative Dashboard: expanded pool cards state
  const [expandedPools, setExpandedPools] = useState({ KSA: false, UAE: false, Nightshift: false });
  const togglePoolExpand = useCallback((poolName) => {
    setExpandedPools(prev => ({ ...prev, [poolName]: !prev[poolName] }));
  }, []);

  // Designers section: pool filter state (All, KSA, UAE, Nightshift)
  const [designerPoolFilter, setDesignerPoolFilter] = useState('All');

  const [dashboardCache, setDashboardCache] = useState({});
  
  const buildDashboardCacheKey = useCallback((dept, period, vtype) => `${dept}|${vtype}|${period}`, []);
  
  const applyDepartmentData = useCallback((deptData) => {
    setEmployees(deptData.employees || []);
    setTeamUtilizationData(deptData.team_utilization || {});
    setTimesheetData(deptData.timesheet_data || []);
    setAvailableResources(deptData.available_resources || []);
  }, []);
  
  const getDepartmentData = useCallback((departmentName) => {
    const deptKey = departmentName === 'Creative Strategy' ? 'creative_strategy' : 
                   (departmentName === 'Instructional Design' ? 'instructional_design' : 'creative');
    return allDepartmentsData[deptKey] || {};
  }, [allDepartmentsData]);
  
  const prePopulateAllTabs = useCallback(() => {
    // This function ensures all tabs have data populated for the current department
    console.log(`Pre-populating all tabs for department: ${selectedDepartment}`);
    const currentDeptData = getDepartmentData(selectedDepartment);
    console.log(`Current dept data available:`, {
      hasEmployees: !!(currentDeptData.employees && currentDeptData.employees.length > 0),
      hasResources: !!(currentDeptData.available_resources && currentDeptData.available_resources.length > 0),
      hasTimesheet: !!(currentDeptData.timesheet_data && currentDeptData.timesheet_data.length > 0),
      hasUtilization: !!(currentDeptData.team_utilization && Object.keys(currentDeptData.team_utilization).length > 0)
    });
    
    // Always apply department data if available, regardless of what's currently in state
    if (currentDeptData.employees || currentDeptData.available_resources || currentDeptData.timesheet_data || currentDeptData.team_utilization) {
      applyDepartmentData(currentDeptData);
      console.log(`Applied department data for ${selectedDepartment}`);
    }
    
    // Also ensure sales order hours data is populated if available
    if (salesOrderHoursCache.cached && salesOrderHoursCache.data) {
      setSalesOrderHoursData(salesOrderHoursCache.data);
    }
  }, [selectedDepartment, getDepartmentData, applyDepartmentData, salesOrderHoursCache]);



  // Helper function to get week date range
  const getWeekDateRange = useCallback((year, week) => {
    // Calculate the first Sunday of the year
    const startOfYear = new Date(year, 0, 1);
    const daysUntilSunday = (7 - startOfYear.getDay()) % 7;
    const firstSunday = new Date(startOfYear);
    firstSunday.setDate(startOfYear.getDate() + daysUntilSunday);
    
    // Calculate the start of the requested week (Sunday)
    const startDate = new Date(firstSunday);
    startDate.setDate(firstSunday.getDate() + (week - 1) * 7);
    
    // Calculate the end of the week (Saturday)
    const endDate = new Date(startDate);
    endDate.setDate(startDate.getDate() + 6);
    
    const startMonth = startDate.toLocaleDateString('en-US', { month: 'short' });
    const startDay = startDate.getDate();
    const endMonth = endDate.toLocaleDateString('en-US', { month: 'short' });
    const endDay = endDate.getDate();
    
    if (startMonth === endMonth) {
      return `${startMonth} ${startDay}-${endDay}, ${year}`;
    } else {
      return `${startMonth} ${startDay} - ${endMonth} ${endDay}, ${year}`;
    }
  }, []);

  // Memoized period options generation
  const periodOptions = useMemo(() => {
    if (viewType === 'monthly') {
      return [
        { value: '2025-01', label: 'January 2025' },
        { value: '2025-02', label: 'February 2025' },
        { value: '2025-03', label: 'March 2025' },
        { value: '2025-04', label: 'April 2025' },
        { value: '2025-05', label: 'May 2025' },
        { value: '2025-06', label: 'June 2025' },
        { value: '2025-07', label: 'July 2025' },
        { value: '2025-08', label: 'August 2025' },
        { value: '2025-09', label: 'September 2025' },
        { value: '2025-10', label: 'October 2025' },
        { value: '2025-11', label: 'November 2025' },
        { value: '2025-12', label: 'December 2025' }
      ];
    } else if (viewType === 'weekly') {
      // Weekly options for 2025
      const weeks = [];
      for (let week = 1; week <= 52; week++) {
        const weekNumber = week.toString().padStart(2, '0');
        weeks.push({
          value: `2025-${weekNumber}`,
          label: `Week ${weekNumber} (${getWeekDateRange(2025, week)})`
        });
      }
      return weeks;
    } else {
      // Daily options for 2025
      const days = [];
      const startDate = new Date(2025, 0, 1); // January 1, 2025
      const endDate = new Date(2025, 11, 31); // December 31, 2025
      
      for (let d = new Date(startDate); d <= endDate; d.setDate(d.getDate() + 1)) {
        const dayNumber = Math.floor((d - startDate) / (1000 * 60 * 60 * 24)) + 1;
        const dayNumberStr = dayNumber.toString().padStart(3, '0');
        const dateStr = d.toLocaleDateString('en-US', { 
          weekday: 'short', 
          month: 'short', 
          day: 'numeric',
          year: 'numeric'
        });
        days.push({
          value: `2025-${dayNumberStr}`,
          label: `${dateStr}`
        });
      }
      return days;
    }
  }, [viewType, getWeekDateRange]);



  // Helper function to format decimal hours consistently
  const formatDecimalHours = useCallback((decimal) => {
    if (decimal === 0) return '0h';
    
    let hours = Math.floor(decimal);
    let minutes = Math.round((decimal - hours) * 60);
    
    // Handle case where rounding minutes gives us 60
    if (minutes === 60) {
      hours += 1;
      minutes = 0;
    }
    
    if (minutes === 0) return hours === 0 ? '0h' : `${hours}h`;
    if (hours === 0) return `${minutes}m`;
    return `${hours}h ${minutes}m`;
  }, []);

  // Name normalization for reliable matching across datasets
  const normalizeName = useCallback((value) => {
    if (!value) return '';
    try {
      return value
        .toString()
        .toLowerCase()
        .normalize('NFD') // strip accents
        .replace(/[\u0300-\u036f]/g, '')
        .replace(/[^a-z0-9\s]/g, '') // remove punctuation
        .replace(/\s+/g, ' ') // collapse spaces
        .trim();
    } catch {
      return String(value).toLowerCase().trim();
    }
  }, []);

  // Build an index from timesheet data for fast and accurate logged-hours lookup by name
  const timesheetIndex = useMemo(() => {
    const map = new Map();
    const entries = [];
    (timesheetData || []).forEach((t) => {
      const nm = normalizeName(t?.name);
      const dec = typeof t?.total_hours === 'object'
        ? (t?.total_hours?.decimal || 0)
        : (t?.total_hours || 0);
      if (nm) {
        map.set(nm, dec);
        entries.push({ nm, dec });
      }
    });
    return { map, entries };
  }, [timesheetData, normalizeName]);

  // Helper to get logged hours for a designer name with light fuzzy fallback
  const getLoggedHoursForName = useCallback((name) => {
    const key = normalizeName(name);
    if (!key) return 0;
    if (timesheetIndex.map.has(key)) return timesheetIndex.map.get(key);
    // Fallback: contains match if unique
    const matches = timesheetIndex.entries.filter((e) => e.nm.includes(key) || key.includes(e.nm));
    if (matches.length === 1) return matches[0].dec;
    return 0;
  }, [normalizeName, timesheetIndex]);

  // Helper function to format currency amounts with correct symbols
  const formatCurrencyAmount = useCallback((amount, currency) => {
    if (!currency || !currency.symbol) {
      return `$${amount.toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
    }
    return `${currency.symbol}${amount.toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
  }, []);

  // Helper function to format pool currencies (handles multiple currencies)
  const formatPoolCurrencies = useCallback((currencies) => {
    if (!currencies || Object.keys(currencies).length === 0) {
      return '$0.00';
    }
    
    const currencyStrings = Object.entries(currencies).map(([currencyName, currencyData]) => {
      return `${currencyData.symbol}${currencyData.amount.toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
    });
    
    return currencyStrings.join(' + ');
  }, []);

  // Helper function to format AED amounts for scorecards only
  const formatAEDAmount = useCallback((amount) => {
    return `AED ${amount.toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
  }, []);

  // Helper function to handle tab switching with proper loading states
  const handleTabSwitch = useCallback((tabName) => {
    const tabsNeedingMainData = ['employees', 'resources', 'timesheet', 'utilization'];
    
    // Always ensure data is populated for tabs that need main data
    if (tabsNeedingMainData.includes(tabName)) {
      // Get current department data and apply it
      const currentDeptData = getDepartmentData(selectedDepartment);
      if (currentDeptData.employees || currentDeptData.available_resources || currentDeptData.timesheet_data || currentDeptData.team_utilization) {
        applyDepartmentData(currentDeptData);
      } else {
        // Only set loading if we truly have no data for this department
        const hasAnyDataForDept = Object.keys(allDepartmentsData).length > 0;
        if (!hasAnyDataForDept) {
          setLoading(true);
        }
      }
    }
    
    setActiveTab(tabName);
  }, [selectedDepartment, getDepartmentData, applyDepartmentData, allDepartmentsData]);

  useEffect(() => {
    console.log(`Data effect - dept: ${selectedDepartment}, period: ${selectedPeriod}, view: ${viewType}`);
    let canceled = false;
    const cacheKey = buildDashboardCacheKey(selectedDepartment, selectedPeriod, viewType);
    const cachedEntry = dashboardCache[cacheKey];
    if (cachedEntry) {
      console.log(`Using dashboard cache for key ${cacheKey}`);
      applyDepartmentData(cachedEntry.data);
      setCacheStatus({ cached: true, last_updated: cachedEntry.last_updated });
      return () => { canceled = true; };
    }

    setLoading(true);
    setError(null);
    setFetchProgress(0);

    const progressInterval = setInterval(() => {
      setFetchProgress(prev => Math.min(prev + 10, 90));
    }, 200);

    const fetchData = async () => {
      try {
        const apiUrl = `/api/all-departments-data?period=${selectedPeriod}&view_type=${viewType}&selected_department=${encodeURIComponent(selectedDepartment)}&include=employees,team_utilization,available_resources,timesheet_data`;
        console.log(`Fetching: ${apiUrl}`);
        const response = await axios.get(apiUrl);
        if (canceled) return;
        clearInterval(progressInterval);
        setFetchProgress(100);
        if (response.data.error) {
          throw new Error(response.data.error);
        }
        
        // Store all departments' data for tab switching
        const { creative, creative_strategy, instructional_design, cached, cache_timestamp } = response.data;
        const allDeptsData = {};
        if (creative) allDeptsData.creative = creative;
        if (creative_strategy) allDeptsData.creative_strategy = creative_strategy;
        if (instructional_design) allDeptsData.instructional_design = instructional_design;
        
        setAllDepartmentsData(allDeptsData);
        
        // Apply data for the currently selected department
        const deptKey = selectedDepartment === 'Creative Strategy' ? 'creative_strategy' : (selectedDepartment === 'Instructional Design' ? 'instructional_design' : 'creative');
        const deptData = allDeptsData[deptKey] || {};
        applyDepartmentData(deptData);
        
        setCacheStatus({ cached: !!cached, last_updated: cache_timestamp });
        setDashboardCache(prev => ({
          ...prev,
          [cacheKey]: { data: deptData, last_updated: Math.floor(Date.now() / 1000) }
        }));
        setLoading(false);
        setIsRefreshing(false);
      } catch (err) {
        clearInterval(progressInterval);
        console.error('Error fetching data:', err);
        setError(err.message);
        setLoading(false);
        setIsRefreshing(false);
      }
    };

    fetchData();
    return () => { canceled = true; clearInterval(progressInterval); };
  }, [selectedDepartment, selectedPeriod, viewType, buildDashboardCacheKey, dashboardCache, applyDepartmentData]);

  // Pre-populate all tabs when allDepartmentsData changes
  useEffect(() => {
    if (Object.keys(allDepartmentsData).length > 0) {
      prePopulateAllTabs();
    }
  }, [allDepartmentsData, prePopulateAllTabs]);

  // Ensure tabs get populated when department changes
  useEffect(() => {
    if (Object.keys(allDepartmentsData).length > 0) {
      console.log(`Department changed to ${selectedDepartment}, re-populating tabs`);
      prePopulateAllTabs();
    }
  }, [selectedDepartment, allDepartmentsData, prePopulateAllTabs]);

  // Load shareholders list on mount
  useEffect(() => {
    axios.get('/api/shareholders').then(res => {
      if (res.data && res.data.success) {
        setShareholders(res.data.shareholders || []);
      }
    }).catch(() => {});
  }, []);

  // Load sold hours data for selected period/view when utilization tab is active
  useEffect(() => {
    if (activeTab === 'utilization') {
      console.log('Fetching sold hours data for selected period/view...');
      setLoading(true);
      const url = `/api/external-hours?period=${selectedPeriod}&view_type=${viewType}`;
      axios.get(url)
        .then(res => {
          console.log('Sold hours response:', res.data);
          if (res.data && res.data.success) {
            const data = res.data.data;
            setExternalHoursData(data);
            setExternalHoursCache({
              cached: true,
              last_updated: Math.floor(Date.now() / 1000),
              data
            });
          } else if (res.data && res.data.error) {
            setError(res.data.error);
          }
        })
        .catch(err => {
          console.error('Error fetching sold hours data:', err);
          setError(err.response?.data?.error || 'Failed to fetch sold hours data');
        })
        .finally(() => {
          setLoading(false);
        });
    }
  }, [activeTab, selectedPeriod, viewType]);

  // Load external hours data when sales-order-hours tab is active
  useEffect(() => {
    if (activeTab === 'sales-order-hours') {
      // Check if we have cached data
      if (salesOrderHoursCache.cached && salesOrderHoursCache.data) {
        console.log('Using cached sales order hours data');
        setSalesOrderHoursData(salesOrderHoursCache.data);
        return;
      }

      console.log('Fetching sales order hours data...');
      setLoading(true);
      
      const url = `/api/sales-order-hours?period=${selectedPeriod}&view_type=${viewType}`;
      axios.get(url)
        .then(res => {
          console.log('Sales order hours response:', res.data);
          if (res.data && res.data.success) {
            const data = res.data.data;
            setSalesOrderHoursData(data);
            
            // Cache the data
            setSalesOrderHoursCache({
              cached: true,
              last_updated: Math.floor(Date.now() / 1000), // Current timestamp in seconds
              data: data
            });
          } else if (res.data && res.data.error) {
            setError(res.data.error);
          }
        })
        .catch(err => {
          console.error('Error fetching sales order hours data:', err);
          const errorMessage = err.response?.data?.error || 'Failed to fetch sales order hours data';
          
          // Check if it's a server unavailability issue
          if (errorMessage.includes('503') || errorMessage.includes('SERVICE UNAVAILABLE') || errorMessage.includes('Authentication failed')) {
            setError('External data service is temporarily unavailable. Please try again later.');
          } else {
            setError(errorMessage);
          }
        })
        .finally(() => {
          setLoading(false);
        });
    }
  }, [activeTab, salesOrderHoursCache.cached, salesOrderHoursCache.data, selectedPeriod, viewType]);



  const refreshSalesOrderHoursCache = async () => {
    console.log('Refreshing sales order hours cache...');
    try {
      const response = await axios.get(`/api/sales-order-hours?period=${selectedPeriod}&view_type=${viewType}`);
      console.log('Fresh sales order hours response:', response.data);
      
      if (response.data && response.data.success) {
        const data = response.data.data;
        setSalesOrderHoursData(data);
        
        // Update cache
        setSalesOrderHoursCache({
          cached: true,
          last_updated: Math.floor(Date.now() / 1000),
          data: data
        });
      } else if (response.data && response.data.error) {
        throw new Error(response.data.error);
      }
    } catch (err) {
      console.error('Error refreshing sales order hours cache:', err);
      const errorMessage = err.response?.data?.error || err.message || 'Failed to refresh sales order hours data';
      
      // Check if it's a server unavailability issue
      if (errorMessage.includes('503') || errorMessage.includes('SERVICE UNAVAILABLE') || errorMessage.includes('Authentication failed')) {
        throw new Error('External data service is temporarily unavailable. Please try again later.');
      } else {
        throw err;
      }
    }
  };


  const handleRefresh = async () => {
    setIsRefreshing(true);
    setError(null);
    
    try {
      if (activeTab === 'sales-order-hours') {
        // Refresh sales order hours cache
        await refreshSalesOrderHoursCache();
        console.log('Sales order hours cache refreshed successfully');
      } else {
        // Refresh main dashboard cache
        console.log('Manually refreshing cache...');
        console.log(`Refresh parameters - period: ${selectedPeriod}, view_type: ${viewType}`);
        
        // Call the cache refresh API
        const refreshPayload = {
          period: selectedPeriod,
          view_type: viewType
        };
        console.log(`Sending refresh payload:`, refreshPayload);
        
        const response = await axios.post('/api/refresh-cache', refreshPayload);
        
        if (response.data.error) {
          throw new Error(response.data.error);
        }
        
        // Fetch fresh data after cache refresh
        // Request full payload after refresh
        const dataApiUrl = `/api/all-departments-data?period=${selectedPeriod}&view_type=${viewType}&selected_department=${encodeURIComponent(selectedDepartment)}&include=employees,team_utilization,available_resources,timesheet_data`;
        console.log(`Fetching fresh data from: ${dataApiUrl}`);
        
        const dataResponse = await axios.get(dataApiUrl);
        
        console.log(`Fresh data response:`);
        console.log(`- Status: ${dataResponse.status}`);
        console.log(`- Cached: ${dataResponse.data.cached}`);
        console.log(`- View Type: ${dataResponse.data.view_type || 'not specified'}`);
        console.log(`- Period: ${dataResponse.data.selected_period || 'not specified'}`);
        
        if (dataResponse.data.error) {
          throw new Error(dataResponse.data.error);
        }
        
        const { creative, creative_strategy, instructional_design, cached, cache_timestamp } = dataResponse.data;
        
        // Only store and process data for departments that were actually returned
        const departmentsData = {};
        if (creative) departmentsData.creative = creative;
        if (creative_strategy) departmentsData.creative_strategy = creative_strategy;
        if (instructional_design) departmentsData.instructional_design = instructional_design;
        
        // Update departments data with only what was returned
        setAllDepartmentsData(departmentsData);
        
        // Update current department data and cache
        const deptKeyName = selectedDepartment === 'Creative Strategy' ? 'creative_strategy' : (selectedDepartment === 'Instructional Design' ? 'instructional_design' : 'creative');
        const currentDepartmentData = departmentsData[deptKeyName] || {};
        if (currentDepartmentData) {
          setEmployees(currentDepartmentData.employees || []);
          setTeamUtilizationData(currentDepartmentData.team_utilization || {});
          setTimesheetData(currentDepartmentData.timesheet_data || []);
          setAvailableResources(currentDepartmentData.available_resources || []);
          const cacheKey = buildDashboardCacheKey(selectedDepartment, selectedPeriod, viewType);
          setDashboardCache(prev => ({
            ...prev,
            [cacheKey]: { data: currentDepartmentData, last_updated: Math.floor(Date.now() / 1000) }
          }));
        }
        
        // Update cache status
        setCacheStatus({
          cached,
          last_updated: cache_timestamp
        });
        
        console.log('Cache refreshed successfully');
      }
      
    } catch (err) {
      console.error('Error refreshing cache:', err);
      setError(err.message);
    } finally {
      setIsRefreshing(false);
    }
  };

  const handleDepartmentChange = (department) => {
    console.log(`Switching department to: ${department}`);
    const cacheKey = buildDashboardCacheKey(department, selectedPeriod, viewType);
    const cachedEntry = dashboardCache[cacheKey];
    if (cachedEntry) {
      console.log(`Using cached dashboard data for ${cacheKey}`);
      applyDepartmentData(cachedEntry.data);
      setCacheStatus({ cached: true, last_updated: cachedEntry.last_updated });
    } else {
      // Check if we have data for this department in allDepartmentsData
      const deptKey = department === 'Creative Strategy' ? 'creative_strategy' : 
                     (department === 'Instructional Design' ? 'instructional_design' : 'creative');
      const deptData = allDepartmentsData[deptKey];
      if (deptData) {
        console.log(`Using existing department data for ${department}`);
        applyDepartmentData(deptData);
      } else {
        setIsRefreshing(true);
      }
    }

    setSelectedDepartment(department);
    setSelectedPoolFilters([]);
    setSelectedResourcePoolFilter(null);
  };

  const handlePeriodChange = (newPeriod) => {
    console.log(`Period changed to: ${newPeriod} (view type: ${viewType})`);
    
    // Validate that the new period is valid for the current view type
    const isValidPeriod = periodOptions.some(option => option.value === newPeriod);
    
    if (!isValidPeriod) {
      console.warn(`Invalid period ${newPeriod} for view type ${viewType}. Using first valid option.`);
      setSelectedPeriod(periodOptions[0].value);
    } else {
      setSelectedPeriod(newPeriod);
    }
    const cacheKey = buildDashboardCacheKey(selectedDepartment, newPeriod, viewType);
    const cachedEntry = dashboardCache[cacheKey];
    if (cachedEntry) {
      applyDepartmentData(cachedEntry.data);
      setCacheStatus({ cached: true, last_updated: cachedEntry.last_updated });
    } else {
      setIsRefreshing(true);
    }
    
    // Clear external hours data and cache when period changes
    setExternalHoursData({
      ksa: { totalHours: 0, contracts: [], orders: [] },
      uae: { totalHours: 0, contracts: [], orders: [] }
    });
    setExternalHoursCache({
      cached: false,
      last_updated: null,
      data: {}
    });
    
    // Clear sales order hours data and cache when period changes
    setSalesOrderHoursData({
      ksa: { totalHours: 0, orders: [] },
      uae: { totalHours: 0, orders: [] }
    });
    setSalesOrderHoursCache({
      cached: false,
      last_updated: null,
      data: {}
    });
    
    // The main effect will handle fetching if needed
  };

  const handleViewTypeChange = (newViewType) => {
    console.log(`Switching to ${newViewType} view`);
    console.log(`Current viewType: ${viewType}, newViewType: ${newViewType}`);
    
    setIsRefreshing(true);
    
    // Clear external hours data and cache when view type changes
    setExternalHoursData({
      ksa: { totalHours: 0, contracts: [], orders: [] },
      uae: { totalHours: 0, contracts: [], orders: [] }
    });
    setExternalHoursCache({
      cached: false,
      last_updated: null,
      data: {}
    });
    
    // Clear sales order hours data and cache when view type changes
    setSalesOrderHoursData({
      ksa: { totalHours: 0, orders: [] },
      uae: { totalHours: 0, orders: [] }
    });
    setSalesOrderHoursCache({
      cached: false,
      last_updated: null,
      data: {}
    });
    
    // Update both state values at the same time to trigger useEffect
    setViewType(newViewType);
    // Reset to first period option when view type changes
    setSelectedPeriod('2025-01'); // Default to first month
    console.log('State update calls completed');
    
    // The main effect will handle fetching or serving from cache
  };

  const handlePoolFilterClick = (poolName) => {
    setSelectedPoolFilters(prevFilters => {
      if (prevFilters.includes(poolName)) {
        // Remove the pool if it's already selected
        return prevFilters.filter(filter => filter !== poolName);
      } else {
        // Add the pool to the selection
        return [...prevFilters, poolName];
      }
    });
  };

  const filteredEmployees = useMemo(() => {
    // First filter out employees without any tags
    const employeesWithTags = employees.filter(emp => 
      emp.tags && emp.tags.length > 0
    );
    
    if (selectedPoolFilters.length === 0) {
      return employeesWithTags;
    }
    
    // Get all employees that have any of the selected tags
    const filteredEmployees = employeesWithTags.filter(emp => {
      const hasMatchingTag = emp.tags && emp.tags.some(tag => 
        selectedPoolFilters.some(filter => 
          tag.trim().toLowerCase() === filter.trim().toLowerCase()
        )
      );
      return hasMatchingTag;
    });
    
    // Remove duplicates by employee name (since we might not have consistent IDs)
    const uniqueEmployees = filteredEmployees.filter((emp, index, self) => 
      index === self.findIndex(e => e.name === emp.name)
    );
    
    return uniqueEmployees;
  }, [employees, selectedPoolFilters]);

  const handleResourcePoolFilterClick = (poolName) => {
    if (selectedResourcePoolFilter === poolName) {
      // If clicking the same pool, clear the filter
      setSelectedResourcePoolFilter(null);
    } else {
      // Set the new filter
      setSelectedResourcePoolFilter(poolName);
    }
  };

  const filteredResources = useMemo(() => {
    // First filter out employees without any tags
    const resourcesWithTags = availableResources.filter(resource => 
      resource.tags && resource.tags.length > 0
    );
    
    // Then apply pool filter if selected
    if (!selectedResourcePoolFilter) {
      return resourcesWithTags;
    }
    return resourcesWithTags.filter(resource => 
      resource.tags && resource.tags.some(tag => 
        tag.trim().toLowerCase() === selectedResourcePoolFilter.trim().toLowerCase()
      )
    );
  }, [availableResources, selectedResourcePoolFilter]);

  const handleTimesheetPoolFilterClick = (poolName) => {
    if (selectedTimesheetPoolFilter === poolName) {
      // If clicking the same pool, clear the filter
      setSelectedTimesheetPoolFilter(null);
    } else {
      // Set the new filter
      setSelectedTimesheetPoolFilter(poolName);
    }
  };

  const filteredTimesheetData = useMemo(() => {
    if (!selectedTimesheetPoolFilter) {
      return timesheetData;
    }
    return timesheetData.filter(employee => 
      employee.tags && employee.tags.some(tag => 
        tag.trim().toLowerCase() === selectedTimesheetPoolFilter.trim().toLowerCase()
      )
    );
  }, [timesheetData, selectedTimesheetPoolFilter]);

  // Memoized pool statistics calculations
  const poolCounts = useMemo(() => ({
    'KSA': employees.filter(emp => emp.tags && emp.tags.some(tag => tag.trim().toLowerCase() === 'ksa')).length,
    'UAE': employees.filter(emp => emp.tags && emp.tags.some(tag => tag.trim().toLowerCase() === 'uae')).length,
    'Nightshift': employees.filter(emp => emp.tags && emp.tags.some(tag => tag.trim().toLowerCase() === 'nightshift')).length
  }), [employees]);

  const activeTimesheetCount = useMemo(() => 
    timesheetData.filter(employee => 
      (employee.total_hours?.decimal || 0) > 0 && 
      employee.tags && 
      employee.tags.length > 0
    ).length,
    [timesheetData]
  );

  const totalLoggedHours = useMemo(() => {
    // Only include employees with KSA, UAE, or Nightshift tags in the total
    const poolEmployees = timesheetData.filter(employee => 
      employee.tags && employee.tags.some(tag => 
        ['ksa', 'uae', 'nightshift'].includes(tag.trim().toLowerCase())
      )
    );
    
    return poolEmployees.reduce((total, employee) => total + (employee.total_hours?.decimal || 0), 0);
  }, [timesheetData]);

  // Memoized resource pool statistics  
  const resourcePoolStats = useMemo(() => ({
    'KSA': {
      availableResources: availableResources.filter(resource => 
        resource.tags && resource.tags.some(tag => tag.trim().toLowerCase() === 'ksa')
      ).length,
      totalPlannedHours: availableResources.filter(resource => 
        resource.tags && resource.tags.some(tag => tag.trim().toLowerCase() === 'ksa')
      ).reduce((total, resource) => total + (resource.planned_hours?.decimal || 0), 0),
      totalAvailableHours: availableResources.filter(resource => 
        resource.tags && resource.tags.some(tag => tag.trim().toLowerCase() === 'ksa')
      ).reduce((total, resource) => total + (resource.available_hours?.decimal || 0), 0)
    },
    'UAE': {
      availableResources: availableResources.filter(resource => 
        resource.tags && resource.tags.some(tag => tag.trim().toLowerCase() === 'uae')
      ).length,
      totalPlannedHours: availableResources.filter(resource => 
        resource.tags && resource.tags.some(tag => tag.trim().toLowerCase() === 'uae')
      ).reduce((total, resource) => total + (resource.planned_hours?.decimal || 0), 0),
      totalAvailableHours: availableResources.filter(resource => 
        resource.tags && resource.tags.some(tag => tag.trim().toLowerCase() === 'uae')
      ).reduce((total, resource) => total + (resource.available_hours?.decimal || 0), 0)
    },
    'Nightshift': {
      availableResources: availableResources.filter(resource => 
        resource.tags && resource.tags.some(tag => tag.trim().toLowerCase() === 'nightshift')
      ).length,
      totalPlannedHours: availableResources.filter(resource => 
        resource.tags && resource.tags.some(tag => tag.trim().toLowerCase() === 'nightshift')
      ).reduce((total, resource) => total + (resource.planned_hours?.decimal || 0), 0),
      totalAvailableHours: availableResources.filter(resource => 
        resource.tags && resource.tags.some(tag => tag.trim().toLowerCase() === 'nightshift')
      ).reduce((total, resource) => total + (resource.available_hours?.decimal || 0), 0)
    }
  }), [availableResources]);

  // Calculate available hours for each pool from availableResources (same source as Available Creatives tab)
  const poolAvailableHours = useMemo(() => ({
    'KSA': availableResources.filter(resource => 
      resource.tags && resource.tags.some(tag => tag.trim().toLowerCase() === 'ksa')
    ).reduce((total, resource) => total + (resource.available_hours?.decimal || 0), 0),
    'UAE': availableResources.filter(resource => 
      resource.tags && resource.tags.some(tag => tag.trim().toLowerCase() === 'uae')
    ).reduce((total, resource) => total + (resource.available_hours?.decimal || 0), 0),
    'Nightshift': availableResources.filter(resource => 
      resource.tags && resource.tags.some(tag => tag.trim().toLowerCase() === 'nightshift')
    ).reduce((total, resource) => total + (resource.available_hours?.decimal || 0), 0)
  }), [availableResources]);

  // Calculate company utilization pie chart data
  const companyUtilizationData = useMemo(() => {
    const pools = ['KSA', 'UAE', 'Nightshift'];
    const poolData = pools.map(poolName => {
      const teamData = teamUtilizationData[poolName];
      if (!teamData) return null;
      
      const loggedHours = teamData.logged_hours || 0;
      const availableHours = poolAvailableHours[poolName] || 0;
      const utilizationRate = availableHours > 0 ? (loggedHours / availableHours * 100) : 0;
      
      return {
        name: poolName,
        loggedHours,
        availableHours,
        utilizationRate,
        color: poolName === 'KSA' ? '#3498db' : poolName === 'UAE' ? '#e74c3c' : '#f39c12'
      };
    }).filter(Boolean);

    const totalLoggedHours = poolData.reduce((sum, pool) => sum + pool.loggedHours, 0);
    const totalAvailableHours = poolData.reduce((sum, pool) => sum + pool.availableHours, 0);
    const totalUtilizationRate = totalAvailableHours > 0 ? (totalLoggedHours / totalAvailableHours * 100) : 0;

    // Calculate angles for pie chart based on each pool's contribution to total company utilization
    let currentAngle = 0;
    const pieData = poolData.map(pool => {
      // Each pool's contribution to total company utilization = (pool's logged hours / total logged hours) * 100
      const poolContributionToTotal = totalLoggedHours > 0 ? (pool.loggedHours / totalLoggedHours) * 100 : 0;
      const angle = (poolContributionToTotal / 100) * 360;
      const poolData = {
        ...pool,
        angle,
        startAngle: currentAngle,
        endAngle: currentAngle + angle,
        utilizationContribution: poolContributionToTotal
      };
      currentAngle += angle;
      return poolData;
    });

    // Calculate additional data for funnel chart
    const totalPlannedHours = poolData.reduce((sum, pool) => {
      const teamData = teamUtilizationData[pool.name];
      return sum + (teamData?.planned_hours || 0);
    }, 0);

    // Get external hours from external hours data (same as used in utilization dashboard)
    const totalExternalHours = (externalHoursData.ksa.totalHours || 0) + (externalHoursData.uae.totalHours || 0);
    
    console.log('Funnel data:', {
      totalAvailableHours,
      totalPlannedHours,
      totalExternalHours,
      totalLoggedHours,
      externalHoursData
    });

    return {
      pools: pieData,
      totalLoggedHours,
      totalAvailableHours,
      totalPlannedHours,
      totalExternalHours,
      totalUtilizationRate
    };
  }, [teamUtilizationData, poolAvailableHours, externalHoursData]);

  const totalResourceHours = useMemo(() => {
    // Only include employees with KSA, UAE, or Nightshift tags in the totals
    const poolResources = availableResources.filter(resource => 
      resource.tags && resource.tags.some(tag => 
        ['ksa', 'uae', 'nightshift'].includes(tag.trim().toLowerCase())
      )
    );
    
    return {
      totalPlannedHours: poolResources.reduce((total, resource) => 
      total + (resource.planned_hours?.decimal || 0), 0
    ),
      totalAvailableHours: poolResources.reduce((total, resource) => 
      total + (resource.available_hours?.decimal || 0), 0
    )
    };
  }, [availableResources]);

  // Function to handle clicking on utilization chart
  const handleUtilizationChartClick = (teamName, teamData) => {
    setSelectedTeamForDetail(teamName);
    setDetailedTeamData(teamData);
    setShowDetailedView(true);
  };

  // Function to close detailed view
  const closeDetailedView = () => {
    setShowDetailedView(false);
    setSelectedTeamForDetail(null);
    setDetailedTeamData(null);
  };

  if (loading) {
    return (
      <div className="app">
        <div className="container">
          <div className="loading">
            <div className="spinner"></div>
            <p>Loading {selectedDepartment.toLowerCase()} department employees...</p>
            <div className="progress-bar">
              <div 
                className="progress-fill" 
                style={{width: `${fetchProgress}%`}}
              ></div>
            </div>
            <p className="progress-text">{fetchProgress}% complete</p>
          </div>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="app">
        <div className="container">
          <div className="error">
            <h2>⚠️ Error</h2>
            <p>{error}</p>
            <button onClick={handleRefresh} className="refresh-btn">
              Try Again
            </button>
          </div>
        </div>
      </div>
    );
  }

    return (
    <div className="app">
      <div className="container">
        {/* Loading Overlay */}
        {loading && (
          <div className="loading-overlay">
            <div className="loading-content">
              <div className="loading-spinner">⏳</div>
              <p>Loading dashboard data...</p>
            </div>
          </div>
        )}
        
        <header className="header">
          <h1>🎨 {selectedDepartment} Department</h1>
          <p>{selectedDepartment} Department Dashboard</p>
          <button className="logout-btn" onClick={onLogout} title="Sign out">Logout</button>
          
          {/* Department Switch Buttons */}
          <div className="department-switch">
            <button 
              className={`department-btn ${selectedDepartment === 'Creative' ? 'active' : ''}`}
              onClick={() => handleDepartmentChange('Creative')}
            >
              Creative
            </button>
            <button 
              className={`department-btn ${selectedDepartment === 'Creative Strategy' ? 'active' : ''}`}
              onClick={() => handleDepartmentChange('Creative Strategy')}
            >
              Creative Strategy
            </button>
            <button 
              className={`department-btn ${selectedDepartment === 'Instructional Design' ? 'active' : ''}`}
              onClick={() => handleDepartmentChange('Instructional Design')}
            >
              Instructional Design
            </button>
          </div>
          

        </header>

        {/* Tab Navigation */}
        <div className="tab-navigation">
          {/* 1) Utilization first */}
          <button 
            className={`tab-btn ${activeTab === 'utilization' ? 'active' : ''}`}
            onClick={() => handleTabSwitch('utilization')}
          >
            📈 Utilization Dashboard
          </button>
          {/* 2) Client Dashboard (formerly External Hours) */}
          <button 
            className={`tab-btn ${activeTab === 'sales-order-hours' ? 'active' : ''}`}
            onClick={() => handleTabSwitch('sales-order-hours')}
          >
            📋 Client Dashboard
          </button>
          {/* 3) Creative Dashboard */}
          <button 
            className={`tab-btn ${activeTab === 'creative-dashboard' ? 'active' : ''}`}
            onClick={() => handleTabSwitch('creative-dashboard')}
          >
            🎨 Creative Dashboard
          </button>
          {/* Remaining tabs */}
          <button 
            className={`tab-btn ${activeTab === 'employees' ? 'active' : ''}`}
            onClick={() => handleTabSwitch('employees')}
          >
            👥 Number of {selectedDepartment === 'Creative Strategy' || selectedDepartment === 'Instructional Design' ? 'Team Members' : 'Creatives'} ({employees.filter(emp => emp.tags && emp.tags.length > 0).length})
          </button>
            <button 
            className={`tab-btn ${activeTab === 'resources' ? 'active' : ''}`}
            onClick={() => handleTabSwitch('resources')}
          >
            📊 Available {selectedDepartment === 'Creative Strategy' || selectedDepartment === 'Instructional Design' ? 'Team Members' : 'Creatives'} ({availableResources.filter(resource => resource.tags && resource.tags.length > 0).length})
          </button>
            <button 
            className={`tab-btn ${activeTab === 'timesheet' ? 'active' : ''}`}
            onClick={() => handleTabSwitch('timesheet')}
          >
            ⏱️ Active {selectedDepartment === 'Creative Strategy' || selectedDepartment === 'Instructional Design' ? 'Team Members' : 'Creatives'} ({activeTimesheetCount})
          </button>
        </div>

        {/* View Type and Period Selector for Resources, Timesheet, Sales Order Hours, and Utilization Tabs */}
        {(activeTab === 'resources' || activeTab === 'timesheet' || activeTab === 'sales-order-hours' || activeTab === 'utilization' || activeTab === 'creative-dashboard') && (
          <div className="view-selector">
            <div className="view-type-selector">
              <label htmlFor="view-type-select">View Type:</label>
              <select 
                id="view-type-select"
                value={viewType}
                onChange={(e) => handleViewTypeChange(e.target.value)}
                className="view-type-dropdown"
              >
                <option value="monthly">Monthly</option>
                <option value="weekly">Weekly</option>
                <option value="daily">Daily</option>
              </select>
            </div>
            
            <div className="period-selector">
              <label htmlFor="period-select">Select {viewType === 'monthly' ? 'Month' : viewType === 'weekly' ? 'Week' : 'Day'}:</label>
              <select
                value={selectedPeriod}
                onChange={(e) => handlePeriodChange(e.target.value)}
                className="period-dropdown"
              >
                {periodOptions.map(option => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </div>
          </div>
        )}

        {/* Tab Content */}
        {activeTab === 'employees' && (
          <div className="tab-content">
            <div className="stats">
              <div className="stat-card">
                <h3>Total Employees</h3>
                <p className="stat-number">{employees.filter(emp => emp.tags && emp.tags.length > 0).length}</p>
              </div>
            </div>

            {/* Creative Pools Section - Only show for Creative department */}
            {selectedDepartment === 'Creative' && (
              <div className="creative-pools-section">
                <h3>{selectedDepartment} Pools by Tags</h3>
                <div className="pools-grid">
                  {Object.entries(poolCounts).map(([poolName, count]) => (
                      <div 
                        key={poolName} 
                        className={`pool-card ${selectedPoolFilters.includes(poolName) ? 'active' : ''}`}
                        onClick={() => handlePoolFilterClick(poolName)}
                      >
                        <div className="pool-header">
                          <h4 className="pool-name">{poolName}</h4>
                        </div>
                        <div className="pool-count">
                          <span className="pool-number">{count}</span>
                          <span className="pool-label">{selectedDepartment === 'Creative Strategy' || selectedDepartment === 'Instructional Design' ? 'Team Members' : 'Creatives'}</span>
                        </div>
                        {selectedPoolFilters.includes(poolName) && (
                          <div className="pool-active-indicator">
                            <span>
                              {selectedPoolFilters.length === 1 ? '✓ Selected' : `✓ Selected (${selectedPoolFilters.indexOf(poolName) + 1}/${selectedPoolFilters.length})`}
                            </span>
                          </div>
                        )}
                      </div>
                    ))}
                
                </div>
                {selectedPoolFilters.length > 0 && (
                  <div className="filter-controls">
                    <button 
                      className="clear-filter-btn"
                      onClick={() => setSelectedPoolFilters([])}
                    >
                      ✕ Clear All Filters
                    </button>
                    <span className="filter-info">
                      Showing {filteredEmployees.length} of {employees.length} {selectedDepartment === 'Creative Strategy' || selectedDepartment === 'Instructional Design' ? 'team members' : 'employees'}
                      {selectedPoolFilters.length > 1 && (
                        <span className="filter-details">
                          {' '}({selectedPoolFilters.join(', ')} pools selected)
                        </span>
                      )}
                    </span>
                  </div>
                )}
              </div>
            )}

            <div className="employees-section">
              {filteredEmployees.length === 0 ? (
                <div className="no-employees">
                  <p>
                    {selectedPoolFilters.length > 0 
                      ? `No ${selectedDepartment === 'Creative Strategy' || selectedDepartment === 'Instructional Design' ? 'team members' : 'employees'} found with the selected pool tags: ${selectedPoolFilters.join(', ')}`
: `No ${selectedDepartment === 'Creative Strategy' || selectedDepartment === 'Instructional Design' ? 'team members' : 'employees'} found in the ${selectedDepartment} Department`
                    }
                  </p>
                </div>
              ) : (
                <div className="employees-grid">
                  {filteredEmployees.map((employee, index) => (
                    <div key={index} className="employee-card">
                      <div className="employee-avatar">
                        {employee.name.charAt(0).toUpperCase()}
                      </div>
                      <h3 className="employee-name">{employee.name}</h3>
                      {employee.job_title && (
                        <p className="employee-title">{employee.job_title}</p>
                      )}
                      {employee.email && (
                        <p className="employee-email">
                          <span className="email-icon">✉️</span>
                          {employee.email}
                        </p>
                      )}
                      {employee.tags && employee.tags.length > 0 && (
                        <div className="employee-tags">
                          {employee.tags.map((tag, tagIndex) => (
                            <span key={tagIndex} className="tag">
                              {tag}
                            </span>
                          ))}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        )}

        {activeTab === 'resources' && (
          <div className="tab-content">
            <div className="stats">
              <div className="stat-card">
                <h3>Available Resources</h3>
                <p className="stat-number">{availableResources.filter(resource => resource.tags && resource.tags.length > 0).length}</p>
              </div>
            </div>

            {/* Pool Statistics Section - Only show for Creative department */}
            {selectedDepartment === 'Creative' && (
              <div className="pool-statistics-section">
                <h3>Pool Statistics</h3>
                <div className="pool-stats-grid">
                  <>
                        {Object.entries(resourcePoolStats).map(([poolName, stats]) => (
                          <div 
                            key={poolName} 
                            className={`pool-stat-card ${selectedResourcePoolFilter === poolName ? 'active' : ''}`}
                            onClick={() => handleResourcePoolFilterClick(poolName)}
                          >
                            <div className="pool-stat-header">
                              <h4 className="pool-stat-name">{poolName}</h4>
                            </div>
                            <div className="pool-stat-content">
                              <div className="pool-stat-item">
                                <span className="pool-stat-label">Available Resources:</span>
                                <span className="pool-stat-value">{stats.availableResources}</span>
                              </div>
                              <div className="pool-stat-item">
                                <span className="pool-stat-label">Total Planned Hours:</span>
                                <span className="pool-stat-value">{formatDecimalHours(stats.totalPlannedHours || 0)}</span>
                              </div>
                              <div className="pool-stat-item">
                                <span className="pool-stat-label">Available Hours:</span>
                                <span className="pool-stat-value">{formatDecimalHours(stats.totalAvailableHours || 0)}</span>
                              </div>
                            </div>
                            {selectedResourcePoolFilter === poolName && (
                              <div className="pool-stat-active-indicator">
                                <span>✓ Active Filter</span>
                              </div>
                            )}
                          </div>
                        ))}
                        <div className="pool-stat-card total">
                          <div className="pool-stat-header">
                            <h4 className="pool-stat-name">All Pools</h4>
                          </div>
                          <div className="pool-stat-content">
                            <div className="pool-stat-item">
                              <span className="pool-stat-label">Total Planned Hours:</span>
                              <span className="pool-stat-value">{formatDecimalHours(totalResourceHours.totalPlannedHours || 0)}</span>
                            </div>
                            <div className="pool-stat-item">
                              <span className="pool-stat-label">Total Available Hours:</span>
                              <span className="pool-stat-value">{formatDecimalHours(totalResourceHours.totalAvailableHours || 0)}</span>
                            </div>
                          </div>
                        </div>
                  </>
                </div>
              </div>
            )}

            {/* Resource Filter Controls */}
            {selectedResourcePoolFilter && (
              <div className="resource-filter-controls">
                <button 
                  className="clear-resource-filter-btn"
                  onClick={() => setSelectedResourcePoolFilter(null)}
                >
                  ✕ Clear Filter
                </button>
                <span className="resource-filter-info">
                  Showing {filteredResources.length} of {availableResources.length} resources
                  {' '}({selectedResourcePoolFilter} pool selected)
                </span>
              </div>
            )}

            <div className="resources-section">
              {filteredResources.length === 0 ? (
                <div className="no-resources">
                  <p>
                    {selectedResourcePoolFilter 
                      ? `No resources found with the "${selectedResourcePoolFilter}" tag` 
                      : `No available ${selectedDepartment.toLowerCase()} resources found`
                    }
                  </p>
                </div>
              ) : (
                <div className="resources-grid">
                  {filteredResources.map((resource, index) => (
                    <div key={index} className="resource-card">
                      <div className="resource-avatar">
                        {resource.name.charAt(0).toUpperCase()}
                      </div>
                      <h3 className="resource-name">{resource.name}</h3>
                      {resource.job_title && (
                        <p className="resource-title">{resource.job_title}</p>
                      )}
                      <div className="resource-availability">
                        <div className="availability-bar">
                          <div 
                            className="availability-fill" 
                            style={{width: `${resource.allocated_percentage}%`}}
                          ></div>
                        </div>
                        <p className="availability-text">
                          {resource.planned_hours?.formatted || '0h'} / {resource.available_hours?.formatted || '0h'} ({Math.round(resource.allocated_percentage || 0)}%)
                        </p>
                        {(resource.time_off_hours?.decimal || 0) > 0 && (
                          <p className="time-off-info">
                            Time Off: {resource.time_off_hours?.formatted || '0h'}
                          </p>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        )}

        {activeTab === 'timesheet' && (
          <div className="tab-content">
            <div className="stats">
              <div className="stat-card">
                <h3>Active {selectedDepartment === 'Creative Strategy' || selectedDepartment === 'Instructional Design' ? 'Team Members' : 'Creatives'}</h3>
                <p className="stat-number">{activeTimesheetCount}</p>
              </div>
              <div className="stat-card">
                <h3>Total Logged Hours</h3>
                <p className="stat-number">{formatDecimalHours(totalLoggedHours)}</p>
              </div>
            </div>

            {/* Timesheet Pool Statistics Section - Only show for Creative department */}
            {selectedDepartment === 'Creative' && (
              <div className="timesheet-pool-statistics-section">
                <h3>Pool Statistics</h3>
                <div className="timesheet-pool-stats-grid">
                  {(() => {
                    // Calculate pool statistics for timesheet data
                    const timesheetPoolStats = {
                      'KSA': {
                        activeCreatives: timesheetData.filter(employee => 
                          employee.tags && employee.tags.some(tag => tag.trim().toLowerCase() === 'ksa') && (employee.total_hours?.decimal || 0) > 0
                        ).length,
                        totalLoggedHours: timesheetData.filter(employee => 
                          employee.tags && employee.tags.some(tag => tag.trim().toLowerCase() === 'ksa')
                        ).reduce((total, employee) => total + (employee.total_hours?.decimal || 0), 0)
                      },
                      'UAE': {
                        activeCreatives: timesheetData.filter(employee => 
                          employee.tags && employee.tags.some(tag => tag.trim().toLowerCase() === 'uae') && (employee.total_hours?.decimal || 0) > 0
                        ).length,
                        totalLoggedHours: timesheetData.filter(employee => 
                          employee.tags && employee.tags.some(tag => tag.trim().toLowerCase() === 'uae')
                        ).reduce((total, employee) => total + (employee.total_hours?.decimal || 0), 0)
                      },
                      'Nightshift': {
                        activeCreatives: timesheetData.filter(employee => 
                          employee.tags && employee.tags.some(tag => tag.trim().toLowerCase() === 'nightshift') && (employee.total_hours?.decimal || 0) > 0
                        ).length,
                        totalLoggedHours: timesheetData.filter(employee => 
                          employee.tags && employee.tags.some(tag => tag.trim().toLowerCase() === 'nightshift')
                        ).reduce((total, employee) => total + (employee.total_hours?.decimal || 0), 0)
                      }
                    };

                    // Calculate total logged hours for KSA, UAE, and Nightshift pools only
                    const totalLoggedHours = timesheetPoolStats.KSA.totalLoggedHours + 
                                           timesheetPoolStats.UAE.totalLoggedHours + 
                                           timesheetPoolStats.Nightshift.totalLoggedHours;

                    return (
                      <>
                        {Object.entries(timesheetPoolStats).map(([poolName, stats]) => (
                          <div 
                            key={poolName} 
                            className={`timesheet-pool-stat-card ${selectedTimesheetPoolFilter === poolName ? 'active' : ''}`}
                            onClick={() => handleTimesheetPoolFilterClick(poolName)}
                          >
                            <div className="timesheet-pool-stat-header">
                              <h4 className="timesheet-pool-stat-name">{poolName}</h4>
                            </div>
                            <div className="timesheet-pool-stat-content">
                              <div className="timesheet-pool-stat-item">
                                <span className="timesheet-pool-stat-label">Active {selectedDepartment === 'Creative Strategy' || selectedDepartment === 'Instructional Design' ? 'Team Members' : 'Creatives'}:</span>
                                <span className="timesheet-pool-stat-value">{stats.activeCreatives}</span>
                              </div>
                              <div className="timesheet-pool-stat-item">
                                <span className="timesheet-pool-stat-label">Logged Hours:</span>
                                <span className="timesheet-pool-stat-value">{(() => {
                                  const decimal = stats.totalLoggedHours || 0;
                                  const hours = Math.floor(decimal);
                                  const minutes = Math.round((decimal - hours) * 60);
                                  if (minutes === 0) return hours === 0 ? '0h' : `${hours}h`;
                                  if (hours === 0) return `${minutes}m`;
                                  return `${hours}h ${minutes}m`;
                                })()}</span>
                              </div>
                            </div>
                            {selectedTimesheetPoolFilter === poolName && (
                              <div className="timesheet-pool-stat-active-indicator">
                                <span>✓ Active Filter</span>
                              </div>
                            )}
                          </div>
                        ))}
                        <div className="timesheet-pool-stat-card total">
                          <div className="timesheet-pool-stat-header">
                            <h4 className="timesheet-pool-stat-name">All Pools</h4>
                          </div>
                          <div className="timesheet-pool-stat-content">
                            <div className="timesheet-pool-stat-item">
                              <span className="timesheet-pool-stat-label">Total Logged Hours:</span>
                              <span className="timesheet-pool-stat-value">{(() => {
                                const decimal = totalLoggedHours || 0;
                                const hours = Math.floor(decimal);
                                const minutes = Math.round((decimal - hours) * 60);
                                if (minutes === 0) return hours === 0 ? '0h' : `${hours}h`;
                                if (hours === 0) return `${minutes}m`;
                                return `${hours}h ${minutes}m`;
                              })()}</span>
                            </div>
                          </div>
                        </div>
                      </>
                    );
                  })()}
                </div>
              </div>
            )}

            {/* Timesheet Filter Controls */}
            {selectedTimesheetPoolFilter && (
              <div className="timesheet-filter-controls">
                <button 
                  className="clear-timesheet-filter-btn"
                  onClick={() => setSelectedTimesheetPoolFilter(null)}
                >
                  ✕ Clear Filter
                </button>
                <span className="timesheet-filter-info">
                  Showing {filteredTimesheetData.length} of {timesheetData.length} employees
                  {' '}({selectedTimesheetPoolFilter} pool selected)
                </span>
              </div>
            )}

            <div className="timesheet-section">
              {filteredTimesheetData.length === 0 ? (
                <div className="no-timesheet">
                  <p>
                    {selectedTimesheetPoolFilter 
                      ? `No employees found with the "${selectedTimesheetPoolFilter}" tag` 
                      : 'No timesheet data found for the selected period'
                    }
                  </p>
                </div>
              ) : (
                <div className="timesheet-grid">
                  {filteredTimesheetData.map((employee, index) => (
                    <div key={index} className="timesheet-card">
                      <div className="timesheet-header">
                        <div className="timesheet-info">
                          <h3 className="timesheet-name">{employee.name}</h3>
                        </div>
                        <div className="hours-group">
                          <div className="hour-block">
                            <span className="hours-number">{employee.total_hours?.formatted || '0h'}</span>
                            <span className="hours-label">Logged</span>
                          </div>
                          <div className="hour-block">
                            <span className="hours-number">{(() => {
                              const totalDecimal = employee.total_hours?.decimal || 0;
                              const unbilledDecimal = employee.unbilled_hours?.decimal || 0;
                              const billedDecimal = Math.max(0, totalDecimal - unbilledDecimal);
                              const hours = Math.floor(billedDecimal);
                              const minutes = Math.round((billedDecimal - hours) * 60);
                              if (minutes === 0) return hours === 0 ? '0h' : `${hours}h`;
                              if (hours === 0) return `${minutes}m`;
                              return `${hours}h ${minutes}m`;
                            })()}</span>
                            <span className="hours-label">Billed</span>
                          </div>
                          {typeof employee.unbilled_hours !== 'undefined' && (
                            <div className="hour-block">
                              <span className="hours-number">{employee.unbilled_hours?.formatted || '0h'}</span>
                              <span className="hours-label">Unbilled</span>
                            </div>
                          )}
                        </div>
                      </div>
                      
                      {employee.timesheet_entries && employee.timesheet_entries.length > 0 && (
                        <div className="timesheet-entries">
                          <div className="entries-list">
                            {employee.timesheet_entries.slice(0, 3).map((entry, entryIndex) => (
                              <div key={entryIndex} className="entry-item">
                                <span className="entry-hours">{entry.hours?.formatted || '0h'}</span>
                                <span className="entry-task">{entry.task || 'No Task'}</span>
                                <span className="entry-date">{entry.date || 'No Date'}</span>
                              </div>
                            ))}
                            {employee.timesheet_entries.length > 3 && (
                              <div className="more-entries">
                                +{employee.timesheet_entries.length - 3} more entries
                              </div>
                            )}
                          </div>
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        )}



        {activeTab === 'sales-order-hours' && (
          <div className="tab-content">
            <div className="external-hours-dashboard">
              <h2>Client Dashboard</h2>
              <p>Calculate external hours from sales orders (adhoc, framework, and strategy) after July 1st for KSA and UAE markets.</p>
              
              {loading ? (
                <div className="loading-message">Loading sales order data...</div>
              ) : salesOrderHoursData ? (
                <div>
              {salesOrderHoursData.source && (
                <div className="data-source-indicator">
                  <span className={`source-badge ${salesOrderHoursData.source}`}>
                    📊 Data Source: {salesOrderHoursData.source === 'google_sheets' ? 'Google Sheets' : 'Odoo'}
                  </span>
                </div>
              )}
              
              <div className="external-hours-stats">
                <div className="pool-stats">
                  <div className="pool-stat-card">
                    <h3>KSA Pool</h3>
                    <div className="stat-number">
                      {formatDecimalHours(salesOrderHoursData.ksa?.totalHours || 0)} 
                      <span className="percentage-contribution">
                        ({(() => {
                          const ksaHours = salesOrderHoursData.ksa?.totalHours || 0;
                          const uaeHours = salesOrderHoursData.uae?.totalHours || 0;
                          const totalHours = ksaHours + uaeHours;
                          return totalHours > 0 ? ((ksaHours / totalHours) * 100).toFixed(1) : '0.0';
                        })()}%)
                      </span>
                    </div>
                    <div className="amount-total">
                      {formatAEDAmount(salesOrderHoursData.ksa?.totalAmountAED || 0)} 
                      <span className="percentage-contribution">
                        ({(() => {
                          const ksaAmountAED = salesOrderHoursData.ksa?.totalAmountAED || 0;
                          const uaeAmountAED = salesOrderHoursData.uae?.totalAmountAED || 0;
                          const totalAmountAED = ksaAmountAED + uaeAmountAED;
                          return totalAmountAED > 0 ? ((ksaAmountAED / totalAmountAED) * 100).toFixed(1) : '0.0';
                        })()}%)
                      </span>
                    </div>
                    <div className="orders-count">
                      {salesOrderHoursData.ksa?.orders?.reduce((total, customer) => total + (customer.orders?.length || 0), 0) || 0} Sales Orders 
                      <span className="percentage-contribution">
                        ({(() => {
                          const ksaOrders = salesOrderHoursData.ksa?.orders?.reduce((total, customer) => total + (customer.orders?.length || 0), 0) || 0;
                          const uaeOrders = salesOrderHoursData.uae?.orders?.reduce((total, customer) => total + (customer.orders?.length || 0), 0) || 0;
                          const totalOrders = ksaOrders + uaeOrders;
                          return totalOrders > 0 ? ((ksaOrders / totalOrders) * 100).toFixed(1) : '0.0';
                        })()}%)
                      </span>
                    </div>
                  </div>
                  
                  <div className="pool-stat-card">
                    <h3>UAE Pool</h3>
                    <div className="stat-number">
                      {formatDecimalHours(salesOrderHoursData.uae?.totalHours || 0)} 
                      <span className="percentage-contribution">
                        ({(() => {
                          const ksaHours = salesOrderHoursData.ksa?.totalHours || 0;
                          const uaeHours = salesOrderHoursData.uae?.totalHours || 0;
                          const totalHours = ksaHours + uaeHours;
                          return totalHours > 0 ? ((uaeHours / totalHours) * 100).toFixed(1) : '0.0';
                        })()}%)
                      </span>
                    </div>
                    <div className="amount-total">
                      {formatAEDAmount(salesOrderHoursData.uae?.totalAmountAED || 0)} 
                      <span className="percentage-contribution">
                        ({(() => {
                          const ksaAmountAED = salesOrderHoursData.ksa?.totalAmountAED || 0;
                          const uaeAmountAED = salesOrderHoursData.uae?.totalAmountAED || 0;
                          const totalAmountAED = ksaAmountAED + uaeAmountAED;
                          return totalAmountAED > 0 ? ((uaeAmountAED / totalAmountAED) * 100).toFixed(1) : '0.0';
                        })()}%)
                      </span>
                    </div>
                    <div className="orders-count">
                      {salesOrderHoursData.uae?.orders?.reduce((total, customer) => total + (customer.orders?.length || 0), 0) || 0} Sales Orders 
                      <span className="percentage-contribution">
                        ({(() => {
                          const ksaOrders = salesOrderHoursData.ksa?.orders?.reduce((total, customer) => total + (customer.orders?.length || 0), 0) || 0;
                          const uaeOrders = salesOrderHoursData.uae?.orders?.reduce((total, customer) => total + (customer.orders?.length || 0), 0) || 0;
                          const totalOrders = ksaOrders + uaeOrders;
                          return totalOrders > 0 ? ((uaeOrders / totalOrders) * 100).toFixed(1) : '0.0';
                        })()}%)
                      </span>
                    </div>
                  </div>
                  
                  <div className="pool-stat-card total-stat">
                    <h3>📊 Total</h3>
                    <div className="stat-number">
                      {formatDecimalHours((salesOrderHoursData.ksa?.totalHours || 0) + (salesOrderHoursData.uae?.totalHours || 0))}
                    </div>
                    <div className="stat-label">Combined External Hours</div>
                    <div className="amount-total">
                      {formatAEDAmount((salesOrderHoursData.ksa?.totalAmountAED || 0) + (salesOrderHoursData.uae?.totalAmountAED || 0))} Total Revenue
                    </div>
                    <div className="orders-count">
                      {(salesOrderHoursData.ksa?.orders?.reduce((total, customer) => total + (customer.orders?.length || 0), 0) || 0) + (salesOrderHoursData.uae?.orders?.reduce((total, customer) => total + (customer.orders?.length || 0), 0) || 0)} Total Sales Orders
                    </div>
                  </div>
                </div>
                
                {/* Top Clients Section */}
                <div className="top-clients-section">
                  <h3>🏆 Top Clients</h3>
                  <div className="top-clients-grid">
                    {/* Top Clients by Hours */}
                    <div className="top-clients-category">
                      <h4>⏱️ Highest External Hours</h4>
                      <div className="top-clients-list">
                        {salesOrderHoursData.top_clients?.by_hours?.length > 0 ? 
                          salesOrderHoursData.top_clients.by_hours.map((client, index) => (
                            <div key={index} className="top-client-item">
                              <div className="client-rank">#{index + 1}</div>
                              <div className="client-info">
                                <div className="client-name">{client.customer_name}</div>
                                <div className="client-market">{client.market}</div>
                              </div>
                              <div className="client-metric">
                                {formatDecimalHours(client.total_hours)}
                              </div>
                            </div>
                          )) : (
                            <div className="no-data-message">
                              No client data available
                            </div>
                          )
                        }
                      </div>
                    </div>
                    
                    {/* Top Clients by Revenue */}
                    <div className="top-clients-category">
                      <h4>💰 Highest Revenue</h4>
                      <div className="top-clients-list">
                        {salesOrderHoursData.top_clients?.by_revenue?.length > 0 ? 
                          salesOrderHoursData.top_clients.by_revenue.map((client, index) => (
                            <div key={index} className="top-client-item">
                              <div className="client-rank">#{index + 1}</div>
                              <div className="client-info">
                                <div className="client-name">{client.customer_name}</div>
                                <div className="client-market">{client.market}</div>
                              </div>
                              <div className="client-metric">
                                {formatAEDAmount(client.total_revenue_aed)}
                              </div>
                            </div>
                          )) : (
                            <div className="no-data-message">
                              No client data available
                            </div>
                          )
                        }
                      </div>
                    </div>
                    
                    {/* Top Clients by Sales Orders */}
                    <div className="top-clients-category">
                      <h4>📋 Most Sales Orders</h4>
                      <div className="top-clients-list">
                        {salesOrderHoursData.top_clients?.by_orders?.length > 0 ? 
                          salesOrderHoursData.top_clients.by_orders.map((client, index) => (
                            <div key={index} className="top-client-item">
                              <div className="client-rank">#{index + 1}</div>
                              <div className="client-info">
                                <div className="client-name">{client.customer_name}</div>
                                <div className="client-market">{client.market}</div>
                              </div>
                              <div className="client-metric">
                                {client.sales_orders_count} orders
                              </div>
                            </div>
                          )) : (
                            <div className="no-data-message">
                              No client data available
                            </div>
                          )
                        }
                      </div>
                    </div>
                  </div>
                </div>
                
                <div className="orders-details">
                  <h3>Customer Sales Order Details</h3>
                  <div className="orders-grid">
                    {/* KSA Orders */}
                    <div className="orders-section">
                      <h4 className="region-header clickable" onClick={() => toggleRegionCollapse('ksa')}>
                        <span className="region-collapse-indicator">
                          {(salesOrderHoursData.ksa?.orders?.length > 0 && salesOrderHoursData.ksa.orders.every((_, index) => collapsedInvoiceAddresses[`ksa-${index}`])) ? '▶' : '▼'}
                        </span>
                        🇸🇦 KSA Sales Orders ({salesOrderHoursData.ksa?.orders?.reduce((total, customer) => total + (customer.orders?.length || 0), 0) || 0})
                      </h4>
                      {(salesOrderHoursData.ksa?.orders?.length || 0) === 0 ? (
                        <div className="no-orders">No KSA customers found with sales orders after July 1st</div>
                      ) : (
                        <div className="orders-list">
                          {salesOrderHoursData.ksa?.orders?.map((customer, index) => {
                            const addressKey = `ksa-${index}`;
                            const isCollapsed = collapsedInvoiceAddresses[addressKey];
                            
                            return (
                            <div key={index} className="customer-card">
                                <div className="customer-header clickable" onClick={() => toggleInvoiceAddressCollapse(addressKey)}>
                                  <div className="customer-info">
                                    <span className="collapse-indicator">
                                      {isCollapsed ? '▶' : '▼'}
                                    </span>
                                <h5>{customer.customer_name}</h5>
                                  </div>
                                  <div className="customer-meta">
                                <span className="customer-total-hours">{formatDecimalHours(customer.total_hours || 0)} hours</span>
                                    <span className="customer-total-amount">{formatPoolCurrencies(customer.currencies || {})}</span>
                                    <span className="orders-count">{customer.orders?.length || 0} orders</span>
                              </div>
                                </div>
                                {!isCollapsed && (
                              <div className="customer-orders">
                                <div className="orders-summary">
                                  {customer.orders?.map((order, orderIndex) => (
                                    <div key={orderIndex} className="order-summary">
                                      <span className="order-name">{order.order_name}</span>
                                      <span className="order-hours">{formatDecimalHours(order.total_hours || 0)}h</span>
                                          <span className="order-amount">{formatCurrencyAmount(order.amount_total || 0, order.currency)}</span>
                                    </div>
                                  ))}
                                </div>
                              </div>
                                )}
                            </div>
                            );
                          })}
                        </div>
                      )}
                    </div>
                    
                    {/* UAE Orders */}
                    <div className="orders-section">
                      <h4 className="region-header clickable" onClick={() => toggleRegionCollapse('uae')}>
                        <span className="region-collapse-indicator">
                          {(salesOrderHoursData.uae?.orders?.length > 0 && salesOrderHoursData.uae.orders.every((_, index) => collapsedInvoiceAddresses[`uae-${index}`])) ? '▶' : '▼'}
                        </span>
                        🇦🇪 UAE Sales Orders ({salesOrderHoursData.uae?.orders?.reduce((total, customer) => total + (customer.orders?.length || 0), 0) || 0})
                      </h4>
                      {(salesOrderHoursData.uae?.orders?.length || 0) === 0 ? (
                        <div className="no-orders">No UAE customers found with sales orders after July 1st</div>
                      ) : (
                        <div className="orders-list">
                          {salesOrderHoursData.uae?.orders?.map((customer, index) => {
                            const addressKey = `uae-${index}`;
                            const isCollapsed = collapsedInvoiceAddresses[addressKey];
                            
                            return (
                            <div key={index} className="customer-card">
                                <div className="customer-header clickable" onClick={() => toggleInvoiceAddressCollapse(addressKey)}>
                                  <div className="customer-info">
                                    <span className="collapse-indicator">
                                      {isCollapsed ? '▶' : '▼'}
                                    </span>
                                <h5>{customer.customer_name}</h5>
                                  </div>
                                  <div className="customer-meta">
                                <span className="customer-total-hours">{formatDecimalHours(customer.total_hours || 0)} hours</span>
                                    <span className="customer-total-amount">{formatPoolCurrencies(customer.currencies || {})}</span>
                                    <span className="orders-count">{customer.orders?.length || 0} orders</span>
                              </div>
                                </div>
                                {!isCollapsed && (
                              <div className="customer-orders">
                                <div className="orders-summary">
                                  {customer.orders?.map((order, orderIndex) => (
                                    <div key={orderIndex} className="order-summary">
                                      <span className="order-name">{order.order_name}</span>
                                      <span className="order-hours">{formatDecimalHours(order.total_hours || 0)}h</span>
                                          <span className="order-amount">{formatCurrencyAmount(order.amount_total || 0, order.currency)}</span>
                                    </div>
                                  ))}
                                </div>
                              </div>
                                )}
                            </div>
                            );
                          })}
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              </div>
            </div>
              ) : (
                <div className="loading-message">No data available</div>
              )}
            </div>
          </div>
        )}

        {activeTab === 'creative-dashboard' && (
          <div className="tab-content">
            <div className="creative-dashboard">
              <h2>Creative Dashboard</h2>
              <p>Overview of creative team members with their availability and activity status.</p>
              
              {loading ? (
                <div className="loading-message">Loading creative data...</div>
              ) : (
                <>
                <div className="creative-stats">
                  <div className="creative-stat-card">
                    <h3>📊 Total Creatives</h3>
                    <div className="stat-number">
                      {employees.filter(emp => emp.tags && emp.tags.length > 0).length}
                    </div>
                    <div className="stat-label">All Creative Team Members</div>
                  </div>
                  
                  <div className="creative-stat-card">
                    <h3>✅ Available Creatives</h3>
                    <div className="stat-number">
                      {employees.filter(emp => emp.tags && emp.tags.length > 0 && emp.available === true).length}
                    </div>
                    <div className="stat-label">Currently Available</div>
                  </div>
                  
                  <div className="creative-stat-card">
                    <h3>🔥 Active Creatives</h3>
                    <div className="stat-number">
                      {(() => {
                        // Use the same calculation as the Active Creatives tab
                        // Count employees with logged hours > 0 AND tags from timesheetData
                        return timesheetData.filter(employee => 
                          (employee.total_hours?.decimal || 0) > 0 && 
                          employee.tags && 
                          employee.tags.length > 0
                        ).length;
                      })()}
                    </div>
                    <div className="stat-label">Currently Active</div>
                  </div>
                </div>

                {/* Creative Dashboard Bar Chart */}
                <div className="creative-bar-chart-section">
                  <h3>Hours Overview</h3>
                  <div className="creative-bar-chart">
                    <div className="chart-container">
                      {(() => {
                        // Calculate actual values using the SAME pool filters as the tabs (KSA, UAE, Nightshift)
                        const isPoolTag = (tag) => ['ksa', 'uae', 'nightshift'].includes((tag || '').trim().toLowerCase());
                        const poolAvailableResources = availableResources.filter(r => r.tags && r.tags.some(isPoolTag));
                        const poolTimesheets = timesheetData.filter(e => e.tags && e.tags.some(isPoolTag));

                        const toDec = (v) => (typeof v === 'object' ? (v?.decimal || 0) : (v || 0));
                        const availableHours = poolAvailableResources.reduce((total, r) => total + toDec(r.available_hours), 0);
                        const plannedHours = poolAvailableResources.reduce((total, r) => total + toDec(r.planned_hours), 0);
                        const loggedHours = poolTimesheets.reduce((total, e) => total + toDec(e.total_hours), 0);
                        
                        // Calculate max value for scaling
                        const maxValue = Math.max(availableHours, plannedHours, loggedHours);
                        const scale = maxValue > 0 ? maxValue : 1000;
                        
                        // Calculate Y-axis labels based on scale
                        const yAxisMax = Math.ceil(scale / 1000) * 1000; // Round up to nearest 1000
                        const step = yAxisMax / 5;
                        
                        return (
                          <>
                            {/* Y-axis */}
                            <div className="y-axis">
                              <div className="y-label">{yAxisMax}h</div>
                              <div className="y-label">{Math.round(yAxisMax * 0.8)}h</div>
                              <div className="y-label">{Math.round(yAxisMax * 0.6)}h</div>
                              <div className="y-label">{Math.round(yAxisMax * 0.4)}h</div>
                              <div className="y-label">{Math.round(yAxisMax * 0.2)}h</div>
                              <div className="y-label">0h</div>
                            </div>
                            
                            {/* Chart area */}
                            <div className="chart-area">
                              {/* Grid lines */}
                              <div className="grid-lines">
                                <div className="grid-line"></div>
                                <div className="grid-line"></div>
                                <div className="grid-line"></div>
                                <div className="grid-line"></div>
                                <div className="grid-line"></div>
                                <div className="grid-line"></div>
                              </div>
                              
                              {/* Bars */}
                              <div className="bars">
                                {/* Bar Tooltip */}
                                <div className="bar-tooltip" style={{ display: 'none' }}></div>
                                
                                {/* Available Hours Bar */}
                                <div className="bar available" style={{
                                  height: `${(availableHours / yAxisMax) * 100}%`
                                }}
                          onMouseEnter={(e) => {
                            const tooltip = e.target.closest('.bars').querySelector('.bar-tooltip');
                            if (tooltip) {
                              tooltip.innerHTML = `
                                <div class="tooltip-title">Available Hours</div>
                                <div class="tooltip-value">${availableHours.toFixed(0)} hours</div>
                              `;
                              tooltip.style.display = 'block';
                              tooltip.style.left = e.target.offsetLeft + e.target.offsetWidth/2 - tooltip.offsetWidth/2 + 'px';
                              tooltip.style.top = e.target.offsetTop - tooltip.offsetHeight - 10 + 'px';
                            }
                          }}
                          onMouseLeave={(e) => {
                            const tooltip = e.target.closest('.bars').querySelector('.bar-tooltip');
                            if (tooltip) {
                              tooltip.style.display = 'none';
                            }
                          }}>
                            <span className="bar-value">
                              {availableHours.toFixed(0)}h
                            </span>
                          </div>
                          
                          {/* Planned Hours Bar */}
                          <div className="bar planned" style={{
                            height: `${(plannedHours / yAxisMax) * 100}%`
                          }}
                          onMouseEnter={(e) => {
                            const tooltip = e.target.closest('.bars').querySelector('.bar-tooltip');
                            if (tooltip) {
                              tooltip.innerHTML = `
                                <div class="tooltip-title">Planned Hours</div>
                                <div class="tooltip-value">${plannedHours.toFixed(0)} hours</div>
                              `;
                              tooltip.style.display = 'block';
                              tooltip.style.left = e.target.offsetLeft + e.target.offsetWidth/2 - tooltip.offsetWidth/2 + 'px';
                              tooltip.style.top = e.target.offsetTop - tooltip.offsetHeight - 10 + 'px';
                            }
                          }}
                          onMouseLeave={(e) => {
                            const tooltip = e.target.closest('.bars').querySelector('.bar-tooltip');
                            if (tooltip) {
                              tooltip.style.display = 'none';
                            }
                          }}>
                            <span className="bar-value">
                              {plannedHours.toFixed(0)}h
                            </span>
                          </div>
                          
                          {/* Logged Hours Bar */}
                          <div className="bar logged" style={{
                            height: `${(loggedHours / yAxisMax) * 100}%`
                          }}
                          onMouseEnter={(e) => {
                            const tooltip = e.target.closest('.bars').querySelector('.bar-tooltip');
                            if (tooltip) {
                              tooltip.innerHTML = `
                                <div class="tooltip-title">Logged Hours</div>
                                <div class="tooltip-value">${loggedHours.toFixed(0)} hours</div>
                              `;
                              tooltip.style.display = 'block';
                              tooltip.style.left = e.target.offsetLeft + e.target.offsetWidth/2 - tooltip.offsetWidth/2 + 'px';
                              tooltip.style.top = e.target.offsetTop - tooltip.offsetHeight - 10 + 'px';
                            }
                          }}
                          onMouseLeave={(e) => {
                            const tooltip = e.target.closest('.bars').querySelector('.bar-tooltip');
                            if (tooltip) {
                              tooltip.style.display = 'none';
                            }
                          }}>
                            <span className="bar-value">
                              {loggedHours.toFixed(0)}h
                            </span>
                          </div>
                        </div>
                      </div>
                          </>
                        );
                      })()}
                    </div>
                      
                    {/* Legend */}
                    <div className="chart-legend">
                      <div className="legend-title">Hours Breakdown</div>
                      <div className="legend-items">
                        {(() => {
                          // Calculate values for legend using the SAME pool filter as tabs
                          const isPoolTag = (tag) => ['ksa', 'uae', 'nightshift'].includes((tag || '').trim().toLowerCase());
                          const poolAvailableResources = availableResources.filter(r => r.tags && r.tags.some(isPoolTag));
                          const poolTimesheets = timesheetData.filter(e => e.tags && e.tags.some(isPoolTag));

                          const availableHours = poolAvailableResources.reduce((total, r) => total + (r.available_hours?.decimal || 0), 0);
                          const plannedHours = poolAvailableResources.reduce((total, r) => total + (r.planned_hours?.decimal || 0), 0);
                          const loggedHours = poolTimesheets.reduce((total, e) => total + (e.total_hours?.decimal || 0), 0);
                          
                          return (
                            <>
                              <div className="legend-item">
                                <div className="legend-color available"></div>
                                <div className="legend-label">Available Hours</div>
                                <div className="legend-value">
                                  {availableHours.toFixed(0)}h
                                </div>
                              </div>
                              <div className="legend-item">
                                <div className="legend-color planned"></div>
                                <div className="legend-label">Planned Hours</div>
                                <div className="legend-value">
                                  {plannedHours.toFixed(0)}h
                                </div>
                              </div>
                              <div className="legend-item">
                                <div className="legend-color logged"></div>
                                <div className="legend-label">Logged Hours</div>
                                <div className="legend-value">
                                  {loggedHours.toFixed(0)}h
                                </div>
                              </div>
                            </>
                          );
                        })()}
                      </div>
                    </div>
                  </div>
                </div>

                {/* Pool statistics under bar chart */}
                <div className="creative-pools-section">
                  <h3>Pool Statistics</h3>
                  <div className="creative-pools-grid">
                    {['KSA','UAE','Nightshift'].map((poolName) => {
                      const lower = poolName.toLowerCase();
                      const resources = availableResources.filter(r => r.tags && r.tags.some(t => t.trim().toLowerCase() === lower));
                      const employeesInPool = employees.filter(e => e.tags && e.tags.some(t => t.trim().toLowerCase() === lower));
                      const activeInPool = timesheetData.filter(e => e.tags && e.tags.some(t => t.trim().toLowerCase() === lower) && (e.total_hours?.decimal || 0) > 0);

                      const toDec = (v) => (typeof v === 'object' ? (v?.decimal || 0) : (v || 0));
                      const availableHours = resources.reduce((sum, r) => sum + toDec(r.available_hours), 0);
                      const plannedHours = resources.reduce((sum, r) => sum + toDec(r.planned_hours), 0);
                      const loggedHours = timesheetData.filter(e => e.tags && e.tags.some(t => t.trim().toLowerCase() === lower))
                        .reduce((sum, e) => sum + toDec(e.total_hours), 0);
                      const externalHours = loggedHours;

                      const isOpen = expandedPools[poolName];

                      return (
                        <div key={poolName} className={`creative-pool-card ${isOpen ? 'open' : ''}`} onClick={() => togglePoolExpand(poolName)}>
                          <div className="pool-card-header">
                            <div className="pool-card-title">
                              <span className="chevron">{isOpen ? '▼' : '▶'}</span>
                              <span>{poolName}</span>
                            </div>
                            <div className="pool-card-summary">
                              <span className="summary-item">Total: {employeesInPool.length}</span>
                              <span className="summary-sep">•</span>
                              <span className="summary-item">Available: {resources.length}</span>
                              <span className="summary-sep">•</span>
                              <span className="summary-item">Active: {activeInPool.length}</span>
                            </div>
                          </div>
                          {isOpen && (
                            <div className="pool-card-body" onClick={(e) => e.stopPropagation()}>
                              <div className="pool-metrics-row">
                                <div className="metric"><span className="label">Available Hours</span><span className="value">{availableHours.toFixed(0)}h</span></div>
                                <div className="metric"><span className="label">External Hours</span><span className="value">{externalHours.toFixed(0)}h</span></div>
                                <div className="metric"><span className="label">Planned Hours</span><span className="value">{plannedHours.toFixed(0)}h</span></div>
                              </div>
                              <div className="pool-metrics-row">
                                <div className="metric"><span className="label">Logged Hours</span><span className="value">{loggedHours.toFixed(0)}h</span></div>
                                <div className="metric"><span className="label">Total Designers</span><span className="value">{employeesInPool.length}</span></div>
                                <div className="metric"><span className="label">Active Designers</span><span className="value">{activeInPool.length}</span></div>
                              </div>
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                </div>

                {/* Designer cards */}
                <div className="designer-cards-section">
                  <div className="designer-header-row">
                    <h3>Designers</h3>
                    <div className="designer-pool-filters">
                      {['All','KSA','UAE','Nightshift'].map((p) => (
                        <button
                          key={p}
                          className={`designer-pool-chip ${designerPoolFilter === p ? 'active' : ''}`}
                          onClick={() => setDesignerPoolFilter(p)}
                        >
                          {p}
                        </button>
                      ))}
                    </div>
                  </div>
                  <div className="designer-cards-grid">
                    {employees
                      .filter(emp => emp.tags && emp.tags.some(tag => ['ksa','uae','nightshift'].includes(tag.trim().toLowerCase())))
                      .filter(emp => {
                        if (designerPoolFilter === 'All') return true;
                        return emp.tags && emp.tags.some(tag => tag.trim().toLowerCase() === designerPoolFilter.toLowerCase());
                      })
                      .map((emp, idx) => {
                      const name = emp.name || 'Unnamed';
                      const title = emp.job_title || 'Designer';
                      // Find matching resource record for available/planned
                      const lowerTags = (emp.tags || []).map(t => t.trim().toLowerCase());
                      const resource = availableResources.find(r => r.name === name || (r.tags && r.tags.some(t => lowerTags.includes(t.trim().toLowerCase()))));
                      const available = resource?.available_hours?.decimal ?? resource?.available_hours ?? 0;
                      const planned = resource?.planned_hours?.decimal ?? resource?.planned_hours ?? (emp.planned_hours ?? 0);
                      // Logged hours should come from the timesheet dataset
                      const logged = getLoggedHoursForName(name);
                      const activeUtil = available > 0 ? (logged / available) * 100 : 0;
                      const hypoUtil = available > 0 ? (planned / available) * 100 : 0;

                      return (
                        <div key={idx} className="designer-card">
                          <div className="designer-header">
                            <div className="designer-name">{name}</div>
                            <div className="designer-title">{title}</div>
                          </div>
                          <div className="designer-hours" title="Available Hours: base hours minus time off and holidays; Planned Hours: allocation from planning; Logged Hours: timesheet entries > 0">
                            <span className="small-metric">Avail: {formatDecimalHours(+available || 0)}</span>
                            <span className="sep">•</span>
                            <span className="small-metric">Planned: {formatDecimalHours(+planned || 0)}</span>
                            <span className="sep">•</span>
                            <span className="small-metric">Logged: {formatDecimalHours(+logged || 0)}</span>
                          </div>
                          <div className="designer-utilization">
                            <div className="util-item" title="Active Utilization = Logged / Available * 100">
                              <span className="util-label">Active Utilization</span>
                              <span className="util-value">{activeUtil.toFixed(1)}%</span>
                            </div>
                            <div className="util-item" title="Hypothetical Utilization = Planned / Available * 100">
                              <span className="util-label">Hypothetical Utilization</span>
                              <span className="util-value">{hypoUtil.toFixed(1)}%</span>
                            </div>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
                </>
              )}
            </div>
          </div>
        )}

        {activeTab === 'utilization' && (
          <div className="tab-content">
            <div className="utilization-dashboard">
              {/* Company Utilization Overview Section - Only for Creative department */}
              {selectedDepartment === 'Creative' && (
                <div className="company-utilization-section">
                  <div className="company-utilization-header">
                    <h3>Company Utilization Overview</h3>
                  </div>
                  
                  <div className="company-overview-container">
                    {/* Left side - Pie Chart */}
                    <div className="pie-chart-section">
                      <div className="pie-legend">
                        {companyUtilizationData.pools.map((pool) => (
                          <div key={pool.name} className="legend-item">
                            <div className={`legend-color ${pool.name.toLowerCase()}`}></div>
                            <div className="legend-info">
                              <div className="legend-pool">{pool.name} Pool</div>
                              <div className="legend-details">
                                {pool.utilizationRate.toFixed(1)}% utilization
                              </div>
                              <div className="legend-details">
                                {pool.utilizationContribution.toFixed(1)}% of company total
                              </div>
                            </div>
                          </div>
                        ))}
                      </div>
                      
                      <div className="pie-chart-wrapper">
                        <svg className="pie-chart-svg" viewBox="0 0 200 200">
                          {companyUtilizationData.pools.map((pool, index) => {
                            console.log(`Pool: ${pool.name}, Angle: ${pool.angle}, Contribution: ${pool.utilizationContribution}`);
                            
                            // Calculate SVG path for pie slice
                            const radius = 80;
                            const centerX = 100;
                            const centerY = 100;
                            
                            const startAngle = (pool.startAngle * Math.PI) / 180;
                            const endAngle = (pool.endAngle * Math.PI) / 180;
                            
                            const x1 = centerX + radius * Math.cos(startAngle);
                            const y1 = centerY + radius * Math.sin(startAngle);
                            const x2 = centerX + radius * Math.cos(endAngle);
                            const y2 = centerY + radius * Math.sin(endAngle);
                            
                            const largeArcFlag = pool.angle > 180 ? 1 : 0;
                            
                            const pathData = [
                              `M ${centerX} ${centerY}`,
                              `L ${x1} ${y1}`,
                              `A ${radius} ${radius} 0 ${largeArcFlag} 1 ${x2} ${y2}`,
                              'Z'
                            ].join(' ');
                            
                            return (
                              <path
                                key={pool.name}
                                d={pathData}
                                fill={pool.color}
                                stroke="white"
                                strokeWidth="2"
                                className="pie-segment-svg"
                                onMouseEnter={(e) => {
                                  console.log('Mouse entered segment:', pool.name);
                                  const tooltip = e.target.closest('.pie-chart-wrapper').querySelector('.pie-tooltip');
                                  if (tooltip) {
                                    console.log('Tooltip found, showing for:', pool.name);
                                    // Position tooltip near the mouse cursor
                                    const rect = e.target.closest('.pie-chart-wrapper').getBoundingClientRect();
                                    const mouseX = e.clientX - rect.left;
                                    const mouseY = e.clientY - rect.top;
                                    
                                    tooltip.style.left = `${mouseX + 10}px`;
                                    tooltip.style.top = `${mouseY - 10}px`;
                                    tooltip.style.display = 'block';
                                    tooltip.classList.add('show');
                                    
                                    tooltip.innerHTML = `
                                      <div class="tooltip-pool">${pool.name} Pool</div>
                                      <div class="tooltip-contribution">${pool.utilizationRate.toFixed(1)}% utilization</div>
                                      <div class="tooltip-contribution">${pool.utilizationContribution.toFixed(1)}% of company total</div>
                                    `;
                                  } else {
                                    console.log('Tooltip not found');
                                  }
                                }}
                                onMouseMove={(e) => {
                                  const tooltip = e.target.closest('.pie-chart-wrapper').querySelector('.pie-tooltip');
                                  if (tooltip && tooltip.style.display === 'block') {
                                    // Update tooltip position as mouse moves
                                    const rect = e.target.closest('.pie-chart-wrapper').getBoundingClientRect();
                                    const mouseX = e.clientX - rect.left;
                                    const mouseY = e.clientY - rect.top;
                                    
                                    tooltip.style.left = `${mouseX + 10}px`;
                                    tooltip.style.top = `${mouseY - 10}px`;
                                  }
                                }}
                                onMouseLeave={(e) => {
                                  console.log('Mouse left segment:', pool.name);
                                  const tooltip = e.target.closest('.pie-chart-wrapper').querySelector('.pie-tooltip');
                                  if (tooltip) {
                                    tooltip.style.display = 'none';
                                    tooltip.classList.remove('show');
                                  }
                                }}
                              />
                            );
                          })}
                        </svg>
                        
                        {/* Tooltip container outside SVG */}
                        <div className="pie-tooltip" style={{ display: 'none' }}></div>
                      </div>
                    </div>

                    {/* Right side - Traditional Bar Chart */}
                    <div className="bar-chart-section">
                      <div className="simple-bar-chart">
                        <div className="chart-container">
                          {/* Y-axis */}
                          <div className="y-axis">
                            <div className="y-label">10000h</div>
                            <div className="y-label">8000h</div>
                            <div className="y-label">6000h</div>
                            <div className="y-label">4000h</div>
                            <div className="y-label">2000h</div>
                            <div className="y-label">0h</div>
                          </div>
                          
                          {/* Chart area */}
                          <div className="chart-area">
                            {/* Grid lines */}
                            <div className="grid-lines">
                              <div className="grid-line"></div>
                              <div className="grid-line"></div>
                              <div className="grid-line"></div>
                              <div className="grid-line"></div>
                              <div className="grid-line"></div>
                              <div className="grid-line"></div>
                            </div>
                            
                            {/* Bars */}
                            <div className="bars">
                              {/* Bar Tooltip */}
                              <div className="bar-tooltip" style={{ display: 'none' }}></div>
                              <div className="bar available" style={{
                                height: `${(companyUtilizationData.totalAvailableHours / 10000) * 100}%`
                              }}
                              onMouseEnter={(e) => {
                                const tooltip = e.target.closest('.bars').querySelector('.bar-tooltip');
                                if (tooltip) {
                                  tooltip.innerHTML = `
                                    <div class="tooltip-title">Available Hours</div>
                                    <div class="tooltip-value">${companyUtilizationData.totalAvailableHours.toFixed(0)} hours</div>
                                  `;
                                  tooltip.style.display = 'block';
                                  tooltip.style.left = e.target.offsetLeft + e.target.offsetWidth/2 - tooltip.offsetWidth/2 + 'px';
                                  tooltip.style.top = e.target.offsetTop - tooltip.offsetHeight - 10 + 'px';
                                }
                              }}
                              onMouseLeave={(e) => {
                                const tooltip = e.target.closest('.bars').querySelector('.bar-tooltip');
                                if (tooltip) {
                                  tooltip.style.display = 'none';
                                }
                              }}>
                                <span className="bar-value">{companyUtilizationData.totalAvailableHours.toFixed(0)}h</span>
                              </div>
                              
                              <div className="bar planned" style={{
                                height: `${((companyUtilizationData.totalPlannedHours || 0) / 10000) * 100}%`
                              }}
                              onMouseEnter={(e) => {
                                const tooltip = e.target.closest('.bars').querySelector('.bar-tooltip');
                                if (tooltip) {
                                  tooltip.innerHTML = `
                                    <div class="tooltip-title">Planned Hours</div>
                                    <div class="tooltip-value">${companyUtilizationData.totalPlannedHours?.toFixed(0) || '0'} hours</div>
                                  `;
                                  tooltip.style.display = 'block';
                                  tooltip.style.left = e.target.offsetLeft + e.target.offsetWidth/2 - tooltip.offsetWidth/2 + 'px';
                                  tooltip.style.top = e.target.offsetTop - tooltip.offsetHeight - 10 + 'px';
                                }
                              }}
                              onMouseLeave={(e) => {
                                const tooltip = e.target.closest('.bars').querySelector('.bar-tooltip');
                                if (tooltip) {
                                  tooltip.style.display = 'none';
                                }
                              }}>
                                <span className="bar-value">{companyUtilizationData.totalPlannedHours?.toFixed(0) || '0'}h</span>
                              </div>
                              
                              <div className="bar external" style={{
                                height: `${((companyUtilizationData.totalExternalHours || 0) / 10000) * 100}%`
                              }}
                              onMouseEnter={(e) => {
                                const tooltip = e.target.closest('.bars').querySelector('.bar-tooltip');
                                if (tooltip) {
                                  tooltip.innerHTML = `
                                    <div class="tooltip-title">External Hours</div>
                                    <div class="tooltip-value">${companyUtilizationData.totalExternalHours?.toFixed(0) || '0'} hours</div>
                                  `;
                                  tooltip.style.display = 'block';
                                  tooltip.style.left = e.target.offsetLeft + e.target.offsetWidth/2 - tooltip.offsetWidth/2 + 'px';
                                  tooltip.style.top = e.target.offsetTop - tooltip.offsetHeight - 10 + 'px';
                                }
                              }}
                              onMouseLeave={(e) => {
                                const tooltip = e.target.closest('.bars').querySelector('.bar-tooltip');
                                if (tooltip) {
                                  tooltip.style.display = 'none';
                                }
                              }}>
                                <span className="bar-value">{companyUtilizationData.totalExternalHours?.toFixed(0) || '0'}h</span>
                              </div>
                              
                              <div className="bar logged" style={{
                                height: `${(companyUtilizationData.totalLoggedHours / 10000) * 100}%`
                              }}
                              onMouseEnter={(e) => {
                                const tooltip = e.target.closest('.bars').querySelector('.bar-tooltip');
                                if (tooltip) {
                                  tooltip.innerHTML = `
                                    <div class="tooltip-title">Logged Hours</div>
                                    <div class="tooltip-value">${companyUtilizationData.totalLoggedHours.toFixed(0)} hours</div>
                                  `;
                                  tooltip.style.display = 'block';
                                  tooltip.style.left = e.target.offsetLeft + e.target.offsetWidth/2 - tooltip.offsetWidth/2 + 'px';
                                  tooltip.style.top = e.target.offsetTop - tooltip.offsetHeight - 10 + 'px';
                                }
                              }}
                              onMouseLeave={(e) => {
                                const tooltip = e.target.closest('.bars').querySelector('.bar-tooltip');
                                if (tooltip) {
                                  tooltip.style.display = 'none';
                                }
                              }}>
                                <span className="bar-value">{companyUtilizationData.totalLoggedHours.toFixed(0)}h</span>
                              </div>
                            </div>
                          </div>
                        </div>
                      </div>
                        
                      {/* Legend */}
                      <div className="chart-legend">
                        <div className="legend-title">Hours Breakdown</div>
                        <div className="legend-items">
                          <div className="legend-item">
                            <div className="legend-color available"></div>
                            <div className="legend-label">Available Hours</div>
                            <div className="legend-value">{companyUtilizationData.totalAvailableHours.toFixed(0)}h</div>
                          </div>
                          <div className="legend-item">
                            <div className="legend-color planned"></div>
                            <div className="legend-label">Planned Hours</div>
                            <div className="legend-value">{companyUtilizationData.totalPlannedHours?.toFixed(0) || '0'}h</div>
                          </div>
                          <div className="legend-item">
                            <div className="legend-color external"></div>
                            <div className="legend-label">External Hours</div>
                            <div className="legend-value">{companyUtilizationData.totalExternalHours?.toFixed(0) || '0'}h</div>
                          </div>
                          <div className="legend-item">
                            <div className="legend-color logged"></div>
                            <div className="legend-label">Logged Hours</div>
                            <div className="legend-value">{companyUtilizationData.totalLoggedHours.toFixed(0)}h</div>
                          </div>
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
              )}
              
              <div className="utilization-grid">
                {selectedDepartment === 'Creative Strategy' ? (
                  // For Creative Strategy: Show one unified department chart
                  (() => {
                    // Get the Creative Strategy team data directly from backend
                    const teamData = teamUtilizationData['Creative Strategy'];
                    
                    if (!teamData) {
                      return (
                        <div className="no-utilization-data">
                          <p>No Creative Strategy utilization data found for the selected period</p>
                        </div>
                      );
                    }

                    return (
                      <div className="team-utilization-card">
                        <div className="team-header">
                          <h3 className="team-name">Creative Strategy Department</h3>
                        </div>
                        
                        <div 
                          className="utilization-gauge clickable"
                          onClick={() => handleUtilizationChartClick('Creative Strategy Department', teamData)}
                          title="Click to view detailed Creative Strategy department information"
                        >
                          <div className="gauge-container">
                            <div 
                              className="gauge-circle"
                              style={{
                                background: `conic-gradient(
                                  #4CAF50 0deg ${(() => {
                                    const loggedHours = teamData.logged_hours || 0;
                                    const availableHours = teamData.available_hours || 0;
                                    const utilizationRate = availableHours > 0 ? (loggedHours / availableHours * 100) : 0;
                                    return utilizationRate * 3.6;
                                  })()}deg,
                                  #e0e0e0 ${(() => {
                                    const loggedHours = teamData.logged_hours || 0;
                                    const availableHours = teamData.available_hours || 0;
                                    const utilizationRate = availableHours > 0 ? (loggedHours / availableHours * 100) : 0;
                                    return utilizationRate * 3.6;
                                  })()}deg 360deg
                                )`
                              }}
                            >
                              <div className="gauge-inner">
                                <span className="gauge-percentage">{(() => {
                                  const loggedHours = teamData.logged_hours || 0;
                                  const availableHours = teamData.available_hours || 0;
                                  const utilizationRate = availableHours > 0 ? (loggedHours / availableHours * 100) : 0;
                                  return utilizationRate.toFixed(1);
                                })()}%</span>
                                <span className="gauge-label tooltip">Utilization
                                  <span className="tooltiptext">The percentage of Logged Hours against Available Hours. (Logged Hours / Available Hours) * 100</span>
                                </span>
                              </div>
                            </div>
                          </div>
                          <div className="click-hint">Click to view details</div>
                        </div>
                        
                        <div className="team-stats">
                          <div className="stat-row">
                            <span className="stat-label">No. Team Members:</span>
                            <span className="stat-value">{teamData.total_creatives}</span>
                          </div>
                          <div className="stat-row">
                            <span className="stat-label">No. Active Team Members:</span>
                            <span className="stat-value">{teamData.active_creatives}</span>
                          </div>
                          <div className="stat-row">
                            <span className="stat-label tooltip">Available Hours:
                              <span className="tooltiptext">{`The total number of available hours for the Creative Strategy pool`}</span>
                            </span>
                            <span className="stat-value">{(() => {
                              const decimal = teamData.available_hours || 0;
                              const hours = Math.floor(decimal);
                              const minutes = Math.round((decimal - hours) * 60);
                              if (minutes === 0) return hours === 0 ? '0h' : `${hours}h`;
                              if (hours === 0) return `${minutes}m`;
                              return `${hours}h ${minutes}m`;
                            })()}</span>
                          </div>
                          <div className="stat-row">
                            <span className="stat-label tooltip">Planned Hours:
                              <span className="tooltiptext">{`The total number of hours planned by CS for the Creative Strategy pool`}</span>
                            </span>
                            <span className="stat-value">{(() => {
                              const decimal = teamData.planned_hours || 0;
                              const hours = Math.floor(decimal);
                              const minutes = Math.round((decimal - hours) * 60);
                              if (minutes === 0) return hours === 0 ? '0h' : `${hours}h`;
                              if (hours === 0) return `${minutes}m`;
                              return `${hours}h ${minutes}m`;
                            })()}</span>
                          </div>
                          <div className="stat-row">
                            <span className="stat-label tooltip">Logged Hours:
                              <span className="tooltiptext">{`the total number of hours logged into Odoo by the design team of the Creative Strategy pool`}</span>
                            </span>
                            <span className="stat-value">{(() => {
                              const decimal = teamData.logged_hours || 0;
                              const hours = Math.floor(decimal);
                              const minutes = Math.round((decimal - hours) * 60);
                              if (minutes === 0) return hours === 0 ? '0h' : `${hours}h`;
                              if (hours === 0) return `${minutes}m`;
                              return `${hours}h ${minutes}m`;
                            })()}</span>
                          </div>
                          <div className="stat-row variance">
                            <span className="stat-label tooltip">Variance:
                              <span className="tooltiptext">The percentage difference between Planned Hours and Logged Hours. ((Logged Hours - Planned Hours) / Planned Hours) * 100</span>
                            </span>
                            <span className={`stat-value ${teamData.variance >= 0 ? 'positive' : 'negative'}`}>
                              {teamData.variance >= 0 ? '+' : ''}{(teamData.variance || 0).toFixed(1)}%
                            </span>
                          </div>
                          <div className="stat-row">
                            <span className="stat-label tooltip">Efficiency Ratio:
                              <span className="tooltiptext">Designer efficiency are they spending more or less time than scoped. ((Internal ÷ External) * 100)</span>
                            </span>
                            <span className="stat-value">
                              {(() => {
                                const loggedHours = teamData.logged_hours || 0;
                                const externalHours = (externalHoursData.ksa.totalHours || 0) + (externalHoursData.uae.totalHours || 0);
                                if (externalHours === 0) return 'N/A';
                                const ratio = (loggedHours / externalHours) * 100;
                                return `${ratio.toFixed(1)}%`;
                              })()}
                            </span>
                          </div>
                          <div className="stat-row">
                            <span className="stat-label tooltip">Billable Utilization:
                              <span className="tooltiptext">% of available capacity that is billable work. ((External ÷ Available)*100)</span>
                            </span>
                            <span className="stat-value">
                              {(() => {
                                const externalHours = (externalHoursData.ksa.totalHours || 0) + (externalHoursData.uae.totalHours || 0);
                                const availableHours = teamData.available_hours || 0;
                                if (availableHours === 0) return 'N/A';
                                const billableUtilization = (externalHours / availableHours) * 100;
                                return `${billableUtilization.toFixed(1)}%`;
                              })()}
                            </span>
                          </div>
                          <div className="stat-row">
                            <span className="stat-label tooltip">Scope Health:
                              <span className="tooltiptext">Scope health How much of what was booked by CS actually matches the scoped external hours. ((External ÷ Allocated)*100)</span>
                            </span>
                            <span className="stat-value">
                              {(() => {
                                const externalHours = (externalHoursData.ksa.totalHours || 0) + (externalHoursData.uae.totalHours || 0);
                                const plannedHours = teamData.planned_hours || 0;
                                if (plannedHours === 0) return 'N/A';
                                const scopeHealth = (externalHours / plannedHours) * 100;
                                return `${scopeHealth.toFixed(1)}%`;
                              })()}
                            </span>
                          </div>
                        </div>
                      </div>
                    );
                  })()
                ) : (
                  Object.entries(teamUtilizationData).map(([teamName, teamData]) => (
                    <div key={teamName} className="team-utilization-card">
                      <div className="team-header">
                        <h3 className="team-name">{teamName}</h3>
                      </div>
                      
                      <div 
                        className="utilization-gauge clickable"
                        onClick={() => handleUtilizationChartClick(teamName, teamData)}
                        title={`Click to view detailed ${teamName} team information`}
                      >
                        <div className="gauge-container">
                          <div 
                            className="gauge-circle"
                                                            style={{
                                  background: `conic-gradient(
                                    #4CAF50 0deg ${(() => {
                                      const loggedHours = teamData.logged_hours || 0;
                                      const availableHours = poolAvailableHours[teamName] || 0;
                                      const utilizationRate = availableHours > 0 ? (loggedHours / availableHours * 100) : 0;
                                      return utilizationRate * 3.6;
                                    })()}deg,
                                    #e0e0e0 ${(() => {
                                      const loggedHours = teamData.logged_hours || 0;
                                      const availableHours = poolAvailableHours[teamName] || 0;
                                      const utilizationRate = availableHours > 0 ? (loggedHours / availableHours * 100) : 0;
                                      return utilizationRate * 3.6;
                                    })()}deg 360deg
                                  )`
                                }}
                          >
                            <div className="gauge-inner">
                                                              <span className="gauge-percentage">{(() => {
                                  const loggedHours = teamData.logged_hours || 0;
                                  const availableHours = poolAvailableHours[teamName] || 0;
                                  const utilizationRate = availableHours > 0 ? (loggedHours / availableHours * 100) : 0;
                                  return utilizationRate.toFixed(1);
                                })()}%</span>
                              <span className="gauge-label tooltip">Utilization
                                <span className="tooltiptext">The percentage of Logged Hours against Available Hours. (Logged Hours / Available Hours) * 100</span>
                              </span>
                            </div>
                          </div>
                        </div>
                        <div className="click-hint">Click to view details</div>
                      </div>
                      
                      <div className="team-stats">
                        <div className="stat-row">
                          <span className="stat-label tooltip">No. {selectedDepartment === 'Creative Strategy' || selectedDepartment === 'Instructional Design' ? 'Team Members' : 'Creatives'}:
                            <span className="tooltiptext">{`Number of creatives in the ${teamName} pool`}</span>
                          </span>
                          <span className="stat-value">{teamData.total_creatives}</span>
                        </div>
                        <div className="stat-row">
                          <span className="stat-label tooltip">No. Active {selectedDepartment === 'Creative Strategy' || selectedDepartment === 'Instructional Design' ? 'Team Members' : 'Creatives'}:
                            <span className="tooltiptext">Number of creatives with hours in the timesheet module</span>
                          </span>
                          <span className="stat-value">{teamData.active_creatives}</span>
                        </div>
                                                <div className="stat-row">
                          <span className="stat-label tooltip">Available Hours:
                            <span className="tooltiptext">{`The total number of available hours for the ${teamName} pool`}</span>
                          </span>
                          <span className="stat-value">{(() => {
                            const decimal = poolAvailableHours[teamName] || 0;
                            const hours = Math.floor(decimal);
                            const minutes = Math.round((decimal - hours) * 60);
                            if (minutes === 0) return hours === 0 ? '0h' : `${hours}h`;
                            if (hours === 0) return `${minutes}m`;
                            return `${hours}h ${minutes}m`;
                          })()}</span>
                        </div>
                        <div className="stat-row">
                          <span className="stat-label tooltip">Planned Hours:
                            <span className="tooltiptext">{`The total number of hours planned by CS for the ${teamName} pool`}</span>
                          </span>
                          <span className="stat-value">{(() => {
                            const decimal = teamData.planned_hours || 0;
                            const hours = Math.floor(decimal);
                            const minutes = Math.round((decimal - hours) * 60);
                            if (minutes === 0) return hours === 0 ? '0h' : `${hours}h`;
                            if (hours === 0) return `${minutes}m`;
                            return `${hours}h ${minutes}m`;
                          })()}</span>
                        </div>
                        <div className="stat-row">
                          <span className="stat-label tooltip">Logged Hours:
                            <span className="tooltiptext">{`the total number of hours logged into Odoo by the design team of the ${teamName} pool`}</span>
                          </span>
                          <span className="stat-value">{(() => {
                            const decimal = teamData.logged_hours || 0;
                            const hours = Math.floor(decimal);
                            const minutes = Math.round((decimal - hours) * 60);
                            if (minutes === 0) return hours === 0 ? '0h' : `${hours}h`;
                            if (hours === 0) return `${minutes}m`;
                            return `${hours}h ${minutes}m`;
                          })()}</span>
                        </div>
                        {(teamName.toLowerCase().includes('ksa') || teamName.toLowerCase().includes('uae')) && (
                          <div className="stat-row">
                            <span className="stat-label tooltip">External Hours:
                              <span className="tooltiptext">{`The sales order hours for the ${teamName} pool`}</span>
                            </span>
                            <span className="stat-value">
                              {(() => {
                                let decimal = 0;
                                if (teamName.toLowerCase().includes('ksa')) {
                                  decimal = externalHoursData.ksa.totalHours || 0;
                                } else if (teamName.toLowerCase().includes('uae')) {
                                  decimal = externalHoursData.uae.totalHours || 0;
                                }
                                const hours = Math.floor(decimal);
                                const minutes = Math.round((decimal - hours) * 60);
                                if (minutes === 0) return hours === 0 ? '0h' : `${hours}h`;
                                if (hours === 0) return `${minutes}m`;
                                return `${hours}h ${minutes}m`;
                              })()}
                            </span>
                          </div>
                        )}
                        <div className="stat-row variance">
                          <span className="stat-label tooltip">Variance:
                            <span className="tooltiptext">The percentage difference between Planned Hours and Logged Hours. ((Logged Hours - Planned Hours) / Planned Hours) * 100</span>
                          </span>
                          <span className={`stat-value ${teamData.variance >= 0 ? 'positive' : 'negative'}`}>
                                                          {teamData.variance >= 0 ? '+' : ''}{(teamData.variance || 0).toFixed(1)}%
                          </span>
                        </div>
                        {teamName.toLowerCase() !== 'nightshift' && (
                          <>
                            <div className="stat-row">
                              <span className="stat-label tooltip">Efficiency Ratio:
                                <span className="tooltiptext">Designer efficiency are they spending more or less time than scoped. ((Internal ÷ External) * 100)</span>
                              </span>
                              <span className="stat-value">
                                {(() => {
                                  const loggedHours = teamData.logged_hours || 0;
                                  let externalHours = 0;
                                  
                                  // Map team name to corresponding external hours pool
                                  if (teamName.toLowerCase().includes('ksa')) {
                                    externalHours = externalHoursData.ksa.totalHours || 0;
                                  } else if (teamName.toLowerCase().includes('uae')) {
                                    externalHours = externalHoursData.uae.totalHours || 0;
                                  } else {
                                    // For teams that don't map directly, use combined total
                                    externalHours = (externalHoursData.ksa.totalHours || 0) + (externalHoursData.uae.totalHours || 0);
                                  }
                                  
                                  if (externalHours === 0) return 'N/A';
                                  const ratio = (loggedHours / externalHours) * 100;
                                  return `${ratio.toFixed(1)}%`;
                                })()}
                              </span>
                            </div>
                            <div className="stat-row">
                              <span className="stat-label tooltip">Billable Utilization:
                                <span className="tooltiptext">% of available capacity that is billable work. ((External ÷ Available)*100)</span>
                              </span>
                              <span className="stat-value">
                                {(() => {
                                  let externalHours = 0;
                                  
                                  // Map team name to corresponding external hours pool
                                  if (teamName.toLowerCase().includes('ksa')) {
                                    externalHours = externalHoursData.ksa.totalHours || 0;
                                  } else if (teamName.toLowerCase().includes('uae')) {
                                    externalHours = externalHoursData.uae.totalHours || 0;
                                  } else {
                                    // For teams that don't map directly, use combined total
                                    externalHours = (externalHoursData.ksa.totalHours || 0) + (externalHoursData.uae.totalHours || 0);
                                  }
                                  
                                  const availableHours = poolAvailableHours[teamName] || 0;
                                  if (availableHours === 0) return 'N/A';
                                  const billableUtilization = (externalHours / availableHours) * 100;
                                  return `${billableUtilization.toFixed(1)}%`;
                                })()}
                              </span>
                            </div>
                            <div className="stat-row">
                              <span className="stat-label tooltip">Scope Health:
                                <span className="tooltiptext">Scope health How much of what was booked by CS actually matches the scoped external hours. ((External ÷ Allocated)*100)</span>
                              </span>
                              <span className="stat-value">
                                {(() => {
                                  let externalHours = 0;
                                  
                                  // Map team name to corresponding external hours pool
                                  if (teamName.toLowerCase().includes('ksa')) {
                                    externalHours = externalHoursData.ksa.totalHours || 0;
                                  } else if (teamName.toLowerCase().includes('uae')) {
                                    externalHours = externalHoursData.uae.totalHours || 0;
                                  } else {
                                    // For teams that don't map directly, use combined total
                                    externalHours = (externalHoursData.ksa.totalHours || 0) + (externalHoursData.uae.totalHours || 0);
                                  }
                                  
                                  const plannedHours = teamData.planned_hours || 0;
                                  if (plannedHours === 0) return 'N/A';
                                  const scopeHealth = (externalHours / plannedHours) * 100;
                                  return `${scopeHealth.toFixed(1)}%`;
                                })()}
                              </span>
                            </div>
                          </>
                        )}
                      </div>
                    </div>
                  ))
                )}
              </div>
              
              {Object.keys(teamUtilizationData).length === 0 && (
                <div className="no-utilization-data">
                  <p>No team utilization data found for the selected period</p>
                </div>
              )}
              
              {/* Shareholder Updates Management */}
              <div className="shareholder-section">
                <h3>Shareholder Updates</h3>
                <p className="shareholder-subtext">Send comprehensive monthly utilization reports with full dashboard data to saved emails.</p>
                <div className="shareholder-controls">
                  <input 
                    type="email"
                    placeholder="Add shareholder email"
                    value={newShareholderEmail}
                    onChange={(e) => setNewShareholderEmail(e.target.value)}
                    className="shareholder-input"
                  />
                  <button 
                    className="shareholder-btn add"
                    onClick={async () => {
                      if (!newShareholderEmail) return;
                      try {
                        const res = await axios.post('/api/shareholders', { email: newShareholderEmail });
                        if (res.data && res.data.success) {
                          setShareholders(res.data.shareholders || []);
                          setNewShareholderEmail('');
                        }
                      } catch (_) {}
                    }}
                  >Add</button>
                  <button 
                    className="shareholder-btn preview"
                    onClick={async () => {
                      try {
                        const res = await axios.get('/api/shareholders/preview-weekly');
                        if (res.data && res.data.success) {
                          setPreviewHtml(res.data.html || '');
                          setShowPreview(true);
                        }
                      } catch (_) {}
                    }}
                  >Preview Weekly Report</button>
                  <button 
                    className={`shareholder-btn send ${isSending ? 'disabled' : ''}`}
                    disabled={isSending}
                    onClick={async () => {
                      setIsSending(true);
                      try {
                        await axios.post('/api/shareholders/send-weekly', {});
                      } catch (_) {}
                      setIsSending(false);
                    }}
                  >{isSending ? 'Sending...' : 'Send Weekly Report'}</button>
                </div>
                
                <div className="shareholder-list">
                  {shareholders.length === 0 ? (
                    <div className="no-shareholders">No shareholders saved yet.</div>
                  ) : (
                    shareholders.map((email, idx) => (
                      <div key={idx} className="shareholder-item">
                        <span className="shareholder-email">{email}</span>
                        <div className="shareholder-actions">
                          <button 
                            className="shareholder-btn test"
                            onClick={async () => {
                              try {
                                await axios.post('/api/shareholders/send-test', { email });
                              } catch (_) {}
                            }}
                          >Send Test Weekly</button>
                          <button 
                            className="shareholder-btn remove"
                            onClick={async () => {
                              try {
                                const res = await axios.delete('/api/shareholders', { data: { email } });
                                if (res.data && res.data.success) {
                                  setShareholders(res.data.shareholders || []);
                                }
                              } catch (_) {}
                            }}
                          >Remove</button>
                        </div>
                      </div>
                    ))
                  )}
                </div>
              </div>
            </div>
          </div>
        )}

        {/* Detailed View Modal */}
        {showDetailedView && detailedTeamData && (
          <div className="modal-overlay" onClick={closeDetailedView}>
            <div className="modal-content" onClick={(e) => e.stopPropagation()}>
              <div className="modal-header">
                <h2>{selectedTeamForDetail} Team - Detailed View</h2>
                <button className="modal-close-btn" onClick={closeDetailedView}>
                  ✕
                </button>
              </div>
              
              <div className="modal-body">
                <div className="team-summary">
                  <div className="summary-stats">
                    <div className="summary-stat">
                      <span className="summary-label">Total {selectedDepartment === 'Creative Strategy' || selectedDepartment === 'Instructional Design' ? 'Team Members' : 'Creatives'}:</span>
                      <span className="summary-value">{detailedTeamData.total_creatives}</span>
                    </div>
                    <div className="summary-stat">
                      <span className="summary-label">Active {selectedDepartment === 'Creative Strategy' || selectedDepartment === 'Instructional Design' ? 'Team Members' : 'Creatives'}:</span>
                      <span className="summary-value">{detailedTeamData.active_creatives}</span>
                    </div>
                    <div className="summary-stat">
                      <span className="summary-label">Utilization Rate:</span>
                                                  <span className="summary-value">{(detailedTeamData.utilization_rate || 0).toFixed(1)}%</span>
                    </div>
                  </div>
                </div>
                
                <div className="creatives-list">
                  <h3>Team Members</h3>
                  {detailedTeamData.employees && detailedTeamData.employees.length > 0 ? (
                    <div className="creatives-grid">
                      {detailedTeamData.employees.map((employee, index) => (
                        <div key={index} className="creative-detail-card">
                          <div className="creative-header">
                            <div className="creative-utilization-display">
                              {(() => {
                                // Calculate individual utilization rate
                                const availableHours = employee.time_off_hours ? (184 - (employee.time_off_hours || 0)) : 184;
                                const plannedHours = employee.planned_hours || 0;
                                const utilizationRate = availableHours > 0 ? (plannedHours / availableHours * 100) : 0;
                                
                                return (
                                  <div className="utilization-circle">
                                    <div 
                                      className="utilization-gauge-small"
                                      style={{
                                        background: `conic-gradient(
                                          #4CAF50 0deg ${utilizationRate * 3.6}deg,
                                          #e0e0e0 ${utilizationRate * 3.6}deg 360deg
                                        )`
                                      }}
                                    >
                                      <div className="utilization-inner-small">
                                        <div className="employee-initials">{employee.name.charAt(0).toUpperCase()}</div>
                                        <div className="utilization-percentage">{utilizationRate.toFixed(0)}%</div>
                                      </div>
                                    </div>
                                  </div>
                                );
                              })()}
                            </div>
                            <div className="creative-info">
                              <h4 className="creative-name">{employee.name}</h4>
                              {employee.job_title && (
                                <p className="creative-title">{employee.job_title}</p>
                              )}
                            </div>
                          </div>
                          
                          <div className="creative-tags">
                            {employee.tags && employee.tags.length > 0 ? (
                              employee.tags.map((tag, tagIndex) => (
                                <span key={tagIndex} className="creative-tag">{tag}</span>
                              ))
                            ) : (
                              <span className="no-tags">No tags assigned</span>
                            )}
                          </div>
                          
                          <div className="creative-hours">
                                                        <div className="hours-row">
                              <span className="hours-label">Available Hours:</span>
                              <span className="hours-value">
                                {formatDecimalHours(employee.time_off_hours ? (184 - (employee.time_off_hours || 0)) : 184)}
                              </span>
                            </div>
                            <div className="hours-row">
                              <span className="hours-label">Planned Hours:</span>
                              <span className="hours-value">{formatDecimalHours(employee.planned_hours || 0)}</span>
                            </div>
                            <div className="hours-row">
                              <span className="hours-label">Logged Hours:</span>
                              <span className="hours-value">{formatDecimalHours(employee.logged_hours || 0)}</span>
                            </div>
                            <div className="hours-row utilization-rate">
                              <span className="hours-label">Utilization Rate:</span>
                              <span className="hours-value utilization-value">
                                {(() => {
                                  const availableHours = employee.time_off_hours ? (184 - (employee.time_off_hours || 0)) : 184;
                                  const plannedHours = employee.planned_hours || 0;
                                  const utilizationRate = availableHours > 0 ? (plannedHours / availableHours * 100) : 0;
                                  return `${utilizationRate.toFixed(1)}%`;
                                })()}
                              </span>
                            </div>
                            {employee.time_off_hours > 0 && (
                              <div className="hours-row time-off">
                                <span className="hours-label">Time Off:</span>
                                <span className="hours-value">{formatDecimalHours(employee.time_off_hours || 0)}</span>
                              </div>
                            )}
                          </div>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <div className="no-creatives">
                      <p>No team members found for {selectedTeamForDetail}</p>
                    </div>
                  )}
                </div>
              </div>
            </div>
          </div>
        )}
      </div>
      {((activeTab === 'sales-order-hours' && salesOrderHoursCache.last_updated) || (activeTab !== 'sales-order-hours' && cacheStatus.last_updated)) && (
        <div className="unified-cache-refresh">
          <div className="cache-status-section">
            <span className="cache-indicator">
              {activeTab === 'sales-order-hours'
                ? (salesOrderHoursCache.cached ? '📦 Cached' : '🔄 Fresh')
                : (cacheStatus.cached ? '📦 Cached' : '🔄 Fresh')
              }
            </span>
            <span className="cache-time">
              Last updated: {new Date((activeTab === 'sales-order-hours' ? salesOrderHoursCache.last_updated : cacheStatus.last_updated) * 1000).toLocaleTimeString()}
            </span>
          </div>
          <button 
            className={`refresh-btn ${isRefreshing ? 'refreshing' : ''}`}
            onClick={handleRefresh}
            disabled={isRefreshing}
            title="Refresh data from Odoo"
          >
            {isRefreshing ? '⏳ Refreshing...' : '🔄 Refresh'}
          </button>
        </div>
      )}

      {/* Preview Modal */}
      {showPreview && (
        <div className="modal-overlay" onClick={() => setShowPreview(false)}>
          <div className="modal-content" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h2>Weekly Email Preview</h2>
              <button className="modal-close-btn" onClick={() => setShowPreview(false)}>✕</button>
            </div>
            <div className="modal-body">
              <div dangerouslySetInnerHTML={{ __html: previewHtml }} />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function App() {
  const [isAuthenticated, setIsAuthenticated] = useState(() => {
    try {
      return localStorage.getItem('auth_token') === 'ok';
    } catch (e) { return false; }
  });
  const [authError, setAuthError] = useState('');

  const handleLogin = useCallback((username, password) => {
    if (username === 'admin' && password === 'Prezlab@12345') {
      try { localStorage.setItem('auth_token', 'ok'); } catch (e) {}
      setAuthError('');
      setIsAuthenticated(true);
    } else {
      setAuthError('Invalid username or password.');
      setIsAuthenticated(false);
    }
  }, []);

  const handleLogout = useCallback(() => {
    try { localStorage.removeItem('auth_token'); } catch (e) {}
    setIsAuthenticated(false);
  }, []);

  if (!isAuthenticated) {
    return (
      <div className="app">
        <Login onLogin={handleLogin} error={authError} />
      </div>
    );
  }

  return <DashboardApp onLogout={handleLogout} />;
}

export default App;