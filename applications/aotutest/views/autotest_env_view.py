# -*- coding: utf-8 -*-
"""
@Author  : yangkai
@Email   : 807440781@qq.com
@Project : Krun
@Module  : autotest_env_view
@DateTime: 2026/1/2 21:21
"""
import traceback
from typing import Optional, List, Dict, Any

from fastapi import APIRouter, Body, Query, Depends
from tortoise.expressions import Q

from applications.aotutest.dependencies import AutoTestApiServices, get_autotest_api_services
from applications.aotutest.schemas.autotest_env_schema import (
    AutoTestApiEnvCreate,
    AutoTestApiEnvUpdate,
    AutoTestApiEnvSelect,
    AutoTestApiEnvDelete,
    EnvListQuery,
    EnvCreate,
    EnvEditRequest,
    EnvDeleteRequest,
)
from configure import LOGGER
from core.exceptions import (
    NotFoundException,
    ParameterException,
    DataBaseStorageException,
    DataAlreadyExistsException,
)
from core.responses import (
    SuccessResponse,
    FailureResponse,
    ParameterResponse,
    NotFoundResponse,
    DataBaseStorageResponse,
    DataAlreadyExistsResponse,
)

autotest_env = APIRouter()


@autotest_env.post("/create", summary="新增环境")
async def create_env_info(
        env_in: AutoTestApiEnvCreate = Body(..., description="环境信息"),
        services: AutoTestApiServices = Depends(get_autotest_api_services),
):
    """
    新增环境。

    :param env_in: 环境入参
    :param services: 自动化测试CRUD依赖聚合
    :return: 统一HTTP响应
    """
    try:
        instance = await services.env_enum_curd.create_env(env_in=env_in)
        data = await instance.to_dict(
            exclude_fields={
                "state",
                "created_user", "updated_user",
                "created_time", "updated_time",
                "reserve_1", "reserve_2", "reserve_3"
            },
            replace_fields={"id": "env_id"}
        )
        LOGGER.info(f"新增环境成功, 结果明细: {data}")
        return SuccessResponse(message="新增成功", data=data, total=1)
    except DataBaseStorageException as e:
        return DataBaseStorageResponse(message=str(e.message))
    except Exception as e:
        LOGGER.error(f"新增环境失败，异常描述: {e}\n{traceback.format_exc()}")
        return FailureResponse(message=f"新增失败，异常描述: {e}")


@autotest_env.delete("/delete", summary="删除环境", description="根据id或code删除环境信息")
async def delete_env_info(
        env_id: Optional[int] = Query(None, description="环境ID"),
        env_code: Optional[str] = Query(None, description="环境标识代码"),
        services: AutoTestApiServices = Depends(get_autotest_api_services),
):
    """
    根据id或code删除环境。

    :param env_id: 环境主键ID
    :param env_code: 环境业务标识
    :param services: 自动化测试CRUD依赖聚合
    :return: 统一HTTP响应
    """
    try:
        instance = await services.env_enum_curd.delete_env(env_id=env_id, env_code=env_code)
        data = await instance.to_dict(
            exclude_fields={
                "state",
                "created_user", "updated_user",
                "created_time", "updated_time",
                "reserve_1", "reserve_2", "reserve_3"
            },
            replace_fields={"id": "env_id"}
        )
        LOGGER.info(f"根据id或code删除环境成功, 结果明细: {data}")
        return SuccessResponse(message="删除成功", data=data, total=1)
    except NotFoundException as e:
        return NotFoundResponse(message=str(e.message))
    except ParameterException as e:
        return ParameterResponse(message=str(e.message))
    except Exception as e:
        LOGGER.error(f"根据id或code删除环境失败，异常描述: {e}\n{traceback.format_exc()}")
        return FailureResponse(message=f"删除失败，异常描述: {e}")


@autotest_env.post("/delete", summary="批量删除环境", description="根据id或code列表删除环境信息")
async def delete_env_batch(
        env_in: AutoTestApiEnvDelete = Body(..., description="环境信息"),
        services: AutoTestApiServices = Depends(get_autotest_api_services),
):
    """
    根据id或code列表删除环境。

    :param env_in: 环境入参
    :param services: 自动化测试CRUD依赖聚合
    :return: 统一HTTP响应
    """
    try:
        count = await services.env_enum_curd.delete_envs(env_in=env_in)
        LOGGER.info(f"根据id或code列表删除环境成功, 数量: {count}")
        return SuccessResponse(message="删除成功", data={"affected": count}, total=count)
    except ParameterException as e:
        return ParameterResponse(message=str(e.message))
    except Exception as e:
        LOGGER.error(f"根据id或code列表删除环境失败，异常描述: {e}\n{traceback.format_exc()}")
        return FailureResponse(message=f"删除失败，异常描述: {e}")


@autotest_env.post("/update", summary="更新环境", description="根据id或code更新环境信息")
async def update_env_info(
        env_in: AutoTestApiEnvUpdate = Body(..., description="环境信息"),
        services: AutoTestApiServices = Depends(get_autotest_api_services),
):
    """
    根据id或code更新环境。

    :param env_in: 环境入参
    :param services: 自动化测试CRUD依赖聚合
    :return: 统一HTTP响应
    """
    try:
        instance = await services.env_enum_curd.update_env(env_in=env_in)
        data = await instance.to_dict(
            exclude_fields={
                "state",
                "created_user", "updated_user",
                "created_time", "updated_time",
                "reserve_1", "reserve_2", "reserve_3"
            },
            replace_fields={"id": "env_id"}
        )
        LOGGER.info(f"根据id或code更新环境成功, 结果明细: {data}")
        return SuccessResponse(message="更新成功", data=data, total=1)
    except NotFoundException as e:
        return NotFoundResponse(message=str(e.message))
    except ParameterException as e:
        return ParameterResponse(message=str(e.message))
    except DataBaseStorageException as e:
        return DataBaseStorageResponse(message=str(e.message))
    except Exception as e:
        LOGGER.error(f"根据id或code更新环境失败，异常描述: {e}\n{traceback.format_exc()}")
        return FailureResponse(message=f"更新失败，异常描述: {e}")


@autotest_env.get("/get", summary="查询环境", description="根据id或code查询环境信息")
async def get_env_info(
        env_id: Optional[int] = Query(None, description="环境ID"),
        env_code: Optional[str] = Query(None, description="环境标识代码"),
        services: AutoTestApiServices = Depends(get_autotest_api_services),
):
    """
    根据id或code查询环境。

    :param env_id: 环境主键ID
    :param env_code: 环境业务标识
    :param services: 自动化测试CRUD依赖聚合
    :return: 统一HTTP响应
    """
    try:
        if env_id:
            instance = await services.env_enum_curd.get_by_id(env_id=env_id, on_error=True, state__not=1)
        else:
            instance = await services.env_enum_curd.get_by_code(env_code=env_code, on_error=True, state__not=1)
        data = await instance.to_dict(
            exclude_fields={
                "state",
                "created_user", "updated_user",
                "created_time", "updated_time",
                "reserve_1", "reserve_2", "reserve_3"
            },
            replace_fields={"id": "env_id"}
        )
        LOGGER.info(f"根据id或code查询环境成功, 结果明细: {data}")
        return SuccessResponse(message="查询成功", data=data, total=1)
    except NotFoundException as e:
        return NotFoundResponse(message=str(e.message))
    except ParameterException as e:
        return ParameterResponse(message=str(e.message))
    except Exception as e:
        LOGGER.error(f"根据id或code查询环境失败，异常描述: {e}\n{traceback.format_exc()}")
        return FailureResponse(message=f"查询失败，异常描述: {e}")


@autotest_env.get("/get_names", summary="查询环境名称", description="查询去重后的环境名称列表")
async def get_env_name_list(
        services: AutoTestApiServices = Depends(get_autotest_api_services),
):
    """
    查询环境名称(去重)。

    :param services: 自动化测试CRUD依赖聚合
    :return: 统一HTTP响应
    """
    try:
        names: List[str] = await services.env_enum_curd.model.filter(state__not=1).distinct().values_list("env_name", flat=True)
        LOGGER.info(f"查询环境名称(去重)成功, 结果明细: {names}")
        return SuccessResponse(message="查询成功", data=names, total=len(names))
    except Exception as e:
        LOGGER.error(f"查询环境名称(去重)环境失败，异常描述: {e}\n{traceback.format_exc()}")
        return FailureResponse(message=f"查询失败，异常描述: {e}")


@autotest_env.post("/search", summary="查询环境列表", description="根据条件分页查询环境列表信息(Body)")
async def search_env_info(
        env_in: AutoTestApiEnvSelect = Body(..., description="查询条件"),
        services: AutoTestApiServices = Depends(get_autotest_api_services),
):
    """
    根据条件查询环境。

    :param env_in: 环境入参
    :param services: 自动化测试CRUD依赖聚合
    :return: 统一HTTP响应
    """
    try:
        q = Q()
        if env_in.env_id:
            q &= Q(id=env_in.env_id)
        if env_in.env_code:
            q &= Q(env_code=env_in.env_code)
        if env_in.env_name:
            q &= Q(env_name__contains=env_in.env_name)
        if env_in.created_user:
            q &= Q(created_user__iexact=env_in.created_user)
        if env_in.updated_user:
            q &= Q(updated_user__iexact=env_in.updated_user)
        q &= Q(state=env_in.state)
        total, instances = await services.env_enum_curd.select_envs(
            search=q,
            page=env_in.page,
            page_size=env_in.page_size,
            order=env_in.order
        )
        env_serializes: List[Dict[str, Any]] = []
        for instance in instances:
            serialize: Dict[str, Any] = await instance.to_dict(
                exclude_fields={
                    "state",
                    "reserve_1", "reserve_2", "reserve_3"
                },
                replace_fields={"id": "env_id"}
            )
            env_serializes.append(serialize)
        LOGGER.info(f"根据条件查询环境成功, 结果数量: {total}")
        return SuccessResponse(message="查询成功", data=env_serializes, total=total)
    except ParameterException as e:
        return ParameterResponse(message=str(e.message))
    except Exception as e:
        LOGGER.error(f"根据条件查询环境失败，异常描述: {e}\n{traceback.format_exc()}")
        return FailureResponse(message=f"查询失败，异常描述: {e}")


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
        return {
            "code": "000000",
            "status": "success",
            "message": "查询成功",
            "data": result_data,
        }
    except Exception as e:
        LOGGER.error(f"获取环境列表失败: {e}\n{traceback.format_exc()}")
        return {
            "code": "999999",
            "status": "error",
            "message": f"查询失败: {e}",
            "data": {} if data.project_id is not None else [],
        }


@autotest_env.get("/search/list", summary="环境搜索列表")
async def get_env_search_list(
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
        LOGGER.error(f"环境搜索列表失败: {e}\n{traceback.format_exc()}")
        return FailureResponse(message=f"查询失败, 错误描述: {e}")


@autotest_env.post("/env/add", summary="新增环境主表")
async def add_env(
        data: EnvCreate,
        user: str = Query("admin", description="操作人"),
        services: AutoTestApiServices = Depends(get_autotest_api_services),
):
    """
    新增环境（确保环境枚举存在）。

    :param data: 环境入参
    :param user: 操作人
    :param services: 自动化测试CRUD依赖聚合
    :return: 统一HTTP响应
    """
    try:
        env_info = await services.env_enum_curd.create_automation_env(data, user)
        return SuccessResponse(message="新增应用成功", data=env_info, total=1)
    except DataAlreadyExistsException as e:
        return DataAlreadyExistsResponse(message=str(e.message))
    except NotFoundException as e:
        return NotFoundResponse(message=str(e.message))
    except ParameterException as e:
        return ParameterResponse(message=str(e.message))
    except Exception as e:
        LOGGER.error(f"新增环境失败: {e}\n{traceback.format_exc()}")
        return FailureResponse(message=f"新增应用环境失败, 错误描述: {e}")


@autotest_env.post("/env/update", summary="编辑环境主表")
async def update_automation_env_info(
        data: EnvEditRequest,
        user: str = Query("admin", description="操作人"),
        services: AutoTestApiServices = Depends(get_autotest_api_services),
):
    """
    编辑环境名称/应用归属，并级联同类型配置。

    :return: 统一HTTP响应
    """
    try:
        env_info = await services.env_enum_curd.update_automation_env(data, user)
        return SuccessResponse(message="编辑环境成功", data=env_info, total=1)
    except DataAlreadyExistsException as e:
        return DataAlreadyExistsResponse(message=str(e.message))
    except NotFoundException as e:
        return NotFoundResponse(message=str(e.message))
    except ParameterException as e:
        return ParameterResponse(message=str(e.message))
    except Exception as e:
        LOGGER.error(f"编辑环境失败: {e}\n{traceback.format_exc()}")
        return FailureResponse(message=f"编辑环境失败, 错误描述: {e}")


@autotest_env.post("/env/delete", summary="删除环境")
async def delete_automation_env_info(
        data: EnvDeleteRequest,
        user: str = Query("admin", description="操作人"),
        services: AutoTestApiServices = Depends(get_autotest_api_services),
):
    """
    软删指定环境下某节点类型的全部配置。

    :return: 统一HTTP响应
    """
    try:
        result = await services.env_enum_curd.delete_automation_env(data.id, data.env_type, user)
        return SuccessResponse(message="删除环境成功", data=result, total=1)
    except NotFoundException as e:
        return NotFoundResponse(message=str(e.message))
    except ParameterException as e:
        return ParameterResponse(message=str(e.message))
    except Exception as e:
        LOGGER.error(f"删除环境失败: {e}\n{traceback.format_exc()}")
        return FailureResponse(message=f"删除环境失败, 错误描述: {e}")


@autotest_env.get("/getallApp", summary="获取全部应用")
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
