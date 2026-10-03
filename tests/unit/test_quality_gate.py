from surface_intel.reporting.export import compute_integrity_status


def test_quality_gate_never_marks_incomplete_work_complete():
    assert compute_integrity_status("failed", gaps=[], unknown_actions=0, failed_actions=0, pending_actions=0) == "failed"
    assert compute_integrity_status("paused", gaps=[], unknown_actions=0, failed_actions=0, pending_actions=0) == "partial"
    assert compute_integrity_status("correlating", gaps=[], unknown_actions=1, failed_actions=0, pending_actions=0) == "unknown"
    assert compute_integrity_status("correlating", gaps=[{"reason": "not_authorized"}], unknown_actions=0, failed_actions=0, pending_actions=0) == "partial"
    assert compute_integrity_status("correlating", gaps=[], unknown_actions=0, failed_actions=0, pending_actions=0) == "complete"
