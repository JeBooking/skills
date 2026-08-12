#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
万兴项目管理(EdrawProj / 亿图项目管理)任务导入工具 v3
====================================================
将CSV/Excel任务表直接转换为 EDPX 文件(万兴项目管理原生格式)，
用户通过 文件 -> 打开 该.edpx 文件即可。

解决的核心问题：
  1. 使用24小时全天候日历，避免8点前任务被强制调整
  2. 支持任务时间精确到分钟
  3. 支持父任务和子任务层级关系
  4. 支持任务间依赖接续(FS/SS/FF/SF)
  5. 后续任务自动排期：默认自动排程(Manual=0)，后续任务按前置任务自动排期；
     使用 --manual-schedule 可切换为手动排程(Manual=1)以保留精确时间
  6. 甘特图主单位=日、次单位=时

原理(基于MPXJ对EDPX schema的逆向分析)：
  - EDPX是XML文件，根元素<Document>，日期格式为 yyyy-MM-ddTHH:mm:ss
  - 设置24小时日历(MinutesPerDay=1440)，WorkingTimes FromTime=00:00:00 ToTime=23:59:00
  - 自动排程：任务Manual=0，软件按前置任务(PredecessorLink)自动重算后续任务时间
  - 手动排程：任务Manual=1，DateManualStart/Finish与DateStart/Finish一致保留精确时间
  - 甘特图时间刻度：MajorUnit=4(日)、MinorUnit=5(时)

用法：
  python task_importer.py input.csv -o output.edpx          # 自动排程(默认)
  python task_importer.py input.csv -o output.edpx --manual-schedule  # 手动保留精确时间
  python task_importer.py input.xlsx -o output.edpx
  python task_importer.py --template template.csv    # 生成CSV模板

依赖：
  - 纯Python实现，无需MPXJ/java
  - 读取Excel需要 openpyxl: pip install openpyxl --break-system-packages
"""

import csv
import sys
import os
import uuid
import argparse
from datetime import datetime, timedelta
from xml.sax.saxutils import escape

# ============================================================
# 常量定义
# ============================================================

# 依赖类型映射
RELATION_TYPE_MAP = {
    "FS": None,  # Will be set after MPXJ import
    "SS": None,
    "FF": None,
    "SF": None,
}

CSV_TEMPLATE = """ID,层级,任务名称,开始时间,结束时间,工期(小时),前置任务,依赖类型,里程碑,备注
1,1,项目名称,2024-01-01 08:00,2024-01-30 18:00,,,,,项目总目标
2,2,阶段一,2024-01-01 08:00,2024-01-10 18:00,,,,,
3,3,任务A,2024-01-01 08:00,2024-01-03 17:00,17,,,,在下面填写你的任务
4,3,任务B,2024-01-03 17:00,2024-01-07 12:00,25,3,FS,,接续任务A
5,2,阶段二,2024-01-08 09:00,2024-01-15 18:00,,,,,
6,3,任务C,2024-01-08 09:00,2024-01-10 18:00,17,4,FS,,
7,3,任务D,2024-01-10 09:00,2024-01-12 18:00,25,6,FS,,
8,3,任务E,2024-01-13 09:00,2024-01-15 18:00,17,"6;7","FS;FS",,依赖任务C和D
"""


# ============================================================
# 数据模型
# ============================================================

class Task:
    """表示一个任务"""
    def __init__(self):
        self.id = 0
        self.level = 1
        self.name = ""
        self.start = None
        self.finish = None
        self.duration_hours = 0
        self.predecessors = []  # [(id, type_str), ...]
        self.milestone = False
        self.notes = ""
        self.is_summary = False


def parse_datetime(s):
    """解析日期时间字符串"""
    if not s or str(s).strip() == "":
        return None
    s = str(s).strip()
    formats = [
        "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
        "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M",
        "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M",
        "%Y-%m-%d", "%Y/%m/%d",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    raise ValueError(f"无法解析日期时间: '{s}'")


# ============================================================
# CSV/Excel读取
# ============================================================

def read_csv(filepath):
    tasks = []
    with open(filepath, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            tasks.append(parse_task_row(row))
    return tasks


def read_excel(filepath):
    try:
        from openpyxl import load_workbook
    except ImportError:
        print("错误: 读取Excel需要安装openpyxl")
        print("  pip install openpyxl --break-system-packages")
        sys.exit(1)

    wb = load_workbook(filepath, data_only=True)
    ws = wb.active
    headers = [cell.value for cell in ws[1]]
    tasks = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        if all(v is None or str(v).strip() == "" for v in row):
            continue
        row_dict = {}
        for i, header in enumerate(headers):
            if header and i < len(row):
                row_dict[str(header).strip()] = row[i]
        tasks.append(parse_task_row(row_dict))
    return tasks


def parse_task_row(row):
    task = Task()

    id_str = str(row.get("ID", row.get("Id", row.get("编号", "")))).strip()
    try:
        task.id = int(id_str) if id_str else 0
    except ValueError:
        task.id = 0
    task._has_explicit_id = bool(id_str)

    task.name = str(row.get("任务名称", row.get("Task Name", row.get("name", "")))).strip()

    level_str = str(row.get("层级", row.get("Level", "1"))).strip()
    try:
        task.level = int(level_str)
    except ValueError:
        task.level = 1

    start_str = row.get("开始时间", row.get("Start", row.get("开始", "")))
    task.start = parse_datetime(start_str) if start_str else None

    finish_str = row.get("结束时间", row.get("Finish", row.get("结束", "")))
    task.finish = parse_datetime(finish_str) if finish_str else None

    duration_str = str(row.get("工期(小时)", row.get("工期", row.get("Duration", "0")))).strip()
    try:
        task.duration_hours = float(duration_str) if duration_str else 0
    except ValueError:
        task.duration_hours = 0

    pred_str = str(row.get("前置任务", row.get("Predecessors", ""))).strip()
    pred_types_str = str(row.get("依赖类型", row.get("Dependency Types", ""))).strip()

    if pred_str:
        pred_ids = [x.strip() for x in pred_str.replace(",", ";").split(";")]
        pred_types = [x.strip().upper() for x in pred_types_str.replace(",", ";").split(";")] if pred_types_str else []
        for i, pid in enumerate(pred_ids):
            if pid:
                try:
                    pred_id = int(pid)
                    pred_type = pred_types[i] if i < len(pred_types) and pred_types[i] else "FS"
                    task.predecessors.append((pred_id, pred_type))
                except ValueError:
                    print(f"警告: 无法解析前置任务ID '{pid}'")

    milestone_str = str(row.get("里程碑", row.get("Milestone", "否"))).strip()
    task.milestone = milestone_str in ("是", "true", "True", "1", "yes", "Yes")

    task.notes = str(row.get("备注", row.get("Notes", ""))).strip()

    return task


# ============================================================
# EDPX生成(基于MPXJ逆向分析的EDPX schema)
# ============================================================

# 依赖类型 -> PredecessorLink Type值 (EDPX标准)
# 1=FS(完成-开始) 2=SS(开始-开始) 3=FF(完成-完成) 4=SF(开始-完成)
RELATION_TYPE_EDPX = {
    "FS": 1,
    "SS": 2,
    "FF": 3,
    "SF": 4,
}


def _fmt_dt(dt):
    """转成EDPX日期格式 yyyy-MM-ddTHH:mm:ss"""
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def _fmt_secs(dt1, dt2):
    """计算两个时间之间的秒数"""
    if dt1 and dt2:
        return int((dt2 - dt1).total_seconds())
    return 0


def prepare_tasks(tasks):
    """分配ID、标记摘要任务、计算时间范围"""
    # 分配ID
    max_id = 0
    for task in tasks:
        if getattr(task, '_has_explicit_id', False) and task.id > 0:
            if task.id > max_id:
                max_id = task.id
        else:
            max_id += 1
            task.id = max_id

    # 标记摘要任务：如果后面有更高层级的任务，则是摘要
    for i, task in enumerate(tasks):
        task.is_summary = False
        if i < len(tasks) - 1:
            for j in range(i + 1, len(tasks)):
                if tasks[j].level <= task.level:
                    break
                if tasks[j].level > task.level:
                    task.is_summary = True
                    break

    # 计算摘要任务的时间范围(取子任务的最小开始/最大结束)
    for i, task in enumerate(tasks):
        if task.is_summary:
            starts = []
            finishes = []
            for j in range(i + 1, len(tasks)):
                if tasks[j].level <= task.level:
                    break
                if tasks[j].start:
                    starts.append(tasks[j].start)
                if tasks[j].finish:
                    finishes.append(tasks[j].finish)
            if starts:
                task.start = min(starts)
            if finishes:
                task.finish = max(finishes)

    # 为叶子任务补齐缺失的工期
    for task in tasks:
        if task.is_summary or task.milestone:
            continue
        if task.start and task.finish and task.duration_hours == 0:
            delta = task.finish - task.start
            task.duration_hours = delta.total_seconds() / 3600
        elif task.start and task.duration_hours > 0 and not task.finish:
            task.finish = task.start + timedelta(hours=task.duration_hours)
        elif task.finish and task.duration_hours > 0 and not task.start:
            task.start = task.finish - timedelta(hours=task.duration_hours)

    return tasks


def generate_edpx(tasks, project_name="导入项目", auto_schedule=True):
    """生成EDPX格式文件（ZIP压缩包，严格对照真实文件结构）。

    auto_schedule=True  : 后续任务自动排期（Manual=0，自动模式）。
                          软件会基于前置任务/日历自动重算后续任务的开始/结束时间。
    auto_schedule=False : 手动模式（Manual=1），保留 DateStart/DateFinish 的精确时间。
    """
    import zipfile
    import io
    tasks = prepare_tasks(tasks)

    if tasks:
        proj_start = min(t.start for t in tasks if t.start)
        proj_finish = max(t.finish for t in tasks if t.finish)
    else:
        proj_start = datetime.now()
        proj_finish = proj_start

    now = datetime.now()
    doc_guid = "{%s}" % str(uuid.uuid4()).upper()

    def _tb(align="132", text=None, mask="49664"):
        """生成TextBlock行"""
        r = []
        r.append('                    <TextBlock VAlign="Center" HAlign="Center" '
                 'TextFormatMask="%s">' % mask)
        r.append('                        <Character Family="微软雅黑" '
                 'Color="#000000" IX="0" Size="9"/>')
        r.append('                        <Paragraph Align="%s" SpLine="100" IX="0"/>' % align)
        r.append('                        <WrapMode Value="1"/>')
        r.append('                        <FillColor Color="#ffffff"/>')
        r.append('                        <Color V="#ff000000"/>')
        if text is not None:
            r.append('                        <Text>')
            r.append('                            <pp PX="0" CX="0">')
            r.append('                                <tp CX="0">%s</tp>' % text)
            r.append('                            </pp>')
            r.append('                        </Text>')
        r.append('                    </TextBlock>')
        return r

    def _cell(fid, dtype, ftype, plain=None, varient=None, text=None, align="132"):
        """生成TextCell行"""
        r = []
        attrs = 'FieldID="%s" DataType="%s" FieldType="%s"' % (fid, dtype, ftype)
        if plain is not None:
            attrs += ' PlainText="%s"' % plain
        r.append('            <TextCell %s>' % attrs)
        if varient is None:
            r.append('                <Varient/>')
        else:
            r.append('                <Varient %s/>' % varient)
        r.extend(_tb(align=align, text=text))
        r.append('            </TextCell>')
        return r

    lines = []
    lines.append('<?xml version="1.0" encoding="utf-8"?>')
    lines.append('<Document DocGuid="%s" Version="3.7.10" OS="Win">' % doc_guid)
    lines.append('    <CreatedVersion V="Win_3.3.0"/>')
    lines.append('    <CreationDate V="%s"/>' % _fmt_dt(now))
    lines.append('    <Creator V="ws"/>')
    lines.append('    <LastSaved V="%s"/>' % _fmt_dt(now))
    lines.append('    <Modifier V="60273"/>')
    lines.append('    <DPi V="1"/>')
    lines.append('    <ScreenWidth V="2560"/>')
    lines.append('    <ScreenHeight V="1440"/>')
    lines.append('    <CalendarUID V="0"/>')
    lines.append('    <MinutesPerDay V="1440"/>')
    lines.append('    <MinutesPerWeek V="10080"/>')
    lines.append('    <DaysPerMonth V="30"/>')
    lines.append('    <DateFormat V="yyyy-MM-dd"/>')
    lines.append('    <m_isShowSpecificTime V="TRUE"/>')
    lines.append('    <GanttOption>')
    lines.append('        <ProjectUnit V="5"/>')
    lines.append('        <BaselineCost V="0"/>')
    lines.append('        <StartDate V="%s"/>' % _fmt_dt(proj_start))
    lines.append('        <FinishDate V="%s"/>' % _fmt_dt(proj_finish))
    lines.append('        <Auto V="1"/>')
    lines.append('        <ThemeIndex V="0"/>')
    # 主单位=日(4)，次单位=时(5)；DispFormatId 为刻度标签显示格式ID
    lines.append('        <MajorUnit V="4" SingleStep="1" DispFormatId="20"/>')
    lines.append('        <MinorUnit V="5" SingleStep="1" DispFormatId="41"/>')
    lines.append('        <TimeLineScaleRate V="1"/>')
    lines.append('        <ShowCritical V="FALSE"/>')
    lines.append('        <ShowDelayed V="FALSE"/>')
    lines.append('        <DurationFormatInfos/>')
    lines.append('        <GraphicsViewsSettings>')
    lines.append('            <GanttViewSplitterRate V="0.7"/>')
    lines.append('            <TableViewPos Y="0" X="0"/>')
    lines.append('            <GanttChartViewPos Y="0" X="0"/>')
    lines.append('        </GraphicsViewsSettings>')
    lines.append('    </GanttOption>')

    # 列定义 (key, width, fieldtype, 名称, 是否显示)
    col_defs = [
        (0, 30, "0", None, True),
        (38, 80, "38", "WBS", True),
        (1, 323, "1", "任务名称", True),
        (2, 128, "2", "开始时间", True),
        (3, 128, "3", "完成时间", True),
        (4, 80, "4", "工期", True),
        (37, 80, "37", "前置任务", False),
        (5, 80, "5", "进度", False),
        (41, 80, "41", "状态", False),
        (6, 80, "6", "优先级", False),
        (7, 200, "7", "资源", False),
        (24, 80, "24", None, False),
        (39, 80, "39", None, False),
        (40, 80, "40", None, False),
        (8, 80, "8", None, False),
        (9, 80, "9", None, False),
        (10, 80, "10", None, False),
        (11, 80, "11", None, False),
        (12, 128, "12", None, False),
        (13, 128, "13", None, False),
    ]
    lines.append('    <RowColumn>')
    lines.append('        <ColumnList>')
    for key, width, ftype, name, vis in col_defs:
        hide = "0" if vis else "1"
        lines.append('            <Column Key="%s" Width="%s" IsHide="%s" FilterType="0" '
                     'ColSelectALL="1" FieldType="%s" SortStatus="0">' % (key, width, hide, ftype))
        tattrs = 'FieldID="%s" DataType="1" FieldType="%s"' % (key, ftype)
        if name is not None:
            tattrs += ' PlainText="%s"' % name
        lines.append('                <Text %s>' % tattrs)
        lines.append('                    <Varient/>')
        lines.append('                    <TextBlock VAlign="Center" HAlign="Center" '
                     'TextFormatMask="0">')
        lines.append('                        <Character Family="微软雅黑" IX="0" Size="9"/>')
        lines.append('                        <Paragraph Align="132" SpLine="100" IX="0"/>')
        lines.append('                        <WrapMode Value="1"/>')
        lines.append('                        <FillColor Color="#ffffff"/>')
        lines.append('                        <Color V="#ff000000"/>')
        lines.append('                    </TextBlock>')
        lines.append('                </Text>')
        lines.append('            </Column>')
    lines.append('        </ColumnList>')
    lines.append('    </RowColumn>')

    # WBS编号
    level_counters = {}
    wbs_map = {}
    for t in tasks:
        for l in list(level_counters.keys()):
            if l > t.level:
                del level_counters[l]
        level_counters[t.level] = level_counters.get(t.level, 0) + 1
        parts = [str(level_counters.get(l, 1)) for l in range(1, t.level + 1)]
        wbs_map[t.id] = ".".join(parts)

    # 父任务 / 子任务ID
    parent_map = {}
    child_map = {}
    for i, t in enumerate(tasks):
        parent = -1
        for j in range(i - 1, -1, -1):
            if tasks[j].level < t.level:
                parent = tasks[j].id
                break
        parent_map[t.id] = parent
        if parent > 0:
            child_map.setdefault(parent, []).append(t.id)

    # TaskList
    lines.append('    <TaskList>')
    for rn, t in enumerate(tasks, start=1):
        parent_id = parent_map[t.id]
        wbs = wbs_map.get(t.id, str(rn))
        is_sum = t.is_summary
        is_mile = t.milestone
        dur_secs = _fmt_secs(t.start, t.finish) if (t.start and t.finish
                                                    and not is_sum and not is_mile) else 0
        dur_hours = round(dur_secs / 3600.0, 1) if dur_secs else 0
        child_ids = ";".join(str(x) for x in child_map.get(t.id, []))
        notes = escape(t.notes) if t.notes else ""
        name_esc = escape(t.name)
        ds = _fmt_dt(t.start) if t.start else ""
        df = _fmt_dt(t.finish) if t.finish else ""
        ds_disp = t.start.strftime("%Y-%m-%d %H:%M") if t.start else ""
        df_disp = t.finish.strftime("%Y-%m-%d %H:%M") if t.finish else ""

        pred_text = ""
        if t.predecessors:
            pred_text = ";".join("%d%s" % (pid, ptype.upper())
                                 for pid, ptype in t.predecessors)

        # 自动设置任务进度到当前时间
        # 依据当前系统时间，计算每个任务的进度状态
        now = datetime.now()
        percent = 0.0
        progress_status = 0
        actual_start = ""
        actual_finish = ""
        if t.start and t.finish:
            total = (t.finish - t.start).total_seconds()
            if total > 0:
                if now >= t.finish:
                    # 已完成
                    percent = 1.0
                    progress_status = 3
                elif now <= t.start:
                    # 未开始
                    percent = 0.0
                    progress_status = 0
                else:
                    # 进行中：按时间比例计算
                    percent = round((now - t.start).total_seconds() / total, 6)
                    progress_status = 4
        percent_str = "%.6f" % percent if percent not in (0.0, 1.0) else str(int(percent))
        # 与v5参考一致：自动设置进度时,ActualStart/ActualFinish使用Unix时间戳0
        if percent > 0:
            actual_start = "0"
            actual_finish = "0"

        # 排程模式：自动(Manual=0)或手动(Manual=1)
        #   自动模式：不写 DateManualStart/DateManualFinish，软件按前置任务重算后续任务
        #   手动模式：写 DateManualStart/DateManualFinish=DateStart/DateFinish，保留精确时间
        manual_flag = "0" if auto_schedule else "1"
        manual_start = "" if auto_schedule else ds
        manual_finish = "" if auto_schedule else df

        attrs = [
            'Name="%s"' % name_esc,
            'SplitOffsetDuration=""',
            'RowHeight="%s"' % (59 if is_sum else 54),
            'FontColor="#000000"',
            'ManualDurationSecs="%s"' % dur_secs,
            'Cost="0"',
            'ActualDuration="%s"' % dur_secs,
            'DateBaseFinish=""',
            'DateBaseStart=""',
            'Notes="%s"' % notes,
            'ShowByChild="0"',
            'ID="%s"' % t.id,
            'Priority="8"',
            'BaseLinePointxBegin="0"',
            'FirstWidth="%s"' % (10 if t.level == 1 else 80),
            'DurationUnitCustomSuffix=""',
            'RemainingCost="0"',
            'BaselineCost=""',
            'ProgressStatus="%s"' % progress_status,
            'BaseLinePointxEnd="0"',
            'm_IbartextIndex=""',
            'Resources=""',
            'DateManualFinish="%s"' % manual_finish,
            'BackGroundColor="#ffffff"',
            'DateLateStart=""',
            'ActualMilestone="0"',
            'Wbs="%s"' % wbs,
            'DateManualStart="%s"' % manual_start,
            'DateStart="%s"' % ds,
            'Milestone="%s"' % (1 if is_mile else 0),
            'LastSaveDate=""',
            'Work="%s"' % dur_hours,
            'LateSlack=""',
            'DateFinish="%s"' % df,
            'm_Istyletext=""',
            'IsToggler="0"',
            'ActualStart="%s"' % actual_start,
            'm_Bestyletext=""',
            'm_bebartextIndex=""',
            'CriticalPath="0"',
            'ActualFinish="%s"' % actual_finish,
            'DateLateFinish=""',
            'ParentID="%s"' % parent_id,
            'Percent="%s"' % percent_str,
            'HideByColumns=""',
            'Childs="%s"' % child_ids,
            'SplitPointList=""',
            'EarlySlack=""',
            'Manual="%s"' % manual_flag,
            'Level="%s"' % t.level,
            'HideID="0"',
            'BaseLineNumber="-1"',
            'DurationSecs="%s"' % dur_secs,
            'RowID="%s"' % rn,
            'CountWidth="10"',
            'DurationUnits="5"',
        ]
        lines.append('        <Task %s>' % " ".join(attrs))
        lines.append('            <Format StrikeOut="0" Bold="0" Family="微软雅黑" '
                     'Underline="0" PointSize="9" Italic="0"/>')
        lines.append('            <ResourceList/>')
        for pred_id, pred_type in t.predecessors:
            if pred_id in parent_map:
                rel_type = RELATION_TYPE_EDPX.get(pred_type.upper(), 1)
                lines.append('            <PredecessorLink>')
                lines.append('                <PredecessorUID>%s</PredecessorUID>' % pred_id)
                lines.append('                <Type>%s</Type>' % rel_type)
                lines.append('                <LinkLag>0</LinkLag>')
                lines.append('                <LagFormat>7</LagFormat>')
                lines.append('                <CrossProject>0</CrossProject>')
                lines.append('            </PredecessorLink>')
        lines.append('            <Texts>')
        lines.append('                <TextCell FieldID="0" DataType="1" FieldType="0" '
                     'PlainText="%d">' % rn)
        lines.append('                    <Varient/>')
        lines.extend(_tb())
        lines.append('                </TextCell>')
        lines.extend(_cell(38, "9", "38", plain=wbs))
        lines.extend(_cell(1, "0", "1", plain=name_esc, text=name_esc, align="1"))
        if t.start:
            lines.extend(_cell(2, "2", "2", plain=ds_disp, varient='Date="%s"' % ds))
        else:
            lines.extend(_cell(2, "2", "2"))
        if t.finish:
            lines.extend(_cell(3, "2", "3", plain=df_disp, varient='Date="%s"' % df))
        else:
            lines.extend(_cell(3, "2", "3"))
        if dur_secs and not is_sum and not is_mile:
            # 工期以小时为单位显示；整数小时去掉小数点
            dur_disp = int(dur_hours) if float(dur_hours).is_integer() else dur_hours
            lines.extend(_cell(4, "3", "4", plain="%s小时" % dur_disp,
                               varient='Value="%s" Suffix="小时"' % dur_disp))
        else:
            lines.extend(_cell(4, "3", "4"))
        if pred_text:
            lines.extend(_cell(37, "9", "37", plain=escape(pred_text)))
        else:
            lines.extend(_cell(37, "9", "37"))
        lines.extend(_cell(5, "3", "5", plain="%s%%" % int(percent * 100)))
        lines.extend(_cell(41, "10", "41", plain="未开始"))
        lines.extend(_cell(6, "10", "6", plain="无"))
        lines.extend(_cell(7, "9", "7"))
        for hfid in (24, 39, 40, 8, 9, 10, 11, 12, 13):
            lines.extend(_cell(hfid, "9", str(hfid)))
        lines.append('            </Texts>')
        lines.append('            <BarChart>')
        lines.append('                <TextFormat TextFormatMask="0">')
        lines.append('                    <Character Family="微软雅黑" IX="0" Size="9"/>')
        lines.append('                    <Paragraph Align="4" SpLine="100" IX="0"/>')
        lines.append('                    <WrapMode Value="1"/>')
        lines.append('                    <FillColor Color="#000000"/>')
        lines.append('                    <Color V="#ff000000"/>')
        lines.append('                </TextFormat>')
        lines.append('                <Texts>')
        lines.append('                    <BarChartText FieldID="1" Value="%s" Type="2" Pos="4"/>'
                     % name_esc)
        lines.append('                </Texts>')
        lines.append('            </BarChart>')
        lines.append('        </Task>')
    lines.append('    </TaskList>')

    # ResourceInfo
    lines.append('    <ResourceInfo/>')

    # Calendars - 24小时全天候
    lines.append('    <Calendars>')
    lines.append('        <Calendar>')
    lines.append('            <UID>0</UID>')
    lines.append('            <Name>标准日历</Name>')
    lines.append('            <NameU>Standard</NameU>')
    lines.append('            <IsBaseCalendar>1</IsBaseCalendar>')
    lines.append('            <BaseCalendarUID>0</BaseCalendarUID>')
    lines.append('            <IsTemplateCalendar>0</IsTemplateCalendar>')
    lines.append('            <MachineInfo/>')
    lines.append('            <WeekDays>')
    for day in range(1, 8):
        lines.append('                <WeekDay>')
        lines.append('                    <DayType>%s</DayType>' % day)
        lines.append('                    <DayWorking>1</DayWorking>')
        lines.append('                    <WorkingTimes>')
        lines.append('                        <WorkingTime>')
        lines.append('                            <FromTime>00:00:00</FromTime>')
        lines.append('                            <ToTime>23:59:59</ToTime>')
        lines.append('                        </WorkingTime>')
        lines.append('                    </WorkingTimes>')
        lines.append('                </WeekDay>')
    lines.append('            </WeekDays>')
    lines.append('        </Calendar>')
    lines.append('    </Calendars>')

    lines.append('    <WaterMark Type="None"/>')
    lines.append('    <lineStyleInformation line_index="0" line_roundsize="0" '
                 'line_Color="#000000" line_width="1"/>')
    lines.append('</Document>')

    document_xml = "\n".join(lines) + "\n"

    # 打包为ZIP
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('document.xml', document_xml)
        zf.writestr('page/page.xml', '<?xml version="1.0" encoding="utf-8"?>\n<Page/>')
        zf.writestr('rels/page_rels.xml',
                    '<?xml version="1.0" encoding="utf-8"?>\n<Relationships/>')
    return buf.getvalue()


# ============================================================
# 主程序
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="万兴项目管理(EdrawProj)任务导入工具 v3 - 生成EDPX原生格式",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s tasks.csv -o project.edpx          # 自动排程(默认)
  %(prog)s tasks.csv -o project.edpx --manual-schedule  # 手动保留精确时间
  %(prog)s tasks.xlsx -o project.edpx
  %(prog)s --template template.csv    # 生成CSV模板

CSV格式说明:
  必需列: 任务名称, 开始时间, 结束时间
  可选列: 层级(默认1), 工期(小时), 前置任务, 依赖类型, 里程碑, 备注
  
  时间格式: 2024-01-15 07:30  (精确到分钟)
  前置任务: 多个用分号分隔, 如 2;3
  依赖类型: FS(完成-开始), SS(开始-开始), FF(完成-完成), SF(开始-完成)

排程模式:
  默认自动排程(Manual=0)：后续任务根据前置任务自动排期，适合任务依赖接续场景。
  加 --manual-schedule 切换为手动排程(Manual=1)：保留每个任务精确的开始/结束时间(含8点前)。

甘特图时间刻度:
  主单位=日、次单位=时（MajorUnit=4, MinorUnit=5）。

CSV编码:
  生成的CSV模板使用UTF-8 with BOM编码，在Excel/WPS中直接打开不会乱码。
  如果你自己创建CSV，请用记事本另存为"UTF-8 with BOM"编码。

导入说明:
  1. 打开万兴项目管理(亿图项目管理)软件
  2. 选择 文件 -> 打开
  3. 选择生成的 .edpx 文件
  4. 自动排程模式下，后续任务会按前置任务自动排期；手动模式保留精确时间
        """
    )
    parser.add_argument("input", nargs="?", help="输入文件路径 (CSV或Excel)")
    parser.add_argument("-o", "--output", help="输出文件路径 (EDPX格式)")
    parser.add_argument("-n", "--name", default="导入项目", help="项目名称")
    parser.add_argument(
        "--manual-schedule", action="store_true",
        help="手动排程模式(Manual=1)：保留每个任务精确的开始/结束时间，包括8点前的时间。"
             "默认关闭——使用自动排程(Manual=0)，后续任务会根据前置任务自动排期。")
    parser.add_argument("--quiet", action="store_true", help="安静模式")
    parser.add_argument("--template", help="生成CSV模板文件到指定路径")

    args = parser.parse_args()

    # 模板生成模式
    if args.template:
        with open(args.template, 'w', encoding='utf-8-sig', newline='') as f:
            f.write(CSV_TEMPLATE)
        print(f"CSV模板已生成: {args.template}")
        print("  - UTF-8 with BOM 编码，Excel/WPS 直接打开不乱码")
        print("  - 按模板格式填写任务数据后运行:")
        print(f"    python {sys.argv[0]} {args.template} -o output.xml")
        return

    if not args.input:
        parser.error("需要指定输入文件，或使用 --template 生成模板")
    if not args.output:
        parser.error("需要指定输出文件: -o output.xml")

    # 读取输入
    ext = os.path.splitext(args.input)[1].lower()
    if ext in ('.xlsx', '.xls'):
        tasks = read_excel(args.input)
    else:
        tasks = read_csv(args.input)

    if not tasks:
        print("错误: 未读取到任何任务数据")
        sys.exit(1)

    # 生成EDPX
    print(f"正在生成EDPX文件...")
    print(f"  输入: {len(tasks)} 个任务")
    print(f"  项目名称: {args.name}")
    mode_label = "自动排程(后续任务自动排期)" if not args.manual_schedule else "手动排程(保留精确时间)"
    print(f"  排程模式: {mode_label}")

    try:
        edpx_content = generate_edpx(tasks, args.name, auto_schedule=not args.manual_schedule)
    except Exception as e:
        print(f"错误: EDPX生成失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    # 写入输出文件(EDPX是ZIP压缩包,以二进制写入)
    with open(args.output, 'wb') as f:
        f.write(edpx_content)

    # 打印摘要
    if not args.quiet:
        print(f"\n{'='*60}")
        print(f"任务摘要: 共 {len(tasks)} 个任务")
        print(f"{'='*60}")
        print(f"{'ID':>4} {'层级':>4} {'名称':<20} {'开始时间':<20} {'结束时间':<20}")
        print(f"{'-'*60}")
        for t in tasks:
            start_str = t.start.strftime("%Y-%m-%d %H:%M") if t.start else "  -  "
            finish_str = t.finish.strftime("%Y-%m-%d %H:%M") if t.finish else "  -  "
            summary_mark = " [摘要]" if getattr(t, 'is_summary', False) else ""
            mile_mark = " [里程碑]" if t.milestone else ""
            print(f"{t.id:>4} {t.level:>4} {t.name:<20}{summary_mark}{mile_mark} {start_str:<20} {finish_str:<20}")
        print(f"{'='*60}")

    print(f"\n输出文件: {args.output}")
    print(f"\n使用方法: 在万兴项目管理软件中 -> 文件 -> 打开")
    print(f"         选择 {args.output} 即可打开项目")
    print(f"\n注意事项:")
    print(f"  1. 使用24小时全天候日历，任务不会因工作日时间限制被调整")
    if args.manual_schedule:
        print(f"  2. 手动排程(Manual=1)，保留每个任务精确的开始/结束时间")
    else:
        print(f"  2. 自动排程(Manual=0)，后续任务按前置任务自动排期")
        print(f"     (如需保留精确时间，可加 --manual-schedule 参数)")
    print(f"  3. 甘特图主单位=日、次单位=时")
    print(f"  4. 时间精确到分钟级别")
    print(f"  5. 如需修改日历，在软件中设置: 项目 -> 更改工作时间")


if __name__ == "__main__":
    main()