"""Codex CLI transport for session memory proposals."""

import asyncio
import json
import shutil
import tempfile
from pathlib import Path

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


async def request_proposal(messages, tools, timeout, documents, images):
    executable = shutil.which("codex.exe") or shutil.which("codex")
    if not executable:
        raise RuntimeError("Codex CLI is missing from the service PATH")
    prompt = (
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
    ).encode("utf-8")
    with tempfile.TemporaryDirectory(prefix="ov-codex-") as directory:
        schema = Path(directory, "schema.json")
        output = Path(directory, "reply.json")
        schema.write_text(json.dumps(REPLY_SCHEMA), encoding="utf-8")
        attachments = []
        for index, (name, data) in enumerate(images.items()):
            attachment = Path(directory, f"image-{index}{Path(name).suffix.lower()}")
            attachment.write_bytes(data)
            attachments.extend(["--image", str(attachment)])
        args = [
            executable,
            "exec",
            *attachments,
            "--skip-git-repo-check",
            "--ephemeral",
            "--ignore-user-config",
            "--sandbox",
            "read-only",
            "--disable",
            "shell_tool",
            "--disable",
            "unified_exec",
            "--disable",
            "plugins",
            "-c",
            'web_search="disabled"',
            "--output-schema",
            str(schema),
            "--output-last-message",
            str(output),
            "-",
        ]
        process = await asyncio.create_subprocess_exec(
            *args,
            cwd=directory,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            await asyncio.wait_for(process.communicate(prompt), timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise RuntimeError("Codex memory proposal timed out") from exc
        finally:
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                await process.wait()
        if process.returncode:
            raise RuntimeError(
                f"Codex exited with code {process.returncode}; check CLI version and service login"
            )
        if not output.is_file() or not 0 < output.stat().st_size <= 2_000_000:
            raise ValueError("Missing or oversized Codex reply")
        reply = json.loads(output.read_text(encoding="utf-8"))
        if not isinstance(reply, dict) or set(reply) != set(REPLY_SCHEMA["required"]):
            raise ValueError("Invalid Codex reply envelope")
        if not all(isinstance(value, str) for value in reply.values()):
            raise ValueError("Invalid Codex reply fields")
        payload = json.loads(reply["payload"])
        if not isinstance(payload, dict):
            raise ValueError("Codex payload must be a JSON object")
        if reply["action"] == "read":
            if reply["name"] not in {tool["function"]["name"] for tool in tools}:
                raise ValueError("Codex requested an unavailable OpenViking tool")
        elif reply["action"] != "propose" or reply["name"] or not payload:
            raise ValueError("Invalid Codex proposal")
        return reply["name"], payload
