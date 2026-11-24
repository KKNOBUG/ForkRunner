from typing import Literal

from pydantic import Field

from applications.autotest.schemas.autotest_step_schema import AutoTestHttpDebugRequest
from enums import AutoTestStepType


class AutoTestOpenApiDebugRequest(AutoTestHttpDebugRequest):
    step_type: Literal[AutoTestStepType.OPEN_API] = AutoTestStepType.OPEN_API
    case_id: int | None = Field(None, ge=1)
    step_code: str | None = Field(None, max_length=128)
    dataset_name: str | None = Field(None, max_length=255)
