"""Tests of tools/catalog.py: HUB_CORE=<checkout of the core> python3 -m unittest discover -s tools -p 'test_catalog.py'"""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import catalog  # noqa: E402

SHA = "a" * 40


def man(**kw):
    out = {"name": "maps", "version": "1.1.0", "description": "Maps", "icon": "icon.svg", "hub": ">=1", "web": True,
           "themes": [], "skills": False, "run": None, "missing": []}
    return {**out, **kw}


class AddTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="catalog-test-")
        self.addCleanup(lambda: __import__("shutil").rmtree(self.dir, ignore_errors=True))
        os.makedirs(os.path.join(self.dir, "icons"))
        with open(os.path.join(self.dir, "icons", "maps.svg"), "w") as f:
            f.write("<svg>old</svg>")
        self.old = {"name": "maps", "type": "plugin", "source": {"github": "acme/hub-plugin-maps", "sha": "b" * 40},
                    "version": "1.0.0", "description": "Maps", "category": "interface", "tags": ["page"],
                    "author": "acme", "icon": "icons/maps.svg", "rights": {"page": True}}
        self.data = {"name": "agents-hub", "plugins": [self.old]}
        self.state = None

    def inspect(self, state, repo, sha, allow_newer=False):
        """What the real one does: the code of the plugin is unpacked inside the state folder."""
        self.state = state
        root = os.path.join(state, "plugins", "maps")
        os.makedirs(root)
        with open(os.path.join(root, "icon.svg"), "w") as f:
            f.write("<svg>new</svg>")
        return {"man": man(), "root": root, "commit": sha, "rights": {"page": True}, "pairs": [], "newer": False}

    def add(self):
        args = mock.Mock(repo="acme/hub-plugin-maps", sha=SHA, upstream=None, upstream_sha=None)
        saved = []
        with mock.patch.object(catalog, "ROOT", self.dir), \
                mock.patch.object(catalog, "inspect", self.inspect), \
                mock.patch.object(catalog, "load", lambda: self.data), \
                mock.patch.object(catalog, "write", lambda data: saved.append(data)), \
                mock.patch.object(catalog, "excluded", lambda: set()):
            with contextlib.redirect_stdout(io.StringIO()):
                code = catalog.add(args)
        return code, saved

    def test_update_keeps_the_icon_and_takes_the_new_file(self):
        code, saved = self.add()
        self.assertEqual(code, 0)
        e = saved[0]["plugins"][0]
        self.assertEqual(e["icon"], "icons/maps.svg")
        self.assertEqual(e["source"]["sha"], SHA)
        with open(os.path.join(self.dir, "icons", "maps.svg")) as f:
            self.assertEqual(f.read(), "<svg>new</svg>")

    def test_checkout_is_removed_afterwards(self):
        self.add()
        self.assertFalse(os.path.exists(self.state))

    def test_taken_name_is_a_refusal_and_the_checkout_is_removed(self):
        self.old["source"]["github"] = "other/hub-plugin-maps"
        code, saved = self.add()
        self.assertEqual(code, 1)
        self.assertEqual(saved, [])
        self.assertFalse(os.path.exists(self.state))

    def test_a_copy_takes_the_place_of_its_authors_entry_and_keeps_the_author(self):
        args = mock.Mock(repo="icons8/hub-plugin-maps", sha=SHA, upstream="acme/hub-plugin-maps", upstream_sha=None)
        saved = []
        with mock.patch.object(catalog, "ROOT", self.dir), \
                mock.patch.object(catalog, "inspect", self.inspect), \
                mock.patch.object(catalog, "load", lambda: self.data), \
                mock.patch.object(catalog, "write", lambda data: saved.append(data)), \
                mock.patch.object(catalog, "excluded", lambda: set()):
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(catalog.add(args), 0)
        (e,) = saved[0]["plugins"]
        self.assertEqual((e["source"], e["author"], e["upstream"]),
                         ({"github": "icons8/hub-plugin-maps", "sha": SHA}, "acme",
                          {"github": "acme/hub-plugin-maps", "sha": "b" * 40}))   # the author's pin is what the copy holds


def git(cwd, *args):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout.strip()


class SyncTest(unittest.TestCase):
    """sync on local repos: the author's repo, the catalog's copy of it (bare, as on GitHub) and a catalog folder."""

    MAN = {"name": "maps", "version": "1.0.0", "description": "Maps", "why": "To see maps", "hub": "^1.0",
           "web": "main.js"}

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="catalog-sync-test-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.author = os.path.join(self.dir, "author")
        os.makedirs(self.author)
        git(self.author, "init", "-q", "-b", "main")
        self.commit(self.author, {"hub-plugin.json": json.dumps(self.MAN), "main.js": "// maps\n"}, "first")
        self.pin = git(self.author, "rev-parse", "HEAD")
        self.copy = os.path.join(self.dir, "copy.git")
        git(self.dir, "clone", "-q", "--bare", self.author, self.copy)
        work = os.path.join(self.dir, "w")
        git(self.dir, "clone", "-q", self.copy, work)
        self.commit(work, {"main.js": "// maps, in English\n"}, "English by default")
        git(work, "push", "-q", "origin", "main")
        self.head = git(work, "rev-parse", "HEAD")
        self.cat = os.path.join(self.dir, "catalog")
        os.makedirs(self.cat)
        self.data = {"name": "agents-hub", "plugins": [
            {"name": "maps", "type": "plugin", "source": {"github": "icons8/hub-plugin-maps", "sha": self.head},
             "version": "1.0.0", "description": "Maps", "author": "acme", "rights": {"page": True},
             "upstream": {"github": "acme/hub-plugin-maps", "sha": self.pin}}]}
        self.clones = os.path.join(self.dir, "clones")

    def commit(self, repo, files, msg):
        for k, v in files.items():
            with open(os.path.join(repo, k), "w", encoding="utf-8") as f:
                f.write(v)
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", msg)

    def sync(self, *extra):
        urls = {"icons8/hub-plugin-maps": self.copy, "acme/hub-plugin-maps": self.author}
        out = io.StringIO()
        args = catalog.main.__globals__["argparse"].Namespace(names=["maps"], all=False, work=self.clones,
                                                              resume="--resume" in extra, accept="--accept" in extra)
        env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
        with mock.patch.object(catalog, "ROOT", self.cat), mock.patch.object(catalog, "git_url", urls.get), \
                mock.patch.object(catalog, "load", lambda: self.data), mock.patch.object(catalog, "write", lambda d: None), \
                mock.patch.dict(os.environ, env), contextlib.redirect_stdout(out):
            code = catalog.sync(args)
        return code, out.getvalue()

    def entry(self):
        (e,) = self.data["plugins"]
        return e

    def copy_head(self):
        return git(self.copy, "rev-parse", "main")

    def test_nothing_new_upstream_changes_nothing(self):
        code, out = self.sync()
        self.assertEqual(code, 0, out)
        self.assertIn("up to date", out)
        self.assertEqual(self.entry()["source"]["sha"], self.head)

    def test_an_english_update_is_merged_pushed_and_the_entry_moves_with_the_author_kept(self):
        self.commit(self.author, {"hub-plugin.json": json.dumps({**self.MAN, "version": "1.1.0"}),
                                  "extra.js": "// a new file\n"}, "1.1.0")
        new = git(self.author, "rev-parse", "HEAD")
        code, out = self.sync()
        self.assertEqual(code, 0, out)
        e = self.entry()
        self.assertEqual(e["source"]["sha"], self.copy_head())
        self.assertNotEqual(e["source"]["sha"], self.head)
        self.assertEqual((e["upstream"], e["author"], e["version"]),
                         ({"github": "acme/hub-plugin-maps", "sha": new}, "acme", "1.1.0"))
        self.assertEqual(git(self.copy, "show", "main:main.js"), "// maps, in English")   # our translation stays
        self.assertIn(f"compare/{self.pin[:12]}...{new[:12]}", out)

    def test_new_text_that_is_not_english_stops_until_it_is_translated(self):
        self.commit(self.author, {"extra.js": 'const t = "Привет";\n'}, "a new string")
        code, out = self.sync()
        self.assertEqual(code, catalog.STOPPED, out)
        self.assertIn("extra.js", out)
        self.assertEqual(self.copy_head(), self.head)                     # nothing pushed
        clone = os.path.join(self.clones, "maps")
        self.commit(clone, {"extra.js": 'const t = "Hello";\n'}, "English by default: the new string")
        code, out = self.sync("--resume")
        self.assertEqual(code, 0, out)
        self.assertEqual(git(self.copy, "show", "main:extra.js"), 'const t = "Hello";')
        self.assertEqual(self.entry()["source"]["sha"], self.copy_head())

    def test_new_rights_stop_and_nothing_is_pushed(self):
        self.commit(self.author, {"hub-plugin.json": json.dumps({**self.MAN, "run": ["python3", "main.py"]})}, "a process")
        code, out = self.sync()
        self.assertEqual(code, catalog.STOPPED, out)
        self.assertIn("new rights", out)
        self.assertIn("python3 main.py", out)
        self.assertEqual((self.copy_head(), self.entry()["source"]["sha"]), (self.head, self.head))
        code, out = self.sync("--resume", "--accept")                       # the owner said yes
        self.assertEqual(code, 0, out)
        self.assertEqual(self.entry()["rights"], {"page": True, "process": "python3 main.py"})

    def test_a_conflict_with_our_translation_stops_and_resumes_after_the_merge(self):
        self.commit(self.author, {"main.js": "// карты\n"}, "the author edits the same line")
        code, out = self.sync()
        self.assertEqual(code, catalog.STOPPED, out)
        self.assertIn("conflict in main.js", out)
        clone = os.path.join(self.clones, "maps")
        with open(os.path.join(clone, "main.js"), "w") as f:
            f.write("// maps, in English\n")
        git(clone, "add", "main.js")
        git(clone, "commit", "-q", "--no-edit")
        code, out = self.sync("--resume")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.entry()["upstream"]["sha"], git(self.author, "rev-parse", "HEAD"))

    def test_a_copy_moved_outside_the_catalog_is_not_synced(self):
        work = os.path.join(self.dir, "w")
        self.commit(work, {"main.js": "// someone pushed\n"}, "by hand")
        git(work, "push", "-q", "origin", "main")
        code, out = self.sync()
        self.assertEqual(code, 1, out)
        self.assertIn("settle that by hand", out)


class ContributorsTest(unittest.TestCase):
    def test_authors_come_from_upstream_and_from_plain_entries(self):
        data = {"plugins": [
            {"name": "maps", "source": {"github": "icons8/hub-plugin-maps"}, "upstream": {"github": "acme/hub-plugin-maps"}},
            {"name": "owl", "source": {"github": "bob/hub-plugin-owl"}}]}
        text = catalog.credits(data)
        self.assertIn("## [@acme](https://github.com/acme)\n\n- **maps** — original repo [acme/hub-plugin-maps]", text)
        self.assertIn("**owl** — original repo [bob/hub-plugin-owl]", text)
        self.assertIn("[@acme](https://github.com/acme) (maps), [@bob](https://github.com/bob) (owl)",
                      catalog.authors_block(data))


if __name__ == "__main__":
    unittest.main()
