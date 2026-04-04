STEP_NAMES = [
    "clone",
    "install",
    "lightweight_security_scan",
    "test",
    "deep_security_scan",
    "build",
]

STEP_STATUSES = {"pending", "running", "success", "failed", "skipped"}
PIPELINE_STATUSES = {"queued", "running", "success", "failed", "cancelled"}

RUNS_DIR_NAME = "runs"
WORKSPACE_DIR_NAME = "workspace"

RUNTIME_TYPE = "node"
