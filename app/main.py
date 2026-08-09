from fastapi import FastAPI
from app.graph import app_graph

app = FastAPI()

@app.get("/run")
def run(query: str):
    result = app_graph.invoke({
        "query": query,
        "steps": [],
        "result": ""
    })

    return result
