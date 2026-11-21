import io
import json
import os
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw
from pydantic import BaseModel

from openviking.session import compressor_v3
from openviking.session.memory.claude_code_proposal import result_proposal
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


def review_loop(tmp_path, monkeypatch, session):
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
    monkeypatch.setattr(compressor_v3, "get_viking_fs", lambda: object())
    loop = compressor_v3.SessionCompressorV3(vikingdb=None)._get_or_create_react(
        ctx=SimpleNamespace(account_id="studio", user=SimpleNamespace(user_id="designer")),
        session_id=session,
        context_provider=object(),
        vlm_config=SimpleNamespace(model="test"),
    )
    loop._operations_model = LayoutOperations
    return loop


def test_project_files_belong_to_the_review_session(tmp_path, monkeypatch):
    assert review_loop(tmp_path, monkeypatch, "another-review").files == []


@pytest.mark.skipif(
    os.getenv("OV_CLAUDE_LIVE_TEST") != "1", reason="requires an authenticated Claude Code CLI"
)
@pytest.mark.asyncio
async def test_live_image_and_decision(tmp_path, monkeypatch):
    loop = review_loop(tmp_path, monkeypatch, "review")
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
