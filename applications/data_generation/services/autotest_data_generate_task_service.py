# -*- coding: utf-8 -*-

'''
    数据生成功能的顶层编排器，对应celery任务的执行入口
    负责把所有服务串成一条可重入的执行流水线
'''

from __future__ import annotations

import asyncio
from typing import Any, Dict, Mapping, Optional

from applications.data_generation.constants import RULE_ENUM
from applications.data_generation.services.autotest_data_generate_export_service import (
    AutoTestDataGenerateExportService,
)
from applications.data_generation.services.autotest_data_generate_result_crud import (
    AutoTestDataGenerateResultCrud,
)
from applications.data_generation.services.autotest_data_generate_service import (
    generate_test_data_scenarios,
)
from applications.data_generation.services.autotest_data_generate_task_crud import (
    AutoTestDataGenerateTaskCrud,
)
from applications.data_generation.services.autotest_project_enum_extraction_service import (
    AutoTestProjectEnumExtractionService,
    ProjectEnumExtractionOutcome,
)
from applications.data_generation.services.autotest_project_interface_workbook_service import (
    AutoTestProjectInterfaceWorkbookService,
)


class DataGenerateTaskInputError(ValueError):
    """任务快照缺失或结构不合法，重试无法恢复。"""


class AutoTestDataGenerateTaskService:
    """
        数据生成任务的总编排器。
    """
    def __init__(
            self,
            task_crud: Optional[AutoTestDataGenerateTaskCrud] = None,
            result_crud: Optional[AutoTestDataGenerateResultCrud] = None,
            export_service: Optional[AutoTestDataGenerateExportService] = None,
            enum_extraction_service: Optional[AutoTestProjectEnumExtractionService] = None,
            workbook_service: Optional[AutoTestProjectInterfaceWorkbookService] = None,
    ):
        """ 允许外部传入各个服务，也允许使用默认实现 """
        self.task_crud = task_crud or AutoTestDataGenerateTaskCrud()
        self.result_crud = result_crud or AutoTestDataGenerateResultCrud()
        self.export_service = export_service or AutoTestDataGenerateExportService(
            result_crud=self.result_crud,
        )
        self.enum_extraction_service = (
            enum_extraction_service or AutoTestProjectEnumExtractionService()
        )
        self.workbook_service = workbook_service or AutoTestProjectInterfaceWorkbookService()

    @staticmethod
    def _validate_snapshots(task: Any) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
        """
            验证任务快照
        """
        interface_document = task.interface_schema_snapshot
        request_snapshot = task.request_snapshot
        if not isinstance(interface_document, Mapping):
            raise DataGenerateTaskInputError("接口文档解析快照不存在或格式不正确")
        if not isinstance(interface_document.get("fields"), list):
            raise DataGenerateTaskInputError("接口文档解析快照缺少fields列表")
        if not isinstance(request_snapshot, Mapping):
            raise DataGenerateTaskInputError("请求报文快照必须是字段路径到值的映射")
        document_style = str(interface_document.get("interface_style") or "").strip().lower()
        task_style = str(task.interface_style or "").strip().lower()
        if document_style and document_style != task_style:
            raise DataGenerateTaskInputError("接口文档解析快照与任务接口样式不一致")
        return interface_document, request_snapshot

    async def execute(
            self,
            task_id: int,
            celery_id: str,
            *,
            attempt: int,
    ) -> Dict[str, Any]:
        """
            核心入口：生成、落库并导出；重复执行会原子替换同一任务的旧结果。
        """
        task = await self.task_crud.claim_attempt(task_id, celery_id, attempt)
        interface_document, request_snapshot = self._validate_snapshots(task)
        enum_outcome: Optional[ProjectEnumExtractionOutcome] = None
        workbook_result = None
        # 旧条件因新增枚举校验限制而失效，保留供变更对照：
        # if str(task.interface_style or "").strip().lower() == "project":
        is_project = str(task.interface_style or "").strip().lower() == "project"
        # 以任务提交时保存的规则为准；未选枚举时既不调用AI，也不改写上传文档。
        needs_enum_extraction = is_project and RULE_ENUM in (task.rule_codes or [])
        if needs_enum_extraction:
            enum_outcome = await self.enum_extraction_service.extract(interface_document)
            interface_document = enum_outcome.document
            # openpyxl是同步文件IO，放入线程避免阻塞Celery异步编排。
            workbook_result = await asyncio.to_thread(
                self.workbook_service.overwrite_project_document,
                task.interface_storage_key,
                interface_document,
            )
        scenarios = generate_test_data_scenarios(
            interface_document,
            request_snapshot,
            task.rule_codes,
        )
        error_scenario_count = sum(
            1 for scenario in scenarios if not scenario.get("scenario_data")
        )
        valid_scenario_count = len(scenarios) - error_scenario_count
        await self.result_crud.replace_task_results(
            task.id,
            scenarios,
            expected_celery_id=celery_id,
        )
        exported = await self.export_service.export_task(task)
        summary: Dict[str, Any] = {
            "success": True,
            "attempt": attempt,
            "interface_style": task.interface_style,
            "rule_codes": list(task.rule_codes or []),
            "file_name": exported.file_name,
            "storage_key": exported.storage_key,
            "size": exported.size,
            "error_scenario_count": error_scenario_count,
            "valid_scenario_count": valid_scenario_count,
        }
        if enum_outcome is not None and workbook_result is not None:
            summary["project_enum_extraction"] = {
                "extracted_count": enum_outcome.extracted_count,
                "not_found_count": enum_outcome.not_found_count,
                "ambiguous_count": enum_outcome.ambiguous_count,
                "interface_document_overwritten": True,
                "inserted_enum_column": workbook_result.inserted_column,
                "written_count": workbook_result.written_count,
                "file_size": workbook_result.file_size,
                "models_used": list(enum_outcome.models_used),
                "attempted_models": list(enum_outcome.attempted_models),
                "failover_count": enum_outcome.failover_count,
            }
        completed = await self.task_crud.mark_success_owned(
            task.id,
            celery_id,
            summary,
        )
        return {
            **summary,
            "task_id": completed.id,
            "task_code": completed.task_code,
            "generated_count": completed.generated_count,
        }
