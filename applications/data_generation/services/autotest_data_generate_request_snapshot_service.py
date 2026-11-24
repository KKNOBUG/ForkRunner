# -*- coding: utf-8 -*-

"""构建数据生成任务使用的请求报文快照。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, Mapping

import orjson

from applications.data_generation.services.autotest_data_generate_service import (
    flatten_json_body_fields,
    flatten_xml_body_fields,
)
from applications.autotest.services.autotest_data_source_service import DataSourceService
from core.exceptions import ParameterException
from enums import AutoTestCaseType, AutoTestReqArgsType, AutoTestStepType

if TYPE_CHECKING:
    from applications.autotest.models.autotest_case_model import AutoTestCaseModel
    from applications.autotest.models.autotest_step_model import AutoTestStepModel
    from applications.data_generation.dependencies import DataGenerationApiServices


async def resolve_generation_request_step(
    services: DataGenerationApiServices,
    case: AutoTestCaseModel,
    step: AutoTestStepModel,
) -> AutoTestStepModel:
    """校验生成任务的归属步骤，并定位用于读取报文的请求步骤。"""
    DataSourceService.ensure_request_step(step, case=case)
    DataSourceService.ensure_case_allows_data_source(case)
    if step.step_type != AutoTestStepType.QUOTE_PUBLIC_API:
        return step

    quoted_case = None
    if step.quote_case_id:
        quoted_case = await services.case_curd.get_by_id(
            case_id=step.quote_case_id,
            case_type=AutoTestCaseType.PUBLIC_API,
            state__not=1,
        )
    if quoted_case is None:
        raise ParameterException(message="引用的公共接口不存在或已失效")

    request_step = await DataSourceService.resolve_datagram_source_step(services, step)
    if request_step.step_type not in DataSourceService.REQUEST_STEP_TYPES:
        raise ParameterException(message="引用的公共接口没有可用的HTTP/TCP请求步骤")
    return request_step


def build_request_body_snapshot(step: Any) -> Dict[str, Any]:
    """读取步骤当前 BODY，并转换为字段路径到当前值的有序映射。"""

    raw_request_type = getattr(step, "request_args_type", None)
    request_type = str(
        getattr(raw_request_type, "value", raw_request_type) or ""
    ).strip()
    if request_type == AutoTestReqArgsType.JSON.value:
        payload = getattr(step, "request_body", None)
        if isinstance(payload, str):
            try:
                payload = orjson.loads(payload)
            except orjson.JSONDecodeError as exc:
                raise ParameterException(
                    message="当前步骤的JSON请求报文格式不正确"
                ) from exc
        if not isinstance(payload, (Mapping, list)):
            raise ParameterException(
                message="当前步骤没有可用于生成测试数据的JSON请求报文"
            )
        snapshot = flatten_json_body_fields(payload)
    elif request_type == AutoTestReqArgsType.XML.value:
        snapshot = flatten_xml_body_fields(
            getattr(step, "request_text", None) or ""
        )
    else:
        raise ParameterException(message="测试数据生成目前只支持JSON或XML请求报文")

    if not snapshot:
        raise ParameterException(
            message="当前步骤的请求报文中没有可生成测试数据的BODY字段"
        )
    return snapshot


def build_intelligent_request_snapshot(step: Any) -> Dict[str, Dict[str, Any]]:
    """按数据编辑分区采集当前请求步骤的HEAD与BODY字段。

    :param step: 包含请求头及JSON/XML请求体的测试步骤。
    :return: 同一快照中的HEAD、BODY有序字段映射；允许HEAD为空。
    :raises ParameterException: BODY缺失、格式错误或字段无法展平。
    """

    body = build_request_body_snapshot(step)
    head = DataSourceService.collect_step_report_original(step)["HEAD"]
    return {"HEAD": head, "BODY": body}
