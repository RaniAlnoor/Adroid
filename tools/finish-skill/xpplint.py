#!/usr/bin/env python3
"""xpplint - static X++ / AOT metadata checker for /finish.

Used by .loop/check.sh when the D365FO compiler (MSBuild + xppc) is not on the
host. It is NOT a compiler: it catches malformed metadata, broken X++ structure
and a set of Microsoft best-practice (BP) rules, and offers small assertion
helpers so a check can tell "task done" from "task not done".

Every failure prints exactly three lines and exits 1:
    expected: ...
    actual:   ...
    where:    path[:line]

Usage:
    xpplint.py lint <model-dir>...
    xpplint.py has-method <model-dir> <Class> <method> [--access private|protected|public|internal]
    xpplint.py count <model-dir> <Class> <regex> <eq|le|ge> <n>
    xpplint.py calls <model-dir> <Class> <method> <callee>
    xpplint.py lacks <model-dir> <Class> <regex>

Custom forbid rules may be added in <repo>/.finish-lint.json:
    [{"id": "LOCAL001", "glob": "**/AxClass/*.xml", "regex": "...", "message": "..."}]
"""
import glob
import json
import os
import re
import sys
import xml.etree.ElementTree as ET

ACCESS = ("private", "protected", "public", "internal")
LABEL_CALLS = re.compile(r'\b(info|warning|error|checkFailed)\s*\(\s*"')


def fail(expected, actual, where):
    print(f"expected: {expected}")
    print(f"actual:   {actual}")
    print(f"where:    {where}")
    sys.exit(1)


# ---------------------------------------------------------------- X++ helpers
def strip_code(src):
    """Blank out comments and string literals, keep newlines (for line numbers)."""
    out, i, n = [], 0, len(src)
    while i < n:
        c = src[i]
        two = src[i:i + 2]
        if two == "//":
            j = src.find("\n", i)
            j = n if j < 0 else j
            out.append(" " * (j - i)); i = j
        elif two == "/*":
            j = src.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append(re.sub(r"[^\n]", " ", src[i:j])); i = j
        elif c in "\"'" or (c == "@" and i + 1 < n and src[i + 1] in "\"'"):
            verbatim = c == "@"
            q = src[i + 1] if verbatim else c
            j = i + (2 if verbatim else 1)
            while j < n and src[j] != q:
                if src[j] == "\\" and not verbatim:
                    j += 1
                j += 1
            j = min(j + 1, n)
            out.append('"' + re.sub(r"[^\n]", " ", src[i + 1:j - 1]) + '"'); i = j
        else:
            out.append(c); i += 1
    return "".join(out)


def line_of(text, idx):
    return text.count("\n", 0, idx) + 1


def balance(code):
    pairs = {")": "(", "]": "[", "}": "{"}
    stack = []
    for idx, ch in enumerate(code):
        if ch in "([{":
            stack.append((ch, idx))
        elif ch in pairs:
            if not stack or stack[-1][0] != pairs[ch]:
                return f"unexpected '{ch}'", idx
            stack.pop()
    if stack:
        return f"unclosed '{stack[-1][0]}'", stack[-1][1]
    return None, None


def signature(code):
    """(name, access) of the method whose (stripped) source is `code`."""
    head = code.split("{", 1)[0]
    head = re.sub(r"\[[^\]]*\]", " ", head)  # attributes
    m = re.search(r"([A-Za-z_]\w*)\s*\(", head)
    if not m:
        return None, None
    words = re.findall(r"[A-Za-z_]\w*", head[:m.start()])
    acc = next((w for w in words if w in ACCESS), "public")  # X++ default is public
    return m.group(1), acc


def body(code):
    i = code.find("{")
    return code[i:] if i >= 0 else ""


# ---------------------------------------------------------------- metadata
def model_files(dirs):
    files = []
    for d in dirs:
        files += glob.glob(os.path.join(d, "**", "Ax*", "*.xml"), recursive=True)
        files += glob.glob(os.path.join(d, "**", "Descriptor", "*.xml"), recursive=True)
    return sorted(set(files))


def parse(path):
    try:
        return ET.parse(path).getroot()
    except ET.ParseError as e:
        fail("well-formed AOT XML", f"XML parse error: {e}", path)


def class_methods(root):
    """yield (name_element, source) for every method of an AxClass/AxTable/..."""
    sc = root.find("SourceCode")
    if sc is None:
        return
    for m in sc.iter("Method"):
        yield (m.findtext("Name") or "").strip(), m.findtext("Source") or ""


def find_class(dirs, cls):
    for f in model_files(dirs):
        if os.path.basename(f) == f"{cls}.xml" and os.sep + "AxClass" + os.sep in f:
            return f
    fail(f"class {cls} in the model", "no AxClass/" + cls + ".xml found", " ".join(dirs))


def repo_root(start):
    d = os.path.abspath(start)
    while d != os.path.dirname(d):
        if os.path.isdir(os.path.join(d, ".git")):
            return d
        d = os.path.dirname(d)
    return os.path.abspath(start)


# ---------------------------------------------------------------- lint
def lint(dirs):
    files = model_files(dirs)
    if not files:
        fail("AOT metadata files (Ax*/ *.xml)", "none found", " ".join(dirs))
    for f in files:
        root = parse(f)
        name = (root.findtext("Name") or "").strip()
        stem = os.path.splitext(os.path.basename(f))[0]
        if "Descriptor" not in f and name != stem:
            fail(f"<Name> equal to file name '{stem}' [XPP002]", f"<Name>{name}</Name>", f)
        sc = root.find("SourceCode")
        if sc is not None:
            decl = sc.findtext("Declaration") or ""
            dc = strip_code(decl)
            err, idx = balance(dc)
            if err:
                fail("balanced braces in class declaration [XPP003]", err, f"{f} (Declaration, line {line_of(dc, idx)})")
        seen = set()
        for mname, src in class_methods(root):
            code = strip_code(src)
            where = f"{f} (method {mname})"
            err, idx = balance(code)
            if err:
                fail("balanced (), [], {} in method [XPP003]", err, f"{where}, line {line_of(code, idx)}")
            sig, acc = signature(code)
            if sig is None:
                fail("a method signature 'type name(...)' [XPP004]", "no signature found", where)
            if sig != mname:
                fail(f"<Name> '{mname}' to match the X++ signature [XPP004]", f"signature declares '{sig}'", where)
            if mname.lower() in seen:
                fail("unique method names [XPP004]", f"duplicate method '{mname}'", f)
            seen.add(mname.lower())
            b = body(code)
            tb = len(re.findall(r"\bttsbegin\b", b, re.I))
            tc = len(re.findall(r"\btts(commit|abort)\b", b, re.I))
            if tb != tc:
                fail("every ttsbegin matched by ttscommit/ttsabort [XPP005]",
                     f"{tb} ttsbegin vs {tc} ttscommit/ttsabort", where)
            m = LABEL_CALLS.search(strip_comments_only(src))
            if m:
                fail("a label (@File:Id) for user messages, not literal text [XPP006 BPErrorLabelIsText]",
                     f"{m.group(1)}() called with a literal string", f"{where}, line {line_of(src, m.start())}")
            if acc in ("public", "protected") and not re.search(r"^\s*///", src, re.M):
                fail(f"/// XML doc comment on {acc} method [XPP007 BPXmlDocNoDocumentationComments]",
                     "no /// comment", where)
    custom(dirs)
    return len(files)


def strip_comments_only(src):
    src = re.sub(r"/\*.*?\*/", lambda m: re.sub(r"[^\n]", " ", m.group(0)), src, flags=re.S)
    return re.sub(r"//[^\n]*", "", src)


def custom(dirs):
    cfg = os.path.join(repo_root(dirs[0]), ".finish-lint.json")
    if not os.path.exists(cfg):
        return
    try:
        rules = json.load(open(cfg))
    except ValueError as e:
        fail("valid JSON in .finish-lint.json", str(e), cfg)
    root = repo_root(dirs[0])
    for r in rules:
        for f in glob.glob(os.path.join(root, r.get("glob", "**/*.xml")), recursive=True):
            text = open(f, encoding="utf-8", errors="replace").read()
            m = re.search(r["regex"], text, re.M)
            if m:
                fail(r.get("message", "no match for " + r["regex"]) + f" [{r.get('id', 'LOCAL')}]",
                     f"found: {m.group(0)[:80]!r}", f"{f}:{line_of(text, m.start())}")


# ---------------------------------------------------------------- assertions
def class_code(dirs, cls):
    f = find_class(dirs, cls)
    root = parse(f)
    methods = {n: strip_code(s) for n, s in class_methods(root)}
    return f, methods


def main(argv):
    if len(argv) < 2:
        print(__doc__); sys.exit(2)
    cmd = argv[1]
    if cmd == "lint":
        n = lint(argv[2:] or ["."])
        print(f"ok: {n} metadata files lint-clean")
    elif cmd == "has-method":
        d, cls, meth = argv[2:5]
        want = argv[6] if len(argv) > 6 and argv[5] == "--access" else None
        f, methods = class_code([d], cls)
        if meth not in methods:
            fail(f"method {cls}.{meth}()", f"not found (methods: {', '.join(sorted(methods)) or 'none'})", f)
        _, acc = signature(methods[meth])
        if want and acc != want:
            fail(f"{cls}.{meth}() to be {want}", f"it is {acc}", f"{f} (method {meth})")
        print(f"ok: {cls}.{meth}() exists" + (f" ({acc})" if want else ""))
    elif cmd == "count":
        d, cls, rx, op, n = argv[2:7]
        f, methods = class_code([d], cls)
        hits = [(m, len(re.findall(rx, c))) for m, c in methods.items()]
        total = sum(h for _, h in hits)
        ok = {"eq": total == int(n), "le": total <= int(n), "ge": total >= int(n)}[op]
        if not ok:
            where = ", ".join(f"{m} x{h}" for m, h in hits if h) or "no method"
            fail(f"/{rx}/ {op} {n} in {cls} code", f"{total} occurrence(s)", f"{f} ({where})")
        print(f"ok: /{rx}/ {op} {n} in {cls}")
    elif cmd == "calls":
        d, cls, meth, callee = argv[2:6]
        f, methods = class_code([d], cls)
        if meth not in methods:
            fail(f"method {cls}.{meth}()", "not found", f)
        if not re.search(r"\b" + re.escape(callee) + r"\s*\(", body(methods[meth])):
            fail(f"{cls}.{meth}() to call {callee}()", "no call found", f"{f} (method {meth})")
        print(f"ok: {cls}.{meth}() calls {callee}()")
    elif cmd == "lacks":
        d, cls, rx = argv[2:5]
        f, methods = class_code([d], cls)
        for m, c in methods.items():
            mm = re.search(rx, c)
            if mm:
                fail(f"no /{rx}/ in {cls}", f"found {mm.group(0)!r}", f"{f} (method {m}, line {line_of(c, mm.start())})")
        print(f"ok: {cls} has no /{rx}/")
    else:
        print(__doc__); sys.exit(2)


if __name__ == "__main__":
    main(sys.argv)
