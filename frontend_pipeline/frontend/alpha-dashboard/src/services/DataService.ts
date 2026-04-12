const API_BASE_URL = 'http://localhost:8000';

export class DataService {
  static async getSummary() {
    const response = await fetch(`${API_BASE_URL}/dataset/summary`);
    if (!response.ok) throw new Error('Failed to fetch summary');
    return response.json();
  }

  static async getSignals() {
    const response = await fetch(`${API_BASE_URL}/dataset/signals`);
    if (!response.ok) throw new Error('Failed to fetch signals');
    return response.json();
  }

  static async getOHLCV(symbol: string, limit: number = 1000) {
    const response = await fetch(`${API_BASE_URL}/dataset/ohlcv/${symbol}?limit=${limit}`);
    if (!response.ok) throw new Error(`Failed to fetch OHLCV for ${symbol}`);
    return response.json();
  }

  static async getHistoricalData(symbol: string, limit: number = 500) {
    // Utilise la nouvelle route API qui supporte les slashes dans les symboles
    const response = await fetch(`${API_BASE_URL}/api/historical/${symbol}?limit=${limit}`);
    if (!response.ok) throw new Error(`Failed to fetch historical data for ${symbol}`);
    return response.json();
  }

  static async getFundingRates() {
    const response = await fetch(`${API_BASE_URL}/dataset/funding-rates`);
    if (!response.ok) throw new Error('Failed to fetch funding rates');
    return response.json();
  }

  static async getFearGreed() {
    const response = await fetch(`${API_BASE_URL}/dataset/fear-greed`);
    if (!response.ok) throw new Error('Failed to fetch fear & greed');
    return response.json();
  }

  static async getSentiment() {
    const response = await fetch(`${API_BASE_URL}/dataset/sentiment`);
    if (!response.ok) throw new Error('Failed to fetch sentiment');
    return response.json();
  }

  static async getMacro() {
    const response = await fetch(`${API_BASE_URL}/dataset/macro`);
    if (!response.ok) throw new Error('Failed to fetch macro data');
    return response.json();
  }

  static async getDerivatives() {
    const response = await fetch(`${API_BASE_URL}/dataset/derivatives`);
    if (!response.ok) throw new Error('Failed to fetch derivatives');
    return response.json();
  }

  static async getAllCryptos() {
    const response = await fetch(`${API_BASE_URL}/market/all-cryptos`);
    if (!response.ok) throw new Error('Failed to fetch all cryptos');
    return response.json();
  }

  static async getTicker(symbol: string = 'BTCUSDT') {
    const response = await fetch(`${API_BASE_URL}/market/ticker?symbol=${symbol}`);
    if (!response.ok) throw new Error(`Failed to fetch ticker for ${symbol}`);
    return response.json();
  }

  static async getKlines(symbol: string = 'BTCUSDT', interval: string = '1h', limit: number = 500) {
    const response = await fetch(`${API_BASE_URL}/market/klines?symbol=${symbol}&interval=${interval}&limit=${limit}`);
    if (!response.ok) throw new Error(`Failed to fetch klines for ${symbol}`);
    return response.json();
  }

  static async getOrderBook(symbol: string = 'BTCUSDT', depth: number = 20) {
    const response = await fetch(`${API_BASE_URL}/market/orderbook?symbol=${symbol}&depth=${depth}`);
    if (!response.ok) throw new Error(`Failed to fetch order book for ${symbol}`);
    return response.json();
  }

  static async getRecentTrades(symbol: string = 'BTCUSDT', limit: number = 50) {
    const response = await fetch(`${API_BASE_URL}/market/trades?symbol=${symbol}&limit=${limit}`);
    if (!response.ok) throw new Error(`Failed to fetch recent trades for ${symbol}`);
    return response.json();
  }

  static async getPipelineStatus() {
    const response = await fetch(`${API_BASE_URL}/pipeline/status`);
    if (!response.ok) throw new Error('Failed to fetch pipeline status');
    return response.json();
  }

  static async startPipeline(config?: any) {
    const response = await fetch(`${API_BASE_URL}/pipeline/start`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: config ? JSON.stringify(config) : undefined,
    });
    if (!response.ok) throw new Error('Failed to start pipeline');
    return response.json();
  }

  // ============================================================================
  // TRAINING MANAGEMENT METHODS
  // ============================================================================

  static async getTrainingConfigs() {
    const response = await fetch(`${API_BASE_URL}/training/configs`);
    if (!response.ok) throw new Error('Failed to fetch training configs');
    return response.json();
  }

  static async startTraining(
    config: string,
    device: string = 'auto',
    debugMode: boolean = false,
    trainingLocation: 'aws' | 'remote' | 'local' = 'aws',
    instanceType: string = 'g4dn.xlarge',
    awsRegion: string = 'eu-west-3',
    remoteHost: string = '100.118.183.51',
    remoteUser: string = 'qbee'
  ) {
    const response = await fetch(`${API_BASE_URL}/training/start`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        config,
        device,
        debug_mode: debugMode,
        training_location: trainingLocation,
        instance_type: instanceType,
        aws_region: awsRegion,
        remote_host: remoteHost,
        remote_user: remoteUser
      }),
    });
    if (!response.ok) throw new Error('Failed to start training');
    return response.json();
  }

  static async getAllTrainingJobs() {
    const response = await fetch(`${API_BASE_URL}/training/jobs`);
    if (!response.ok) throw new Error('Failed to get training jobs');
    return response.json();
  }

  static async getTrainingStatus(jobId: string) {
    const response = await fetch(`${API_BASE_URL}/training/status/${jobId}`);
    if (!response.ok) throw new Error('Failed to get training status');
    return response.json();
  }

  static async stopTraining(jobId: string) {
    const response = await fetch(`${API_BASE_URL}/training/stop/${jobId}`, {
      method: 'POST',
    });
    if (!response.ok) throw new Error('Failed to stop training');
    return response.json();
  }

  static async getTrainingLogs(jobId: string, lines: number = 100) {
    const response = await fetch(`${API_BASE_URL}/training/logs/${jobId}?lines=${lines}`);
    if (!response.ok) throw new Error('Failed to get logs');
    return response.json();
  }

  static async getModelVersions() {
    const response = await fetch(`${API_BASE_URL}/training/models`);
    if (!response.ok) throw new Error('Failed to get model versions');
    return response.json();
  }

  static async setProductionModel(filename: string) {
    const response = await fetch(`${API_BASE_URL}/training/models/${filename}/set-production`, {
      method: 'POST',
    });
    if (!response.ok) throw new Error('Failed to set production model');
    return response.json();
  }

  static async getModelMetadata(filename: string) {
    const response = await fetch(`${API_BASE_URL}/training/models/${filename}/metadata`);
    if (!response.ok) throw new Error('Failed to get model metadata');
    return response.json();
  }

  static async getTrainingCost(jobId: string) {
    const response = await fetch(`${API_BASE_URL}/training/aws-cost/${jobId}`);
    if (!response.ok) throw new Error('Failed to get training cost');
    return response.json();
  }

  // ============================================================================
  // ML ARCHITECTURE METHODS
  // ============================================================================

  static async getMLArchitectureData() {
    const response = await fetch(`${API_BASE_URL}/ml/architecture/status`);
    if (!response.ok) throw new Error('Failed to fetch ML architecture data');
    return response.json();
  }

  static async getLevel0Data() {
    const response = await fetch(`${API_BASE_URL}/ml/level0/gating`);
    if (!response.ok) throw new Error('Failed to fetch Level 0 data');
    return response.json();
  }

  static async getLevel1Data() {
    const response = await fetch(`${API_BASE_URL}/ml/level1/contexts`);
    if (!response.ok) throw new Error('Failed to fetch Level 1 data');
    return response.json();
  }

  static async getLevel2Data() {
    const response = await fetch(`${API_BASE_URL}/ml/level2/specialists`);
    if (!response.ok) throw new Error('Failed to fetch Level 2 data');
    return response.json();
  }

  static async getLevel3Data() {
    const response = await fetch(`${API_BASE_URL}/ml/level3/aggregators`);
    if (!response.ok) throw new Error('Failed to fetch Level 3 data');
    return response.json();
  }

  static async getLevel4Data() {
    const response = await fetch(`${API_BASE_URL}/ml/level4/policy`);
    if (!response.ok) throw new Error('Failed to fetch Level 4 data');
    return response.json();
  }

  static async getLatestPrediction() {
    const response = await fetch(`${API_BASE_URL}/ml/predictions/latest`);
    if (!response.ok) throw new Error('Failed to fetch latest prediction');
    return response.json();
  }

  static async getPipelineThroughput() {
    const response = await fetch(`${API_BASE_URL}/ml/flow/throughput`);
    if (!response.ok) throw new Error('Failed to fetch pipeline throughput');
    return response.json();
  }

  static async getLevelMetrics(levelId: number) {
    const response = await fetch(`${API_BASE_URL}/ml/level/${levelId}/metrics`);
    if (!response.ok) throw new Error(`Failed to fetch Level ${levelId} metrics`);
    return response.json();
  }
}
