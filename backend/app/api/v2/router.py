from fastapi import APIRouter

from app.api.adapters.system_status import serialize_v2
from app.api.agent_routes import v2_router as agent_router
from app.api.ai_operations_routes import v2_router as ai_operations_router
from app.api.auth_routes import router as auth_router
from app.api.conversation_routes import v2_router as conversation_router
from app.api.governance_routes import v2_router as governance_router
from app.api.integration_capability_routes import v2_router as integration_router
from app.api.jobs_routes import v2_router as jobs_router
from app.api.organization_routes import v2_router as organization_router
from app.api.product_ops_routes import v2_router as product_ops_router
from app.api.realtime_routes import v2_router as realtime_router
from app.api.runtime_result_routes import v2_router as runtime_result_router
from app.api.template_routes import v2_router as template_router
from app.api.workforce_routes import v2_router as workforce_router
from app.application.queries.system_status import get_system_status

router = APIRouter()
router.include_router(ai_operations_router)
router.include_router(conversation_router)
router.include_router(workforce_router)
router.include_router(jobs_router)
router.include_router(template_router)
router.include_router(integration_router)
router.include_router(governance_router)
router.include_router(runtime_result_router)
router.include_router(product_ops_router)
router.include_router(realtime_router)
router.include_router(agent_router)
router.include_router(auth_router)
router.include_router(organization_router)


@router.get("/system/status", tags=["system"])
async def system_status() -> dict[str, object]:
    return serialize_v2(get_system_status())
