/**
 * Professional Design System
 * A refined, subtle design system that exudes excellence
 */

export const designSystem = {
  // Color Palette - Sophisticated and Minimal
  colors: {
    // Dark theme - professional and elegant
    background: {
      primary: '#0A0E1A',
      secondary: '#111827',
      tertiary: '#1A202E',
      elevated: '#1E293B',
      hover: '#2D3748',
    },

    // Accent colors - subtle and refined
    accent: {
      primary: '#3B82F6',      // Professional blue
      secondary: '#8B5CF6',    // Refined purple
      success: '#10B981',      // Clean green
      warning: '#F59E0B',      // Warm amber
      error: '#EF4444',        // Clear red
      info: '#06B6D4',         // Cyan
    },

    // Text hierarchy
    text: {
      primary: '#F9FAFB',
      secondary: '#D1D5DB',
      tertiary: '#9CA3AF',
      muted: '#6B7280',
      disabled: '#4B5563',
    },

    // Borders
    border: {
      light: 'rgba(255, 255, 255, 0.06)',
      medium: 'rgba(255, 255, 255, 0.1)',
      strong: 'rgba(255, 255, 255, 0.2)',
    },

    // Overlays
    overlay: {
      light: 'rgba(0, 0, 0, 0.2)',
      medium: 'rgba(0, 0, 0, 0.5)',
      strong: 'rgba(0, 0, 0, 0.8)',
    },
  },

  // Typography - Clean and Professional
  typography: {
    fontFamily: {
      primary: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif',
      mono: '"JetBrains Mono", "Fira Code", monospace',
    },

    fontSize: {
      xs: '0.75rem',      // 12px
      sm: '0.875rem',     // 14px
      base: '1rem',       // 16px
      lg: '1.125rem',     // 18px
      xl: '1.25rem',      // 20px
      '2xl': '1.5rem',    // 24px
      '3xl': '1.875rem',  // 30px
      '4xl': '2.25rem',   // 36px
      '5xl': '3rem',      // 48px
    },

    fontWeight: {
      light: 300,
      normal: 400,
      medium: 500,
      semibold: 600,
      bold: 700,
    },

    lineHeight: {
      tight: 1.2,
      normal: 1.5,
      relaxed: 1.75,
    },
  },

  // Spacing - Consistent rhythm
  spacing: {
    0: '0',
    1: '0.25rem',   // 4px
    2: '0.5rem',    // 8px
    3: '0.75rem',   // 12px
    4: '1rem',      // 16px
    5: '1.25rem',   // 20px
    6: '1.5rem',    // 24px
    8: '2rem',      // 32px
    10: '2.5rem',   // 40px
    12: '3rem',     // 48px
    16: '4rem',     // 64px
    20: '5rem',     // 80px
  },

  // Border Radius - Subtle curves
  borderRadius: {
    none: '0',
    sm: '0.25rem',    // 4px
    md: '0.5rem',     // 8px
    lg: '0.75rem',    // 12px
    xl: '1rem',       // 16px
    '2xl': '1.5rem',  // 24px
    full: '9999px',
  },

  // Shadows - Depth and elevation
  shadows: {
    sm: '0 1px 2px 0 rgba(0, 0, 0, 0.05)',
    md: '0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06)',
    lg: '0 10px 15px -3px rgba(0, 0, 0, 0.1), 0 4px 6px -2px rgba(0, 0, 0, 0.05)',
    xl: '0 20px 25px -5px rgba(0, 0, 0, 0.1), 0 10px 10px -5px rgba(0, 0, 0, 0.04)',
    '2xl': '0 25px 50px -12px rgba(0, 0, 0, 0.25)',
    glow: {
      blue: '0 0 20px rgba(59, 130, 246, 0.3)',
      purple: '0 0 20px rgba(139, 92, 246, 0.3)',
      green: '0 0 20px rgba(16, 185, 129, 0.3)',
    },
  },

  // Transitions - Smooth and subtle
  transitions: {
    fast: '150ms cubic-bezier(0.4, 0, 0.2, 1)',
    base: '200ms cubic-bezier(0.4, 0, 0.2, 1)',
    slow: '300ms cubic-bezier(0.4, 0, 0.2, 1)',
    slower: '500ms cubic-bezier(0.4, 0, 0.2, 1)',
  },

  // Z-index layers
  zIndex: {
    base: 0,
    dropdown: 1000,
    sticky: 1100,
    overlay: 1200,
    modal: 1300,
    popover: 1400,
    tooltip: 1500,
  },

  // Breakpoints for responsive design
  breakpoints: {
    sm: '640px',
    md: '768px',
    lg: '1024px',
    xl: '1280px',
    '2xl': '1536px',
  },
};

// Helper functions for gradient backgrounds
export const gradients = {
  primary: `linear-gradient(135deg, ${designSystem.colors.accent.primary}, ${designSystem.colors.accent.secondary})`,
  success: `linear-gradient(135deg, ${designSystem.colors.accent.success}, ${designSystem.colors.accent.info})`,
  warning: `linear-gradient(135deg, ${designSystem.colors.accent.warning}, ${designSystem.colors.accent.error})`,
  subtle: 'linear-gradient(135deg, rgba(59, 130, 246, 0.1), rgba(139, 92, 246, 0.1))',
};

// Glass morphism effect
export const glassMorphism = {
  background: 'rgba(255, 255, 255, 0.05)',
  backdropFilter: 'blur(10px)',
  border: `1px solid ${designSystem.colors.border.medium}`,
};
