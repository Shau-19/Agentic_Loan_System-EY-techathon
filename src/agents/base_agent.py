
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict
from dotenv import load_dotenv
import os
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))


@dataclass
class AgentMessage:
    sender: str = ""
    recipient: str = ""
    content: Dict[str, Any] = None
    timestamp: datetime = datetime.now()

class BaseAgent:
    def __init__(self, agent_id: str, agent_type: str = "generic"):
        self.agent_id = agent_id
        self.agent_type = agent_type

    # override in agents
    async def handle(self, message: AgentMessage):
        raise NotImplementedError