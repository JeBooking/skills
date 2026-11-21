"""静态勘察一个工程：规模、框架、入口、类与继承、绘制钩子、IO 点、形态判断。

只看语法树，不 import 任何业务代码——有副作用的工程也能安全分析。

用法：
  python inspect_project.py --path <工程目录> [--json out.json]
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    "node_modules",
    "build",
    "dist",
    "site-packages",
    ".idea",
    ".pytest_cache",
}
FRAMEWORKS = {
    "PySide6": "Qt（PySide6）",
    "PyQt6": "Qt（PyQt6）",
    "PyQt5": "Qt（PyQt5）",
    "tkinter": "Tk",
    "wx": "wxWidgets",
    "kivy": "Kivy",
}
SERVICE_HINTS = {"fastapi": "FastAPI", "flask": "Flask", "django": "Django"}
DRAW_HINTS = ("paint", "draw", "render", "plot")


def main() -> int:
    _force_utf8()
    parser = argparse.ArgumentParser(description="静态勘察工程形态")
    parser.add_argument("--path", required=True, help="工程目录")
    parser.add_argument("--json", dest="json_out", default="", help="把结果写到文件")
    args = parser.parse_args()

    root = Path(args.path).expanduser().resolve()
    if not root.is_dir():
        print(f"目录不存在：{root}", file=sys.stderr)
        return 2

    report = analyze(root)
    if args.json_out:
        Path(args.json_out).expanduser().write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    render(report)
    return 0


def analyze(root: Path) -> dict:
    files = []
    for path in sorted(root.rglob("*.py")):
        if any(part in SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        files.append(path)

    modules = []
    imports: set[str] = set()
    classes: list[dict] = []
    functions: list[dict] = []
    io_calls: list[dict] = []
    async_defs: list[str] = []
    has_main_guard = False
    errors: list[str] = []

    for path in files:
        relative = str(path.relative_to(root)).replace("\\", "/")
        try:
            source = path.read_text(encoding="utf-8-sig")
            tree = ast.parse(source, filename=relative)
        except (OSError, SyntaxError, UnicodeDecodeError) as exc:
            errors.append(f"{relative}: {exc}")
            continue
        modules.append({"file": relative, "lines": len(source.splitlines())})
        imports |= _imports(tree)
        if _has_main_guard(tree):
            has_main_guard = True
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if isinstance(node, ast.AsyncFunctionDef):
                    async_defs.append(f"{relative}:{node.name}")
                if isinstance(node, ast.FunctionDef) and _is_module_level(tree, node):
                    functions.append({"name": node.name, "file": relative})
            if isinstance(node, ast.ClassDef):
                classes.append(_class_entry(node, relative))
            if isinstance(node, ast.Call):
                name = _call_name(node)
                if name in ("open", "read_text", "read_csv", "connect", "urlopen"):
                    io_calls.append({"call": name, "file": relative, "line": node.lineno})

    total_lines = sum(item["lines"] for item in modules)
    framework = _framework(imports)
    service = _service(imports)
    hooks = [
        {"class": item["name"], "method": method, "file": item["file"]}
        for item in classes
        for method in item["methods"]
        if any(hint in method.lower() for hint in DRAW_HINTS)
    ]
    base_classes = _base_classes(classes)
    entry = next(
        (item["file"] for item in modules if item["file"].endswith("__main__.py")),
        _entry_file(root, modules),
    )
    gui = framework.startswith("Qt") or framework == "Tk" or framework in ("wxWidgets", "Kivy")
    shaped = {
        "gui": gui,
        "framework": framework,
        "service": service,
        "entry": entry,
        "hasMainGuard": has_main_guard,
        "selfDrawn": bool(hooks),
        "baseLayered": bool(base_classes) and bool(hooks),
        "singleFile": len(modules) == 1,
        "totalLines": total_lines,
        "ioInClass": _io_in_class(classes, io_calls),
        "async": async_defs,
    }
    return {
        "root": str(root),
        "modules": modules,
        "totalLines": total_lines,
        "imports": sorted(imports),
        "classes": classes,
        "functions": functions,
        "hooks": hooks,
        "baseClasses": base_classes,
        "ioCalls": io_calls,
        "asyncDefs": async_defs,
        "errors": errors,
        "shape": shaped,
    }


def _force_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8")
            except (ValueError, OSError):
                pass


def _imports(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


def _has_main_guard(tree: ast.AST) -> bool:
    for node in tree.body if isinstance(tree, ast.Module) else []:
        if not isinstance(node, ast.If) or not isinstance(node.test, ast.Compare):
            continue
        left = node.test.left
        if isinstance(left, ast.Name) and left.id == "__name__":
            return True
    return False


def _is_module_level(tree: ast.AST, target: ast.AST) -> bool:
    return any(node is target for node in getattr(tree, "body", []))


def _class_entry(node: ast.ClassDef, file: str) -> dict:
    methods = [
        item.name
        for item in node.body
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    end = getattr(node, "end_lineno", None) or node.lineno
    return {
        "name": node.name,
        "bases": [_expr_name(base) for base in node.bases],
        "file": file,
        "line": node.lineno,
        "lines": end - node.lineno + 1,
        "methods": methods,
    }


def _expr_name(node) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return _expr_name(node.value) + "." + node.attr
    return ""


def _call_name(node: ast.Call) -> str:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return ""


def _framework(imports: set[str]) -> str:
    for key, label in FRAMEWORKS.items():
        if key in imports:
            return label
    return ""


def _service(imports: set[str]) -> str:
    for key, label in SERVICE_HINTS.items():
        if key in imports:
            return label
    return ""


def _base_classes(classes: list[dict]) -> list[dict]:
    """被继承、并且自己画东西的类：这类基类会把基础图像带进子类。"""
    counts: dict[str, int] = {}
    for item in classes:
        for base in item["bases"]:
            counts[base.split(".")[-1]] = counts.get(base.split(".")[-1], 0) + 1
    result = []
    for item in classes:
        inherited = counts.get(item["name"], 0) >= 1
        draws = any(hint in method.lower() for method in item["methods"] for hint in DRAW_HINTS)
        if inherited and draws:
            result.append({"name": item["name"], "lines": item["lines"], "methods": len(item["methods"])})
    return result


def _io_in_class(classes: list[dict], io_calls: list[dict]) -> bool:
    """粗略判断 IO 是否和类写在同一文件同一行段；静态分析拿不到作用域时不硬猜。"""
    if not io_calls:
        return False
    for call in io_calls:
        for item in classes:
            if item["file"] != call["file"]:
                continue
            if item["line"] <= call["line"] <= item["line"] + item["lines"]:
                return True
    return False


def _entry_file(root: Path, modules: list[dict]) -> str:
    for candidate in ("main.py", "app.py", "run.py"):
        if any(item["file"] == candidate for item in modules):
            return candidate
    return modules[0]["file"] if modules else ""


def render(report: dict) -> None:
    shape = report["shape"]
    print(f"工程    {report['root']}")
    print(f"规模    {len(report['modules'])} 个 .py，{report['totalLines']} 行")
    print(f"框架    {shape['framework'] or shape['service'] or '无 GUI/服务框架（库或脚本）'}")
    print(f"入口    {shape['entry'] or '未识别'}{'（有 __main__ 保护）' if shape['hasMainGuard'] else ''}")

    if report["modules"]:
        print("\n模块")
        for item in report["modules"][:12]:
            print(f"  {item['file']:<28}{item['lines']} 行")

    if report["classes"]:
        print(f"\n类（{len(report['classes'])}）")
        for item in report["classes"][:15]:
            bases = ", ".join(item["bases"]) or "-"
            print(f"  {item['name']:<26}{bases}  {item['file']}:{item['line']}  {item['lines']} 行")
            methods = [name for name in item["methods"] if name != "__init__"]
            if methods:
                print(f"      方法：{', '.join(methods[:10])}")

    if report["hooks"]:
        print("\n绘制钩子")
        for item in report["hooks"]:
            print(f"  {item['class']}.{item['method']}  ({item['file']})")

    if report["baseClasses"]:
        print("\n基类（被多个类继承）")
        for item in report["baseClasses"]:
            print(f"  {item['name']}   {item['lines']} 行，{item['methods']} 个方法")

    if report["ioCalls"]:
        print(f"\nIO 调用（{len(report['ioCalls'])}）")
        for item in report["ioCalls"][:8]:
            print(f"  {item['call']}()  {item['file']}:{item['line']}")

    if report["asyncDefs"]:
        print(f"\nasync 函数（{len(report['asyncDefs'])}）")
        for item in report["asyncDefs"][:6]:
            print(f"  {item}")

    print("\n形态判断")
    labels = []
    if shape["gui"]:
        labels.append(f"GUI（{shape['framework']}）")
    if shape["service"]:
        labels.append(f"服务（{shape['service']}）")
    if not labels:
        labels.append("无界面，按库或脚本处理")
    if shape["selfDrawn"]:
        labels.append("自绘窗口")
    if shape["baseLayered"]:
        labels.append("存在「基类绘制 + 子类叠加」结构")
    if shape["singleFile"]:
        labels.append("单文件")
    if shape["ioInClass"]:
        labels.append("IO 与类写在一起")
    print("  " + "；".join(labels))

    hints = _hints(shape, report)
    if hints:
        print("\n接入提示")
        for hint in hints:
            print(f"  - {hint}")
    if report["errors"]:
        print("\n解析失败（不影响其余文件）")
        for item in report["errors"]:
            print(f"  - {item}")


def _hints(shape: dict, report: dict) -> list[str]:
    hints: list[str] = []
    if shape["baseLayered"]:
        hints.append(
            "基类画基础元素、子类叠加内容：导出图像时必须两层都在，"
            "见 references/integration-patterns.md 的 Qt 一节与「图层验收」"
        )
    elif shape["gui"] and shape["selfDrawn"]:
        hints.append("自绘窗口：不能无头渲染时改用数值展示（line/metrics），或另用 PIL/matplotlib 重画")
    if shape["gui"] and not shape["hasMainGuard"]:
        hints.append("入口没有 __main__ 保护：探针 import 时可能弹窗或卡住，先加保护")
    if shape["ioInClass"]:
        hints.append("IO 写在类里：适配层复用同一份读取逻辑，不要复制一套解析")
    if shape["async"]:
        hints.append("存在 async 接口：需要同步门面，见「async 接口」一节")
    if shape["singleFile"] and shape["totalLines"] > 400:
        hints.append("单文件偏大：适配层单独建文件，不要往原文件里继续堆")
    if not shape["gui"] and not shape["service"]:
        hints.append("纯库或脚本：槽位直接填公开函数，object 可留空")
    return hints


if __name__ == "__main__":
    raise SystemExit(main())
