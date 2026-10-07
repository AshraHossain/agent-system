from fastapi import FastAPI
from app.graph import app_graph
from memory.store import save_run

app = FastAPI()

@app.get("/run")
def run(query: str):
    result = app_graph.invoke({
        "query": query,
        "steps": [],
        "tool_results": [],
        "errors": [],
        "attempts": 0,
        "result": ""
    })

    run_id = save_run(result)

    return {"run_id": run_id, **result}
