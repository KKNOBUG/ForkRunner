# -*- coding: utf-8 -*-

"""智能生成任务的提交编排服务。"""

from __future__ import annotations

import asyncio
import os
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional
from zoneinfo import ZoneInfo

from fastapi import UploadFile

from applications.autotest.services.autotest_data_source_service import DataSourceService
from applications.data_generation.constants import MAX_DOCUMENT_SIZE
from applications.data_generation.dependencies import DataGenerationApiServices
from applications.data_generation.schemas.autotest_data_generate_schema import (
    AutoTestIntelligentDataGenerateTaskCreate,
)
from applications.data_generation.services.autotest_data_generate_file_utils import (
    file_sha256,
    generated_document_name,
)
from applications.data_generation.services.autotest_data_generate_submission_common import (
    dispatch_and_load_task,
    remove_uploaded_file_safely,
    uploaded_file_storage_key,
)
from applications.data_generation.services.autotest_data_generate_request_snapshot_service import (
    build_intelligent_request_snapshot,
)
from celery_scheduler.tasks.task_autotest_data_generate import (
    dispatch_data_generate_task,
)
from configure import LOGGER, PROJECT_CONFIG
from core.exceptions import ParameterException
from services.file_transfer import FileTransfer

_XLSX_REQUIRED_MEMBERS = frozenset({
    "[Content_Types].xml",
    "_rels/.rels",
    "xl/workbook.xml",
})
_MAX_XLSX_MEMBERS = 2_048
_MAX_XLSX_UNCOMPRESSED_SIZE = 64 * 1024 * 1024


class IntelligentTaskSubmissionError(RuntimeError):
    """智能生成任务提交阶段发生的非参数类业务错误。"""


@dataclass(frozen=True)
class IntelligentTaskSubmissionResult:
    """任务记录及其异步投递状态。"""

    task: Any
    dispatch_failed: bool = False


class AutoTestIntelligentTaskSubmissionService:
    """校验并保存智能生成输入，创建任务后投递到 Celery。"""

    def __init__(
            self,
            *,
            dispatcher: Callable[[int], Awaitable[Any]] = dispatch_data_generate_task,
            file_transfer: Any = FileTransfer,
            upload_root: str = PROJECT_CONFIG.OUTPUT_UPLOAD_DIR,
    ) -> None:
        self.dispatcher = dispatcher
        self.file_transfer = file_transfer
        self.upload_root = upload_root

    async def submit(
            self,
            *,
            case_id: int,
            step_id: int,
            step_code: str,
            scenario_file: UploadFile,
            services: DataGenerationApiServices,
            created_user: Optional[str],
    ) -> IntelligentTaskSubmissionResult:
        """保存并校验场景文件，创建智能生成任务后投递到Celery。

        :param case_id: 场景文件所属的测试用例ID。
        :param step_id: 当前请求步骤ID。
        :param step_code: 用于确认步骤归属的步骤编码。
        :param scenario_file: 用户上传的XLSX场景规则文件。
        :param services: 提供用例、步骤和数据生成任务CRUD的接口服务集合。
        :param created_user: 创建任务的用户标识，允许为空。
        :return: 已创建的任务；仅在投递异常且任务已回写失败时标记投递失败。
        :raises ParameterException: 上传文件或请求步骤不符合生成条件。
        :raises IntelligentTaskSubmissionError: 文件保存或存储路径处理失败。

        投递异常后若无法确认任务已进入失败状态，则继续传播原异常；
        任务落库后不清理其仍被任务记录引用的场景文件。

        任务落库前发生错误时，会删除本次已经保存的场景文件。
        """

        original_name = os.path.basename(
            str(scenario_file.filename or "").strip()
        )
        if not original_name.lower().endswith(".xlsx"):
            raise ParameterException(message="场景文档仅支持.xlsx文件")

        file_path: Optional[str] = None
        task = None
        try:
            case, step = await DataSourceService.resolve_case_and_step(
                services,
                case_id=case_id,
                step_id=step_id,
                step_code=step_code,
            )
            DataSourceService.ensure_request_step(step)
            DataSourceService.ensure_case_allows_data_source(case)
            request_snapshot = build_intelligent_request_snapshot(step)

            destination = os.path.join(
                "autotest_data_generate",
                str(case_id),
                str(step.step_code),
                "intelligent",
            )
            ok, path_or_error = await self.file_transfer.save_upload_file_chunks(
                upload_file=scenario_file,
                destination=destination,
                add_timestamp=True,
                check_filename=True,
                check_filetype=True,
                check_filesize=True,
                add_left_identifier=str(step.step_code),
                upload_file_size="tiny",
            )
            if not ok:
                LOGGER.warning("智能生成场景文档保存失败")
                raise IntelligentTaskSubmissionError("场景文档上传失败")
            file_path = path_or_error
            try:
                storage_key = uploaded_file_storage_key(file_path, self.upload_root)
            except ValueError as exc:
                raise IntelligentTaskSubmissionError("场景文档存储路径不合法") from exc
            interface_file_hash = await asyncio.to_thread(
                self._validate_and_hash_document,
                file_path,
            )

            task_in = AutoTestIntelligentDataGenerateTaskCreate(
                case_id=case.id,
                step_id=step.id,
                step_code=step.step_code,
                request_snapshot=request_snapshot,
                interface_file_name=original_name,
                interface_file_hash=interface_file_hash,
                interface_storage_key=storage_key,
                generated_file_name=generated_document_name(
                    step.step_name,
                    datetime.now(ZoneInfo("Asia/Shanghai")),
                ),
                task_summary={
                    "claude_agent": {
                        "model": PROJECT_CONFIG.CLAUDE_AGENT_MODEL,
                    },
                },
                created_user=created_user,
            )
            task = await services.data_generate_task_curd.create_task(task_in)

            task, dispatch_failed = await dispatch_and_load_task(
                task.id,
                services.data_generate_task_curd,
                self.dispatcher,
            )
            return IntelligentTaskSubmissionResult(
                task=task,
                dispatch_failed=dispatch_failed,
            )
        except Exception:
            if task is None:
                remove_uploaded_file_safely(file_path, self.upload_root)
            raise

    @staticmethod
    def _validate_and_hash_document(file_path: str) -> str:
        """校验已保存的XLSX场景文件并计算内容摘要。

        :param file_path: ``FileTransfer`` 返回的已保存文件路径。
        :return: 文件内容的SHA-256十六进制摘要。
        :raises ParameterException: 文件为空、超限或不是合法的OOXML工作簿。
        :raises IntelligentTaskSubmissionError: 已保存文件无法访问或读取。

        校验直接针对落盘后的真实内容执行，避免上传流被完整读取两次；
        同时限制ZIP成员数量和解压后总体积，防止异常压缩文件消耗资源。
        """

        path = Path(file_path)
        try:
            if not path.is_file() or path.is_symlink():
                raise IntelligentTaskSubmissionError("已保存的场景文档不可访问")
            file_size = path.stat().st_size
        except OSError as exc:
            raise IntelligentTaskSubmissionError("已保存的场景文档不可访问") from exc

        if file_size > MAX_DOCUMENT_SIZE:
            raise ParameterException(
                message=(
                    f"场景文档大小不能超过"
                    f"{MAX_DOCUMENT_SIZE // 1024 // 1024}MB"
                )
            )
        if file_size < 1:
            raise ParameterException(message="场景文档不是有效的.xlsx文件")

        try:
            with zipfile.ZipFile(path) as archive:
                members = archive.infolist()
                if len(members) > _MAX_XLSX_MEMBERS:
                    raise ParameterException(message="场景文档不是有效的.xlsx文件")
                uncompressed_size = sum(
                    member.file_size for member in members
                )
                if uncompressed_size > _MAX_XLSX_UNCOMPRESSED_SIZE:
                    raise ParameterException(message="场景文档不是有效的.xlsx文件")
                if not _XLSX_REQUIRED_MEMBERS.issubset(
                        member.filename for member in members
                ):
                    raise ParameterException(message="场景文档不是有效的.xlsx文件")
        except zipfile.BadZipFile as exc:
            raise ParameterException(message="场景文档不是有效的.xlsx文件") from exc
        except OSError as exc:
            raise IntelligentTaskSubmissionError("已保存的场景文档无法读取") from exc

        try:
            return file_sha256(path)
        except OSError as exc:
            raise IntelligentTaskSubmissionError("已保存的场景文档无法读取") from exc
