# -*- coding: utf-8 -*-
from fastapi import APIRouter

from .autotest_data_generate_view import autotest_data_generate

data_generation = APIRouter()

# tags 采用「一级目录:二级模块」，与侧边栏菜单对齐，便于角色权限按模块制定规则
data_generation.include_router(
    autotest_data_generate,
    prefix="/data_generate",
    tags=["自动化测试:数据生成"],
)
