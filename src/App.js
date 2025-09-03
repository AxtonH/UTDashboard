import React, { useState, useEffect, useMemo, useCallback } from 'react';
import axios from 'axios';
import './App.css';

function App() {
  const [selectedDepartment, setSelectedDepartment] = useState('Creative');
  const [selectedPeriod, setSelectedPeriod] = useState('2025-01');
  const [viewType, setViewType] = useState('monthly');
  const [activeTab, setActiveTab] = useState('utilization');
  const [loading, setLoading] = useState(true);
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
  const [currentCacheKey, setCurrentCacheKey] = useState(''); // Track current cache key (period_viewType)
  const [shareholders, setShareholders] = useState([]);
  const [newShareholderEmail, setNewShareholderEmail] = useState('');
  const [isSending, setIsSending] = useState(false);
  const [previewHtml, setPreviewHtml] = useState('');
  const [showPreview, setShowPreview] = useState(false);
  const [externalHoursData, setExternalHoursData] = useState({
    ksa: { totalHours: 0, contracts: [] },
    uae: { totalHours: 0, contracts: [] }
  });



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

  useEffect(() => {
    console.log(`useEffect triggered - selectedPeriod: ${selectedPeriod}, viewType: ${viewType}, selectedDepartment: ${selectedDepartment}`);
    
    const fetchData = async () => {
      const hasAnyData = (
        employees.length > 0 ||
        availableResources.length > 0 ||
        timesheetData.length > 0 ||
        Object.keys(teamUtilizationData).length > 0
      );
      if (!hasAnyData) {
        setLoading(true);
      } else {
        setIsRefreshing(true);
      }
      setError(null);
      setFetchProgress(0);
      
      try {
        console.log(`Fetching data for ${selectedDepartment} department, period: ${selectedPeriod}, view type: ${viewType}`);
        console.log(`API URL: /api/all-departments-data?period=${selectedPeriod}&view_type=${viewType}`);
        
        // Simulate progress updates for better UX
        const progressInterval = setInterval(() => {
          setFetchProgress(prev => Math.min(prev + 10, 90));
        }, 200);
        
        // Use the new caching API to fetch all departments data
        // Request full payload on initial load to ensure dashboard completeness
        const apiUrl = `/api/all-departments-data?period=${selectedPeriod}&view_type=${viewType}&selected_department=${encodeURIComponent(selectedDepartment)}&include=employees,team_utilization,available_resources,timesheet_data`;
        console.log(`Making API request to: ${apiUrl}`);
        console.log(`Request parameters - period: ${selectedPeriod}, view_type: ${viewType}`);
        
        const response = await axios.get(apiUrl);
        
        console.log(`API Response received:`);
        console.log(`- Status: ${response.status}`);
        console.log(`- Cached: ${response.data.cached}`);
        console.log(`- View Type in response: ${response.data.view_type || 'not specified'}`);
        console.log(`- Period in response: ${response.data.selected_period || 'not specified'}`);
        
        clearInterval(progressInterval);
        setFetchProgress(100);
        
        if (response.data.error) {
          throw new Error(response.data.error);
        }
        
        const { creative, creative_strategy, instructional_design, cached, cache_timestamp } = response.data;
        
        console.log(`=== Data Processing Debug ===`);
        console.log(`Creative data available: ${creative ? 'yes' : 'no'}`);
        console.log(`Creative Strategy data available: ${creative_strategy ? 'yes' : 'no'}`);
        console.log(`Instructional Design data available: ${instructional_design ? 'yes' : 'no'}`);
        
        if (creative) {
          console.log(`Creative available_resources count: ${creative.available_resources ? creative.available_resources.length : 'undefined'}`);
          console.log(`Creative employees count: ${creative.employees ? creative.employees.length : 'undefined'}`);
        }
        
        if (creative_strategy) {
          console.log(`Creative Strategy available_resources count: ${creative_strategy.available_resources ? creative_strategy.available_resources.length : 'undefined'}`);
          console.log(`Creative Strategy employees count: ${creative_strategy.employees ? creative_strategy.employees.length : 'undefined'}`);
        }
        
        if (instructional_design) {
          console.log(`Instructional Design available_resources count: ${instructional_design.available_resources ? instructional_design.available_resources.length : 'undefined'}`);
          console.log(`Instructional Design employees count: ${instructional_design.employees ? instructional_design.employees.length : 'undefined'}`);
        }
        
        // Store all departments data for quick switching
        setAllDepartmentsData({
          creative,
          creative_strategy,
          instructional_design
        });
        
        // Update current cache key
        const newCacheKey = `${selectedPeriod}_${viewType}`;
        setCurrentCacheKey(newCacheKey);
        console.log(`Updated cache key to: ${newCacheKey}`);
        
        // Set data for the currently selected department
        const currentDepartmentData = selectedDepartment === 'Creative Strategy' ? creative_strategy : selectedDepartment === 'Instructional Design' ? instructional_design : creative;
        
        console.log(`Current department: ${selectedDepartment}`);
        console.log(`Using department data: ${currentDepartmentData ? 'yes' : 'no'}`);
        
        if (currentDepartmentData) {
          const newEmployees = currentDepartmentData.employees || [];
          const newTeamUtilization = currentDepartmentData.team_utilization || {};
          // In lean include, these may be missing; keep old values until explicitly requested
          const newTimesheetData = currentDepartmentData.timesheet_data !== undefined ? currentDepartmentData.timesheet_data : timesheetData;
          const newAvailableResources = currentDepartmentData.available_resources !== undefined ? currentDepartmentData.available_resources : availableResources;
          
          console.log(`Setting new data:`);
          console.log(`- Employees: ${newEmployees.length}`);
          console.log(`- Team Utilization keys: ${Object.keys(newTeamUtilization).length}`);
          console.log(`- Timesheet Data: ${newTimesheetData.length}`);
          console.log(`- Available Resources: ${newAvailableResources.length}`);
          
          // Debug available resources specifically
          if (newAvailableResources.length > 0) {
            console.log(`Sample available resource:`, newAvailableResources[0]);
            console.log(`Available resources base_available_hours:`, newAvailableResources.map(r => r.base_available_hours).slice(0, 5));
          }
          
          setEmployees(newEmployees);
          setTeamUtilizationData(newTeamUtilization);
          setTimesheetData(newTimesheetData);
          setAvailableResources(newAvailableResources);
        } else {
          console.log(`No department data available for ${selectedDepartment}`);
        }
        
        // Update cache status
         setCacheStatus({
          cached,
          last_updated: cache_timestamp
        });
        
        // Log cache status
        if (cached) {
          console.log('Using cached data');
        } else {
          console.log('Fetched fresh data from Odoo');
        }
        
        setLoading(false);
        setIsRefreshing(false);
      } catch (err) {
        console.error('Error fetching data:', err);
        setError(err.message);
        setLoading(false);
        setIsRefreshing(false);
      }
    };

    fetchData();
  }, [selectedPeriod, viewType, selectedDepartment]); // Depend on period, view type, and department

  // Load shareholders list on mount
  useEffect(() => {
    axios.get('/api/shareholders').then(res => {
      if (res.data && res.data.success) {
        setShareholders(res.data.shareholders || []);
      }
    }).catch(() => {});
  }, []);

  // Load external hours data when external-hours tab is active
  useEffect(() => {
    if (activeTab === 'external-hours') {
      console.log('Fetching external hours data...');
      setLoading(true);
      
      axios.get('/api/external-hours')
        .then(res => {
          console.log('External hours response:', res.data);
          if (res.data && res.data.success) {
            setExternalHoursData(res.data.data);
          } else if (res.data && res.data.error) {
            setError(res.data.error);
          }
        })
        .catch(err => {
          console.error('Error fetching external hours data:', err);
          setError(err.response?.data?.error || 'Failed to fetch external hours data');
        })
        .finally(() => {
          setLoading(false);
        });
    }
  }, [activeTab]);

  const handleRefresh = async () => {
    setIsRefreshing(true);
    setError(null);
    
    try {
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
      
      const { creative, creative_strategy, cached, cache_timestamp } = dataResponse.data;
      
      // Update all departments data
      setAllDepartmentsData({
        creative,
        creative_strategy
      });
      
      // Update current department data
      const currentDepartmentData = selectedDepartment === 'Creative Strategy' ? creative_strategy : selectedDepartment === 'Instructional Design' ? allDepartmentsData.instructional_design : creative;
      
      if (currentDepartmentData) {
        setEmployees(currentDepartmentData.employees || []);
        setTeamUtilizationData(currentDepartmentData.team_utilization || {});
        setTimesheetData(currentDepartmentData.timesheet_data || []);
        setAvailableResources(currentDepartmentData.available_resources || []);
      }
      
      // Update cache status
      setCacheStatus({
        cached,
        last_updated: cache_timestamp
      });
      
      console.log('Cache refreshed successfully');
      
    } catch (err) {
      console.error('Error refreshing cache:', err);
      setError(err.message);
    } finally {
      setIsRefreshing(false);
    }
  };

  const handleDepartmentChange = (department) => {
    console.log(`Switching department to: ${department}`);
    setSelectedDepartment(department);
    
    // Only use cached data if we have data for the current view type and period
            const departmentData = allDepartmentsData[department === 'Creative Strategy' ? 'creative_strategy' : department === 'Instructional Design' ? 'instructional_design' : 'creative'];
    const expectedCacheKey = `${selectedPeriod}_${viewType}`;
    
    if (departmentData && Object.keys(allDepartmentsData).length > 0 && currentCacheKey === expectedCacheKey) {
      console.log(`Using cached data for department switch (cache key matches: ${currentCacheKey})`);
      setEmployees(departmentData.employees || []);
      setTeamUtilizationData(departmentData.team_utilization || {});
      setTimesheetData(departmentData.timesheet_data || []);
      setAvailableResources(departmentData.available_resources || []);
    } else {
      console.log(`No valid cached data available (cache key mismatch or no data), will fetch fresh data`);
      console.log(`Current cache key: ${currentCacheKey}, Expected: ${expectedCacheKey}`);
      // Keep current data visible while fetching; use soft refresh indicator
      setIsRefreshing(true);
    }

    // Ensure we fetch full payload when switching department so view is complete
    const fullUrl = `/api/all-departments-data?period=${selectedPeriod}&view_type=${viewType}&selected_department=${encodeURIComponent(department)}&include=employees,team_utilization,available_resources,timesheet_data`;
    axios.get(fullUrl).then(res => {
      const data = res.data[department === 'Creative Strategy' ? 'creative_strategy' : department === 'Instructional Design' ? 'instructional_design' : 'creative'] || {};
      setEmployees(data.employees || []);
      setTeamUtilizationData(data.team_utilization || {});
      setAvailableResources(data.available_resources || []);
      setTimesheetData(data.timesheet_data || []);
      setIsRefreshing(false);
    }).catch(() => {}).finally(() => {});
    
    // Reset filters when switching departments
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
    
    // Force a fresh data fetch but keep current data visible
    setAllDepartmentsData({});
    setCurrentCacheKey(''); // Clear cache key to force fresh data fetch
    setIsRefreshing(true);
    
    // Note: Removed auto-refresh to prevent conflicts with useEffect
    // The useEffect will handle data fetching automatically when state updates
  };

  const handleViewTypeChange = (newViewType) => {
    console.log(`Switching to ${newViewType} view`);
    console.log(`Current viewType: ${viewType}, newViewType: ${newViewType}`);
    
    // Reset period to first option of the new view type
    // Force a fresh data fetch but keep current data visible
    setAllDepartmentsData({});
    setCurrentCacheKey(''); // Clear cache key to force fresh data fetch
    setIsRefreshing(true);
    
    // Update both state values at the same time to trigger useEffect
    setViewType(newViewType);
    // Reset to first period option when view type changes
    setSelectedPeriod('2025-01'); // Default to first month
    console.log('State update calls completed');
    
    // Note: Removed auto-refresh to prevent conflicts with useEffect
    // The useEffect will handle data fetching automatically when state updates
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
            onClick={() => setActiveTab('employees')}
          >
            👥 Number of {selectedDepartment === 'Creative Strategy' || selectedDepartment === 'Instructional Design' ? 'Team Members' : 'Creatives'} ({employees.length})
          </button>
            <button 
            className={`tab-btn ${activeTab === 'resources' ? 'active' : ''}`}
            onClick={() => setActiveTab('resources')}
          >
            📊 Available {selectedDepartment === 'Creative Strategy' || selectedDepartment === 'Instructional Design' ? 'Team Members' : 'Creatives'} ({availableResources.length})
          </button>
            <button 
            className={`tab-btn ${activeTab === 'timesheet' ? 'active' : ''}`}
            onClick={() => setActiveTab('timesheet')}
          >
            ⏱️ Active {selectedDepartment === 'Creative Strategy' || selectedDepartment === 'Instructional Design' ? 'Team Members' : 'Creatives'} ({activeTimesheetCount})
          </button>
          <button 
            className={`tab-btn ${activeTab === 'external-hours' ? 'active' : ''}`}
            onClick={() => setActiveTab('external-hours')}
          >
            🌐 External Hours
          </button>
          <button 
            className={`tab-btn ${activeTab === 'utilization' ? 'active' : ''}`}
            onClick={() => setActiveTab('utilization')}
          >
            📈 Utilization Dashboard
          </button>
        </div>

        {/* View Type and Period Selector for Resources, Timesheet, External Hours, and Utilization Tabs */}
        {(activeTab === 'resources' || activeTab === 'timesheet' || activeTab === 'external-hours' || activeTab === 'utilization') && (
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

        {activeTab === 'external-hours' && (
          <div className="tab-content">
            <div className="external-hours-dashboard">
              <h2>External Hours Dashboard</h2>
              <p>Calculate external hours for KSA and UAE pools based on retainer contracts with 'In Progress' subscription status.</p>
              
              <div className="external-hours-stats">
                <div className="pool-stats">
                  <div className="pool-stat-card">
                    <h3>🇸🇦 KSA Pool</h3>
                    <div className="stat-number">{formatDecimalHours(externalHoursData.ksa.totalHours)}</div>
                    <div className="stat-label">Total External Hours</div>
                    <div className="contracts-count">{externalHoursData.ksa.contracts.length} Active Contracts</div>
                  </div>
                  
                  <div className="pool-stat-card">
                    <h3>🇦🇪 UAE Pool</h3>
                    <div className="stat-number">{formatDecimalHours(externalHoursData.uae.totalHours)}</div>
                    <div className="stat-label">Total External Hours</div>
                    <div className="contracts-count">{externalHoursData.uae.contracts.length} Active Contracts</div>
                  </div>
                  
                  <div className="pool-stat-card total-stat">
                    <h3>📊 Total</h3>
                    <div className="stat-number">
                      {formatDecimalHours(externalHoursData.ksa.totalHours + externalHoursData.uae.totalHours)}
                    </div>
                    <div className="stat-label">Combined External Hours</div>
                    <div className="contracts-count">
                      {externalHoursData.ksa.contracts.length + externalHoursData.uae.contracts.length} Total Contracts
                    </div>
                  </div>
                </div>
                
                <div className="contracts-details">
                  <h3>Contract Details</h3>
                  <div className="contracts-grid">
                    {/* KSA Contracts */}
                    <div className="contracts-section">
                      <h4>🇸🇦 KSA Contracts ({externalHoursData.ksa.contracts.length})</h4>
                      {externalHoursData.ksa.contracts.length === 0 ? (
                        <div className="no-contracts">No KSA contracts found with 'In Progress' status</div>
                      ) : (
                        <div className="contracts-list">
                          {externalHoursData.ksa.contracts.map((contract, index) => (
                            <div key={index} className="contract-card">
                              <div className="contract-header">
                                <h5>{contract.partner_name}</h5>
                                <span className="contract-status">{contract.subscription_status}</span>
                              </div>
                              <div className="contract-details">
                                <div className="contract-hours">
                                  <span className="hours-label">External Hours:</span>
                                  <span className="hours-value">{formatDecimalHours(contract.external_hours || 0)}</span>
                                </div>
                                <div className="contract-market">
                                  <span className="market-label">Market:</span>
                                  <span className="market-value">{contract.market}</span>
                                </div>
                              </div>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                    
                    {/* UAE Contracts */}
                    <div className="contracts-section">
                      <h4>🇦🇪 UAE Contracts ({externalHoursData.uae.contracts.length})</h4>
                      {externalHoursData.uae.contracts.length === 0 ? (
                        <div className="no-contracts">No UAE contracts found with 'In Progress' status</div>
                      ) : (
                        <div className="contracts-list">
                          {externalHoursData.uae.contracts.map((contract, index) => (
                            <div key={index} className="contract-card">
                              <div className="contract-header">
                                <h5>{contract.partner_name}</h5>
                                <span className="contract-status">{contract.subscription_status}</span>
                              </div>
                              <div className="contract-details">
                                <div className="contract-hours">
                                  <span className="hours-label">External Hours:</span>
                                  <span className="hours-value">{formatDecimalHours(contract.external_hours || 0)}</span>
                                </div>
                                <div className="contract-market">
                                  <span className="market-label">Market:</span>
                                  <span className="market-value">{contract.market}</span>
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
                <p className="shareholder-subtext">Send weekly utilization summaries (weekly view) to saved emails.</p>
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
                  >Preview</button>
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
                  >{isSending ? 'Sending...' : 'Send Weekly Now'}</button>
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
                          >Send Test</button>
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
      {cacheStatus.last_updated && (
        <div className="unified-cache-refresh">
          <div className="cache-status-section">
            <span className="cache-indicator">
              {cacheStatus.cached ? '📦 Cached' : '🔄 Fresh'}
            </span>
            <span className="cache-time">
              Last updated: {new Date(cacheStatus.last_updated * 1000).toLocaleTimeString()}
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