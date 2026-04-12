"""
Item pipelines for processing scraped data
"""

from .validation import ValidationPipeline
from .deduplication import DeduplicationPipeline
from .metadata_extraction import MetadataExtractionPipeline
from .storage import StoragePipeline

__all__ = [
    'ValidationPipeline',
    'DeduplicationPipeline',
    'MetadataExtractionPipeline',
    'StoragePipeline'
]
