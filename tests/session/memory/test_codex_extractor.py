import asyncio
import json
import os
import sys
from types import SimpleNamespace

import pytest
from pydantic import BaseModel, ValidationError

from openviking.session import compressor_v3
from openviking.session.memory import codex_proposal
from openviking.session.memory.extract_loop import ExtractLoop
from openviking_cli.utils.config.memory_config import MemoryConfig


class GameDecision(BaseModel):
    screenshot: str
    trial_columns: int
    adopted_columns: int


class GameOperations(BaseModel):
    events: list[GameDecision]


MESSAGES = [
    {
        "role": "system",
        "content": "Return memory operations with an events array; each event "
        "has screenshot (filename), trial_columns (int), adopted_columns (int).",
    },
    {
        "role": "user",
        "content": "Four columns in pack-320.png were a small-screen trial. "
        "Record the final number from the project decision.",
    },
]
DECISION = {"screenshot": "pack-320.png", "trial_columns": 4, "adopted_columns": 3}
SPAWN = asyncio.create_subprocess_exec


def game_loop(
    tmp_path, monkeypatch, *, account="studio", user="designer", session="backpack", backend="codex"
):
    document = tmp_path / "decision.md"
    document.write_text("Approved: use three columns on small screens.", encoding="utf-8")
    config = MemoryConfig(
        session_extractor=backend,
        codex_project_evidence=[
            {
                "account_id": "studio",
                "user_id": "designer",
                "session_id": "backpack",
                "files": [document],
            }
        ],
    )
    monkeypatch.setattr(
        compressor_v3, "get_openviking_config", lambda: SimpleNamespace(memory=config)
    )
    monkeypatch.setattr(compressor_v3, "get_viking_fs", lambda: object())
    loop = compressor_v3.SessionCompressorV3(vikingdb=None)._get_or_create_react(
        ctx=SimpleNamespace(account_id=account, user=SimpleNamespace(user_id=user)),
        session_id=session,
        context_provider=object(),
        vlm_config=SimpleNamespace(model="test"),
    )
    loop._operations_model = GameOperations
    return loop


def stand_in(monkeypatch, body):
    monkeypatch.setattr(codex_proposal.shutil, "which", lambda _: sys.executable)

    async def run(*args, **kwargs):
        return await SPAWN(sys.executable, "-c", body, *args[1:], **kwargs)

    monkeypatch.setattr(codex_proposal.asyncio, "create_subprocess_exec", run)


def send_reply(action, name, payload):
    envelope = json.dumps({"action": action, "name": name, "payload": json.dumps(payload)})
    return (
        "import sys,pathlib; pathlib.Path(sys.argv[sys.argv.index('--output-last-message')+1])"
        f".write_text({envelope!r}, encoding='utf-8')"
    )


@pytest.mark.asyncio
async def test_game_proposal_through_cli(tmp_path, monkeypatch):
    loop = game_loop(tmp_path, monkeypatch)
    stand_in(
        monkeypatch,
        "import sys; data=sys.stdin.buffer.read(); "
        "assert b'pack-320.png' in data and b'Approved: use three columns' in data; "
        "assert '--ephemeral' in sys.argv and 'read-only' in sys.argv; "
        + send_reply("propose", "", {"events": [DECISION]}),
    )
    tools, proposal = await loop._call_llm(MESSAGES)
    assert tools is None
    assert proposal.events[0].model_dump() == DECISION


@pytest.mark.parametrize("scope", [{"account": "other"}, {"user": "other"}, {"session": "other"}])
def test_project_evidence_requires_exact_scope(tmp_path, monkeypatch, scope):
    assert game_loop(tmp_path, monkeypatch, **scope).files == []


def test_builtin_remains_default(tmp_path, monkeypatch):
    assert (
        type(game_loop(tmp_path, monkeypatch, backend=MemoryConfig().session_extractor))
        is ExtractLoop
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reply, error",
    [
        (("propose", "", {}), ValueError),
        (("propose", "", {"unknown": []}), ValueError),
        (("propose", "", {"events": ["invalid"]}), ValidationError),
        (("read", "unlisted", {}), ValueError),
    ],
)
async def test_bad_reply_is_a_failure(tmp_path, monkeypatch, reply, error):
    loop = game_loop(tmp_path, monkeypatch)
    stand_in(monkeypatch, send_reply(*reply))
    with pytest.raises(error):
        await loop._call_llm(MESSAGES)


@pytest.mark.asyncio
async def test_scoped_read_and_valid_noop(tmp_path, monkeypatch):
    loop = game_loop(tmp_path, monkeypatch)
    loop._tool_schemas = [{"function": {"name": "read"}}]
    stand_in(monkeypatch, send_reply("read", "read", {"page_id": "p1"}))
    calls, proposal = await loop._call_llm(MESSAGES)
    assert proposal is None
    assert (calls[0].name, calls[0].arguments) == ("read", {"page_id": "p1"})
    loop._disable_tools_for_iteration = True
    with pytest.raises(ValueError, match="unavailable"):
        await loop._call_llm(MESSAGES)
    stand_in(monkeypatch, send_reply("propose", "", {"events": []}))
    _, proposal = await loop._call_llm(MESSAGES)
    assert proposal.events == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body,timeout,error",
    [("raise SystemExit(7)", 10, "code 7"), ("import time;time.sleep(8)", 0.1, "timed out")],
)
async def test_failed_cli_raises(monkeypatch, body, timeout, error):
    stand_in(monkeypatch, body)
    with pytest.raises(RuntimeError, match=error):
        await codex_proposal.request_proposal(MESSAGES, [], timeout, {}, {})


@pytest.mark.asyncio
async def test_image_staged_only_for_call(tmp_path, monkeypatch):
    image = tmp_path / "pack-320.png"
    image.write_bytes(b"image fixture")
    documents, images = codex_proposal.read_evidence([image])
    stand_in(
        monkeypatch,
        "import sys,pathlib; image=pathlib.Path(sys.argv[sys.argv.index('--image')+1]); "
        "assert image.read_bytes() == b'image fixture'; "
        "assert b'pack-320.png' in sys.stdin.buffer.read(); "
        + send_reply("propose", "", {"events": []}),
    )
    assert await codex_proposal.request_proposal(MESSAGES, [], 10, documents, images) == (
        "",
        {"events": []},
    )


def test_reject_large_or_unsupported_evidence(tmp_path):
    for name, content in [("big.md", b"x" * 64001), ("data.zip", b"zip")]:
        path = tmp_path / name
        path.write_bytes(content)
        with pytest.raises(ValueError):
            codex_proposal.read_evidence([path])


@pytest.mark.skipif(os.getenv("OV_CODEX_LIVE_TEST") != "1", reason="requires a logged-in Codex CLI")
@pytest.mark.asyncio
async def test_live_game_decision(tmp_path, monkeypatch):
    loop = game_loop(tmp_path, monkeypatch)
    _, proposal = await loop._call_llm(MESSAGES)
    assert any(event.model_dump() == DECISION for event in proposal.events)
