"""接入 skill 的公共装配：定位仓库、装载平台模块、读工程登记表。

脚本不复制平台规则：校验与探测一律调用 backend 里的实现，避免 skill 与平台漂移。
"""

from __future__ import annotations

import json
import os
import sys
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

SKILL_DIR = Path(__file__).resolve().parent.parent
MARKERS = ("backend/app/services/compiler.py", "backend/app/worker/probe.py", "docs/spec.md")


def force_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8")
            except (ValueError, OSError):
                pass


def find_root() -> Path:
    """按 环境变量 → skill 位置 → 当前目录 的顺序找 NewDiag 仓库根。"""
    seen: list[Path] = []
    for base in (os.environ.get("NEWDIAG_ROOT"), SKILL_DIR, Path.cwd()):
        if not base:
            continue
        start = Path(base).expanduser().resolve()
        for candidate in (start, *start.parents):
            if candidate in seen:
                continue
            seen.append(candidate)
            if all((candidate / marker).is_file() for marker in MARKERS):
                return candidate
    raise SystemExit(
        "找不到 NewDiag 仓库根目录。请设置环境变量 NEWDIAG_ROOT 指向包含 "
        "backend/app 与 docs/spec.md 的目录。"
    )


def platform_python(root: Path) -> str:
    """平台侧解释器：项目 venv 优先，缺失时退回当前解释器。"""
    for rel in ("backend/.venv/Scripts/python.exe", "backend/.venv/bin/python", ".venv/Scripts/python.exe"):
        candidate = root / rel
        if candidate.is_file():
            return str(candidate)
    return sys.executable


def ensure_platform(root: Path) -> dict:
    """把 backend 放进 sys.path 并返回平台模块。"""
    import logging

    # 脚本自己会展示探测错误，不必再让平台 logger 打一遍带 JSON 的原文
    logging.getLogger("app.services.projects").setLevel(logging.ERROR)
    backend = str(root / "backend")
    if backend not in sys.path:
        sys.path.insert(0, backend)
    try:
        from app.services import slots as slot_service
        from app.services.codegen import generate, penetrate
        from app.services.compiler import ValidationError, compile_doc, validate_doc
        from app.services.flow_format import compact, expand
        from app.services.signatures import projects_in
    except ImportError as exc:  # pragma: no cover - 环境问题时才走到
        raise SystemExit(
            f"无法导入平台模块（{exc}）。用项目 venv 运行：{platform_python(root)}，"
            "并确认已执行 pip install -r backend/requirements.txt。"
        ) from exc
    return {
        "slots": slot_service,
        "generate": generate,
        "penetrate": penetrate,
        "ValidationError": ValidationError,
        "compile_doc": compile_doc,
        "validate_doc": validate_doc,
        "compact": compact,
        "expand": expand,
        "projects_in": projects_in,
    }


def load_doc(path: str | Path, expand_fn) -> dict:
    """读流程定义，接受导入用的 format=1 与持久 format=2；返回编辑投影。"""
    source = Path(path).expanduser()
    try:
        raw = json.loads(source.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        raise SystemExit(f"流程文件不存在：{source}")
    except json.JSONDecodeError as exc:
        raise SystemExit(f"流程文件不是合法 JSON：{source}（{exc}）")
    if not isinstance(raw, dict):
        raise SystemExit("流程文档必须是 JSON 对象")
    if raw.get("format") == 2:
        try:
            raw = expand_fn(raw)
        except ValueError as exc:
            raise SystemExit(f"持久格式读不出来：{exc}")
    if raw.get("format") != 1:
        raise SystemExit(f"不支持的流程格式：{raw.get('format')}；导入需要 format=1 编辑投影")
    return raw


def load_projects(path: str | Path, root: Path | None = None) -> dict[str, SimpleNamespace]:
    """读工程登记表；path 相对登记表所在目录解析，@newdiag 前缀指向平台仓库。"""
    source = Path(path).expanduser().resolve()
    try:
        raw = json.loads(source.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        raise SystemExit(
            f"工程登记表不存在：{source}\n"
            "用 assets/projects.example.json 复制一份，填 name / path / module / python。"
        )
    except json.JSONDecodeError as exc:
        raise SystemExit(f"工程登记表不是合法 JSON：{exc}")
    entries = raw.get("projects", raw) if isinstance(raw, dict) else raw
    if isinstance(entries, dict):
        entries = [{"name": key, **value} for key, value in entries.items()]
    if not isinstance(entries, list):
        raise SystemExit("工程登记表需要是工程数组，或 {工程名: {...}} 对象")
    result: dict[str, SimpleNamespace] = {}
    for item in entries:
        if not isinstance(item, dict) or not item.get("name"):
            raise SystemExit(f"工程登记项缺少 name：{item!r}")
        name = str(item["name"])
        raw_path = str(item.get("path") or "")
        if raw_path.startswith("@newdiag"):
            if root is None:
                raise SystemExit("登记表用了 @newdiag 前缀，但没定位到 NewDiag 仓库根")
            project_path = root / raw_path[len("@newdiag"):].lstrip("/\\")
        else:
            project_path = Path(raw_path).expanduser()
            if not project_path.is_absolute():
                project_path = (source.parent / project_path).resolve()
        result[name] = SimpleNamespace(
            name=name,
            path=str(project_path),
            module=str(item.get("module") or ""),
            python=str(item.get("python") or ""),
            factory="",
        )
    return result


def probe(doc: dict, projects: dict, platform: dict) -> dict:
    """按槽位探测接口；返回 {工程:模块:接口: [参数] 或 {error}}。"""
    return platform["slots"].probe_slots(doc, projects)


def probe_failures(functions: dict) -> list[tuple[str, str]]:
    return [
        (key, clean_error(value.get("error", "探测失败")))
        for key, value in functions.items()
        if isinstance(value, dict)
    ]


def clean_error(text) -> str:
    """平台会把整段探针输出塞进 error，这里剥到真正的错误文本。"""
    value = str(text).strip()
    for prefix in ("接口发现失败", "探针无输出"):
        head = value.splitlines()[0] if value else ""
        if head.startswith(prefix) and "：" in value:
            value = value.split("：", 1)[1].strip()
            break
    if value.startswith("{"):
        try:
            payload = json.loads(value)
            if isinstance(payload, dict) and isinstance(payload.get("error"), str):
                return str(payload["error"]).strip()
        except json.JSONDecodeError:
            pass
    return value


def error_brief(text, keep: int = 3) -> str:
    """错误摘要：第一行 + 最后几行栈帧，去掉中间的 import 噪音。"""
    lines = [line.strip() for line in clean_error(text).splitlines() if line.strip()]
    if len(lines) <= keep + 1:
        return " / ".join(lines)
    return " / ".join([lines[0], "…", *lines[-keep:]])


def referenced_projects(doc: dict, platform: dict) -> set[str]:
    return platform["projects_in"](doc)


def doc_shape(raw: dict) -> tuple[int, int]:
    nodes = raw.get("nodes") or []
    edges = raw.get("edges") or []
    return len(nodes), len(edges)


def node_label(node: dict) -> str:
    title = str(node.get("title") or "").strip()
    return f"{node.get('id')} {title}".strip()


def describe_edges(doc: dict) -> list[str]:
    """把连线渲染成人读的 A --yes--> B，方便核对分支。"""
    nodes = {str(n.get("id")): str(n.get("title") or n.get("id")) for n in doc.get("nodes") or []}
    lines = []
    for edge in doc.get("edges") or []:
        branch = edge.get("branch") or "default"
        source = nodes.get(str(edge.get("from")), str(edge.get("from")))
        target = nodes.get(str(edge.get("to")), str(edge.get("to")))
        order = f" #{edge['order']}" if edge.get("order") is not None else ""
        lines.append(f"{source} --{branch}{order}--> {target}")
    return lines


def flatten_params(params: list[dict]) -> str:
    """渲染成 name: type 或 name: type = default。"""
    parts = []
    for param in params:
        text = f"{param.get('name')}: {param.get('type') or 'any'}"
        if param.get("default") is not None:
            text += f" = {json.dumps(param['default'], ensure_ascii=False)}"
        elif param.get("required"):
            text += " 必填"
        parts.append(text)
    return "(" + ", ".join(parts) + ")"


def json_dump(value, path: str | Path) -> None:
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def recompact(raw: dict, platform: dict) -> dict:
    """走一遍平台的持久化压缩，提前暴露导入期的结构问题（不修改原对象）。"""
    return platform["compact"](deepcopy(raw))
