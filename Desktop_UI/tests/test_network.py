from src.infrastructure.network import build_api_url, requires_https_in_production, validate_api_base_url


def test_validate_api_base_url_allows_local_http():
    valid, error = validate_api_base_url("http://localhost:8000", env="production")
    assert valid is True
    assert error == ""


def test_validate_api_base_url_rejects_non_https_in_production():
    valid, error = validate_api_base_url("http://api.example.com", env="production")
    assert valid is False
    assert "HTTPS" in error


def test_build_api_url_normalizes_suffix_and_slashes():
    assert build_api_url("https://api.example.com/inventory/", "/api/health/") == "https://api.example.com/api/health/"


def test_requires_https_in_production_ignores_localhost():
    assert requires_https_in_production("http://127.0.0.1:8000", env="production") is False
