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
