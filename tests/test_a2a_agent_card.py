from plugin.server import A2AServer


def test_agent_card_advertises_native_interface_metadata(monkeypatch):
    monkeypatch.setattr(
        "plugin.server.get_identity_config",
        lambda: type("Identity", (), {"name": "primary_agent", "description": "Primary"})(),
    )
    monkeypatch.setattr(
        "plugin.server.get_server_config",
        lambda: type(
            "ServerCfg",
            (),
            {
                "host": "127.0.0.1",
                "port": 41731,
                "public_url": "http://127.0.0.1:41731",
                "require_auth": True,
                "sync_response_timeout_seconds": 120,
                "active_task_timeout_seconds": 7200,
                "max_pending_tasks": 10,
                "auth_token": "token",
            },
        )(),
    )

    server = A2AServer("127.0.0.1", 0)
    try:
        card = server.build_agent_card()
    finally:
        server.server_close()

    assert card["name"] == "primary_agent"
    assert card["url"] == "http://127.0.0.1:41731"
    assert card["version"] == "0.3.0"
    assert card["metadata"]["pluginVersion"] == "0.3.0"
    assert card["metadata"]["a2aProtocolVersion"] == "0.3.0"
    assert "hermesRuntimeVersion" in card["metadata"]
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
    monkeypatch.setattr(
        "plugin.server.get_server_config",
        lambda: type(
            "ServerCfg",
            (),
            {
                "host": "127.0.0.1",
                "port": 41731,
                "public_url": "",
                "require_auth": True,
                "sync_response_timeout_seconds": 120,
                "active_task_timeout_seconds": 7200,
                "max_pending_tasks": 10,
                "auth_token": "",
            },
        )(),
    )

    server = A2AServer("127.0.0.1", 0)
    try:
        card = server.build_agent_card()
    finally:
        server.server_close()

    assert card["capabilities"]["pushNotifications"] is False
