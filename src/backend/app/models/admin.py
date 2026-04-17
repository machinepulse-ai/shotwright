"""Pydantic schemas for admin operations."""

from pydantic import BaseModel, Field


class AdminLogin(BaseModel):
    password: str = Field(min_length=1)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class GithubTokenUpdate(BaseModel):
    github_token: str = Field(min_length=1)


class AdminSettings(BaseModel):
    github_token_set: bool = False
