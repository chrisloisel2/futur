"""Configuration loader with validation."""
import os
from pathlib import Path
from typing import Any, Dict

import yaml
from dotenv import load_dotenv


class ConfigLoader:
    """Load and validate configuration from YAML and environment variables."""

    def __init__(self, config_path: str = "config.yaml", env_path: str = ".env") -> None:
        """
        Initialize config loader.

        Args:
            config_path: Path to YAML config file
            env_path: Path to .env file
        """
        self.config_path = Path(config_path)
        self.env_path = Path(env_path)
        self._config: Dict[str, Any] = {}

        self._load()

    def _load(self) -> None:
        """Load configuration from files."""
        # Load environment variables
        if self.env_path.exists():
            load_dotenv(self.env_path)

        # Load YAML config
        if self.config_path.exists():
            with open(self.config_path, "r") as f:
                self._config = yaml.safe_load(f) or {}
        else:
            raise FileNotFoundError(f"Config file not found: {self.config_path}")

    def get(self, key: str, default: Any = None) -> Any:
        """
        Get config value by dot-notation key.

        Args:
            key: Config key in dot notation (e.g., 'data_sources.ccxt.exchange')
            default: Default value if key not found
        """
        parts = key.split(".")
        value = self._config

        for part in parts:
            if isinstance(value, dict):
                value = value.get(part)
            else:
                return default

        return value if value is not None else default

    def get_env(self, key: str, default: str = "") -> str:
        """
        Get environment variable.

        Args:
            key: Environment variable name
            default: Default value if not found
        """
        return os.getenv(key, default)

    def validate(self) -> None:
        """Validate required configuration values."""
        required_keys = [
            "data_sources.ccxt.exchange",
            "symbols",
            "timeframes.primary",
        ]

        missing = []
        for key in required_keys:
            if self.get(key) is None:
                missing.append(key)

        if missing:
            raise ValueError(f"Missing required config keys: {missing}")

        # Validate API keys if using certain features
        if self.get("data_sources.glassnode") and not self.get_env("GLASSNODE_API_KEY"):
            raise ValueError("GLASSNODE_API_KEY not set in environment")

    @property
    def config(self) -> Dict[str, Any]:
        """Get full config dictionary."""
        return self._config


# Global config instance
_config_instance = None


def get_config(config_path: str = "config.yaml", env_path: str = ".env") -> ConfigLoader:
    """
    Get or create global config instance.

    Args:
        config_path: Path to YAML config file
        env_path: Path to .env file
    """
    global _config_instance

    if _config_instance is None:
        # Try to find config in pipeline directory
        pipeline_dir = Path(__file__).parent
        full_config_path = pipeline_dir / config_path
        full_env_path = pipeline_dir / env_path

        _config_instance = ConfigLoader(
            str(full_config_path) if full_config_path.exists() else config_path,
            str(full_env_path) if full_env_path.exists() else env_path,
        )
        _config_instance.validate()

    return _config_instance
