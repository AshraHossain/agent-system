from typing import TypedDict, List

class AgentState(TypedDict):
    query: str
    steps: List[str]
    result: str
