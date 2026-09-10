# -*- coding: utf-8 -*-
'''
    数据生成任务表的CRUD和状态机服务，负责增删改查，还负责控制Celery任务的状态流转与执行权
    对应模型：AutoTestDataGenerateTaskModel
'''

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Union

from tortoise.exceptions import IntegrityError
from tortoise.expressions import Q
from tortoise.transactions import in_transaction

from applications.data_generation.models.autotest_data_generate_task_model import AutoTestDataGenerateTaskModel
from applications.data_generation.schemas.autotest_data_generate_schema import (
    AutoTestDataGenerateTaskCreate,
    AutoTestDataGenerateTaskSelect,
    AutoTestDataGenerateTaskUpdate,
)
from applications.base.services.scaffold import ScaffoldCrud
from configure import LOGGER
from core.exceptions import DataBaseStorageException, NotFoundException, ParameterException
from enums import AutoTestDataGenerateStatus


class DataGenerateTaskLeaseLostError(ParameterException):
    """当前Worker已失去任务执行权，禁止继续写入结果或终态。"""


class DataGenerateTaskFinishedError(ParameterException):
    """重复投递命中了已经结束的任务。"""


class AutoTestDataGenerateTaskCrud(ScaffoldCrud[
    AutoTestDataGenerateTaskModel,
    AutoTestDataGenerateTaskCreate,
    AutoTestDataGenerateTaskUpdate,
]):
    """
        CRUD类定义
    """

    def __init__(self):
        super().__init__(model=AutoTestDataGenerateTaskModel)

    async def get_by_id(
            self,
            task_id: int,
            on_error: bool = False,
            **kwargs,
    ) -> Optional[AutoTestDataGenerateTaskModel]:
        """
            根据ID查询任务
        """
        if not task_id:
            raise ParameterException(message="查询数据生成任务失败, 参数[task_id]不允许为空")
        task = await self.model.filter(id=task_id, **kwargs).first()
        if task is None and on_error:
            raise NotFoundException(message=f"数据生成任务[id={task_id}]不存在")
        return task

    async def create_task(
            self,
            data: Union[AutoTestDataGenerateTaskCreate, Dict[str, Any]],
    ) -> AutoTestDataGenerateTaskModel:
        """
            创建任务
        """
        task_in = data if isinstance(data, AutoTestDataGenerateTaskCreate) else AutoTestDataGenerateTaskCreate.model_validate(data)
        try:
            return await self.create(task_in.create_dict())
        except IntegrityError as exc:
            LOGGER.exception("创建数据生成任务失败")
            raise DataBaseStorageException(message="创建数据生成任务失败, 任务标识重复") from exc

    @staticmethod
    def validate_status_transition(
            current: AutoTestDataGenerateStatus,
            target: AutoTestDataGenerateStatus,
    ) -> None:
        """
            状态转换校验。
        """
        current_value = AutoTestDataGenerateStatus(current)
        target_value = AutoTestDataGenerateStatus(target)
        if current_value == target_value:
            return
        if current_value != AutoTestDataGenerateStatus.IN_PROGRESS:
            raise ParameterException(
                message=f"数据生成任务已处于终态[{current_value.value}], 不允许变更为[{target_value.value}]"
            )

    async def update_task(
            self,
            data: Union[AutoTestDataGenerateTaskUpdate, Dict[str, Any]],
            *,
            task_id: Optional[int] = None,
            task_code: Optional[str] = None,
            celery_id: Optional[str] = None,
    ) -> AutoTestDataGenerateTaskModel:
        """
            允许通过三种标识之一更新任务
        """
        selectors = sum(bool(item) for item in (task_id, task_code, celery_id))
        if selectors != 1:
            raise ParameterException(message="更新数据生成任务时必须且只能提供task_id、task_code或celery_id之一")

        raw = data if isinstance(data, dict) else data.model_dump(exclude_unset=True)
        task_in = data if isinstance(data, AutoTestDataGenerateTaskUpdate) else AutoTestDataGenerateTaskUpdate.model_validate(data)
        try:
            async with in_transaction() as connection:
                filters: Dict[str, Any] = {"state__not": 1}
                if task_id:
                    filters["id"] = task_id
                elif task_code:
                    filters["task_code"] = task_code
                else:
                    filters["celery_id"] = celery_id
                task = await self.model.filter(**filters).using_db(connection).select_for_update().first()
                if task is None:
                    raise NotFoundException(message="待更新的数据生成任务不存在")

                update_dict = task_in.update_dict()
                if task_in.task_status is not None:
                    self.validate_status_transition(task.task_status, task_in.task_status)
                    if task_in.task_status in {
                        AutoTestDataGenerateStatus.SUCCESS,
                        AutoTestDataGenerateStatus.FAILURE,
                    }:
                        update_dict["finished_time"] = datetime.now()
                if task_in.task_status == AutoTestDataGenerateStatus.SUCCESS and "error_message" not in raw:
                    update_dict["error_message"] = None

                allow_none = {"error_message"}
                update_dict = {
                    key: value
                    for key, value in update_dict.items()
                    if value is not None or (key in allow_none and key in raw)
                }
                self.fill_updated_user(update_dict)
                task.update_from_dict(update_dict)
                await task.save(using_db=connection)
                return task
        except (NotFoundException, ParameterException):
            raise
        except IntegrityError as exc:
            raise DataBaseStorageException(message="更新数据生成任务失败, Celery任务标识重复") from exc

    async def mark_failure(
            self,
            task_id: int,
            error_message: str,
            task_summary: Optional[Dict[str, Any]] = None,
    ) -> AutoTestDataGenerateTaskModel:
        """
            将任务标记为失败，用于celery正式领取任务之前发生的错误
        """
        message = str(error_message or "").strip()
        if not message:
            raise ParameterException(message="任务失败原因不能为空")
        return await self.update_task(
            {
                "task_status": AutoTestDataGenerateStatus.FAILURE,
                "error_message": message,
                "task_summary": task_summary or {},
            },
            task_id=task_id,
        )

    @staticmethod
    def _ensure_task_owner(task: AutoTestDataGenerateTaskModel, celery_id: str) -> None:
        """
            检查Worker执行权
        """
        owner = str(task.celery_id or "").strip()
        current = str(celery_id or "").strip()
        if not current or (owner and owner != current):
            raise DataGenerateTaskLeaseLostError(
                message=f"数据生成任务[id={task.id}]执行权已变更"
            )

    async def claim_attempt(
            self,
            task_id: int,
            celery_id: str,
            attempt: int,
    ) -> AutoTestDataGenerateTaskModel:
        """
            绑定Worker执行权；在celery worker真正开始执行时调用。
        """
        if attempt < 1:
            raise ParameterException(message="任务执行次数必须大于0")
        current_id = str(celery_id or "").strip()
        if not current_id:
            raise ParameterException(message="Celery任务ID不能为空")

        async with in_transaction() as connection:
            task = await self.model.filter(id=task_id, state__not=1).using_db(
                connection
            ).select_for_update().first()
            if task is None:
                raise NotFoundException(message=f"数据生成任务[id={task_id}]不存在")
            if task.task_status != AutoTestDataGenerateStatus.IN_PROGRESS:
                raise DataGenerateTaskFinishedError(
                    message=f"数据生成任务已处于终态[{task.task_status.value}]"
                )
            self._ensure_task_owner(task, current_id)

            summary = dict(task.task_summary or {})
            summary.update({
                "attempt": attempt,
                "celery_id": current_id,
                "dispatch_pending": False,
                "retry_pending": False,
            })
            task.celery_id = current_id
            task.task_summary = summary
            task.error_message = None
            # started_time记录Worker首次取得执行权的时间，重试或重复投递不得覆盖。
            if task.started_time is None:
                task.started_time = datetime.now()
            await task.save(
                using_db=connection,
                update_fields=[
                    "celery_id",
                    "task_summary",
                    "error_message",
                    "started_time",
                    "updated_time",
                ],
            )
            return task

    async def reserve_dispatch(
            self,
            task_id: int,
            celery_id: str,
    ) -> AutoTestDataGenerateTaskModel:
        """
            在发送Celery消息前预占任务，防止同一任务被重复下发。
        """
        current_id = str(celery_id or "").strip()
        if not current_id:
            raise ParameterException(message="Celery任务ID不能为空")
        async with in_transaction() as connection:
            task = await self.model.filter(id=task_id, state__not=1).using_db(
                connection
            ).select_for_update().first()
            if task is None:
                raise NotFoundException(message=f"数据生成任务[id={task_id}]不存在")
            if task.task_status != AutoTestDataGenerateStatus.IN_PROGRESS:
                raise DataGenerateTaskFinishedError(
                    message=f"数据生成任务已处于终态[{task.task_status.value}]"
                )
            if task.celery_id:
                raise ParameterException(message=f"数据生成任务[id={task_id}]已经下发")

            summary = dict(task.task_summary or {})
            summary.update({"dispatch_pending": True, "celery_id": current_id})
            task.celery_id = current_id
            task.task_summary = summary
            await task.save(
                using_db=connection,
                update_fields=["celery_id", "task_summary", "updated_time"],
            )
            return task

    async def record_retry(
            self,
            task_id: int,
            celery_id: str,
            *,
            attempt: int,
            error_message: str,
            countdown: int,
    ) -> AutoTestDataGenerateTaskModel:
        """
            任务执行遇到临时错误时调用，不改变任务状态，只更新任务摘要。
        """
        async with in_transaction() as connection:
            task = await self.model.filter(id=task_id, state__not=1).using_db(
                connection
            ).select_for_update().first()
            if task is None:
                raise NotFoundException(message=f"数据生成任务[id={task_id}]不存在")
            self._ensure_task_owner(task, celery_id)
            if task.task_status != AutoTestDataGenerateStatus.IN_PROGRESS:
                raise DataGenerateTaskLeaseLostError(message=f"数据生成任务[id={task_id}]已结束")

            summary = dict(task.task_summary or {})
            summary.update({
                "attempt": attempt,
                "retry_pending": True,
                "next_retry_seconds": countdown,
                "last_error": str(error_message or "执行失败")[:2000],
            })
            task.task_summary = summary
            await task.save(using_db=connection, update_fields=["task_summary", "updated_time"])
            return task

    async def mark_success_owned(
            self,
            task_id: int,
            celery_id: str,
            task_summary: Dict[str, Any],
    ) -> AutoTestDataGenerateTaskModel:
        """
            仅允许当前Worker将进行中任务提交为成功。
        """
        async with in_transaction() as connection:
            task = await self.model.filter(id=task_id, state__not=1).using_db(
                connection
            ).select_for_update().first()
            if task is None:
                raise NotFoundException(message=f"数据生成任务[id={task_id}]不存在")
            self._ensure_task_owner(task, celery_id)
            if task.task_status != AutoTestDataGenerateStatus.IN_PROGRESS:
                raise DataGenerateTaskLeaseLostError(message=f"数据生成任务[id={task_id}]已结束")
            if task.generated_count < 1:
                raise ParameterException(message="数据生成任务没有已保存的测试场景，不能标记成功")

            summary = dict(task_summary or {})
            summary.update({"retry_pending": False, "generated_count": task.generated_count})
            task.task_status = AutoTestDataGenerateStatus.SUCCESS
            task.task_summary = summary
            task.error_message = None
            task.finished_time = datetime.now()
            await task.save(
                using_db=connection,
                update_fields=[
                    "task_status",
                    "task_summary",
                    "error_message",
                    "finished_time",
                    "updated_time",
                ],
            )
            return task

    async def mark_failure_owned(
            self,
            task_id: int,
            celery_id: str,
            error_message: str,
            *,
            task_summary: Optional[Dict[str, Any]] = None,
    ) -> AutoTestDataGenerateTaskModel:
        """
            当前Worker标记失败，用于celery任务开始执行以后发生的失败。
        """
        message = str(error_message or "执行失败").strip()[:4000]
        async with in_transaction() as connection:
            task = await self.model.filter(id=task_id, state__not=1).using_db(
                connection
            ).select_for_update().first()
            if task is None:
                raise NotFoundException(message=f"数据生成任务[id={task_id}]不存在")
            self._ensure_task_owner(task, celery_id)
            if task.task_status == AutoTestDataGenerateStatus.FAILURE:
                return task
            if task.task_status != AutoTestDataGenerateStatus.IN_PROGRESS:
                raise DataGenerateTaskLeaseLostError(message=f"数据生成任务[id={task_id}]已结束")

            summary = dict(task.task_summary or {})
            summary.update(task_summary or {})
            summary.update({"retry_pending": False, "last_error": message})
            task.task_status = AutoTestDataGenerateStatus.FAILURE
            task.task_summary = summary
            task.error_message = message
            task.finished_time = datetime.now()
            await task.save(
                using_db=connection,
                update_fields=[
                    "task_status",
                    "task_summary",
                    "error_message",
                    "finished_time",
                    "updated_time",
                ],
            )
            return task

    async def recover_timed_out_tasks(
            self,
            *,
            timeout_seconds: int,
            limit: int = 100,
    ) -> List[int]:
        """
            把超时的进行中任务恢复为失败，处理硬超时和Worker丢失。
        """
        if timeout_seconds < 1:
            raise ParameterException(message="超时秒数必须大于0")
        if limit < 1 or limit > 1000:
            raise ParameterException(message="单次恢复数量必须在1到1000之间")
        cutoff = datetime.now() - timedelta(seconds=timeout_seconds)
        recovered: List[int] = []
        async with in_transaction() as connection:
            tasks = await self.model.filter(
                state__not=1,
                task_status=AutoTestDataGenerateStatus.IN_PROGRESS,
                updated_time__lte=cutoff,
            ).using_db(connection).select_for_update().order_by("updated_time").limit(limit)
            now = datetime.now()
            for task in tasks:
                message = f"数据生成任务超过{timeout_seconds}秒无状态更新，已由系统恢复为失败"
                summary = dict(task.task_summary or {})
                summary.update({
                    "retry_pending": False,
                    "timeout_recovered": True,
                    "timeout_seconds": timeout_seconds,
                    "last_error": message,
                })
                task.task_status = AutoTestDataGenerateStatus.FAILURE
                task.task_summary = summary
                task.error_message = message
                task.finished_time = now
                await task.save(
                    using_db=connection,
                    update_fields=[
                        "task_status",
                        "task_summary",
                        "error_message",
                        "finished_time",
                        "updated_time",
                    ],
                )
                recovered.append(task.id)
        return recovered

    async def select_tasks(
            self,
            task_in: AutoTestDataGenerateTaskSelect,
    ) -> List[AutoTestDataGenerateTaskModel]:
        """
            查询当前步骤最近五条任务
        """
        return await self.model.filter(
            state__not=1,
            step_code=task_in.step_code,
        ).order_by("-created_time", "-id").limit(5)

    async def delete_task(self, task_id: int, updated_user: Optional[str] = None) -> AutoTestDataGenerateTaskModel:
        """
            删除任务
        """
        await self.get_by_id(task_id, on_error=True, state__not=1)
        return await self.soft_delete(task_id, updated_user=updated_user)
