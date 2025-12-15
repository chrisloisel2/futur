import React from 'react';
import TrainingControl from './TrainingControl';
import TrainingMonitor from './TrainingMonitor';
import ModelVersions from './ModelVersions';
import './TrainingDashboard.css';

const TrainingDashboard: React.FC = () => {
  return (
    <div className="training-dashboard">
      <header className="dashboard-modern-header">
        <div className="header-content">
          <h1 className="dashboard-modern-title">Model Training</h1>
          <p className="dashboard-modern-subtitle">
            Manage and monitor AI model training jobs
          </p>
        </div>
      </header>

      <div className="dashboard-modern-grid">
        {/* Training Control */}
        <div className="modern-card">
          <div className="card-header-modern">
            <h2 className="card-title-modern">
              <span className="card-icon">🚀</span>
              Start New Training
            </h2>
          </div>
          <div className="card-body-modern">
            <TrainingControl />
          </div>
        </div>

        {/* Active Training Jobs */}
        <div className="modern-card full-width">
          <div className="card-header-modern">
            <h2 className="card-title-modern">
              <span className="card-icon">📊</span>
              Active Training Jobs
            </h2>
          </div>
          <div className="card-body-modern">
            <TrainingMonitor />
          </div>
        </div>

        {/* Model Versions */}
        <div className="modern-card full-width">
          <div className="card-header-modern">
            <h2 className="card-title-modern">
              <span className="card-icon">🤖</span>
              Model Versions
            </h2>
          </div>
          <div className="card-body-modern">
            <ModelVersions />
          </div>
        </div>
      </div>
    </div>
  );
};

export default TrainingDashboard;
