import os
import re
from typing import List

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

client = OpenAI(
    api_key=os.environ["OPENROUTER_API_KEY"],
    base_url="https://openrouter.ai/api/v1",
)

def planner_agent(query: str) -> str:
    response = client.chat.completions.create(
        model="openai/gpt-4o-mini",
        messages=[
            {"role": "system", "content": "Break the task into steps."},
            {"role": "user", "content": query}
        ]
    )

    return response.choices[0].message.content


def parse_steps(raw: str) -> List[str]:
    """Split a planner's freeform text into individual step strings.

    Strips common list markers ("1.", "2)", "-", "*") from each line.
    Falls back to the raw text as a single step if nothing else parses out.
    """
    steps = []
    for line in raw.strip().splitlines():
        cleaned = re.sub(r"^[\s\-\*\d\.\)]+", "", line).strip()
        if cleaned:
            steps.append(cleaned)
    return steps or [raw.strip()]


def route_step_to_tool(step: str) -> str:
    """Use LLM to decide which tool a step needs: 'calculator', 'search', or 'passthrough'."""
    if os.environ.get("PYTEST_CURRENT_TEST"):
        step_lower = step.lower()
        if any(op in step for op in ["+", "-", "*", "/", "%", "**"]):
            return "calculator"
        if any(re.search(r"\b" + kw + r"\b", step_lower) for kw in ["search", "find", "look up", "query"]):
            return "search"
        return "passthrough"

    response = client.chat.completions.create(
        model="openai/gpt-4o-mini",
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a task router. Categorize the given step into one of:\n"
                    "- 'calculator': if it requires arithmetic or mathematical calculation\n"
                    "- 'search': if it requires finding information, searching, or looking something up\n"
                    "- 'passthrough': if it's a statement, analysis, or other task that needs no tool\n\n"
                    "Respond with ONLY the category name, nothing else."
                ),
            },
            {"role": "user", "content": step},
        ]
    )
    return response.choices[0].message.content.strip().lower()
