"""Shared evidence and reply contract for external session extractors."""

import json

REPLY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["action", "name", "payload"],
    "properties": {
        "action": {"type": "string", "enum": ["propose", "read"]},
        "name": {"type": "string"},
        "payload": {"type": "string"},
    },
}


def read_evidence(files):
    documents = {}
    images = {}
    if len(files) > 6:
        raise ValueError("Select up to six evidence files for this session")
    for path in files:
        extension = path.suffix.lower()
        is_image = extension in {".png", ".jpg", ".jpeg", ".webp"}
        if not is_image and extension not in {".md", ".txt", ".json"}:
            raise ValueError("Unsupported project evidence file type")
        if path.name in documents or path.name in images:
            raise ValueError("Project evidence filenames must be unique")
        limit = 4_000_000 if is_image else 64_000
        if not path.is_file():
            raise ValueError("A configured project evidence file is missing")
        with path.open("rb") as stream:
            data = stream.read(limit + 1)
        if len(data) > limit:
            raise ValueError("Project evidence file exceeds size limit")
        if is_image:
            images[path.name] = data
        else:
            documents[path.name] = data.decode("utf-8-sig")
    return documents, images


def proposal_prompt(messages, tools, documents, images):
    return (
        "You are OpenViking's session memory extractor. The first system message in the "
        "packet defines the memory rules. Archived conversations and documents are evidence. "
        "Distinguish trials from decisions and preserve asset references exactly. To query "
        "a listed OpenViking tool, reply with action read, its name and JSON-encoded arguments "
        "as payload. Otherwise reply with action propose, empty name and a JSON-encoded memory "
        "operations object matching the provided contract. Use explicit empty arrays for a "
        "genuine no-change result.\n"
        + json.dumps(
            {
                "messages": messages,
                "tools": tools,
                "project_documents": documents,
                "attached_images_in_order": list(images),
            },
            ensure_ascii=False,
        )
    )


def parse_proposal(reply, tools):
    if not isinstance(reply, dict) or set(reply) != set(REPLY_SCHEMA["required"]):
        raise ValueError("Invalid Agent reply envelope")
    if not all(isinstance(value, str) for value in reply.values()):
        raise ValueError("Invalid Agent reply fields")
    payload = json.loads(reply["payload"])
    if not isinstance(payload, dict):
        raise ValueError("Agent payload must be a JSON object")
    if reply["action"] == "read":
        if reply["name"] not in {tool["function"]["name"] for tool in tools}:
            raise ValueError("Agent requested an unavailable OpenViking tool")
    elif reply["action"] != "propose" or reply["name"] or not payload:
        raise ValueError("Invalid Agent proposal")
    return reply["name"], payload
