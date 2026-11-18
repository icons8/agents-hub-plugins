#!/usr/bin/env python3
"""Tools of the Agents Hub marketplace repo: build the catalog, check it against the code it pins.

  python3 tools/catalog.py init   --trusted trusted.json     one-off: first catalog from the hub's trusted list
  python3 tools/catalog.py add    owner/hub-plugin-x [--sha S]   add a plugin (or move it to a newer commit)
  python3 tools/catalog.py add    icons8/hub-plugin-x --upstream author/hub-plugin-x [--upstream-sha S]
                                                              the entry installs the catalog's copy of an author's plugin
  python3 tools/catalog.py sync   <name> | --all [--resume]   take the author's updates into the copy, move the entry
  python3 tools/catalog.py contributors                       CONTRIBUTORS.md and the authors block of README.md
  python3 tools/catalog.py verify [--since base.json]         every pinned entry against the code at its sha

The hub core is needed (same parser, same rights as the hub uses when installing): HUB_CORE=<checkout of the core>.
add and verify only read GitHub (`gh api`, `git fetch`); sync pushes the merged copy to its repo (never with force).
"""
import argparse
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unicodedata

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORE = os.environ.get("HUB_CORE") or os.path.join(os.path.dirname(ROOT), "core")
sys.path.insert(0, CORE)

from core import marketplace, mpinstall, plugrights, version  # noqa: E402
from modules.market import market  # noqa: E402

FILE = marketplace.FILE
EXCLUDE = "exclude.txt"
CATEGORY = (("themes", "themes"), ("skills", "skills"), ("process", "tools"), ("page", "interface"))
ORDER = ("name", "type", "source", "version", "description", "category", "tags", "author", "upstream", "icon", "hubs",
         "rights")
CONTRIBUTORS = "CONTRIBUTORS.md"
README = "README.md"
AUTHORS = ("<!-- authors: tools/catalog.py contributors -->", "<!-- /authors -->")
WORK = os.path.join(tempfile.gettempdir(), "hub-catalog-sync")   # sync's clones: kept when a person has to step in
STOPPED = 3   # sync exit code: a person has to look (conflict, new text to translate, new rights)


class Skip(RuntimeError):
    """The entry cannot be built or checked: why, in one line."""


def gh(*args):
    r = subprocess.run(["gh", "api", *args], capture_output=True, text=True)
    if r.returncode:
        raise Skip((r.stderr or r.stdout).strip().splitlines()[-1][:200] if (r.stderr or r.stdout).strip() else "gh api failed")
    return r.stdout


def head(repo):
    """Current commit of the author's default branch."""
    return gh(f"repos/{repo}/commits/HEAD", "--jq", ".sha").strip()


def load(folder=ROOT):
    with open(os.path.join(folder, FILE), encoding="utf-8") as f:
        return json.load(f)


def write(data, folder=ROOT):
    data["plugins"].sort(key=lambda p: p["name"])
    data["plugins"] = [{**{k: p[k] for k in ORDER if k in p}, **{k: v for k, v in p.items() if k not in ORDER}}
                       for p in data["plugins"]]
    path = os.path.join(folder, FILE)
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
        f.write("\n")
    os.replace(path + ".tmp", path)


def excluded(folder=ROOT):
    """Repos the maintainers keep out (one per line, `#` — why)."""
    try:
        with open(os.path.join(folder, EXCLUDE), encoding="utf-8") as f:
            return {x for x in (line.split("#")[0].strip() for line in f) if x}
    except OSError:
        return set()


def said(man, rights):
    """The rights of a plugin the way a catalog entry says them (what the hub compares with the code)."""
    out = {k: True for k in sorted({k if k in ("page", "process", "themes") else "skills" for k, _ in rights})}
    if man.get("run"):
        out["process"] = " ".join(man["run"])
    return out


def inspect(state, repo, sha, allow_newer=False):
    """The code of repo at sha, fetched and unpacked the way the hub does it -> manifest, rights, folder."""
    store = marketplace.plugins_store(state)
    try:
        commit, root = marketplace.fetch_code(store, {"url": f"https://github.com/{repo}.git", "sha": sha})
    except (RuntimeError, OSError) as e:
        raise Skip(str(e)) from None
    try:
        with open(os.path.join(root, market.MANIFEST), encoding="utf-8") as f:
            man = market.valid(json.load(f))
    except (OSError, ValueError):
        man = None
    if not man:
        raise Skip(f"no valid {market.MANIFEST} at {sha[:12]}")
    state_api, why = version.compat(man["hub"])
    if state_api == "incompatible":
        raise Skip(f"needs another Plugin API: {why}")
    if state_api == "newer" and not allow_newer:   # no hub of this release can install it
        raise Skip(f"needs a newer Plugin API than the core ({version.PLUGIN_API}): {man['hub']}")
    rights = plugrights.of(man, root)
    return {"man": man, "root": root, "commit": commit, "rights": said(man, rights), "pairs": rights,
            "newer": state_api == "newer"}


def icon(folder, name, info):
    """Copy the plugin icon into icons/ (the page never goes to an author's server) -> relative path or None."""
    rel = info["man"].get("icon")
    if not rel:
        return None
    src = os.path.join(info["root"], *rel.split("/"))
    ext = os.path.splitext(rel)[1].lower()
    if os.path.islink(src) or not os.path.isfile(src) or os.path.getsize(src) > market.ICON_MAX:
        return None
    os.makedirs(os.path.join(folder, "icons"), exist_ok=True)
    for old in os.listdir(os.path.join(folder, "icons")):
        if os.path.splitext(old)[0] == name and os.path.splitext(old)[1].lower() != ext:
            os.remove(os.path.join(folder, "icons", old))
    shutil.copyfile(src, os.path.join(folder, "icons", name + ext))
    return f"icons/{name}{ext}"


def category(man):
    kinds = {"themes": bool(man["themes"]), "skills": man["skills"], "process": bool(man["run"]),
             "page": bool(man["web"])}
    return next(c for k, c in CATEGORY if kinds[k]) if any(kinds.values()) else "tools"


def entry(folder, repo, sha, info, old=None):
    man = info["man"]
    e = dict(old or {})
    e.update({"name": man["name"], "type": "plugin", "source": {"github": repo, "sha": sha}, "rights": info["rights"],
              "author": owner(e, repo), "category": (old or {}).get("category") or category(man),
              "tags": (old or {}).get("tags") or sorted(
                  k for k, on in (("page", man["web"]), ("process", man["run"]), ("themes", man["themes"]),
                                  ("skills", man["skills"])) if on)})
    # the catalog keeps the description it has (maintainers write it in English); a new entry takes the
    # manifest's
    for key, val in (("version", man["version"]),
                     ("description", (old or {}).get("description") or man["description"])):
        if val:
            e[key] = val
        else:
            e.pop(key, None)
    ic = icon(folder, man["name"], info)
    if ic:
        e["icon"] = ic
    else:
        e.pop("icon", None)
    return e


def owner(e, repo=None):
    """The author of an entry: the owner of the upstream repo for a copy the catalog keeps, else of its own repo."""
    up = e.get("upstream") if isinstance(e.get("upstream"), dict) else {}
    src = e.get("source") if isinstance(e.get("source"), dict) else {}
    return str(up.get("github") or repo or src.get("github") or "").split("/")[0]


def init(args):
    """First catalog: every plugin of the hub's trusted list (minus the excluded) at the current head of its author."""
    with open(args.trusted, encoding="utf-8") as f:
        trusted = json.load(f)
    out = {"name": "agents-hub", "description": "Plugins for Agents Hub: pages, themes, skills, helper processes",
           "owner": {"name": "Agents Hub", "url": args.owner_url}, "plugins": []}
    if args.hub:
        out["hub"] = args.hub
    skip, names, state = excluded(), set(), tempfile.mkdtemp(prefix="catalog-")
    problems = []
    try:
        for p in sorted(trusted["plugins"], key=lambda p: p["repo"].lower()):
            repo = p["repo"]
            if repo in skip:
                print(f"excluded  {repo}")
                continue
            try:
                sha = head(repo)
                info = inspect(state, repo, sha)
            except Skip as e:
                problems.append(f"{repo}: {e}")
                print(f"SKIPPED   {repo}: {e}")
                continue
            e = entry(ROOT, repo, sha, info)
            if e["name"] in names:
                problems.append(f"{repo}: name {e['name']} is taken")
                continue
            if p.get("hubs"):
                e["hubs"] = int(p["hubs"])
            names.add(e["name"])
            out["plugins"].append(e)
            moved = f" (trusted list: {p.get('version')})" if p.get("version") != e.get("version") else ""
            print(f"ok        {repo} {e.get('version', '-')} {sha[:12]}{moved}{'' if e.get('icon') else '  NO ICON'}")
    finally:
        shutil.rmtree(state, ignore_errors=True)
    write(out)
    print(f"{len(out['plugins'])} entries, {len(problems)} problems")
    return 1 if problems else 0


def add(args):
    """The entry for one plugin repo at its head (or --sha): what a contributor puts into a pull request."""
    repo = market.repo_of(args.repo)
    if not repo:
        print(f"«{args.repo}» is not owner/repo")
        return 2
    up = market.repo_of(args.upstream) if args.upstream else None
    if args.upstream and not up:
        print(f"«{args.upstream}» is not owner/repo")
        return 2
    for x in (repo, up):
        if x and x in excluded():
            print(f"{x} is in {EXCLUDE}")
            return 1
    state = tempfile.mkdtemp(prefix="catalog-")
    try:   # the checkout stays until the entry is built: the icon is copied out of it
        sha = args.sha or head(repo)
        info = inspect(state, repo, sha)
        data = load()
        same = [p for p in data["plugins"] if p["name"] == info["man"]["name"]]
        was = same[0].get("source", {}).get("github") if same else None
        if same and was != repo and not (up and was == up):   # a copy may take the place of its author's own entry
            print(f"the name «{info['man']['name']}» is taken by {same[0].get('source')}")
            return 1
        old = same[0] if same else None
        if up:   # the author's commit the copy was made from: given, or the one the author's entry pinned
            prev = old or {}
            took = prev.get("upstream") if isinstance(prev.get("upstream"), dict) else {}
            up_sha = args.upstream_sha or (prev["source"].get("sha") if was == up else
                                           took.get("sha") if took.get("github") == up else None)
            if not up_sha:
                print("--upstream-sha: the commit of the author's repo the copy was made from")
                return 2
            old = {**prev, "upstream": {"github": up, "sha": up_sha}}
        e = entry(ROOT, repo, sha, info, old)
    except Skip as err:
        print(f"{repo}: {err}")
        return 1
    finally:
        shutil.rmtree(state, ignore_errors=True)
    data["plugins"] = [p for p in data["plugins"] if p["name"] != e["name"]] + [e]
    write(data)
    write_credits(data)
    print(f"{'updated' if same else 'added'} {e['name']} {e.get('version', '')} at {sha[:12]}; rights: {describe(e['rights'])}"
          + (f"; a copy of {e['upstream']['github']} at {e['upstream']['sha'][:12]}" if e.get("upstream") else ""))
    for miss in info["man"]["missing"]:
        print(f"! the manifest has no «{miss}» (icon, description and why are asked of every plugin)")
    return 0


def verify(args):
    """Every pinned entry against the code at its sha: the same checks the hub makes when installing, plus that the
    entry's own words (version, rights, name) are the manifest's. --since: only entries that differ from that file."""
    data = load()
    base = {}
    if args.since:
        with open(args.since, encoding="utf-8") as f:
            base = {p["name"]: p for p in json.load(f).get("plugins", [])}
    errors, state, checked, skip = [], tempfile.mkdtemp(prefix="catalog-"), 0, excluded()
    try:
        for e in data["plugins"]:
            src = e.get("source")
            if not isinstance(src, dict) or "github" not in src:
                continue
            if e.get("revoked") or base.get(e["name"]) == e:
                continue
            checked += 1
            label = f"{e['name']} ({src['github']})"
            if src["github"] in skip:
                errors.append(f"{label}: the repo is in {EXCLUDE}")
                continue
            if not src.get("sha"):
                errors.append(f"{label}: no sha")
                continue
            try:
                info = inspect(state, src["github"], src["sha"], allow_newer=True)
            except Skip as err:
                errors.append(f"{label}: {err}")
                continue
            man = info["man"]
            if man["name"] != e["name"]:
                errors.append(f"{label}: the manifest is named «{man['name']}»")
            if man["version"] and man["version"] != e.get("version"):
                errors.append(f"{label}: version {e.get('version')} in the catalog, {man['version']} in the code")
            if info["rights"] != e.get("rights"):
                errors.append(f"{label}: rights {e.get('rights')} in the catalog, {info['rights']} in the code")
            if e.get("author") and e["author"] != owner(e, src["github"]):
                errors.append(f"{label}: author is not the owner of the repo (of the upstream repo, for a copy)")
            up = e.get("upstream")
            if up is not None:
                why = upstream_wrong(src, up, skip)
                if why:
                    errors.append(f"{label}: {why}")
            if man.get("icon") and not e.get("icon"):
                errors.append(f"{label}: the plugin has an icon, the entry has none")
            if info["newer"]:
                print(f"! {label}: needs a newer Plugin API than this core ({version.PLUGIN_API})")
            print(f"ok {label}")
    finally:
        shutil.rmtree(state, ignore_errors=True)
    if credits(data) != _read(CONTRIBUTORS) or authors_block(data) not in _read(README):
        errors.append(f"{CONTRIBUTORS} or the authors block of {README} is not the catalog's: run tools/catalog.py contributors")
    for x in errors:
        print(f"✗ {x}")
    print(f"checked {checked} entries, {len(errors)} errors")
    return 1 if errors else 0


def upstream_wrong(src, up, skip=()):
    """A copy's `upstream` -> why it is wrong, or None: {github, sha} of the author's repo, and the copy at its pinned
    commit holds that commit of the author (the catalog says what of the author's it installs)."""
    if not isinstance(up, dict) or not market.repo_of(str(up.get("github") or "")) or not str(up.get("sha") or ""):
        return "upstream: {github: owner/repo, sha} of the author's repo"
    if up["github"] in skip:
        return f"the upstream repo is in {EXCLUDE}"
    try:
        gh(f"repos/{up['github']}/commits/{up['sha']}", "--jq", ".sha")
    except Skip as e:
        return f"upstream {up['sha'][:12]} is not a commit of {up['github']}: {e}"
    try:
        status = gh(f"repos/{src['github']}/compare/{up['sha']}...{src['sha']}", "--jq", ".status").strip()
    except Skip as e:
        return f"upstream {up['sha'][:12]} is not in the copy: {e}"
    return None if status in ("ahead", "identical") else f"upstream {up['sha'][:12]} is not in the copy ({status})"


# --- copies the catalog keeps: the author's updates (sync) -----------------------------------------------------------

def git_url(repo):
    """The address git fetches and pushes a GitHub repo with (the tests point it at local repos)."""
    return f"https://github.com/{repo}.git"


def git(cwd, *args, check=True, code=False):
    """git's output (code - (exit code, output)); a failure with check - Skip."""
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
    if check and r.returncode:
        tail = (r.stderr or r.stdout).strip().splitlines()
        raise Skip(f"git {args[0]}: {tail[-1][:200] if tail else 'failed'}")
    return (r.returncode, r.stdout.strip()) if code else r.stdout.strip()


def ancestor(cwd, a, b):
    return subprocess.run(["git", "merge-base", "--is-ancestor", a, b], cwd=cwd, capture_output=True).returncode == 0


def at(clone, rev, tmp):
    """The plugin at rev of a local clone, unpacked -> {man, root, rights, pairs}, as inspect gives it (nothing runs)."""
    root = tempfile.mkdtemp(dir=tmp)
    try:
        data = subprocess.run(["git", "archive", rev], cwd=clone, capture_output=True, check=True).stdout
        with tarfile.open(fileobj=io.BytesIO(data)) as t:
            t.extractall(root, filter="data")
    except (subprocess.CalledProcessError, tarfile.TarError, OSError) as e:
        raise Skip(f"cannot unpack {rev[:12]}: {e}") from None
    try:
        with open(os.path.join(root, market.MANIFEST), encoding="utf-8") as f:
            man = market.valid(json.load(f))
    except (OSError, ValueError):
        man = None
    if not man:
        raise Skip(f"no valid {market.MANIFEST} at {rev[:12]}")
    pairs = plugrights.of(man, root)
    return {"man": man, "root": root, "rights": said(man, pairs), "pairs": pairs, "commit": rev, "newer": False}


def foreign(line):
    """A line with letters of a script other than Latin: text that is not English yet (or a translation the plugin
    keeps on purpose - a person tells which)."""
    return any(ch.isalpha() and not unicodedata.name(ch, "").startswith("LATIN") for ch in line)


def new_text(clone, base, rev, limit=60):
    """[(file, line)] of lines added between base and rev that are not English yet (file by file: a line of the text
    is never taken for a header)."""
    out = []
    names = git(clone, "diff", "--name-only", "-z", "--diff-filter=AM", base, rev).split("\0")
    for path in filter(None, names):
        body = False
        for line in git(clone, "diff", "--unified=0", "--no-color", "--no-ext-diff", base, rev, "--", path).splitlines():
            if line.startswith("@@"):
                body = True
            elif body and line.startswith("+") and foreign(line[1:]):
                out.append((path, line[1:].strip()[:160]))
    return out[:limit]


def stopped(note, st, why):
    """Where sync stopped, in the clone: --resume skips the text check only after a stop for the text."""
    with open(note, "w", encoding="utf-8") as f:
        json.dump({**st, "stopped": why}, f)


def sync_one(data, e, work, resume=False, accept=False):
    """One copy: the author's new commits merged into it, the rights the same, the new text English -> the copy pushed
    and the entry moved (returns a line). A person has to look - Stop (the clone stays in work/<name>)."""
    up, src = e.get("upstream"), e.get("source") or {}
    if not isinstance(up, dict) or not up.get("github") or not up.get("sha"):
        raise Skip("not a copy: the entry has no upstream")
    copy, base, name = src["github"], src["sha"], e["name"]
    folder = os.path.join(work, name)
    note = os.path.join(folder, ".git", "hub-sync.json")
    tmp = tempfile.mkdtemp(prefix="catalog-sync-")
    try:
        if resume:
            try:
                with open(note, encoding="utf-8") as f:
                    st = json.load(f)
            except (OSError, ValueError):
                raise Skip(f"nothing to resume in {folder}: run sync without --resume") from None
            if st.get("base") != base:
                raise Skip(f"the entry moved since ({base[:12]}, the clone has {str(st.get('base'))[:12]}): sync again")
            if git(folder, "status", "--porcelain"):
                raise Stop(f"{folder}: commit the merge and the translation first")
            if not ancestor(folder, st["upstream"], "HEAD"):
                raise Stop(f"{folder}: HEAD does not hold the author's {st['upstream'][:12]}: finish the merge")
        else:
            if os.path.exists(note):
                raise Stop(f"an earlier sync stopped in {folder}: `tools/catalog.py sync {name} --resume`, or remove the "
                           f"folder to start over")
            shutil.rmtree(folder, ignore_errors=True)
            os.makedirs(work, exist_ok=True)
            git(work, "clone", "--quiet", git_url(copy), folder)
            branch = git(folder, "symbolic-ref", "--short", "refs/remotes/origin/HEAD").split("/", 1)[1]
            now = git(folder, "rev-parse", f"origin/{branch}")
            if now != base:
                raise Skip(f"{copy} {branch} is at {now[:12]}, the catalog pins {base[:12]}: settle that by hand first")
            git(folder, "checkout", "--quiet", "-B", branch, base)
            git(folder, "fetch", "--quiet", git_url(up["github"]), "HEAD")
            new = git(folder, "rev-parse", "FETCH_HEAD")
            if new == up["sha"] or ancestor(folder, new, base):
                shutil.rmtree(folder, ignore_errors=True)
                return f"{name}: up to date with {up['github']} ({new[:12]})"
            if not ancestor(folder, up["sha"], new):
                raise Stop(f"{up['github']} no longer holds {up['sha'][:12]} (history rewritten): read it by hand")
            st = {"base": base, "upstream": new, "branch": branch}
            stopped(note, st, "")
            failed, _ = git(folder, "merge", "--no-ff", "--no-edit", "-m",
                            f"Merge {up['github']} at {new[:12]}: the author's updates", "FETCH_HEAD", check=False, code=True)
            if failed:
                files = git(folder, "diff", "--name-only", "--diff-filter=U", check=False).split()
                stopped(note, st, "conflict")
                raise Stop(f"conflict in {', '.join(files) or 'the merge'}: resolve it in {folder}, commit, "
                           f"then `tools/catalog.py sync {name} --resume`")
        old, now = at(folder, base, tmp), at(folder, "HEAD", tmp)
        api, why = version.compat(now["man"]["hub"])
        if api in ("incompatible", "newer"):
            raise Stop(f"needs another Plugin API ({now['man']['hub']}; the core has {version.PLUGIN_API}): {why}")
        wider = plugrights.wider(now["pairs"], old["pairs"])
        if wider and accept:
            print(f"  {name}: new rights the owner allowed: {'; '.join(plugrights.shown(w) for w in wider)}")
        elif wider:
            stopped(note, st, "rights")
            raise Stop(f"asks for new rights: {'; '.join(plugrights.shown(w) for w in wider)} - the owner's word first, "
                       f"nothing is pushed; with it: `tools/catalog.py sync {name} --resume --accept` (the clone: {folder})")
        if now["man"]["name"] != name:
            raise Stop(f"the manifest is named «{now['man']['name']}» now")
        text = [] if resume and st.get("stopped") == "text" else new_text(folder, base, "HEAD")
        if text:
            stopped(note, st, "text")
            shown = "\n".join(f"    {p}: {t}" for p, t in text)
            raise Stop(f"new text that is not English yet:\n{shown}\n  translate it in {folder} (a translation the "
                       f"plugin keeps on purpose stays), commit, then `tools/catalog.py sync {name} --resume`")
        sha = git(folder, "rev-parse", "HEAD")
        git(folder, "push", "--quiet", git_url(copy), f"HEAD:refs/heads/{st['branch']}")
        moved = entry(ROOT, copy, sha, now, {**e, "upstream": {"github": up["github"], "sha": st["upstream"]}})
        data["plugins"] = [p for p in data["plugins"] if p["name"] != name] + [moved]
        shutil.rmtree(folder, ignore_errors=True)
        return (f"{name}: {e.get('version', '-')} -> {moved.get('version', '-')}, copy at {sha[:12]}; read the author's "
                f"diff: https://github.com/{up['github']}/compare/{up['sha'][:12]}...{st['upstream'][:12]}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


class Stop(RuntimeError):
    """sync stopped where a person has to look: why and what to do, the clone stays."""


def sync(args):
    """The author's updates into the copies the catalog keeps: `<name>` or `--all`. Nothing on a schedule: a maintainer
    runs it, reads the author's diff it prints and sends the moved entries as a pull request."""
    data = load()
    copies = [p for p in data["plugins"] if isinstance(p.get("upstream"), dict)]
    picked = copies if args.all else [p for p in copies if p["name"] in args.names]
    unknown = sorted(set(args.names) - {p["name"] for p in copies})
    if unknown or not picked:
        print(f"not a copy the catalog keeps: {', '.join(unknown) or '(nothing given)'}")
        return 2
    if args.accept and (args.all or not args.resume or len(picked) != 1):
        print("--accept: only with --resume and one name, after sync stopped on that plugin's new rights")
        return 2
    code = 0
    for e in picked:
        try:
            print(sync_one(data, e, args.work, args.resume, args.accept))
            write(data)
            write_credits(data)
        except Stop as why:
            print(f"STOPPED {e['name']}: {why}")
            code = max(code, STOPPED)
        except Skip as why:
            print(f"✗ {e['name']}: {why}")
            code = 1 if code != STOPPED else code
    return code


# --- the authors ---------------------------------------------------------------------------------------------------

def _read(name, folder=ROOT):
    try:
        with open(os.path.join(folder, name), encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def _by_author(data):
    out = {}
    for p in data["plugins"]:
        if p.get("type", "plugin") == "plugin" and owner(p) and (
                isinstance(p.get("upstream"), dict) or isinstance(p.get("source"), dict) and p["source"].get("github")):
            out.setdefault(owner(p), []).append(p)
    return dict(sorted(out.items(), key=lambda kv: kv[0].lower()))


def credits(data):
    """CONTRIBUTORS.md: every author with their plugins and the repo each one comes from."""
    lines = ["# Contributors", "",
             "The plugins of this catalog are written by their authors. The catalog installs copies it keeps under",
             "icons8 (`icons8/hub-plugin-<name>`, English by default) and takes each author's updates into them; the",
             "plugin stays its author's. Built by `tools/catalog.py contributors` from `hub-marketplace.json`.", ""]
    for login, plugins in _by_author(data).items():
        lines.append(f"## [@{login}](https://github.com/{login})")
        lines.append("")
        for p in sorted(plugins, key=lambda p: p["name"]):
            up = (p.get("upstream") if isinstance(p.get("upstream"), dict) else {}).get("github") or p["source"]["github"]
            lines.append(f"- **{p['name']}** — original repo [{up}](https://github.com/{up})")
        lines.append("")
    return "\n".join(lines)


def authors_block(data):
    names = ", ".join(f"[@{login}](https://github.com/{login}) ({', '.join(sorted(p['name'] for p in plugins))})"
                      for login, plugins in _by_author(data).items())
    return f"{AUTHORS[0]}\n{names}\n{AUTHORS[1]}"


def write_credits(data, folder=None):
    """CONTRIBUTORS.md and the authors block of README.md from the catalog -> False when README has no block."""
    folder = folder or ROOT
    with open(os.path.join(folder, CONTRIBUTORS), "w", encoding="utf-8") as f:
        f.write(credits(data))
    text = _read(README, folder)
    start, end = text.find(AUTHORS[0]), text.find(AUTHORS[1])
    if start < 0 or end < start:
        return False
    with open(os.path.join(folder, README), "w", encoding="utf-8") as f:
        f.write(text[:start] + authors_block(data) + text[end + len(AUTHORS[1]):])
    return True


def contributors(args):
    data = load()
    if not write_credits(data):
        print(f"{README} has no {AUTHORS[0]} … {AUTHORS[1]} block: put it where the authors are listed")
        return 1
    print(f"{CONTRIBUTORS} and {README}: {len(_by_author(data))} authors")
    return 0


def describe(r):
    return ", ".join(f"{k}: `{v}`" if isinstance(v, str) else k for k, v in sorted(r.items())) or "none"


def main(argv):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("init")
    a.add_argument("--trusted", required=True)
    a.add_argument("--owner-url", default="https://github.com/icons8")
    a.add_argument("--hub", default="", help="Plugin API the catalog format needs (optional, for the future)")
    a = sub.add_parser("add")
    a.add_argument("repo", help="owner/repo or the GitHub link of the plugin")
    a.add_argument("--sha", help="a commit of the repo (default: the head of its default branch)")
    a.add_argument("--upstream", help="the author's repo: the entry installs this copy of it (owner/repo)")
    a.add_argument("--upstream-sha", help="the author's commit the copy holds (default: the one the author's entry pinned)")
    a = sub.add_parser("sync")
    a.add_argument("names", nargs="*", help="entries that are copies (the default: none; --all for every copy)")
    a.add_argument("--all", action="store_true")
    a.add_argument("--resume", action="store_true", help="after a conflict or a translation committed in the clone")
    a.add_argument("--accept", action="store_true", help="new rights the owner has allowed (the pull request says so)")
    a.add_argument("--work", default=WORK, help=f"where the clones are kept (default {WORK})")
    sub.add_parser("contributors")
    a = sub.add_parser("verify")
    a.add_argument("--since", help="an earlier hub-marketplace.json: check only what differs from it")
    args = p.parse_args(argv)
    return {"init": init, "add": add, "sync": sync, "contributors": contributors, "verify": verify}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
