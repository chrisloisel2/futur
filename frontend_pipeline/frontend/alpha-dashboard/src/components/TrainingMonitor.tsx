import React, { useState, useEffect, useRef } from 'react';
import { DataService } from '../services/DataService';

interface TrainingJob {
  job_id: string;
  status: 'running' | 'launching' | 'completed' | 'failed' | 'stopped';
  config_path: string;
  device: string;
  debug_mode: boolean;
  start_time: string;
  end_time?: string;
  current_epoch: number;
  total_epochs: number;
  progress_pct: number;
  current_loss: number;
  current_val_loss: number;
  current_sharpe: number;
  error?: string;
  is_aws?: boolean;
  instance_type?: string;
  aws_region?: string;
  aws_instance_id?: string;
  aws_public_ip?: string;
  aws_s3_path?: string;
  is_remote?: boolean;
  remote_host?: string;
  remote_user?: string;
  remote_work_dir?: string;
}

const TrainingJobCard: React.FC<{ job: TrainingJob; onRefresh: () => void }> = ({ job, onRefresh }) => {
  const [stopping, setStopping] = useState(false);
  const [showLogs, setShowLogs] = useState(false);
  const [logs, setLogs] = useState<string[]>([]);
  const [loadingLogs, setLoadingLogs] = useState(false);
  const [cost, setCost] = useState<any>(null);

  // Fetch AWS cost for AWS jobs
  useEffect(() => {
    if (job.is_aws) {
      const fetchCost = async () => {
        try {
          const data = await DataService.getTrainingCost(job.job_id);
          setCost(data);
        } catch (err) {
          console.error('Failed to get training cost:', err);
        }
      };

      // Fetch immediately and then every 30 seconds
      fetchCost();
      const interval = setInterval(fetchCost, 30000);
      return () => clearInterval(interval);
    }
  }, [job.is_aws, job.job_id]);

  const handleStop = async () => {
    if (!window.confirm(`Are you sure you want to stop training job ${job.job_id}?`)) {
      return;
    }

    setStopping(true);
    try {
      await DataService.stopTraining(job.job_id);
      onRefresh();
    } catch (err) {
      alert(`Failed to stop training: ${err instanceof Error ? err.message : 'Unknown error'}`);
    } finally {
      setStopping(false);
    }
  };

  const loadLogs = async () => {
    setLoadingLogs(true);
    try {
      const data = await DataService.getTrainingLogs(job.job_id, 50);
      setLogs(data.logs);
    } catch (err) {
      console.error('Failed to load logs:', err);
    } finally {
      setLoadingLogs(false);
    }
  };

  const toggleLogs = async () => {
    if (!showLogs) {
      await loadLogs();
    }
    setShowLogs(!showLogs);
  };

  const getStatusClass = (status: string) => {
    switch (status) {
      case 'running':
        return 'status-running';
      case 'launching':
        return 'status-launching';
      case 'completed':
        return 'status-completed';
      case 'failed':
        return 'status-failed';
      case 'stopped':
        return 'status-stopped';
      default:
        return 'status-unknown';
    }
  };

  const getElapsedTime = () => {
    const start = new Date(job.start_time).getTime();
    const end = job.end_time ? new Date(job.end_time).getTime() : Date.now();
    const diff = end - start;

    const hours = Math.floor(diff / (1000 * 60 * 60));
    const minutes = Math.floor((diff % (1000 * 60 * 60)) / (1000 * 60));
    const seconds = Math.floor((diff % (1000 * 60)) / 1000);

    if (hours > 0) {
      return `${hours}h ${minutes}m ${seconds}s`;
    } else if (minutes > 0) {
      return `${minutes}m ${seconds}s`;
    } else {
      return `${seconds}s`;
    }
  };

  return (
    <div className={`training-job-card ${getStatusClass(job.status)}`}>
      <div className="job-card-header">
        <div className="job-title">
          <h3>{job.job_id}</h3>
          <span className={`status-badge ${getStatusClass(job.status)}`}>
            {job.status.toUpperCase()}
          </span>
        </div>
        <div className="job-time">
          <span className="time-label">Time: </span>
          <span className="time-value">{getElapsedTime()}</span>
        </div>
      </div>

      <div className="job-card-body">
        <div className="job-info">
          <div className="info-item">
            <span className="info-label">Config:</span>
            <span className="info-value">{job.config_path.split('/').pop()}</span>
          </div>
          <div className="info-item">
            <span className="info-label">Device:</span>
            <span className="info-value">{job.device}</span>
          </div>
          {job.debug_mode && (
            <div className="info-item">
              <span className="debug-badge">DEBUG MODE</span>
            </div>
          )}
        </div>

        {job.is_aws && (
          <div className="aws-info">
            <div className="aws-badge">
              <span className="aws-icon">☁️</span>
              <span>AWS EC2</span>
            </div>
            {job.instance_type && (
              <div className="info-item">
                <span className="info-label">Instance:</span>
                <span className="info-value">{job.instance_type}</span>
              </div>
            )}
            {job.aws_instance_id && (
              <div className="info-item">
                <span className="info-label">Instance ID:</span>
                <span className="info-value">{job.aws_instance_id}</span>
              </div>
            )}
            {job.aws_public_ip && (
              <div className="info-item">
                <span className="info-label">IP:</span>
                <span className="info-value">{job.aws_public_ip}</span>
              </div>
            )}
            {cost && cost.success && (
              <div className="cost-display">
                <span className="cost-label">Estimated Cost:</span>
                <span className="cost-value">${cost.cost_usd.toFixed(2)}</span>
                <span className="cost-detail">
                  ({cost.duration_hours.toFixed(1)}h @ ${cost.hourly_rate_usd}/h)
                </span>
              </div>
            )}
          </div>
        )}

        {job.is_remote && (
          <div className="remote-info">
            <div className="remote-badge">
              <span className="location-icon">🖥️</span>
              <span>Remote Server</span>
            </div>
            {job.remote_host && (
              <div className="info-item">
                <span className="info-label">Host:</span>
                <span className="info-value">{job.remote_host}</span>
              </div>
            )}
            {job.remote_user && (
              <div className="info-item">
                <span className="info-label">User:</span>
                <span className="info-value">{job.remote_user}</span>
              </div>
            )}
            {job.remote_work_dir && (
              <div className="info-item">
                <span className="info-label">Work Directory:</span>
                <span className="info-value">{job.remote_work_dir}</span>
              </div>
            )}
          </div>
        )}

        <div className="progress-section">
          <div className="progress-header">
            <span>Progress: {Math.round(job.progress_pct)}%</span>
            <span>Epoch {job.current_epoch}/{job.total_epochs}</span>
          </div>
          <div className="progress-bar">
            <div
              className="progress-bar-fill"
              style={{ width: `${Math.min(job.progress_pct, 100)}%` }}
            />
          </div>
        </div>

        {job.status === 'running' && (
          <div className="metrics-grid">
            <div className="metric-item">
              <div className="metric-label">Train Loss</div>
              <div className="metric-value">{job.current_loss.toFixed(4)}</div>
            </div>
            <div className="metric-item">
              <div className="metric-label">Val Loss</div>
              <div className="metric-value">{job.current_val_loss.toFixed(4)}</div>
            </div>
            <div className="metric-item">
              <div className="metric-label">Val Sharpe</div>
              <div className="metric-value">{job.current_sharpe.toFixed(4)}</div>
            </div>
          </div>
        )}

        {job.error && (
          <div className="job-error">
            <span className="error-icon">⚠️</span>
            <span>{job.error}</span>
          </div>
        )}
      </div>

      <div className="job-card-footer">
        <button className="btn btn-secondary" onClick={toggleLogs}>
          {showLogs ? '📄 Hide Logs' : '📄 View Logs'}
        </button>

        {(job.status === 'running' || job.status === 'launching') && (
          <button
            className="btn btn-danger"
            onClick={handleStop}
            disabled={stopping}
          >
            {stopping ? 'Stopping...' : (job.is_aws ? '⏹️ Terminate Instance' : '⏹️ Stop Training')}
          </button>
        )}
      </div>

      {showLogs && (
        <div className="logs-panel">
          {loadingLogs ? (
            <div className="logs-loading">
              <div className="spinner-small"></div>
              <span>Loading logs...</span>
            </div>
          ) : (
            <div className="logs-content">
              {logs.length === 0 ? (
                <p className="logs-empty">No logs available yet</p>
              ) : (
                logs.map((line, index) => (
                  <div key={index} className="log-line">
                    {line}
                  </div>
                ))
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
};

const TrainingMonitor: React.FC = () => {
  const [jobs, setJobs] = useState<TrainingJob[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const updateInterval = useRef<NodeJS.Timeout | null>(null);

  const fetchJobs = async () => {
    try {
      const data = await DataService.getAllTrainingJobs();
      setJobs(data.jobs);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load training jobs');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchJobs();

    // Auto-refresh every 5 seconds
    updateInterval.current = setInterval(fetchJobs, 5000);

    return () => {
      if (updateInterval.current) {
        clearInterval(updateInterval.current);
      }
    };
  }, []);

  if (loading) {
    return (
      <div className="training-monitor">
        <div className="loading-state">
          <div className="spinner"></div>
          <p>Loading training jobs...</p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="training-monitor">
        <div className="error-state">
          <span className="error-icon">⚠️</span>
          <p>{error}</p>
          <button className="btn btn-primary" onClick={fetchJobs}>
            Retry
          </button>
        </div>
      </div>
    );
  }

  if (jobs.length === 0) {
    return (
      <div className="training-monitor">
        <div className="empty-state">
          <div className="empty-icon">📚</div>
          <h3>No Training Jobs</h3>
          <p>Start a new training job to see it here.</p>
        </div>
      </div>
    );
  }

  return (
    <div className="training-monitor">
      <div className="monitor-header">
        <h3>
          {jobs.filter((j) => j.status === 'running').length} Active Jobs
        </h3>
        <button className="btn btn-sm" onClick={fetchJobs}>
          🔄 Refresh
        </button>
      </div>

      <div className="jobs-grid">
        {jobs.map((job) => (
          <TrainingJobCard key={job.job_id} job={job} onRefresh={fetchJobs} />
        ))}
      </div>
    </div>
  );
};

export default TrainingMonitor;
