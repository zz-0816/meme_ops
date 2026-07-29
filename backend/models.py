"""
Pydantic 数据模型
"""

from pydantic import BaseModel, Field
from typing import Optional


# ============ 认证 ============

class NonceResponse(BaseModel):
    nonce: str
    message: str


class LoginRequest(BaseModel):
    address: str
    signature: str
    nonce: str


class LoginResponse(BaseModel):
    token: str
    address: str


class UserProfile(BaseModel):
    address: str
    nickname: Optional[str] = None
    avatar: Optional[str] = None
    ens: Optional[str] = None
    bio: Optional[str] = None
    created_at: Optional[str] = None


# ============ 分析 ============

class AnalyzeRequest(BaseModel):
    prompt: str = Field(..., min_length=1)
    persona: str = "operator"
    report_style: Optional[str] = Field(None, max_length=500)
    token_name: Optional[str] = None
    contract_addr: Optional[str] = None
    chain: Optional[str] = None


class AnalyzeResponse(BaseModel):
    analysis_id: int
    report: dict


class ComparisonRequest(BaseModel):
    watchlist_ids: list[int] = Field(..., min_length=2, max_length=5)
    persona: str = "operator"
    report_style: Optional[str] = Field(
        "Detailed horizontal comparison with evidence and limitations",
        max_length=500,
    )


# ============ 帖子 ============

class CreatePostRequest(BaseModel):
    content: str = Field("", max_length=500)
    attached_analysis_id: Optional[int] = None
    image_data: Optional[str] = None


class PostResponse(BaseModel):
    id: int
    author: str
    author_nickname: Optional[str] = None
    content: str
    attached_analysis_id: Optional[int] = None
    like_count: int = 0
    repost_count: int = 0
    created_at: str


class RepostRequest(BaseModel):
    quote_text: Optional[str] = Field(None, max_length=500)


# ============ NFT ============

class MintNFTRequest(BaseModel):
    analysis_id: int
    token_uri: str


class MintNFTResponse(BaseModel):
    token_id: str
    tx_hash: str
    contract_address: str


# ============ 用户 ============

class UpdateProfileRequest(BaseModel):
    nickname: Optional[str] = None
    avatar: Optional[str] = None
    bio: Optional[str] = None
