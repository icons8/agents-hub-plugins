# Maintaining the catalog

## One-time setup (the owner)

- Repo settings: branch protection on `main` — merge only through a pull request with one approval (the author of the
  pull request does not approve their own), push to `main` forbidden, status check `check` required, force-push and
  deletion forbidden.
- Variable `HUB_CORE_REPO` — the hub core repo (`owner/repo`) the workflows check out.
- Secret `HUB_CORE_TOKEN` — a read token for the core, only while the core repo is private.
- Maintainers (merge rights) — the owner and whoever the owner gives `maintain` to.

## The routine

The only check that runs by itself is `check.yml` (on a pull request and on a push to `main`). Nothing opens pull requests
and nothing runs on a schedule: the catalog changes when someone sends a pull request.

1. For an update: read the code diff between the two pinned commits, check the rights line, write the verdict in the
   pull request, merge. An update that asks for more rights goes to the owner first.
   The plugins are copies the catalog keeps (below): the author's update comes in through `sync`.
2. A new plugin is the owner's decision: read the whole code, bring the verdict to the owner, merge only after the
   owner's word. The catalog holds only plugins read that way.
3. After a merge hubs see the update within a day (they refresh the catalog daily, or on `hub marketplace update`).
   An author may delete a pinned commit: run `python3 tools/catalog.py verify` locally now and then (see below).

## Copies of the authors' plugins

Every entry installs a copy under icons8: `source` is `icons8/hub-plugin-<name>`, `author` is the original author's
login, `upstream` is `{github: <author>/hub-plugin-<name>, sha: <the author's commit the copy holds>}`. A copy is the
author's history with our commits on top (the text in English by default, the author's line in its README). Nothing
pulls by itself: a maintainer runs

```
python3 tools/catalog.py sync <name>     # or --all
```

For each copy it fetches the author's default branch and merges it into the copy, then stops where a person has to
look, with the clone kept in the folder it prints:

- a conflict with our commits: resolve it in the clone, commit, `sync <name> --resume`;
- new text that is not English: translate it in the clone (a translation the plugin keeps on purpose stays), commit,
  `sync <name> --resume`;
- new rights (`plugrights` of the copy before and after differ): the owner's word first; with it,
  `sync <name> --resume --accept` (only for that one plugin) and the pull request says so;
- a Plugin API newer than the core's: wait for the core release.

A clone `sync` stopped in is never wiped by the next run: `--resume`, or remove the folder to start over.

Otherwise it pushes the merged copy (never with force) and moves the entry (`source.sha`, `upstream.sha`, version,
rights) with CONTRIBUTORS.md. Read the author's diff it prints, run `verify`, send the change as a pull request. `sync`
needs a git identity that may push to the copies. While the copies are private, `verify` is run locally: the token of
this repo's CI does not see other private repos.

A new plugin: copy the author's repo into a private `icons8/hub-plugin-<name>` (a mirror of its default branch, not a
GitHub fork: a fork of a public repo is public), add our commits, then
`tools/catalog.py add icons8/hub-plugin-<name> --upstream <author>/hub-plugin-<name> --upstream-sha <their commit>` and
`tools/catalog.py contributors`.

## Taking a plugin out

Remove its entry (hubs keep what they have installed; it just stops getting updates), or set
`"revoked": "<why>"` (a hub that has it switches it off and tells its owner; an install needs `--despite-revoked`). A repo that must not come back: a line in
`exclude.txt` (`tools/catalog.py add` skips it, `verify` fails on it).

## Local checks

```
export HUB_CORE=<checkout of the hub core>
(cd "$HUB_CORE" && python3 -m core.marketplace validate "$OLDPWD" --strict)
python3 tools/catalog.py verify                    # every pinned entry against the code at its sha
                                                   # (a copy holds its upstream commit; CONTRIBUTORS.md is current)
```
