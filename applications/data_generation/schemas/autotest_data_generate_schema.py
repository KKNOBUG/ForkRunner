# -*- coding: utf-8 -*-

"""
    测试数据生成任务的数据契约
"""

from __future__ import annotations

import os
import re
from typing import Any, ClassVar, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from applications.base.services.scaffold import UpperStr
from applications.data_generation.constants import (
    MAX_SCENE_NAME_LENGTH,
    SUPPORTED_RULE_CODES,
)
from enums import (
    AutoTestDataGenerateMode,
    AutoTestDataGenerateStatus,
    AutoTestInterfaceStyle,
)


def normalize_generated_file_name(value: str) -> str:
    """
        生成文件必须是当前目录下的xlsx文件
    """
    name = str(value or "").strip()
    if (
        not name
        or name != os.path.basename(name)
        or "\\" in name
        or not name.lower().endswith(".xlsx")
    ):
        raise ValueError("生成数据文件名必须是不含路径的.xlsx文件名")
    return name


class _AutoTestDataGenerateTaskCreateBase(BaseModel):
    """
        普通生成和智能生成共用的不可变任务输入。
    """

    model_config = ConfigDict(extra="forbid")
    document_label: ClassVar[str] = "输入文档"

    case_id: int = Field(..., ge=1, description="用例ID")
    step_id: int = Field(..., ge=1, description="步骤ID")
    step_code: str = Field(..., min_length=1, max_length=64, description="步骤标识代码")
    request_snapshot: Dict[str, Any] = Field(..., description="请求报文快照；智能生成按HEAD/BODY分区")
    interface_file_name: str = Field(..., min_length=1, max_length=255, description="接口文档原始文件名")
    interface_file_hash: str = Field(..., description="接口文档SHA-256")
    interface_storage_key: str = Field(..., min_length=1, max_length=1024, description="接口文档存储相对键")
    generated_file_name: str = Field(..., max_length=255, description="生成数据文件名")
    created_user: Optional[UpperStr] = Field(None, max_length=16, description="创建人员")

    @field_validator("interface_file_hash")
    @classmethod
    def validate_file_hash(cls, value: str) -> str:
        file_hash = str(value or "").strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", file_hash):
            raise ValueError(f"{cls.document_label}哈希值必须是64位SHA-256十六进制字符串")
        return file_hash

    @field_validator("interface_storage_key")
    @classmethod
    def validate_storage_key(cls, value: str) -> str:
        raw = str(value or "").strip().replace("\\", "/")
        if raw.startswith("/") or re.match(r"^[A-Za-z]:/", raw):
            raise ValueError(f"{cls.document_label}存储键必须是相对路径")
        key = raw
        if not key or ".." in key.split("/") or any(ord(char) < 32 for char in key):
            raise ValueError(f"{cls.document_label}存储键不合法")
        return key

    @field_validator("generated_file_name")
    @classmethod
    def validate_generated_file_name(cls, value: str) -> str:
        return normalize_generated_file_name(value)

    def create_dict(self) -> Dict[str, Any]:
        """转换成任务模型可以直接持久化的字典。"""
        return self.model_dump(exclude_none=False)


class AutoTestDataGenerateTaskCreate(_AutoTestDataGenerateTaskCreateBase):
    """普通数据生成任务创建契约。"""

    document_label: ClassVar[str] = "接口文档"
    generation_mode: AutoTestDataGenerateMode = Field(
        AutoTestDataGenerateMode.NORMAL,
        description="生成方式",
    )
    interface_style: str = Field(..., description="接口样式(esb/project/integration)")
    rule_codes: List[str] = Field(..., min_length=1, description="数据校验规则代码")
    interface_schema_snapshot: Optional[Dict[str, Any]] = Field(None, description="接口文档统一结构快照")

    @model_validator(mode="after")
    def validate_generation_mode(self):
        if self.generation_mode != AutoTestDataGenerateMode.NORMAL:
            raise ValueError("普通生成任务的生成方式必须为normal")
        return self

    @field_validator("interface_style")
    @classmethod
    def validate_interface_style(cls, value: str) -> str:
        """
            interface_style字段校验和标准化函数
        """
        try:
            style = AutoTestInterfaceStyle.normalize(value)
        except ValueError as exc:
            raise ValueError("接口样式只支持esb、project或integration") from exc
        return style.value

    @field_validator("rule_codes")
    @classmethod
    def validate_rule_codes(cls, value: List[str]) -> List[str]:
        """
            校验和标准化用户选择的数据校验规则代码
        """
        selected = {
            text
            for item in value
            if (text := str(item).strip())
        }
        unknown = selected.difference(SUPPORTED_RULE_CODES)
        if unknown:
            raise ValueError(f"存在不支持的数据校验规则: {', '.join(sorted(unknown))}")
        if not selected:
            raise ValueError("请至少选择一个数据校验点")
        #按照系统规定的顺序排列校验规则，规定了校验点的标准执行顺序
        return [code for code in SUPPORTED_RULE_CODES if code in selected]


class AutoTestIntelligentDataGenerateTaskCreate(_AutoTestDataGenerateTaskCreateBase):
    """定义智能生成任务的持久化输入。

    字段结构和默认值来自请求报文快照，不接收普通生成的接口样式、
    校验规则或接口文档快照；任务摘要用于保存本次Agent执行所选模型。
    """

    document_label: ClassVar[str] = "场景文档"
    generation_mode: AutoTestDataGenerateMode = Field(
        AutoTestDataGenerateMode.INTELLIGENT,
        description="生成方式",
    )
    interface_style: str = Field("", description="智能生成不使用接口样式")
    rule_codes: List[str] = Field(default_factory=list, description="智能生成不使用普通校验规则")
    interface_schema_snapshot: Optional[Dict[str, Any]] = Field(
        None,
        description="智能生成不使用接口文档结构快照",
    )
    task_summary: Dict[str, Any] = Field(
        default_factory=dict,
        description="智能生成任务的初始执行摘要",
    )

    @model_validator(mode="after")
    def validate_intelligent_inputs(self):
        """拒绝混入普通生成参数的智能生成任务输入。

        :return: 完成生成方式和专属字段校验后的当前任务输入。
        :raises ValueError: 生成方式错误或包含普通生成专用参数。
        """

        if self.generation_mode != AutoTestDataGenerateMode.INTELLIGENT:
            raise ValueError("智能生成任务的生成方式必须为intelligent")
        if self.interface_style or self.rule_codes or self.interface_schema_snapshot is not None:
            raise ValueError("智能生成任务不允许提供接口样式、普通校验规则或接口文档快照")
        return self


class AutoTestDataGenerateTaskUpdate(BaseModel):
    """
        更新契约：任务失败回写使用的状态字段
    """

    model_config = ConfigDict(extra="forbid")

    task_status: Optional[AutoTestDataGenerateStatus] = None
    task_summary: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None

    def update_dict(self) -> Dict[str, Any]:
        """
            排除调用者没有传入的字段
        """
        return self.model_dump(exclude_unset=True)


class AutoTestDataGenerateTaskSelect(BaseModel):
    """
        查询契约：查询当前步骤最近的数据生成任务
    """

    model_config = ConfigDict(extra="forbid")

    step_code: str = Field(..., min_length=1, max_length=64)


class AutoTestDataGenerateResultCreate(BaseModel):
    """校验入库场景名称及字段数据；普通模式使用BODY映射，智能模式使用HEAD/BODY映射。"""

    model_config = ConfigDict(extra="forbid")

    scene_name: str = Field(..., min_length=1, max_length=MAX_SCENE_NAME_LENGTH)
    scenario_data: Dict[str, str] | Dict[str, Dict[str, str]] = Field(
        default_factory=dict,
        description="普通生成使用BODY字段映射，智能生成使用HEAD/BODY分区映射",
    )

    @field_validator("scene_name")
    @classmethod
    def normalize_scene_name(cls, value: str) -> str:
        """校验场景名称包含可见内容，同时保留传入的完整文本。

        :param value: 模型生成或普通生成的场景名称。
        :return: 未经截断、压缩或去除首尾空白的原始名称。
        :raises ValueError: 名称只有空白字符。
        """
        if not value.strip():
            raise ValueError("测试场景名称不能为空")
        return value
