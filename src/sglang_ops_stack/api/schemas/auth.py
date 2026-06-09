from pydantic import BaseModel, ConfigDict, Field


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)

    username: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=1)


class UserCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    username: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=12)
    role: str = Field(pattern="^(admin|operator|viewer)$")
    is_active: bool = True


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    role: str
    is_active: bool


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserRead
