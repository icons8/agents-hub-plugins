#!/usr/bin/env python3
"""Tools of the Agents Hub marketplace repo: build the catalog, check it against the code it pins.

  python3 tools/catalog.py init   --trusted trusted.json     one-off: first catalog from the hub's trusted list
  python3 tools/catalog.py add    owner/hub-plugin-x [--sha S]   add a plugin (or move it to a newer commit)
  python3 tools/catalog.py verify [--since base.json]         every pinned entry against the code at its sha

The hub core is needed (same parser, same rights as the hub uses when installing): HUB_CORE=<checkout of the core>.
Only reads GitHub (`gh api`, `git fetch`).
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORE = os.environ.get("HUB_CORE") or os.path.join(os.path.dirname(ROOT), "core")
sys.path.insert(0, CORE)

from core import marketplace, mpinstall, plugrights, version  # noqa: E402
from modules.market import market  # noqa: E402

FILE = marketplace.FILE
EXCLUDE = "exclude.txt"
CATEGORY = (("themes", "themes"), ("skills", "skills"), ("process", "tools"), ("page", "interface"))
ORDER = ("name", "type", "source", "version", "description", "category", "tags", "author", "icon", "hubs", "rights")


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
              "author": repo.split("/")[0], "category": (old or {}).get("category") or category(man),
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
    if repo in excluded():
        print(f"{repo} is in {EXCLUDE}")
        return 1
    state = tempfile.mkdtemp(prefix="catalog-")
    try:
        sha = args.sha or head(repo)
        info = inspect(state, repo, sha)
    except Skip as e:
        print(f"{repo}: {e}")
        return 1
    finally:
        shutil.rmtree(state, ignore_errors=True)
    data = load()
    same = [p for p in data["plugins"] if p["name"] == info["man"]["name"]]
    if same and same[0].get("source", {}).get("github") != repo:
        print(f"the name «{info['man']['name']}» is taken by {same[0].get('source')}")
        return 1
    e = entry(ROOT, repo, sha, info, same[0] if same else None)
    data["plugins"] = [p for p in data["plugins"] if p["name"] != e["name"]] + [e]
    write(data)
    print(f"{'updated' if same else 'added'} {e['name']} {e.get('version', '')} at {sha[:12]}; rights: {describe(e['rights'])}")
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
            if e.get("author") and e["author"] != src["github"].split("/")[0]:
                errors.append(f"{label}: author is not the owner of the repo")
            if man.get("icon") and not e.get("icon"):
                errors.append(f"{label}: the plugin has an icon, the entry has none")
            if info["newer"]:
                print(f"! {label}: needs a newer Plugin API than this core ({version.PLUGIN_API})")
            print(f"ok {label}")
    finally:
        shutil.rmtree(state, ignore_errors=True)
    for x in errors:
        print(f"✗ {x}")
    print(f"checked {checked} entries, {len(errors)} errors")
    return 1 if errors else 0


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
    a = sub.add_parser("verify")
    a.add_argument("--since", help="an earlier hub-marketplace.json: check only what differs from it")
    args = p.parse_args(argv)
    return {"init": init, "add": add, "verify": verify}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
