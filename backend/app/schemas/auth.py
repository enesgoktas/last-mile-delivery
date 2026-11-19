import uuid

from pydantic import BaseModel, EmailStr


class LoginRequest(BaseModel):
    email: EmailStr
    password: str
    tenant_slug: str | None = None


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class TokenPayload(BaseModel):
    sub: str  # user id
    role: str
    tenant_id: uuid.UUID
    exp: int


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: str | None = None


class SignupRequest(BaseModel):
    email: EmailStr
    password: str
    company_name: str
    full_name: str
