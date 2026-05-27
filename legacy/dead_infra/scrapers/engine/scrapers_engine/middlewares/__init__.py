"""
Scrapy middlewares for advanced scraping capabilities
"""

from .user_agent import RotatingUserAgentMiddleware
from .proxy import ProxyMiddleware
from .enrichment import MetadataEnrichmentMiddleware
from .error_handling import ErrorHandlingMiddleware

__all__ = [
    'RotatingUserAgentMiddleware',
    'ProxyMiddleware',
    'MetadataEnrichmentMiddleware',
    'ErrorHandlingMiddleware'
]
