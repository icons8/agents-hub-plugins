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
2. A new plugin is the owner's decision: read the whole code, bring the verdict to the owner, merge only after the
   owner's word. The catalog holds only plugins read that way.
3. After a merge hubs see the update within a day (they refresh the catalog daily, or on `hub marketplace update`).
   An author may delete a pinned commit: run `python3 tools/catalog.py verify` locally now and then (see below).

## Taking a plugin out

Remove its entry (hubs keep what they have installed; it just stops getting updates), or set
`"revoked": "<why>"` (a hub that has it switches it off and tells its owner; an install needs `--despite-revoked`). A repo that must not come back: a line in
`exclude.txt` (`tools/catalog.py add` skips it, `verify` fails on it).

## Local checks

```
export HUB_CORE=<checkout of the hub core>
(cd "$HUB_CORE" && python3 -m core.marketplace validate "$OLDPWD" --strict)
python3 tools/catalog.py verify                    # every pinned entry against the code at its sha
```
