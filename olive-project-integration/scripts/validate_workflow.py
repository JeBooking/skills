"""按发布口径校验一份 workflow.json：结构、连线、槽位、接口、必填参数。

用法：
  python validate_workflow.py workflow.json --projects projects.json [--no-probe] [--json out]

校验直接调用后端 compiler.validate_doc 与 slots.probe_slots，和「发布」时拦的是同一套规则。
"""

from __future__ import annotations

import argparse
import difflib
import json
import sys
from pathlib import Path

import _common
import standalone


def main() -> int:
    _common.force_utf8()
    parser = argparse.ArgumentParser(description="发布级校验 workflow.json")
    parser.add_argument("workflow", help="流程文件（format=1 编辑投影，或 format=2 持久格式）")
    parser.add_argument("--projects", default="", help="工程登记表 projects.json")
    parser.add_argument("--root", default="", help="NewDiag 仓库根；缺省自动定位")
    parser.add_argument("--no-probe", action="store_true", help="跳过接口探测，只校验结构")
    parser.add_argument("--json", dest="json_out", default="", help="把结果写到文件")
    parser.add_argument("--quiet", action="store_true", help="只打印问题汇总")
    args = parser.parse_args()

    root, platform, mode = _common.resolve(args.root)

    doc = _common.load_doc(args.workflow, platform["expand"] if platform else None)
    _common.recompact(doc, platform)

    errors: list[str] = []
    warnings: list[str] = []
    functions: dict = {}

    projects: dict = {}
    if args.projects:
        projects = _common.load_projects(args.projects, root)
        errors.extend(_check_projects(doc, projects, platform))
    referenced = _common.referenced_projects(doc, platform)

    if not args.no_probe:
        if not projects:
            for name in sorted(referenced):
                errors.append(f"工程「{name}」没有登记信息：本次未提供 --projects，无法探测接口")
        else:
            functions = _common.probe(doc, projects, platform, _common.platform_python(root))
            for key, message in _common.probe_failures(functions):
                if "工程未登记" in message:
                    continue  # 已经按工程报过一次，不按接口重复刷屏
                errors.append(f"接口探测失败 {key}：{_tail(message)}")
            usable = {key: value for key, value in functions.items() if isinstance(value, list)}
            functions = usable

    if platform is not None:
        issues = platform["validate_doc"](doc, functions if functions else None)
    else:
        issues = standalone.validate(doc, functions if functions else None)
    warnings.extend(_view_consistency(doc))
    for issue in issues:
        text = str(issue.get("message") or "")
        node_id = issue.get("nodeId")
        edge_id = issue.get("edgeId")
        where = f"[{node_id}]" if node_id else (f"[边 {edge_id}]" if edge_id else "")
        line = f"{where} {text}".strip()
        if issue.get("level") == "error":
            errors.append(line)
        else:
            warnings.append(line)

    calls = _call_list(doc)
    arg_details = _argument_details(doc, functions) if functions else []
    ok = not errors

    if args.json_out:
        _common.json_dump(
            {
                "ok": ok,
                "workflow": str(Path(args.workflow).expanduser().resolve()),
                "referencedProjects": sorted(referenced),
                "errors": errors,
                "warnings": warnings,
                "argumentDetails": arg_details,
                "calls": calls,
                "probe": {
                    key: (value if isinstance(value, dict) else [
                        {"name": p.get("name"), "type": p.get("type"), "required": p.get("required")}
                        for p in value
                        if not str(p.get("name", "")).startswith("$")
                    ])
                    for key, value in functions.items()
                },
            },
            args.json_out,
        )

    if not args.quiet:
        node_count, edge_count = _common.doc_shape(doc)
        print(f"模式    {'发布级（平台 compiler）' if mode == 'platform' else '独立预检（内置规则）'}")
        print(f"流程    {Path(args.workflow).name}（{node_count} 节点，{edge_count} 条连线）")
        print(f"引用工程 {', '.join(sorted(referenced)) or '无'}")
        if calls:
            print("\n槽位调用")
            for line in calls:
                print(f"  {line}")
        if not args.no_probe and functions:
            print("\n接口探测")
            for key in sorted(functions):
                params = [p for p in functions[key] if not str(p.get("name", "")).startswith("$")]
                print(f"  ok  {key}{_common.flatten_params(params)}")

    if warnings:
        print(f"\n警告（{len(warnings)}）")
        for line in warnings:
            print(f"  - {line}")
    if errors:
        print(f"\n错误（{len(errors)}）")
        for line in errors:
            print(f"  - {line}")
        if arg_details:
            print("\n参数明细")
            for line in arg_details:
                print(f"  - {line}")
        print("\n未通过：以上错误在平台里同样会拦住发布。" if mode == "platform"
              else "\n未通过：以上按内置规则判定，平台侧应会拦下同类问题。")
        print("接口/参数类问题见 references/troubleshooting.md；结构类问题见 references/workflow-format.md。")
        return 1

    if args.quiet:
        print("通过（无 error）" if ok else f"未通过（{len(errors)} 个 error，{len(warnings)} 个 warning）")
    else:
        print("\n通过：结构与槽位校验无 error，可以进入冒烟执行。")
        if args.projects:
            if mode == "platform":
                print(
                    "下一步：\n  {python} {script} {workflow} --projects {projects}".format(
                        python=_common.platform_python(root),
                        script=Path(__file__).resolve().with_name("smoke_run.py"),
                        workflow=Path(args.workflow).expanduser().resolve(),
                        projects=Path(args.projects).expanduser().resolve(),
                    )
                )
            else:
                print("下一步：本机装上 NewDiag 仓库后再跑冒烟；独立模式下只能做到结构预检。")
    if mode != "platform":
        print("\n注：未使用平台实现（本机没有可导入的 NewDiag 仓库），以上由内置规则给出。")
        print("拿到平台源码后重跑本命令，结论以平台为准。")
    return 0


def _check_projects(doc: dict, projects: dict, platform: dict) -> list[str]:
    """工程登记表本身的离线检查：未登记、路径不存在、解释器不存在。"""
    errors: list[str] = []
    referenced = _common.referenced_projects(doc, platform)
    for name in sorted(referenced - set(projects)):
        errors.append(f"工程「{name}」未在登记表里：先在平台工程页登记，或补进 projects.json")
    for name in sorted(referenced & set(projects)):
        project = projects[name]
        if not Path(project.path).is_dir():
            errors.append(f"工程「{name}」的目录不存在：{project.path}")
        if project.python and not Path(project.python).is_file():
            errors.append(f"工程「{name}」的解释器不存在：{project.python}")
    for name, project in projects.items():
        if name not in referenced:
            print(f"提示：登记表里的工程「{name}」没有被这份流程引用", file=sys.stderr)
    return errors


def _argument_details(doc: dict, functions: dict) -> list[str]:
    """平台文案只说「包含未声明的参数」，这里补上具体名字与最接近的正确写法。"""
    messages: list[str] = []
    for node in doc.get("nodes") or []:
        slots = node.get("slots") or {}
        if not isinstance(slots, dict):
            continue
        items = list(slots.get("constructs") or [])
        items += [slots.get(name) for name in ("fetch", "process", "present", "decide")]
        for call in items:
            if not isinstance(call, dict):
                continue
            key = "{}:{}:{}".format(
                call.get("project", ""),
                call.get("module", ""),
                call.get("interface") or call.get("function") or "",
            )
            params = functions.get(key)
            if not isinstance(params, list):
                continue
            known = {p["name"] for p in params if not str(p.get("name", "")).startswith("$")}
            supplied = set(call.get("arguments") or {})
            extra = supplied - known
            if extra:
                hints = []
                for name in sorted(extra):
                    match = difflib.get_close_matches(name, sorted(known), n=1)
                    hints.append(f"{name}（是否想写 {match[0]}？）" if match else name)
                messages.append(
                    f"节点 {node.get('id')} 的 {key} 多出参数：{'、'.join(hints)}；"
                    f"接口只有 {', '.join(sorted(known)) or '（无参数）'}"
                )
    return messages


def _view_consistency(doc: dict) -> list[str]:
    """同一接口配了不同展示类型，必有一处错：一个方法只产出一种形状。"""
    messages: list[str] = []
    seen: dict[str, tuple[str, str]] = {}
    for node in doc.get("nodes") or []:
        present = (node.get("slots") or {}).get("present")
        if not isinstance(present, dict) or not present.get("view"):
            continue
        key = "{}:{}:{}".format(
            present.get("project", ""),
            present.get("module", ""),
            present.get("interface") or present.get("function") or "",
        )
        view = str(present["view"])
        previous = seen.get(key)
        if previous and previous[0] != view:
            messages.append(
                f"接口 {key} 在节点 {previous[1]} 用 {previous[0]}，在节点 {node.get('id')} 用 {view}："
                "同一个方法只产出一种形状，必有一处配错"
            )
        elif not previous:
            seen[key] = (view, str(node.get("id")))
    return messages


def _call_list(doc: dict) -> list[str]:
    """把每个节点的槽位渲染成一行，便于人工核对。"""
    labels = {"fetch": "拉取", "process": "处理", "present": "展示", "decide": "判定"}
    lines: list[str] = []
    for node in doc.get("nodes") or []:
        slots = node.get("slots") or {}
        if not isinstance(slots, dict) or not slots:
            continue
        head = f"{node.get('id')} {node.get('title') or ''}".strip()
        for call in slots.get("constructs") or []:
            if isinstance(call, dict):
                lines.append(f"{head} | 构造 {_call_text(call, '←')}")
        for name in ("fetch", "process", "present", "decide"):
            call = slots.get(name)
            if not isinstance(call, dict):
                continue
            if name == "decide" and call.get("human") and not (call.get("interface") or call.get("function")):
                text = "人工判断"
            else:
                text = _call_text(call, "→")
            if name == "present" and call.get("view"):
                text += f"  [{call['view']}]"
            if name == "fetch" and call.get("useCache") is False:
                text += "  [不使用缓存]"
            lines.append(f"{head} | {labels[name]} {text}")
    return lines


def _call_text(call: dict, arrow: str) -> str:
    target = str(call.get("object") or "").strip()
    interface = call.get("interface") or call.get("function") or "?"
    params = ", ".join(
        f"{key}={_argument_text(value)}" for key, value in (call.get("arguments") or {}).items()
    )
    callee = f"{interface}({params})"
    return f"{target} {arrow} {callee}" if target else callee


def _argument_text(value) -> str:
    if not isinstance(value, dict):
        return repr(value)
    if "lit" in value:
        return json.dumps(value["lit"], ensure_ascii=False)
    if "source" in value:
        source = value["source"]
        if source == "previous":
            return "上一槽结果"
        if source == "presentation":
            return "展示结果"
        name = value.get("name") or "?"
        label = "启动输入" if source == "input" else "现场参数"
        text = f"{label}:{name}"
        return text + (f".{value['path']}" if value.get("path") else "")
    if "items" in value:
        return "[" + ", ".join(_argument_text(item) for item in value.get("items") or []) + "]"
    if "fields" in value:
        inner = ", ".join(f"{k}: {_argument_text(v)}" for k, v in (value.get("fields") or {}).items())
        return "{" + inner + "}"
    return "?"


def _tail(text: str, limit: int = 240) -> str:
    flat = " / ".join(line.strip() for line in _common.clean_error(text).splitlines() if line.strip())
    return flat if len(flat) <= limit else "…" + flat[-limit:]


if __name__ == "__main__":
    raise SystemExit(main())
