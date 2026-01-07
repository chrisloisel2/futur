import React, { useEffect, useRef, useState } from 'react';

interface Level {
  id: number;
  name: string;
  description: string;
  icon: string;
  color: string;
  component: any;
}

interface ArchitectureFlowProps {
  levels: Level[];
  onLevelClick: (id: number) => void;
  selectedLevel: number | null;
  levelData: any;
}

interface FlowParticle {
  id: number;
  x: number;
  y: number;
  level: number;
  speed: number;
  type: 'data' | 'prediction' | 'action';
}

const ArchitectureFlow: React.FC<ArchitectureFlowProps> = ({
  levels,
  onLevelClick,
  selectedLevel,
  levelData
}) => {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [particles, setParticles] = useState<FlowParticle[]>([]);
  const [hoveredLevel, setHoveredLevel] = useState<number | null>(null);
  const animationFrameRef = useRef<number | undefined>(undefined);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const updateCanvas = () => {
      canvas.width = canvas.offsetWidth;
      canvas.height = canvas.offsetHeight;
    };

    updateCanvas();
    window.addEventListener('resize', updateCanvas);

    return () => window.removeEventListener('resize', updateCanvas);
  }, []);

  useEffect(() => {
    const particleInterval = setInterval(() => {
      setParticles(prev => {
        const newParticles = prev.map(p => ({
          ...p,
          y: p.y + p.speed
        })).filter(p => p.y < 1000);

        if (Math.random() > 0.7) {
          newParticles.push({
            id: Date.now(),
            x: Math.random() * 100,
            y: 0,
            level: 0,
            speed: 2 + Math.random() * 2,
            type: Math.random() > 0.5 ? 'data' : 'prediction'
          });
        }

        return newParticles;
      });
    }, 100);

    return () => clearInterval(particleInterval);
  }, []);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const animate = () => {
      ctx.clearRect(0, 0, canvas.width, canvas.height);

      particles.forEach(particle => {
        const gradient = ctx.createRadialGradient(
          particle.x, particle.y, 0,
          particle.x, particle.y, 5
        );

        if (particle.type === 'data') {
          gradient.addColorStop(0, 'rgba(78, 205, 196, 1)');
          gradient.addColorStop(1, 'rgba(78, 205, 196, 0)');
        } else if (particle.type === 'prediction') {
          gradient.addColorStop(0, 'rgba(69, 183, 209, 1)');
          gradient.addColorStop(1, 'rgba(69, 183, 209, 0)');
        } else {
          gradient.addColorStop(0, 'rgba(255, 234, 167, 1)');
          gradient.addColorStop(1, 'rgba(255, 234, 167, 0)');
        }

        ctx.fillStyle = gradient;
        ctx.beginPath();
        ctx.arc(particle.x, particle.y, 3, 0, Math.PI * 2);
        ctx.fill();
      });

      animationFrameRef.current = requestAnimationFrame(animate);
    };

    animate();

    return () => {
      if (animationFrameRef.current) {
        cancelAnimationFrame(animationFrameRef.current);
      }
    };
  }, [particles]);

  const getStatusIndicator = (levelId: number) => {
    const data = levelData[`level${levelId}`];
    if (!data) return 'inactive';
    if (data.status === 'active') return 'active';
    if (data.status === 'processing') return 'processing';
    return 'error';
  };

  return (
    <div className="architecture-flow">
      <canvas ref={canvasRef} className="flow-canvas" />

      <div className="flow-container">
        <div className="flow-header">
          <div className="data-source-node">
            <div className="node-icon">📊</div>
            <div className="node-label">Raw Data</div>
            <div className="node-sublabel">OHLCV + Features</div>
          </div>
        </div>

        <div className="levels-flow">
          {levels.map((level, index) => {
            const status = getStatusIndicator(level.id);
            const isHovered = hoveredLevel === level.id;
            const isSelected = selectedLevel === level.id;

            return (
              <React.Fragment key={level.id}>
                {index > 0 && (
                  <div className="flow-connector">
                    <div className={`connector-line ${status}`}>
                      <div className="connector-arrow"></div>
                    </div>
                  </div>
                )}

                <div
                  className={`flow-level ${status} ${isHovered ? 'hovered' : ''} ${isSelected ? 'selected' : ''}`}
                  onMouseEnter={() => setHoveredLevel(level.id)}
                  onMouseLeave={() => setHoveredLevel(null)}
                  onClick={() => onLevelClick(level.id)}
                >
                  <div className="level-badge-flow" style={{ backgroundColor: level.color }}>
                    <span className="badge-icon">{level.icon}</span>
                    <span className="badge-number">L{level.id}</span>
                  </div>

                  <div className="level-content-flow">
                    <div className="level-header-flow">
                      <h3 className="level-name-flow">{level.name}</h3>
                      <div className={`status-dot ${status}`}></div>
                    </div>
                    <p className="level-description-flow">{level.description}</p>

                    {levelData[`level${level.id}`] && (
                      <div className="level-metrics-preview">
                        {level.id === 0 && levelData.level0 && (
                          <>
                            <div className="metric-preview">
                              <span className="metric-preview-label">Score:</span>
                              <span className="metric-preview-value">
                                {(levelData.level0.tradeability_score * 100).toFixed(1)}%
                              </span>
                            </div>
                            <div className="metric-preview">
                              <span className="metric-preview-label">Status:</span>
                              <span className={`metric-preview-badge ${levelData.level0.is_tradeable ? 'success' : 'error'}`}>
                                {levelData.level0.is_tradeable ? 'Tradeable' : 'Non-tradeable'}
                              </span>
                            </div>
                          </>
                        )}

                        {level.id === 1 && levelData.level1 && (
                          <>
                            <div className="metric-preview">
                              <span className="metric-preview-label">Direction:</span>
                              <span className="metric-preview-value">
                                {levelData.level1.direction || 'N/A'}
                              </span>
                            </div>
                            <div className="metric-preview">
                              <span className="metric-preview-label">Patterns:</span>
                              <span className="metric-preview-value">
                                {levelData.level1.active_patterns?.length || 0}
                              </span>
                            </div>
                          </>
                        )}

                        {level.id === 2 && levelData.level2 && (
                          <>
                            <div className="metric-preview">
                              <span className="metric-preview-label">Active Expert:</span>
                              <span className="metric-preview-value">
                                {levelData.level2.active_expert || 'None'}
                              </span>
                            </div>
                            <div className="metric-preview">
                              <span className="metric-preview-label">Return:</span>
                              <span className={`metric-preview-value ${levelData.level2.predicted_return > 0 ? 'success' : 'error'}`}>
                                {(levelData.level2.predicted_return * 100).toFixed(2)}%
                              </span>
                            </div>
                          </>
                        )}

                        {level.id === 3 && levelData.level3 && (
                          <>
                            <div className="metric-preview">
                              <span className="metric-preview-label">Event:</span>
                              <span className="metric-preview-value">
                                {levelData.level3.event_type || 'NORMAL'}
                              </span>
                            </div>
                            <div className="metric-preview">
                              <span className="metric-preview-label">Decision:</span>
                              <span className="metric-preview-value">
                                {levelData.level3.decision || 'DELAY'}
                              </span>
                            </div>
                          </>
                        )}

                        {level.id === 4 && levelData.level4 && (
                          <>
                            <div className="metric-preview">
                              <span className="metric-preview-label">Action:</span>
                              <span className={`metric-preview-badge action-${levelData.level4.action?.toLowerCase()}`}>
                                {levelData.level4.action || 'WAIT'}
                              </span>
                            </div>
                            <div className="metric-preview">
                              <span className="metric-preview-label">Confidence:</span>
                              <span className="metric-preview-value">
                                {(levelData.level4.confidence * 100).toFixed(1)}%
                              </span>
                            </div>
                          </>
                        )}
                      </div>
                    )}
                  </div>

                  <div className="level-expand-hint">
                    Click for details
                  </div>
                </div>
              </React.Fragment>
            );
          })}
        </div>

        <div className="flow-footer">
          <div className="output-node">
            <div className="node-icon">🎯</div>
            <div className="node-label">Final Action</div>
            <div className="node-sublabel">
              {levelData.level4?.action || 'WAIT'}
              {levelData.level4?.confidence && (
                <span className="confidence-badge">
                  {(levelData.level4.confidence * 100).toFixed(0)}%
                </span>
              )}
            </div>
          </div>
        </div>
      </div>

      <div className="flow-legend">
        <div className="legend-item">
          <div className="legend-dot active"></div>
          <span>Active</span>
        </div>
        <div className="legend-item">
          <div className="legend-dot processing"></div>
          <span>Processing</span>
        </div>
        <div className="legend-item">
          <div className="legend-dot inactive"></div>
          <span>Inactive</span>
        </div>
        <div className="legend-item">
          <div className="legend-dot error"></div>
          <span>Error</span>
        </div>
      </div>
    </div>
  );
};

export default ArchitectureFlow;
