import React, { useState, useEffect, useMemo, useCallback } from 'react';
import axios from 'axios';
import './App.css';

function App() {
  const [selectedDepartment, setSelectedDepartment] = useState('Creative');
  const [selectedPeriod, setSelectedPeriod] = useState(() => {
    const now = new Date();
    const year = now.getFullYear();
    const month = String(now.getMonth() + 1).padStart(2, '0');
    return `${year}-${month}`;
  });
  const [viewType, setViewType] = useState('monthly');
  const [activeTab, setActiveTab] = useState('resources');
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
  const [salesOrderHoursCache, setSalesOrderHoursCache] = useState({
    cached: false,
    last_updated: null,
    data: null
  });

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
    if (selectedPoolFilters.length === 0) {
      return employees;
    }
    
    // Get all employees that have any of the selected tags
    const filteredEmployees = employees.filter(emp => {
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
    if (!selectedResourcePoolFilter) {
      return availableResources;
    }
    return availableResources.filter(resource => 
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
    timesheetData.filter(employee => (employee.total_hours?.decimal || 0) > 0).length,
    [timesheetData]
  );

  const totalLoggedHours = useMemo(() => 
    timesheetData.reduce((total, employee) => total + (employee.total_hours?.decimal || 0), 0),
    [timesheetData]
  );

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

  const totalResourceHours = useMemo(() => ({
    totalPlannedHours: availableResources.reduce((total, resource) => 
      total + (resource.planned_hours?.decimal || 0), 0
    ),
    totalAvailableHours: availableResources.reduce((total, resource) => 
      total + (resource.available_hours?.decimal || 0), 0
    )
  }), [availableResources]);

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
          <button 
            className={`tab-btn ${activeTab === 'employees' ? 'active' : ''}`}
            onClick={() => handleTabSwitch('employees')}
          >
            👥 Number of {selectedDepartment === 'Creative Strategy' || selectedDepartment === 'Instructional Design' ? 'Team Members' : 'Creatives'} ({employees.length})
          </button>
            <button 
            className={`tab-btn ${activeTab === 'resources' ? 'active' : ''}`}
            onClick={() => handleTabSwitch('resources')}
          >
            📊 Available {selectedDepartment === 'Creative Strategy' || selectedDepartment === 'Instructional Design' ? 'Team Members' : 'Creatives'} ({availableResources.length})
          </button>
            <button 
            className={`tab-btn ${activeTab === 'timesheet' ? 'active' : ''}`}
            onClick={() => handleTabSwitch('timesheet')}
          >
            ⏱️ Active {selectedDepartment === 'Creative Strategy' || selectedDepartment === 'Instructional Design' ? 'Team Members' : 'Creatives'} ({activeTimesheetCount})
          </button>

          <button 
            className={`tab-btn ${activeTab === 'sales-order-hours' ? 'active' : ''}`}
            onClick={() => handleTabSwitch('sales-order-hours')}
          >
            📋 External Hours
          </button>
          <button 
            className={`tab-btn ${activeTab === 'utilization' ? 'active' : ''}`}
            onClick={() => handleTabSwitch('utilization')}
          >
            📈 Utilization Dashboard
          </button>
        </div>

        {/* View Type and Period Selector for Resources, Timesheet, Sales Order Hours, and Utilization Tabs */}
        {(activeTab === 'resources' || activeTab === 'timesheet' || activeTab === 'sales-order-hours' || activeTab === 'utilization') && (
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
                <p className="stat-number">{employees.length}</p>
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
                <p className="stat-number">{availableResources.length}</p>
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

                    // Calculate total logged hours for all pools (avoiding double-counting)
                    // Use the original timesheetData to get the true total without duplication
                    const totalLoggedHours = timesheetData.reduce((total, employee) => 
                      total + (employee.total_hours?.decimal || 0), 0
                    );

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
              <h2>External Hours Dashboard</h2>
              <p>Calculate external hours from sales orders (adhoc, framework, and strategy) after July 1st for KSA and UAE markets.</p>
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
                    <div className="stat-number">{formatDecimalHours(salesOrderHoursData.ksa.totalHours)}</div>
                    <div className="stat-label">Total External Hours</div>
                    <div className="orders-count">{salesOrderHoursData.ksa.orders?.length || 0} Customers</div>
                  </div>
                  
                  <div className="pool-stat-card">
                    <h3>UAE Pool</h3>
                    <div className="stat-number">{formatDecimalHours(salesOrderHoursData.uae.totalHours)}</div>
                    <div className="stat-label">Total External Hours</div>
                    <div className="orders-count">{salesOrderHoursData.uae.orders?.length || 0} Customers</div>
                  </div>
                  
                  <div className="pool-stat-card total-stat">
                    <h3>📊 Total</h3>
                    <div className="stat-number">
                      {formatDecimalHours(salesOrderHoursData.ksa.totalHours + salesOrderHoursData.uae.totalHours)}
                    </div>
                    <div className="stat-label">Combined External Hours</div>
                    <div className="orders-count">
                      {(salesOrderHoursData.ksa.orders?.length || 0) + (salesOrderHoursData.uae.orders?.length || 0)} Total Customers
                    </div>
                  </div>
                </div>
                
                <div className="orders-details">
                  <h3>Customer Sales Order Details</h3>
                  <div className="orders-grid">
                    {/* KSA Orders */}
                    <div className="orders-section">
                      <h4>🇸🇦 KSA Customers ({salesOrderHoursData.ksa.orders?.length || 0})</h4>
                      {(salesOrderHoursData.ksa.orders?.length || 0) === 0 ? (
                        <div className="no-orders">No KSA customers found with sales orders after July 1st</div>
                      ) : (
                        <div className="orders-list">
                          {salesOrderHoursData.ksa.orders?.map((customer, index) => (
                            <div key={index} className="customer-card">
                              <div className="customer-header">
                                <h5>{customer.customer_name}</h5>
                                <span className="customer-total-hours">{formatDecimalHours(customer.total_hours || 0)} hours</span>
                              </div>
                              <div className="customer-orders">
                                <span className="orders-count">{customer.orders?.length || 0} orders</span>
                                <div className="orders-summary">
                                  {customer.orders?.map((order, orderIndex) => (
                                    <div key={orderIndex} className="order-summary">
                                      <span className="order-name">{order.order_name}</span>
                                      <span className="order-hours">{formatDecimalHours(order.total_hours || 0)}h</span>
                                    </div>
                                  ))}
                                </div>
                              </div>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                    
                    {/* UAE Orders */}
                    <div className="orders-section">
                      <h4>🇦🇪 UAE Customers ({salesOrderHoursData.uae.orders?.length || 0})</h4>
                      {(salesOrderHoursData.uae.orders?.length || 0) === 0 ? (
                        <div className="no-orders">No UAE customers found with sales orders after July 1st</div>
                      ) : (
                        <div className="orders-list">
                          {salesOrderHoursData.uae.orders?.map((customer, index) => (
                            <div key={index} className="customer-card">
                              <div className="customer-header">
                                <h5>{customer.customer_name}</h5>
                                <span className="customer-total-hours">{formatDecimalHours(customer.total_hours || 0)} hours</span>
                              </div>
                              <div className="customer-orders">
                                <span className="orders-count">{customer.orders?.length || 0} orders</span>
                                <div className="orders-summary">
                                  {customer.orders?.map((order, orderIndex) => (
                                    <div key={orderIndex} className="order-summary">
                                      <span className="order-name">{order.order_name}</span>
                                      <span className="order-hours">{formatDecimalHours(order.total_hours || 0)}h</span>
                                    </div>
                                  ))}
                                </div>
                              </div>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        )}

        {activeTab === 'utilization' && (
          <div className="tab-content">
            <div className="utilization-dashboard">
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
                                  #4CAF50 0deg ${(teamData.utilization_rate || 0) * 3.6}deg,
                                  #e0e0e0 ${(teamData.utilization_rate || 0) * 3.6}deg 360deg
                                )`
                              }}
                            >
                              <div className="gauge-inner">
                                <span className="gauge-percentage">{(teamData.utilization_rate || 0).toFixed(1)}%</span>
                                <span className="gauge-label">Utilization</span>
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
                            <span className="stat-label">Available Hours:</span>
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
                            <span className="stat-label">Planned Hours:</span>
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
                            <span className="stat-label">Logged Hours:</span>
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
                            <span className="stat-label">Variance:</span>
                            <span className={`stat-value ${teamData.variance >= 0 ? 'positive' : 'negative'}`}>
                              {teamData.variance >= 0 ? '+' : ''}{(teamData.variance || 0).toFixed(1)}%
                            </span>
                          </div>
                          <div className="stat-row">
                            <span className="stat-label">Efficiency Ratio:</span>
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
                            <span className="stat-label">Billable Utilization:</span>
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
                            <span className="stat-label">Scope Health:</span>
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
                  // For Creative and Instructional Design: Show individual pool charts
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
                                    #4CAF50 0deg ${(teamData.utilization_rate || 0) * 3.6}deg,
                                    #e0e0e0 ${(teamData.utilization_rate || 0) * 3.6}deg 360deg
                                  )`
                                }}
                          >
                            <div className="gauge-inner">
                                                              <span className="gauge-percentage">{(teamData.utilization_rate || 0).toFixed(1)}%</span>
                              <span className="gauge-label">Utilization</span>
                            </div>
                          </div>
                        </div>
                        <div className="click-hint">Click to view details</div>
                      </div>
                      
                      <div className="team-stats">
                        <div className="stat-row">
                          <span className="stat-label">No. {selectedDepartment === 'Creative Strategy' || selectedDepartment === 'Instructional Design' ? 'Team Members' : 'Creatives'}:</span>
                          <span className="stat-value">{teamData.total_creatives}</span>
                        </div>
                        <div className="stat-row">
                          <span className="stat-label">No. Active {selectedDepartment === 'Creative Strategy' || selectedDepartment === 'Instructional Design' ? 'Team Members' : 'Creatives'}:</span>
                          <span className="stat-value">{teamData.active_creatives}</span>
                        </div>
                                                <div className="stat-row">
                          <span className="stat-label">Available Hours:</span>
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
                          <span className="stat-label">Planned Hours:</span>
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
                          <span className="stat-label">Logged Hours:</span>
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
                          <span className="stat-label">Variance:</span>
                          <span className={`stat-value ${teamData.variance >= 0 ? 'positive' : 'negative'}`}>
                                                          {teamData.variance >= 0 ? '+' : ''}{(teamData.variance || 0).toFixed(1)}%
                          </span>
                        </div>
                        {teamName.toLowerCase() !== 'nightshift' && (
                          <>
                            <div className="stat-row">
                              <span className="stat-label">Efficiency Ratio:</span>
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
                              <span className="stat-label">Billable Utilization:</span>
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
                                  
                                  const availableHours = teamData.available_hours || 0;
                                  if (availableHours === 0) return 'N/A';
                                  const billableUtilization = (externalHours / availableHours) * 100;
                                  return `${billableUtilization.toFixed(1)}%`;
                                })()}
                              </span>
                            </div>
                            <div className="stat-row">
                              <span className="stat-label">Scope Health:</span>
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

export default App;