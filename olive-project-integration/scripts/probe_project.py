"""探明一个 Python 工程能被平台看到哪些接口。

用法：
  python probe_project.py --path <工程目录> --module <模块> [--python <解释器>] [--json 输出]

探测直接调用后端 app/services/projects.py，与平台「工程管理」页看到的结果一致。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import _common

KIND_TITLES = {
    "constructor": "可构造（填构造槽）",
    "function": "模块级函数（object 留空）",
    "instance_method": "实例方法（必须指定 object）",
    "static_method": "静态方法（object 可留空）",
    "class_method": "类方法（object 可留空）",
}


def main() -> int:
    _common.force_utf8()
    parser = argparse.ArgumentParser(description="探测外接工程的公开接口")
    parser.add_argument("--path", required=True, help="工程源码目录")
    parser.add_argument("--module", required=True, help="可导入的模块名，如 records")
    parser.add_argument("--python", default="", help="工程解释器；缺省用项目 venv")
    parser.add_argument("--json", dest="json_out", default="", help="把原始探测结果写到文件")
    parser.add_argument("--root", default="", help="NewDiag 仓库根；缺省自动定位")
    args = parser.parse_args()

    root, platform, mode = _common.resolve(args.root)

    project_path = Path(args.path).expanduser().resolve()
    if not project_path.is_dir():
        print(f"工程目录不存在：{project_path}", file=sys.stderr)
        return 2
    python = args.python or _common.platform_python(root)
    if not Path(python).is_file():
        print(f"解释器不存在：{python}", file=sys.stderr)
        return 2

    if platform is not None:
        from app.services.projects import probe as run_probe

        result = run_probe(
            {
                "name": project_path.name,
                "path": str(project_path),
                "module": args.module,
                "python": python,
                "factory": "",
                "catalog": True,
            }
        )
    else:
        project = SimpleNamespace(
            name=project_path.name,
            path=str(project_path),
            module=args.module,
            python=python,
            factory="",
        )
        result = _common.run_builtin_probe(project, args.module, python)
    if args.json_out:
        _common.json_dump(result, args.json_out)

    print(f"模式    {_mode_label(mode)}")
    print(f"工程    {project_path}")
    print(f"模块    {args.module}")
    print(f"解释器  {python}")

    error = result.get("error")
    if error:
        print("\n探测失败：模块无法导入。")
        print(_indent(_common.error_brief(error)))
        print("\n先确认三件事一致：登记目录是包所在目录、module 是可导入名、解释器里装好了依赖。")
        print("更多原因见 references/troubleshooting.md 的 A 节。")
        return 1

    functions = result.get("functions") or []
    if not functions:
        print("\n没有发现任何公开接口。")
        print("模块需要在目标解释器里可导入，并暴露顶层函数或类；")
        print("若要精确控制目录，在模块里写 __all__ = [...]。")
        return 1

    groups: dict[str, list[dict]] = {}
    for entry in functions:
        groups.setdefault(entry.get("kind") or "function", []).append(entry)

    blocked = [item for item in functions if item.get("interfaceError")]
    for kind, title in KIND_TITLES.items():
        items = groups.get(kind) or []
        if not items:
            continue
        print(f"\n{title}（{len(items)}）")
        for entry in items:
            params = _common.flatten_params(entry.get("params") or [])
            mark = "  [不可接入]" if entry.get("interfaceError") else ""
            print(f"  {entry['name']}{params}{mark}")
            if entry.get("doc"):
                first = str(entry["doc"]).strip().splitlines()[0]
                print(f"      {first}")
            if entry.get("interfaceError"):
                print(f"      原因：{entry['interfaceError']}")
            if entry.get("variadicKeywords"):
                print("      接受 **kwargs：可以只绑定显式声明的参数")

    hints = _hints(functions, blocked)
    if hints:
        print("\n接入提示")
        for hint in hints:
            print(f"  - {hint}")

    print("\n登记建议（工程名登记后不可改，取稳定的业务名）")
    print(f'  name:   "{project_path.name}"')
    print(f'  path:   "{project_path}"')
    print(f'  module: "{args.module}"')
    print(f'  python: "{python}"')
    if mode != "platform":
        print("\n本机没有 NewDiag 仓库，以上由内置探针给出；接入平台后再用平台模式复核一次。")
    return 0


def _mode_label(mode: str) -> str:
    return "平台探针（规则来自本机 NewDiag）" if mode == "platform" else "独立探针（内置规则，未与平台比对）"


def _hints(functions: list[dict], blocked: list[dict]) -> list[str]:
    hints: list[str] = []
    constructors = [item for item in functions if item.get("kind") == "constructor"]
    methods = [item for item in functions if item.get("kind") == "instance_method"]
    if constructors:
        required = [
            f"{item['name']}({', '.join(p['name'] for p in item.get('params') or [] if p.get('required'))})"
            for item in constructors
            if any(p.get("required") for p in item.get("params") or [])
        ]
        if required:
            hints.append("构造槽需要给必填参数：{}".format("、".join(required)))
        else:
            hints.append("构造器无必填参数，构造槽 arguments 可留空对象")
    if methods:
        hints.append("实例方法的 object 必须指向构造槽里建好的对象名；object 留空会被判为接口错误")
    if blocked:
        hints.append("标 [不可接入] 的接口是 async 或含位置专用参数，需要补一个同步、可关键字调用的公开入口")
    dynamic = [
        item["name"]
        for item in functions
        if not item.get("doc") and (item.get("params") or item.get("kind") in ("constructor", "function"))
    ]
    if dynamic:
        hints.append("缺少 docstring 的接口：{}；槽位下面显示的就是它，写给编排者看".format("、".join(dynamic[:4])))
    if not constructors and not methods:
        hints.append("整个工程只有模块级函数：开始节点可以不填构造槽，槽位 object 留空即可")
    return hints


def _indent(text: str) -> str:
    return "\n".join("  " + line for line in text.splitlines())


if __name__ == "__main__":
    raise SystemExit(main())
