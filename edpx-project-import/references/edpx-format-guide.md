# EDPX 文件格式技术规范

> **格式版本**：3.7.10
> **目标应用**：万兴 / 亿图项目管理（Wondershare / Edraw Project Management）
> **依据**：真实文件逆向分析 + MPXJ 开源库对 EDPX schema 的解析逻辑交叉验证
> **用途**：为需要程序化新建、读取、修改 EDPX 文件的开发者与 AI 智能体提供完整格式定义

---

## 目录

1. [概述与容器结构](#1-概述与容器结构)
2. [document.xml 顶层结构](#2-documentxml-顶层结构)
3. [项目元数据字段](#3-项目元数据字段)
4. [GanttOption 甘特图选项](#4-ganttoption-甘特图选项)
5. [RowColumn 列定义与显示](#5-rowcolumn-列定义与显示)
6. [Task 任务元素](#6-task-任务元素)
7. [Task 全部属性详解](#7-task-全部属性详解)
8. [TextCell 单元格文本](#8-textcell-单元格文本)
9. [BarChart 甘特条](#9-barchart-甘特条)
10. [PredecessorLink 依赖关系](#10-predecessorlink-依赖关系)
11. [Calendars 日历与工时](#11-calendars-日历与工时)
12. [ResourceInfo 资源](#12-resourceinfo-资源)
13. [枚举值对照表](#13-枚举值对照表)
14. [新建与修改实操指南](#14-新建与修改实操指南)
15. [常见陷阱与验证方法](#15-常见陷阱与验证方法)

---

## 1. 概述与容器结构

EDPX 是万兴项目管理系统（Wondershare / Edraw Project Management）的原生文件格式，扩展名为 `.edpx`。它本质上是一个 **ZIP 压缩包**，内部装载若干 XML 文档。

一个有效的 EDPX 文件内部包含以下三个文件（所有路径均为相对路径）：

- `document.xml` — 主数据文件，承载全部项目内容
- `page/page.xml` — 页面占位，通常为空 `<Page/>` 根元素
- `rels/page_rels.xml` — 页面关系占位，通常为空 `<Relationships/>` 根元素

三个文件都必须在 ZIP 包内存在，缺一不可。压缩方式建议使用 `ZIP_DEFLATED`。

> **核心结论**：生成 EDPX = 构造合法的 `document.xml` + 两个空占位 XML，再整体打成 ZIP。根元素 `<Document>` 的属性、元素顺序、Enum 取值三者缺一不可。

最小可用的 `document.xml` 骨架：

```xml
<?xml version="1.0" encoding="UTF-8"?>
<Document DocGuid="{...}" Version="3.7.10" OS="Win">
  <CreatedVersion V="Win_3.3.0"/>
  <CreationDate V="2024-01-01T08:00:00"/>
  <Creator V="ws"/>
  <DateFormat V="yyyy-MM-dd"/>
  <MinutesPerDay V="480"/>
  <GanttOption> ... </GanttOption>
  <RowColumn> ... </RowColumn>
  <TaskList>
    <Task ... />
  </TaskList>
  <ResourceInfo/>
  <Calendars> ... </Calendars>
  <WaterMark/>
  <lineStyleInformation/>
</Document>
```

---

## 2. document.xml 顶层结构

根元素 `<Document>` 带三个必填属性，其子元素必须按固定顺序排列。元素顺序由 EDPX schema（JAXB 绑定）严格约束，顺序错乱会导致解析失败。

### 2.1 根元素属性

| 属性 | 示例值 | 说明 |
|---|---|---|
| `DocGuid` | `{dcb03870-...}` | 文档全局唯一标识，花括号包裹的 UUID（大写）。每次新建生成新值。 |
| `Version` | `3.7.10` | 文件格式版本。真实文件为 3.7.10，新建时直接使用该值最稳妥。 |
| `OS` | `Win` | 生成平台。Windows 为 `Win`，macOS 为 `Mac`。 |

### 2.2 子元素固定顺序

以下元素必须按此顺序依次出现，不能跳跃、不能交换位置：

```
<!-- 顺序 1-8：元数据 -->
CreatedVersion  CreationDate  Creator  LastSaved  Modifier  DPi
ScreenWidth     ScreenHeight

<!-- 顺序 9-12：时间/日历配置 -->
CalendarUID  MinutesPerDay  MinutesPerWeek  DaysPerMonth

<!-- 顺序 13-14 -->
DateFormat   m_isShowSpecificTime

<!-- 顺序 15+：内容主体 -->
GanttOption  RowColumn  TaskList  ResourceInfo  Calendars  WaterMark  lineStyleInformation
```

> **重要**：元素顺序是 EDPX 最常见的错误来源。即使每个元素内容都正确，只要顺序错一位，软件就会报"打开文件失败"。

### 2.3 实际文件示例（截取）

```xml
<?xml version="1.0" encoding="UTF-8"?>
<Document DocGuid="{dcb03870-8e03-42d7-a564-a2efc6bdb251}" Version="3.7.10" OS="Win">
  <CreatedVersion V="Win_3.3.0"/>
  <CreationDate V="2023-10-11T15:14:37"/>
  <Creator V="ws"/>
  <LastSaved V="2026-08-13T00:05:01"/>
  <Modifier V="60273"/>
  <DPi V="1"/>
  <ScreenWidth V="2560"/>
  <ScreenHeight V="1440"/>
  <CalendarUID V="0"/>
  <MinutesPerDay V="480"/>
  <MinutesPerWeek V="2400"/>
  <DaysPerMonth V="20"/>
  <DateFormat V="yyyy-MM-dd"/>
  <m_isShowSpecificTime V="TRUE"/>
  <!-- GanttOption / RowColumn / TaskList / ResourceInfo / Calendars / WaterMark / lineStyleInformation -->
</Document>
```

---

## 3. 项目元数据字段

元数据段位于 `TaskList` 之前，描述项目文档的基本属性和默认时间配置。所有元素都使用 `V="值"` 的单一属性形式。

| 元素 | 示例值 | 说明 |
|---|---|---|
| `CreatedVersion` | `Win_3.3.0` | 创建该文件的软件版本标识。 |
| `CreationDate` | `2023-10-11T15:14:37` | 创建时间，格式 `yyyy-MM-ddTHH:mm:ss`。 |
| `Creator` | `ws` | 创建者标识。程序生成可写任意字符串。 |
| `LastSaved` | `2026-08-13T00:05:01` | 最后保存时间，格式同上。 |
| `Modifier` | `60273` | 最后修改者标识，可为数字或字符串。 |
| `DPi` | `1` | 显示分辨率倍率，真实文件通常为 1。 |
| `ScreenWidth` | `2560` | 创建时屏幕宽度（像素）。 |
| `ScreenHeight` | `1440` | 创建时屏幕高度（像素）。 |
| `CalendarUID` | `0` | 默认日历 ID，对应 `Calendars` 段中的日历。 |
| `MinutesPerDay` | `480` | 每个工作日包含的分钟数。480 = 8 小时标准工作日。若需 24 小时日历可设 1440。 |
| `MinutesPerWeek` | `2400` | 每周工作分钟数。2400 = 5 个工作日 × 480 分钟。 |
| `DaysPerMonth` | `20` | 每月工作天数。 |
| `DateFormat` | `yyyy-MM-dd` | 日期显示格式字符串。 |
| `m_isShowSpecificTime` | `TRUE` | 是否显示具体时间（时分）。布尔值，大写 TRUE/FALSE。 |

> **关于 8 点前的任务**：软件默认按 8 小时工作制（`MinutesPerDay=480`）计算，因此早于 8:00 开始的任务在自动排程下可能被调整。要保留任意时刻的精确时间，需使用**手动排程**（`--manual-schedule`，`Manual="1"`）+ 24 小时日历（`MinutesPerDay=1440` 且工作日全天工作）。详见第 11、14 章。

---

## 4. GanttOption 甘特图选项

`GanttOption` 定义甘特图网格、时间刻度和显示选项。它的子元素顺序同样固定。

```xml
<GanttOption>
  <ProjectUnit V="5"/>
  <BaselineCost V="0"/>
  <StartDate V="2024-03-01T06:30:00"/>
  <FinishDate V="2024-03-22T18:00:00"/>
  <Auto V="1"/>
  <ThemeIndex V="0"/>
  <MajorUnit V="4" SingleStep="1" DispFormatId="20"/>
  <MinorUnit V="5" SingleStep="1" DispFormatId="41"/>
  <TimeLineScaleRate V="1"/>
  <ShowCritical V="FALSE"/>
  <ShowDelayed V="FALSE"/>
  <DurationFormatInfos/>
</GanttOption>
```

| 元素 | 示例值 | 说明 |
|---|---|---|
| `ProjectUnit` | `5` | 项目默认工期显示单位。5=小时，7=工作日。见第 13 章枚举。 |
| `BaselineCost` | `0` | 基线成本。 |
| `StartDate` | `2024-03-01T06:30:00` | 项目整体开始时间。 |
| `FinishDate` | `2024-03-22T18:00:00` | 项目整体结束时间。 |
| `Auto` | `1` | 进度条是否自动更新（**注意：与任务排程模式无关**）。任务排程由每个任务的 `Manual` 属性控制。 |
| `ThemeIndex` | `0` | 主题索引，默认 0。 |
| `MajorUnit` | `4` | 主时间刻度单位。**4=日**（默认）。 |
| `MinorUnit` | `5` | 次时间刻度单位。**5=时**（默认）。 |
| `TimeLineScaleRate` | `1` | 时间轴缩放比例。 |
| `ShowCritical` / `ShowDelayed` | `FALSE` | 是否高亮关键路径 / 延迟任务。 |

**MajorUnit / MinorUnit 枚举**（甘特图时间刻度）：

| 值 | 单位 |
|---|---|
| `1` | 年 |
| `2` | 月 |
| `3` | 周 |
| `4` | 日 |
| `5` | 时 |
| `6` | 分 |

> 主单位=日、次单位=时 时，`MajorUnit="4"`、`MinorUnit="5"`，各带 `SingleStep="1"` 与对应 `DispFormatId`（刻度标签显示格式 ID）。

真实文件还可能在 `GanttOption` 末尾追加 `GraphicsViewsSettings` 等可选元素。最小可用文件核心元素即可被软件识别。

---

## 5. RowColumn 列定义与显示

`RowColumn` 定义甘特图左侧表格的列。它包含一个 `ColumnList`，其中每个 `<Column>` 代表一列。列的顺序、宽度、显示/隐藏状态都由这里决定。

```xml
<RowColumn>
  <ColumnList>
    <Column Key="1" Width="215" IsHide="0" FieldType="1" ColIndex="0">
      <Text TextFormatMask="0" PlainText="任务">
        <!-- 列头文本格式，结构与 TextCell 的 TextBlock 一致 -->
      </Text>
    </Column>
    <!-- ... 其他列 ... -->
  </ColumnList>
</RowColumn>
```

### 5.1 常用列 FieldType 对照

| FieldType | 列含义 | 说明 |
|---|---|---|
| `0` | 序号 | 行号列，通常 Key=0。 |
| `38` | WBS | 工作分解结构编码列。 |
| `1` | 任务 | 任务名称列，核心列。 |
| `2` | 开始时间 | 任务开始时间。 |
| `3` | 完成时间 | 任务结束时间。 |
| `4` | 工期 | 任务工期。 |
| `37` | 前置任务 | 依赖关系显示列。 |
| `5` | 进度 | 完成百分比。 |
| `41` | 状态 | 任务进度状态。 |
| `6` | 优先级 | 任务优先级。 |
| `7` | 资源 | 资源分配。 |
| `42` | 实际工期 | 实际完成工期。 |
| `25` | 自定义 | 自定义列（Key=43/513）。 |

### 5.2 列显示控制

- `IsHide="0"` — 显示该列。
- `IsHide="1"` — 隐藏该列，但数据仍保留在 `TextCell` 中。

> **隐藏列不丢数据**：隐藏一列（如"前置任务"）只是不显示，不会删除该列对应的数据和依赖关系。任务间的 `PredecessorLink` 结构独立于列显示，始终保留。

---

## 6. Task 任务元素

所有任务都位于 `TaskList` 内，每个任务是一个 `<Task>` 元素。任务通过 `ParentID` 建立父子层级，通过 `PredecessorLink` 建立依赖。

```xml
<Task ID="3" RowID="3" ParentID="2" Name="用户调研" Level="3"
      Manual="1" Milestone="0" CriticalPath="0" Percent="1"
      DurationSecs="118800" DurationUnits="5" ManualDurationSecs="118800"
      Priority="8" Cost="0" RemainingCost="0" Work="0" BaselineCost="0"
      DateStart="2024-03-01T06:30:00" DateManualStart="2024-03-01T06:30:00"
      DateBaseStart="2024-03-01T06:30:00" DateFinish="2024-03-02T15:30:00"
      DateManualFinish="2024-03-02T15:30:00" DateBaseFinish="2024-03-02T15:30:00"
      Wbs="1.1.1" ParentID="2" ProgressStatus="3" ActualStart="0" ActualFinish="0">
  <Format/>
  <ResourceList/>
  <PredecessorLink>
    <PredecessorUID>2</PredecessorUID>
    <Type>1</Type>
    <LinkLag>0</LinkLag>
    <LagFormat>7</LagFormat>
    <CrossProject>0</CrossProject>
  </PredecessorLink>
  <Texts>
    <!-- 每个列一个 TextCell -->
  </Texts>
  <BarChart> ... </BarChart>
</Task>
```

### 6.1 子元素顺序

Task 的子元素必须按以下顺序：`Format` → `ResourceList` → `PredecessorLink`（可多个）→ `Texts` → `BarChart`。

### 6.2 父子任务与层级

- `Level` — 层级深度。根任务为 1，子任务递增。
- `ParentID` — 父任务的 `ID`。
- `Wbs` — 工作分解编码，如 `1`、`1.1`、`1.1.1`。

父任务的 `DateStart/DateFinish` 应覆盖其所有子任务的时间范围。父任务通常 `DurationSecs="0"`（汇总项）。

---

## 7. Task 全部属性详解

Task 元素携带大量属性。下表汇总了真实文件中出现的全部属性键及其语义。生成文件时，**核心必填**的属性已加粗标注。

| 属性 | 示例值 | 说明 |
|---|---|---|
| **`ID`** | `3` | 任务唯一标识，从 1 递增，用于 ParentID 和 PredecessorLink 引用。 |
| `RowID` | `3` | 行号，通常与 ID 相同。 |
| **`ParentID`** | `2` | 父任务 ID。根任务为 0（或 -1）。 |
| **`Name`** | `用户调研` | 任务名称。 |
| **`Level`** | `3` | 层级深度，根任务为 1。 |
| **`Manual`** | `0` | 排程模式。**0=自动排程**（后续任务按前置任务自动排期，默认），**1=手动**（保留精确时间）。 |
| `Milestone` | `0` | 是否里程碑。1=是，0=否。 |
| `CriticalPath` | `0` | 是否关键路径任务。 |
| `Percent` | `1` | 完成度，0~1 的小数。（1=100%） |
| `ProgressStatus` | `3` | 进度状态枚举。见第 13 章。 |
| `DurationSecs` | `118800` | 任务工期（秒）。 |
| `DurationUnits` | `5` | 工期显示单位枚举。5=小时，7=工作日。见第 13 章。 |
| `ManualDurationSecs` | `118800` | 手动模式的工期（秒），与 DurationSecs 一致。 |
| `Priority` | `8` | 优先级。真实文件为 8。 |
| `Cost` | `0` | 成本。 |
| `RemainingCost` | `0` | 剩余成本。 |
| `Work` | `0` | 工作量。 |
| `BaselineCost` | `0` | 基线成本。 |
| `ActualDuration` | `720000` | 实际工期（秒）。 |
| `ActualStart` | `0` | 实际开始时间（Unix 秒时间戳）。0=无/未开始。 |
| `ActualFinish` | `0` | 实际结束时间（Unix 秒时间戳）。0=无。 |
| **`DateStart`** | `2024-03-01T06:30:00` | 计划开始时间。 |
| `DateManualStart` | `2024-03-01T06:30:00` | 手动模式开始时间。自动排程（`Manual=0`）时留空，软件按前置任务重算；手动模式（`Manual=1`）时与 `DateStart` 一致。 |
| `DateBaseStart` | `2024-03-01T06:30:00` | 基线开始时间。 |
| **`DateFinish`** | `2024-03-02T15:30:00` | 计划结束时间。 |
| `DateManualFinish` | `2024-03-02T15:30:00` | 手动模式结束时间。自动排程时留空；手动模式时与 `DateFinish` 一致。 |
| `DateBaseFinish` | `2024-03-02T15:30:00` | 基线结束时间。 |
| `DateLateStart` | （空） | 最晚开始时间（可空）。 |
| `DateLateFinish` | （空） | 最晚结束时间（可空）。 |
| **`Wbs`** | `1.1.1` | 工作分解编码。 |
| `Notes` | `备注` | 任务备注。 |
| `Childs` | `2` | 子任务数量。 |
| `RowHeight` | `54` | 行高（像素）。 |
| `FontColor` | `#000000` | 字体颜色。 |
| `BackGroundColor` | （空） | 背景颜色。 |
| `FirstWidth` | `215` | 首列宽度。 |
| `CountWidth` | `215` | 宽度计数。 |
| `IsToggler` | `0` | 折叠开关状态。 |
| `ShowByChild` | `1` | 是否按子任务显示。 |
| `HideByColumns` | `0` | 是否被列隐藏。 |
| `SplitOffsetDuration` | （空） | 拆分偏移工期（可空）。 |
| `SplitPointList` | （空） | 拆分点列表（可空）。 |
| `ActualMilestone` | （空） | 实际里程碑（可空）。 |
| `EarlySlack` | （空） | 最早松弛时间。 |
| `LateSlack` | （空） | 最晚松弛时间。 |
| `LastSaveDate` | （空） | 最后保存日期。 |
| `BaseLineNumber` | （空） | 基线编号。 |
| `BaseLinePointxBegin` | `0` | 基线起点 x。 |
| `BaseLinePointxEnd` | （空） | 基线终点 x。 |
| `Resources` | （空） | 资源字符串。 |
| `m_IbartextIndex` | （空） | 条形图文本索引（留空）。 |
| `m_bebartextIndex` | （空） | 条形图文本索引（留空）。 |
| `m_Istyletext` | （空） | 样式文本（留空）。 |
| `m_Bestyletext` | （空） | 样式文本（留空）。 |
| `DurationUnitCustomSuffix` | （空） | 自定义工期单位后缀（可空）。 |

> **排程模式速记**：默认自动排程（`Manual="0"`）→ 后续任务按前置任务自动排期，`DateManualStart/DateManualFinish` 留空。若要保留精确时间（如 6:30 开始），需 `Manual="1"`（手动排程）+ `DateManualStart/DateManualFinish` 与 `DateStart/DateFinish` 相同 + 日历一致。否则软件会按日历/依赖重新计算时间。

---

## 8. TextCell 单元格文本

每个 Task 的 `<Texts>` 内包含多个 `<TextCell>`，每个对应表格中的一列，用于展示该任务在该列的值。TextCell 通过 `FieldID` 与列对应，与 `Column` 的 `FieldType` 一致。

### 8.1 TextCell 属性

| 属性 | 说明 |
|---|---|
| `FieldID` | 对应列字段类型，如 1=任务名、2=开始时间、37=前置任务。 |
| `DataType` | 数据类型。0=文本，2=日期/数字，3/10=其他。见第 13 章。 |
| `FieldType` | 与 FieldID 相同。 |
| `PlainText` | 单元格显示的纯文本内容。 |

### 8.2 TextCell 完整结构

```xml
<TextCell FieldID="1" DataType="0" FieldType="1" PlainText="用户调研">
  <Varient/>
  <TextBlock VAlign="Center" HAlign="Center" TextFormatMask="49664">
    <Character Family="微软雅黑" Color="#000000" IX="0" Size="9" Style="1"/>
    <Paragraph Align="1" SpLine="100" IX="0"/>
    <WrapMode Value="1"/>
    <FillColor Color="#ffffff"/>
    <Color V="#ff000000"/>
    <Text>
      <pp PX="0" CX="0">
        <tp CX="0">用户调研</tp>
      </pp>
    </Text>
  </TextBlock>
</TextCell>
```

### 8.3 TextBlock 关键子元素

- `Character` — 字体：`Family`（微软雅黑）、`Size`（字号）、`Color`、`Style`。
- `Paragraph` — 段落：`Align`（1=左对齐等）、`SpLine`（行距）。
- `WrapMode Value="1"` — 自动换行。
- `tp` — 实际文本内容。

> **最小可用的 TextCell**：若只关心数据能被软件读取，`TextCell` 的 `PlainText` 属性是核心；`TextBlock` 内的 `tp` 文本应与之一致，用于显示渲染。生成时可复用同一套 TextBlock 模板，仅替换 `PlainText` 和 `tp` 内容。

---

## 9. BarChart 甘特条

`BarChart` 定义甘特图中该任务的条形图及其上/内部显示的文本。它包含一个 `TextFormat`（文本格式）和 `Texts`（内含 `BarChartText` 列表）。

```xml
<BarChart>
  <TextFormat TextFormatMask="0">
    <Character Family="微软雅黑" IX="0" Size="9"/>
    <Paragraph Align="4" SpLine="100" IX="0"/>
    <WrapMode Value="1"/>
    <FillColor Color="#000000"/>
    <Color V="#ff000000"/>
  </TextFormat>
  <Texts>
    <BarChartText FieldID="1" Value="软件研发项目" Type="2" Pos="4"/>
    <BarChartText FieldID="5" Value="100%" Type="2" Pos="1"/>
  </Texts>
</BarChart>
```

### 9.1 BarChartText 属性

| 属性 | 取值 | 说明 |
|---|---|---|
| `FieldID` | `1` / `5` | 显示的数据字段。1=任务名，5=进度。 |
| `Value` | 文本 | 缓存渲染后的显示值。软件会按 FieldID 对应字段重新推导。 |
| `Type` | `2` | 固定为 2，表示条形图题注文本。 |
| `Pos` | `4` / `1` | 文本位置。4=条形图上侧，1=条形图内部。 |

### 9.2 控制条形图内部/上侧文本

- `Pos="4"` — 显示在条形图**上侧**，通常为任务名（FieldID=1）。
- `Pos="1"` — 显示在条形图**内部**，通常为进度（FieldID=5）。

> **去掉条形图内部文本**：要隐藏条形图内部的进度显示，应**删除** `BarChartText FieldID="5" Pos="1"` 这一行，仅保留上侧的任务名（`FieldID="1" Pos="4"`）。注意：`Value` 只是缓存渲染值，若删除不彻底，软件打开时会按 FieldID 重新生成，因此必须以"删除整个 BarChartText 元素"为准。

---

## 10. PredecessorLink 依赖关系

任务依赖通过 `PredecessorLink` 定义，位于 Task 子元素顺序中的第三位（`Format` 之后、`Texts` 之前）。一个任务可以有多个 `PredecessorLink`，表示多个前置任务。

```xml
<PredecessorLink>
  <PredecessorUID>3</PredecessorUID>
  <Type>1</Type>
  <LinkLag>0</LinkLag>
  <LagFormat>7</LagFormat>
  <CrossProject>0</CrossProject>
</PredecessorLink>
```

### 10.1 子元素说明

| 元素 | 取值范围 | 说明 |
|---|---|---|
| `PredecessorUID` | 任务 ID | 前置任务的 ID。 |
| `Type` | `1` | 依赖类型。1=FS（完成-开始）。见第 13 章。 |
| `LinkLag` | `0` | 滞后量（与 LagFormat 单位配合）。 |
| `LagFormat` | `7` | 滞后单位。7=工作日。 |
| `CrossProject` | `0` | 是否跨项目。 |

> **依赖与列显示无关**：`PredecessorLink` 是任务间的逻辑关系，独立于"前置任务"列的显示状态。即使隐藏了 FieldType=37 列，依赖关系依然生效，甘特图中仍会绘制连接箭头。

---

## 11. Calendars 日历与工时

`Calendars` 段定义项目日历，决定哪些天是工作日、每天的工作时段。它是"8 点前任务被调整"问题的关键所在。

```xml
<Calendars>
  <Calendar UID="0" Name="Standard">
    <WeekDays>
      <WeekDay>
        <DayType>1</DayType>
        <DayWorking>0</DayWorking>
      </WeekDay>
      <WeekDay>
        <DayType>2</DayType>
        <DayWorking>1</DayWorking>
        <WorkingTimes>
          <WorkingTime>
            <FromTime>08:00:00</FromTime>
            <ToTime>12:00:00</ToTime>
          </WorkingTime>
          <WorkingTime>
            <FromTime>13:00:00</FromTime>
            <ToTime>17:00:00</ToTime>
          </WorkingTime>
        </WorkingTimes>
      </WeekDay>
      <!-- DayType 3-7 与 2 类似，均为工作日 -->
    </WeekDays>
  </Calendar>
</Calendars>
```

### 11.1 字段说明

- `Calendar UID` — 日历 ID，与顶层 `CalendarUID` 对应。
- `DayType` — 星期几，1=周日，2=周一，…，7=周六。见第 13 章。
- `DayWorking` — 是否工作日。1=是，0=否。
- `WorkingTime` — 每天的工作时段，可多个（如上午+下午）。

### 11.2 保留 8 点前精确时间的关键配置

- **方案 A（推荐，保留任意时刻）**：设置 24 小时日历——顶层 `MinutesPerDay=1440`，每个工作日 `WorkingTime` 为 `00:00:00` 到 `23:59:00`，同时任务 `Manual="1"`。这样 6:30、7:00 等任意时刻都能精确保留。
- **方案 B（标准 8 小时制）**：顶层 `MinutesPerDay=480`，工作日为 08:00-12:00、13:00-17:00。此方案下早于 8:00 的任务会被软件自动调整到 8:00。

---

## 12. ResourceInfo 资源

`ResourceInfo` 段定义项目资源（人员、设备等）。若项目不涉及资源，可保持空元素 `<ResourceInfo/>`。它位于 `TaskList` 之后、`Calendars` 之前。

```xml
<ResourceInfo/>
<!-- 或包含资源列表 -->
<ResourceInfo>
  <ResourceList>
    <Resource ID="1" Name="张三" Type="1"/>
  </ResourceList>
</ResourceInfo>
```

任务侧的 `<ResourceList/>` 子元素（位于 Task 内）用于为该任务分配资源；若不需要可为空。资源分配也通过 Task 的 `Resources` 属性以字符串形式表达。

---

## 13. 枚举值对照表

EDPX 大量使用整型枚举。生成时取错枚举值会导致单位、状态、依赖显示错误。以下为验证过的枚举表。

### 13.1 DurationUnits 工期单位

| 值 | 单位 |
|---|---|
| `5` | 小时 |
| `6` | 分钟 |
| `7` | 工作日 |
| `8` | 周 |
| `10` | 天 |

### 13.2 ProgressStatus 进度状态

| 值 | 含义 |
|---|---|
| `0` | 未开始 |
| `1` | 进行中（未开始但已排程） |
| `2` | 进行中（延迟） |
| `3` | 已完成 |
| `4` | 进行中 |

### 13.3 DayType 星期

| 值 | 星期 |
|---|---|
| `1` | 周日 |
| `2` | 周一 |
| `3` | 周二 |
| `4` | 周三 |
| `5` | 周四 |
| `6` | 周五 |
| `7` | 周六 |

### 13.4 RelationType 依赖类型

| 值 | 类型 | 全称 |
|---|---|---|
| `1` | FS | 完成-开始（Finish to Start） |
| `2` | SS | 开始-开始（Start to Start） |
| `3` | FF | 完成-完成（Finish to Finish） |
| `4` | SF | 开始-完成（Start to Finish） |

### 13.5 LagFormat 滞后单位

| 值 | 单位 |
|---|---|
| `7` | 工作日 |

### 13.6 TextCell DataType

| 值 | 含义 |
|---|---|
| `0` | 文本 |
| `1` | 数字 |
| `2` | 日期/时间 |
| `3` | 其他（如工期） |
| `9` | 其他 |
| `10` | 其他（如状态） |

---

## 14. 新建与修改实操指南

### 14.1 新建 EDPX 文件流程

1. **构造 document.xml**：按第 2 章的元素顺序生成 XML。先写元数据，再写 `GanttOption`、`RowColumn`、`TaskList`、`ResourceInfo`、`Calendars`，最后补 `WaterMark` 和 `lineStyleInformation` 空元素。
2. **生成任务**：遍历任务表，为每个任务生成 `<Task>` 元素，包含全部核心属性、`Texts`（每列一个 TextCell）、`BarChart`、依赖 `PredecessorLink`。
3. **构造占位文件**：创建 `page/page.xml`（内容 `<?xml version="1.0"?><Page/>`）和 `rels/page_rels.xml`（内容 `<?xml version="1.0"?><Relationships/>`）。
4. **打包 ZIP**：将三个文件按相对路径压入 ZIP（deflate 压缩），得到 `.edpx` 文件。

### 14.2 Python 生成 EDPX 参考实现

```python
# 生成 EDPX 的核心步骤（Python）
import zipfile, io, uuid

# 1. 组装 document.xml 字符串（按元素顺序）
xml_parts = []
xml_parts.append('<?xml version="1.0" encoding="UTF-8"?>')
xml_parts.append('<Document DocGuid="{%s}" Version="3.7.10" OS="Win">'
                 % str(uuid.uuid4()).upper())
# ... 按序追加元数据、GanttOption、RowColumn、TaskList、ResourceInfo、Calendars ...
xml_parts.append('<WaterMark/>')
xml_parts.append('<lineStyleInformation/>')
xml_parts.append('</Document>')
document_xml = "\n".join(xml_parts)

# 2. 占位文件
page_xml = '<?xml version="1.0" encoding="utf-8"?><Page/>'
rels_xml = '<?xml version="1.0" encoding="utf-8"?><Relationships/>'

# 3. 打包 ZIP
buf = io.BytesIO()
with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
    zf.writestr('document.xml', document_xml.encode('utf-8'))
    zf.writestr('page/page.xml', page_xml)
    zf.writestr('rels/page_rels.xml', rels_xml)
with open('output.edpx', 'wb') as f:
    f.write(buf.getvalue())
```

### 14.3 修改已有 EDPX 文件

1. **解压**：用 ZIP 工具解压 `.edpx`，读取 `document.xml`。
2. **解析**：用 XML 解析器（如 Python `xml.etree` 或 Java DOM）解析，注意保留元素顺序。
3. **定位修改**：按需调整任务属性、TextCell、BarChart、依赖、日历等节点。
4. **重新打包**：按原路径重新压入三个文件，覆盖为新的 `.edpx`。

> **修改时务必保留结构**：只改动目标节点的属性值或文本，不要破坏元素顺序、不要删除 `DocGuid` 等根属性、不要改动 `Version`。重新打包时三个文件路径必须与原来一致。

### 14.4 生成任务的要点

- 每个任务必须有唯一递增的 `ID`，父任务 `ParentID` 指向其父的 `ID`。
- 父任务的 `DateStart` ≤ 所有子任务的最小开始时间，`DateFinish` ≥ 所有子任务的最大结束时间。
- `Wbs` 编码按层级生成：根为 `1`，子级为 `1.1`、`1.1.1`。
- 若需保留精确时间，任务设 `Manual="1"`，且 `DateManual*` 与 `Date*` 一致。
- 依赖通过 `PredecessorLink`（Type=1 表示 FS）表达，指向前置任务 `ID`。

---

## 15. 常见陷阱与验证方法

### 问题一：软件报"打开文件失败"

最常见原因是：文件不是 ZIP 包、元素顺序错乱、或缺少占位文件。验证：
- 用 `unzip -l file.edpx` 确认是 ZIP 且包含三个文件。
- 用 XML 校验器检查 `document.xml` 是否良构（well-formed）。
- 核对元素顺序是否与第 2 章一致。

### 问题二：开始时间被自动改成 8 点

原因是日历为 8 小时制且任务非手动排程。解决：采用 24 小时日历（`MinutesPerDay=1440`、工作日 00:00-23:59）并设 `Manual="1"`。详见第 11 章。

### 问题三：年份变成 1924 这类异常

通常是日期字符串格式错误或文本被当作数值解析。EDPX 中计划时间一律使用 `yyyy-MM-ddTHH:mm:ss` 格式（如 `2024-03-01T06:30:00`），不要用 Excel 导出的"常规"文本格式。验证：检查 `DateStart/DateFinish` 等属性是否为上述格式。

### 问题四：工期显示为工作日而非小时

由 `DurationUnits` 决定。要按小时显示设为 `5`，按工作日设为 `7`。同时 `GanttOption/ProjectUnit` 也应一致。详见第 13 章。

### 问题五：条形图内部仍显示进度

删除 `BarChart` 内的 `BarChartText FieldID="5"` 元素（而非仅改 Value）。软件会按 FieldID 重新推导文本，必须移除整个元素。详见第 9 章。

### 问题六：隐藏列后依赖消失

隐藏列（`IsHide="1"`）不影响 `PredecessorLink` 依赖。若依赖消失，说明 `PredecessorLink` 本身缺失或 `PredecessorUID` 指向了不存在的任务 ID。验证：解压检查 `PredecessorLink` 存在且 ID 有效。

### 通用验证清单

| 检查项 | 方法 |
|---|---|
| ZIP 结构 | `unzip -l output.edpx` 应含 3 个文件 |
| XML 良构 | XML 解析器解析 `document.xml` 无报错 |
| 元素顺序 | 与第 2 章顺序逐项核对 |
| 枚举取值 | DurationUnits/ProgressStatus/DayType 等用第 13 章值 |
| 日期格式 | 所有时间属性为 `yyyy-MM-ddTHH:mm:ss` |
| 任务引用 | ParentID / PredecessorUID 指向存在的任务 ID |
| 最终验证 | 用万兴项目管理软件打开，确认显示符合预期 |

---

*本规范基于万兴项目管理软件真实生成的 EDPX 文件逆向分析，并结合 MPXJ 开源库对 EDPX schema 的解析逻辑交叉验证。所有字段、枚举、结构均以真实文件为准，建议在实现后始终用目标软件做最终验证。*