from surface_intel.reporting.export import compute_integrity_status


def test_quality_gate_never_marks_incomplete_work_complete():
    assert compute_integrity_status("failed", gaps=[], unknown_actions=0, failed_actions=0, pending_actions=0, total_actions=3) == "failed"
    assert compute_integrity_status("paused", gaps=[], unknown_actions=0, failed_actions=0, pending_actions=0, total_actions=2) == "partial"
    assert compute_integrity_status("correlating", gaps=[], unknown_actions=1, failed_actions=0, pending_actions=0, total_actions=3) == "unknown"
    assert compute_integrity_status("correlating", gaps=[{"reason": "not_authorized"}], unknown_actions=0, failed_actions=0, pending_actions=0, total_actions=1) == "partial"
    assert compute_integrity_status("correlating", gaps=[], unknown_actions=0, failed_actions=0, pending_actions=0, total_actions=1) == "complete"


def test_zero_action_rows_do_not_force_unknown():
    """D10：真实 recon 不写 action 行——action 维度不得把任务拖成 unknown。

    缺口（gaps）仍诚实降级 partial；有 action 行且含 unknown 终态时
    依旧 unknown（诚实教义不受 total 保护）。
    """
    assert compute_integrity_status("correlating", gaps=[], unknown_actions=0, failed_actions=0, pending_actions=0, total_actions=0) == "complete"
    assert compute_integrity_status("correlating", gaps=[{"reason": "x"}], unknown_actions=0, failed_actions=0, pending_actions=0, total_actions=0) == "partial"
    assert compute_integrity_status("correlating", gaps=[], unknown_actions=1, failed_actions=0, pending_actions=0, total_actions=3) == "unknown"
