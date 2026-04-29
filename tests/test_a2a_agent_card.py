from plugin.server import A2AServer


def test_agent_card_advertises_native_interface_metadata(monkeypatch):
    monkeypatch.setenv("A2A_AUTH_TOKEN", "token")
    monkeypatch.setenv("A2A_AGENT_NAME", "primary_agent")
    monkeypatch.setenv("A2A_AGENT_DESCRIPTION", "Primary")
    monkeypatch.setenv("A2A_PUBLIC_URL", "http://127.0.0.1:41731")
    monkeypatch.setenv("A2A_REQUIRE_AUTH", "true")

    server = A2AServer("127.0.0.1", 0)
    try:
        card = server.build_agent_card()
    finally:
        server.server_close()

    assert card["name"] == "primary_agent"
    assert card["url"] == "http://127.0.0.1:41731"
    assert card["preferredTransport"] == "JSONRPC"
    assert card["supportedInterfaces"][0]["url"] == "http://127.0.0.1:41731"
    assert card["supportedInterfaces"][0]["protocolBinding"] == "JSONRPC"
    assert card["additionalInterfaces"][0]["transport"] == "JSONRPC"
    assert "text/plain" in card["defaultInputModes"]
    assert "application/json" in card["defaultInputModes"]
    assert "text/plain" in card["defaultOutputModes"]
    assert card["capabilities"]["streaming"] is False
    assert card["capabilities"]["pushNotifications"] is True
    assert card["capabilities"]["structuredMetadata"] is True
    assert any(ext["uri"].endswith("multimodal-reference/v1") for ext in card["capabilities"]["extensions"])
    assert "tags" in card["skills"][0]
    assert card["securitySchemes"]["bearerAuth"]["scheme"] == "bearer"
    assert card["security"] == [{"bearerAuth": []}]
    assert card["authentication"]["schemes"] == ["bearer"]


def test_agent_card_does_not_advertise_push_without_public_url_or_auth(monkeypatch):
    monkeypatch.delenv("A2A_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("A2A_PUBLIC_URL", raising=False)
    monkeypatch.setenv("A2A_REQUIRE_AUTH", "true")

    server = A2AServer("127.0.0.1", 0)
    try:
        card = server.build_agent_card()
    finally:
        server.server_close()

    assert card["capabilities"]["pushNotifications"] is False
