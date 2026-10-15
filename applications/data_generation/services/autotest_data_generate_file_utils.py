# -*- coding: utf-8 -*-

"""数据生成任务共用的文件命名和内容摘要工具。"""

import hashlib
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


def generated_document_name(step_name: str, submitted_at: datetime) -> str:
    """按步骤名和上海时区的提交时间生成数据工作簿文件名。

    :param step_name: 当前请求步骤的名称；空值保留为空前缀。
    :param submitted_at: 任务提交时间，沿用 ``datetime.astimezone`` 的转换规则。
    :return: ``步骤名-生成数据-YYYYMMDDHHMMSS.xlsx`` 格式的文件名。
    """

    timestamp = submitted_at.astimezone(ZoneInfo("Asia/Shanghai")).strftime(
        "%Y%m%d%H%M%S"
    )
    return f"{str(step_name or '').strip()}-生成数据-{timestamp}.xlsx"


def file_sha256(file_path: str | Path) -> str:
    """分块读取已保存文件并计算完整内容的 SHA-256 摘要。

    :param file_path: 需要校验内容的本地文件路径。
    :return: 小写的 64 位十六进制 SHA-256 摘要。
    :raises OSError: 文件无法打开或读取。

    每次最多读取 1 MiB，不把整个上传文件载入内存。
    """

    digest = hashlib.sha256()
    with Path(file_path).open("rb") as document:
        for chunk in iter(lambda: document.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
