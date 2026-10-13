# ADR-0008: ADK serving instead of a custom FastAPI app

**Status:** Accepted

`adk web` (dev UI) and `adk api_server` (REST) already serve the agent from
`adk_apps/`. A custom FastAPI layer would duplicate session handling without
adding value now. Revisit if we need auth, a typed `/investigations` resource
API, or multi-tenant routing. The `opspilot` CLI covers reproducible demos.

**Update:** [ADR-0010](0010-read-only-run-lookup.md) adds one read-only route on top of
ADK's server; session handling is still ADK's.
[ADR-0012](0012-bearer-token-authentication.md) adds optional bearer-token authentication to
`opspilot serve`.
