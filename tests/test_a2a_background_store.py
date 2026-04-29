from plugin import task_store


def test_task_store_persists_background_task_and_updates_state(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    record = task_store.create_task(
        task_id="local-1",
        direction="outbound",
        agent_name="reviewer",
        url="http://agent.local",
        state="submitted",
        context_id="ctx-1",
        local_task_id="local-1",
        remote_task_id="",
        notify_requested=True,
    )
    updated = task_store.update_task("local-1", state="working", remote_task_id="remote-1")
    loaded = task_store.get_task("local-1")

    assert record["task_id"] == "local-1"
    assert updated["remote_task_id"] == "remote-1"
    assert loaded["state"] == "working"
    assert loaded["agent_name"] == "reviewer"
    assert loaded["notify_requested"] is True


def test_task_store_terminal_state_is_immutable(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    task_store.create_task("t1", direction="outbound", agent_name="reviewer", url="http://agent.local", state="submitted")
    completed = task_store.update_task("t1", state="completed", response="done")
    late = task_store.update_task("t1", state="working", response="late")

    assert completed["state"] == "completed"
    assert late["state"] == "completed"
    assert late["response"] == "done"


def test_task_store_can_match_by_remote_task_id_and_agent(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    task_store.create_task(
        "local-1",
        direction="outbound",
        agent_name="reviewer",
        url="http://agent.local",
        state="submitted",
        remote_task_id="remote-1",
    )

    assert task_store.find_task("remote-1", agent_name="reviewer")["task_id"] == "local-1"
    assert task_store.find_task("remote-1", agent_name="other") is None
