import React, { useState, useEffect } from 'react';
import { DataService } from '../services/DataService';

const TrainingControl: React.FC = () => {
  const [configs, setConfigs] = useState<string[]>([]);
  const [selectedConfig, setSelectedConfig] = useState('');
  const [device, setDevice] = useState('auto');
  const [debugMode, setDebugMode] = useState(false);
  const [trainingLocation, setTrainingLocation] = useState<'aws' | 'remote' | 'local'>('aws'); // Default to AWS
  const [instanceType, setInstanceType] = useState('g4dn.xlarge');
  const [awsRegion, setAwsRegion] = useState('eu-west-3');
  const [remoteHost, setRemoteHost] = useState('100.118.183.51');
  const [remoteUser, setRemoteUser] = useState('qbee');
  const [loading, setLoading] = useState(false);
  const [loadingConfigs, setLoadingConfigs] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  useEffect(() => {
    loadConfigs();
  }, []);

  const loadConfigs = async () => {
    try {
      setLoadingConfigs(true);
      const data = await DataService.getTrainingConfigs();
      setConfigs(data.configs);
      if (data.configs.length > 0) {
        setSelectedConfig(data.configs[0]);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load configs');
    } finally {
      setLoadingConfigs(false);
    }
  };

  const handleStartTraining = async () => {
    if (!selectedConfig) {
      setError('Please select a configuration');
      return;
    }

    setLoading(true);
    setError(null);
    setSuccess(null);

    try {
      const result = await DataService.startTraining(
        selectedConfig,
        device,
        debugMode,
        trainingLocation,
        instanceType,
        awsRegion,
        remoteHost,
        remoteUser
      );
      let location = 'local machine';
      if (result.is_aws) {
        location = `AWS EC2 (${result.instance_type})`;
      } else if (result.is_remote) {
        location = `Remote Server (${result.remote_host})`;
      }
      setSuccess(`Training started on ${location}! Job ID: ${result.job_id}`);

      // Reset form after 5 seconds
      setTimeout(() => {
        setSuccess(null);
      }, 5000);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to start training');
    } finally {
      setLoading(false);
    }
  };

  if (loadingConfigs) {
    return (
      <div className="training-control">
        <div className="loading-state">
          <div className="spinner"></div>
          <p>Loading configurations...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="training-control">
      {error && (
        <div className="alert alert-error">
          <span className="alert-icon">⚠️</span>
          <span>{error}</span>
        </div>
      )}

      {success && (
        <div className="alert alert-success">
          <span className="alert-icon">✅</span>
          <span>{success}</span>
        </div>
      )}

      <div className="form-group">
        <label htmlFor="config-select" className="form-label">
          Training Configuration
        </label>
        <select
          id="config-select"
          className="form-select"
          value={selectedConfig}
          onChange={(e) => setSelectedConfig(e.target.value)}
          disabled={loading}
        >
          {configs.map((config) => (
            <option key={config} value={config}>
              {config}
            </option>
          ))}
        </select>
        <p className="form-help">
          Select the training configuration file to use
        </p>
      </div>

      <div className="form-group">
        <label className="form-label">Training Location</label>
        <div className="radio-group">
          <label className="form-radio aws-radio">
            <input
              type="radio"
              name="trainingLocation"
              value="aws"
              checked={trainingLocation === 'aws'}
              onChange={(e) => setTrainingLocation('aws')}
              disabled={loading}
            />
            <span className="radio-label">
              <span className="location-icon">☁️</span>
              <span>AWS EC2 (GPU Recommended)</span>
            </span>
          </label>
          <label className="form-radio remote-radio">
            <input
              type="radio"
              name="trainingLocation"
              value="remote"
              checked={trainingLocation === 'remote'}
              onChange={(e) => setTrainingLocation('remote')}
              disabled={loading}
            />
            <span className="radio-label">
              <span className="location-icon">🖥️</span>
              <span>Remote Server (100.118.183.51)</span>
            </span>
          </label>
          <label className="form-radio local-radio">
            <input
              type="radio"
              name="trainingLocation"
              value="local"
              checked={trainingLocation === 'local'}
              onChange={(e) => setTrainingLocation('local')}
              disabled={loading}
            />
            <span className="radio-label">
              <span className="location-icon">💻</span>
              <span>Local Machine</span>
            </span>
          </label>
        </div>
        <p className="form-help">
          {trainingLocation === 'aws' && 'Train on AWS EC2 with GPU for 10-50x faster training'}
          {trainingLocation === 'remote' && 'Train on your remote server via SSH'}
          {trainingLocation === 'local' && 'Train on your local machine'}
        </p>
      </div>

      {trainingLocation === 'aws' ? (
        <>
          <div className="form-group">
            <label htmlFor="instance-select" className="form-label">
              AWS Instance Type
            </label>
            <select
              id="instance-select"
              className="form-select"
              value={instanceType}
              onChange={(e) => setInstanceType(e.target.value)}
              disabled={loading}
            >
              <option value="g4dn.xlarge">g4dn.xlarge - T4 GPU, 16GB RAM (~$0.53/h)</option>
              <option value="g4dn.2xlarge">g4dn.2xlarge - T4 GPU, 32GB RAM (~$0.75/h)</option>
              <option value="p3.2xlarge">p3.2xlarge - V100 GPU, 61GB RAM (~$3.06/h)</option>
              <option value="t3.large">t3.large - CPU only, 8GB RAM (~$0.08/h)</option>
            </select>
            <p className="form-help">
              GPU instances provide significantly faster training
            </p>
          </div>

          <div className="form-group">
            <label htmlFor="region-select" className="form-label">
              AWS Region
            </label>
            <select
              id="region-select"
              className="form-select"
              value={awsRegion}
              onChange={(e) => setAwsRegion(e.target.value)}
              disabled={loading}
            >
              <option value="eu-west-3">eu-west-3 (Paris)</option>
              <option value="us-east-1">us-east-1 (N. Virginia)</option>
              <option value="us-west-2">us-west-2 (Oregon)</option>
            </select>
            <p className="form-help">
              Select the AWS region closest to you
            </p>
          </div>
        </>
      ) : trainingLocation === 'remote' ? (
        <>
          <div className="form-group">
            <label htmlFor="remote-host" className="form-label">
              Remote Host
            </label>
            <input
              id="remote-host"
              type="text"
              className="form-input"
              value={remoteHost}
              onChange={(e) => setRemoteHost(e.target.value)}
              disabled={loading}
              placeholder="100.118.183.51"
            />
            <p className="form-help">
              SSH host address of the remote training server
            </p>
          </div>

          <div className="form-group">
            <label htmlFor="remote-user" className="form-label">
              SSH User
            </label>
            <input
              id="remote-user"
              type="text"
              className="form-input"
              value={remoteUser}
              onChange={(e) => setRemoteUser(e.target.value)}
              disabled={loading}
              placeholder="qbee"
            />
            <p className="form-help">
              SSH username for the remote server
            </p>
          </div>

          <div className="form-group">
            <label htmlFor="device-select" className="form-label">
              Device
            </label>
            <select
              id="device-select"
              className="form-select"
              value={device}
              onChange={(e) => setDevice(e.target.value)}
              disabled={loading}
            >
              <option value="auto">Auto (Recommended)</option>
              <option value="cuda">CUDA (NVIDIA GPU)</option>
              <option value="cpu">CPU</option>
            </select>
            <p className="form-help">
              Device to use on the remote server
            </p>
          </div>
        </>
      ) : (
        <div className="form-group">
          <label htmlFor="device-select" className="form-label">
            Device
          </label>
          <select
            id="device-select"
            className="form-select"
            value={device}
            onChange={(e) => setDevice(e.target.value)}
            disabled={loading}
          >
            <option value="auto">Auto (Recommended)</option>
            <option value="mps">MPS (Apple Silicon)</option>
            <option value="cpu">CPU</option>
          </select>
          <p className="form-help">
            Auto will automatically select MPS if available, otherwise CPU
          </p>
        </div>
      )}

      <div className="form-group">
        <label className="form-checkbox">
          <input
            type="checkbox"
            checked={debugMode}
            onChange={(e) => setDebugMode(e.target.checked)}
            disabled={loading}
          />
          <span className="checkbox-label">Debug Mode</span>
        </label>
        <p className="form-help">
          Run a single epoch with limited batches for quick testing
        </p>
      </div>

      <button
        className="btn btn-primary btn-large"
        onClick={handleStartTraining}
        disabled={loading || !selectedConfig}
      >
        {loading ? (
          <>
            <span className="spinner-small"></span>
            <span>Starting Training...</span>
          </>
        ) : (
          <>
            <span>🚀</span>
            <span>Start Training</span>
          </>
        )}
      </button>

      {selectedConfig && (
        <div className="config-info">
          <h4>Selected Configuration:</h4>
          <div className="config-badge">{selectedConfig}</div>
        </div>
      )}
    </div>
  );
};

export default TrainingControl;
