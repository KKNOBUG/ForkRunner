# -*- coding: utf-8 -*-

"""
    接口文档文本->AI->本地校验器
"""

from __future__ import annotations

import re#正则表达式
from typing import List, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from applications.data_generation.constants import (
    MAX_ENUM_NORMALIZED_TEXT_LENGTH,
    MAX_ENUM_VALUES_PER_FIELD,
)

"""提取结果的三种状态"""
ProjectEnumExtractionStatus = Literal["extracted", "not_found", "ambiguous"]

#备注字段的最大长度
MAX_ENUM_REMARK_LENGTH = 20_000


class ProjectEnumExtractionFieldInput(BaseModel):
    """
        请求侧，发送给AI的单字段输入。
    """
    model_config = ConfigDict(extra="forbid")

    #字段在Excel表中的原行号
    source_row: int = Field(..., ge=1)
    #接口文档的字段名
    field_name: str = Field(..., min_length=1, max_length=255)
    #原始备注
    remark: str = Field(..., min_length=1, max_length=MAX_ENUM_REMARK_LENGTH)

    @field_validator("field_name", "remark")
    @classmethod
    def strip_non_empty_text(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("字段名和备注不能为空")
        return text


class ProjectEnumExtractionItem(BaseModel):
    """
        响应侧：接收并校验AI返回的单个枚举键值对。
    """
    model_config = ConfigDict(extra="forbid")

    #可以理解为提取出枚举值对应的键
    value: str = Field(..., min_length=1, max_length=128)
    #提取出枚举值对应的值
    description: str = Field(..., min_length=1, max_length=1_000)

    @field_validator("value")
    @classmethod
    def validate_value(cls, value: str) -> str:
        text = str(value or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9]+", text):
            raise ValueError("枚举键只能包含ASCII数字和英文字母")
        return text

    @field_validator("description")
    @classmethod
    def validate_description(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("枚举描述不能为空")
        if "\r" in text or "\n" in text:
            raise ValueError("枚举描述不能跨行")
        return text


class _ProjectEnumExtractionCandidateBase(BaseModel):
    """AI原始结果与本地校验结果共用的字段。"""

    model_config = ConfigDict(extra="forbid")

    source_row: int = Field(..., ge=1)
    field_name: str = Field(..., min_length=1, max_length=255)
    status: ProjectEnumExtractionStatus
    items: List[ProjectEnumExtractionItem] = Field(
        default_factory=list,
        max_length=MAX_ENUM_VALUES_PER_FIELD,
    )
    reason: Optional[str] = Field(None, max_length=1_000)

    @field_validator("field_name")
    @classmethod
    def strip_field_name(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("字段名不能为空")
        return text

    @field_validator("reason")
    @classmethod
    def strip_reason(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return str(value).strip() or None

    @model_validator(mode="after")
    def validate_status_items(self) -> "_ProjectEnumExtractionCandidateBase":
        if self.status == "extracted":
            if len(self.items) < 2:
                raise ValueError("枚举提取成功时至少需要两个枚举项")
        elif self.items:
            raise ValueError("未提取或存在歧义时不能返回枚举项")
        return self


class ProjectEnumExtractionRawCandidate(_ProjectEnumExtractionCandidateBase):
    """AI返回的原始判断；标准文本由本地校验后生成。"""


class ProjectEnumExtractionCandidate(_ProjectEnumExtractionCandidateBase):
    """
        AI结果通过本地证据校验后形成的最终判断。
    """
    # 标准化枚举文本
    normalized_text: Optional[str] = Field(None, max_length=MAX_ENUM_NORMALIZED_TEXT_LENGTH)

    @field_validator("normalized_text")
    @classmethod
    def strip_normalized_text(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return str(value).strip() or None

    @model_validator(mode="after")
    def validate_status_payload(self) -> "ProjectEnumExtractionCandidate":
        """
            检查最终提取状态是否与本地生成的标准文本一致。
        """
        if self.status == "extracted" and not self.normalized_text:
            raise ValueError("枚举提取成功时标准文本不能为空")
        if self.status != "extracted" and self.normalized_text is not None:
            raise ValueError("未提取或存在歧义时不能返回标准文本")
        return self
