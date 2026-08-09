from openai import OpenAI

client = OpenAI()

def planner_agent(query: str) -> str:
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "Break the task into steps."},
            {"role": "user", "content": query}
        ]
    )

    return response.choices[0].message.content
