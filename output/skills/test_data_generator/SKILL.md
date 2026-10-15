---
name: forkrunner-test-data-generator
description: >
  根据严格格式的场景规则表和 ForkRunner 当前步骤 HEAD/BODY 字段快照，生成可直接导入
  ForkRunner 数据生成模块的 Excel 测试数据。仅处理请求头和请求体中已经存在的字段。
allowed-tools: Read
---

# ForkRunner 智能测试数据生成器

## 职责

依据场景规则生成字段级测试数据，并提交分析结果供 ForkRunner 后端生成 `output_scenarios_data.xlsx`。本 Skill 只改变当前步骤 `request_header`（HEAD）和请求体（BODY）中已经存在的字段，不增加字段，也不读取接口文档或格式模板。

全部执行在调用方指定的任务工作目录内。不得询问用户文件写入权限；权限由 ForkRunner 调用 Claude Agent SDK 时预先限制和授予。

## 输入

任务目录必须包含：

| 文件 | 用途 |
|---|---|
| `input_scenarios.json` | 后端从场景规则表第 2 行起按前三列预处理的当前批次场景数组，每项含全任务唯一的 `source_index`；Agent只读取此文件 |
| `request_snapshot.json` | `{"HEAD": {字段路径: 当前值}, "BODY": {字段路径: 当前值}}`；两个分区各自保持字段顺序，BODY 不得为空 |

不需要、也不得查找 `xlsx_format.xlsx`。
原始 `input_scenarios.xlsx` 由后端在任务根目录校验；当前批次目录无需该文件，Agent 不得打开它。

第 2 行起，序号和场景都为空的行会被跳过；其他行按原始内容交给 Agent 分析。

## 输出

Agent 每批最终返回 SDK 结构化对象 `{"analysis": [...]}`，不自行写入文件。ForkRunner 后端校验来源及字段，逐批追加到内部 CSV；全部批次成功后转为 `output_scenarios_data.xlsx`，工作簿格式固定为：

- 第 1 行：`["", "Head", ...HEAD 字段路径, "Body", ...BODY 字段路径]`
- 第 2 行起：`[场景描述, "", ...完整 HEAD 字段值, "", ...完整 BODY 字段值]`
- 每个场景都必须包含 HEAD 和 BODY 快照中的全部字段；未受约束字段沿用同分区快照值
- `Head`、`Body` 两个分区单元格保持为空
- 受约束字段使用黄色底色

## 执行流程

当前工作目录就是任务目录。场景规则和请求快照已由后端预处理；Agent只能读取文件，不执行脚本。首次批次须完整执行第 1 至 3 步；同一会话的续接批次只重新读取已更新的 `input_scenarios.json`，沿用此前已读取的快照和规则。如果续接时无法确认这些内容，明确失败，不得猜测或跳过规则。

1. 使用 Read 读取 `input_scenarios.json` 中的当前批次场景数组；每项保留 `source_index`、`序号`、`场景`、`场景类型`，不要重新打开原始 XLSX。

2. 首次批次使用 Read 读取 `request_snapshot.json` 中的 HEAD/BODY 字段快照；续接批次沿用同一任务的不变快照。

3. 首次批次使用 Read 阅读 `.claude/skills/forkrunner-test-data-generator/rules/00-完整规则与分析模板.md`，其中依次包含场景约束解析、约束拆分、场景描述构建、字段数据生成规则和分析结果 JSON 模板；续接批次沿用同一会话已读取的规则，不重复调用 Skill 或读取静态规则。完整掌握规则后再逐条分析场景。

   字段是否存在、默认值和输出顺序一律以 `request_snapshot.json` 为准。

4. 在最终回复中按 SDK 结构化输出契约交付 `{"analysis": [...]}`，其中 `analysis` 必须覆盖当前批次每个输入场景，拆成多行时重复原场景的 `source_index`，不得引用其他批次。不得使用 Write、Bash、Shell 重定向、临时脚本或其他命令创建分析文件。每项返回 `source_index`、`场景描述`、`字段改动` 和 `受约束字段`；`字段改动` 按 HEAD/BODY 分区，仅包含本行需要修改的字段路径和值，未改动字段由后端按快照补齐。不得在结果中加入快照以外的字段，无法映射到任一分区的字段约束直接忽略。同名路径出现在两个分区时，必须依据场景上下文确定分区，不得混写。

5. 返回当前批次的结构化分析结果后结束执行。后端逐批写入CSV，全部批次通过后转换为最终Excel；Agent 不得自行调用 `output_writer.py` 或写入工作簿。

涉及当前日期或相对时间时，必须使用系统提示词中由服务端提供的 `Asia/Shanghai` 基准日期时间与 Unix 时间戳。同一次执行的全部场景复用该基准，不得使用模型自带日期、重新查询系统时间或调用其他取时命令。系统提示词缺少基准时间时明确失败，不得估算。
