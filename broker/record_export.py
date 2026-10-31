#!/usr/bin/env python3
"""record_export.py -- Broker Recorder trajectories -> SFT-ready JSONL.

Each agent step that worked becomes one training sample:

    system   what the model is and the tools it has
    user     the task, the last few actions, and the page as it was before the step
             (URL, title, focused element, every visible element as [id] role "name")
    assistant  one tool call: click(element="e3"), type(element="e1", text="..."), ...

Formats:
    --format openai  (default) {"messages": [...], "tools": [...]} with OpenAI-style
                     tool_calls; what TRL / most SFT trainers take, LFM2 chat template included.
    --format lfm     the tool list and the call written inline with LFM2's own tokens
                     (<|tool_list_start|> ... <|tool_call_start|>[click(element="e3")]<|tool_call_end|>).

    --images         put the step's screenshot (when one was recorded) in front of the
                     text as an image part, for vision models (LFM2-VL / LFM3-VL).
    --stats          print a summary of the recordings instead of exporting.

Usage:
    python3 record_export.py                      # ~/.local/broker-recordings -> stdout
    python3 record_export.py DIR_OR_FILES... -o train.jsonl
    python3 record_export.py --stats

Everything is passed through scrub.deep_scrub() once more on the way out.
"""
import argparse
import glob
import json
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import scrub  # noqa: E402

DEFAULT_DIR = os.path.expanduser(os.environ.get("BROKER_RECORD_DIR") or "~/.local/broker-recordings")
FAILED = {"failure", "failed", "fail", "error", "abandoned", "aborted"}

SYSTEM = ("You are a browser agent working in a real, logged-in web browser. Each turn you get the task, "
          "the actions taken so far, and the current page: its URL, title and the visible elements, each "
          "with an id like [e3]. Answer with exactly one tool call that moves the task forward. Refer to "
          "elements by id. Never type passwords, card numbers or one-time codes; the human enters those.")

TOOLS = [
    ("navigate", "Go to a URL in the current tab.", {"url": "string"}, ["url"]),
    ("click", "Click an element (or a point when no element fits).",
     {"element": "string", "x": "number", "y": "number", "button": "string", "count": "integer"}, []),
    ("type", "Type text into the focused element, or into `element` (it is focused first).",
     {"element": "string", "text": "string"}, ["text"]),
    ("key", "Press a key, with optional modifiers (Ctrl, Meta, Alt).",
     {"key": "string", "modifiers": "array"}, ["key"]),
    ("scroll", "Scroll by dx, dy pixels, over `element` if given.",
     {"element": "string", "dx": "number", "dy": "number"}, []),
    ("select", "Choose an option in a select element.", {"element": "string", "code": "string"}, []),
    ("upload", "Attach files to a file input.", {"element": "string", "files": "array"}, ["files"]),
    ("run_js", "Run JavaScript in the page.", {"code": "string"}, ["code"]),
    ("open_tab", "Open a new tab at a URL.", {"url": "string"}, ["url"]),
    ("close_tab", "Close the current tab.", {}, []),
    ("reload", "Reload the page.", {}, []),
    ("history", "Go to a history entry.", {"entry": "integer"}, ["entry"]),
    ("focus", "Focus an element.", {"element": "string"}, []),
    ("set_value", "Set an attribute on an element.", {"element": "string", "attribute": "string",
                                                      "text": "string"}, ["text"]),
]


def tool_specs():
    out = []
    for name, desc, props, req in TOOLS:
        out.append({"type": "function", "function": {
            "name": name, "description": desc,
            "parameters": {"type": "object",
                           "properties": {k: ({"type": v, "items": {"type": "string"}} if v == "array" else {"type": v})
                                          for k, v in props.items()},
                           "required": req}}})
    return out


# ---------------------------------------------------------------------------
# reading recordings
# ---------------------------------------------------------------------------
def find_files(paths):
    files = []
    for p in paths or [DEFAULT_DIR]:
        p = os.path.expanduser(p)
        if os.path.isdir(p):
            files += sorted(glob.glob(os.path.join(p, "**", "*.jsonl"), recursive=True))
        elif os.path.isfile(p):
            files.append(p)
    return files


def load_job(path):
    head, steps, end = None, [], None
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue                     # a line cut short by a crash
            t = r.get("type")
            if t == "job":
                head = r
            elif t == "step":
                steps.append(r)
            elif t == "end":
                end = r
    steps.sort(key=lambda s: s.get("seq", 0))
    by_seq = {s.get("seq"): s for s in steps}
    for s in steps:                          # resolve {"same_as": seq}
        o = s.get("observation")
        hops = 0
        while isinstance(o, dict) and "same_as" in o and hops < 1000:
            o = (by_seq.get(o["same_as"]) or {}).get("observation")
            hops += 1
        s["observation"] = o if isinstance(o, dict) and "elements" in o else None
    job = dict(head or {})
    if end:
        job.update({k: v for k, v in end.items() if v is not None and k not in ("type", "t")})
    job["path"] = path
    job["steps"] = steps
    return job


# ---------------------------------------------------------------------------
# rendering one sample
# ---------------------------------------------------------------------------
def render_element(e):
    bits = [f'[{e.get("node")}] {e.get("role") or e.get("tag")}']
    if e.get("name"):
        bits.append(json.dumps(e["name"], ensure_ascii=False))
    extra = []
    if e.get("input_type"):
        extra.append(e["input_type"])
        if "checked" in e:
            extra.append("checked" if e["checked"] else "unchecked")
        else:
            extra.append("filled" if e.get("filled") else "empty")
        if e.get("sensitive"):
            extra.append("sensitive")
    if extra:
        bits.append("(" + ", ".join(extra) + ")")
    b = e.get("box")
    if b:
        bits.append("@" + ",".join(str(int(v)) for v in b))
    return " ".join(bits)


def render_observation(o):
    lines = [f"URL: {o.get('url') or ''}", f"Title: {o.get('title') or ''}"]
    if o.get("focused"):
        lines.append(f"Focused: {o['focused']}")
    lines.append("Elements:")
    lines += [render_element(e) for e in o.get("elements") or []]
    return "\n".join(lines)


def tool_call(step):
    """(name, arguments) for one recorded action, or None when it is not a trainable call."""
    a, t = step.get("action") or {}, step.get("target") or {}
    typ = a.get("type")
    el = t.get("node")
    if typ == "navigate":
        return "navigate", {"url": a.get("url")}
    if typ == "click":
        args = {"element": el} if el else {"x": a.get("x"), "y": a.get("y")}
        if a.get("button") not in (None, "left"):
            args["button"] = a["button"]
        if (a.get("count") or 1) > 1:
            args["count"] = a["count"]
        return "click", args
    if typ == "type":
        args = {"text": a.get("text") or ""}
        if el:
            args = {"element": el, **args}
        return "type", args
    if typ == "key":
        args = {"key": a.get("key")}
        if a.get("modifiers"):
            args["modifiers"] = a["modifiers"]
        return "key", args
    if typ == "scroll":
        args = {"dx": a.get("dx", 0), "dy": a.get("dy", 0)}
        if el:
            args["element"] = el
        return "scroll", args
    if typ == "select":
        return "select", {k: v for k, v in (("element", el), ("code", a.get("code"))) if v}
    if typ == "upload":
        return "upload", {k: v for k, v in (("element", el), ("files", a.get("files") or [])) if v is not None}
    if typ == "js":
        return "run_js", {"code": a.get("code") or ""}
    if typ in ("open_tab",):
        return "open_tab", {"url": a.get("url")}
    if typ in ("close_tab", "reload"):
        return typ, {}
    if typ == "history":
        return "history", {"entry": a.get("entry")}
    if typ == "focus":
        return "focus", ({"element": el} if el else {})
    if typ == "set_value":
        return "set_value", {k: v for k, v in (("element", el), ("attribute", a.get("attribute")),
                                                ("text", a.get("text"))) if v is not None}
    return None


def describe(step):
    tc = tool_call(step)
    if tc is None:
        return (step.get("action") or {}).get("type", "?")
    name, args = tc
    return f"{name}({', '.join(f'{k}={json.dumps(v, ensure_ascii=False)}' for k, v in args.items())})"


def lfm_call(name, args):
    return "<|tool_call_start|>[" + name + "(" + ", ".join(
        f"{k}={json.dumps(v, ensure_ascii=False)}" for k, v in args.items()) + ")]<|tool_call_end|>"


def usable(step, opts):
    """Why this step is not a training sample, or None when it is one."""
    if step.get("actor") != "agent":
        return "human step"
    r = step.get("result") or {}
    if r.get("ok") is not True:
        return "failed or refused"
    if step.get("observation") is None:
        return "no observation"
    if step.get("observation_stale") and not opts.allow_stale:
        return "stale observation"
    a = step.get("action") or {}
    if a.get("redacted") and not opts.keep_redacted:
        return "redacted"
    if a.get("type") in ("js", "select") and opts.drop_js:
        return "js dropped"
    if tool_call(step) is None:
        return "not a tool call"
    return None


def samples(job, opts):
    task = job.get("task")
    if not task and not opts.allow_no_task:
        return
    if str(job.get("outcome") or "").lower() in FAILED and not opts.include_failed:
        return
    if opts.only_success and str(job.get("outcome") or "").lower() != "success":
        return
    tools = tool_specs()
    history = []
    for step in job["steps"]:
        why = usable(step, opts)
        if why is None:
            name, args = tool_call(step)
            past = "\n".join(f"{i}. {h}" for i, h in enumerate(history[-opts.history:],
                                                              max(1, len(history) - opts.history + 1)))
            text = (f"Task: {task or '(not given)'}\n\n"
                    f"Previous actions:\n{past or '(none)'}\n\n"
                    f"Current page:\n{render_observation(step['observation'])}")
            user = {"role": "user", "content": text}
            shot = step.get("screenshot")
            if opts.images and shot:
                path = os.path.join(opts.root_for(job["path"]), shot)
                if os.path.exists(path):
                    user["content"] = [{"type": "image", "image": path}, {"type": "text", "text": text}]
            if opts.format == "lfm":
                system = SYSTEM + "\nList of tools: <|tool_list_start|>" + json.dumps(
                    [t["function"] for t in tools]) + "<|tool_list_end|>"
                rec = {"messages": [{"role": "system", "content": system}, user,
                                    {"role": "assistant", "content": lfm_call(name, args)}]}
            else:
                rec = {"messages": [{"role": "system", "content": SYSTEM}, user,
                                    {"role": "assistant", "content": "", "tool_calls": [
                                        {"type": "function",
                                         "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}]}],
                       "tools": tools}
            rec["meta"] = {"job": job.get("job"), "seq": step.get("seq"), "outcome": job.get("outcome")}
            yield scrub.deep_scrub(rec)
        if step.get("actor") == "agent" and (step.get("result") or {}).get("ok"):
            history.append(describe(step))
        elif step.get("actor") == "human":
            history.append(f"(the human: {(step.get('action') or {}).get('type')})")


# ---------------------------------------------------------------------------
# stats
# ---------------------------------------------------------------------------
def stats(jobs, opts):
    c = Counter()
    outcomes, kinds, actions, actors, skip = Counter(), Counter(), Counter(), Counter(), Counter()
    dropped = 0
    for job in jobs:
        c["jobs"] += 1
        outcomes[str(job.get("outcome") or "unfinished")] += 1
        kinds[str(job.get("owner_kind") or "?")] += 1
        if job.get("task"):
            c["jobs_with_task"] += 1
        dropped = max(dropped, int(job.get("dropped_events") or 0))
        for s in job["steps"]:
            c["steps"] += 1
            actors[s.get("actor")] += 1
            actions[(s.get("action") or {}).get("type")] += 1
            if s.get("observation") is not None:
                c["steps_with_observation"] += 1
            if s.get("observation_stale"):
                c["stale_observations"] += 1
            if s.get("screenshot"):
                c["screenshots"] += 1
            if (s.get("action") or {}).get("redacted"):
                c["redacted_steps"] += 1
            r = s.get("result") or {}
            if r.get("refused_by_broker"):
                c["refused_by_broker"] += 1
            elif r.get("ok") is False:
                c["errors"] += 1
            why = usable(s, opts)
            if why:
                skip[why] += 1
        c["samples"] += sum(1 for _ in samples(job, opts))
    return {"recordings": opts.paths or [DEFAULT_DIR], "jobs": c["jobs"], "jobs_with_task": c["jobs_with_task"],
            "outcomes": dict(outcomes), "owner_kinds": dict(kinds), "steps": c["steps"],
            "by_actor": dict(actors), "by_action": dict(actions),
            "steps_with_observation": c["steps_with_observation"], "stale_observations": c["stale_observations"],
            "screenshots": c["screenshots"], "redacted_steps": c["redacted_steps"],
            "refused_by_broker": c["refused_by_broker"], "errors": c["errors"],
            "max_dropped_events": dropped, "samples_exportable": c["samples"], "skipped": dict(skip)}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="*", help=f"recording dirs or .jsonl files (default {DEFAULT_DIR})")
    ap.add_argument("-o", "--out", help="write here instead of stdout")
    ap.add_argument("--stats", action="store_true", help="print a summary, export nothing")
    ap.add_argument("--format", choices=("openai", "lfm"), default="openai")
    ap.add_argument("--images", action="store_true", help="add the step's screenshot as an image part")
    ap.add_argument("--history", type=int, default=6, help="previous actions shown per sample (default 6)")
    ap.add_argument("--allow-stale", action="store_true", help="keep steps whose page snapshot predates the step before")
    ap.add_argument("--allow-no-task", action="store_true", help="keep jobs that never said what the task was")
    ap.add_argument("--include-failed", action="store_true", help="keep jobs whose outcome is a failure")
    ap.add_argument("--only-success", action="store_true", help="only jobs whose outcome is success")
    ap.add_argument("--keep-redacted", action="store_true", help="keep steps whose text was redacted")
    ap.add_argument("--drop-js", action="store_true", help="leave out run_js / select steps")
    opts = ap.parse_args(argv)
    files = find_files(opts.paths)
    roots = [os.path.expanduser(p) for p in (opts.paths or [DEFAULT_DIR]) if os.path.isdir(os.path.expanduser(p))]

    def root_for(path):
        for r in roots:
            if os.path.abspath(path).startswith(os.path.abspath(r)):
                return r
        return os.path.dirname(os.path.dirname(path))     # <root>/<day>/<job>.jsonl
    opts.root_for = root_for
    jobs = [load_job(f) for f in files]
    if opts.stats:
        print(json.dumps(stats(jobs, opts), indent=2))
        return 0
    out = open(opts.out, "w", encoding="utf-8") if opts.out else sys.stdout
    if opts.out:
        os.chmod(opts.out, 0o600)
    n = 0
    try:
        for job in jobs:
            for rec in samples(job, opts):
                out.write(json.dumps(rec, ensure_ascii=False) + "\n")
                n += 1
    finally:
        if opts.out:
            out.close()
    print(f"{n} samples from {len(jobs)} jobs", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
