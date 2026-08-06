from __future__ import annotations


def _cryptodome():
    try:
        from Crypto.Cipher import AES
    except ImportError:
        try:
            from Cryptodome.Cipher import AES
        except ImportError as exc:
            raise ImportError(
                "AES-128 decryption requires pycryptodome: uv add pycryptodome"
            ) from exc
    return AES


def unpad_pkcs7(payload: bytes, block_size: int = 16) -> bytes:
    if not payload:
        raise ValueError("empty payload")

    padding = payload[-1]
    if not (1 <= padding <= block_size):
        raise ValueError("invalid pkcs7 padding length")
    if payload[-padding:] != bytes([padding]) * padding:
        raise ValueError("invalid pkcs7 padding")

    return payload[:-padding]


def aes_128_cbc_decrypt(ciphertext: bytes, key: bytes, iv: bytes) -> bytes:
    if len(key) != 16:
        raise ValueError("aes-128 requires a 16-byte key")
    if len(iv) != 16:
        raise ValueError("aes-128-cbc requires a 16-byte iv")

    aes = _cryptodome()
    cipher = aes.new(key, aes.MODE_CBC, iv)
    return unpad_pkcs7(cipher.decrypt(ciphertext))
