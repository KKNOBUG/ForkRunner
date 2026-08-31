# -*- coding: utf-8 -*-
import hashlib
import os.path
import traceback
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import quote

import aiofiles.os as aos
from fastapi import APIRouter, Depends, File, Form, UploadFile
from starlette.responses import StreamingResponse

from applications.autotest.dependencies import AutoTestApiServices, get_autotest_api_services
from applications.autotest.models.autotest_step_model import AutoTestStepModel
from applications.autotest.schemas.autotest_data_create_schema import AutoTestDataCreateCreate
from applications.autotest.services.autotest_xlsx_create import generate_tcp_test_data, generate_test_data, is_field_mapping_doc
from configure import LOGGER, PROJECT_CONFIG
from core.responses import FailureResponse, SuccessResponse
from enums import AutoTestReqArgsType
from services.file_transfer import FileTransfer

autotest_data_create = APIRouter()


async def _calc_file_hash(file: UploadFile, *seeds: Any) -> str:
    """计算上传文件哈希，并在末尾拼接业务种子。"""
    sha256 = hashlib.sha256()
    while True:
        chunk = await file.read(8192)
        if not chunk:
            break
        sha256.update(chunk)
    await file.seek(0)
    suffix = "_".join(str(s) for s in seeds)
    return f"{sha256.hexdigest()}_{suffix}"


async def _serialize_data_create(instance: Any) -> Dict[str, Any]:
    """序列化单条生成记录，仅返回前端需要的核心字段。"""
    created = getattr(instance, "created_time", None)
    updated = getattr(instance, "updated_time", None)
    consuming = 0
    if isinstance(created, datetime) and isinstance(updated, datetime):
        consuming = int((updated - created).total_seconds())
    return {**(await instance.to_dict(replace_fields={"id": "data_create_id"})), "consuming": consuming}


async def _remove_file_if_exists(file_path: str) -> None:
    """安全删除本地文件，失败仅记录警告。"""
    try:
        if file_path and await aos.path.exists(file_path):
            await aos.remove(file_path)
    except Exception as e:
        LOGGER.warning(f"清理文件[{file_path}]失败, 异常描述: {e}")


@autotest_data_create.post(path="/download-temple", summary="测试模板下载")
async def download_file_temple(file_type: str = Form(..., description="模板类型")):
    """
    下载测试模板文件。

    :param file_type: 0=接口文档模板, 1=数据驱动模板
    :return: 文件流响应
    """
    temple_type = {
        "0": "接口文档模板.xlsx",
        "1": "数据驱动模板.xlsx",
    }
    if file_type not in temple_type:
        return FailureResponse(message="模板对应文件不存在")

    file_path = Path(PROJECT_CONFIG.OUTPUT_TEMPLATE_DIR) / temple_type[file_type]
    if not file_path.is_file():
        return FailureResponse(message="模板对应文件不存在")

    file_name = quote(temple_type[file_type].encode("utf-8"))
    return StreamingResponse(
        content=FileTransfer.iter_download_file_chunks(download_file=str(file_path)),
        media_type="application/octet-stream",
        headers={
            "fileName": file_name,
            "Content-Disposition": f"attachment; filename*=utf-8''{file_name}",
        }
    )


@autotest_data_create.post(path="/download-create", summary="接口数据下载")
async def download_file_create(
        create_code: str = Form(..., title="创建CODE"),
        services: AutoTestApiServices = Depends(get_autotest_api_services),
):
    instance_hash = await services.data_create_curd.get_by_code(create_code=create_code)
    if not instance_hash:
        return FailureResponse(message="不存在关联数据，请重试")

    case_id = instance_hash.case_id
    file_name = instance_hash.file_name
    file_path = os.path.join(PROJECT_CONFIG.OUTPUT_UPLOAD_DIR, "autotest", str(case_id), file_name)
    if not os.path.isfile(file_path):
        return FailureResponse(message="对应文件不存在")
    file_name = quote(file_name.encode('utf-8'))
    return StreamingResponse(
        content=FileTransfer.iter_download_file_chunks(download_file=file_path),
        media_type="application/octet-stream",
        headers={
            "fileName": file_name,
            "Content-Disposition": f"attachment; filename*=utf-8''{file_name}"
        }
    )


@autotest_data_create.post(path="/query-create", summary="接口数据记录查询")
async def query_file_create(
        step_code: str = Form(..., description="步骤CODE"),
        services: AutoTestApiServices = Depends(get_autotest_api_services),
):
    """
    根据步骤标识查询接口文件生成记录。

    :param step_code: 步骤标识代码
    :param services: 自动化测试CRUD依赖聚合
    :return: 统一HTTP响应
    """
    try:
        instances = await services.data_create_curd.get_by_step(step_code=step_code, state__not=1)
        if not instances:
            return SuccessResponse(message="查询成功", data=[])

        data_list: List[Dict[str, Any]] = []
        for instance in instances:
            data_list.append(await _serialize_data_create(instance))
        return SuccessResponse(message="查询成功", data=data_list)
    except Exception as e:
        LOGGER.error(f"查询接口文件生成记录失败，异常描述: {e}\n{traceback.format_exc()}")
        return FailureResponse(message=f"查询失败，异常描述: {e}")


@autotest_data_create.post(path="/delete-create", summary="接口数据记录删除")
async def delete_file_create(
        create_code: str = Form(..., description="生成CODE"),
        step_code: str = Form(..., description="步骤CODE"),
        services: AutoTestApiServices = Depends(get_autotest_api_services),
):
    """
    根据生成CODE软删除接口文件生成记录。

    :param create_code: 接口文件标识代码
    :param step_code: 步骤标识代码(与源项目保持一致，当前逻辑暂未使用)
    :param services: 自动化测试CRUD依赖聚合
    :return: 统一HTTP响应
    """
    try:
        instance = await services.data_create_curd.delete_data_create(create_code=create_code)
        data = await _serialize_data_create(instance)
        return SuccessResponse(message="删除成功", data=data)
    except Exception as e:
        LOGGER.error(f"删除接口文件生成记录失败，异常描述: {e}\n{traceback.format_exc()}")
        return FailureResponse(message=f"删除失败，异常描述: {e}")


@autotest_data_create.post(path="/upload-create", summary="接口文档上传")
async def upload_file_create(
        case_id: int = Form(..., description="案例ID"),
        step_id: int = Form(..., description="步骤ID"),
        step_code: str = Form(..., description="步骤CODE"),
        step_name: str = Form(..., description="步骤NAME"),
        rules_list: str = Form(..., description="生成规则"),
        file: UploadFile = File(..., description="案例数据源文件"),
        services: AutoTestApiServices = Depends(get_autotest_api_services),
):
    """
    上传接口文档并生成测试数据。

    :param case_id: 用例主键ID
    :param step_id: 步骤主键ID
    :param step_code: 步骤标识代码
    :param step_name: 步骤名称
    :param rules_list: 生成规则列表，逗号分隔
    :param file: 上传的接口文档
    :param services: 自动化测试CRUD依赖聚合
    :return: 统一HTTP响应
    """
    if not (file.filename or "").endswith(".xlsx"):
        return FailureResponse(message="仅支持xlsx格式文件")

    rules_dict = {
        0: "required",
        1: "length",
        2: "enum",
        3: "decimal",
    }
    file_hash = await _calc_file_hash(file, case_id, f"{rules_list}_{step_id}")

    try:
        instance_hash = await services.data_create_curd.get_by_hash(file_hash=file_hash, state__not=1)
        if instance_hash:
            if instance_hash.create_status != 2:
                return FailureResponse(message="该文件已存在相同数据，请更换文件或修改对应测试步骤")
            else:
                return FailureResponse(message="该文件上次生成测试数据失败，请核对修改文件信息")

        step_info: AutoTestStepModel = await services.step_curd.get_by_conditions(
            on_error=True,
            only_one=True,
            state__not=1,
            id=step_id,
            case_id=case_id,
            step_code=step_code,
        )
        request_args_type = step_info.request_args_type
        base_message = step_info.request_body

        case_info = await services.case_curd.get_by_id(case_id=case_id, on_error=True, state__not=1)
        case_code = case_info.case_code

        save_path = Path(PROJECT_CONFIG.OUTPUT_UPLOAD_DIR) / "autotest" / str(case_id)
        ok, path_or_error = await FileTransfer.save_upload_file_chunks(
            upload_file=file,
            destination=f"autotest/{case_id}",
            add_timestamp=False,
            check_filename=True,
            check_filetype=True,
            check_filesize=True,
            add_left_identifier=str(uuid.uuid4()),
            upload_file_size="small",
        )
        if not ok:
            return FailureResponse(message=f"交易失败，文件保存失败: {path_or_error}")
        save_file_name: str = path_or_error

        today_str = datetime.now().strftime("%Y%m%d%H%M%S")
        output_excel = save_path / f"{step_name}-{today_str}.xlsx"

        instance_create = await services.data_create_curd.create_data_create(
            data_in=AutoTestDataCreateCreate(
                case_id=case_id,
                case_code=case_code,
                step_code=step_code,
                create_status="0",
                file_name=output_excel.name,
                file_hash=file_hash,
                file_path=save_file_name,
                dataset={},
            )
        )

        rules = [rules_dict.get(i) for i in list(map(int, rules_list.split(",")))]

        if request_args_type == AutoTestReqArgsType.XML:
            await generate_tcp_test_data(
                input_excel=save_file_name,
                output_excel=str(output_excel),
                rules=rules,
                request_args_type="xml",
                xml_message=step_info.request_text,
                create_id=instance_create.id,
                step_name=step_name,
            )
        else:
            if is_field_mapping_doc(save_file_name):
                await generate_tcp_test_data(
                    input_excel=save_file_name,
                    output_excel=str(output_excel),
                    rules=rules,
                    request_args_type="json",
                    json_message=base_message,
                    create_id=instance_create.id,
                    step_name=step_name,
                )
            else:
                await generate_test_data(
                    input_excel=save_file_name,
                    output_excel=str(output_excel),
                    rules=rules,
                    json_message=base_message,
                    create_id=instance_create.id,
                    step_name=step_name,
                )
        return SuccessResponse(message="交易成功，任务已提交")
    except Exception as e:
        LOGGER.error(f"上传接口文档失败，异常描述: {e}\n{traceback.format_exc()}")
        return FailureResponse(message=f"交易异常，{e}")


@autotest_data_create.post(path="/delete-source", summary="数据源上传记录删除")
async def delete_file_source(
        step_code: str = Form(..., description="步骤CODE"),
        services: AutoTestApiServices = Depends(get_autotest_api_services),
):
    """
    删除步骤数据源上传记录并清空步骤上的数据源元信息。

    :param step_code: 步骤标识代码
    :param services: 自动化测试CRUD依赖聚合
    :return: 统一HTTP响应
    """
    try:
        instance = await services.data_source_curd.model.filter(
            step_code=step_code,
            state__not=1,
        ).first()
        if not instance:
            return FailureResponse(message="不存在关联数据，请重试")

        # 清空步骤上的数据源名称（源项目为file_name字段，当前项目映射为data_source_name）
        await services.step_curd.model.filter(
            case_id=instance.case_id,
            step_code=step_code,
            state=0,
        ).update(
            data_source_id=None,
            data_source_name=None,
            data_source_desc=None,
        )

        data = await instance.to_dict(
            exclude_fields={
                "state",
                "file_path",
                "created_user", "updated_user",
                "created_time", "updated_time",
                "reserve_1", "reserve_2", "reserve_3", "reserve_4", "reserve_5",
            },
            replace_fields={"id": "data_create_id"}
        )

        # 非占位哈希则删除物理文件并软删除记录
        if not instance.file_hash.endswith("X"):
            await _remove_file_if_exists(instance.file_path)
            await services.data_source_curd.soft_delete(id=instance.id)

        return SuccessResponse(message="删除成功", data=data)
    except Exception as e:
        LOGGER.error(f"删除数据源上传记录失败，异常描述: {e}\n{traceback.format_exc()}")
        return FailureResponse(message=f"删除失败，异常描述: {e}")
