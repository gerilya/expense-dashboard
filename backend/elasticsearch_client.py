"""Elasticsearch client configuration and index management."""

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from elasticsearch import AsyncElasticsearch
from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""
    
    elasticsearch_url: str = "http://localhost:9200"
    elasticsearch_index: str = "expenses"
    elasticsearch_username: str | None = None
    elasticsearch_password: str | None = None
    
    class Config:
        env_prefix = ""
        case_sensitive = False


settings = Settings()

# Elasticsearch index mapping
INDEX_MAPPING = {
    "mappings": {
        "properties": {
            "date": {"type": "keyword"},
            "merchant": {
                "type": "text",
                "fields": {
                    "keyword": {"type": "keyword"}
                }
            },
            "category": {"type": "keyword"},
            "card": {"type": "keyword"},
            "amount": {"type": "float"},
            "month": {"type": "keyword"}
        }
    },
    "settings": {
        "number_of_shards": 1,
        "number_of_replicas": 0
    }
}


class ElasticsearchClient:
    """Async Elasticsearch client wrapper."""
    
    def __init__(self):
        self._client: AsyncElasticsearch | None = None
    
    async def connect(self) -> None:
        """Initialize the Elasticsearch connection."""
        connection_params = {
            "hosts": [settings.elasticsearch_url],
        }
        
        if settings.elasticsearch_username and settings.elasticsearch_password:
            connection_params["basic_auth"] = (
                settings.elasticsearch_username,
                settings.elasticsearch_password
            )
        
        self._client = AsyncElasticsearch(**connection_params)
        
        # Verify connection
        info = await self._client.info()
        logger.info(f"Connected to Elasticsearch cluster: {info['cluster_name']}")
        
        # Create index if it doesn't exist
        await self._ensure_index()
    
    async def _ensure_index(self) -> None:
        """Create the expenses index if it doesn't exist."""
        index_name = settings.elasticsearch_index
        
        if not await self._client.indices.exists(index=index_name):
            await self._client.indices.create(
                index=index_name,
                body=INDEX_MAPPING
            )
            logger.info(f"Created index: {index_name}")
        else:
            logger.info(f"Index already exists: {index_name}")
    
    async def close(self) -> None:
        """Close the Elasticsearch connection."""
        if self._client:
            await self._client.close()
            self._client = None
            logger.info("Elasticsearch connection closed")
    
    @property
    def client(self) -> AsyncElasticsearch:
        """Get the Elasticsearch client instance."""
        if self._client is None:
            raise RuntimeError("Elasticsearch client not initialized")
        return self._client
    
    @property
    def index_name(self) -> str:
        """Get the index name."""
        return settings.elasticsearch_index


# Global client instance
es_client = ElasticsearchClient()


@asynccontextmanager
async def get_es_client() -> AsyncGenerator[ElasticsearchClient, None]:
    """Dependency for getting the Elasticsearch client."""
    yield es_client

