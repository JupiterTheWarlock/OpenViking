"""Request a structured memory proposal from a headless Claude Code session."""

import asyncio
import base64
import json
import shutil
import tempfile
from pathlib import Path

from openviking.session.memory.external_proposal import (
    REPLY_SCHEMA,
    parse_proposal,
    proposal_prompt,
)

MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}


def result_proposal(output, tools, exit_code):
    result = None
    if output.seek(0, 2) > 48_000_000:
        raise ValueError("Claude Code response exceeds size limit")
    output.seek(0)
    for line in output:
        if line.strip():
            event = json.loads(line)
            if event.get("type") == "result":
                result = event
    if exit_code or not result or result.get("is_error") or result.get("subtype") != "success":
        raise RuntimeError(
            f"Claude Code extraction failed (exit {exit_code}); check model and service login"
        )
    return parse_proposal(result.get("structured_output"), tools)


async def request_proposal(messages, tools, timeout, documents, images, *, model=None):
    executable = shutil.which("claude")
    if not executable:
        raise RuntimeError("Claude Code CLI is missing from the service PATH")
    content = [{"type": "text", "text": proposal_prompt(messages, tools, documents, images)}]
    for name, image in images.items():
        content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": MEDIA_TYPES[Path(name).suffix.lower()],
                    "data": base64.b64encode(image).decode("ascii"),
                },
            }
        )
    message = {"type": "user", "message": {"role": "user", "content": content}}
    packet = (json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8")
    args = [
        executable,
        "-p",
        "--input-format",
        "stream-json",
        "--output-format",
        "stream-json",
        "--verbose",
        "--json-schema",
        json.dumps(REPLY_SCHEMA),
        "--tools",
        "",
        "--strict-mcp-config",
        "--setting-sources",
        "user",
        "--settings",
        '{"disableAllHooks":true}',
        "--disable-slash-commands",
        "--no-session-persistence",
    ]
    if model:
        args.extend(["--model", model])
    with (
        tempfile.TemporaryDirectory(prefix="ov-claude-") as directory,
        tempfile.TemporaryFile() as output,
    ):
        process = await asyncio.create_subprocess_exec(
            *args,
            cwd=directory,
            stdin=asyncio.subprocess.PIPE,
            stdout=output,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            await asyncio.wait_for(process.communicate(packet), timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise RuntimeError("Claude Code memory proposal timed out") from exc
        finally:
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                await process.wait()
        return result_proposal(output, tools, process.returncode)
