# Agents Hub marketplace

The official catalog of plugins for Agents Hub: pages, themes, skills and helper
processes. The hub connects it on its own; nothing else is needed to browse and install.

```
hub marketplace search            # find a plugin
hub marketplace list agents-hub   # everything in this catalog, with the rights each one asks
hub marketplace install <name>    # install, or move an installed one to the version in the catalog
```

## What the catalog promises

- **Every entry is pinned to a commit** (`source.sha`). The hub installs exactly that commit, never the author's
  latest one. An author's new push reaches hubs only after a maintainer has read it and merged it here.
- **The rights are shown before the install** and are taken from the code at the pinned commit, not from this
  file. An update that asks for more than you allowed is not installed until you say so (`--accept`).
- **Every merge is a read.** A page plugin has full access to the hub page (it can do what you can do from the page),
  a process runs with your rights on your machine, so the code diff of each update is read before it goes in.
  That is a review by a person, not a guarantee: read the rights, and keep to plugins whose authors you know.
- A plugin can be taken back (`revoked` in its entry): a hub that has it installed switches it off and tells its owner why, and an
  install needs an explicit `--despite-revoked`.

## What is here

```
hub-marketplace.json   the catalog: one entry per plugin (name, author, pinned commit, version, rights, icon)
icons/                 icons of the entries (the hub page never goes to an author's server for them)
exclude.txt            repos that are kept out, and why
tools/catalog.py       add a plugin, check the catalog against the code it pins
.github/workflows/     check (on every pull request and every push to main)
```

The format of `hub-marketplace.json` and the rules the hub applies to it are in the hub's documentation (the section
about marketplaces). Check a file yourself with `hub marketplace validate <folder>`.

## Add your plugin

See [CONTRIBUTING.md](CONTRIBUTING.md). Maintaining the repo itself: [MAINTAINING.md](MAINTAINING.md).
