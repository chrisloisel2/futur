import React, { useState, useEffect } from 'react';
import './App.css';
import Dashboard from './components/Dashboard';
import { DataService } from './services/DataService';

function App() {
  const [dataLoaded, setDataLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // Test API connection
    DataService.getSummary()
      .then(() => setDataLoaded(true))
      .catch((err) => setError(err.message));
  }, []);

  return (
    <div className="App">
      {error ? (
        <div className="error-container">
          <h2>⚠️ API Error</h2>
          <p>{error}</p>
          <p>Make sure the API server is running:</p>
          <code>python api_server.py</code>
        </div>
      ) : !dataLoaded ? (
        <div className="loading-container">
          <div className="spinner"></div>
          <p>Loading Alpha Trading Dashboard...</p>
        </div>
      ) : (
        <Dashboard />
      )}
    </div>
  );
}

export default App;
