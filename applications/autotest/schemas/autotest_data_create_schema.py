# -*- coding: utf-8 -*-
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class AutoTestDataCreateBase(BaseModel):
    """接口文件生成记录公共字段。"""

    case_id: int = Field(..., ge=1, description="用例ID")
    case_code: Optional[str] = Field(None, max_length=64, description="用例标识代码")
    step_code: str = Field(..., max_length=64, description="步骤标识代码")
    create_status: str = Field(..., max_length=16, description="创建状态(0提交, 1生成中, 2失败, 3成功)")
    file_name: str = Field(..., max_length=255, description="接口文件存储名称")
    file_hash: str = Field(..., max_length=255, description="接口文件哈希代码")
    file_path: str = Field(..., max_length=1024, description="接口文件存储路径")
    file_desc: Optional[str] = Field(None, max_length=2048, description="接口文件场景描述")
    dataset: Optional[Dict[str, Any]] = Field(None, description="接口文件解析后的数据集")


class AutoTestDataCreateCreate(AutoTestDataCreateBase):
    """创建接口文件生成记录入参。"""

    pass


class AutoTestDataCreateUpdate(BaseModel):
    """更新接口文件生成记录入参。"""

    data_create_id: int = Field(..., ge=1, description="生成记录主键ID")
    create_status: Optional[str] = Field(None, max_length=16, description="创建状态(0提交, 1生成中, 2失败, 3成功)")
    file_path: Optional[str] = Field(None, max_length=1024, description="接口文件存储路径")
    file_desc: Optional[str] = Field(None, max_length=2048, description="接口文件场景描述")
    dataset: Optional[Dict[str, Any]] = Field(None, description="接口文件解析后的数据集")
