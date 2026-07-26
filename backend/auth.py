"""
钱包签名认证模块 — Sign-In with Ethereum 风格
用户用钱包签名 nonce 证明所有权，后端验证后签发 JWT

安全约束：
- 钱包连接由用户主动触发（前端 eth_requestAccounts）
- 签名由用户在 MetaMask 中手动确认
- 后端仅验证签名有效性，不接触私钥
- 生产环境需加入人机验证（reCAPTCHA / turnstile）
"""

import secrets
import time
from datetime import datetime, timedelta
from typing import Optional
from jose import jwt, JWTError

# 简单内存 nonce 存储（生产环境应使用 Redis）
_nonce_store: dict[str, dict] = {}

# JWT 配置
JWT_SECRET = secrets.token_hex(32)  # 生产环境使用环境变量
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = 24


# ============ Nonce 管理 ============

def generate_nonce(address: str) -> str:
    """
    为用户生成随机 nonce，用于签名验证。
    存储 nonce + 过期时间（10 分钟）。
    """
    address = address.lower()
    nonce = secrets.token_hex(16)
    message = (
        f"欢迎使用 Meme Ops！\n\n"
        f"签名此消息以验证你是钱包 {address} 的所有者。\n\n"
        f"Nonce: {nonce}\n"
        f"过期时间: {(datetime.utcnow() + timedelta(minutes=10)).isoformat()}\n\n"
        f"此操作不消耗 Gas，不会发送任何交易。"
    )
    _nonce_store[address] = {
        "nonce": nonce,
        "message": message,
        "expires_at": time.time() + 600,  # 10 分钟
    }
    return nonce


def get_nonce_message(address: str) -> Optional[str]:
    data = _nonce_store.get(address.lower())
    if not data or time.time() > data["expires_at"]:
        _nonce_store.pop(address.lower(), None)
        return None
    return data["message"]


# ============ 签名验证 ============

def verify_signature(address: str, signature: str, signed_message: str) -> bool:
    """
    验证钱包签名是否有效。

    用户在前端用 personal_sign 对 nonce 消息签名。
    后端恢复签名地址，比对是否匹配。
    """
    address = address.lower()
    data = _nonce_store.get(address)

    # 验证消息一致性（前端传回的 signed_message 就是完整 nonce 消息）
    if not data or data["message"] != signed_message:
        return False

    # 验证 nonce 过期
    if time.time() > data["expires_at"]:
        _nonce_store.pop(address, None)
        return False

    # 恢复签名地址
    try:
        from eth_account.messages import encode_defunct
        from eth_account import Account

        message = encode_defunct(text=data["message"])
        recovered = Account.recover_message(message, signature=signature)

        if recovered.lower() == address:
            _nonce_store.pop(address, None)  # nonce 只能使用一次
            return True
        return False
    except Exception:
        return False


# ============ JWT ============

def create_token(address: str) -> str:
    payload = {
        "sub": address.lower(),
        "iat": datetime.utcnow(),
        "exp": datetime.utcnow() + timedelta(hours=JWT_EXPIRE_HOURS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def verify_token(token: str) -> Optional[str]:
    """验证 JWT，返回钱包地址或 None"""
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload.get("sub")
    except JWTError:
        return None
