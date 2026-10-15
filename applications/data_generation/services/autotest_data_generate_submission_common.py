# -*- coding: utf-8 -*-

"""普通和智能生成任务共用的上传路径与投递状态处理。"""

from __future__ import annotations

import os
from typing import Any, Awaitable, Callable, Optional

from configure import LOGGER
from enums import AutoTestDataGenerateStatus


def uploaded_file_storage_key(file_path: str, upload_root: str) -> str:
    """将上传目录内的文件路径转换为任务记录使用的相对存储键。

    :param file_path: 文件保存服务返回的文件路径。
    :param upload_root: 允许保存上传文件的根目录。
    :return: 使用正斜杠分隔且不包含根目录的存储键。
    :raises ValueError: 文件路径不在上传根目录内，或指向根目录本身。
    """

    real_root = os.path.realpath(upload_root)
    real_file = os.path.realpath(file_path)
    try:
        inside_root = os.path.commonpath((real_root, real_file)) == real_root
    except ValueError as exc:
        raise ValueError("上传文件存储路径不合法") from exc
    if not inside_root or real_file == real_root:
        raise ValueError("上传文件存储路径不合法")
    return os.path.relpath(real_file, real_root).replace(os.sep, "/")


def remove_uploaded_file_safely(file_path: Optional[str], upload_root: str) -> None:
    """仅清理本次上传目录内的文件，忽略缺失或越界路径。

    :param file_path: 待清理文件的路径；为空时无需清理。
    :param upload_root: 允许删除上传文件的根目录。

    清理失败只写入不含文件路径的日志，不覆盖提交阶段的原始异常。
    """

    if not file_path:
        return
    try:
        uploaded_file_storage_key(file_path, upload_root)
        if os.path.isfile(file_path):
            os.remove(file_path)
    except ValueError:
        LOGGER.warning("跳过清理越界的生成任务上传文件路径")
    except OSError as exc:
        LOGGER.warning(
            "清理生成任务上传文件失败, "
            f"错误类型={type(exc).__name__}"
        )


async def dispatch_and_load_task(
        task_id: int,
        task_crud: Any,
        dispatcher: Callable[[int], Awaitable[Any]],
) -> tuple[Any, bool]:
    """投递任务并从数据库读取最终状态，确认投递异常是否已回写失败。

    :param task_id: 已创建的数据生成任务 ID。
    :param task_crud: 提供按 ID 读取任务记录的 CRUD 对象。
    :param dispatcher: 异步任务投递函数。
    :return: 最新任务记录，以及投递异常已回写为失败的标志。
    :raises Exception: 投递异常后任务未进入失败状态，或任务状态无法读取。
    """

    try:
        await dispatcher(task_id)
    except Exception:
        task = await task_crud.get_by_id(task_id, on_error=True, state__not=1)
        if task.task_status != AutoTestDataGenerateStatus.FAILURE:
            raise
        return task, True
    task = await task_crud.get_by_id(task_id, on_error=True, state__not=1)
    return task, False
