# plugins/

Reserved for SuperClaude Framework plugin extensions for this project (e.g.
custom agent tools, eval harnesses, or CI helpers packaged as plugins).

No plugins are defined yet. `agent-system` currently ships its own tool
functions directly under `tools/` (`calculator_tool.py`, `search_tool.py`)
rather than as installable plugins — see `PLANNING.md` for how those are
(not yet) wired into the LangGraph flow.
