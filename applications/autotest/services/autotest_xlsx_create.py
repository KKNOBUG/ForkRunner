# -*- coding: utf-8 -*-
import json
import random
import re
import string
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Dict, List, Optional, Union

import numpy as np
import pandas as pd

# =============================
# 数据模型
# =============================
from applications.autotest.schemas.autotest_data_create_schema import AutoTestDataCreateUpdate
from applications.autotest.services.autotest_data_create_crud import AutoTestDataCreateCrud

_DATA_CREATE_CRUD: AutoTestDataCreateCrud = AutoTestDataCreateCrud()


@dataclass
class Field:
    cn_name: str
    en_name: str
    data_type: str
    length: Optional[str]
    required: Optional[str]
    enum: Optional[str]


@dataclass
class TCPField:
    cn_name: str
    en_name: str
    data_type: str
    base_type: str        # char, decimal, integer, array, unknown
    length: Optional[str]
    required: Optional[str]
    enum: Optional[str]
    is_head: bool


def parse_tcp_data_type(data_type: str) -> tuple:
    """解析字段映射文档的数据类型字符串。

    返回 (base_type, length_str):
        C..15  -> ("char", "15")
        C8     -> ("char", "8")
        D26,8  -> ("decimal", "26,8")
        I..10  -> ("integer", "10")
        N..20  -> ("char", "20")
        array  -> ("array", None)
    """
    if not data_type:
        return "unknown", None
    s = data_type.strip()
    s_lower = s.lower()

    if s_lower in ("array", "list"):
        return "array", None

    # D26,8 / D..26,8
    m = re.match(r'^[Dd]\.?(\d+),(\d+)$', s)
    if m:
        return "decimal", f"{m.group(1)},{m.group(2)}"

    # I..10 / I10
    m = re.match(r'^[Ii]\.?\.?(\d+)$', s)
    if m:
        return "integer", m.group(1)

    # C..15 / C8 / C..15
    m = re.match(r'^[Cc]\.?\.?(\d+)$', s)
    if m:
        return "char", m.group(1)

    # N..20 / N8
    m = re.match(r'^[Nn]\.?\.?(\d+)$', s)
    if m:
        return "char", m.group(1)

    # A..30 / Ans..50
    m = re.match(r'^[Aa](ns)?\.?\.?(\d+)$', s)
    if m:
        return "char", m.group(2)

    return "unknown", None


# =============================
# 字段映射文档读取（TCP 接口）
# =============================

def _parse_mapping_sheet(df: pd.DataFrame, is_head: bool) -> List[TCPField]:
    """解析字段映射文档的单个 Sheet，提取 ESB 映射后（右侧）列的字段信息。

    右侧 ESB 列索引: 10=英文名称, 11=中文名称, 12=数据类型/长度,
                      14=是否必输, 15=枚举值, 16=备注
    """
    col_en = 10
    col_cn = 11
    col_dtype = 12
    col_required = 14
    col_enum = 15
    col_remark = 16

    values = df.values

    # 定位"输入"和"输出"行
    input_row = None
    output_row = None
    header_row = None

    for i, row in enumerate(values):
        first_cell = str(row[0]).strip() if not pd.isna(row[0]) else ""
        if first_cell == "输入":
            input_row = i
        elif first_cell == "输出":
            output_row = i
        # 表头行包含"英文名称"在右侧
        if not pd.isna(row[col_en]) and str(row[col_en]).strip() == "英文名称":
            header_row = i

    if header_row is None:
        return []
    if input_row is None:
        return []

    start = input_row + 1
    end = output_row if output_row else len(values)

    fields: List[TCPField] = []
    current_array_parent: Optional[str] = None

    for i in range(start, end):
        row = values[i]
        en_name = str(row[col_en]).strip() if not pd.isna(row[col_en]) else ""
        if not en_name or en_name == "nan":
            continue

        cn_name = str(row[col_cn]).strip() if not pd.isna(row[col_cn]) else ""
        data_type_raw = str(row[col_dtype]).strip() if not pd.isna(row[col_dtype]) else ""
        required = str(row[col_required]).strip() if not pd.isna(row[col_required]) else ""
        enum_val = str(row[col_enum]).strip() if not pd.isna(row[col_enum]) else ""
        remark = str(row[col_remark]).strip() if not pd.isna(row[col_remark]) else ""

        base_type, length = parse_tcp_data_type(data_type_raw)

        # array Start/End 处理
        if base_type == "array":
            remark_lower = remark.lower()
            if "end" in remark_lower:
                current_array_parent = None
                continue
            if "start" in remark_lower:
                current_array_parent = en_name
                # array 本身不作为独立测试字段，仅记录父名
                continue
            continue

        # 子字段前缀
        if current_array_parent:
            en_name = f"{current_array_parent}[0].{en_name}"

        if required in ("", "nan"):
            required = None
        if enum_val in ("", "nan"):
            enum_val = None
        if length in ("", "nan"):
            length = None

        fields.append(TCPField(
            cn_name=cn_name,
            en_name=en_name,
            data_type=data_type_raw,
            base_type=base_type,
            length=length,
            required=required,
            enum=enum_val,
            is_head=is_head,
        ))

    return fields


def is_field_mapping_doc(file_path: str) -> bool:
    """检测文件是否为字段映射文档（TCP 标准接口文档）。
    判断依据: 包含 "BOSFX3.0公共报文头" 或纯数字命名的 Sheet。
    """
    try:
        all_sheets = pd.read_excel(file_path, sheet_name=None, header=None)
        for sheet_name in all_sheets:
            if "BOSFX3.0公共报文头" in sheet_name:
                return True
            if sheet_name.isdigit():
                return True
    except Exception:
        pass
    return False


def read_field_mapping_doc(file_path: str) -> tuple:
    """读取字段映射文档，返回 (head_fields, body_fields)。

    Head 字段: BOSFX3.0公共报文头 Sheet
    Body 字段: 纯数字命名的 Sheet（如 "163002908", "183002207"）
    """
    all_sheets = pd.read_excel(file_path, sheet_name=None, header=None)
    head_fields: List[TCPField] = []
    body_fields: List[TCPField] = []

    for sheet_name, df in all_sheets.items():
        if df.empty:
            continue
        if "BOSFX3.0公共报文头" in sheet_name:
            fields = _parse_mapping_sheet(df, is_head=True)
            head_fields.extend(fields)
        elif sheet_name.isdigit():
            fields = _parse_mapping_sheet(df, is_head=False)
            body_fields.extend(fields)

    # 按 en_name 去重（同一 sheet 内可能存在重复字段行）
    seen_head = set()
    deduped_head = []
    for f in head_fields:
        if f.en_name not in seen_head:
            seen_head.add(f.en_name)
            deduped_head.append(f)

    seen_body = set()
    deduped_body = []
    for f in body_fields:
        if f.en_name not in seen_body:
            seen_body.add(f.en_name)
            deduped_body.append(f)

    return deduped_head, deduped_body


# =============================
# Excel读取
# =============================

def read_excel_template(file_path: str) -> List[Field]:
    df = pd.read_excel(file_path, sheet_name=0, header=None)

    header_row = None
    header_filed = ["英文名称", "中文名称", "数据类型", "长度", "是否必输", "枚举值"]
    input_row = None
    output_row = None
    header_all = []

    for i, row in df.iterrows():
        row_values = [str(v).strip() for v in row.values]

        if "英文名称" in row_values and header_row is None:
            header_row = i
            header_all = [x for x in header_filed if x not in row_values]

        if "输入" in row_values and input_row is None:
            input_row = i

        if "输出" in row_values and output_row is None:
            output_row = i

    if header_row is None:
        raise ValueError("未找到表头行缺少【英文名称】")
    if header_row and header_all != []:
        raise ValueError(f"未找到表头行缺少【{header_all}】")
    if input_row is None:
        raise ValueError("未找到【输入】标识行")

    headers = []
    for v in df.iloc[header_row].values:
        if pd.isna(v):
            break
        headers.append(str(v).strip())

    start = input_row + 1
    end = output_row if output_row else len(df)

    data_df = df.iloc[start:end, :len(headers)].copy()
    data_df.columns = headers

    fields: List[Field] = []

    for _, row in data_df.iterrows():

        cn_name = str(row.get("中文名称", "")).strip()
        en_name = str(row.get("英文名称", "")).strip()
        data_type = str(row.get("数据类型", "")).strip()
        length = str(row.get("长度", "")).strip()
        required = str(row.get("是否必输", "")).strip()
        enum = str(row.get("枚举值", "")).strip()

        if not en_name or en_name == "nan":
            continue

        fields.append(
            Field(
                cn_name=cn_name,
                en_name=en_name,
                data_type=data_type,
                length=None if length in ["", "nan"] else length,
                required=None if required in ["", "nan"] else required,
                enum=None if enum in ["", "nan"] else enum,
            )
        )

        # 处理 list / array 子字段结构
    processed_fields: List[Field] = []
    current_parent: Optional[str] = None

    for f in fields:
        dtype = (f.data_type or "").lower()

        # 如果是 list / array 字段
        if dtype in ["list", "array"]:
            current_parent = f.en_name
            processed_fields.append(f)
            continue

        # 如果当前存在父字段，则认为是子字段
        if current_parent:
            f = Field(
                cn_name=f.cn_name,
                en_name=f"{current_parent}[0].{f.en_name}",
                data_type=f.data_type,
                length=f.length,
                required=f.required,
                enum=f.enum,
            )

        processed_fields.append(f)

    return processed_fields


# =============================
# TCP 字段桥接 & 默认值生成
# =============================

def _tcp_field_to_field(tcp_field: TCPField) -> Field:
    """将 TCPField 转换为 Field，用于复用 generate_cases_np 逻辑。"""
    return Field(
        cn_name=tcp_field.cn_name,
        en_name=tcp_field.en_name,
        data_type=tcp_field.base_type,
        length=tcp_field.length,
        required=tcp_field.required,
        enum=tcp_field.enum,
    )


def _generate_placeholder_value(base_type: str, length: Optional[str]) -> str:
    """根据类型和长度生成默认占位值。"""
    if base_type == "integer":
        return "1"
    if base_type == "decimal":
        return "0.01"
    if base_type == "char":
        try:
            n = int(length) if length else 10
        except (ValueError, TypeError):
            n = 10
        n = min(n, 20)
        return random_string(n)
    return "test"


# =============================
# 工具函数
# =============================

def is_required(value: Optional[str]) -> bool:
    if not value:
        return False
    return str(value).strip().lower() in ["是", "m", "y", "true"]


def random_string(length: int) -> str:
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length))


def random_enum_invalid(enum_str: str, length: int):
    values = [v.strip() for v in enum_str.split(',') if v.strip()]
    max_int = length * 9
    while True:
        if length == 1 and len(values) == 10 and all(x in "0123456789" for x in values):
            val = str(random.choice(string.ascii_letters))
        else:
            val = str(random.randint(0, max_int))
        if val not in values:
            return val


def generate_length_invalid(field: Field, rule: str):
    if not field.length:
        return None, None
    s = field.length.replace("，", ",")
    s = s.replace("(", "").replace(")", "")
    s = s.replace("（", "").replace("）", "")
    if rule == 'length_int':
        if ',' in s:
            all_length = int(s.split(',')[0].strip())
            float_length = int(s.split(',')[1].strip())
            length = all_length - float_length
            if length <= 0:
                raise ValueError(f"{field.cn_name}小数位数大于等于整数位数")
        else:
            length = int(s.strip())
        invalid_len = length + 1
        value = "9" * invalid_len
        return value, length
    else:
        if ',' not in s:
            return None, None
        float_length = int(s.split(',')[1].strip())
        invalid_len = float_length + 1
        value = "9." + "9" * invalid_len
        return value, float_length


def generate_decimal_invalid(field: Field, decimal_flag: str):
    if not field.length:
        return None
    s = field.length.replace("，", ",")
    if ',' not in s:
        return None
    s = s.replace("(", "").replace(")", "")
    s = s.replace("（", "").replace("）", "")
    all_length = int(s.split(',')[0].strip())
    float_length = int(s.split(',')[1].strip())
    int_length = all_length - float_length
    if int_length <= 0:
        raise ValueError(f"{field.cn_name}小数位数大于等于整数位数")
    if decimal_flag == "decimal_nine":
        value = "9" * int_length + "." + "9" * float_length
    elif decimal_flag == "decimal_nine_max":
        value = "9" * int_length + "." + "9" * float_length
        decimal_value = Decimal(value)
        decimal_value += Decimal(f"0.{'0' * (float_length - 1)}1")
        value = decimal_value.__str__()
    elif decimal_flag == "decimal_nine_min":
        value = "9" * int_length + "." + "9" * float_length
        decimal_value = Decimal(value)
        decimal_value -= Decimal(f"0.{'0' * (float_length - 1)}1")
        value = decimal_value.__str__()
    elif decimal_flag == "decimal_zero":
        value = "0"
    elif decimal_flag == "decimal_zero_min":
        value = f"-0.{'0' * (float_length - 1)}1"
    else:
        value = f"0.{'0' * (float_length - 1)}1"
    return value


def generate_cases_np(fields: List[Field], selected_rules: List[str], base_json: Dict[str, Any]):
    if "body" in base_json.keys() or "Body" in base_json.keys():
        base_json = base_json.get("body", base_json.get("Body"))
    base_json_neo = {}
    for k, v in base_json.items():
        if isinstance(v, list) and isinstance(v[0], dict):
            for a, b in v[0].items():
                base_json_neo[f"{k}[0].{a}"] = b
        elif isinstance(v, dict):
            for a, b in v.items():
                base_json_neo[f"{k}.{a}"] = b
        else:
            base_json_neo[k] = v

    base_array = np.array([base_json_neo for _ in range(len(fields) * len(selected_rules))], dtype=object)
    idx = 1
    row = base_json_neo.copy()
    row["case_name"] = "正交易场景"
    base_array[0] = row
    for field in fields:
        dtype = (field.data_type or "").lower()
        if dtype in ["list", "array"]:
            continue
        rule_flag = True
        for rule in selected_rules:
            row = base_json_neo.copy()
            if rule in ("required_", "required_null"):
                if not field.required:
                    if rule_flag:
                        row["case_name"] = f"【{field.cn_name}】【{field.en_name}】接口文档的是否必输项为空，请检查"
                        rule_flag = False
                else:
                    if is_required(field.required):
                        if rule == "required_":
                            row[field.en_name] = "空"
                            config_val = "空"
                        else:
                            row[field.en_name] = "null"
                            config_val = "null"
                        row["case_name"] = f"【{field.cn_name}】【{field.en_name}】必输项校验，生成{config_val}值"
            elif rule in ("length_int", "length_float"):
                value, config_len = generate_length_invalid(field, rule)
                config_val = "整数" if rule == "length_int" else "小数"
                if value:
                    row[field.en_name] = value
                    row[
                        "case_name"] = f"【{field.cn_name}】【{field.en_name}】长度校验，配置{config_val}长度{config_len}，生成长度{config_len + 1}"
            elif rule in ("decimal_nine", "decimal_nine_max", "decimal_nine_min", "decimal_zero", "decimal_zero_min",
                          "decimal_zero_max"):
                value = generate_decimal_invalid(field, rule)
                if value:
                    row[field.en_name] = value
                    row["case_name"] = f"【{field.cn_name}】【{field.en_name}】边界值校验校验，配置长度{field.length}，生成值{value}"
            elif rule == "enum":
                if field.enum:
                    try:
                        length = int(field.length)
                    except Exception:
                        raise ValueError(f"{field.cn_name}枚举值长度异常")
                    invalid = random_enum_invalid(field.enum, length)
                    row[field.en_name] = invalid
                    row["case_name"] = f"【{field.cn_name}】【{field.en_name}】枚举值校验，配置枚举值为[{field.enum}]，生成枚举值{invalid}"
                else:
                    if not field.length:
                        row["case_name"] = f"【{field.cn_name}】【{field.en_name}】接口文档的长度项和枚举值项均为空，请检查"
            else:
                continue
            if row.get("case_name"):
                base_array[idx] = row
                idx += 1
    return base_array[:idx].tolist()


def generate_tcp_cases_np(
        head_fields: List[TCPField],
        body_fields: List[TCPField],
        selected_rules: List[str],
        request_args_type: str,
        xpath_map: Optional[Dict[str, str]] = None,
        json_message: Optional[Union[str, dict]] = None,
        xml_message: Optional[str] = None,
) -> list:
    """生成 TCP 测试用例，返回 [cases]。

    每个案例同时包含 Head 和 Body 字段，key 格式:
    - JSON: $.Head.xxx / $.Body.xxx
    - XML:  ./Head/xxx / ./Body/xxx（XPath）

    第一个案例（正交易场景）使用原始接口数据填充，无原始值的字段用 placeholder 补充。
    """
    from common.xpath_utils import XPathUtils

    # 从原始请求数据中提取字段真实值
    original_head = {}
    original_body = {}
    if request_args_type == "xml" and xml_message:
        from xml.etree import ElementTree
        root = ElementTree.fromstring(xml_message.encode("utf-8"))
        for child in root:
            tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
            if tag == "Head":
                for elem in child:
                    name = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
                    if elem.text is not None:
                        original_head[name] = elem.text
            elif tag == "Body":
                for elem in child:
                    name = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
                    if elem.text is not None:
                        original_body[name] = elem.text
                    if len(elem) > 0:
                        for sub in elem:
                            sub_name = sub.tag.split("}")[-1] if "}" in sub.tag else sub.tag
                            key = f"{name}[0].{sub_name}"
                            original_body[key] = sub.text if sub.text is not None else ""
    elif json_message:
        if isinstance(json_message, dict):
            base_json = json_message
        else:
            base_json = json.loads(json_message)
        if "Head" in base_json and isinstance(base_json["Head"], dict):
            original_head = base_json["Head"]
        if "Body" in base_json and isinstance(base_json["Body"], dict):
            for k, v in base_json["Body"].items():
                if isinstance(v, list) and len(v) > 0 and isinstance(v[0], dict):
                    for sk, sv in v[0].items():
                        original_body[f"{k}[0].{sk}"] = sv
                elif isinstance(v, dict):
                    for sk, sv in v.items():
                        original_body[f"{k}.{sk}"] = sv
                else:
                    original_body[k] = v

    # 构建 Field 列表和 base_json
    # Head 字段的 en_name 用 Head.xxx 格式，Body 字段保持原样
    all_fields_bridge = []
    base_json_neo = {}
    for f in head_fields:
        if f.base_type == "array":
            continue
        bridge = _tcp_field_to_field(f)
        bridge.en_name = f"Head.{f.en_name}"
        all_fields_bridge.append(bridge)
        if f.en_name in original_head:
            base_json_neo[bridge.en_name] = original_head[f.en_name]
        else:
            base_json_neo[bridge.en_name] = _generate_placeholder_value(f.base_type, f.length)
    for f in body_fields:
        if f.base_type == "array":
            continue
        bridge = _tcp_field_to_field(f)
        all_fields_bridge.append(bridge)
        if f.en_name in original_body:
            base_json_neo[f.en_name] = original_body[f.en_name]
        else:
            base_json_neo[f.en_name] = _generate_placeholder_value(f.base_type, f.length)

    if not all_fields_bridge:
        return []

    # 一次性生成所有用例
    cases_raw = generate_cases_np(all_fields_bridge, selected_rules, base_json_neo)

    # 转换 key 格式
    # XML 模式: replace_xml_datagram 只接收 body_map，因此 Head 字段也合并到 body 区域
    #   使用 ./Head/xxx 格式，替换时统一走 body_map
    # JSON 模式: replace_json_datagram 同时接收 head_map 和 body_map，保持分离
    cases = []
    for case in cases_raw:
        new_case = {}
        for k, v in case.items():
            if k == "case_name":
                new_case[k] = v
            elif k.startswith("Head."):
                field_name = k[5:]
                if request_args_type == "xml" and xpath_map:
                    # xpath_map 中 key 是纯字段名(如 TxnDt)，值是完整 XPath(如 ./Head/TxnDt)
                    new_case[XPathUtils.resolve_field_to_xpath(field_name, xpath_map)] = v
                else:
                    new_case[f"$.Head.{field_name}"] = v
            else:
                if request_args_type == "xml" and xpath_map:
                    new_case[XPathUtils.resolve_field_to_xpath(k, xpath_map)] = v
                else:
                    new_case[f"$.Body.{k}"] = v
        cases.append(new_case)

    return cases


# =============================
# Excel导出
# =============================

def export_excel(cases: List[Dict[str, Any]], fields: List[Field], output_file: str, step_name: str):
    # 父字段(list/array)不作为导出行
    export_fields = [f for f in fields if (f.data_type or "").lower() not in ["list", "array"]]

    columns = ["case_name"] + [f.en_name for f in export_fields]

    df = pd.DataFrame(cases)

    for col in columns:
        if col not in df.columns:
            df[col] = ""

    df = df[columns]

    # 以case_name作为列标题进行转置
    df.set_index("case_name", inplace=True)

    df = df.T

    # 第一列作为字段名
    df.index.name = ""

    df = df.reset_index()

    # 插入 Body 行（表头第二行第一列）
    # body_row = {col: "" for col in df.columns}
    # body_row[""] = "Body"
    # df = pd.concat([pd.DataFrame([body_row]), df], ignore_index=True)

    body_row = pd.Series([None] * len(df.columns), index=df.columns)
    body_row.iloc[0] = "Body"
    df = pd.concat([body_row.to_frame().T, df], ignore_index=True)

    df.to_excel(output_file, sheet_name=step_name, index=False)


def export_tcp_excel(
        cases: list,
        head_fields: List[TCPField],
        body_fields: List[TCPField],
        output_file: str,
        step_name: str,
        request_args_type: str = "json",
):
    """导出符合 autotest_xlsx_engine 格式的 TCP 测试数据 Excel。

    JSON 模式布局:
    行1: [空, 场景1, 场景2, ...]
    行2: [Head, marker, marker, ...]
    行3+: [$.Head.xxx, 值, 值, ...]
    行N: [Body, marker, marker, ...]
    行N+1+: [$.Body.xxx, 值, 值, ...]
    行M: [响应报文校验-Body, ...]

    XML 模式布局 (replace_xml_datagram 只接收 body_map, Head 字段合并到 Body 区域):
    行1: [空, 场景1, 场景2, ...]
    行2: [Body, marker, marker, ...]
    行3+: [./Head/xxx, 值, 值, ...]     (Head 字段以 ./Head/ 开头)
    行K+: [./Body/xxx, 值, 值, ...]     (Body 字段以 ./Body/ 开头)
    行M: [响应报文校验-Body, ...]
    """
    def _match_field_key(key, en_name):
        """匹配 key 中的字段名。

        优先匹配 en_name 整体（适用于 array 子字段如 Xxx[0].Field），
        否则用最后一段精确匹配（适用于简单字段如 SvcCd）。
        """
        # key 以 en_name 结尾，且前面是路径分隔符
        suffix = f".{en_name}"
        if key.endswith(suffix) or key.endswith(f"/{en_name}"):
            return True
        # key 本身就是 en_name（无前缀的情况）
        if key == en_name:
            return True
        # fallback: 取最后一段匹配（简单字段）
        last_part = key.rsplit("/", 1)[-1].rsplit(".", 1)[-1]
        return last_part == en_name

    # 收集场景名
    all_scene_names = []
    seen = set()
    for c in cases:
        name = c.get("case_name", "")
        if name and name not in seen:
            all_scene_names.append(name)
            seen.add(name)
    if not all_scene_names:
        all_scene_names = ["正交易场景"]

    # 构建场景名 → case 的映射
    case_by_name = {}
    for c in cases:
        name = c.get("case_name", "")
        if name:
            case_by_name[name] = c

    columns = [""] + all_scene_names
    rows = [columns]

    # 辅助函数: 为一组字段生成行
    def _append_field_rows(fields, section_marker):
        export_fields = [f for f in fields if f.base_type != "array"]
        if not export_fields and not fields:
            return
        marker_row = [section_marker] + [None] * len(all_scene_names)
        rows.append(marker_row)
        for f in export_fields:
            row = [None]
            for name in all_scene_names:
                val = None
                c = case_by_name.get(name)
                if c:
                    for k, v in c.items():
                        if k == "case_name":
                            continue
                        if _match_field_key(k, f.en_name):
                            val = v
                            break
                row.append(val)
            first_case = cases[0] if cases else {}
            for k in first_case:
                if k == "case_name":
                    continue
                if _match_field_key(k, f.en_name):
                    row[0] = k
                    break
            if row[0] is None:
                if section_marker == "Head":
                    row[0] = f"$.Head.{f.en_name}"
                else:
                    row[0] = f"$.Body.{f.en_name}"
            rows.append(row)

    if request_args_type == "xml":
        # XML 模式: replace_xml_datagram 只接收 body_map，Head 字段合并到 Body 区域
        # cases 中的 key 已经是 ./Head/xxx 和 ./Body/xxx 格式
        body_export = [f for f in (head_fields + body_fields) if f.base_type != "array"]
        if body_export:
            marker_row = ["Body"] + [None] * len(all_scene_names)
            rows.append(marker_row)
            for f in body_export:
                row = [None]
                for name in all_scene_names:
                    val = None
                    c = case_by_name.get(name)
                    if c:
                        for k, v in c.items():
                            if k == "case_name":
                                continue
                            if _match_field_key(k, f.en_name):
                                val = v
                                break
                    row.append(val)
                first_case = cases[0] if cases else {}
                for k in first_case:
                    if k == "case_name":
                        continue
                    if _match_field_key(k, f.en_name):
                        row[0] = k
                        break
                if row[0] is None:
                    row[0] = f"./Body/{f.en_name}"
                rows.append(row)
    else:
        # JSON 模式: Head 和 Body 分开，replace_json_datagram 各自接收
        _append_field_rows(head_fields, "Head")
        _append_field_rows(body_fields, "Body")

    # Assert-Body marker (assert-head 对 TCP 无效，合并到 assert-body)
    rows.append(["响应报文校验-Body"] + [None] * len(all_scene_names))

    df = pd.DataFrame(rows[1:], columns=rows[0])
    df.to_excel(output_file, sheet_name=step_name, index=False)


async def generate_test_data(
        input_excel: str,
        output_excel: str,
        rules: List[str],
        json_message: Union[str, dict],
        create_id: int,
        step_name: str
):
    await _DATA_CREATE_CRUD.update_data_create(
        data_in=(
            AutoTestDataCreateUpdate(
                data_create_id=create_id,
                create_status="1"
            )
        )
    )
    if "length" in rules:
        append_rules = ["length_int", "length_float"]
        rules.extend(append_rules)
    if "decimal" in rules:
        append_rules = ["decimal_nine", "decimal_nine_max", "decimal_nine_min", "decimal_zero", "decimal_zero_min", "decimal_zero_max", ]
        rules.extend(append_rules)
    if "required" in rules:
        append_rules = ["required_", "required_null"]
        rules.extend(append_rules)
    try:
        fields = read_excel_template(input_excel)
        if isinstance(json_message, dict):
            base_json = json_message
        else:
            base_json = json.loads(json_message)
        cases = generate_cases_np(fields, rules, base_json)
        export_excel(cases, fields, output_excel, step_name)
        dataset = {
            case["case_name"]: {k: v for k, v in case.items() if k != "case_name"}
            for case in cases if case.get("case_name")
        }
        await _DATA_CREATE_CRUD.update_data_create(
            data_in=(
                AutoTestDataCreateUpdate(
                    data_create_id=create_id,
                    create_status="3",
                    file_desc="",
                    dataset=dataset,
                )
            )
        )
    except Exception as e:
        await _DATA_CREATE_CRUD.update_data_create(
            data_in=(
                AutoTestDataCreateUpdate(
                    data_create_id=create_id,
                    create_status="2",
                    file_desc=f"{e}"
                )
            )
        )


async def generate_tcp_test_data(
        input_excel: str,
        output_excel: str,
        rules: List[str],
        request_args_type: str,
        json_message: Optional[Union[str, dict]] = None,
        xml_message: Optional[str] = None,
        create_id: int = None,
        step_name: str = "",
):
    """TCP 接口测试数据生成编排函数。"""
    from common.xpath_utils import XPathUtils

    await _DATA_CREATE_CRUD.update_data_create(
        data_in=(
            AutoTestDataCreateUpdate(
                data_create_id=create_id,
                create_status="1"
            )
        )
    )
    if "length" in rules:
        append_rules = ["length_int", "length_float"]
        rules.extend(append_rules)
    if "decimal" in rules:
        append_rules = ["decimal_nine", "decimal_nine_max", "decimal_nine_min", "decimal_zero", "decimal_zero_min", "decimal_zero_max", ]
        rules.extend(append_rules)
    if "required" in rules:
        append_rules = ["required_", "required_null"]
        rules.extend(append_rules)
    try:
        head_fields, body_fields = read_field_mapping_doc(input_excel)

        # 生成 xpath_map（仅 XML 模式）
        xpath_map = None
        if request_args_type == "xml" and xml_message:
            xpath_map = XPathUtils.generate_xpath_map(xml_message)

        cases = generate_tcp_cases_np(
            head_fields=head_fields,
            body_fields=body_fields,
            selected_rules=rules,
            request_args_type=request_args_type,
            xpath_map=xpath_map,
            json_message=json_message,
            xml_message=xml_message,
        )
        export_tcp_excel(cases, head_fields, body_fields, output_excel, step_name, request_args_type=request_args_type)
        dataset = {
            case["case_name"]: {k: v for k, v in case.items() if k != "case_name"}
            for case in cases if case.get("case_name")
        }
        await _DATA_CREATE_CRUD.update_data_create(
            data_in=(
                AutoTestDataCreateUpdate(
                    data_create_id=create_id,
                    create_status="3",
                    file_desc="",
                    dataset=dataset,
                )
            )
        )
    except Exception as e:
        await _DATA_CREATE_CRUD.update_data_create(
            data_in=(
                AutoTestDataCreateUpdate(
                    data_create_id=create_id,
                    create_status="2",
                    file_desc=f"{e}"
                )
            )
        )
