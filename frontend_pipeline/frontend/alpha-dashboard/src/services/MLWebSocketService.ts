const WS_BASE_URL = 'ws://localhost:8000';

export interface MLArchitectureUpdate {
  level0?: any;
  level1?: any;
  level2?: any;
  level3?: any;
  level4?: any;
  timestamp: string;
}

export class MLWebSocketService {
  private ws: WebSocket | null = null;
  private reconnectAttempts = 0;
  private maxReconnectAttempts = 5;
  private reconnectDelay = 3000;
  private listeners: Map<string, Set<(data: any) => void>> = new Map();

  connect() {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      console.log('WebSocket already connected');
      return;
    }

    try {
      this.ws = new WebSocket(`${WS_BASE_URL}/ws/ml-architecture`);

      this.ws.onopen = () => {
        console.log('ML Architecture WebSocket connected');
        this.reconnectAttempts = 0;
        this.emit('connection', { status: 'connected' });
      };

      this.ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          this.handleMessage(data);
        } catch (error) {
          console.error('Error parsing WebSocket message:', error);
        }
      };

      this.ws.onerror = (error) => {
        console.error('WebSocket error:', error);
        this.emit('error', { error });
      };

      this.ws.onclose = () => {
        console.log('WebSocket closed');
        this.emit('connection', { status: 'disconnected' });
        this.attemptReconnect();
      };
    } catch (error) {
      console.error('Error creating WebSocket:', error);
      this.attemptReconnect();
    }
  }

  disconnect() {
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
    this.reconnectAttempts = this.maxReconnectAttempts;
  }

  private attemptReconnect() {
    if (this.reconnectAttempts < this.maxReconnectAttempts) {
      this.reconnectAttempts++;
      console.log(`Attempting to reconnect (${this.reconnectAttempts}/${this.maxReconnectAttempts})...`);
      setTimeout(() => this.connect(), this.reconnectDelay);
    } else {
      console.error('Max reconnection attempts reached');
      this.emit('error', { error: 'Max reconnection attempts reached' });
    }
  }

  private handleMessage(data: any) {
    const { type, payload } = data;

    switch (type) {
      case 'architecture_update':
        this.emit('architecture', payload);
        break;

      case 'level0_update':
        this.emit('level0', payload);
        break;

      case 'level1_update':
        this.emit('level1', payload);
        break;

      case 'level2_update':
        this.emit('level2', payload);
        break;

      case 'level3_update':
        this.emit('level3', payload);
        break;

      case 'level4_update':
        this.emit('level4', payload);
        break;

      case 'prediction_update':
        this.emit('prediction', payload);
        break;

      case 'throughput_update':
        this.emit('throughput', payload);
        break;

      default:
        console.warn('Unknown message type:', type);
    }
  }

  on(event: string, callback: (data: any) => void) {
    if (!this.listeners.has(event)) {
      this.listeners.set(event, new Set());
    }
    this.listeners.get(event)!.add(callback);
  }

  off(event: string, callback: (data: any) => void) {
    const eventListeners = this.listeners.get(event);
    if (eventListeners) {
      eventListeners.delete(callback);
    }
  }

  private emit(event: string, data: any) {
    const eventListeners = this.listeners.get(event);
    if (eventListeners) {
      eventListeners.forEach(callback => callback(data));
    }
  }

  send(message: any) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(message));
    } else {
      console.warn('WebSocket is not connected');
    }
  }

  subscribeToLevel(levelId: number) {
    this.send({
      type: 'subscribe',
      level: levelId
    });
  }

  unsubscribeFromLevel(levelId: number) {
    this.send({
      type: 'unsubscribe',
      level: levelId
    });
  }

  isConnected(): boolean {
    return this.ws !== null && this.ws.readyState === WebSocket.OPEN;
  }
}

export const mlWebSocketService = new MLWebSocketService();
