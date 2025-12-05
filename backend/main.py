"""FastAPI application for expense management with Elasticsearch backend."""

import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from elasticsearch import NotFoundError

from models import (
    ExpenseCreate,
    ExpenseUpdate,
    ExpenseResponse,
    ExpenseListResponse,
)
from elasticsearch_client import es_client, settings

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler for startup/shutdown."""
    # Startup
    logger.info("Starting up application...")
    await es_client.connect()
    yield
    # Shutdown
    logger.info("Shutting down application...")
    await es_client.close()


app = FastAPI(
    title="Expense Dashboard API",
    description="REST API for managing personal expenses with Elasticsearch storage",
    version="1.0.0",
    lifespan=lifespan,
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify exact origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    try:
        await es_client.client.ping()
        return {"status": "healthy", "elasticsearch": "connected"}
    except Exception as e:
        return {"status": "unhealthy", "elasticsearch": str(e)}


@app.get("/api/expenses", response_model=ExpenseListResponse)
async def list_expenses(
    month: Optional[str] = Query(None, description="Filter by month"),
    category: Optional[str] = Query(None, description="Filter by category"),
    card: Optional[str] = Query(None, description="Filter by card last 4 digits"),
    merchant: Optional[str] = Query(None, description="Search by merchant name"),
    min_amount: Optional[float] = Query(None, ge=0, description="Minimum amount"),
    max_amount: Optional[float] = Query(None, ge=0, description="Maximum amount"),
    size: int = Query(1000, ge=1, le=10000, description="Number of results"),
):
    """
    List all expenses with optional filters.
    
    Supports filtering by month, category, card, merchant search,
    and amount range.
    """
    # Build query
    must_clauses = []
    
    if month:
        must_clauses.append({"term": {"month": month}})
    
    if category:
        must_clauses.append({"term": {"category": category}})
    
    if card:
        must_clauses.append({"term": {"card": card}})
    
    if merchant:
        must_clauses.append({
            "match": {
                "merchant": {
                    "query": merchant,
                    "fuzziness": "AUTO"
                }
            }
        })
    
    # Amount range filter
    if min_amount is not None or max_amount is not None:
        range_filter = {"range": {"amount": {}}}
        if min_amount is not None:
            range_filter["range"]["amount"]["gte"] = min_amount
        if max_amount is not None:
            range_filter["range"]["amount"]["lte"] = max_amount
        must_clauses.append(range_filter)
    
    # Build final query
    if must_clauses:
        query = {"bool": {"must": must_clauses}}
    else:
        query = {"match_all": {}}
    
    # Execute search
    response = await es_client.client.search(
        index=es_client.index_name,
        query=query,
        size=size,
        sort=[{"date": "desc"}]
    )
    
    # Parse results
    expenses = []
    for hit in response["hits"]["hits"]:
        expense = ExpenseResponse(
            id=hit["_id"],
            **hit["_source"]
        )
        expenses.append(expense)
    
    return ExpenseListResponse(
        total=response["hits"]["total"]["value"],
        expenses=expenses
    )


@app.get("/api/expenses/{expense_id}", response_model=ExpenseResponse)
async def get_expense(expense_id: str):
    """Get a single expense by ID."""
    try:
        response = await es_client.client.get(
            index=es_client.index_name,
            id=expense_id
        )
        return ExpenseResponse(
            id=response["_id"],
            **response["_source"]
        )
    except NotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Expense with ID '{expense_id}' not found"
        )


@app.post("/api/expenses", response_model=ExpenseResponse, status_code=status.HTTP_201_CREATED)
async def create_expense(expense: ExpenseCreate):
    """Create a new expense."""
    # Index the document
    response = await es_client.client.index(
        index=es_client.index_name,
        document=expense.model_dump(),
        refresh=True  # Make immediately searchable
    )
    
    return ExpenseResponse(
        id=response["_id"],
        **expense.model_dump()
    )


@app.put("/api/expenses/{expense_id}", response_model=ExpenseResponse)
async def update_expense(expense_id: str, expense_update: ExpenseUpdate):
    """Update an existing expense."""
    # Check if expense exists
    try:
        existing = await es_client.client.get(
            index=es_client.index_name,
            id=expense_id
        )
    except NotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Expense with ID '{expense_id}' not found"
        )
    
    # Merge updates with existing data
    updated_data = existing["_source"].copy()
    update_dict = expense_update.model_dump(exclude_unset=True)
    updated_data.update(update_dict)
    
    # Update the document
    await es_client.client.index(
        index=es_client.index_name,
        id=expense_id,
        document=updated_data,
        refresh=True
    )
    
    return ExpenseResponse(
        id=expense_id,
        **updated_data
    )


@app.delete("/api/expenses/{expense_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_expense(expense_id: str):
    """Delete an expense."""
    try:
        await es_client.client.delete(
            index=es_client.index_name,
            id=expense_id,
            refresh=True
        )
    except NotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Expense with ID '{expense_id}' not found"
        )


@app.get("/api/categories", response_model=list[str])
async def list_categories():
    """Get all unique expense categories."""
    response = await es_client.client.search(
        index=es_client.index_name,
        size=0,
        aggs={
            "categories": {
                "terms": {
                    "field": "category",
                    "size": 100
                }
            }
        }
    )
    
    return [bucket["key"] for bucket in response["aggregations"]["categories"]["buckets"]]


@app.get("/api/months", response_model=list[str])
async def list_months():
    """Get all unique months."""
    response = await es_client.client.search(
        index=es_client.index_name,
        size=0,
        aggs={
            "months": {
                "terms": {
                    "field": "month",
                    "size": 100
                }
            }
        }
    )
    
    return [bucket["key"] for bucket in response["aggregations"]["months"]["buckets"]]


@app.get("/api/cards", response_model=list[str])
async def list_cards():
    """Get all unique card numbers."""
    response = await es_client.client.search(
        index=es_client.index_name,
        size=0,
        aggs={
            "cards": {
                "terms": {
                    "field": "card",
                    "size": 100
                }
            }
        }
    )
    
    return [bucket["key"] for bucket in response["aggregations"]["cards"]["buckets"]]


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

