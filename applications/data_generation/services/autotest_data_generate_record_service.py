# -*- coding: utf-8 -*-

'''
结果应用功能
1.查询单个任务的生成情况
2.导出已经生成成功的结果文件
3.把生成结果追加到数据编辑模块
4.安全解析已经上传的接口文档的存储路径
'''

from __future__ import annotations
from typing import Any, Dict, List, Mapping, Optional, Tuple
from tortoise.transactions import in_transaction

from applications.data_generation.services.autotest_data_generate_export_service import (
    AutoTestDataGenerateExportService,
    DataGenerateExportFile,
)
from applications.data_generation.services.autotest_data_generate_result_crud import (
    AutoTestDataGenerateResultCrud,
)
from applications.data_generation.services.autotest_data_generate_result_utils import get_result_value
from applications.data_generation.services.autotest_data_generate_task_crud import (
    AutoTestDataGenerateTaskCrud,
)
from applications.data_generation.services.autotest_project_interface_workbook_service import (
    ProjectInterfaceWorkbookError,
    resolve_uploaded_interface_document_path,
)
from applications.autotest.services.autotest_data_source_parser import (
    AXIS_HORIZONTAL,
    AXIS_VERTICAL,
    extract_scene_names_from_matrix,
    normalize_dataset_record,
    resolve_matrix_axis,
)
from applications.autotest.schemas.autotest_data_source_schema import AutoTestDataSourceUpdate
from applications.autotest.services.autotest_data_source_service import (
    build_vertical_matrix_from_step,
    ensure_case_allows_data_source,
    ensure_request_step,
    resolve_case_and_step,
    sync_step_data_source_meta,
)
from core.exceptions import ParameterException
from enums import AutoTestDataGenerateStatus
from services import get_current_username


_SECTION_MARKERS = {"HEAD", "BODY", "ASSERT_HEAD", "ASSERT_BODY"}
_DEFAULT_EMPTY_SCENE_NAME = "场景1名称"


def _unique_scene_name(raw_name: Any, used_names: set) -> str:
    """
        防止追加测试数据时覆盖已有场景。
        raw_name：原始场景名
        used_names：已经存在的场景名集合
        返回值：返回一个不与used_names重复的字符串场景名
    """
    base_name = str(raw_name or "").strip()
    if not base_name:
        raise ParameterException(message="存在名称为空的生成场景，无法应用")
    candidate = base_name
    sequence = 2
    #防止同名场景名称覆盖？逻辑可以优化，理论上不存在完全相同的像个场景名称，如果遇到怎么处理
    while candidate in used_names:
        candidate = f"{base_name}（{sequence}）"
        sequence += 1
    used_names.add(candidate)
    return candidate


def _pad_matrix(matrix: List[List[Any]]) -> List[List[Any]]:
    """
        把不规则二维数组补充为矩形。
        matrix：二维数组
        返回值：返回一个矩形二维数组
    """
    if not isinstance(matrix, list) or not matrix:
        raise ParameterException(message="数据编辑表格结构为空，无法追加测试场景")
    width = max((len(row) for row in matrix if isinstance(row, list)), default=0)
    if width == 0:
        raise ParameterException(message="数据编辑表格结构为空，无法追加测试场景")
    padded: List[List[Any]] = []
    for raw_row in matrix:
        row = list(raw_row) if isinstance(raw_row, list) else []
        row.extend([""] * (width - len(row)))
        padded.append(row)
    return padded


def append_generated_scenarios(
        matrix: List[List[Any]],
        axis: Optional[int],
        existing_dataset: Optional[Mapping[str, Any]],
        results: List[Any],
) -> Tuple[Dict[str, Dict[str, Any]], List[str], List[List[Any]], int]:
    """
        核心功能函数：在不新增、不删除、不重排的情况下，把所有有效生成场景追加到已有的数据编辑表格中
        matrix：数据编辑页面当前表格的二维数组，外层list表示整个表格，内层list表示一行
        axis：表示数据编辑表格中场景的排列方向，即转置
        existing_dataset：数据编辑页面中已经保存的场景数据集合，与matrix表示同一份业务数据的不同结构
        results：当前数据生成任务产生的所有测试场景
        返回值：[更新后的完整数据集，场景名称列表，更新后的二维矩阵，实际识别出的矩阵方向]
    """
    #过滤无效场景
    results = [
        result for result in results
        if isinstance(get_result_value(result, "scenario_data"), Mapping)
        and bool(get_result_value(result, "scenario_data"))
    ]
    if not results:
        raise ParameterException(message="当前任务没有可应用的有效测试场景")

    appended_matrix = _pad_matrix(matrix)
    used_axis = resolve_matrix_axis(appended_matrix, declared_axis=axis)
    existing_names = extract_scene_names_from_matrix(appended_matrix, used_axis)
    if existing_names == [_DEFAULT_EMPTY_SCENE_NAME]:
        if used_axis == AXIS_VERTICAL:
            scene_column = next(
                index
                for index, cell in enumerate(appended_matrix[0][1:], start=1)
                if str(cell or "").strip() == _DEFAULT_EMPTY_SCENE_NAME
            )
            for row in appended_matrix:
                row.pop(scene_column)
        else:
            appended_matrix = [
                appended_matrix[0],
                *[
                    row for row in appended_matrix[1:]
                    if not row or str(row[0] or "").strip() != _DEFAULT_EMPTY_SCENE_NAME
                ],
            ]
        existing_names = []
        existing_dataset = {}
    used_names = set(existing_names)
    dataset: Dict[str, Dict[str, Any]] = {
        str(name): normalize_dataset_record(record if isinstance(record, dict) else {})
        for name, record in (existing_dataset or {}).items()
    }
    appended_names: List[str] = []

    if used_axis == AXIS_VERTICAL:
        body_rows: Dict[str, int] = {}
        current_section = ""
        for row_index, row in enumerate(appended_matrix[1:], start=1):
            first_cell = str(row[0] or "").strip()
            marker = first_cell.upper()
            if marker in _SECTION_MARKERS:
                current_section = marker
            elif current_section == "BODY" and first_cell and first_cell not in body_rows:
                body_rows[first_cell] = row_index
        if not body_rows:
            raise ParameterException(message="数据编辑表格中没有可匹配的BODY字段")

        body_row_indices = set(body_rows.values())
        for result in results:
            name = _unique_scene_name(get_result_value(result, "scene_name"), used_names)
            scenario_data = get_result_value(result, "scenario_data")
            values = scenario_data if isinstance(scenario_data, Mapping) else {}
            appended_matrix[0].append(name)
            body: Dict[str, Any] = {}
            for row_index, row in enumerate(appended_matrix[1:], start=1):
                path = str(row[0] or "").strip()
                if row_index in body_row_indices and path in values:
                    value = values[path]
                    row.append(value)
                    body[path] = value
                else:
                    row.append("")
            dataset[name] = normalize_dataset_record({"body": body})
            appended_names.append(name)
    elif used_axis == AXIS_HORIZONTAL:
        header = appended_matrix[0]
        body_columns: Dict[str, int] = {}
        current_section = ""
        for column_index, cell in enumerate(header[1:], start=1):
            text = str(cell or "").strip()
            marker = text.upper()
            if marker in _SECTION_MARKERS:
                current_section = marker
            elif current_section == "BODY" and text and text not in body_columns:
                body_columns[text] = column_index
        if not body_columns:
            raise ParameterException(message="数据编辑表格中没有可匹配的BODY字段")

        for result in results:
            name = _unique_scene_name(get_result_value(result, "scene_name"), used_names)
            scenario_data = get_result_value(result, "scenario_data")
            values = scenario_data if isinstance(scenario_data, Mapping) else {}
            row: List[Any] = [name, *([""] * (len(header) - 1))]
            body: Dict[str, Any] = {}
            for path, column_index in body_columns.items():
                if path in values:
                    value = values[path]
                    row[column_index] = value
                    body[path] = value
            appended_matrix.append(row)
            dataset[name] = normalize_dataset_record({"body": body})
            appended_names.append(name)

    return dataset, [*existing_names, *appended_names], appended_matrix, used_axis


class AutoTestDataGenerateRecordService:
    """生成记录的详情、应用和文件定位服务。"""

    def __init__(
            self,
            task_crud: Optional[AutoTestDataGenerateTaskCrud] = None,
            result_crud: Optional[AutoTestDataGenerateResultCrud] = None,
            export_service: Optional[AutoTestDataGenerateExportService] = None,
    ):
        self.task_crud = task_crud or AutoTestDataGenerateTaskCrud()
        self.result_crud = result_crud or AutoTestDataGenerateResultCrud()
        self._export_service = export_service

    @property
    def export_service(self) -> AutoTestDataGenerateExportService:
        if self._export_service is None:
            self._export_service = AutoTestDataGenerateExportService(
                result_crud=self.result_crud,
            )
        return self._export_service

    async def detail(self, task_id: int) -> Dict[str, Any]:
        task = await self.task_crud.get_by_id(task_id, on_error=True, state__not=1)
        task_summary = dict(task.task_summary or {})
        error_scenario_count = task_summary.setdefault("error_scenario_count", 0)
        task_summary.setdefault(
            "valid_scenario_count",
            max((task.generated_count or 0) - error_scenario_count, 0),
        )
        return {
            "task": task,
            "task_summary": task_summary,
        }

    async def ensure_generated_file(self, task_id: int) -> DataGenerateExportFile:
        task = await self.task_crud.get_by_id(task_id, on_error=True, state__not=1)
        if task.task_status != AutoTestDataGenerateStatus.SUCCESS:
            raise ParameterException(message="只有生成成功的任务可以下载数据生成文档")
        return await self.export_service.export_task(task)

    async def apply(self, task_id: int, services: Any):
        task = await self.task_crud.get_by_id(task_id, on_error=True, state__not=1)
        if task.task_status != AutoTestDataGenerateStatus.SUCCESS:
            raise ParameterException(message="只有生成成功的任务可以应用")

        case, step = await resolve_case_and_step(
            services,
            case_id=task.case_id,
            step_id=task.step_id,
        )
        ensure_request_step(step)
        ensure_case_allows_data_source(case)

        results = await self.result_crud.list_by_task_id(task.id)
        async with in_transaction() as connection:
            # 先锁定步骤槽位，既串行化多任务应用，也覆盖“数据源尚未创建”的并发场景。
            locked_step = await services.step_curd.model.filter(id=step.id).using_db(
                connection
            ).select_for_update().first()
            if locked_step is None:
                raise ParameterException(message="应用目标步骤不存在")
            existing = await services.data_source_curd.model.filter(
                case_id=case.id,
                step_id=step.id,
                step_code=step.step_code,
                state__not=1,
            ).using_db(connection).select_for_update().first()
            base_matrix = (
                existing.dataframe
                if existing and isinstance(existing.dataframe, list) and existing.dataframe
                else build_vertical_matrix_from_step(step)
            )
            dataset, dataset_names, dataframe, axis = append_generated_scenarios(
                base_matrix,
                existing.axis if existing else AXIS_VERTICAL,
                existing.dataset if existing else None,
                results,
            )

            if existing:
                instance = await services.data_source_curd.update_data_source(
                    AutoTestDataSourceUpdate(
                        data_source_id=existing.id,
                        dataset=dataset,
                        dataset_names=dataset_names,
                        dataframe=dataframe,
                        axis=axis,
                        updated_user=get_current_username(),
                    )
                )
            else:
                instance = await services.data_source_curd.create_data_sources_from_parsed(
                    case_id=case.id,
                    case_code=case.case_code,
                    step_id=step.id,
                    step_code=step.step_code,
                    file_desc=f"应用数据生成任务 {task.task_code}",
                    parsed_data=dataset,
                    dataset_names=dataset_names,
                    dataframe=dataframe,
                    axis=axis,
                    created_user=get_current_username(),
                )
            await sync_step_data_source_meta(
                services,
                case_id=case.id,
                step_code=step.step_code,
                data_source_id=instance.id,
                file_name=None,
                file_desc=f"应用数据生成任务 {task.task_code}",
            )
            return instance


def resolve_interface_document_path(storage_key: str) -> str:
    """把接口文档相对键限制在上传目录内，防止下载路径越界。"""
    try:
        return resolve_uploaded_interface_document_path(storage_key)
    except ProjectInterfaceWorkbookError as exc:
        raise ParameterException(message=str(exc)) from exc
