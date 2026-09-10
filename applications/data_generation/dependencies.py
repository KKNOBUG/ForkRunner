# -*- coding: utf-8 -*-
from dataclasses import dataclass

from applications.aotutest.services.autotest_case_crud import AutoTestCaseCrud
from applications.aotutest.services.autotest_data_source_crud import AutoTestDataSourceCrud
from applications.aotutest.services.autotest_step_crud import AutoTestStepCrud
from applications.data_generation.services.autotest_data_generate_task_crud import (
    AutoTestDataGenerateTaskCrud,
)


@dataclass
class DataGenerationApiServices:
    """数据生成接口使用的CRUD服务聚合。"""

    case_curd: AutoTestCaseCrud
    step_curd: AutoTestStepCrud
    data_source_curd: AutoTestDataSourceCrud
    data_generate_task_curd: AutoTestDataGenerateTaskCrud


async def get_data_generation_api_services() -> DataGenerationApiServices:
    """构造数据生成接口需要的CRUD服务。"""

    return DataGenerationApiServices(
        case_curd=AutoTestCaseCrud(),
        step_curd=AutoTestStepCrud(),
        data_source_curd=AutoTestDataSourceCrud(),
        data_generate_task_curd=AutoTestDataGenerateTaskCrud(),
    )
