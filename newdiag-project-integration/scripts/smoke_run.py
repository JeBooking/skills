"""用平台生成的代码把流程真跑一遍，自动应答人工待办。

用法：
  python smoke_run.py workflow.json --projects projects.json [--input k=v] [--params n1.scale=1]
                        [--decisions n1=yes] [--timeout 180] [--dir 目录] [--keep]

它做四件事：探测 → 编译（发布口径）→ 生成 flow.py → 起子进程按平台事件协议执行。
人工待办由本脚本按规则应答，所以能一路跑到终止节点，暴露只有运行时才看得到的形状/序列化问题。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

import _common


class SmokeError(Exception):
    pass


def main() -> int:
    _common.force_utf8()
    parser = argparse.ArgumentParser(description="生成代码冒烟执行")
    parser.add_argument("workflow", help="流程文件")
    parser.add_argument("--projects", required=True, help="工程登记表 projects.json")
    parser.add_argument("--root", default="", help="NewDiag 仓库根；缺省自动定位")
    parser.add_argument("--input", action="append", default=[], help="启动输入 k=v，可重复")
    parser.add_argument("--params", action="append", default=[], help="现场参数 node.k=v 或 k=v，可重复")
    parser.add_argument("--file", action="append", default=[], help="文件型现场参数 node.k=本地路径，可重复")
    parser.add_argument("--decisions", action="append", default=[], help="判定 node=yes / node=yes,no / node=always:no")
    parser.add_argument("--timeout", type=float, default=180.0, help="总超时秒数")
    parser.add_argument("--max-events", type=int, default=400, help="事件上限，防止环把冒烟跑飞")
    parser.add_argument("--dir", default="", help="工作目录；缺省用临时目录")
    parser.add_argument("--keep", action="store_true", help="结束后保留工作目录")
    parser.add_argument("--json", dest="json_out", default="", help="把报告写到文件")
    args = parser.parse_args()

    root, platform, mode = _common.resolve(args.root)
    if platform is None:
        print("冒烟执行  已跳过：需要 NewDiag 源码")
        print("  生成 flow.py 与运行库都来自平台，脱离源码无法真实执行。")
        print("  本机有源码时会自动启用；在此之前可用 validate_workflow.py 做结构预检。")
        return 0
    python = _common.platform_python(root)

    doc = _common.load_doc(args.workflow, platform["expand"])
    _common.recompact(doc, platform)
    projects = _common.load_projects(args.projects, root)

    functions = _common.probe(doc, projects, platform)
    failures = _common.probe_failures(functions)
    if failures:
        print("接口探测失败，先修正再冒烟：")
        for key, message in failures:
            print(f"  - {key}：{_tail(message)}")
        return 1
    usable = {key: value for key, value in functions.items() if isinstance(value, list)}

    try:
        plan = platform["compile_doc"](doc, "smoke", 1, usable)
    except platform["ValidationError"] as exc:
        print("编译未通过（与发布同一套校验）：")
        for issue in exc.issues:
            node = f"[{issue.get('nodeId')}] " if issue.get("nodeId") else ""
            print(f"  - {node}{issue.get('message')}")
        return 1

    plan = platform["penetrate"](plan)
    if not plan.get("steps"):
        print("编译结果为空：没有可执行的步骤")
        return 1

    directory = _prepare_dir(args, root, platform, plan, doc, projects, python, args.input)
    report = {
        "workflow": str(Path(args.workflow).expanduser().resolve()),
        "directory": str(directory),
        "passed": False,
        "terminal": "",
        "path": [],
        "records": [],
        "errors": [],
        "stderr": "",
        "input": _parse_assignments(args.input),
        "startedAt": _now(),
    }

    driver = _Driver(directory, python, plan, args, report)
    try:
        driver.run(timeout=args.timeout)
    finally:
        driver.close()

    report["stderr"] = driver.stderr_tail()
    # 有执行错误就不算通过：别的分支跑到终止，掩盖不了这条分支的接口/形状问题
    report["passed"] = bool(report["terminal"]) and not report["errors"]
    if args.json_out:
        _common.json_dump(report, args.json_out)

    _print_report(report, args.keep)
    if not args.keep and not args.json_out:
        shutil.rmtree(directory, ignore_errors=True)
    return 0 if report["passed"] else 1


# ---------------------------------------------------------------- 布局


def _prepare_dir(args, root, platform, plan, doc, projects, python, inputs) -> Path:
    directory = Path(args.dir).expanduser().resolve() if args.dir else Path(tempfile.mkdtemp(prefix="newdiag-smoke-"))
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "flow.py").write_text(platform["generate"](plan), encoding="utf-8")
    runtime = directory / "runtime"
    if runtime.exists():
        shutil.rmtree(runtime)
    shutil.copytree(
        root / "backend" / "app" / "worker",
        runtime,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    (directory / "artifacts").mkdir(exist_ok=True)
    (directory / "files").mkdir(exist_ok=True)

    configuration = {
        "projects": {
            name: {
                "name": name,
                "path": project.path,
                "module": project.module,
                "factory": "",
                "python": project.python or python,
            }
            for name, project in projects.items()
            if name in {call.get("project") for _, call in _all_calls(doc)}
        },
        "runtime": {"mode": "serial", "maxParallel": 1},
        "input": {"global": _start_inputs(doc, inputs)},
        "artifactRoot": str(directory / "artifacts"),
        "fileRoot": str(directory / "files"),
        "meta": {
            node["id"]: {
                "title": node.get("title") or node["id"],
                "detail": node.get("detail") or (node.get("fields") or {}).get("observation") or "",
                "kind": node["kind"],
                "attachments": [],
            }
            for node in doc.get("nodes") or []
            if node.get("id") and node.get("kind")
        },
    }
    (directory / "configuration.json").write_text(
        json.dumps(configuration, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    initial = {"node": plan["entry"], "token": uuid.uuid4().hex}
    (directory / "initial.json").write_text(json.dumps(initial, ensure_ascii=False), encoding="utf-8")
    for name, spec in configuration["projects"].items():
        if not Path(spec["path"]).is_dir():
            raise SmokeError(f"工程「{name}」的目录不存在：{spec['path']}")
        if not Path(spec["python"]).is_file():
            raise SmokeError(f"工程「{name}」的解释器不存在：{spec['python']}")
    return directory


def _all_calls(doc: dict):
    for node in doc.get("nodes") or []:
        slots = node.get("slots") or {}
        if not isinstance(slots, dict):
            continue
        for name, value in slots.items():
            items = value if name == "constructs" else [value]
            for call in items or []:
                if isinstance(call, dict):
                    yield node.get("id"), call


def _start_inputs(doc: dict, overrides: list[str]) -> dict:
    """启动上下文：设计时默认值打底，--input 覆盖；缺值当场报出来。"""
    defaults: dict = {}
    start = next((node for node in doc.get("nodes") or [] if node.get("kind") == "start"), None)
    for item in (start or {}).get("inputs") or []:
        if isinstance(item, dict) and item.get("name") and item.get("default") is not None:
            defaults[str(item["name"])] = item["default"]
    values = {**defaults, **_parse_assignments(overrides)}
    used: set[str] = set()

    def walk(value) -> None:
        if not isinstance(value, dict):
            return
        if value.get("source") == "input" and value.get("name"):
            used.add(str(value["name"]))
        for item in value.get("items") or []:
            walk(item)
        for item in (value.get("fields") or {}).values():
            walk(item)

    for _, call in _all_calls(doc):
        for value in (call.get("arguments") or {}).values():
            walk(value)
    missing = sorted(used - set(values))
    if missing:
        raise SmokeError(
            "启动输入没有值：{}；在开始节点给 default，或用 --input name=value 提供".format(
                "、".join(missing)
            )
        )
    return values


# ---------------------------------------------------------------- 驱动


class _Driver:
    def __init__(self, directory: Path, python: str, plan: dict, args, report: dict) -> None:
        self.directory = directory
        self.plan = plan
        self.args = args
        self.report = report
        self.kinds = {nid: step.get("kind", "") for nid, step in (plan.get("steps") or {}).items()}
        self.decisions = _parse_decisions(args.decisions)
        self.decision_index: dict[str, int] = {}
        self.params = _parse_named(args.params)
        self.files = _parse_named(args.file)
        self.events = 0
        self.process = subprocess.Popen(
            [python, "-u", "flow.py", "--worker", "initial.json"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            cwd=str(directory),
            env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"},
        )
        self.queue: queue.Queue = queue.Queue()
        self.stderr_lines: list[str] = []
        threading.Thread(target=_pump, args=(self.process.stdout, self.queue), daemon=True).start()
        threading.Thread(target=_pump, args=(self.process.stderr, None, self.stderr_lines), daemon=True).start()
        self.states: dict[str, str] = {}

    def run(self, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self.report["errors"].append(f"超时：{int(timeout)} 秒内没有到达终止节点")
                _stop_tree(self.process)
                return
            try:
                line = self.queue.get(timeout=min(remaining, 1.0))
            except queue.Empty:
                continue
            if line is None:
                return
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                self.report["errors"].append(f"无法解析子进程事件：{line[:200]}")
                continue
            self.events += 1
            if self.events > self.args.max_events:
                self.report["errors"].append(f"事件超过 {self.args.max_events} 条，疑似环没有出口")
                _stop_tree(self.process)
                return
            if not self._handle(event):
                return

    def _handle(self, event: dict) -> bool:
        kind = event.get("type")
        node = str(event.get("node") or "")
        if kind == "enter":
            self._send({"token": event.get("token") or uuid.uuid4().hex})
        elif kind == "ask":
            request = event.get("request") or {}
            self._send(self._answer(node, request))
        elif kind == "complete":
            self._record(event, node)
            self._send({})
        elif kind == "error":
            message = str(event.get("message") or "")
            self.report["errors"].append(_summarize_error(message))
            self._send({})
        elif kind == "finish":
            self._send({})
            return False
        else:
            self._send({})
        return True

    def _record(self, event: dict, node: str) -> None:
        output = event.get("output") if isinstance(event.get("output"), dict) else {}
        presentation = output.get("presentation") if isinstance(output.get("presentation"), dict) else {}
        blocks = presentation.get("blocks") if isinstance(presentation.get("blocks"), list) else []
        branch = str(event.get("branch") or "default")
        self.report["path"].append(f"{node}({branch})" if self.kinds.get(node) == "investigation" else node)
        record = {
            "node": node,
            "kind": self.kinds.get(node, ""),
            "branch": branch,
            "blocks": [str(block.get("type")) for block in blocks if isinstance(block, dict)],
            "result": output.get("result"),
            "reason": output.get("reason"),
            "failed": bool(event.get("failed")),
            "files": len(event.get("files") or []),
        }
        if record["failed"]:
            self.report["errors"].append(f"节点 {node} 没有后继，被平台记为失败")
        self.report["records"].append(record)
        if self.kinds.get(node) == "problemSolved":
            self.report["terminal"] = "problemSolved"
        elif self.kinds.get(node) == "escalation" and not self.report["terminal"]:
            self.report["terminal"] = "escalation"

    def _answer(self, node: str, request: dict) -> dict:
        rtype = request.get("type")
        if rtype == "parameters":
            data = {}
            for field in request.get("fields") or []:
                name = str(field.get("name"))
                ftype = str(field.get("type") or "text")
                raw = self.params.get(f"{node}.{name}", self.params.get(name))
                if ftype == "file":
                    data[name] = self._file(node, name)
                elif raw is not None:
                    data[name] = _coerce(ftype, raw, name)
                elif field.get("default") is not None:
                    data[name] = field["default"]
                elif not field.get("required", True):
                    continue
                else:
                    data[name] = _fallback(ftype, name)
            return {"result": "done", "data": data}
        if rtype in ("decision", "judgment"):
            return {"result": self._decision(node), "reason": "冒烟脚本自动应答"}
        if rtype == "after_success":
            return {"result": "done"}
        return {"result": "done"}

    def _decision(self, node: str) -> str:
        sequence = self.decisions.get(node) or self.decisions.get("*")
        if not sequence:
            return "yes"
        index = self.decision_index.get(node, 0)
        self.decision_index[node] = index + 1
        return sequence[min(index, len(sequence) - 1)]

    def _file(self, node: str, name: str) -> dict:
        raw = self.files.get(f"{node}.{name}", self.files.get(name))
        if not raw:
            raise SmokeError(f"现场参数 {name} 是文件类型：用 --file {node}.{name}=<本地路径> 提供")
        source = Path(raw).expanduser().resolve()
        if not source.is_file():
            raise SmokeError(f"文件不存在：{source}")
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        target = self.directory / "files" / digest[:2] / digest
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.is_file():
            shutil.copyfile(source, target)
        return {"hash": f"sha256:{digest}", "name": source.name}

    def _send(self, payload: dict) -> None:
        try:
            self.process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
            self.process.stdin.flush()
        except (BrokenPipeError, OSError):
            pass

    def close(self) -> None:
        try:
            if self.process.stdin and not self.process.stdin.closed:
                self.process.stdin.close()
        except OSError:
            pass
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _stop_tree(self.process)

    def stderr_tail(self) -> str:
        text = "\n".join(self.stderr_lines)
        return text[-6000:]


def _pump(stream, target: queue.Queue | None, sink: list[str] | None = None) -> None:
    try:
        for line in stream:
            if target is not None:
                target.put(line.rstrip("\n"))
            if sink is not None:
                sink.append(line.rstrip("\n"))
    except (ValueError, OSError):
        pass
    finally:
        if target is not None:
            target.put(None)


def _stop_tree(process) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            timeout=10,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    else:
        try:
            os.killpg(process.pid, 9)
        except (ProcessLookupError, PermissionError):
            process.kill()


# ---------------------------------------------------------------- 参数


def _parse_assignments(items: list[str]) -> dict:
    result = {}
    for item in items:
        if "=" not in item:
            raise SmokeError(f"参数要写成 k=v：{item}")
        key, value = item.split("=", 1)
        result[key.strip()] = _loose(value)
    return result


def _parse_named(items: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise SmokeError(f"参数要写成 name=value 或 node.name=value：{item}")
        key, value = item.split("=", 1)
        result[key.strip()] = value
    return result


def _parse_decisions(items: list[str]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for item in items:
        if "=" not in item:
            raise SmokeError(f"判定要写成 node=yes 或 node=yes,no：{item}")
        node, value = item.split("=", 1)
        node = node.strip() or "*"
        if value.strip().startswith("always:"):
            value = value.split(":", 1)[1]
        sequence = [part.strip() for part in value.split(",") if part.strip()]
        bad = [part for part in sequence if part not in ("yes", "no")]
        if bad:
            raise SmokeError(f"判定只能是 yes/no：{', '.join(bad)}")
        result[node] = sequence or ["yes"]
    return result


def _loose(text: str):
    stripped = text.strip()
    if stripped in ("true", "True"):
        return True
    if stripped in ("false", "False"):
        return False
    if stripped in ("null", "None"):
        return None
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return text


def _coerce(kind: str, raw: str, name: str):
    if kind == "number":
        try:
            number = float(raw)
        except ValueError:
            raise SmokeError(f"{name} 需要数字，收到 {raw!r}")
        return int(number) if number.is_integer() else number
    if kind == "boolean":
        value = raw.strip().lower()
        if value in ("1", "true", "yes"):
            return True
        if value in ("0", "false", "no"):
            return False
        raise SmokeError(f"{name} 需要布尔值，收到 {raw!r}")
    if kind in ("array", "object", "json"):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            raise SmokeError(f"{name} 需要合法 JSON：{raw!r}")
    return raw


def _fallback(kind: str, name: str):
    mapping = {
        "text": "冒烟",
        "number": 1,
        "boolean": True,
        "datetime": "2026-01-01T00:00:00+08:00",
        "array": [],
        "object": {},
        "json": {},
    }
    if kind == "file":
        raise SmokeError(f"现场参数 {name} 是文件类型：用 --file 提供本地文件")
    if kind not in mapping:
        raise SmokeError(f"现场参数 {name} 的类型 {kind} 没有默认值，请用 --params 指定")
    return mapping[kind]


# ---------------------------------------------------------------- 报告


def _print_report(report: dict, keep: bool) -> None:
    if report["passed"]:
        headline = f"通过（到达 {report['terminal']}）"
    elif report["terminal"]:
        headline = f"未通过：到达 {report['terminal']}，但有 {len(report['errors'])} 处执行错误"
    else:
        headline = "未通过：未到达终止节点"
    print(f"\n冒烟执行  {headline}")
    print(f"节点路径  {' → '.join(report['path']) or '（无）'}")
    if report["records"]:
        print("\n节点结果")
        for record in report["records"]:
            marks = []
            if record["blocks"]:
                marks.append("展示=" + "/".join(record["blocks"]))
            if record["result"] is not None and record["kind"] == "investigation":
                marks.append(f"判定={record['result']}")
            if record["reason"]:
                marks.append(str(record["reason"])[:60])
            if record["files"]:
                marks.append(f"文件={record['files']}")
            print(f"  {record['node']:<14} {record['branch']:<8} {'，'.join(marks) or 'ok'}")
    if report["errors"]:
        print(f"\n错误（{len(report['errors'])}）")
        for line in report["errors"]:
            print(f"  - {_tail(line, 400)}")
    if report["stderr"]:
        tail = [line for line in report["stderr"].splitlines() if line.strip()][-6:]
        if tail:
            print("\n子进程输出（尾部）")
            for line in tail:
                print(f"  {line[:240]}")
    if keep or not report["passed"]:
        print(f"\n工作目录  {report['directory']}")
        print("  可查看 flow.py / configuration.json / artifacts；确认无用后整个目录删掉即可。")


def _tail(text: str, limit: int = 240) -> str:
    flat = " / ".join(line.strip() for line in str(text).splitlines() if line.strip())
    flat = re.sub(r"\s+", " ", flat)
    return flat if len(flat) <= limit else "…" + flat[-limit:]


FILE_LINE = re.compile(r'File "([^"]+)", line (\d+), in ([\w<>]+)')
EXCEPTION_LINE = re.compile(r"^([A-Za-z_][\w.]*(?:Error|Exception|Exit|Interrupt))\b")


def _summarize_error(message: str) -> str:
    """从 execution/exception 的 traceback 里提炼异常行和最后一条业务栈帧。"""
    text = str(message)
    if text.startswith("execution/exception:"):
        text = text[len("execution/exception:"):]
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    location = ""
    for line in reversed(lines):
        match = FILE_LINE.search(line)
        if match:
            location = f"{Path(match.group(1)).name}:{match.group(2)} {match.group(3)}"
            break
    detail = ""
    for line in reversed(lines):
        if EXCEPTION_LINE.match(line):
            detail = line
            break
    if not detail:
        detail = lines[-1] if lines else "执行失败"
    return f"{detail}（{location}）" if location else detail


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SmokeError as exc:
        print(f"冒烟无法开始：{exc}", file=sys.stderr)
        raise SystemExit(2)
