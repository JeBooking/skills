"""接入 skill 的公共装配：定位仓库、装载平台模块、读工程登记表。

两套模式：
- 平台模式：本机有 NewDiag 源码时，校验与探测直接调用 backend 实现，结论与发布一致
- 独立模式：没有源码时退回内置实现（builtin_probe.py / standalone.py），只做结构预检

独立模式下的内置规则需要随平台升级同步；它的结论不冒充「发布级」。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

SKILL_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = Path(__file__).resolve().parent
MARKERS = ("backend/app/services/compiler.py", "backend/app/worker/probe.py", "docs/spec.md")
PROBE_TIMEOUT = 60


def force_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8")
            except (ValueError, OSError):
                pass


def try_find_root() -> Path | None:
    """按 环境变量 → skill 位置 → 当前目录 的顺序找 NewDiag 仓库根；找不到返回 None。"""
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
    return None


def platform_python(root: Path | None) -> str:
    """平台侧解释器：项目 venv 优先，缺失时退回当前解释器。"""
    if root is not None:
        for rel in (
            "backend/.venv/Scripts/python.exe",
            "backend/.venv/bin/python",
            ".venv/Scripts/python.exe",
        ):
            candidate = root / rel
            if candidate.is_file():
                return str(candidate)
    return sys.executable


def load_platform(root: Path) -> tuple[dict | None, str]:
    """导入平台模块；失败时返回原因，由调用方决定降级还是报错。"""
    import logging

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
    except ImportError as exc:
        return None, str(exc)
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
    }, ""


def resolve(explicit_root: str = "") -> tuple[Path | None, dict | None, str]:
    """返回 (root, platform, mode)；mode 为 platform 或 standalone。

    显式给了 --root 就必须成功，否则报错；自动定位失败则静默降级。
    """
    if explicit_root:
        root = Path(explicit_root).expanduser().resolve()
        if not all((root / marker).is_file() for marker in MARKERS):
            raise SystemExit(f"--root 指向的目录不是 NewDiag 仓库（缺少 backend/app 或 docs/spec.md）：{root}")
        platform, reason = load_platform(root)
        if platform is None:
            raise SystemExit(
                f"指定了平台仓库，但平台模块导入失败：{reason}\n"
                f"用项目解释器运行：{platform_python(root)}，并确认装好 backend/requirements.txt。"
            )
        return root, platform, "platform"

    root = try_find_root()
    if root is None:
        return None, None, "standalone"
    platform, _ = load_platform(root)
    return root, platform, ("platform" if platform else "standalone")


def load_doc(path: str | Path, expand_fn=None) -> dict:
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
        if expand_fn is None:
            raise SystemExit("收到持久格式 2，但独立模式不知道怎么展开；请提供 format=1 的编辑投影")
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


# ---------------------------------------------------------------- 探测


def probe(doc: dict, projects: dict, platform: dict | None, python: str = "") -> dict:
    """按槽位探测接口；平台模式走 backend，独立模式走内置探针。"""
    if platform is not None:
        return platform["slots"].probe_slots(doc, projects)
    return _builtin_probe_slots(doc, projects, python or sys.executable)


def _builtin_probe_slots(doc: dict, projects: dict, python: str) -> dict:
    result: dict = {}
    catalogs: dict[tuple[str, str], dict] = {}
    for call in _all_calls(doc):
        project_name = str(call.get("project") or "")
        module = str(call.get("module") or "")
        interface = call.get("interface") or call.get("function") or ""
        key = f"{project_name}:{module}:{interface}"
        if key in result:
            continue
        cache_key = (project_name, module)
        if cache_key not in catalogs:
            project = projects.get(project_name)
            if project is None:
                catalogs[cache_key] = {"error": "工程未登记"}
            else:
                catalogs[cache_key] = run_builtin_probe(project, module or project.module, python)
        catalog = catalogs[cache_key]
        entry = next((item for item in catalog.get("functions", []) if item.get("name") == interface), None)
        if entry and entry.get("kind") == "instance_method" and not call.get("object"):
            result[key] = {"error": f"实例方法 {interface} 必须指定目标对象"}
        elif entry and entry.get("interfaceError"):
            result[key] = {"error": entry["interfaceError"]}
        elif not entry:
            result[key] = {"error": catalog.get("error") or f"公开接口 {interface} 不存在"}
        else:
            result[key] = [
                *entry.get("params", []),
                {
                    "name": "$implementation",
                    "type": entry.get("fingerprint", ""),
                    "required": False,
                    "default": None,
                },
                {
                    "name": "$keywords",
                    "type": "yes" if entry.get("variadicKeywords") else "no",
                    "required": False,
                    "default": None,
                },
            ]
    return result


def run_builtin_probe(project, module: str, python: str) -> dict:
    payload = {
        "name": project.name,
        "path": project.path,
        "module": module,
        "factory": "",
        "catalog": True,
    }
    handle = tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", prefix="newdiag-probe-", delete=False, encoding="utf-8"
    )
    try:
        json.dump(payload, handle, ensure_ascii=False)
        handle.close()
        request_path = handle.name
    except OSError as exc:
        handle.close()
        return {"functions": [], "error": f"准备请求失败：{exc}"}

    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    parts = [str(Path(project.path))]
    if env.get("PYTHONPATH"):
        parts.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(parts)
    try:
        completed = subprocess.run(
            [python, str(SCRIPTS_DIR / "builtin_probe.py"), request_path],
            cwd=project.path if Path(project.path).is_dir() else None,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=PROBE_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return {"functions": [], "error": f"接口发现超时（{PROBE_TIMEOUT}s）"}
    except OSError as exc:
        return {"functions": [], "error": f"无法启动解释器 {python}：{exc}"}
    finally:
        try:
            os.unlink(request_path)
        except OSError:
            pass

    if completed.returncode != 0 and not (completed.stdout or "").strip():
        message = [line for line in (completed.stderr or "").splitlines() if line.strip()]
        return {"functions": [], "error": " / ".join(message[-3:]) or f"探针退出码 {completed.returncode}"}
    try:
        return json.loads(_last_json_line(completed.stdout))
    except (ValueError, json.JSONDecodeError):
        return {"functions": [], "error": "探针输出无法解析为 JSON"}


def _last_json_line(text: str) -> str:
    lines = [line for line in (text or "").splitlines() if line.strip()]
    for line in reversed(lines):
        if line.lstrip().startswith("{"):
            return line
    raise ValueError("探针无输出")


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


def referenced_projects(doc: dict, platform: dict | None) -> set[str]:
    if platform is not None:
        return platform["projects_in"](doc)
    names = {str(call.get("project") or "") for call in _all_calls(doc)}
    return {name for name in names if name}


def _all_calls(doc: dict):
    for node in doc.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        slots = node.get("slots")
        if not isinstance(slots, dict):
            continue
        constructs = slots.get("constructs")
        if isinstance(constructs, list):
            for call in constructs:
                if isinstance(call, dict):
                    yield call
        for name in ("fetch", "process", "present", "decide"):
            call = slots.get(name)
            if isinstance(call, dict):
                yield call


# ---------------------------------------------------------------- 小工具


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


def recompact(raw: dict, platform: dict | None) -> dict | None:
    """平台模式下走一遍持久化压缩，提前暴露导入期的结构问题（不修改原对象）。"""
    if platform is None:
        return None
    return platform["compact"](deepcopy(raw))
