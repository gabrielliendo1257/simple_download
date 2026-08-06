import pytest

from simple_downloader.engines.hls.crypt import aes_128_cbc_decrypt, unpad_pkcs7

try:
    import Crypto  # noqa: F401

    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False

needs_crypto = pytest.mark.skipif(not HAS_CRYPTO, reason="pycryptodome not installed")


def test_unpad_pkcs7_removes_padding() -> None:
    assert unpad_pkcs7(b"data" + b"\x04" * 4) == b"data"


def test_unpad_pkcs7_full_block_padding() -> None:
    assert unpad_pkcs7(bytes([0x01]) * 15 + bytes([0x01])) == bytes([0x01]) * 15


def test_unpad_pkcs7_empty_raises() -> None:
    with pytest.raises(ValueError, match="empty"):
        unpad_pkcs7(b"")


def test_unpad_pkcs7_invalid_padding_length_raises() -> None:
    with pytest.raises(ValueError, match="padding length"):
        unpad_pkcs7(b"abc\x00")


def test_unpad_pkcs7_corrupt_padding_raises() -> None:
    with pytest.raises(ValueError, match="padding"):
        unpad_pkcs7(b"data\x04\x01\x03")


@needs_crypto
def test_aes_128_cbc_roundtrip() -> None:
    from Crypto.Cipher import AES

    key = bytes(range(16))
    iv = bytes(range(16, 32))
    plaintext = b"hello hls segment!!"  # 19 bytes -> 13 bytes de padding 0x0d

    cipher = AES.new(key, AES.MODE_CBC, iv)
    ciphertext = cipher.encrypt(plaintext + bytes([13]) * 13)

    assert aes_128_cbc_decrypt(ciphertext, key, iv) == plaintext


@needs_crypto
def test_aes_128_cbc_wrong_key_raises_padding() -> None:
    from Crypto.Cipher import AES

    key = bytes(range(16))
    iv = bytes(range(16, 32))

    cipher = AES.new(key, AES.MODE_CBC, iv)
    ciphertext = cipher.encrypt(b"x" * 16)

    with pytest.raises(ValueError):
        aes_128_cbc_decrypt(ciphertext, bytes(16), iv)


def test_aes_128_cbc_rejects_bad_key_length() -> None:
    with pytest.raises(ValueError, match="16-byte key"):
        aes_128_cbc_decrypt(b"x" * 16, b"short", bytes(16))
