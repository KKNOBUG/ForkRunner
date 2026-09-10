# -*- coding: utf-8 -*-
from __future__ import annotations

import uuid
from typing import Any, Dict

from billiard.exceptions import SoftTimeLimitExceeded

from applications.data_generation.services.autotest_data_generate_service import TestDataGenerationError
from applications.data_generation.services.autotest_data_generate_task_crud import (
    AutoTestDataGenerateTaskCrud,
    DataGenerateTaskFinishedError,
    DataGenerateTaskLeaseLostError,
)
from applications.data_generation.services.autotest_data_generate_task_service import (
    AutoTestDataGenerateTaskService,
    DataGenerateTaskInputError,
)
from applications.data_generation.services.autotest_ai_enum_extraction_client import (
    AIEnumExtractionError,
)
from applications.data_generation.services.autotest_project_enum_extraction_service import (
    ProjectEnumExtractionInputError,
)
from applications.data_generation.services.autotest_project_interface_workbook_service import (
    ProjectInterfaceWorkbookError,
)
from celery_scheduler.celery_base import run_async
from celery_scheduler.celery_worker import celery
from configure import LOGGER
from core.exceptions import NotFoundException, ParameterException

DATA_GENERATE_TASK_NAME = (
    "celery_scheduler.tasks.task_autotest_data_generate.generate_test_data_task"
)
DATA_GENERATE_RECOVERY_TASK_NAME = (
    "celery_scheduler.tasks.task_autotest_data_generate.recover_timed_out_data_generate_tasks"
)
DATA_GENERATE_MAX_RETRIES = 2
DATA_GENERATE_SOFT_TIME_LIMIT = 300
DATA_GENERATE_TIME_LIMIT = 330
DATA_GENERATE_RECOVERY_TIMEOUT = 600


def retry_countdown(retries: int) -> int:
    """短指数退避，避免数据库或文件系统瞬时异常形成重试风暴。"""
    retry_index = max(0, int(retries))
    return min(60, 15 * (2 ** retry_index))


def is_permanent_generation_error(exc: BaseException) -> bool:
    """业务输入错误不会因重试而恢复。"""
    return isinstance(exc, (
        DataGenerateTaskInputError,
        TestDataGenerationError,
        AIEnumExtractionError,
        ProjectEnumExtractionInputError,
        ProjectInterfaceWorkbookError,
        NotFoundException,
        ParameterException,
    ))


def generation_error_message(exc: BaseException) -> str:
    """生成可回写的安全错误文案，避免把基础设施细节暴露给前端。"""
    if isinstance(exc, SoftTimeLimitExceeded):
        return f"数据生成任务执行超过{DATA_GENERATE_SOFT_TIME_LIMIT}秒，已中止本次尝试"
    if isinstance(exc, AIEnumExtractionError):
        return str(exc).strip()[:4000]
    if is_permanent_generation_error(exc):
        return str(exc or "数据生成任务输入不合法").strip()[:4000]
    return f"数据生成任务执行异常({type(exc).__name__})"


async def dispatch_data_generate_task(task_id: int):
    """预占业务任务后下发Celery消息；发布失败立即回写失败。"""
    celery_id = str(uuid.uuid4())
    task_crud = AutoTestDataGenerateTaskCrud()
    await task_crud.reserve_dispatch(task_id, celery_id)
    try:
        return generate_test_data_task.apply_async(
            args=[task_id],
            task_id=celery_id,
            __task_id=task_id,
        )
    except Exception as exc:
        await task_crud.mark_failure_owned(
            task_id,
            celery_id,
            f"数据生成任务投递失败({type(exc).__name__})",
            task_summary={"success": False, "error_type": type(exc).__name__},
        )
        raise


async def _mark_failure_safely(
        task_id: int,
        celery_id: str,
        exc: BaseException,
        *,
        attempt: int,
) -> None:
    message = generation_error_message(exc)
    summary: Dict[str, Any] = {
        "success": False,
        "attempt": attempt,
        "error_type": type(exc).__name__,
    }
    if isinstance(exc, AIEnumExtractionError) and (
            exc.attempted_models or exc.failure_types
    ):
        summary["ai_failover"] = {
            "attempted_models": list(exc.attempted_models),
            "failure_types": list(exc.failure_types),
        }
    try:
        await AutoTestDataGenerateTaskCrud().mark_failure_owned(
            task_id,
            celery_id,
            message,
            task_summary=summary,
        )
    except (DataGenerateTaskLeaseLostError, DataGenerateTaskFinishedError):
        LOGGER.warning(
            f"【数据生成任务】失败回写已忽略，任务执行权已变更: task_id={task_id}, celery_id={celery_id}"
        )
    except Exception as write_exc:
        LOGGER.error(
            f"【数据生成任务】失败状态回写异常: task_id={task_id}, celery_id={celery_id}, "
            f"错误类型={type(write_exc).__name__}"
        )


@celery.task(
    bind=True,
    name=DATA_GENERATE_TASK_NAME,
    max_retries=DATA_GENERATE_MAX_RETRIES,
    soft_time_limit=DATA_GENERATE_SOFT_TIME_LIMIT,
    time_limit=DATA_GENERATE_TIME_LIMIT,
    acks_late=True,
    reject_on_worker_lost=True,
)
def generate_test_data_task(self, task_id: int) -> Dict[str, Any]:
    """执行测试数据生成；瞬时失败重试，最终失败必须回写业务任务。"""
    celery_id = str(self.request.id or "").strip()
    attempt = int(self.request.retries or 0) + 1
    try:
        result = run_async(
            AutoTestDataGenerateTaskService().execute(
                task_id,
                celery_id,
                attempt=attempt,
            )
        )
        LOGGER.info(
            f"【数据生成任务】执行成功: task_id={task_id}, celery_id={celery_id}, "
            f"attempt={attempt}, generated_count={result.get('generated_count')}"
        )
        return result
    except (DataGenerateTaskLeaseLostError, DataGenerateTaskFinishedError) as exc:
        LOGGER.warning(
            f"【数据生成任务】忽略重复或过期消息: task_id={task_id}, celery_id={celery_id}, "
            f"错误描述={exc}"
        )
        return {"success": True, "ignored": True, "task_id": task_id}
    except Exception as exc:
        can_retry = (
            not is_permanent_generation_error(exc)
            and int(self.request.retries or 0) < DATA_GENERATE_MAX_RETRIES
        )
        if can_retry:
            countdown = retry_countdown(int(self.request.retries or 0))
            try:
                run_async(
                    AutoTestDataGenerateTaskCrud().record_retry(
                        task_id,
                        celery_id,
                        attempt=attempt + 1,
                        error_message=generation_error_message(exc),
                        countdown=countdown,
                    )
                )
            except (DataGenerateTaskLeaseLostError, DataGenerateTaskFinishedError):
                return {"success": True, "ignored": True, "task_id": task_id}
            LOGGER.warning(
                f"【数据生成任务】准备重试: task_id={task_id}, celery_id={celery_id}, "
                f"next_attempt={attempt + 1}, countdown={countdown}, error_type={type(exc).__name__}"
            )
            raise self.retry(exc=exc, countdown=countdown, max_retries=DATA_GENERATE_MAX_RETRIES)

        run_async(_mark_failure_safely(task_id, celery_id, exc, attempt=attempt))
        LOGGER.error(
            f"【数据生成任务】执行失败: task_id={task_id}, celery_id={celery_id}, "
            f"attempt={attempt}, error_type={type(exc).__name__}"
        )
        raise


async def _recover_timed_out_tasks_impl() -> Dict[str, Any]:
    recovered_ids = await AutoTestDataGenerateTaskCrud().recover_timed_out_tasks(
        timeout_seconds=DATA_GENERATE_RECOVERY_TIMEOUT,
    )
    if recovered_ids:
        LOGGER.warning(
            f"【数据生成任务】超时状态恢复完成: count={len(recovered_ids)}, task_ids={recovered_ids}"
        )
    return {"success": True, "recovered": len(recovered_ids), "task_ids": recovered_ids}


@celery.task(name=DATA_GENERATE_RECOVERY_TASK_NAME)
def recover_timed_out_data_generate_tasks() -> Dict[str, Any]:
    """Beat入口，恢复硬超时或Worker丢失后遗留的进行中任务。"""
    return run_async(_recover_timed_out_tasks_impl())
