"""Phase 4: detection regression guard on the v1 synthetic dataset.

The floors sit below the measured values (precision 0.94, recall 1.00) so
that real regressions fail without making the test flaky. Raising them
requires a deliberate change, not tuning detectors to the labels.
"""

from eval.detection_benchmark import run


def test_detection_precision_and_recall_floors():
    report = run()
    assert report["recall"] >= 0.95, report
    assert report["precision"] >= 0.90, report
    normal = [c for c in report["cases"] if c["scenario"] in {"normal_quiet", "normal_noisy"}]
    assert all(c["predicted"] == 0 for c in normal), normal
