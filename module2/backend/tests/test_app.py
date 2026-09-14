"""App-level wiring: CORS for the Vite dev server, error shapes, configuration."""

from app.config import DEFAULT_CORS_ORIGINS, Settings


def test_cors_preflight_allows_the_frontend_with_a_bearer_token(api):
    response = api.client.options(
        "/api/restaurant/waitlist",
        headers={
            "Origin": "http://localhost:3417",
            "Access-Control-Request-Method": "PATCH",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3417"
    assert "PATCH" in response.headers["access-control-allow-methods"]
    assert "authorization" in response.headers["access-control-allow-headers"].lower()


def test_cors_does_not_allow_other_origins(api):
    response = api.client.get("/api/restaurants", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in response.headers


def test_unknown_routes_use_the_error_shape(api):
    response = api.get("/nope")
    assert response.status_code == 404
    assert response.json() == {"code": "NOT_FOUND", "message": "We couldn't find that.", "field_errors": {}}


def test_malformed_json_is_a_validation_error(api):
    response = api.client.post(
        "/api/restaurants/1/waitlist", content="{not json", headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION"


def test_settings_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("WAITWISE_CORS_ORIGINS", "http://a.test, http://b.test")
    monkeypatch.setenv("WAITWISE_TOKEN_TTL_MINUTES", "30")
    monkeypatch.setenv("WAITWISE_SEED_DEMO_DATA", "false")
    settings = Settings.from_env()
    assert settings.cors_origins == ("http://a.test", "http://b.test")
    assert settings.token_ttl_minutes == 30
    assert settings.seed_demo_data is False


def test_defaults_target_the_vite_dev_server(monkeypatch):
    for name in ("WAITWISE_CORS_ORIGINS", "WAITWISE_TOKEN_TTL_MINUTES", "WAITWISE_SEED_DEMO_DATA"):
        monkeypatch.delenv(name, raising=False)
    settings = Settings.from_env()
    assert settings.cors_origins == DEFAULT_CORS_ORIGINS
    assert "http://localhost:3417" in settings.cors_origins
    assert settings.seed_demo_data is True


def test_unseeded_app_starts_empty(empty_api):
    assert empty_api.get("/restaurants").json() == []
    assert empty_api.post("/auth/login", json={"username": "admin", "password": "password"}).status_code == 401
