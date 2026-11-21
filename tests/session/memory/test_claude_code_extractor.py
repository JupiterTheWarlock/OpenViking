import io
import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from PIL import Image, ImageDraw
from pydantic import BaseModel

from openviking.session import compressor_v3
from openviking.session.memory import claude_code_proposal
from openviking.session.memory.claude_code_proposal import result_proposal
from openviking.session.memory.claude_review import ClaudeReview
from openviking_cli.utils.config.memory_config import MemoryConfig


class LayoutDecision(BaseModel):
    screenshot: str
    observed_columns: int
    adopted_columns: int
    requirement: str


class LayoutOperations(BaseModel):
    events: list[LayoutDecision]


def transcript(events):
    return io.BytesIO(b"\n".join(json.dumps(event).encode() for event in events))


def test_only_successful_terminal_event_can_submit_a_read():
    read = {"action": "read", "name": "read", "payload": '{"page_id":"p1"}'}
    result = {"type": "result", "subtype": "success", "is_error": False, "structured_output": read}
    tools = [{"function": {"name": "read"}}]
    events = [{"type": "assistant", "message": "irrelevant"}, result]
    assert result_proposal(transcript(events), tools, 0) == ("read", {"page_id": "p1"})
    with pytest.raises(ValueError, match="unavailable"):
        result_proposal(transcript(events), [], 0)
    with pytest.raises(RuntimeError):
        result_proposal(transcript([dict(result, is_error=True)]), tools, 0)
    with pytest.raises(RuntimeError):
        result_proposal(transcript(events), tools, 1)
    with pytest.raises(RuntimeError):
        result_proposal(transcript(events[:1]), tools, 0)


def review_loop(tmp_path, monkeypatch, session, review_dir=None, archive=None, fs=None):
    screenshot = tmp_path / "pack-trial.png"
    inventory = Image.new("RGB", (320, 170), "#222225")
    drawing = ImageDraw.Draw(inventory)
    for row in range(2):
        for column in range(4):
            left, top = 12 + 76 * column, 12 + 75 * row
            drawing.rectangle([left, top, left + 63, top + 60], fill="#DA7756")
    inventory.save(screenshot)
    review = tmp_path / "approved.md"
    review.write_text(
        "For UI-17 the adopted mobile backpack layout is three columns.", encoding="utf-8"
    )
    config = MemoryConfig(
        session_extractor="claude_code",
        claude_code_review_dir=review_dir,
        claude_code_model=os.getenv("OV_CLAUDE_TEST_MODEL"),
        claude_code_project_evidence=[
            {
                "account_id": "studio",
                "user_id": "designer",
                "session_id": "review",
                "files": [review, screenshot],
            }
        ],
    )
    monkeypatch.setattr(
        compressor_v3, "get_openviking_config", lambda: SimpleNamespace(memory=config)
    )
    monkeypatch.setattr(compressor_v3, "get_viking_fs", lambda: fs or object())
    loop = compressor_v3.SessionCompressorV3(vikingdb=None)._get_or_create_react(
        ctx=SimpleNamespace(account_id="studio", user=SimpleNamespace(user_id="designer")),
        session_id=session,
        archive_uri=archive,
        context_provider=object(),
        vlm_config=SimpleNamespace(model="test"),
    )
    loop._operations_model = LayoutOperations
    return loop


def test_project_files_belong_to_the_review_session(tmp_path, monkeypatch):
    assert review_loop(tmp_path, monkeypatch, "another-review").files == []


@pytest.mark.asyncio
async def test_review_receipt_covers_each_call_and_uses_scoped_archive(tmp_path, monkeypatch):
    fs = SimpleNamespace(write_file=AsyncMock())
    archive = "viking://session/review/archive/001"
    loop = review_loop(tmp_path, monkeypatch, "review", tmp_path / "audits", archive, fs)
    runner = AsyncMock(side_effect=[("", {"events": []}), RuntimeError("test failure")])
    monkeypatch.setattr(claude_code_proposal, "run_claude", runner)
    await loop._call_llm([])
    with pytest.raises(RuntimeError, match="test failure"):
        await loop._call_llm([])
    assert fs.write_file.await_count == 2
    records = []
    for run, write in zip(runner.await_args_list, fs.write_file.await_args_list, strict=True):
        args, directory, *_ = run.args
        record = json.loads(write.kwargs["content"])
        identifier = record["claude_session_id"]
        assert args[args.index("--session-id") + 1] == identifier
        assert "--no-session-persistence" not in args
        assert record["memory_diff_uri"] == f"{archive}/memory_diff.json"
        filename = f"claude_review_{identifier}.json"
        assert write.kwargs["uri"] == f"{archive}/{filename}"
        assert write.kwargs["ctx"].account_id == "studio"
        assert json.loads((directory / filename).read_text(encoding="utf-8")) == record
        records.append(record)
    assert records[0]["claude_session_id"] != records[1]["claude_session_id"]
    assert [record["status"] for record in records] == ["proposed", "failed"]
    assert records[0]["payload"] == {"events": []}
    assert records[0]["images"] == ["pack-trial.png"]
    assert "payload" not in records[1]


@pytest.mark.asyncio
async def test_review_disabled_uses_ephemeral_cli(tmp_path, monkeypatch):
    loop = review_loop(tmp_path, monkeypatch, "review")
    runner = AsyncMock(return_value=("", {"events": []}))
    monkeypatch.setattr(claude_code_proposal, "run_claude", runner)
    await loop._call_llm([])
    assert "--no-session-persistence" in runner.await_args.args[0]
    assert "--session-id" not in runner.await_args.args[0]


def test_shared_source_id_scoped_to_owner(tmp_path):
    def folder(account, user):
        ctx = SimpleNamespace(account_id=account, user=SimpleNamespace(user_id=user))
        return ClaudeReview.for_session(tmp_path, ctx, "../../game").directory

    folders = {folder("studio", "artist"), folder("private", "artist"), folder("studio", "tester")}
    assert len(folders) == 3 and all(directory.parent == tmp_path for directory in folders)


@pytest.mark.skipif(
    os.getenv("OV_CLAUDE_LIVE_TEST") != "1", reason="requires an authenticated Claude Code CLI"
)
@pytest.mark.asyncio
async def test_live_image_and_decision(tmp_path, monkeypatch):
    loop = review_loop(tmp_path, monkeypatch, "review", os.getenv("OV_CLAUDE_TEST_REVIEW_DIR"))
    messages = [
        {
            "role": "system",
            "content": "Extract the layout decision. Output contract: "
            + json.dumps(LayoutOperations.model_json_schema()),
        },
        {
            "role": "user",
            "content": "Count the slot columns actually visible in the attached trial screenshot. Record that value together with the adopted column count and requirement ID from the approved project note.",
        },
    ]
    _, proposal = await loop._call_llm(messages)
    assert proposal.events == [
        LayoutDecision(
            screenshot="pack-trial.png", observed_columns=4, adopted_columns=3, requirement="UI-17"
        )
    ]
