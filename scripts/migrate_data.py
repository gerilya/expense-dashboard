#!/usr/bin/env python3
"""
Migration script to load expense data from JSON file into Elasticsearch.

Usage:
    python migrate_data.py [path_to_json_file]

If no path is provided, defaults to ../expense_data.json
"""

import asyncio
import json
import sys
from pathlib import Path

from elasticsearch import AsyncElasticsearch

# Default configuration
ELASTICSEARCH_URL = "http://localhost:9200"
INDEX_NAME = "expenses"
DEFAULT_JSON_PATH = Path(__file__).parent.parent / "expense_data.json"

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


async def migrate_data(json_path: Path) -> None:
    """Load data from JSON file and index it in Elasticsearch."""
    
    # Read JSON file
    print(f"Reading data from: {json_path}")
    
    if not json_path.exists():
        print(f"Error: File not found: {json_path}")
        sys.exit(1)
    
    with open(json_path, "r", encoding="utf-8") as f:
        expenses = json.load(f)
    
    if not isinstance(expenses, list):
        print("Error: JSON file must contain an array of expenses")
        sys.exit(1)
    
    print(f"Found {len(expenses)} expenses to migrate")
    
    # Connect to Elasticsearch
    print(f"Connecting to Elasticsearch at: {ELASTICSEARCH_URL}")
    client = AsyncElasticsearch([ELASTICSEARCH_URL])
    
    try:
        # Verify connection
        info = await client.info()
        print(f"Connected to cluster: {info['cluster_name']}")
        
        # Check if index exists and offer to recreate
        index_exists = await client.indices.exists(index=INDEX_NAME)
        
        if index_exists:
            print(f"\nIndex '{INDEX_NAME}' already exists.")
            response = input("Delete and recreate? (y/N): ").strip().lower()
            
            if response == "y":
                await client.indices.delete(index=INDEX_NAME)
                print(f"Deleted index: {INDEX_NAME}")
            else:
                print("Appending to existing index...")
        
        # Create index if needed
        if not await client.indices.exists(index=INDEX_NAME):
            await client.indices.create(index=INDEX_NAME, body=INDEX_MAPPING)
            print(f"Created index: {INDEX_NAME}")
        
        # Index documents
        print("\nIndexing documents...")
        success_count = 0
        error_count = 0
        
        for i, expense in enumerate(expenses):
            try:
                await client.index(
                    index=INDEX_NAME,
                    document=expense
                )
                success_count += 1
                
                # Progress indicator
                if (i + 1) % 10 == 0 or i + 1 == len(expenses):
                    print(f"  Indexed {i + 1}/{len(expenses)} documents", end="\r")
                    
            except Exception as e:
                error_count += 1
                print(f"\nError indexing document {i}: {e}")
        
        # Refresh index to make documents searchable
        await client.indices.refresh(index=INDEX_NAME)
        
        print(f"\n\nMigration complete!")
        print(f"  Successfully indexed: {success_count}")
        print(f"  Errors: {error_count}")
        
        # Verify count
        count_response = await client.count(index=INDEX_NAME)
        print(f"  Total documents in index: {count_response['count']}")
        
    finally:
        await client.close()


def main():
    """Entry point for the migration script."""
    # Get JSON file path from command line or use default
    if len(sys.argv) > 1:
        json_path = Path(sys.argv[1])
    else:
        json_path = DEFAULT_JSON_PATH
    
    # Run migration
    asyncio.run(migrate_data(json_path))


if __name__ == "__main__":
    main()

