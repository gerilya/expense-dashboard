#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Import script for financial transaction data from various sources.

Supports:
- Multiple file formats: CSV, Excel (.xlsx, .xls)
- Multiple financial institutions: Max, Cal (extensible)
- Batch import from directory
- Metadata tracking (source file, import timestamp)

Usage:
    python import_transactions.py <directory_or_file> [--dry-run] [--clear-index]

Examples:
    python import_transactions.py ../import/
    python import_transactions.py ../import/transactions.xlsx --dry-run
    python import_transactions.py ../import/ --clear-index
"""

import asyncio
import argparse
import hashlib
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd
from elasticsearch import AsyncElasticsearch

# Configuration
ELASTICSEARCH_URL = os.environ.get("ELASTICSEARCH_URL", "http://localhost:9200")
INDEX_NAME = os.environ.get("ELASTICSEARCH_INDEX", "expenses")

# Index mapping (same as main app)
INDEX_MAPPING = {
    "mappings": {
        "properties": {
            "date": {"type": "keyword"},
            "merchant": {
                "type": "text",
                "fields": {"keyword": {"type": "keyword"}}
            },
            "category": {"type": "keyword"},
            "card": {"type": "keyword"},
            "amount": {"type": "float"},
            "month": {"type": "keyword"},
            "tags": {"type": "keyword"},
            "source_file": {"type": "keyword"},
            "source_institution": {"type": "keyword"},
            "import_timestamp": {"type": "date"},
            "original_currency": {"type": "keyword"},
            "transaction_type": {"type": "keyword"},
            "notes": {"type": "text"},
            "document_hash": {"type": "keyword"}
        }
    },
    "settings": {
        "number_of_shards": 1,
        "number_of_replicas": 0
    }
}


class BaseParser:
    """Base class for financial institution parsers."""
    
    institution_name: str = "unknown"
    
    @classmethod
    def can_parse(cls, df: pd.DataFrame, filename: str) -> bool:
        raise NotImplementedError
    
    @classmethod
    def parse(cls, df: pd.DataFrame, filename: str) -> list[dict]:
        raise NotImplementedError
    
    @staticmethod
    def format_date(date_val) -> str:
        """Convert various date formats to DD/MM/YY."""
        if pd.isna(date_val):
            return ""
        
        if isinstance(date_val, datetime):
            return date_val.strftime("%d/%m/%y")
        
        if isinstance(date_val, str):
            for fmt in ["%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d", "%d-%m-%y", "%d/%m/%y"]:
                try:
                    dt = datetime.strptime(date_val, fmt)
                    return dt.strftime("%d/%m/%y")
                except ValueError:
                    continue
        
        return str(date_val)
    
    @staticmethod
    def get_month(date_str: str) -> str:
        """Extract month from date string (DD/MM/YY) -> 'Mon YYYY'."""
        if not date_str:
            return ""
        
        try:
            parts = date_str.split("/")
            if len(parts) == 3:
                day, month, year = parts
                year_full = f"20{year}" if len(year) == 2 else year
                month_names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
                return f"{month_names[int(month) - 1]} {year_full}"
        except (ValueError, IndexError):
            pass
        
        return ""
    
    @staticmethod
    def clean_amount(amount_val) -> float:
        """Clean and convert amount to float."""
        if pd.isna(amount_val):
            return 0.0
        
        if isinstance(amount_val, (int, float)):
            return float(amount_val)
        
        cleaned = re.sub(r'[^\d.,\-]', '', str(amount_val))
        cleaned = cleaned.replace(',', '')
        
        try:
            return float(cleaned)
        except ValueError:
            return 0.0


class MaxParser(BaseParser):
    """Parser for Max credit card exports."""
    
    institution_name = "Max"
    
    # Hebrew column identifiers
    COL_DATE = "date"  # Contains Hebrew chars in actual file
    COL_MERCHANT = "merchant"
    COL_CATEGORY = "category"
    COL_CARD = "card"
    COL_AMOUNT = "amount"
    
    @classmethod
    def can_parse(cls, df: pd.DataFrame, filename: str) -> bool:
        """Detect Max files by filename pattern or header content."""
        if "transaction-details" in filename.lower():
            return True
        
        # Check for Max-specific pattern in content
        for i in range(min(5, len(df))):
            row_str = " ".join(str(x) for x in df.iloc[i].values if pd.notna(x))
            # Look for characteristic Max headers
            if "BIT" in row_str or "transaction" in filename.lower():
                return True
        
        return False
    
    @classmethod
    def find_header_row(cls, df: pd.DataFrame) -> int:
        """Find the row containing column headers."""
        for i in range(min(10, len(df))):
            row = df.iloc[i]
            # Check if row has multiple non-null string values (header row)
            non_null = [str(x) for x in row.values if pd.notna(x)]
            if len(non_null) >= 5:
                # Check if it looks like a header (not a data row)
                first_val = str(row.iloc[0]) if pd.notna(row.iloc[0]) else ""
                # Headers don't start with dates
                if not re.match(r'\d{1,2}[-/]\d{1,2}[-/]\d{2,4}', first_val):
                    return i
        return 3
    
    @classmethod
    def parse(cls, df: pd.DataFrame, filename: str) -> list[dict]:
        """Parse Max format transactions."""
        transactions = []
        
        header_row = cls.find_header_row(df)
        headers = [str(h).strip() if pd.notna(h) else f"col_{i}" 
                   for i, h in enumerate(df.iloc[header_row].values)]
        data_start = header_row + 1
        
        # Map column indices
        date_idx = 0
        merchant_idx = 1
        category_idx = 2
        card_idx = 3
        amount_idx = 5  # Amount column is typically 6th
        
        for idx in range(data_start, len(df)):
            row = df.iloc[idx]
            
            # Skip empty rows
            if pd.isna(row.iloc[0]):
                continue
            
            date_str = cls.format_date(row.iloc[date_idx])
            if not date_str:
                continue
            
            amount = cls.clean_amount(row.iloc[amount_idx] if len(row) > amount_idx else 0)
            if amount <= 0:
                continue
            
            # Extract card (column 4)
            card = "0000"
            if len(row) > card_idx and pd.notna(row.iloc[card_idx]):
                card_val = row.iloc[card_idx]
                card = str(int(card_val) if isinstance(card_val, float) else card_val).zfill(4)[-4:]
            
            merchant = str(row.iloc[merchant_idx]).strip() if len(row) > merchant_idx and pd.notna(row.iloc[merchant_idx]) else ""
            category = str(row.iloc[category_idx]).strip() if len(row) > category_idx and pd.notna(row.iloc[category_idx]) else "Other"
            
            # Get tags if available (column 11)
            tags = []
            if len(row) > 11 and pd.notna(row.iloc[11]):
                tags_raw = str(row.iloc[11])
                tags = [t.strip().lower() for t in tags_raw.split(",") if t.strip()]
            
            # Get notes if available (column 10)
            notes = ""
            if len(row) > 10 and pd.notna(row.iloc[10]):
                notes = str(row.iloc[10]).strip()
            
            transaction = {
                "date": date_str,
                "merchant": merchant,
                "category": category,
                "card": card,
                "amount": round(amount, 2),
                "month": cls.get_month(date_str),
                "tags": tags,
                "original_currency": "ILS",
                "transaction_type": str(row.iloc[4]).strip() if len(row) > 4 and pd.notna(row.iloc[4]) else "",
                "notes": notes,
                "source_institution": cls.institution_name,
            }
            
            transactions.append(transaction)
        
        return transactions


class CalParser(BaseParser):
    """Parser for Cal (Visa Cal) credit card exports."""
    
    institution_name = "Cal"
    
    @classmethod
    def can_parse(cls, df: pd.DataFrame, filename: str) -> bool:
        """Detect Cal files by filename pattern or header content."""
        # Check filename - Cal files typically have Hebrew names with visa/card info
        # or contain specific patterns
        filename_check = filename.lower()
        
        # Check for patterns that indicate Cal files
        if "1472" in filename or "visa" in filename_check:
            return True
        
        # Check first row for Cal-specific content
        if len(df) > 0:
            first_row_str = " ".join(str(x) for x in df.iloc[0].values if pd.notna(x))
            # Cal files have account info in first row
            if "520-" in first_row_str or "1472" in first_row_str:
                return True
        
        return False
    
    @classmethod
    def extract_card_number(cls, df: pd.DataFrame, filename: str) -> str:
        """Extract card last 4 digits from file header or filename."""
        match = re.search(r'(\d{4})', filename)
        if match:
            return match.group(1)
        
        if len(df) > 0:
            first_row = " ".join(str(x) for x in df.iloc[0].values if pd.notna(x))
            match = re.search(r'(\d{4})', first_row)
            if match:
                return match.group(1)
        
        return "0000"
    
    @classmethod
    def find_header_row(cls, df: pd.DataFrame) -> int:
        """Find the row containing column headers."""
        for i in range(min(10, len(df))):
            row = df.iloc[i]
            row_str = " ".join(str(x) for x in row.values if pd.notna(x))
            # Cal header row has multiple columns with text
            non_null = [x for x in row.values if pd.notna(x)]
            if len(non_null) >= 5:
                # Check if first value is not a date (header row)
                first_val = row.iloc[0]
                if not isinstance(first_val, datetime) and pd.notna(first_val):
                    first_str = str(first_val)
                    if not re.match(r'\d{1,2}[-/]\d{1,2}[-/]\d{2,4}', first_str):
                        return i
        return 4
    
    @classmethod
    def parse(cls, df: pd.DataFrame, filename: str) -> list[dict]:
        """Parse Cal format transactions."""
        transactions = []
        
        card = cls.extract_card_number(df, filename)
        header_row = cls.find_header_row(df)
        data_start = header_row + 1
        
        # Cal format: Date, Merchant, Amount, Charge Amount, Type, Category, Notes
        # Indices: 0, 1, 2, 3, 4, 5, 6
        
        for idx in range(data_start, len(df)):
            row = df.iloc[idx]
            
            # Skip empty rows
            if pd.isna(row.iloc[0]):
                continue
            
            date_str = cls.format_date(row.iloc[0])
            if not date_str:
                continue
            
            # Amount is in column 3 (charge amount) or column 2 (transaction amount)
            amount = 0.0
            if len(row) > 3 and pd.notna(row.iloc[3]):
                amount = cls.clean_amount(row.iloc[3])
            if amount <= 0 and len(row) > 2 and pd.notna(row.iloc[2]):
                amount = cls.clean_amount(row.iloc[2])
            
            if amount <= 0:
                continue
            
            merchant = str(row.iloc[1]).strip() if len(row) > 1 and pd.notna(row.iloc[1]) else ""
            
            # Category in column 5
            category = "Other"
            if len(row) > 5 and pd.notna(row.iloc[5]):
                category = str(row.iloc[5]).strip()
            
            # Transaction type in column 4
            tx_type = ""
            if len(row) > 4 and pd.notna(row.iloc[4]):
                tx_type = str(row.iloc[4]).strip()
            
            # Notes in column 6
            notes = ""
            if len(row) > 6 and pd.notna(row.iloc[6]):
                notes = str(row.iloc[6]).strip()
            
            transaction = {
                "date": date_str,
                "merchant": merchant,
                "category": category,
                "card": card,
                "amount": round(amount, 2),
                "month": cls.get_month(date_str),
                "tags": [],
                "original_currency": "ILS",
                "transaction_type": tx_type,
                "notes": notes,
                "source_institution": cls.institution_name,
            }
            
            transactions.append(transaction)
        
        return transactions


# Registry of available parsers
PARSERS = [MaxParser, CalParser]


def detect_parser(df: pd.DataFrame, filename: str) -> Optional[type]:
    """Detect the appropriate parser for a file."""
    for parser_class in PARSERS:
        if parser_class.can_parse(df, filename):
            return parser_class
    return None


def read_file(filepath: Path) -> Optional[pd.DataFrame]:
    """Read a file into a DataFrame."""
    suffix = filepath.suffix.lower()
    
    try:
        if suffix == '.csv':
            for encoding in ['utf-8', 'windows-1255', 'iso-8859-8', 'cp1252']:
                try:
                    return pd.read_csv(filepath, encoding=encoding, header=None)
                except UnicodeDecodeError:
                    continue
            return pd.read_csv(filepath, encoding='utf-8', errors='ignore', header=None)
        
        elif suffix in ['.xlsx', '.xls']:
            engine = 'openpyxl' if suffix == '.xlsx' else 'xlrd'
            return pd.read_excel(filepath, engine=engine, header=None)
        
        else:
            print(f"  Unsupported file format: {suffix}")
            return None
            
    except Exception as e:
        print(f"  Error reading file: {e}")
        return None


def generate_document_hash(transaction: dict) -> str:
    """Generate a unique hash for deduplication."""
    key_string = f"{transaction['date']}|{transaction['merchant']}|{transaction['amount']}|{transaction['card']}"
    return hashlib.md5(key_string.encode()).hexdigest()


def process_file(filepath: Path) -> list[dict]:
    """Process a single file and return transactions."""
    print(f"\nProcessing: {filepath.name}")
    
    df = read_file(filepath)
    if df is None:
        return []
    
    parser_class = detect_parser(df, filepath.name)
    if parser_class is None:
        print(f"  No parser found for this file format")
        return []
    
    print(f"  Detected format: {parser_class.institution_name}")
    
    transactions = parser_class.parse(df, filepath.name)
    
    import_timestamp = datetime.utcnow().isoformat()
    for tx in transactions:
        tx["source_file"] = filepath.name
        tx["import_timestamp"] = import_timestamp
        tx["document_hash"] = generate_document_hash(tx)
    
    print(f"  Parsed {len(transactions)} transactions")
    return transactions


async def import_to_elasticsearch(
    transactions: list[dict],
    dry_run: bool = False,
    clear_index: bool = False
) -> tuple[int, int]:
    """Import transactions to Elasticsearch."""
    
    if dry_run:
        print(f"\n[DRY RUN] Would import {len(transactions)} transactions")
        for tx in transactions[:5]:
            print(f"  - {tx['date']} | {tx['merchant'][:30]:<30} | {tx['amount']:>8.2f} | {tx['category']}")
        if len(transactions) > 5:
            print(f"  ... and {len(transactions) - 5} more")
        return len(transactions), 0
    
    client = AsyncElasticsearch([ELASTICSEARCH_URL])
    
    try:
        info = await client.info()
        print(f"\nConnected to Elasticsearch: {info['cluster_name']}")
        
        index_exists = await client.indices.exists(index=INDEX_NAME)
        
        if clear_index and index_exists:
            await client.indices.delete(index=INDEX_NAME)
            print(f"Deleted existing index: {INDEX_NAME}")
            index_exists = False
        
        if not index_exists:
            await client.indices.create(index=INDEX_NAME, body=INDEX_MAPPING)
            print(f"Created index: {INDEX_NAME}")
        
        success_count = 0
        skip_count = 0
        
        for i, tx in enumerate(transactions):
            existing = await client.search(
                index=INDEX_NAME,
                query={"term": {"document_hash": tx["document_hash"]}},
                size=1
            )
            
            if existing["hits"]["total"]["value"] > 0:
                skip_count += 1
                continue
            
            await client.index(index=INDEX_NAME, document=tx)
            success_count += 1
            
            if (i + 1) % 50 == 0:
                print(f"  Imported {i + 1}/{len(transactions)}...")
        
        await client.indices.refresh(index=INDEX_NAME)
        
        return success_count, skip_count
        
    finally:
        await client.close()


def main():
    parser = argparse.ArgumentParser(
        description="Import financial transactions from CSV/Excel files to Elasticsearch"
    )
    parser.add_argument("path", type=str, help="Path to file or directory")
    parser.add_argument("--dry-run", action="store_true", help="Parse but don't import")
    parser.add_argument("--clear-index", action="store_true", help="Clear existing index")
    
    args = parser.parse_args()
    path = Path(args.path)
    
    if not path.exists():
        print(f"Error: Path does not exist: {path}")
        sys.exit(1)
    
    if path.is_file():
        files = [path]
    else:
        files = sorted([
            f for f in path.iterdir()
            if f.is_file() and f.suffix.lower() in ['.csv', '.xlsx', '.xls']
        ])
    
    if not files:
        print("No CSV or Excel files found")
        sys.exit(1)
    
    print(f"Found {len(files)} file(s) to process")
    
    all_transactions = []
    for filepath in files:
        transactions = process_file(filepath)
        all_transactions.extend(transactions)
    
    print(f"\nTotal transactions parsed: {len(all_transactions)}")
    
    if not all_transactions:
        print("No transactions to import")
        sys.exit(0)
    
    success, skipped = asyncio.run(
        import_to_elasticsearch(
            all_transactions,
            dry_run=args.dry_run,
            clear_index=args.clear_index
        )
    )
    
    print(f"\nImport complete!")
    print(f"  Imported: {success}")
    print(f"  Skipped (duplicates): {skipped}")


if __name__ == "__main__":
    main()
