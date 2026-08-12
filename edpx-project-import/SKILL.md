---
name: "edpx-project-import"
description: "Create, read, and modify .edpx project files (Wondershare/Edraw Project Management). Invoke when the user wants to import task/csv data into project management software, generate or edit .edpx files, fix EDPX open failures, adjust task times/units/dependencies, or analyze an existing .edpx file."
---

# EDPX 项目文件创建与修改

本 skill 封装了对万兴/亿图项目管理（Wondershare/Edraw Project Management）原生 `.edpx` 文件格式的完整逆向分析结论，用于程序化生成、读取、修改项目文件。

## 何时使用

- 用户需要把任务表（CSV/Excel）导入项目管理软件，生成 `.edpx` 文件。
- 用户需要新建、读取或修改 `.edpx` 项目文件。
- 用户报告 `.edpx` 文件在软件中打不开、时间被自动调整、单位显示错误、依赖丢失等问题。
- 用户提供一个 `.edpx` 文件要求解析其内容或结构。

## 输入契约（调用方必须提供）

本 skill 的输入是一份**任务表**（CSV 或 Excel 文件）。调用方必须提供满足以下契约的数据；数据不合规时，智能体应主动生成 CSV 模板请调用方填写，或根据口述任务代为整理成合规表格。

### 列定义（表头固定，大小写不敏感）

| 列名 | 必填 | 类型 | 规则 |
|---|---|---|---|
| `任务名称` | **是** | 文本 | 非空，任务显示名称 |
| `开始时间` | **是** | 日期串 | 格式 `YYYY-MM-DD HH:mm`，精确到分钟 |
| `结束时间` | **是** | 日期串 | 格式 `YYYY-MM-DD HH:mm`，须晚于开始时间 |
| `ID` | 否 | 整数 | 任务唯一编号；缺省自动递增 |
| `层级` | 否 | 整数 | 1=根，2=子，3=孙…；缺省 1 |
| `工期(小时)` | 否 | 数字 | 缺省按 开始-结束 自动计算 |
| `前置任务` | 否 | 文本 | 前置任务 ID，多个用 `;` 分隔，如 `3;6` |
| `依赖类型` | 否 | 文本 | `FS`/`SS`/`FF`/`SF`，与前置任务一一对应；缺省 `FS` |
| `里程碑` | 否 | 文本 | `是` 或留空 |
| `备注` | 否 | 文本 | 任务备注 |

### 数据校验规则

1. **日期格式**：接受 `2024-01-01 06:30`、`2024-01-01T06:30:00`、`2024-01-01 06:30:00`、`2024/01/01 06:30`。拒绝"常规"文本 / Excel 序列号格式，否则年份会异常（如变 1924）。
2. **父子关系**：`层级` 数字越大越靠下；父任务开始时间 ≤ 所有子任务、结束时间 ≥ 所有子任务。含子任务的父项自动识别为汇总任务。
3. **依赖引用**：`前置任务` 的 ID 必须已存在；`依赖类型` 与前置任务数量一一对应，缺省 `FS`。
4. **8 点前任务**：直接写精确时间（如 `06:30`），生成时自动采用 24 小时日历。若需保留精确时间用 `--manual-schedule`（手动排程）；默认自动排程下后续任务会按前置任务自动排期。
5. **后续任务自动排期**：默认自动排程（`Manual="0"`），软件基于前置任务（`PredecessorLink`）自动重算后续任务的开始/结束时间。若需保留每个任务的精确时间，加 `--manual-schedule`。

### 生成 CSV 模板

```bash
python references/task_importer.py --template template.csv
```

模板示例：

```csv
ID,层级,任务名称,开始时间,结束时间,工期(小时),前置任务,依赖类型,里程碑,备注
1,1,项目名称,2024-01-01 08:00,2024-01-30 18:00,,,,,项目总目标
2,2,阶段一,2024-01-01 08:00,2024-01-10 18:00,,,,,
3,3,任务A,2024-01-01 06:30,2024-01-03 17:00,17,,,,8点前开始
4,3,任务B,2024-01-03 17:00,2024-01-07 12:00,25,3,FS,,接续任务A
```

## 核心概念（必须掌握）

```
EDPX 不是纯 XML，而是一个 ZIP 压缩包，内部包含三个文件：
  document.xml       主数据文件（全部项目内容）
  page/page.xml      页面占位（空 <Page/>）
  rels/page_rels.xml 页面关系占位（空 <Relationships/>）
```

生成 EDPX = 构造合法的 `document.xml` + 两个空占位 XML，再整体打成 ZIP（deflate 压缩）。三个文件路径必须与上面一致，缺一不可。

## 关键约束速查

### document.xml 根元素与元素顺序

根元素 `<Document>` 带三个必填属性：`DocGuid="{UUID大写}"`、`Version="3.7.10"`、`OS="Win"`。

子元素必须按固定顺序，顺序错乱会导致软件报"打开文件失败"：

```
CreatedVersion CreationDate Creator LastSaved Modifier DPi ScreenWidth ScreenHeight
CalendarUID MinutesPerDay MinutesPerWeek DaysPerMonth
DateFormat m_isShowSpecificTime
GanttOption RowColumn TaskList ResourceInfo Calendars WaterMark lineStyleInformation
```

### 排程模式：自动排期 vs 手动保留

软件通过每个任务的 `Manual` 属性控制排程模式：

| 模式 | Manual | 说明 |
|---|---|---|
| **自动排程（默认）** | `0` | 后续任务根据前置任务（`PredecessorLink`）自动重算开始/结束时间，用于任务依赖接续场景。`DateManualStart/DateManualFinish` 留空。 |
| **手动保留** | `1` | 保留每个任务 `DateStart/DateFinish` 的精确时间，且 `DateManualStart/DateManualFinish` 与之一致。用于需要固定 8 点前等任意时刻的场景。 |

> 脚本默认自动排程；加 `--manual-schedule` 切换为手动保留。注意：`GanttOption/Auto` 控制的是进度条自动更新（非任务排程），与 `Manual` 无关。

### 保留 8 点前精确时间的方案（手动模式）

软件默认 8 小时工作制会把早于 8:00 的任务自动调整。要保留任意时刻（需配合 `--manual-schedule`）：

1. 顶层 `MinutesPerDay="1440"`（24 小时）。
2. `Calendars` 中每个工作日 `WorkingTime` 为 `00:00:00` 到 `23:59:00`。
3. 每个任务设 `Manual="1"`（手动排程），且 `DateManualStart/DateManualFinish` 与 `DateStart/DateFinish` 一致。

### 关键枚举值

| DurationUnits（工期单位） | 值 |
|---|---|
| 小时 | `5` |
| 分钟 | `6` |
| 工作日 | `7` |
| 周 | `8` |
| 天 | `10` |

| 甘特图时间刻度 MajorUnit/MinorUnit | 值 |
|---|---|
| 年 | `1` |
| 月 | `2` |
| 周 | `3` |
| **日（主单位默认）** | `4` |
| **时（次单位默认）** | `5` |
| 分 | `6` |

| 列 FieldType | 含义 |
|---|---|
| `0` | 序号 |
| `38` | WBS |
| `1` | 任务名 |
| `2` / `3` | 开始 / 完成时间 |
| `4` | 工期 |
| `37` | 前置任务 |
| `5` | 进度 |
| `41` | 状态 |
| `6` / `7` | 优先级 / 资源 |

| 依赖 Type | 含义 |
|---|---|
| `1` | FS 完成-开始 |
| `2` | SS 开始-开始 |
| `3` | FF 完成-完成 |
| `4` | SF 开始-完成 |

| ProgressStatus | 含义 |
|---|---|
| `0` | 未开始 |
| `3` | 已完成 |

`DurationUnits` 决定工期显示单位：要按小时显示设为 `5`（同时 `GanttOption/ProjectUnit` 一致）。

## 常用操作

### 1. 生成 EDPX 文件

1. 读取任务数据（CSV 用 `utf-8-sig` 编码避免中文乱码；Excel 用 openpyxl）。
2. 按根元素顺序构造 `document.xml`。
3. 为每个任务生成 `<Task>` 元素（含核心属性、`Texts` 内每列一个 `TextCell`、`BarChart`、依赖 `PredecessorLink`）。
4. 创建两个占位 XML。
5. 三个文件压成 ZIP，扩展名为 `.edpx`。

完整可运行的参考实现见 `references/task_importer.py`：

```bash
python references/task_importer.py input.csv -o output.edpx                    # 自动排程(默认)
python references/task_importer.py input.csv -o output.edpx --manual-schedule  # 手动保留精确时间
python references/task_importer.py input.xlsx -o output.edpx
python references/task_importer.py --template template.csv   # 生成 CSV 模板
```

运行前确认脚本依赖（openpyxl）已安装：`pip install openpyxl --break-system-packages`。

生成的甘特图默认主单位=日（`MajorUnit=4`）、次单位=时（`MinorUnit=5`）。

### 2. 修改已有 EDPX 文件

1. 用 ZIP 工具解压 `.edpx`，读取 `document.xml`。
2. 用 XML 解析器解析（注意保留元素顺序）。
3. 只改动目标节点的属性值或文本，不破坏元素顺序、不删 `DocGuid`/`Version`。
4. 按原路径重新压回三个文件，覆盖保存。

### 3. 常见调优

- **工期按小时显示**：`DurationUnits="5"`，且 `GanttOption/ProjectUnit` 一致。
- **甘特图时间刻度设为日/时**：`GanttOption` 中 `MajorUnit="4"`（日）、`MinorUnit="5"`（时），并各带 `SingleStep="1"` 与对应 `DispFormatId`。
- **隐藏某列**（如前置任务）：该 `Column` 设 `IsHide="1"`。数据与依赖仍保留，`PredecessorLink` 独立于列显示，不受影响。
- **去掉条形图内部进度文本**：删除 `BarChart` 内的 `<BarChartText FieldID="5" .../>` 元素（整个元素，不是只改 Value）。
- **自动设置进度**：写 `Percent`（0~1）、`ProgressStatus`、`ActualStart/ActualFinish`（Unix 秒时间戳）。

## 验证流程

生成后必须验证，避免软件打不开：

1. `unzip -l output.edpx` 确认是 ZIP 且含 3 个文件。
2. XML 解析器解析 `document.xml` 无报错。
3. 元素顺序与本章一致。
4. 枚举取值、日期格式正确。
5. `ParentID`/`PredecessorUID` 指向存在的任务 ID。
6. 用万兴项目管理软件打开做最终确认。

## 更多细节

字段全表、枚举全表、TextCell/BarChart/PredecessorLink/Calendars 完整结构、常见陷阱与排查，见 `references/edpx-format-guide.md`。遇到本 skill 未覆盖的边界情况，以真实 EDPX 文件为准做交叉验证。