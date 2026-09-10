# -*- coding: utf-8 -*-

"""
说明：
    测试数据生成任务的数据契约
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator

from applications.base.services.scaffold import UpperStr
from applications.data_generation.constants import SUPPORTED_RULE_CODES
from enums import AutoTestDataGenerateStatus

#支持的四种数据校验规则代码，0：必填；1：长度；2:枚举；3.小数边界;不仅判断是否合法，还决定最终规则顺序


def normalize_generated_file_name(value: str) -> str:
    """生成文件必须是当前目录下的xlsx文件。"""
    name = str(value or "").strip()
    if (
        not name
        or name != os.path.basename(name)
        or "\\" in name
        or not name.lower().endswith(".xlsx")
    ):
        raise ValueError("生成数据文件名必须是不含路径的.xlsx文件名")
    return name


class AutoTestDataGenerateTaskCreate(BaseModel):
    """创建生成任务时的输入契约，对应任务表的任务输入快照"""

    model_config = ConfigDict(extra="forbid")

    case_id: int = Field(..., ge=1, description="用例ID")
    step_id: int = Field(..., ge=1, description="步骤ID")
    step_code: str = Field(..., min_length=1, max_length=64, description="步骤标识代码")
    interface_style: str = Field(..., description="接口样式(esb/project)")
    rule_codes: List[str] = Field(..., min_length=1, description="数据校验规则代码")
    request_snapshot: Dict[str, Any] = Field(..., description="请求报文展平快照")
    interface_schema_snapshot: Optional[Dict[str, Any]] = Field(None, description="接口文档统一结构快照")
    interface_file_name: str = Field(..., min_length=1, max_length=255, description="接口文档原始文件名")
    interface_file_hash: str = Field(..., description="接口文档SHA-256")
    interface_storage_key: str = Field(..., min_length=1, max_length=1024, description="接口文档存储相对键")
    generated_file_name: str = Field(..., max_length=255, description="生成数据文件名")
    created_user: Optional[UpperStr] = Field(None, max_length=16, description="创建人员")

    @field_validator("interface_style")
    @classmethod
    def validate_interface_style(cls, value: str) -> str:
        """
            interface_style字段校验器，
        """
        style = str(value or "").strip().lower()
        if style not in {"esb", "project"}:
            raise ValueError("接口样式只支持esb或project")
        return style

    @field_validator("rule_codes")
    @classmethod
    def validate_rule_codes(cls, value: List[str]) -> List[str]:
        selected = {str(item).strip() for item in value if str(item).strip()}
        unknown = selected.difference(SUPPORTED_RULE_CODES)
        if unknown:
            raise ValueError(f"存在不支持的数据校验规则: {', '.join(sorted(unknown))}")
        if not selected:
            raise ValueError("请至少选择一个数据校验点")
        return [code for code in SUPPORTED_RULE_CODES if code in selected]

    @field_validator("interface_file_hash")
    @classmethod
    def validate_file_hash(cls, value: str) -> str:
        file_hash = str(value or "").strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", file_hash):
            raise ValueError("接口文档哈希值必须是64位SHA-256十六进制字符串")
        return file_hash

    @field_validator("interface_storage_key")
    @classmethod
    def validate_storage_key(cls, value: str) -> str:
        raw = str(value or "").strip().replace("\\", "/")
        if raw.startswith("/") or re.match(r"^[A-Za-z]:/", raw):
            raise ValueError("接口文档存储键必须是相对路径")
        key = raw
        if not key or ".." in key.split("/") or any(ord(char) < 32 for char in key):
            raise ValueError("接口文档存储键不合法")
        return key

    @field_validator("generated_file_name")
    @classmethod
    def validate_generated_file_name(cls, value: str) -> str:
        return normalize_generated_file_name(value)

    def create_dict(self) -> Dict[str, Any]:
        return self.model_dump(exclude_none=False)


class AutoTestDataGenerateTaskUpdate(BaseModel):
    """任务失败回写使用的状态字段。"""

    model_config = ConfigDict(extra="forbid")

    task_status: Optional[AutoTestDataGenerateStatus] = None
    task_summary: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None

    def update_dict(self) -> Dict[str, Any]:
        """排除调用者没有传入的字段"""
        return self.model_dump(exclude_unset=True)


class AutoTestDataGenerateTaskSelect(BaseModel):
    """查询当前步骤最近的数据生成任务。"""

    model_config = ConfigDict(extra="forbid")

    step_code: str = Field(..., min_length=1, max_length=64)


class AutoTestDataGenerateResultCreate(BaseModel):
    """
        单条场景的生成结果
    """

    model_config = ConfigDict(extra="forbid")

    scene_name: str = Field(..., min_length=1, max_length=255)
    scenario_data: Dict[str, str] = Field(default_factory=dict)

    @field_validator("scene_name")
    @classmethod
    def normalize_scene_name(cls, value: str) -> str:
        name = str(value or "").strip()
        if not name:
            raise ValueError("测试场景名称不能为空")
        return name
