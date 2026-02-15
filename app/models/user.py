"""
User Models
Pydantic schemas for user authentication and management
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr, Field


class UserBase(BaseModel):
    """Base user model with common fields."""
    email: str = Field(..., description="User email address")
    display_name: Optional[str] = Field(None, description="Display name")


class UserCreate(UserBase):
    """Model for creating a new user."""
    password: str = Field(..., min_length=8, description="User password")


class UserLogin(BaseModel):
    """Model for user login."""
    email: str = Field(..., description="User email address")
    password: str = Field(..., description="User password")


class UserInDB(UserBase):
    """Model representing user stored in database."""
    id: str
    is_admin: bool = False
    created_at: Optional[datetime] = None
    last_login: Optional[datetime] = None

    class Config:
        from_attributes = True


class User(UserBase):
    """Model for user in API responses (no sensitive data)."""
    id: str
    is_admin: bool = False
    created_at: Optional[datetime] = None
    last_login: Optional[datetime] = None

    class Config:
        from_attributes = True


class TokenData(BaseModel):
    """Data extracted from JWT token."""
    user_id: str
    email: str
    session_id: str
    is_admin: bool = False


class PasswordChange(BaseModel):
    """Model for password change request."""
    current_password: str = Field(..., description="Current password")
    new_password: str = Field(..., min_length=8, description="New password")


class UserInvite(BaseModel):
    """Model for inviting a new user."""
    email: str = Field(..., description="Email address to invite")
    is_admin: bool = Field(False, description="Whether to grant admin privileges")
