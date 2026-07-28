def test_health_reports_provider_configuration_without_secret(client):
    response = client.get("/api/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload == {
        "status": "ok",
        "vision_provider": "mock",
        "image_provider": "mock",
        "volcengine_configured": False,
        "bailian_configured": False,
    }
    assert "key" not in str(payload).lower()
