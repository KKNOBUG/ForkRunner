# -*- coding: utf-8 -*-
"""
@Author  : yangkai
@Email   : 807440781@qq.com
@Project : Krun
@Module  : autotest_env_view
@DateTime: 2026/1/2 21:21
"""
import traceback
from typing import Optional

from fastapi import APIRouter, Query, Depends, Body

from applications.aotutest.dependencies import AutoTestApiServices, get_autotest_api_services
from applications.aotutest.schemas.autotest_env_schema import (
    EnvListQuery,
    EnvCreate,
    EnvEditRequest,
    EnvDeleteRequest, AutoTestApiEnvConfigQueryByProjectsIn,
)
from configure import LOGGER
from core.exceptions import (
    NotFoundException,
    ParameterException,
    DataAlreadyExistsException,
)
from core.responses import (
    SuccessResponse,
    FailureResponse,
    ParameterResponse,
    NotFoundResponse,
    DataAlreadyExistsResponse,
)
from services import get_current_username

autotest_env = APIRouter()


@autotest_env.post("/list", summary="获取环境列表", response_model=None)
async def get_env_list(
        data: EnvListQuery,
        services: AutoTestApiServices = Depends(get_autotest_api_services),
):
    """
    按节点类型/应用聚合环境名称。

    :param data: 可选应用ID列表
    :param services: 自动化测试CRUD依赖聚合
    :return: 统一HTTP响应
    """
    try:
        result_data = await services.env_enum_curd.get_envs(data.project_id)
        return SuccessResponse(message="查询成功", data=result_data)
    except ParameterException as e:
        return ParameterResponse(message=str(e.message))
    except Exception as e:
        LOGGER.error(f"获取环境列表失败: {e}\n{traceback.format_exc()}")
        return FailureResponse(message=f"查询失败: {e}")


@autotest_env.get("/page", summary="环境分页列表")
async def get_env_page(
        project_id: Optional[int] = Query(None, description="应用ID", ge=1),
        env_name: Optional[str] = Query(None, description="环境名称"),
        env_type: Optional[int] = Query(None, description="节点类型"),
        ip: Optional[str] = Query(None, description="IP地址"),
        page: int = Query(1, description="页码", ge=1),
        page_size: int = Query(10, description="每页条数", ge=1, le=100),
        services: AutoTestApiServices = Depends(get_autotest_api_services),
):
    """
    按应用/环境/节点类型聚合后分页查询。

    :return: 统一HTTP响应
    """
    try:
        total, data = await services.env_enum_curd.get_env_search_list(
            project_id=project_id,
            env_name=env_name,
            env_type=env_type,
            ip=ip,
            page=page,
            page_size=page_size,
        )
        return SuccessResponse(data=data, total=total, message="查询成功")
    except ParameterException as e:
        return ParameterResponse(message=str(e.message))
    except Exception as e:
        LOGGER.error(f"环境分页列表失败: {e}\n{traceback.format_exc()}")
        return FailureResponse(message=f"查询失败, 错误描述: {e}")


@autotest_env.post("/create", summary="新增环境")
async def create_env(
        data: EnvCreate = Body(..., description="环境信息"),
        services: AutoTestApiServices = Depends(get_autotest_api_services),
):
    """
    新增环境（确保环境枚举存在）。

    :param data: 环境入参
    :param services: 自动化测试CRUD依赖聚合
    :return: 统一HTTP响应
    """
    try:
        env_info = await services.env_enum_curd.create_automation_env(data, get_current_username() or "system")
        return SuccessResponse(message="新增环境成功", data=env_info, total=1)
    except DataAlreadyExistsException as e:
        return DataAlreadyExistsResponse(message=str(e.message))
    except NotFoundException as e:
        return NotFoundResponse(message=str(e.message))
    except ParameterException as e:
        return ParameterResponse(message=str(e.message))
    except Exception as e:
        LOGGER.error(f"新增环境失败: {e}\n{traceback.format_exc()}")
        return FailureResponse(message=f"新增环境失败, 错误描述: {e}")


@autotest_env.post("/update", summary="编辑环境")
async def update_env(
        data: EnvEditRequest = Body(..., description="环境信息"),
        services: AutoTestApiServices = Depends(get_autotest_api_services),
):
    """
    编辑环境名称/应用归属，并级联同类型配置。

    :return: 统一HTTP响应
    """
    try:
        env_info = await services.env_enum_curd.update_automation_env(data, get_current_username() or "system")
        return SuccessResponse(message="更新环境成功", data=env_info, total=1)
    except DataAlreadyExistsException as e:
        return DataAlreadyExistsResponse(message=str(e.message))
    except NotFoundException as e:
        return NotFoundResponse(message=str(e.message))
    except ParameterException as e:
        return ParameterResponse(message=str(e.message))
    except Exception as e:
        LOGGER.error(f"更新环境失败: {e}\n{traceback.format_exc()}")
        return FailureResponse(message=f"更新环境失败, 错误描述: {e}")


@autotest_env.post("/delete", summary="删除环境")
async def delete_env(
        data: EnvDeleteRequest = Body(..., description="环境信息"),
        services: AutoTestApiServices = Depends(get_autotest_api_services),
):
    """
    软删指定环境下某节点类型的全部配置。

    :return: 统一HTTP响应
    """
    try:
        result = await services.env_enum_curd.delete_automation_env(
            data.id, data.env_type, get_current_username() or "system"
        )
        return SuccessResponse(message="删除环境成功", data=result, total=1)
    except NotFoundException as e:
        return NotFoundResponse(message=str(e.message))
    except ParameterException as e:
        return ParameterResponse(message=str(e.message))
    except Exception as e:
        LOGGER.error(f"删除环境失败: {e}\n{traceback.format_exc()}")
        return FailureResponse(message=f"删除环境失败, 错误描述: {e}")


@autotest_env.get("/get_all_app", summary="获取全部应用")
async def get_all_app(
        page: int = Query(1, ge=1, description="页码"),
        page_size: int = Query(10000, ge=1, description="每页条数"),
        services: AutoTestApiServices = Depends(get_autotest_api_services),
):
    """
    获取全部启用应用列表。

    :return: 统一HTTP响应；data 元素含 id/project_name/project_mark
    """
    try:
        data, total = await services.project_curd.get_all_project(page, page_size)
        LOGGER.info(f"获取所有应用成功, 结果明细: {total}")
        return SuccessResponse(message="查询成功", data=data, total=total)
    except Exception as e:
        LOGGER.error(f"获取所有应用失败，异常描述: {e}\n{traceback.format_exc()}")
        return FailureResponse(message=f"查询失败, 异常描述: {e}")


@autotest_env.post("/query", summary="API自动化测试-按应用列表查询环境配置并分类")
async def query_classify_env_config(
        env_config_in: AutoTestApiEnvConfigQueryByProjectsIn = Body(..., description="应用ID列表"),
        services: AutoTestApiServices = Depends(get_autotest_api_services),
):
    """
    按应用ID列表查询环境配置并分类返回。

    :param env_config_in: 含 project_ids 的查询入参
    :param services: 自动化测试CRUD依赖聚合
    :return: 统一HTTP响应
    """
    try:
        data = await services.env_config_curd.query_classified_by_project_ids(
            project_ids=env_config_in.project_ids,
        )
        total_configs: int = sum(
            len(names)
            for envs in data.values()
            for buckets in envs.values()
            for names in buckets.values()
        )
        LOGGER.info(
            f"按应用列表查询环境配置并分类成功, project_ids={env_config_in.project_ids}, 配置条数: {total_configs}"
        )
        return SuccessResponse(message="查询成功", data=data, total=total_configs)
    except ParameterException as e:
        return ParameterResponse(message=str(e.message))
    except Exception as e:
        LOGGER.error(f"按应用列表查询环境配置并分类失败，异常描述: {e}\n{traceback.format_exc()}")
        return FailureResponse(message=f"查询失败, 异常描述: {e}")
