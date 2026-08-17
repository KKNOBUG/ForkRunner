# -*- coding: utf-8 -*-
from dataclasses import dataclass

from applications.aotutest.services.autotest_case_crud import AutoTestCaseCrud
from applications.aotutest.services.autotest_case_transfer_crud import AutoTestCaseTransferCrud
from applications.aotutest.services.autotest_data_source_crud import AutoTestDataSourceCrud
from applications.aotutest.services.autotest_detail_crud import AutoTestDetailCrud
from applications.aotutest.services.autotest_env_config_crud import AutoTestEnvConfigCrud
from applications.aotutest.services.autotest_env_crud import AutoTestEnvCrud
from applications.aotutest.services.autotest_project_crud import AutoTestProjectCrud
from applications.aotutest.services.autotest_record_crud import AutoTestRecordCrud
from applications.aotutest.services.autotest_report_crud import AutoTestReportCrud
from applications.aotutest.services.autotest_step_crud import AutoTestStepCrud
from applications.aotutest.services.autotest_tag_crud import AutoTestTagCrud
from applications.aotutest.services.autotest_task_crud import AutoTestTaskCrud


@dataclass
class AutoTestApiServices:
    """自动化测试相关CRUD服务聚合，供视图层依赖注入。"""
    case_curd: AutoTestCaseCrud
    case_transfer_curd: AutoTestCaseTransferCrud
    data_source_curd: AutoTestDataSourceCrud
    detail_curd: AutoTestDetailCrud
    env_config_curd: AutoTestEnvConfigCrud
    env_curd: AutoTestEnvCrud
    project_curd: AutoTestProjectCrud
    record_curd: AutoTestRecordCrud
    report_curd: AutoTestReportCrud
    step_curd: AutoTestStepCrud
    tag_curd: AutoTestTagCrud
    task_curd: AutoTestTaskCrud


async def get_autotest_api_services() -> AutoTestApiServices:
    """
    构造并返回自动化测试CRUD服务聚合实例。

    :return: AutoTestApiServices 实例
    """
    return AutoTestApiServices(
        case_curd=AutoTestCaseCrud(),
        case_transfer_curd=AutoTestCaseTransferCrud(),
        data_source_curd=AutoTestDataSourceCrud(),
        detail_curd=AutoTestDetailCrud(),
        env_config_curd=AutoTestEnvConfigCrud(),
        env_curd=AutoTestEnvCrud(),
        project_curd=AutoTestProjectCrud(),
        record_curd=AutoTestRecordCrud(),
        report_curd=AutoTestReportCrud(),
        step_curd=AutoTestStepCrud(),
        tag_curd=AutoTestTagCrud(),
        task_curd=AutoTestTaskCrud(),
    )
