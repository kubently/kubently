from .alertmanager import create_router  # noqa: F401
from .fleet_report import create_router as create_fleet_report_router  # noqa: F401
from .scheduled_checks import create_router as create_scheduled_checks_router  # noqa: F401
from .verify_deployment import create_router as create_verify_deployment_router  # noqa: F401
