import os

os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")
os.environ.pop("TRAFFICFLOW_MODEL", None)
