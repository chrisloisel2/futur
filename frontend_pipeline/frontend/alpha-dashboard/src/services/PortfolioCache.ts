/**
 * Portfolio Cache Service
 * Gère la persistance des données du portfolio dans localStorage
 * pour un chargement instantané au démarrage
 */

interface CachedPortfolioData {
  initialCapital: number;
  cash: number;
  positions: any[];
  tradeHistory: any[];
  portfolioHistory: Array<{ time: string; value: number }>;
  predictions: any[];
  config: any;
  timestamp: number;
}

const CACHE_KEY = 'portfolio_cache';
const CACHE_EXPIRY_MS = 24 * 60 * 60 * 1000; // 24 heures

export class PortfolioCache {
  /**
   * Sauvegarde toutes les données du portfolio dans le cache
   */
  static save(data: {
    initialCapital: number;
    cash: number;
    positions: any[];
    tradeHistory: any[];
    portfolioHistory: Array<{ time: Date; value: number }>;
    predictions: any[];
    config: any;
  }): void {
    try {
      const cacheData: CachedPortfolioData = {
        initialCapital: data.initialCapital,
        cash: data.cash,
        positions: data.positions,
        tradeHistory: data.tradeHistory.map(trade => ({
          ...trade,
          timestamp: trade.timestamp instanceof Date ? trade.timestamp.toISOString() : trade.timestamp
        })),
        portfolioHistory: data.portfolioHistory.map(point => ({
          time: point.time instanceof Date ? point.time.toISOString() : point.time,
          value: point.value
        })),
        predictions: data.predictions,
        config: data.config,
        timestamp: Date.now()
      };

      localStorage.setItem(CACHE_KEY, JSON.stringify(cacheData));
      console.log('💾 Portfolio data cached successfully');
    } catch (error) {
      console.warn('Failed to cache portfolio data:', error);
    }
  }

  /**
   * Charge les données du portfolio depuis le cache
   */
  static load(): CachedPortfolioData | null {
    try {
      const cached = localStorage.getItem(CACHE_KEY);
      if (!cached) {
        console.log('📭 No cached portfolio data found');
        return null;
      }

      const data: CachedPortfolioData = JSON.parse(cached);

      // Vérifier si le cache n'est pas expiré
      const age = Date.now() - data.timestamp;
      if (age > CACHE_EXPIRY_MS) {
        console.log('⏰ Cached portfolio data expired, clearing cache');
        this.clear();
        return null;
      }

      console.log('✅ Loaded cached portfolio data (age: ' + Math.round(age / 1000 / 60) + ' minutes)');
      return data;
    } catch (error) {
      console.warn('Failed to load cached portfolio data:', error);
      return null;
    }
  }

  /**
   * Convertit les données du cache en format utilisable par le composant
   */
  static hydrate(cached: CachedPortfolioData): {
    initialCapital: number;
    cash: number;
    positions: any[];
    tradeHistory: any[];
    portfolioHistory: Array<{ time: Date; value: number }>;
    predictions: any[];
    config: any;
  } {
    return {
      initialCapital: cached.initialCapital,
      cash: cached.cash,
      positions: cached.positions,
      tradeHistory: cached.tradeHistory.map(trade => ({
        ...trade,
        timestamp: new Date(trade.timestamp)
      })),
      portfolioHistory: cached.portfolioHistory.map(point => ({
        time: new Date(point.time),
        value: point.value
      })),
      predictions: cached.predictions,
      config: cached.config
    };
  }

  /**
   * Efface le cache du portfolio
   */
  static clear(): void {
    try {
      localStorage.removeItem(CACHE_KEY);
      console.log('🗑️ Portfolio cache cleared');
    } catch (error) {
      console.warn('Failed to clear portfolio cache:', error);
    }
  }

  /**
   * Vérifie si des données en cache existent
   */
  static hasCache(): boolean {
    return localStorage.getItem(CACHE_KEY) !== null;
  }

  /**
   * Récupère l'âge du cache en millisecondes
   */
  static getCacheAge(): number | null {
    try {
      const cached = localStorage.getItem(CACHE_KEY);
      if (!cached) return null;

      const data: CachedPortfolioData = JSON.parse(cached);
      return Date.now() - data.timestamp;
    } catch {
      return null;
    }
  }
}
