"""HTTP client for the NetPulse API. The only way the UI reaches the service."""

from __future__ import annotations

from typing import Any

import httpx


class ApiError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(f"{status_code}: {detail}")
        self.status_code = status_code
        self.detail = detail


class ApiClient:
    def __init__(self, base_url: str, token: str, http: httpx.Client | None = None, timeout: float = 15.0) -> None:
        self._http = http or httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout)
        self._headers = {"Authorization": f"Bearer {token}"} if token else {}

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = self._http.request(method, path, headers=self._headers, **kwargs)
        except httpx.HTTPError as exc:
            raise ApiError(0, f"cannot reach the NetPulse API: {exc}") from exc
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail", response.text)
            except ValueError:
                detail = response.text
            if not isinstance(detail, str):
                detail = str(detail)
            raise ApiError(response.status_code, detail)
        return response.json()

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/health")

    def submit_incident(self, submission: dict[str, Any], incident_id: str | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {"submission": submission}
        if incident_id:
            body["incident_id"] = incident_id
        return self._request("POST", "/incidents", json=body)

    def status(self, incident_id: str) -> dict[str, Any]:
        return self._request("GET", f"/incidents/{incident_id}")

    def results(self, incident_id: str) -> dict[str, Any]:
        return self._request("GET", f"/incidents/{incident_id}/results")

    def evidence(self, incident_id: str) -> dict[str, Any]:
        return self._request("GET", f"/incidents/{incident_id}/evidence")

    def decide(
        self,
        incident_id: str,
        choice: str,
        comment: str = "",
        approved_action_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        body = {"choice": choice, "comment": comment, "approved_action_ids": approved_action_ids}
        return self._request("POST", f"/incidents/{incident_id}/approval", json=body)

    def resume(self, incident_id: str) -> dict[str, Any]:
        return self._request("POST", f"/incidents/{incident_id}/resume")

    def eval_reports(self) -> dict[str, Any]:
        return self._request("GET", "/eval/reports")


def is_settled(status: dict[str, Any]) -> bool:
    """True when polling can stop: no job in flight and the run is paused or finished."""
    if status.get("job_state") in {"queued", "running"}:
        return False
    return status.get("status") is not None or status.get("job_state") == "error"
