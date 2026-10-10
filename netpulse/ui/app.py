"""Streamlit UI for NetPulse AI.

    uv run streamlit run netpulse/ui/app.py

Talks only to the NetPulse API over HTTP (``netpulse.ui.client``). Reviewer
identity and role come from the bearer token the API resolves, never from
anything typed here. Free text from operators or retrieved documents is shown
with ``st.text`` / ``st.code`` so it is never interpreted as markdown or HTML.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

import streamlit as st

from netpulse.ui.client import ApiClient, ApiError, is_settled

SYNTHETIC_BANNER = (
    "SYNTHETIC DATA: every result below comes from a simulated network. "
    "Nothing here was observed on a real network, and no action is ever executed."
)
POLL_SECONDS = 2


def _client() -> ApiClient | None:
    url = st.session_state.get("api_url", "")
    token = st.session_state.get("api_token", "")
    if not url:
        return None
    return ApiClient(url, token)


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except ApiError as exc:
        st.error(f"API error ({exc.status_code}): {exc.detail}")
        return None


def _sidebar() -> None:
    if "_new_incident_id" in st.session_state:
        st.session_state["incident_id"] = st.session_state.pop("_new_incident_id")
    st.sidebar.header("Connection")
    st.sidebar.text_input("API URL", key="api_url", value=os.environ.get("NETPULSE_API_URL", "http://localhost:8000"))
    st.sidebar.text_input("API token", key="api_token", type="password", value=os.environ.get("NETPULSE_UI_TOKEN", ""))
    client = _client()
    if client and st.sidebar.button("Check connection"):
        health = _call(client.health)
        if health:
            durable = "durable" if health["durable"] else "NOT durable (in-memory checkpoints)"
            st.sidebar.success(f"API ok, checkpoints {durable}")
    st.sidebar.divider()
    st.sidebar.text_input("Incident ID", key="incident_id")


def _submit_tab(client: ApiClient) -> None:
    with st.form("submit"):
        title = st.text_input("Title", max_chars=200)
        description = st.text_area("Description (untrusted free text)", max_chars=4000)
        dataset_id = st.text_input("Dataset ID", value="case-04")
        c1, c2 = st.columns(2)
        start = c1.text_input("Window start (ISO 8601, UTC)", value="2026-03-08T08:00:00+00:00")
        end = c2.text_input("Window end (ISO 8601, UTC)", value="2026-03-08T13:55:00+00:00")
        entities = st.text_input("Suspected entities (comma separated)")
        severity = st.selectbox("Severity hint", ["", "low", "medium", "high", "critical"])
        submitted = st.form_submit_button("Start investigation")
    if not submitted:
        return
    submission = {
        "title": title,
        "description": description,
        "dataset_id": dataset_id,
        "window_start": start,
        "window_end": end,
        "suspected_entities": [e.strip() for e in entities.split(",") if e.strip()],
        "severity_hint": severity or None,
    }
    accepted = _call(client.submit_incident, submission)
    if accepted:
        st.session_state["_new_incident_id"] = accepted["incident_id"]
        st.rerun()


@st.fragment(run_every=POLL_SECONDS)
def _status_fragment(incident_id: str) -> None:
    client = _client()
    status = _call(client.status, incident_id) if client else None
    if status is None:
        return
    c1, c2, c3 = st.columns(3)
    c1.metric("Job", status["job_state"] or "idle")
    c2.metric("Workflow", status["status"] or "pending")
    c3.metric("Approval", status["approval_status"] or "n/a")
    if status.get("error"):
        st.error(status["error"])
    st.caption(f"Next nodes: {', '.join(status['next_nodes']) or 'none'} · polled {datetime.now(UTC):%H:%M:%S}Z")
    if is_settled(status):
        st.info("Run is settled; polling continues cheaply. Use Results, Evidence or Review.")


def _status_tab(client: ApiClient, incident_id: str) -> None:
    _status_fragment(incident_id)
    if st.button("Resume stalled run"):
        if _call(client.resume, incident_id):
            st.success("Resume queued.")


def _results_tab(client: ApiClient, incident_id: str) -> None:
    results = _call(client.results, incident_id)
    if not results:
        return
    report = results.get("final_report")
    if report:
        st.subheader(f"Outcome: {report['outcome']}")
        st.text(report["summary"])
        for limitation in report.get("limitations", []):
            st.caption(limitation)
    st.subheader("Hypotheses")
    ranking = {r["hypothesis_id"]: r for r in (results.get("confidence_assessment") or {}).get("ranking", [])}
    for hyp in results["hypotheses"]:
        rank = ranking.get(hyp["hypothesis_id"])
        label = f"{hyp['hypothesis_id']} · {hyp['cause_category']}"
        if rank:
            label += f" · rank {rank['rank']} · confidence {rank['confidence']}"
        with st.expander(label):
            st.text(hyp["description"])
            st.write("Supporting evidence:", ", ".join(hyp["supporting_evidence"]) or "none")
            st.write("Contradicting evidence:", ", ".join(hyp["contradicting_evidence"]) or "none")
            st.write("Missing evidence:", hyp["missing_evidence"] or "none")
            st.write("Validation steps:", hyp["validation_steps"] or "none")
            st.caption(f"generated by: {hyp['generated_by']}")
    st.subheader("Proposed actions (never executed)")
    decisions = {d.get("action_id"): d for d in results["policy_decisions"]}
    for action in results["recommended_actions"]:
        decision = decisions.get(action["action_id"], {})
        st.markdown(f"**{action['action_id']}** `{action['catalog_id']}` → policy: `{decision.get('outcome', 'n/a')}`")
        st.text(action["description"])
    with st.expander("Node trace"):
        st.dataframe(results["node_trace"], use_container_width=True)


def _evidence_tab(client: ApiClient, incident_id: str) -> None:
    data = _call(client.evidence, incident_id)
    if not data:
        return
    st.dataframe(
        [
            {
                "id": e["evidence_id"],
                "source": e["source"],
                "provenance": e.get("provenance"),
                "summary": e["summary"],
                "entities": ", ".join(e.get("entity_ids", [])),
            }
            for e in data["evidence"]
        ],
        use_container_width=True,
    )
    with st.expander("Telemetry window"):
        st.json(data["telemetry_window"] or {})
    with st.expander("Topology evidence"):
        st.json(data["topology_evidence"] or {})


def _review_tab(client: ApiClient, incident_id: str) -> None:
    status = _call(client.status, incident_id)
    if not status:
        return
    pending = status.get("pending_review")
    if not pending:
        st.info("This incident is not awaiting a reviewer decision.")
        return
    st.warning(f"Decision required: {pending['required_role']} role or higher.")
    st.text(pending["summary"])
    for reason in pending.get("escalation_reasons", []):
        st.caption(f"Escalation: {reason}")
    item_ids = []
    for item in pending["pending_actions"]:
        action, decision = item["action"], item["decision"]
        item_ids.append(action["action_id"])
        st.markdown(f"**{action['action_id']}** `{action['catalog_id']}` · policy `{decision['outcome']}`")
        st.text(action["description"])
    with st.form("review"):
        choice = st.radio("Decision", pending["allowed_choices"])
        selected = st.multiselect("Approve only these actions (empty = all)", item_ids)
        comment = st.text_area("Comment", max_chars=2000)
        if st.form_submit_button("Submit decision"):
            accepted = _call(client.decide, incident_id, choice, comment, selected or None)
            if accepted:
                st.success("Decision submitted. Check the Status tab.")


def main() -> None:
    st.set_page_config(page_title="NetPulse AI", layout="wide")
    st.title("NetPulse AI")
    st.warning(SYNTHETIC_BANNER)
    _sidebar()
    client = _client()
    if client is None:
        st.stop()
    incident_id = st.session_state.get("incident_id", "").strip()
    submit, status, results, evidence, review = st.tabs(["Submit", "Status", "Results", "Evidence", "Review"])
    with submit:
        _submit_tab(client)
    for tab, render in (
        (status, _status_tab),
        (results, _results_tab),
        (evidence, _evidence_tab),
        (review, _review_tab),
    ):
        with tab:
            if incident_id:
                render(client, incident_id)
            else:
                st.info("Enter or submit an incident ID.")


main()
