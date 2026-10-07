from typing import TypedDict, List

class AgentState(TypedDict):
    query: str
    steps: List[str]
    tool_results: List[str]
    errors: List[str]
    attempts: int
    result: str
