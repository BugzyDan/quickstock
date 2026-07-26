from src.storage.cache import SecureCache


def test_secure_cache_round_trip(tmp_path):
    cache_file = tmp_path / "secure_cache.json"
    cache = SecureCache(str(cache_file))

    payload = {
        "username": "cashier1",
        "api_token": "secret-token",
        "hash": "hashed-password",
        "role": "cashier",
    }

    cache.save(payload)
    loaded = cache.load()

    assert loaded["username"] == "cashier1"
    assert loaded["api_token"] == "secret-token"
    assert loaded["hash"] == "hashed-password"
    assert loaded["role"] == "cashier"
