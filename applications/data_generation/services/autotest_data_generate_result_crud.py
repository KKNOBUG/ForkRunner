# -*- coding: utf-8 -*-

'''
    测试数据生成结果表的CRUD服务，对应模型表：krun_autotest_data_generate_result
'''

from __future__ import annotations

from typing import Any, Dict, List, Sequence, Union

from pydantic import ValidationError
from tortoise.exceptions import IntegrityError
from tortoise.transactions import in_transaction

from applications.data_generation.models.autotest_data_generate_result_model import AutoTestDataGenerateResultModel
from applications.data_generation.models.autotest_data_generate_task_model import AutoTestDataGenerateTaskModel
from applications.data_generation.schemas.autotest_data_generate_schema import AutoTestDataGenerateResultCreate
from applications.data_generation.services.autotest_data_generate_task_crud import DataGenerateTaskLeaseLostError
from applications.base.services.scaffold import ScaffoldCrud
from configure import LOGGER
from core.exceptions import DataBaseStorageException, NotFoundException, ParameterException
from enums import AutoTestDataGenerateStatus

MAX_BATCH_RESULT_COUNT = 10_000#单次结果数量限制


class AutoTestDataGenerateResultCrud(ScaffoldCrud[
    AutoTestDataGenerateResultModel,
    AutoTestDataGenerateResultCreate,
    AutoTestDataGenerateResultCreate,
]):
    """
        生成结果查询与事务化批量写入服务。
    """

    def __init__(self):
        super().__init__(model=AutoTestDataGenerateResultModel)

    async def list_by_task_id(self, task_id: int) -> List[AutoTestDataGenerateResultModel]:
        """ 查询任务结果 """
        if not task_id:
            raise ParameterException(message="查询生成结果失败, 参数[task_id]不允许为空")
        return await self.model.filter(task_id=task_id).order_by("id")

    @staticmethod
    def _validate_results(
            scenarios: Sequence[Union[AutoTestDataGenerateResultCreate, Dict[str, Any]]],
    ) -> List[AutoTestDataGenerateResultCreate]:
        """
            在进入数据库事务前，完整验证所有测试场景
        """
        if not scenarios:
            raise ParameterException(message="批量写入生成结果失败, 场景列表不能为空")
        if len(scenarios) > MAX_BATCH_RESULT_COUNT:
            raise ParameterException(message=f"单次最多写入{MAX_BATCH_RESULT_COUNT}个测试场景")

        validated: List[AutoTestDataGenerateResultCreate] = []
        names = set()
        try:
            for scenario in scenarios:
                item = scenario if isinstance(scenario, AutoTestDataGenerateResultCreate) else AutoTestDataGenerateResultCreate.model_validate(scenario)
                if item.scene_name in names:
                    raise ParameterException(message=f"同一任务内测试场景名称重复: {item.scene_name}")
                names.add(item.scene_name)
                validated.append(item)
        except ValidationError as exc:
            raise ParameterException(message=f"生成结果数据不合法: {exc}") from exc
        return validated

    async def replace_task_results(
            self,
            task_id: int,
            scenarios: Sequence[Union[AutoTestDataGenerateResultCreate, Dict[str, Any]]],
            *,
            batch_size: int = 500,
            expected_celery_id: str = "",
    ) -> List[AutoTestDataGenerateResultModel]:
        """
            完整替换任务结果，主要用于celery任务重试。
        """
        if not task_id:
            raise ParameterException(message="批量写入生成结果失败, 参数[task_id]不允许为空")
        if batch_size < 1 or batch_size > 2000:
            raise ParameterException(message="batch_size必须在1到2000之间")
        validated = self._validate_results(scenarios)
        instances = [
            self.model(
                task_id=task_id,
                scene_name=item.scene_name,
                scenario_data=item.scenario_data,
            )
            for item in validated
        ]

        try:
            async with in_transaction() as connection:
                task = await AutoTestDataGenerateTaskModel.filter(
                    id=task_id,
                    state__not=1,
                ).using_db(connection).select_for_update().first()
                if task is None:
                    raise NotFoundException(message=f"数据生成任务[id={task_id}]不存在")
                if task.task_status != AutoTestDataGenerateStatus.IN_PROGRESS:
                    raise ParameterException(message=f"终态任务[{task.task_status.value}]不允许替换生成结果")
                owner = str(task.celery_id or "").strip()
                expected_owner = str(expected_celery_id or "").strip()
                if expected_owner and owner != expected_owner:
                    raise DataGenerateTaskLeaseLostError(
                        message=f"数据生成任务[id={task_id}]执行权已变更"
                    )

                await self.model.filter(task_id=task_id).using_db(connection).delete()
                await self.model.bulk_create(instances, batch_size=batch_size, using_db=connection)
                task.generated_count = len(instances)
                await task.save(using_db=connection, update_fields=["generated_count", "updated_time"])
        except (NotFoundException, ParameterException):
            raise
        except IntegrityError as exc:
            LOGGER.exception("批量写入数据生成结果失败")
            raise DataBaseStorageException(message="批量写入数据生成结果失败, 场景名称重复") from exc
        except Exception as exc:
            LOGGER.exception("批量写入数据生成结果异常")
            raise DataBaseStorageException(message="批量写入数据生成结果失败") from exc
        return instances
