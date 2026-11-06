import sys

from backend.core.config import get_settings
from backend.core.worker_health import worker_heartbeat_is_fresh


def main():
    settings = get_settings()
    path = settings.automation_scheduler_heartbeat_path
    healthy = (
        settings.automations_enabled
        and path is not None
        and worker_heartbeat_is_fresh(path, "automations", 15)
    )
    return 0 if healthy else 1


if __name__ == "__main__":
    sys.exit(main())
