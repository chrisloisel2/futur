/**
 * Hook React pour consommer l'API crypto data
 */
import { useState, useEffect, useCallback } from 'react';
import { API_BASE_URL, WS_BASE_URL } from '../config/api';

export interface CryptoMetrics {
  current_price: number;
  price_change_24h: number;
  price_change_7d: number;
  price_change_1y: number;
  volume_24h: number;
  avg_volume: number;
  high_24h: number;
  low_24h: number;
  ath: number;
  atl: number;
  ath_change: number;
  atl_change: number;
}

export interface Crypto {
  symbol: string;
  name: string;
  current_price: number;
  price_change_24h: number;
  volume_24h: number;
}

export interface HistoricalDataPoint {
  timestamp: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  quote_volume: number;
  trades: number;
}

export interface MarketOverview {
  total_cryptos: number;
  top_gainers: Array<{
    symbol: string;
    change: number;
    price: number;
  }>;
  top_losers: Array<{
    symbol: string;
    change: number;
    price: number;
  }>;
  highest_volume: Array<{
    symbol: string;
    volume: number;
    price: number;
  }>;
}

/**
 * Hook pour charger toutes les cryptos disponibles
 */
export function useCryptos() {
  const [cryptos, setCryptos] = useState<Crypto[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchCryptos = useCallback(async () => {
    try {
      setLoading(true);
      const response = await fetch(`${API_BASE_URL}/api/cryptos`);
      const data = await response.json();

      if (data.success) {
        setCryptos(data.data);
        setError(null);
      } else {
        setError('Failed to fetch cryptos');
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchCryptos();
    // Rafraîchir toutes les 30 secondes
    const interval = setInterval(fetchCryptos, 30000);
    return () => clearInterval(interval);
  }, [fetchCryptos]);

  return { cryptos, loading, error, refetch: fetchCryptos };
}

/**
 * Hook pour charger les données historiques d'une crypto
 */
export function useHistoricalData(symbol: string, limit?: number) {
  const [data, setData] = useState<HistoricalDataPoint[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchHistoricalData = useCallback(async () => {
    if (!symbol) return;

    try {
      setLoading(true);
      const url = new URL(`${API_BASE_URL}/api/historical/${encodeURIComponent(symbol)}`);
      if (limit) url.searchParams.set('limit', limit.toString());

      const response = await fetch(url.toString());
      const result = await response.json();

      if (result.success) {
        setData(result.data);
        setError(null);
      } else {
        setError('Failed to fetch historical data');
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error');
    } finally {
      setLoading(false);
    }
  }, [symbol, limit]);

  useEffect(() => {
    fetchHistoricalData();
  }, [fetchHistoricalData]);

  return { data, loading, error, refetch: fetchHistoricalData };
}

/**
 * Hook pour charger les métriques d'une crypto
 */
export function useMetrics(symbol: string) {
  const [metrics, setMetrics] = useState<CryptoMetrics | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchMetrics = useCallback(async () => {
    if (!symbol) return;

    try {
      setLoading(true);
      const response = await fetch(`${API_BASE_URL}/api/metrics/${encodeURIComponent(symbol)}`);
      const result = await response.json();

      if (result.success) {
        setMetrics(result.metrics);
        setError(null);
      } else {
        setError('Failed to fetch metrics');
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error');
    } finally {
      setLoading(false);
    }
  }, [symbol]);

  useEffect(() => {
    fetchMetrics();
    // Rafraîchir toutes les 10 secondes
    const interval = setInterval(fetchMetrics, 10000);
    return () => clearInterval(interval);
  }, [fetchMetrics]);

  return { metrics, loading, error, refetch: fetchMetrics };
}

/**
 * Hook pour l'aperçu du marché
 */
export function useMarketOverview() {
  const [overview, setOverview] = useState<MarketOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchOverview = useCallback(async () => {
    try {
      setLoading(true);
      const response = await fetch(`${API_BASE_URL}/api/overview`);
      const result = await response.json();

      if (result.success) {
        setOverview(result.overview);
        setError(null);
      } else {
        setError('Failed to fetch overview');
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchOverview();
    // Rafraîchir toutes les 30 secondes
    const interval = setInterval(fetchOverview, 30000);
    return () => clearInterval(interval);
  }, [fetchOverview]);

  return { overview, loading, error, refetch: fetchOverview };
}

/**
 * Hook pour comparer plusieurs cryptos
 */
export function useCompare(symbols: string[], limit: number = 168) {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchComparison = useCallback(async () => {
    if (symbols.length === 0) return;

    try {
      setLoading(true);
      const symbolsParam = symbols.join(',');
      const response = await fetch(
        `${API_BASE_URL}/api/compare?symbols=${encodeURIComponent(symbolsParam)}&limit=${limit}`
      );
      const result = await response.json();

      if (result.success) {
        setData(result.data);
        setError(null);
      } else {
        setError('Failed to fetch comparison');
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error');
    } finally {
      setLoading(false);
    }
  }, [symbols, limit]);

  useEffect(() => {
    fetchComparison();
  }, [fetchComparison]);

  return { data, loading, error, refetch: fetchComparison };
}

/**
 * Hook pour WebSocket temps réel
 */
export function useRealtimeData() {
  const [connected, setConnected] = useState(false);
  const [messages, setMessages] = useState<any[]>([]);
  const [ws, setWs] = useState<WebSocket | null>(null);

  useEffect(() => {
    const websocket = new WebSocket(`${WS_BASE_URL}/ws`);

    websocket.onopen = () => {
      console.log('WebSocket connected');
      setConnected(true);
    };

    websocket.onmessage = (event) => {
      const message = JSON.parse(event.data);
      console.log('WebSocket message:', message);
      setMessages((prev) => [...prev, message]);
    };

    websocket.onerror = (error) => {
      console.error('WebSocket error:', error);
      setConnected(false);
    };

    websocket.onclose = () => {
      console.log('WebSocket disconnected');
      setConnected(false);
    };

    setWs(websocket);

    return () => {
      websocket.close();
    };
  }, []);

  return { connected, messages, ws };
}
