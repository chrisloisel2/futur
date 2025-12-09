/**
 * Composant de diagnostic pour tester la connexion API
 */
import React, { useState, useEffect } from 'react';

interface TestResult {
  url: string;
  result: any;
  error: string | null;
}

interface TestResults {
  [key: string]: TestResult;
}

const DiagnosticTest: React.FC = () => {
  const [status, setStatus] = useState<string>('Testing...');
  const [details, setDetails] = useState<TestResults | null>(null);

  useEffect(() => {
    testAPI();
  }, []);

  const testAPI = async () => {
    const tests: TestResults = {
      'API Health': { url: 'http://localhost:8000/health', result: null, error: null },
      'BTC/USDT Data': { url: 'http://localhost:8000/api/historical/BTC/USDT?limit=2', result: null, error: null },
      'DOGE/USDT Data': { url: 'http://localhost:8000/api/historical/DOGE/USDT?limit=2', result: null, error: null }
    };

    for (const [testName, test] of Object.entries(tests)) {
      try {
        console.log(`Testing ${testName}: ${test.url}`);
        const response = await fetch(test.url);

        if (!response.ok) {
          test.error = `HTTP ${response.status}: ${response.statusText}`;
        } else {
          const data = await response.json();
          test.result = data;
          console.log(`${testName} SUCCESS:`, data);
        }
      } catch (error) {
        test.error = error instanceof Error ? error.message : 'Unknown error';
        console.error(`${testName} ERROR:`, error);
      }
    }

    const allPassed = Object.values(tests).every((t) => t.result !== null && t.error === null);
    setStatus(allPassed ? '✅ ALL TESTS PASSED' : '❌ SOME TESTS FAILED');
    setDetails(tests);
  };

  return (
    <div style={{ padding: '20px', background: '#0B0E11', color: '#D1D4DC', fontFamily: 'monospace' }}>
      <h1>API Diagnostic Test</h1>
      <h2 style={{ color: status.includes('✅') ? '#26A69A' : '#EF5350' }}>{status}</h2>

      <button
        onClick={testAPI}
        style={{
          background: '#26A69A',
          color: 'white',
          border: 'none',
          padding: '10px 20px',
          borderRadius: '5px',
          cursor: 'pointer',
          fontSize: '16px',
          marginBottom: '20px'
        }}
      >
        Re-test
      </button>

      {details && (
        <div>
          {Object.entries(details).map(([testName, test]) => (
            <div key={testName} style={{
              marginBottom: '20px',
              padding: '15px',
              background: '#1a1d29',
              borderRadius: '5px',
              borderLeft: `4px solid ${test.error ? '#EF5350' : '#26A69A'}`
            }}>
              <h3>{test.error ? '❌' : '✅'} {testName}</h3>
              <div style={{ fontSize: '12px', color: '#8E9098', marginBottom: '10px' }}>
                URL: {test.url}
              </div>

              {test.error && (
                <div style={{ color: '#EF5350', marginBottom: '10px' }}>
                  Error: {test.error}
                </div>
              )}

              {test.result && (
                <pre style={{
                  background: '#0B0E11',
                  padding: '10px',
                  borderRadius: '3px',
                  overflow: 'auto',
                  maxHeight: '300px'
                }}>
                  {JSON.stringify(test.result, null, 2)}
                </pre>
              )}
            </div>
          ))}
        </div>
      )}

      <div style={{ marginTop: '30px', padding: '15px', background: '#1a1d29', borderRadius: '5px' }}>
        <h3>Instructions</h3>
        <ol style={{ lineHeight: '1.8' }}>
          <li>Tous les tests doivent être verts (✅)</li>
          <li>Si un test échoue:
            <ul>
              <li>Vérifiez que l'API tourne: <code>ps aux | grep api_server</code></li>
              <li>Vérifiez les CORS dans la console</li>
              <li>Redémarrez l'API: <code>python api_server.py</code></li>
            </ul>
          </li>
          <li>Ouvrez la console du navigateur (F12) pour voir les logs détaillés</li>
        </ol>
      </div>
    </div>
  );
};

export default DiagnosticTest;
