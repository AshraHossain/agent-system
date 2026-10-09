---
{"runbook_id": "RB-007", "title": "Correlating incidents with configuration changes", "categories": ["configuration_change"], "entity_types": ["node"], "version": 3, "last_reviewed": "2025-10-27", "provenance": "synthetic"}
---
# Correlating incidents with configuration changes

## Guidance
A change shortly before symptom onset is a strong lead, but timing alone does not prove it caused the
symptoms. Confirm a mechanism (for example, CPU punt or ACL drops) before you propose a rollback.

## Diagnostics
1. `review_recent_config_changes` on the devices in the blast radius.
2. `check_device_process_table` if the change could affect forwarding.

## Remediation (requires approval)
- `rollback_config_change`. This is reversible and needs a senior operator.
