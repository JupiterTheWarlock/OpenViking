"""Resolve external agent proposals through OpenViking's session extraction loop."""

import asyncio
from uuid import uuid4

from openviking.models.vlm.base import ToolCall
from openviking.session.memory.external_proposal import read_evidence
from openviking.session.memory.extract_loop import ExtractLoop


class ExternalExtractLoop(ExtractLoop):
    output_format_override = "json"

    def __init__(self, *, files, timeout, request_proposal, **kwargs):
        super().__init__(**kwargs)
        self.files = files
        self.timeout = timeout
        self.request_proposal = request_proposal
        self.evidence = None

    async def _call_llm(self, messages):
        tools = [] if self._disable_tools_for_iteration else (self._tool_schemas or [])
        if self.evidence is None:
            self.evidence = await asyncio.to_thread(read_evidence, self.files)
        documents, images = self.evidence
        name, payload = await self.request_proposal(
            messages, tools, self.timeout, documents, images
        )
        if name:
            return [ToolCall(id=uuid4().hex, name=name, arguments=payload)], None
        if payload.keys() - self._operations_model.model_fields.keys():
            raise ValueError("Unknown fields in external memory proposal")
        return None, self._operations_model.model_validate(payload)
