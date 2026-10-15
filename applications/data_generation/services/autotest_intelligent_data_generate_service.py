# -*- coding: utf-8 -*-

"""智能生成任务的执行、结果入库和原始工作簿发布。"""

from __future__ import annotations

import asyncio
import json
import math
import shutil
import tempfile
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException

from applications.data_generation.constants import (
    INTELLIGENT_ANALYSIS_FILE_NAME as _ANALYSIS_FILE_NAME,
    INTELLIGENT_OUTPUT_FILE_NAME as _OUTPUT_FILE_NAME,
    INTELLIGENT_SCENARIO_DATA_FILE_NAME as _SCENARIO_DATA_FILE_NAME,
    INTELLIGENT_SCENARIO_FILE_NAME as _SCENARIO_FILE_NAME,
    INTELLIGENT_SNAPSHOT_FILE_NAME as _SNAPSHOT_FILE_NAME,
    MAX_GENERATED_SCENARIOS,
    MAX_INTELLIGENT_OUTPUT_SIZE,
)
from applications.data_generation.services.autotest_claude_agent_client import (
    AutoTestClaudeAgentClient,
    ClaudeAgentExecutionError,
    ClaudeAgentRunResult,
)
from applications.data_generation.services.autotest_data_generate_export_service import (
    AutoTestDataGenerateExportService,
)
from applications.data_generation.services.autotest_data_generate_file_utils import (
    file_sha256,
)
from applications.data_generation.services.autotest_data_generate_result_crud import (
    AutoTestDataGenerateResultCrud,
)
from applications.data_generation.services.autotest_data_generate_task_crud import (
    AutoTestDataGenerateTaskCrud,
)
from configure import LOGGER
from applications.data_generation.services.autotest_interface_workbook_service import (
    InterfaceWorkbookError,
    resolve_uploaded_interface_document_path,
)
from output.skills.test_data_generator.scripts.input_reader import (
    ScenarioInputError,
    load_scenarios,
)
from output.skills.test_data_generator.scripts.output_writer import (
    OutputDataError,
    append_analysis_batch,
    generate_output_from_csv,
    load_analysis_result,
)
from output.skills.test_data_generator.scripts.request_snapshot_reader import (
    RequestSnapshotError,
    normalize_request_snapshot,
)

# 单次任务级默认值；可通过构造函数覆盖以便测试。
_DEFAULT_BATCH_SIZE = 20
_DEFAULT_MAX_CONCURRENT_BATCHES = 2
_INTERMEDIATE_CSV_FILE_NAME = "_analysis_result.csv"


class IntelligentDataGenerateInputError(ValueError):
    """
    表示智能生成任务输入或分析结果不符合服务端固定契约。

    本异常统一承载HEAD/BODY快照、场景文档、分析结果和生成工作簿的校验失败，
    供上层任务编排逻辑将不可重试的输入错误写入任务状态。
    """


@dataclass
class _BatchTracker:
    """集中追踪一次智能生成任务的批次执行状态。

    把批次循环中需要跨迭代维护的预算、会话、统计、去重等状态从主编排函数
    中提取出来，避免单函数承载过多局部变量。
    """

    total_rows: int = 0
    duration_ms: int = 0
    num_turns: int = 0
    total_cost_usd: float = 0.0
    committed_budget_usd: float = 0.0
    peak_batch_cost_usd: float = 0.0
    has_cost: bool = False
    seen_names: set[str] = field(default_factory=set)
    lane_sessions: list[Optional[str]] = field(default_factory=list)


def _cell_text(value: Any) -> str:
    """将工作簿标量单元格值转换为场景数据使用的文本。

    :param value: ``openpyxl`` 读取的单元格值；允许为空、布尔值、字符串或
        数值。
    :return: 规范化后的文本；空值转换为空字符串，布尔值转换为小写英文。
    :raises IntelligentDataGenerateInputError: 单元格包含不受支持的复合值或
        其他对象。
    """

    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (str, int, float)):
        return str(value)
    raise IntelligentDataGenerateInputError("智能生成工作簿包含不支持的单元格值")


def read_intelligent_workbook(
        file_path: str | Path,
        request_snapshot: Mapping[str, Any],
) -> list[Dict[str, Any]]:
    """读取并校验后端生成的固定格式工作簿，转换为待入库场景。

    :param file_path: 后端输出的XLSX工作簿路径；必须是非符号链接的普通
        文件，且大小不能超过智能生成输出限制。
    :param request_snapshot: 提交任务时保存的HEAD/BODY字段快照；各分区的键及
        顺序定义工作簿期望的字段列。
    :return: 按工作表行顺序生成的场景字典列表，每项包含原样保留的场景名及
        HEAD/BODY分区的字段路径到文本值映射。
    :raises IntelligentDataGenerateInputError: 文件不可用、工作簿结构或字段与
        快照不一致、场景超限，或者单元格内容不符合输入契约。

    场景名长度和重复性由结果CRUD统一校验；本函数只负责文件信任边界和
    工作簿格式校验，并保证打开的工作簿在返回或抛出异常前关闭。
    """

    path = Path(file_path)
    if not path.is_file() or path.is_symlink():
        raise IntelligentDataGenerateInputError("智能生成工作簿不存在")
    if path.stat().st_size > MAX_INTELLIGENT_OUTPUT_SIZE:
        raise IntelligentDataGenerateInputError("智能生成工作簿大小超出限制")

    head_paths = list(request_snapshot["HEAD"])
    body_paths = list(request_snapshot["BODY"])
    body_marker_index = len(head_paths) + 2
    expected_headers = ["", "Head", *head_paths, "Body", *body_paths]
    try:
        workbook = load_workbook(
            path,
            read_only=True,
            data_only=False,
            keep_links=False,
        )
    except (OSError, InvalidFileException, KeyError, ValueError) as exc:
        raise IntelligentDataGenerateInputError(
            "智能生成文件不是有效的.xlsx工作簿"
        ) from exc

    try:
        if len(workbook.sheetnames) != 1:
            raise IntelligentDataGenerateInputError("智能生成工作簿只能包含一个工作表")
        sheet = workbook[workbook.sheetnames[0]]
        rows = sheet.iter_rows()
        header_cells = next(rows, None)
        if header_cells is None or len(header_cells) != len(expected_headers):
            raise IntelligentDataGenerateInputError("智能生成工作簿字段数量与请求快照不一致")
        headers = [
            _cell_text(cell.value).strip()
            for cell in header_cells
        ]
        if headers != expected_headers:
            raise IntelligentDataGenerateInputError("智能生成工作簿字段与请求快照不一致")

        scenarios: list[Dict[str, Any]] = []
        for row_number, cells in enumerate(rows, start=2):
            if len(scenarios) >= MAX_GENERATED_SCENARIOS:
                raise IntelligentDataGenerateInputError(
                    f"智能生成场景数量不能超过{MAX_GENERATED_SCENARIOS}条"
                )
            if any(cell.data_type == "f" for cell in cells):
                raise IntelligentDataGenerateInputError(
                    f"智能生成工作簿第{row_number}行包含公式单元格"
                )
            values = [_cell_text(cell.value) for cell in cells]
            if any(values[column] for column in (1, body_marker_index)):
                raise IntelligentDataGenerateInputError(
                    f"智能生成工作簿第{row_number}行的Head和Body分区必须为空"
                )

            scenarios.append({
                "scene_name": values[0],
                "scenario_data": {
                    "HEAD": dict(zip(head_paths, values[2:body_marker_index])),
                    "BODY": dict(zip(body_paths, values[body_marker_index + 1:])),
                },
            })
        if not scenarios:
            raise IntelligentDataGenerateInputError("智能生成工作簿没有测试场景")
        return scenarios
    finally:
        workbook.close()


class AutoTestIntelligentDataGenerateService:
    """
    编排一次智能测试数据生成并复用现有任务状态、结果表和导出机制。

    服务负责校验任务快照、构造隔离工作区、调用Claude Agent取得分析结果、
    生成并校验工作簿、替换生成结果、发布工作簿以及提交成功状态；任务执行权
    和状态归属由调用方传入的Celery标识及各CRUD协作对象共同约束。
    """

    def __init__(
            self,
            *,
            task_crud: Optional[AutoTestDataGenerateTaskCrud] = None,
            result_crud: Optional[AutoTestDataGenerateResultCrud] = None,
            export_service: Optional[AutoTestDataGenerateExportService] = None,
            agent_client: Optional[AutoTestClaudeAgentClient] = None,
            batch_size: int = _DEFAULT_BATCH_SIZE,
            max_concurrent_batches: int = _DEFAULT_MAX_CONCURRENT_BATCHES,
    ) -> None:
        """初始化智能生成所需的持久化、文件发布和Agent协作对象。

        :param task_crud: 数据生成任务状态访问器；未传入时创建默认实例。
        :param result_crud: 生成场景结果访问器；未传入时创建默认实例。
        :param export_service: 后端输出工作簿发布服务；未传入时使用同一个
            结果访问器创建默认实例。
        :param agent_client: Claude Agent调用客户端；未传入时创建默认实例。
        :param batch_size: 每个Agent批次包含的输入场景数量；测试可注入较小值。
        :param max_concurrent_batches: 并行执行的Agent批次数上限。

        初始化后的服务不持有单次任务状态，可由调用方按依赖生命周期复用。
        """

        if batch_size < 1:
            raise ValueError("batch_size必须大于等于1")
        if max_concurrent_batches < 1:
            raise ValueError("max_concurrent_batches必须大于等于1")
        self.task_crud = task_crud or AutoTestDataGenerateTaskCrud()
        self.result_crud = result_crud or AutoTestDataGenerateResultCrud()
        self.export_service = export_service or AutoTestDataGenerateExportService(
            result_crud=self.result_crud,
        )
        self.agent_client = agent_client or AutoTestClaudeAgentClient()
        self.batch_size = batch_size
        self.max_concurrent_batches = max_concurrent_batches

    @staticmethod
    def _prepare_workspace(
            task: Any,
            workspace: Path,
            request_snapshot: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        """在隔离工作区准备Agent可直接读取的场景JSON和HEAD/BODY快照。

        :param task: 包含场景文件存储键及提交时文件摘要的智能生成任务。
        :param workspace: 本次执行专用的临时目录。
        :param request_snapshot: 已通过校验、需要序列化给Agent的分区快照。
        :return: 与写入场景JSON一致、可直接用于批次切分的场景列表。
        :raises IntelligentDataGenerateInputError: 场景文件不存在、内容与提交时
            不一致、模板校验失败，或者预处理输入无法写入。

        本方法会复制并校验场景文件，复用Skill输入读取逻辑生成场景JSON，
        同时写入紧凑的请求快照；不会修改原始上传文件，也无需Agent执行脚本。
        """

        try:
            source = Path(
                resolve_uploaded_interface_document_path(task.interface_storage_key)
            )
        except InterfaceWorkbookError as exc:
            raise IntelligentDataGenerateInputError(str(exc)) from exc
        if not source.is_file():
            raise IntelligentDataGenerateInputError("智能生成场景文档不存在")

        scenario_path = workspace / _SCENARIO_FILE_NAME
        shutil.copyfile(source, scenario_path)
        if file_sha256(scenario_path) != str(task.interface_file_hash or "").lower():
            raise IntelligentDataGenerateInputError("智能生成场景文档与任务提交时不一致")
        try:
            # 提前执行Skill使用的严格校验，模板错误可直接失败而不浪费Agent调用。
            scenarios = load_scenarios(str(scenario_path))
            LOGGER.info(
                f"【智能生成】场景规则预处理完成: 共{len(scenarios)}条场景"
            )
        except ScenarioInputError as exc:
            raise IntelligentDataGenerateInputError(str(exc)) from exc
        try:
            scenario_json = json.dumps(
                scenarios,
                ensure_ascii=False,
                default=str,
                separators=(",", ":"),
            )
            # Excel单元格可能是日期；内存批次必须与Agent读取的JSON值一致。
            serialized_scenarios = json.loads(scenario_json)
            (workspace / _SCENARIO_DATA_FILE_NAME).write_text(
                scenario_json, encoding="utf-8",
            )
            (workspace / _SNAPSHOT_FILE_NAME).write_text(
                json.dumps(
                    dict(request_snapshot),
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                encoding="utf-8",
            )
        except (OSError, TypeError, ValueError) as exc:
            raise IntelligentDataGenerateInputError("智能生成任务输入无法序列化") from exc
        return serialized_scenarios

    async def _run_batch_wave(
            self,
            batch_sessions: list[tuple[Path, Optional[str]]],
            *,
            baseline_time: datetime,
            batch_budget: float,
            remaining_time: float,
    ) -> list[ClaudeAgentRunResult]:
        """并行执行最多两个已准备批次，并在失败或超时时取消其他批次。

        :param batch_sessions: 按输入顺序排列的工作目录和各自待续接的
            会话标识，数量为一或二；同一目录不会在一波内重复出现。
        :param baseline_time: 全任务共用的上海时区时间基准。
        :param batch_budget: 为每个批次预留的美元预算上限。
        :param remaining_time: 当前任务截止时间之前剩余的秒数。
        :return: 与输入顺序一致、含各自会话标识的Agent执行摘要。
        :raises ClaudeAgentExecutionError: 任务截止时间已到；批次自身的错误
            保留原类型向上传播。

        返回前等待已取消的查询清理完毕，避免临时工作区先于SDK进程删除。
        """

        agent_tasks = [
            asyncio.create_task(self.agent_client.run(
                batch_workspace,
                baseline_time=baseline_time,
                max_budget_usd=batch_budget,
                timeout_seconds=remaining_time,
                resume_session_id=session_id,
            ))
            for batch_workspace, session_id in batch_sessions
        ]
        try:
            completed, pending = await asyncio.wait(
                agent_tasks,
                timeout=remaining_time,
                return_when=asyncio.FIRST_EXCEPTION,
            )
            for agent_task in agent_tasks:
                if agent_task in completed:
                    agent_task.result()
            if pending:
                raise ClaudeAgentExecutionError(
                    "智能生成任务执行超时",
                    public_message="智能生成任务执行超时",
                )
            return [agent_task.result() for agent_task in agent_tasks]
        finally:
            for agent_task in agent_tasks:
                if not agent_task.done():
                    agent_task.cancel()
            await asyncio.gather(*agent_tasks, return_exceptions=True)

    async def execute(
            self,
            task: Any,
            celery_id: str,
            *,
            attempt: int,
    ) -> Dict[str, Any]:
        """执行智能生成并提交结果、文件和成功状态。

        :param task: 已由当前Celery任务取得执行权的智能生成任务。
        :param celery_id: 当前Celery任务标识，用于约束结果和状态写入权限。
        :param attempt: 当前执行次数，从1开始并写入任务摘要。
        :return: 包含任务标识、生成数量、文件信息和Agent执行摘要的结果。
        :raises IntelligentDataGenerateInputError: 输入快照、场景文件、分析结果或
            输出文件不符合契约。
        :raises ClaudeAgentConfigurationError: Claude Agent配置或Skill不完整。
        :raises ClaudeAgentExecutionError: Claude Agent调用失败、执行超时或
            工作簿写入失败。

        主编排函数，按 _stage_inputs → _run_waves → _consolidate_and_publish 三段
        执行；返回前临时工作区会被自动清理，任一步骤失败时异常向上传播，成功状态
        在最后一段中以当前 Celery 任务所有权提交。
        """

        with tempfile.TemporaryDirectory(prefix="forkrunner-intelligent-") as directory:
            workspace = Path(directory)
            request_snapshot, input_scenarios, baseline_time = await self._stage_inputs(
                task, workspace,
            )
            tracker = _BatchTracker(
                lane_sessions=[None] * self.max_concurrent_batches,
            )
            csv_path = workspace / _INTERMEDIATE_CSV_FILE_NAME
            await self._run_waves(
                workspace, input_scenarios, request_snapshot,
                baseline_time, csv_path, tracker,
            )
            return await self._consolidate_and_publish(
                task, celery_id, workspace, request_snapshot, csv_path,
                tracker, attempt,
            )

    async def _stage_inputs(
            self,
            task: Any,
            workspace: Path,
    ) -> tuple[Mapping[str, Any], list[dict[str, Any]], datetime]:
        """校验任务快照并准备工作区，返回快照、场景列表与时间基准。

        :param task: 智能生成任务记录，提供请求快照和场景文件引用。
        :param workspace: 已创建的隔离临时目录。
        :return: 三元组：经规范化的HEAD/BODY快照、与写入JSON一致的场景列表、
            全任务共用的上海时区时间基准。
        :raises IntelligentDataGenerateInputError: 快照不合法、场景文件缺失
            或与任务摘要不一致、预处理失败。
        """

        try:
            request_snapshot = normalize_request_snapshot(task.request_snapshot)
        except RequestSnapshotError as exc:
            raise IntelligentDataGenerateInputError("智能生成请求快照不合法") from exc
        input_scenarios = await asyncio.to_thread(
            self._prepare_workspace,
            task,
            workspace,
            request_snapshot,
        )
        # _prepare_workspace 通过 json.dumps→json.loads 往返序列化保证返回值是
        # list[dict]，且 load_scenarios 内部已校验数量上限，此处只校验契约关键属性。
        if not 0 < len(input_scenarios) <= MAX_GENERATED_SCENARIOS:
            raise IntelligentDataGenerateInputError("智能生成场景预处理结果数量不一致")
        baseline_time = datetime.now(ZoneInfo("Asia/Shanghai"))
        return request_snapshot, input_scenarios, baseline_time

    async def _run_waves(
            self,
            workspace: Path,
            input_scenarios: list[dict[str, Any]],
            request_snapshot: Mapping[str, Any],
            baseline_time: datetime,
            csv_path: Path,
            tracker: _BatchTracker,
    ) -> None:
        """按输入顺序循环执行批次 wave，并把每批分析结果追加到中间CSV。

        :param workspace: 任务级隔离目录，用于存放批次lane子目录。
        :param input_scenarios: 已序列化、可切片的全任务场景数组。
        :param request_snapshot: 已规范的HEAD/BODY快照。
        :param baseline_time: 全任务共用的时间基准。
        :param csv_path: 中间CSV路径，由本函数逐批追加。
        :param tracker: 批次状态聚合器；本函数就地更新其字段。
        :raises IntelligentDataGenerateInputError: 分析结果不符合契约。
        :raises ClaudeAgentExecutionError: 预算/时间耗尽、批次写入失败、
            会话标识不一致。
        """

        input_scenario_count = len(input_scenarios)
        deadline = time.monotonic() + self.agent_client.timeout_seconds
        next_start = 0
        while next_start < input_scenario_count:
            remaining_time = deadline - time.monotonic()
            remaining_budget = (
                self.agent_client.max_budget_usd - tracker.committed_budget_usd
            )
            if remaining_time <= 0 or remaining_budget <= 0:
                raise ClaudeAgentExecutionError(
                    "智能生成任务时间或预算已耗尽",
                    public_message="智能生成任务时间或预算已耗尽",
                )
            remaining_batches = (
                input_scenario_count - next_start + self.batch_size - 1
            ) // self.batch_size
            parallel_count = min(self.max_concurrent_batches, remaining_batches)
            # 平分额度低于已见单批成本时回退串行，避免后续批次更易触及单批上限。
            if (parallel_count > 1
                    and remaining_budget / parallel_count < tracker.peak_batch_cost_usd):
                parallel_count = 1
            batch_budget = remaining_budget / parallel_count

            batch_inputs = await self._prepare_batch_inputs(
                workspace, input_scenarios, next_start,
                parallel_count, tracker,
            )

            agent_results = await self._run_batch_wave(
                [
                    (batch_workspace, tracker.lane_sessions[offset])
                    for _, _, batch_workspace, offset in batch_inputs
                ],
                baseline_time=baseline_time,
                batch_budget=batch_budget,
                remaining_time=remaining_time,
            )

            self._verify_session_continuity(batch_inputs, agent_results, tracker)

            for (start, batch_scenarios, batch_workspace, offset), agent_result in zip(
                    batch_inputs, agent_results,
            ):
                tracker.lane_sessions[offset] = agent_result.session_id
                self._record_batch_cost(tracker, agent_result, batch_budget)
                await self._persist_batch_analysis(
                    batch_workspace, batch_scenarios, start,
                    request_snapshot, csv_path, tracker,
                )
            next_start += parallel_count * self.batch_size

    async def _prepare_batch_inputs(
            self,
            workspace: Path,
            input_scenarios: list[dict[str, Any]],
            next_start: int,
            parallel_count: int,
            tracker: _BatchTracker,
    ) -> list[tuple[int, list[dict[str, Any]], Path, int]]:
        """为本波每个并行批次准备工作目录与输入JSON。

        :param workspace: 任务级工作区。
        :param input_scenarios: 全任务场景数组。
        :param next_start: 本波第一个批次在全任务中的起始索引。
        :param parallel_count: 本波并行的批次数。
        :param tracker: 批次状态聚合器，读取其 lane_sessions 以判断是否续接。
        :return: 按输入顺序的 (start, batch_scenarios, batch_workspace, offset) 元组列表。
        :raises ClaudeAgentExecutionError: 批次工作区不安全或输入写入失败。
        """

        batch_inputs: list[tuple[int, list[dict[str, Any]], Path, int]] = []
        for offset in range(parallel_count):
            start = next_start + offset * self.batch_size
            batch_scenarios = input_scenarios[start:start + self.batch_size]
            batch_workspace = workspace / f"batch-lane-{offset}"
            try:
                if tracker.lane_sessions[offset] is None:
                    batch_workspace.mkdir()
                    shutil.copyfile(
                        workspace / _SNAPSHOT_FILE_NAME,
                        batch_workspace / _SNAPSHOT_FILE_NAME,
                    )
                elif not batch_workspace.is_dir() or batch_workspace.is_symlink():
                    raise ClaudeAgentExecutionError("智能生成续接工作区不安全")
                (batch_workspace / _SCENARIO_DATA_FILE_NAME).write_text(
                    json.dumps([
                        {**scenario, "source_index": start + index}
                        for index, scenario in enumerate(batch_scenarios)
                    ], ensure_ascii=False, separators=(",", ":")),
                    encoding="utf-8",
                )
            except OSError as exc:
                raise ClaudeAgentExecutionError(
                    "智能生成批次输入写入失败",
                    public_message="智能生成批次输入写入失败",
                ) from exc
            batch_inputs.append((start, batch_scenarios, batch_workspace, offset))
        return batch_inputs

    @staticmethod
    def _verify_session_continuity(
            batch_inputs: list[tuple[int, list[dict[str, Any]], Path, int]],
            agent_results: list[ClaudeAgentRunResult],
            tracker: _BatchTracker,
    ) -> None:
        """校验本波并行槽位会话延续且互不重复，再接收任何批次结果。

        :param batch_inputs: 本波批次入参，按输入顺序。
        :param agent_results: 与 batch_inputs 对齐的 Agent 执行结果。
        :param tracker: 批次状态聚合器，读取其 lane_sessions。
        :raises ClaudeAgentExecutionError: 会话标识不一致或并行槽位间重复。
        """

        next_session_ids = []
        for (_, _, _, offset), agent_result in zip(batch_inputs, agent_results):
            session_id = agent_result.session_id
            if (not isinstance(session_id, str) or not session_id
                    or (tracker.lane_sessions[offset] is not None
                        and session_id != tracker.lane_sessions[offset])):
                raise ClaudeAgentExecutionError("Claude Agent续接会话标识不一致")
            next_session_ids.append(session_id)
        all_session_ids = next_session_ids + [
            session_id
            for offset, session_id in enumerate(tracker.lane_sessions)
            if session_id is not None and offset >= len(batch_inputs)
        ]
        if len(set(all_session_ids)) != len(all_session_ids):
            raise ClaudeAgentExecutionError("Claude Agent并行槽位会话标识重复")

    @staticmethod
    def _record_batch_cost(
            tracker: _BatchTracker,
            agent_result: ClaudeAgentRunResult,
            batch_budget: float,
    ) -> None:
        """把一批 Agent 执行的耗时、轮次、成本累加到任务级追踪器。

        :param tracker: 批次状态聚合器；本函数就地更新。
        :param agent_result: 单批 Agent 执行结果。
        :param batch_budget: 本批预留预算，用于校验实际成本上限及未报成本时的占位。
        :raises ClaudeAgentExecutionError: 成本超出本批预算或不合法。
        """

        tracker.duration_ms += agent_result.duration_ms
        tracker.num_turns += agent_result.num_turns
        cost = agent_result.total_cost_usd
        if cost is not None:
            if (not math.isfinite(cost) or cost < 0
                    or cost > batch_budget + 1e-6):
                raise ClaudeAgentExecutionError(
                    "Claude Agent批次成本超出预算",
                    public_message="智能生成任务预算校验失败",
                )
            tracker.has_cost = True
            tracker.total_cost_usd += cost
            tracker.peak_batch_cost_usd = max(tracker.peak_batch_cost_usd, cost)
        # 未返回成本时按整批预留额度记账，避免后续并发超出任务预算。
        tracker.committed_budget_usd += cost if cost is not None else batch_budget

    async def _persist_batch_analysis(
            self,
            batch_workspace: Path,
            batch_scenarios: list[dict[str, Any]],
            start: int,
            request_snapshot: Mapping[str, Any],
            csv_path: Path,
            tracker: _BatchTracker,
    ) -> None:
        """把一批 Agent 的分析结果校验并追加到任务级中间CSV，然后清理批次文件。

        :param batch_workspace: 当前批次的工作目录。
        :param batch_scenarios: 本批次的输入场景切片。
        :param start: 本批次在全任务中的起始索引。
        :param request_snapshot: 已规范的HEAD/BODY快照。
        :param csv_path: 中间CSV路径。
        :param tracker: 批次状态聚合器；本函数就地更新 total_rows 和 seen_names。
        :raises IntelligentDataGenerateInputError: 分析结果缺失、字段越界或行数超限。
        :raises ClaudeAgentExecutionError: CSV或批次清理失败。
        """

        analysis_path = batch_workspace / _ANALYSIS_FILE_NAME
        if not analysis_path.is_file() or analysis_path.is_symlink():
            raise IntelligentDataGenerateInputError(
                "Claude Agent未生成测试数据分析结果"
            )
        try:
            analysis = load_analysis_result(str(analysis_path))
            if tracker.total_rows + len(analysis) > MAX_GENERATED_SCENARIOS:
                raise OutputDataError("智能生成场景数量超出限制")
            tracker.total_rows += append_analysis_batch(
                csv_path,
                request_snapshot,
                analysis,
                set(range(start, start + len(batch_scenarios))),
                tracker.seen_names,
                {
                    start + index: scenario.get("场景类型", "")
                    for index, scenario in enumerate(batch_scenarios)
                },
            )
        except OutputDataError as exc:
            LOGGER.warning(
                "【智能生成】第{}批分析结果校验失败",
                start // self.batch_size + 1,
            )
            raise IntelligentDataGenerateInputError(
                "Claude Agent分析结果不合法，无法生成工作簿"
            ) from exc
        except OSError as exc:
            raise ClaudeAgentExecutionError(
                "智能生成CSV写入失败",
                public_message="智能生成CSV写入失败",
            ) from exc
        try:
            analysis_path.unlink()
        except OSError as exc:
            raise ClaudeAgentExecutionError("智能生成批次分析文件清理失败") from exc
        LOGGER.info(
            "【智能生成】批次已写入CSV: batch={}, rows={}, total_rows={}, "
            "duration_ms={}",
            start // self.batch_size + 1,
            len(analysis), tracker.total_rows,
            tracker.duration_ms,
        )

    async def _consolidate_and_publish(
            self,
            task: Any,
            celery_id: str,
            workspace: Path,
            request_snapshot: Mapping[str, Any],
            csv_path: Path,
            tracker: _BatchTracker,
            attempt: int,
    ) -> Dict[str, Any]:
        """CSV→XLSX→入库→发布→提交成功状态。

        :param task: 智能生成任务记录。
        :param celery_id: 当前Celery任务标识，用于约束写入权限。
        :param workspace: 任务级隔离目录。
        :param request_snapshot: 已规范的HEAD/BODY快照。
        :param csv_path: 已完成的中间CSV路径。
        :param tracker: 已完成的批次状态聚合器。
        :param attempt: 当前执行次数，写入任务摘要。
        :return: 包含任务标识、生成数量、文件信息和Agent执行摘要的结果。
        :raises IntelligentDataGenerateInputError: CSV→XLSX转换或回读校验失败。
        :raises ClaudeAgentExecutionError: 工作簿写入失败。
        """

        output_path = workspace / _OUTPUT_FILE_NAME
        try:
            await asyncio.to_thread(
                generate_output_from_csv,
                str(workspace / _SNAPSHOT_FILE_NAME),
                str(output_path),
                str(csv_path),
            )
        except OutputDataError as exc:
            LOGGER.warning(
                "【智能生成】CSV转工作簿校验失败: rows={}",
                tracker.total_rows,
            )
            raise IntelligentDataGenerateInputError(
                "Claude Agent分析结果不合法，无法生成工作簿"
            ) from exc
        except OSError as exc:
            raise ClaudeAgentExecutionError(
                "智能生成工作簿写入失败",
                public_message="智能生成工作簿写入失败",
            ) from exc
        scenarios = await asyncio.to_thread(
            read_intelligent_workbook,
            output_path,
            request_snapshot,
        )
        if len(scenarios) != tracker.total_rows:
            raise IntelligentDataGenerateInputError("智能生成工作簿场景数量与CSV不一致")
        await self.result_crud.replace_task_results(
            task.id,
            scenarios,
            expected_celery_id=celery_id,
        )
        exported = await asyncio.to_thread(
            self.export_service.publish_task_file,
            task,
            output_path,
        )

        summary: Dict[str, Any] = {
            "success": True,
            "attempt": attempt,
            "generation_mode": "intelligent",
            "file_name": exported.file_name,
            "storage_key": exported.storage_key,
            "size": exported.size,
            "error_scenario_count": 0,
            "valid_scenario_count": len(scenarios),
            "claude_agent": {
                "model": self.agent_client.model,
                "duration_ms": tracker.duration_ms,
                "num_turns": tracker.num_turns,
                "total_cost_usd": tracker.total_cost_usd if tracker.has_cost else None,
            },
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
