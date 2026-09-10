# -*- coding: utf-8 -*-

'''
    数据生成功能的HTTP接口层
    接受前端请求，调用服务，投递celery任务，把结果转换成统一的HTYP响应
'''

from __future__ import annotations

import hashlib#计算上传文档的哈希值
import os
import mimetypes#根据文件名推断下载文件类型
import traceback#记录完整异常堆栈
from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional
from urllib.parse import quote#对下载文件名进行URL编码
from zoneinfo import ZoneInfo#统一时区

import orjson
from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from pydantic import ValidationError
from starlette.responses import StreamingResponse

from applications.data_generation.dependencies import (
    DataGenerationApiServices,
    get_data_generation_api_services,
)
from applications.data_generation.schemas.autotest_data_generate_schema import (
    AutoTestDataGenerateTaskCreate,
    AutoTestDataGenerateTaskSelect,
)
from applications.data_generation.services.autotest_data_generate_service import (
    TestDataGenerationError,
    flatten_json_body_fields,
    flatten_xml_body_fields,
)
from applications.data_generation.services.autotest_data_generate_record_service import (
    AutoTestDataGenerateRecordService,
    resolve_interface_document_path,
)
from applications.data_generation.services.autotest_data_generate_task_crud import AutoTestDataGenerateTaskCrud
from applications.autotest.services.autotest_data_source_service import (
    ensure_case_allows_data_source,
    ensure_request_step,
    resolve_case_and_step,
)
from applications.data_generation.services.autotest_interface_document_parser import (
    InterfaceDocumentParseError,
    MAX_DOCUMENT_SIZE,
    parse_interface_document,
)
from celery_scheduler.tasks.task_autotest_data_generate import dispatch_data_generate_task
from configure import LOGGER, PROJECT_CONFIG
from core.exceptions import NotFoundException, ParameterException
from core.responses import FailureResponse, NotFoundResponse, ParameterResponse, SuccessResponse
from enums import AutoTestDataGenerateStatus, AutoTestReqArgsType
from services import get_current_username
from services.file_transfer import FileTransfer

#数据生成模块的路由集合
autotest_data_generate = APIRouter()

_INTERFACE_TEMPLATE_FILES = {
    "esb": "ESB接口文档模板.xlsx",
    "project": "项目接口文档模板.xlsx",
}


def _request_body_snapshot(step: Any) -> Dict[str, Any]:
    """
        读取步骤当前BODY，并转换为数据生成规则使用的字段路径快照。
        step：当前用例/脚本中的一条具体步骤记录，具体的说就是一个AutoTestStepModel对象
        返回值：请求体展开后的字段路径->当前字段值的字典
    """
    raw_request_type = getattr(step, "request_args_type", None)
    request_type = str(getattr(raw_request_type, "value", raw_request_type) or "").strip()
    if request_type == AutoTestReqArgsType.JSON.value:
        payload = getattr(step, "request_body", None)
        if isinstance(payload, str):
            try:
                payload = orjson.loads(payload)
            except orjson.JSONDecodeError as exc:
                raise ParameterException(message="当前步骤的JSON请求报文格式不正确") from exc
        if not isinstance(payload, (Mapping, list)):
            raise ParameterException(message="当前步骤没有可用于生成测试数据的JSON请求报文")
        snapshot = flatten_json_body_fields(payload)
    elif request_type == AutoTestReqArgsType.XML.value:
        snapshot = flatten_xml_body_fields(getattr(step, "request_text", None) or "")
    else:
        raise ParameterException(message="测试数据生成目前只支持JSON或XML请求报文")
    if not snapshot:
        raise ParameterException(message="当前步骤的请求报文中没有可生成测试数据的BODY字段")
    return snapshot


def _generated_document_name(step_name: str, submitted_at: datetime) -> str:
    timestamp = submitted_at.astimezone(ZoneInfo("Asia/Shanghai")).strftime(
        "%Y%m%d%H%M%S"
    )
    return f"{str(step_name or '').strip()}-生成数据-{timestamp}.xlsx"


def _remove_file_safely(file_path: Optional[str]) -> None:
    if not file_path:
        return
    try:
        if os.path.isfile(file_path):
            os.remove(file_path)
    except OSError as exc:
        LOGGER.warning(f"清理数据生成接口文档失败, 文件={file_path}, 错误类型={type(exc).__name__}")


def _format_time(value: Optional[datetime]) -> Optional[str]:
    if not value:
        return None
    display_time = value.astimezone(ZoneInfo("Asia/Shanghai")) if value.tzinfo else value
    return display_time.strftime("%Y-%m-%d %H:%M:%S")


def _serialize_task(task: Any) -> Dict[str, Any]:
    """生成记录列表仅返回展示字段，避免暴露报文和文档解析快照。"""
    return {
        "task_id": task.id,
        "task_code": task.task_code,
        "generated_file_name": task.generated_file_name,
        "interface_file_name": task.interface_file_name,
        "task_status": task.task_status.value,
        "generated_count": task.generated_count,
        "created_time": _format_time(task.created_time),
        "finished_time": _format_time(task.finished_time),
        "error_message": task.error_message,
    }


def _download_response(file_path: str, file_name: str) -> StreamingResponse:
    if not os.path.isfile(file_path):
        raise NotFoundException(message=f"文件[{file_name}]不存在")
    content_type, _ = mimetypes.guess_type(file_name)
    return StreamingResponse(
        FileTransfer.iter_download_file_chunks(download_file=file_path, add_bom=False),
        media_type=content_type or "application/octet-stream",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(file_name)}"},
    )


def _resolve_interface_template(interface_style: str) -> tuple[str, str]:
    """
        根据接口样式找到对应的excel模版文件，并返回文件路径和文件名
    """
    file_name = _INTERFACE_TEMPLATE_FILES.get(str(interface_style or "").strip().lower())
    if not file_name:
        raise ParameterException(message="接口样式仅支持esb或project")

    template_root = os.path.realpath(os.path.join(PROJECT_CONFIG.OUTPUT_DIR, "template"))
    file_path = os.path.realpath(os.path.join(template_root, file_name))
    if os.path.commonpath((template_root, file_path)) != template_root:
        raise ParameterException(message="接口文档模板路径不合法")
    if not os.path.isfile(file_path):
        raise NotFoundException(
            message=f"{file_name}不存在，请将模板上传至output/template目录"
        )
    return file_path, file_name


@autotest_data_generate.post("/tasks", summary="创建测试数据生成任务")
async def create_task(
        case_id: int = Form(..., ge=1),
        step_id: int = Form(..., ge=1),
        step_code: str = Form(..., min_length=1, max_length=64),
        interface_style: str = Form(...),
        rule_codes: List[str] = Form(...),
        file: UploadFile = File(..., description="ESB或项目接口文档，仅支持xlsx"),
        services: DataGenerationApiServices = Depends(get_data_generation_api_services),
):
    """保存任务输入快照并投递Celery；解析失败也保留一条失败记录。"""
    submitted_at = datetime.now(ZoneInfo("Asia/Shanghai"))
    original_name = os.path.basename(str(file.filename or "").strip())
    if not original_name.lower().endswith(".xlsx"):
        return ParameterResponse(message="接口文档仅支持.xlsx文件")

    file_path: Optional[str] = None
    task = None
    try:
        case, step = await resolve_case_and_step(
            services,
            case_id=case_id,
            step_id=step_id,
            step_code=step_code,
        )
        ensure_request_step(step)
        ensure_case_allows_data_source(case)
        request_snapshot = _request_body_snapshot(step)

        content = await file.read(MAX_DOCUMENT_SIZE + 1)
        await file.seek(0)
        if len(content) > MAX_DOCUMENT_SIZE:
            return ParameterResponse(
                message=f"接口文档大小不能超过{MAX_DOCUMENT_SIZE // 1024 // 1024}MB"
            )
        # 先解析内存内容，避免无效文档进入异步队列；任务创建后仍会将解析异常记录为失败。
        parsed_document = None
        parse_error = None
        try:
            parsed_document = parse_interface_document(content, interface_style)
        except InterfaceDocumentParseError as exc:
            parse_error = exc

        destination = os.path.join(
            "autotest_data_generate",
            str(case_id),
            str(step.step_code),
        )
        ok, path_or_error = await FileTransfer.save_upload_file_chunks(
            upload_file=file,
            destination=destination,
            add_timestamp=True,
            check_filename=True,
            check_filetype=True,
            check_filesize=True,
            add_left_identifier=str(step.step_code),
            upload_file_size="tiny",
        )
        if not ok:
            return FailureResponse(message=f"接口文档上传失败: {path_or_error}")
        file_path = path_or_error
        storage_key = os.path.relpath(
            file_path,
            PROJECT_CONFIG.OUTPUT_UPLOAD_DIR,
        ).replace(os.sep, "/")

        task_in = AutoTestDataGenerateTaskCreate(
            case_id=case.id,
            step_id=step.id,
            step_code=step.step_code,
            interface_style=interface_style,
            rule_codes=rule_codes,
            request_snapshot=request_snapshot,
            interface_schema_snapshot=parsed_document,
            interface_file_name=original_name,
            interface_file_hash=hashlib.sha256(content).hexdigest(),
            interface_storage_key=storage_key,
            generated_file_name=_generated_document_name(step.step_name, submitted_at),
            created_user=get_current_username(),
        )
        task = await services.data_generate_task_curd.create_task(task_in)
        if parse_error is not None:
            task = await services.data_generate_task_curd.mark_failure(
                task.id,
                str(parse_error),
                task_summary={"success": False, "error_type": type(parse_error).__name__},
            )
            return SuccessResponse(
                message="任务创建成功，接口文档解析失败",
                data=_serialize_task(task),
                total=1,
            )

        try:
            await dispatch_data_generate_task(task.id)
        except Exception:
            # 投递函数已经将发布异常回写为失败，仍返回任务记录供前端展示。
            task = await services.data_generate_task_curd.get_by_id(
                task.id,
                on_error=True,
                state__not=1,
            )
            return SuccessResponse(
                message="任务创建成功，但异步任务投递失败",
                data=_serialize_task(task),
                total=1,
            )
        task = await services.data_generate_task_curd.get_by_id(
            task.id,
            on_error=True,
            state__not=1,
        )
        return SuccessResponse(message="任务已提交", data=_serialize_task(task), total=1)
    except (ValidationError, ParameterException, TestDataGenerationError) as exc:
        if task is None:
            _remove_file_safely(file_path)
        message = getattr(exc, "message", None) or str(exc)
        return ParameterResponse(message=message)
    except NotFoundException as exc:
        if task is None:
            _remove_file_safely(file_path)
        return NotFoundResponse(message=str(exc.message))
    except Exception as exc:
        if task is None:
            _remove_file_safely(file_path)
        LOGGER.error(f"创建数据生成任务失败, 错误类型={type(exc).__name__}\n{traceback.format_exc()}")
        return FailureResponse(message="创建数据生成任务失败")


@autotest_data_generate.get("/records", summary="查询数据生成记录")
async def list_records(
        step_code: str = Query(..., min_length=1, max_length=64),
):
    try:
        selector = AutoTestDataGenerateTaskSelect(
            step_code=step_code,
        )
        tasks = await AutoTestDataGenerateTaskCrud().select_tasks(selector)
        return SuccessResponse(
            message="查询成功",
            data=[_serialize_task(task) for task in tasks],
            total=len(tasks),
        )
    except ParameterException as exc:
        return ParameterResponse(message=str(exc.message))
    except Exception as exc:
        LOGGER.error(f"查询数据生成记录失败, 错误类型={type(exc).__name__}\n{traceback.format_exc()}")
        return FailureResponse(message="查询数据生成记录失败")


@autotest_data_generate.get("/templates/{interface_style}", summary="下载接口文档模板")
async def download_interface_template(interface_style: str):
    try:
        file_path, file_name = _resolve_interface_template(interface_style)
        return _download_response(file_path, file_name)
    except ParameterException as exc:
        return ParameterResponse(message=str(exc.message))
    except NotFoundException as exc:
        return NotFoundResponse(message=str(exc.message))
    except Exception as exc:
        LOGGER.error(f"下载接口文档模板失败, 错误类型={type(exc).__name__}\n{traceback.format_exc()}")
        return FailureResponse(message="下载接口文档模板失败")


@autotest_data_generate.get("/{task_id}/detail", summary="查询数据生成任务详情")
async def task_detail(task_id: int):
    try:
        detail = await AutoTestDataGenerateRecordService().detail(task_id)
        task = detail["task"]
        data = {
            **_serialize_task(task),
            "interface_style": task.interface_style,
            "rule_codes": task.rule_codes,
            "started_time": _format_time(task.started_time),
            "task_summary": detail["task_summary"],
        }
        return SuccessResponse(message="查询成功", data=data, total=1)
    except NotFoundException as exc:
        return NotFoundResponse(message=str(exc.message))
    except ParameterException as exc:
        return ParameterResponse(message=str(exc.message))
    except Exception as exc:
        LOGGER.error(f"查询数据生成任务详情失败, 错误类型={type(exc).__name__}\n{traceback.format_exc()}")
        return FailureResponse(message="查询数据生成任务详情失败")


@autotest_data_generate.get("/{task_id}/refresh", summary="刷新单条数据生成任务")
async def refresh_task(task_id: int):
    try:
        task = await AutoTestDataGenerateTaskCrud().get_by_id(task_id, on_error=True, state__not=1)
        return SuccessResponse(message="刷新成功", data=_serialize_task(task), total=1)
    except NotFoundException as exc:
        return NotFoundResponse(message=str(exc.message))
    except ParameterException as exc:
        return ParameterResponse(message=str(exc.message))
    except Exception as exc:
        LOGGER.error(f"刷新数据生成任务失败, 错误类型={type(exc).__name__}\n{traceback.format_exc()}")
        return FailureResponse(message="刷新数据生成任务失败")


@autotest_data_generate.post("/{task_id}/apply", summary="应用生成结果到数据编辑")
async def apply_task(
        task_id: int,
        services: DataGenerationApiServices = Depends(get_data_generation_api_services),
):
    try:
        instance = await AutoTestDataGenerateRecordService().apply(task_id, services)
        return SuccessResponse(
            message="应用成功，全部生成场景已追加到数据编辑",
            data={
                "data_source_id": instance.id,
                "case_id": instance.case_id,
                "step_id": instance.step_id,
                "step_code": instance.step_code,
                "dataset_names": instance.dataset_names,
                # 直接返回本次持久化后的矩阵，前端无需再发起一次可能与页面
                # 其他查询竞争的刷新请求；矩阵列/行顺序即应用后的最终顺序。
                "dataframe": instance.dataframe,
                "axis": instance.axis,
            },
            total=1,
        )
    except NotFoundException as exc:
        return NotFoundResponse(message=str(exc.message))
    except ParameterException as exc:
        return ParameterResponse(message=str(exc.message))
    except Exception as exc:
        LOGGER.error(f"应用数据生成任务失败, 错误类型={type(exc).__name__}\n{traceback.format_exc()}")
        return FailureResponse(message="应用数据生成任务失败")


@autotest_data_generate.delete("/{task_id}", summary="删除数据生成记录")
async def delete_task(task_id: int):
    try:
        crud = AutoTestDataGenerateTaskCrud()
        task = await crud.get_by_id(task_id, on_error=True, state__not=1)
        if task.task_status == AutoTestDataGenerateStatus.IN_PROGRESS:
            return ParameterResponse(message="进行中的数据生成任务不能删除")
        await crud.delete_task(task.id)
        return SuccessResponse(message="删除成功", data={"task_id": task.id}, total=1)
    except NotFoundException as exc:
        return NotFoundResponse(message=str(exc.message))
    except ParameterException as exc:
        return ParameterResponse(message=str(exc.message))
    except Exception as exc:
        LOGGER.error(f"删除数据生成任务失败, 错误类型={type(exc).__name__}\n{traceback.format_exc()}")
        return FailureResponse(message="删除数据生成任务失败")


@autotest_data_generate.get("/{task_id}/generated-document", summary="下载数据生成文档")
async def download_generated_document(task_id: int):
    try:
        service = AutoTestDataGenerateRecordService()
        exported = await service.ensure_generated_file(task_id)
        return _download_response(exported.file_path, exported.file_name)
    except NotFoundException as exc:
        return NotFoundResponse(message=str(exc.message))
    except ParameterException as exc:
        return ParameterResponse(message=str(exc.message))
    except Exception as exc:
        LOGGER.error(f"下载数据生成文档失败, 错误类型={type(exc).__name__}\n{traceback.format_exc()}")
        return FailureResponse(message="下载数据生成文档失败")


@autotest_data_generate.get("/{task_id}/interface-document", summary="下载接口文档")
async def download_interface_document(task_id: int):
    """
        根据数据生成任务ID，找到该任务对应的接口文档，并以附件形式流式返回给浏览器下载
    """
    try:
        task = await AutoTestDataGenerateTaskCrud().get_by_id(task_id, on_error=True, state__not=1)
        file_path = resolve_interface_document_path(task.interface_storage_key)
        return _download_response(file_path, task.interface_file_name)
    except NotFoundException as exc:
        return NotFoundResponse(message=str(exc.message))
    except ParameterException as exc:
        return ParameterResponse(message=str(exc.message))
    except Exception as exc:
        LOGGER.error(f"下载接口文档失败, 错误类型={type(exc).__name__}\n{traceback.format_exc()}")
        return FailureResponse(message="下载接口文档失败")
