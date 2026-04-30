import yaml

from hermes_a2a_cli.installer import build_compat_webhook_routes, dump_config


def test_discord_wake_session_generates_legacy_route_shape():
    a2a = {
        "wake": {
            "secret": "secret-value",
            "prompt": "[A2A trigger]",
            "session": {
                "platform": "discord",
                "chat_id": "chat-1",
                "chat_type": "group",
                "actor": {"id": "user-1", "name": "Owner"},
            },
        },
        "dashboard": {"enabled": True},
    }

    routes = build_compat_webhook_routes(a2a)

    assert routes["a2a_trigger"] == {
        "secret": "secret-value",
        "prompt": "[A2A trigger]",
        "deliver": "discord",
        "deliver_extra": {"chat_id": "chat-1"},
        "source": {
            "platform": "discord",
            "chat_type": "group",
            "chat_id": "chat-1",
            "user_id": "user-1",
            "user_name": "Owner",
        },
    }
    assert routes["a2a_dashboard"] == {"secret": "secret-value", "prompt": "[A2A dashboard]"}


def test_telegram_thread_id_is_preserved_in_compat_route():
    a2a = {
        "wake": {
            "secret": "secret-value",
            "session": {
                "platform": "telegram",
                "chat_id": "191060132",
                "thread_id": 67618,
                "chat_type": "dm",
                "actor": {"id": "191060132", "name": "Owner"},
            },
        }
    }

    route = build_compat_webhook_routes(a2a)["a2a_trigger"]

    assert route["deliver_extra"] == {"chat_id": "191060132", "thread_id": 67618}
    assert route["source"]["thread_id"] == 67618


def test_dump_config_does_not_emit_yaml_aliases_for_shared_routes():
    routes = build_compat_webhook_routes(
        {
            "wake": {
                "secret": "secret-value",
                "session": {
                    "platform": "discord",
                    "chat_id": "chat-1",
                    "actor": {"id": "user-1", "name": "Owner"},
                },
            }
        }
    )
    cfg = {
        "webhook": {"extra": {"routes": routes}},
        "platforms": {"webhook": {"extra": {"routes": routes}}},
    }

    dumped = dump_config(cfg)

    assert "&id" not in dumped
    assert "*id" not in dumped
    loaded = yaml.safe_load(dumped)
    assert loaded["webhook"]["extra"]["routes"] == loaded["platforms"]["webhook"]["extra"]["routes"]


def test_session_ref_alone_does_not_guess_actor_for_compat_route():
    routes = build_compat_webhook_routes({"wake": {"secret": "***", "session_ref": {"platform": "discord", "chat_id": "chat-1"}}})

    assert routes["a2a_trigger"] == {"secret": "***", "prompt": "[A2A trigger]"}


def test_generated_resolved_session_can_create_compat_route_without_canonical_actor():
    a2a = {
        "wake": {
            "secret": "***",
            "session_ref": {"platform": "discord", "chat_id": "chat-1"},
            "session": {
                "platform": "discord",
                "chat_id": "chat-1",
                "chat_type": "group",
                "actor": {"id": "user-1", "name": "Owner"},
            },
        }
    }

    route = build_compat_webhook_routes(a2a)["a2a_trigger"]

    assert route["deliver"] == "discord"
    assert route["source"]["user_id"] == "user-1"
