"""
Database Schemas for Screen Demon

Each Pydantic model maps to a MongoDB collection (lowercased class name).
- User -> user
- Challenge -> challenge
- ScreenTimeLog -> screentimelog
"""

from pydantic import BaseModel, Field, EmailStr
from typing import List, Optional
from datetime import date as DateType

class User(BaseModel):
    handle: str = Field(..., description="Unique display handle, e.g., @alex")
    name: str = Field(..., description="Full name")
    email: Optional[EmailStr] = Field(None, description="Email address")

class Challenge(BaseModel):
    title: str = Field(..., description="Challenge title")
    creator_id: str = Field(..., description="User id of challenge creator")
    start_date: DateType = Field(..., description="Start date (YYYY-MM-DD)")
    end_date: DateType = Field(..., description="End date (YYYY-MM-DD)")
    bet_type: str = Field(..., description="What you’re betting on: food, activity, etc.")
    bet_details: Optional[str] = Field(None, description="Details of the bet (e.g., loser buys pizza)")
    participants: List[str] = Field(default_factory=list, description="User ids of participants")
    status: str = Field("active", description="active | completed | upcoming")

class ScreenTimeLog(BaseModel):
    user_id: str = Field(..., description="User id")
    challenge_id: str = Field(..., description="Challenge id")
    date: DateType = Field(..., description="Date of the log (YYYY-MM-DD)")
    minutes: int = Field(..., ge=0, le=24*60, description="Screen time in minutes for the date")
