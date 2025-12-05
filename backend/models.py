"""Pydantic models for expense data validation."""

from typing import Optional
from pydantic import BaseModel, Field, field_validator


class ExpenseBase(BaseModel):
    """Base expense model with common fields."""
    
    date: str = Field(..., description="Date in DD/MM/YY format", examples=["01/01/24"])
    merchant: str = Field(..., min_length=1, max_length=200, description="Merchant name")
    category: str = Field(..., min_length=1, max_length=100, description="Expense category")
    card: str = Field(..., min_length=4, max_length=4, description="Last 4 digits of card")
    amount: float = Field(..., gt=0, description="Transaction amount")
    month: str = Field(..., description="Month in 'Mon YYYY' format", examples=["Jan 2024"])
    
    @field_validator("card")
    @classmethod
    def validate_card(cls, v: str) -> str:
        """Ensure card is exactly 4 digits."""
        if not v.isdigit() or len(v) != 4:
            raise ValueError("Card must be exactly 4 digits")
        return v
    
    @field_validator("amount")
    @classmethod
    def validate_amount(cls, v: float) -> float:
        """Round amount to 2 decimal places."""
        return round(v, 2)


class ExpenseCreate(ExpenseBase):
    """Model for creating a new expense."""
    pass


class ExpenseUpdate(BaseModel):
    """Model for updating an expense (all fields optional)."""
    
    date: Optional[str] = Field(None, description="Date in DD/MM/YY format")
    merchant: Optional[str] = Field(None, min_length=1, max_length=200)
    category: Optional[str] = Field(None, min_length=1, max_length=100)
    card: Optional[str] = Field(None, min_length=4, max_length=4)
    amount: Optional[float] = Field(None, gt=0)
    month: Optional[str] = Field(None)
    
    @field_validator("card")
    @classmethod
    def validate_card(cls, v: Optional[str]) -> Optional[str]:
        """Ensure card is exactly 4 digits if provided."""
        if v is not None and (not v.isdigit() or len(v) != 4):
            raise ValueError("Card must be exactly 4 digits")
        return v
    
    @field_validator("amount")
    @classmethod
    def validate_amount(cls, v: Optional[float]) -> Optional[float]:
        """Round amount to 2 decimal places if provided."""
        return round(v, 2) if v is not None else None


class ExpenseResponse(ExpenseBase):
    """Model for expense response with ID."""
    
    id: str = Field(..., description="Elasticsearch document ID")
    
    class Config:
        from_attributes = True


class ExpenseListResponse(BaseModel):
    """Response model for listing expenses."""
    
    total: int = Field(..., description="Total number of matching expenses")
    expenses: list[ExpenseResponse] = Field(default_factory=list)


class FilterParams(BaseModel):
    """Query parameters for filtering expenses."""
    
    month: Optional[str] = Field(None, description="Filter by month")
    category: Optional[str] = Field(None, description="Filter by category")
    card: Optional[str] = Field(None, description="Filter by card last 4 digits")
    merchant: Optional[str] = Field(None, description="Search by merchant name")
    min_amount: Optional[float] = Field(None, ge=0, description="Minimum amount")
    max_amount: Optional[float] = Field(None, ge=0, description="Maximum amount")

