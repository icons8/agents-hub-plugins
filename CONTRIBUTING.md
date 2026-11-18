# Adding a plugin to the catalog

## What a plugin needs

- A public GitHub repo, named `hub-plugin-<name>`, with `hub-plugin.json` in its root (the format is in the hub's
  `EXTENDING.md`). Besides what the plugin does, the manifest carries `icon`, `description` and `why` — a plugin
  without them is installable, but the catalog asks for all three.
- `hub` in the manifest: the Plugin API your plugin needs. A plugin that needs a newer API than the core of the
  current release is not taken until that release is out.
- No secrets in the repo, a license, a README that says what the plugin reads, writes and sends out.

## How to send it

1. Check out the hub core (the checker uses the same code the hub uses to install): `git clone <core repo> core`.
2. In a branch of this repo:
   ```
   HUB_CORE=$PWD/core python3 tools/catalog.py add <owner>/hub-plugin-<name>
   ```
   It pins the current head of your repo, reads the manifest, writes the entry (version, description, rights, your
   login as `author`) and copies the icon into `icons/`.
3. Check: `HUB_CORE=$PWD/core python3 tools/catalog.py verify` and
   `(cd core && python3 -m core.marketplace validate .. --strict)`.
4. Open a pull request. CI runs the same two checks.

## What happens next

- **The catalog keeps a copy of your plugin** under icons8 (`icons8/hub-plugin-<name>`), with the text in English by
  default, and installs that copy. You stay the author: in the entry, in [CONTRIBUTORS.md](CONTRIBUTORS.md) and at the
  top of the copy's README. Your later commits are taken into the copy by the maintainers; you keep working in your
  repo as before.

- **A new plugin is accepted only with the owner's word.** Open the pull request and say what the plugin is for;
  the maintainers read all of the code (what the page does, where it goes on the network, which files it reads and
  writes, what processes it starts) and ask the owner.
- **New rights are accepted only with the owner's word** as well: a plugin that starts to ask for a page, a process, a
  command or a skill it did not have is read like a new plugin.
- **An update with the same rights** is read by a maintainer (the diff of the code between the two commits) and
  merged on their verdict. Send it the same way as a new plugin: `tools/catalog.py add <owner>/hub-plugin-<name>`
  moves your entry to the current head of your repo.
- The person who opened a pull request does not approve it.
- To leave the catalog, or to take a bad version back: open an issue or a pull request that removes the entry or
  sets `"revoked": "<why>"`.

## What the reviewers look at

The description must match what the code does. A page plugin has full access to the hub page, so: what it renders,
what it sends to the hub, what it sends anywhere else. A process: what it runs, which files and settings of the hub
and of the machine it touches, the network. Skills: what they tell the agent to do. Obfuscated or minified code that
is not built from the sources in the repo is not accepted.
