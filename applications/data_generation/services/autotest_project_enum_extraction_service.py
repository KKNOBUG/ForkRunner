# -*- coding: utf-8 -*-

"""
    项目接口枚举提取编排：分批调用AI、合并结果并更新文档快照。
"""

from __future__ import annotations

import asyncio
import json
import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, MutableMapping, Optional, Protocol, Sequence

from pydantic import ValidationError

from applications.data_generation.schemas.autotest_project_enum_extraction_schema import (
    MAX_ENUM_EXTRACTION_BATCH_SIZE,
    ProjectEnumExtractionCandidate,
    ProjectEnumExtractionFieldInput,
)
from applications.data_generation.services.autotest_ai_enum_extraction_client import (
    AIEnumExtractionError,
    AIProjectEnumExtractionClient,
)
from applications.data_generation.services.autotest_project_enum_extraction_validator import (
    build_ambiguous_candidate,
    validate_project_enum_candidate,
    validate_project_enum_response,
)
from configure import PROJECT_CONFIG


class ProjectEnumExtractionInputError(ValueError):
    """项目接口解析快照缺失或结构不合法。"""


class _EnumExtractionClient(Protocol):
    """
        定义了一个协议类型，用于规定枚举抽取客户端必须具备什么能力=
        项类型检查器声明：只要一个对象有符合此签名的异步extract方法，
        就可以被当作枚举提取客户端使用
    """
    async def extract(
            self,
            fields: Sequence[ProjectEnumExtractionFieldInput],
    ) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class ProjectEnumExtractionOutcome:
    """
        项目接口枚举抽取的最终结果对象；
        统一封住了处理后的接口文档快照、每个字段的枚举提取结果和统计数量以及AI模型的调用与故障转移信息
    """
    document: Mapping[str, Any]                         #完成枚举抽取后的项目接口文档快照
    results: Sequence[ProjectEnumExtractionCandidate]   #保存每一个送给AI处理的字段的枚举抽取结果
    extracted_count: int                                #成功提取枚举值的字段数量
    not_found_count: int                                #没有发现枚举的字段数量
    ambiguous_count: int                                #存在歧义、不能确定结果的字段数量
    models_used: Sequence[Mapping[str, str]]            #记录真正成功完成枚举抽取任务的AI模型
    attempted_models: Sequence[str]                     #整个枚举抽取过程中尝试过的某行配置名称
    failover_count: int                                 #记录故障转移次输


def _batch_payload_chars(fields: Sequence[ProjectEnumExtractionFieldInput]) -> int:
    """
        用于计算一批字段转换成JSON请求数据后大约包含多少字符，避免单次发送给AI的字段备注过长
        fields：表示一组准备发送给AI的字段
        返回值：序列化后的JSON字符串长度
    """
    payload = {"fields": [field.model_dump(mode="json") for field in fields]}
    return len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def _split_text(text: str, limit: int) -> List[str]:
    """
        用于将过长的字段备注拆成多个较短的文本片段，避免单次发送给AI的备注超过批次字符限制
        拆分策略：优先按照中文句号和换行拆分；若某一段仍然超长，再按照固定长度切割
        text：需要拆分的完整备注文本
        limit：单个片段允许的最大字符数
        返回值：拆分后的字符串列表
    """
    if len(text) <= limit:
        return [text]

    chunks: List[str] = []
    current = ""
    overlap = min(256, max(1, limit // 4))
    for part in filter(None, re.split(r"(?<=[。\r\n])", text)):
        if len(part) <= limit:
            if current and len(current) + len(part) > limit:
                chunks.append(current)
                current = ""
            current += part
            continue

        if current:
            chunks.append(current)
            current = ""
        start = 0
        while start < len(part):
            end = min(len(part), start + limit)
            chunks.append(part[start:end])
            if end == len(part):
                break
            start = end - overlap
    if current:
        chunks.append(current)
    return chunks


def _split_field(
        field: ProjectEnumExtractionFieldInput,
        max_chars: int,
) -> List[ProjectEnumExtractionFieldInput]:
    """
        处理单个待提交给AI的字段：根据单次请求允许的最大字符数，扣除字段名，中文名，
        行号和JSON结构占用的字符，然后拆分超长备注，并为每个备注片段创建一份新的字段对象
        field：表示需要AI分析的字段
        max_chars：表示包含该字段的AI数据部分最多允许多少字符
        返回值：字段对象列表
    """
    empty_remark = field.model_copy(update={"remark": "x"})
    metadata_chars = _batch_payload_chars([empty_remark]) - 1
    remark_limit = max(1, max_chars - metadata_chars)
    return [
        field.model_copy(update={"remark": remark})
        for remark in _split_text(field.remark, remark_limit)
    ]


def _build_batches(
        fields: Sequence[ProjectEnumExtractionFieldInput],
        size: int,
        max_chars: int,
) -> Sequence[Sequence[ProjectEnumExtractionFieldInput]]:
    """
        用于把所有待AI识别的字段划分成多个请求批次（分割）
    """
    batches: List[List[ProjectEnumExtractionFieldInput]] = []
    current: List[ProjectEnumExtractionFieldInput] = []
    for field in fields:
        fragments = _split_field(field, max_chars)
        if len(fragments) > 1:
            if current:
                batches.append(current)
                current = []
            # 同一字段的片段必须分开发送，避免响应中的字段标识发生重复。
            batches.extend([[fragment] for fragment in fragments])
            continue

        candidate = current + fragments
        if current and (
                len(candidate) > size
                or _batch_payload_chars(candidate) > max_chars
        ):
            batches.append(current)
            current = fragments
        else:
            current = candidate
    if current:
        batches.append(current)
    return batches


def _merge_fragment_results(
        fields: Sequence[ProjectEnumExtractionFieldInput],
        fragment_results: Sequence[ProjectEnumExtractionCandidate],
) -> List[ProjectEnumExtractionCandidate]:
    """
        将一个原始字段因为备注过长而拆出的多个AI识别结果，合并成该字段唯一的一条最终结果
        fields：原始字段列表，即备注拆分之前的字段
        fragment_results：所有备注片段经过AI识别后产生的结果
    """
    grouped: Dict[tuple[int, str], List[ProjectEnumExtractionCandidate]] = {}
    for result in fragment_results:
        grouped.setdefault((result.source_row, result.field_name), []).append(result)

    merged: List[ProjectEnumExtractionCandidate] = []
    for field in fields:
        candidates = grouped.get((field.source_row, field.field_name), [])
        if len(candidates) == 1:
            # 未拆分字段已在单批响应校验中完成原文核对。
            merged.append(candidates[0])
            continue
        extracted = {
            candidate.normalized_text: candidate
            for candidate in candidates
            if candidate.status == "extracted" and candidate.normalized_text
        }
        if len(extracted) > 1:
            merged.append(build_ambiguous_candidate(field, "超长备注的不同片段识别出多个枚举组"))
        elif extracted:
            merged.append(validate_project_enum_candidate(field, next(iter(extracted.values()))))
        elif any(candidate.status == "ambiguous" for candidate in candidates):
            merged.append(build_ambiguous_candidate(field, "超长备注存在无法确定的枚举片段"))
        elif candidates:
            merged.append(validate_project_enum_candidate(field, candidates[0]))
        else:
            merged.append(build_ambiguous_candidate(field, "AI枚举提取响应缺少当前字段"))
    return merged


class AutoTestProjectEnumExtractionService:
    """
        调用配置的AI模型并将通过本地校验的枚举值合并到项目文档快照。
    """

    def __init__(
            self,
            client: Optional[_EnumExtractionClient] = None,
            *,
            batch_size: Optional[int] = None,
            batch_max_chars: Optional[int] = None,
            max_concurrency: Optional[int] = None,
            total_timeout_seconds: Optional[float] = None,
    ):
        self.client = client or AIProjectEnumExtractionClient()
        configured_batch_size = (
            PROJECT_CONFIG.ENUM_AI_BATCH_SIZE if batch_size is None else batch_size
        )
        self.batch_size = max(1, min(int(configured_batch_size), MAX_ENUM_EXTRACTION_BATCH_SIZE))
        self.batch_max_chars = max(256, int(
            PROJECT_CONFIG.ENUM_AI_BATCH_MAX_CHARS
            if batch_max_chars is None
            else batch_max_chars
        ))
        self.max_concurrency = max(1, int(
            PROJECT_CONFIG.ENUM_AI_MAX_CONCURRENCY
            if max_concurrency is None
            else max_concurrency
        ))
        self.total_timeout_seconds = max(0.1, float(
            PROJECT_CONFIG.ENUM_AI_TOTAL_TIMEOUT_SECONDS
            if total_timeout_seconds is None
            else total_timeout_seconds
        ))

    @staticmethod
    def _build_inputs(fields: Sequence[Any]) -> List[ProjectEnumExtractionFieldInput]:
        inputs: List[ProjectEnumExtractionFieldInput] = []
        identities = set()
        for raw_field in fields:
            if not isinstance(raw_field, Mapping):
                raise ProjectEnumExtractionInputError("项目接口fields中存在非对象字段")
            remark = str(raw_field.get("remark") or "").strip()
            if not remark:
                continue
            try:
                field = ProjectEnumExtractionFieldInput(
                    source_row=raw_field.get("source_row"),
                    field_name=raw_field.get("field_name"),
                    field_chinese_name=raw_field.get("field_chinese_name"),
                    remark=remark,
                )
            except (ValidationError, TypeError, ValueError) as exc:
                raise ProjectEnumExtractionInputError("项目接口字段缺少枚举抽取所需信息") from exc
            identity = (field.source_row, field.field_name)
            if identity in identities:
                raise ProjectEnumExtractionInputError("项目接口字段行号与字段名重复")
            identities.add(identity)
            inputs.append(field)
        return inputs

    @staticmethod
    def _apply_results_to_document(
            document: MutableMapping[str, Any],
            results: Sequence[ProjectEnumExtractionCandidate],
    ) -> None:
        by_identity = {
            (result.source_row, result.field_name): result
            for result in results
        }
        for field in document["fields"]:
            if not isinstance(field, MutableMapping):
                continue
            identity = (field.get("source_row"), field.get("field_name"))
            result = by_identity.get(identity)
            if result is not None:
                # 只有通过原备注证据校验的结果可以进入生成规则。
                field["enum_values"] = (
                    result.normalized_text if result.status == "extracted" else None
                )

    async def extract(
            self,
            interface_document: Mapping[str, Any],
    ) -> ProjectEnumExtractionOutcome:
        """处理项目接口快照；ESB或未知样式直接拒绝，不调用AI。"""
        if not isinstance(interface_document, Mapping):
            raise ProjectEnumExtractionInputError("接口文档解析快照必须是对象")
        if str(interface_document.get("interface_style") or "").strip().lower() != "project":
            raise ProjectEnumExtractionInputError("枚举AI抽取仅支持项目接口文档")
        fields = interface_document.get("fields")
        if not isinstance(fields, list):
            raise ProjectEnumExtractionInputError("项目接口解析快照缺少fields列表")

        document = deepcopy(dict(interface_document))
        inputs = self._build_inputs(fields)
        batches = _build_batches(inputs, self.batch_size, self.batch_max_chars)
        semaphore = asyncio.Semaphore(self.max_concurrency)

        async def extract_batch(index: int, batch: Sequence[ProjectEnumExtractionFieldInput]):
            async with semaphore:
                return index, batch, await self.client.extract(batch)

        tasks = [
            asyncio.create_task(extract_batch(index, batch))
            for index, batch in enumerate(batches)
        ]
        try:
            batch_results = await asyncio.wait_for(
                asyncio.gather(*tasks),
                timeout=self.total_timeout_seconds,
            )
        except TimeoutError as exc:
            raise AIEnumExtractionError(
                f"项目接口枚举抽取超过{self.total_timeout_seconds:g}秒，已终止",
                failure_types=("TimeoutError",),
            ) from exc
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

        fragment_results: List[ProjectEnumExtractionCandidate] = []
        models_used: List[Dict[str, str]] = []
        attempted_models: List[str] = []
        failover_count = 0
        for _, batch, raw_response in sorted(batch_results, key=lambda item: item[0]):
            fragment_results.extend(validate_project_enum_response(batch, raw_response))
            execution = raw_response.get("_ai_execution")
            if isinstance(execution, Mapping):
                provider_name = str(execution.get("provider_name") or "").strip()
                model_name = str(execution.get("model_name") or "").strip()
                model_identity = {
                    "provider_name": provider_name,
                    "model_name": model_name,
                }
                if provider_name and model_name and model_identity not in models_used:
                    models_used.append(model_identity)
                raw_attempted_models = execution.get("attempted_models")
                if isinstance(raw_attempted_models, (list, tuple)):
                    for attempted in raw_attempted_models:
                        name = str(attempted or "").strip()
                        if name and name not in attempted_models:
                            attempted_models.append(name)
                raw_failover_count = execution.get("failover_count", 0)
                if isinstance(raw_failover_count, int) and raw_failover_count > 0:
                    failover_count += raw_failover_count
        results = _merge_fragment_results(inputs, fragment_results)
        self._apply_results_to_document(document, results)

        return ProjectEnumExtractionOutcome(
            document=document,
            results=tuple(results),
            extracted_count=sum(item.status == "extracted" for item in results),
            not_found_count=sum(item.status == "not_found" for item in results),
            ambiguous_count=sum(item.status == "ambiguous" for item in results),
            models_used=tuple(models_used),
            attempted_models=tuple(attempted_models),
            failover_count=failover_count,
        )
