"""A2A tool schemas — what the LLM sees."""

A2A_DISCOVER = {
    "name": "a2a_discover",
    "description": (
        "Discover a remote A2A agent by fetching its Agent Card. "
        "Returns the agent's name, description, capabilities, and supported skills. "
        "Use this before calling an agent to understand what it can do. "
        "Provide either 'url' or 'name' (at least one is required)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "Base URL of the remote agent (e.g. http://agent:41731)",
            },
            "name": {
                "type": "string",
                "description": "Name of a configured agent from ~/.hermes/config.yaml",
            },
        },
    },
}

A2A_CALL = {
    "name": "a2a_call",
    "description": (
        "Send a message/task to a remote A2A agent and get its response. "
        "Use a2a_discover first to learn what the agent can do."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "Base URL of the remote agent",
            },
            "name": {
                "type": "string",
                "description": "Name of a configured agent (alternative to url)",
            },
            "message": {
                "type": "string",
                "description": "The message or task to send to the remote agent",
            },
            "task_id": {
                "type": "string",
                "description": "Optional task ID for continuing an existing conversation",
            },
            "reply_to_task_id": {
                "type": "string",
                "description": "Task ID this message is replying to (for multi-turn threading)",
            },
            "context_id": {
                "type": "string",
                "description": "Optional native A2A context ID for correlating related messages",
            },
            "background": {
                "type": "boolean",
                "description": "Submit the task and return immediately without polling; final response can be retrieved with a2a_get or notification",
            },
            "notify": {
                "type": "boolean",
                "description": "When background=true, request remote final notification if supported",
            },
            "notify_url": {
                "type": "string",
                "description": "Optional callback URL for background notification; normally omit unless explicitly configured",
            },
            "parts": {
                "type": "array",
                "description": "Optional extra A2A content parts, such as data or safe file/image/audio references",
                "items": {"type": "object"},
            },
            "intent": {
                "type": "string",
                "enum": ["action_request", "review", "consultation", "notification", "instruction"],
                "description": "What kind of message this is",
            },
            "expected_action": {
                "type": "string",
                "enum": ["reply", "forward", "acknowledge"],
                "description": "What you expect the remote agent to do",
            },
        },
        "required": ["message"],
    },
}

A2A_LIST = {
    "name": "a2a_list",
    "description": (
        "List all configured remote A2A agents from ~/.hermes/config.yaml. "
        "Shows agent names, URLs, and descriptions."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
    },
}


A2A_GET = {
    "name": "a2a_get",
    "description": "Fetch the current state/result of a remote A2A task without submitting a new message.",
    "parameters": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Base URL of the remote agent"},
            "name": {"type": "string", "description": "Name of a configured agent (alternative to url)"},
            "task_id": {"type": "string", "description": "Remote task ID to fetch"},
        },
        "required": ["task_id"],
    },
}

A2A_CANCEL = {
    "name": "a2a_cancel",
    "description": "Cancel a remote A2A task when the remote agent supports cancellation.",
    "parameters": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Base URL of the remote agent"},
            "name": {"type": "string", "description": "Name of a configured agent (alternative to url)"},
            "task_id": {"type": "string", "description": "Remote task ID to cancel"},
        },
        "required": ["task_id"],
    },
}
