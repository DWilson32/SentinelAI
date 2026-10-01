from datetime import datetime
from typing import Literal

from pydantic import BaseModel

AgentStatus = Literal["queued", "running", "completed", "failed"]


class AgentRun(BaseModel):
    id: str
    incident_id: str
    agent_name: str
    status: AgentStatus
    input: dict
    output: dict
    created_at: datetime
    # Which investigation of the incident this step belongs to, counting from 1.
    investigation: int = 1

