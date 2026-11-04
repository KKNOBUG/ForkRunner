# -*- coding: utf-8 -*-
from fastapi import APIRouter

from enums import ResourceTag
from .autotest_data_generate_view import autotest_data_generate

data_generation = APIRouter()

# tags 取 ResourceTag.desc（唯一事实源），启动契约据此绑定资源族，禁止手写中文字面量
data_generation.include_router(autotest_data_generate, prefix="/data_generate", tags=[ResourceTag.DATA_GENERATION.desc])
