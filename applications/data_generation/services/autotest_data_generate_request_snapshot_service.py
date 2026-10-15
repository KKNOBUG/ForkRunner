# -*- coding: utf-8 -*-

"""构建数据生成任务使用的请求报文快照。"""

from __future__ import annotations

from typing import Any, Dict, Mapping

import orjson

from applications.data_generation.services.autotest_data_generate_service import (
    flatten_json_body_fields,
    flatten_xml_body_fields,
)
from applications.autotest.services.autotest_data_source_service import (
    collect_step_report_original,
)
from core.exceptions import ParameterException
from enums import AutoTestReqArgsType


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
    head = collect_step_report_original(step)["HEAD"]
    return {"HEAD": head, "BODY": body}
