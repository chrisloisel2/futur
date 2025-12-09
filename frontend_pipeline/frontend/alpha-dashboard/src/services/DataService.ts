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
}
