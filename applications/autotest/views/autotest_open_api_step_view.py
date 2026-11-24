import traceback

from fastapi import APIRouter

from applications.autotest.dependencies import AutoTestServices, DependAutoTestServices
from applications.autotest.schemas.autotest_open_api_step_schema import AutoTestOpenApiDebugRequest
from applications.autotest.services.autotest_open_api_debug_service import OpenApiStepDebugService
from applications.autotest.services.autotest_step_debug_service import StepDebugException
from configure import LOGGER
from core.exceptions import NoPermissionException, NotFoundException, ParameterException
from core.responses import FailureResponse, ForbiddenResponse, NotFoundResponse, ParameterResponse, SuccessResponse
from services import DataScope, DependTagScope


autotest_open_api_step = APIRouter()


@autotest_open_api_step.post("/openApi_debugging", summary="调试openapi加解密", description="调试openapi加解密步骤")
async def debug_open_api_request(
    debug_in: AutoTestOpenApiDebugRequest,
    services: AutoTestServices = DependAutoTestServices,
    scope: DataScope = DependTagScope,
):
    try:
        data = await OpenApiStepDebugService.debug_open_api(debug_in, services, scope)
        return SuccessResponse(message="openapi加解密调试完成", data=data)
    except NotFoundException as exc:
        return NotFoundResponse(message=str(exc.message))
    except ParameterException as exc:
        return ParameterResponse(message=str(exc.message))
    except NoPermissionException as exc:
        return ForbiddenResponse(message=str(exc.message))
    except StepDebugException as exc:
        return FailureResponse(message=exc.message, data=exc.data)
    except Exception:
        LOGGER.error(f"openapi加解密调试失败\n{traceback.format_exc()}")
        return FailureResponse(message="openapi加解密调试失败")
