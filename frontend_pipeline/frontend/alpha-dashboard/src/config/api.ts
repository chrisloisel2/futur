const browserHost =
  typeof window !== 'undefined' && window.location.hostname
    ? window.location.hostname
    : 'localhost';

const apiProtocol =
  typeof window !== 'undefined' && window.location.protocol === 'https:'
    ? 'https'
    : 'http';

const wsProtocol = apiProtocol === 'https' ? 'wss' : 'ws';

export const API_BASE_URL =
  process.env.REACT_APP_API_BASE_URL || `${apiProtocol}://${browserHost}:8000`;

export const WS_BASE_URL =
  process.env.REACT_APP_WS_BASE_URL || `${wsProtocol}://${browserHost}:8000`;
