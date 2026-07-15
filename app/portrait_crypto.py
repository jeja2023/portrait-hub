import base64
import hashlib
import hmac
import os
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.settings import (
    ENCRYPTION_KDF,
    ENCRYPTION_KEY,
    ENCRYPTION_KEY_ID,
    ENCRYPTION_KEYRING,
    ENCRYPTION_PBKDF2_ITERATIONS,
    REQUIRE_ENCRYPTION,
)

AES_GCM_ALGORITHM = "aes-256-gcm"
LEGACY_XOR_ALGORITHM = "hmac-sha256-xor-stream"
LEGACY_SHA256_KDF = "sha256"
PBKDF2_SHA256_KDF = "pbkdf2-sha256"
RAW_BASE64_KDF = "raw-base64"
AES_GCM_NONCE_BYTES = 12
PBKDF2_SALT_BYTES = 16
DEFAULT_ENCRYPTION_KEY_ID = "primary"
MAX_ENCRYPTION_KEY_ID_LENGTH = 64
ENCRYPTION_KEY_ID_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")


def encryption_enabled() -> bool:
    return bool(ENCRYPTION_KEY)


def encryption_required() -> bool:
    return bool(REQUIRE_ENCRYPTION)


def normalize_encryption_key_id(value: str | None) -> str:
    cleaned = str(value or "").strip() or DEFAULT_ENCRYPTION_KEY_ID
    if len(cleaned) > MAX_ENCRYPTION_KEY_ID_LENGTH or any(char not in ENCRYPTION_KEY_ID_CHARS for char in cleaned):
        raise ValueError("加密密钥 ID 无效")
    return cleaned


def current_encryption_key_id() -> str:
    return normalize_encryption_key_id(ENCRYPTION_KEY_ID)


def derive_key(
    secret: str | None = None,
    *,
    kdf: str | None = None,
    salt: bytes | None = None,
    iterations: int | None = None,
) -> bytes:
    key_material = ENCRYPTION_KEY if secret is None else secret
    kdf_name = str(kdf or ENCRYPTION_KDF or PBKDF2_SHA256_KDF).strip().lower()
    if kdf_name == RAW_BASE64_KDF:
        raw = base64.b64decode(key_material.encode("ascii"))
        if len(raw) != 32:
            raise ValueError("raw-base64 ENCRYPTION_KEY 必须解码为 32 字节")
        return raw
    if kdf_name == PBKDF2_SHA256_KDF:
        if salt is None:
            return hashlib.sha256(key_material.encode("utf-8")).digest()
        rounds = max(100_000, int(iterations or ENCRYPTION_PBKDF2_ITERATIONS))
        return hashlib.pbkdf2_hmac("sha256", key_material.encode("utf-8"), salt, rounds, dklen=32)
    if kdf_name == LEGACY_SHA256_KDF:
        return hashlib.sha256(key_material.encode("utf-8")).digest()
    raise ValueError("不支持的加密 KDF")


def parse_encryption_keyring() -> dict[str, str]:
    keyring: dict[str, str] = {}
    for raw_entry in str(ENCRYPTION_KEYRING or "").split(","):
        entry = raw_entry.strip()
        if not entry:
            continue
        if "=" not in entry:
            raise ValueError("ENCRYPTION_KEYRING 条目必须使用 key_id=secret")
        raw_key_id, raw_secret = entry.split("=", 1)
        key_id = normalize_encryption_key_id(raw_key_id)
        secret = raw_secret.strip()
        if not secret:
            raise ValueError("ENCRYPTION_KEYRING 条目必须包含非空密钥")
        if key_id in keyring:
            raise ValueError("ENCRYPTION_KEYRING 中存在重复的加密密钥 ID")
        keyring[key_id] = secret
    return keyring


def encryption_key_materials() -> dict[str, str]:
    materials = parse_encryption_keyring()
    if ENCRYPTION_KEY:
        active_key_id = current_encryption_key_id()
        previous = materials.get(active_key_id)
        if previous is not None and previous != ENCRYPTION_KEY:
            raise ValueError("活动加密密钥 ID 与 ENCRYPTION_KEYRING 冲突")
        materials[active_key_id] = ENCRYPTION_KEY
    return materials


def candidate_decryption_keys(
    key_id: str | None = None,
    *,
    kdf: str | None = None,
    salt: bytes | None = None,
    iterations: int | None = None,
) -> list[tuple[str, bytes]]:
    materials = encryption_key_materials()
    if key_id:
        normalized = normalize_encryption_key_id(key_id)
        if normalized not in materials:
            raise ValueError("加密载荷的密钥 ID 未配置")
        return [(normalized, derive_key(materials[normalized], kdf=kdf, salt=salt, iterations=iterations))]
    return [
        (item_key_id, derive_key(secret, kdf=kdf, salt=salt, iterations=iterations))
        for item_key_id, secret in materials.items()
    ]


def xor_stream(data: bytes, key: bytes) -> bytes:
    output = bytearray()
    counter = 0
    while len(output) < len(data):
        output.extend(hmac.new(key, counter.to_bytes(8, "big"), hashlib.sha256).digest())
        counter += 1
    return bytes(byte ^ mask for byte, mask in zip(data, output, strict=False))


def encrypt_bytes(data: bytes) -> dict[str, Any]:
    if not encryption_enabled():
        if encryption_required():
            raise RuntimeError("当 REQUIRE_ENCRYPTION=true 时，ENCRYPTION_KEY 为必填项")
        return {"encrypted": False, "data": base64.b64encode(data).decode("ascii")}
    key_id = current_encryption_key_id()
    kdf_name = str(ENCRYPTION_KDF or PBKDF2_SHA256_KDF).strip().lower()
    salt = os.urandom(PBKDF2_SALT_BYTES) if kdf_name == PBKDF2_SHA256_KDF else None
    key = derive_key(kdf=kdf_name, salt=salt)
    nonce = os.urandom(AES_GCM_NONCE_BYTES)
    encrypted = AESGCM(key).encrypt(nonce, data, None)
    payload = {
        "encrypted": True,
        "algorithm": AES_GCM_ALGORITHM,
        "key_id": key_id,
        "kdf": kdf_name,
        "nonce": base64.b64encode(nonce).decode("ascii"),
        "data": base64.b64encode(encrypted).decode("ascii"),
    }
    if salt is not None:
        payload["salt"] = base64.b64encode(salt).decode("ascii")
        payload["iterations"] = max(100_000, int(ENCRYPTION_PBKDF2_ITERATIONS))
    return payload


def decrypt_bytes(payload: dict[str, Any]) -> bytes:
    data = base64.b64decode(str(payload.get("data", "")).encode("ascii"))
    if not payload.get("encrypted"):
        return data
    algorithm = str(payload.get("algorithm") or LEGACY_XOR_ALGORITHM)
    key_id = str(payload.get("key_id") or "").strip() or None
    if algorithm == AES_GCM_ALGORITHM:
        nonce = base64.b64decode(str(payload.get("nonce", "")).encode("ascii"))
        kdf_name = str(payload.get("kdf") or LEGACY_SHA256_KDF).strip().lower()
        salt = base64.b64decode(str(payload.get("salt", "")).encode("ascii")) if payload.get("salt") else None
        iterations = int(payload.get("iterations") or ENCRYPTION_PBKDF2_ITERATIONS)
        last_error: InvalidTag | None = None
        for _, key in candidate_decryption_keys(key_id, kdf=kdf_name, salt=salt, iterations=iterations):
            try:
                return AESGCM(key).decrypt(nonce, data, None)
            except InvalidTag as exc:
                last_error = exc
        raise ValueError("加密载荷认证失败") from last_error
    if algorithm != LEGACY_XOR_ALGORITHM:
        raise ValueError(f"不支持的加密载荷算法：{algorithm}")
    for _, key in candidate_decryption_keys(key_id, kdf=LEGACY_SHA256_KDF):
        expected = hmac.new(key, data, hashlib.sha256).hexdigest()
        if hmac.compare_digest(expected, str(payload.get("digest", ""))):
            return xor_stream(data, key)
    raise ValueError("加密载荷摘要不匹配")


def protect_embedding(embedding: list[float]) -> dict[str, Any]:
    raw = ",".join(f"{float(value):.8f}" for value in embedding).encode("utf-8")
    payload = encrypt_bytes(raw)
    payload["dim"] = len(embedding)
    return payload


def reveal_embedding(payload: dict[str, Any]) -> list[float]:
    raw = decrypt_bytes(payload).decode("utf-8")
    return [float(item) for item in raw.split(",")] if raw else []
