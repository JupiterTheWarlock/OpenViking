"""Codex CLI transport for session memory proposals."""

import asyncio
import json
import shutil
import tempfile
from pathlib import Path

from openviking.session.memory.external_proposal import (
    REPLY_SCHEMA,
    parse_proposal,
    proposal_prompt,
)
from openviking.session.memory.external_proposal import (
    read_evidence as read_evidence,
)


async def request_proposal(messages, tools, timeout, documents, images):
    executable = shutil.which("codex.exe") or shutil.which("codex")
    if not executable:
        raise RuntimeError("Codex CLI is missing from the service PATH")
    prompt = proposal_prompt(messages, tools, documents, images).encode("utf-8")
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
        return parse_proposal(reply, tools)
