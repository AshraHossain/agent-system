import pytest

from opspilot.core.policy import check_step


@pytest.mark.parametrize(
    "step",
    [
        "Show CRC/FCS and input error counters on both ends of lnk-l3-s2",
        "Check optic digital diagnostics for leaf-3:et50",
        "Compare ECMP load distribution between uplinks",
        "Escalate to network on-call for a change-approved optic replacement",
        "Identify top talkers on lnk-l1-s1 from flow telemetry",
        "Review QoS queue drop counters",
    ],
)
def test_read_only_steps_allowed(step):
    assert check_step(step).allowed


@pytest.mark.parametrize(
    "step",
    [
        "Reboot the leaf switch",
        "Show counters then clear counters on leaf-1",
        "Check the link and shut down interface et50",
        "Run `reload` on spine-2",
        "Apply the QoS change to spine-1",
        "Escalate and replace the optic immediately",
        "Configure ECMP weights on leaf-1",
        "Check status && rm -rf /",
        "",
    ],
)
def test_mutating_or_unsafe_steps_rejected(step):
    assert not check_step(step).allowed
