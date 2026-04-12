import React, { useState, useEffect } from 'react';
import { DataService } from '../services/DataService';

interface ModelVersion {
  filename: string;
  path: string;
  created_at: string;
  size_mb: number;
  metadata: any;
  is_production: boolean;
}

const ModelVersionCard: React.FC<{ model: ModelVersion; onRefresh: () => void }> = ({ model, onRefresh }) => {
  const [settingProduction, setSettingProduction] = useState(false);

  const handleSetProduction = async () => {
    if (!window.confirm(`Set ${model.filename} as the production model?`)) {
      return;
    }

    setSettingProduction(true);
    try {
      await DataService.setProductionModel(model.filename);
      onRefresh();
    } catch (err) {
      alert(`Failed to set production model: ${err instanceof Error ? err.message : 'Unknown error'}`);
    } finally {
      setSettingProduction(false);
    }
  };

  const formatDate = (dateString: string) => {
    const date = new Date(dateString);
    return date.toLocaleString();
  };

  const hasMetadata = model.metadata && Object.keys(model.metadata).length > 0;

  return (
    <div className={`model-version-card ${model.is_production ? 'production' : ''}`}>
      {model.is_production && (
        <div className="production-badge">
          ⭐ PRODUCTION
        </div>
      )}

      <div className="model-card-header">
        <h3 className="model-filename">{model.filename}</h3>
        <div className="model-size">{model.size_mb} MB</div>
      </div>

      <div className="model-card-body">
        <div className="model-info-row">
          <span className="info-label">Created:</span>
          <span className="info-value">{formatDate(model.created_at)}</span>
        </div>

        {hasMetadata && (
          <>
            {model.metadata.config_path && (
              <div className="model-info-row">
                <span className="info-label">Config:</span>
                <span className="info-value">{model.metadata.config_path.split('/').pop()}</span>
              </div>
            )}

            {model.metadata.device && (
              <div className="model-info-row">
                <span className="info-label">Device:</span>
                <span className="info-value">{model.metadata.device}</span>
              </div>
            )}

            {model.metadata.total_epochs > 0 && (
              <div className="metrics-section">
                <h4>Training Metrics</h4>
                <div className="metrics-grid-compact">
                  <div className="metric-compact">
                    <div className="metric-label-compact">Epochs</div>
                    <div className="metric-value-compact">{model.metadata.current_epoch}/{model.metadata.total_epochs}</div>
                  </div>
                  {model.metadata.current_loss > 0 && (
                    <div className="metric-compact">
                      <div className="metric-label-compact">Loss</div>
                      <div className="metric-value-compact">{model.metadata.current_loss.toFixed(4)}</div>
                    </div>
                  )}
                  {model.metadata.current_val_loss > 0 && (
                    <div className="metric-compact">
                      <div className="metric-label-compact">Val Loss</div>
                      <div className="metric-value-compact">{model.metadata.current_val_loss.toFixed(4)}</div>
                    </div>
                  )}
                  {model.metadata.current_sharpe > 0 && (
                    <div className="metric-compact">
                      <div className="metric-label-compact">Sharpe</div>
                      <div className="metric-value-compact">{model.metadata.current_sharpe.toFixed(4)}</div>
                    </div>
                  )}
                </div>
              </div>
            )}

            {model.metadata.status && (
              <div className="model-status">
                <span className={`status-badge status-${model.metadata.status}`}>
                  {model.metadata.status.toUpperCase()}
                </span>
              </div>
            )}
          </>
        )}

        {!hasMetadata && (
          <div className="no-metadata">
            <p>No training metadata available</p>
          </div>
        )}
      </div>

      <div className="model-card-footer">
        {!model.is_production && (
          <button
            className="btn btn-primary btn-sm"
            onClick={handleSetProduction}
            disabled={settingProduction}
          >
            {settingProduction ? 'Setting...' : '⭐ Set as Production'}
          </button>
        )}
      </div>
    </div>
  );
};

const ModelVersions: React.FC = () => {
  const [models, setModels] = useState<ModelVersion[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filterStatus, setFilterStatus] = useState<string>('all');

  const fetchModels = async () => {
    try {
      setLoading(true);
      const data = await DataService.getModelVersions();
      setModels(data.models);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load model versions');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchModels();
  }, []);

  const filteredModels = models.filter((model) => {
    if (filterStatus === 'production') {
      return model.is_production;
    } else if (filterStatus === 'completed') {
      return model.metadata?.status === 'completed';
    } else if (filterStatus === 'failed') {
      return model.metadata?.status === 'failed';
    }
    return true; // 'all'
  });

  if (loading) {
    return (
      <div className="model-versions">
        <div className="loading-state">
          <div className="spinner"></div>
          <p>Loading model versions...</p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="model-versions">
        <div className="error-state">
          <span className="error-icon">⚠️</span>
          <p>{error}</p>
          <button className="btn btn-primary" onClick={fetchModels}>
            Retry
          </button>
        </div>
      </div>
    );
  }

  if (models.length === 0) {
    return (
      <div className="model-versions">
        <div className="empty-state">
          <div className="empty-icon">🤖</div>
          <h3>No Model Versions</h3>
          <p>Train a model to see versions here.</p>
        </div>
      </div>
    );
  }

  return (
    <div className="model-versions">
      <div className="versions-header">
        <div className="versions-stats">
          <span className="stat-item">
            <strong>{models.length}</strong> Total Models
          </span>
          <span className="stat-item">
            <strong>{models.filter((m) => m.is_production).length}</strong> Production
          </span>
          <span className="stat-item">
            <strong>{models.filter((m) => m.metadata?.status === 'completed').length}</strong> Completed
          </span>
        </div>

        <div className="versions-filters">
          <button
            className={`filter-btn ${filterStatus === 'all' ? 'active' : ''}`}
            onClick={() => setFilterStatus('all')}
          >
            All
          </button>
          <button
            className={`filter-btn ${filterStatus === 'production' ? 'active' : ''}`}
            onClick={() => setFilterStatus('production')}
          >
            Production
          </button>
          <button
            className={`filter-btn ${filterStatus === 'completed' ? 'active' : ''}`}
            onClick={() => setFilterStatus('completed')}
          >
            Completed
          </button>
          <button
            className={`filter-btn ${filterStatus === 'failed' ? 'active' : ''}`}
            onClick={() => setFilterStatus('failed')}
          >
            Failed
          </button>
        </div>
      </div>

      <div className="model-versions-grid">
        {filteredModels.map((model) => (
          <ModelVersionCard key={model.filename} model={model} onRefresh={fetchModels} />
        ))}
      </div>

      {filteredModels.length === 0 && (
        <div className="empty-filter-state">
          <p>No models match the selected filter</p>
        </div>
      )}
    </div>
  );
};

export default ModelVersions;
