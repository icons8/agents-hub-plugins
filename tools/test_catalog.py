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
        self.commit(self.author, {"extra.js": 'const t = "Γεια";\n'}, "a new string")
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
        self.commit(self.author, {"main.js": "// χάρτες\n"}, "the author edits the same line")
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

    def test_after_a_conflict_the_resume_still_checks_the_new_text(self):
        self.commit(self.author, {"main.js": "// χάρτες\n", "extra.js": 'const t = "Γεια";\n'}, "both")
        self.assertEqual(self.sync()[0], catalog.STOPPED)
        clone = os.path.join(self.clones, "maps")
        with open(os.path.join(clone, "main.js"), "w") as f:
            f.write("// maps, in English\n")
        git(clone, "add", "main.js")
        git(clone, "commit", "-q", "--no-edit")
        code, out = self.sync("--resume")
        self.assertEqual(code, catalog.STOPPED, out)
        self.assertIn("extra.js", out)
        self.assertEqual(self.copy_head(), self.head)

    def test_a_new_sync_does_not_wipe_a_clone_a_person_is_working_in(self):
        self.commit(self.author, {"extra.js": 'const t = "Γεια";\n'}, "a new string")
        self.sync()
        clone = os.path.join(self.clones, "maps")
        self.commit(clone, {"extra.js": 'const t = "Hello";\n'}, "translated")
        code, out = self.sync()
        self.assertEqual(code, catalog.STOPPED, out)
        self.assertIn("--resume", out)
        self.assertEqual(git(clone, "show", "HEAD:extra.js"), 'const t = "Hello";')

    def test_accept_is_only_for_one_plugin_that_stopped(self):
        code, out = self.sync("--accept")
        self.assertEqual(code, 2, out)

    def test_a_copy_moved_outside_the_catalog_is_not_synced(self):
        work = os.path.join(self.dir, "w")
        self.commit(work, {"main.js": "// someone pushed\n"}, "by hand")
        git(work, "push", "-q", "origin", "main")
        code, out = self.sync()
        self.assertEqual(code, 1, out)
        self.assertIn("settle that by hand", out)


class SnapshotSyncTest(SyncTest):
    """The same, for a copy of snapshots: one commit holding the author's tree with our translation, no history of the
    author. sync merges through a graft in its clone and pushes one more snapshot on top."""

    def setUp(self):
        super().setUp()
        work = os.path.join(self.dir, "w")
        tree = git(work, "rev-parse", "HEAD^{tree}")
        msg = catalog.snapshot_message("maps", "1.0.0", "acme/hub-plugin-maps", self.pin, "acme <acme@example.test>")
        snap = git(work, "commit-tree", tree, "-m", msg)
        shutil.rmtree(self.copy)
        git(self.dir, "init", "-q", "--bare", "-b", "main", self.copy)
        git(work, "push", "-q", self.copy, f"{snap}:refs/heads/main")
        self.head = snap
        self.entry()["source"]["sha"] = snap
        coauthor = mock.patch.object(catalog, "coauthor", lambda login: f"{login} <1+{login}@users.noreply.github.com>")
        coauthor.start()
        self.addCleanup(coauthor.stop)

    def test_a_copy_moved_outside_the_catalog_is_not_synced(self):
        work = os.path.join(self.dir, "w2")
        git(self.dir, "clone", "-q", self.copy, work)
        self.commit(work, {"main.js": "// someone pushed\n"}, "by hand")
        git(work, "push", "-q", "origin", "main")
        code, out = self.sync()
        self.assertEqual(code, 1, out)
        self.assertIn("settle that by hand", out)

    def history(self):
        return git(self.copy, "rev-list", "main").split()

    def test_the_update_is_one_snapshot_on_top_and_the_authors_history_stays_out(self):
        self.commit(self.author, {"extra.js": "// a new file\n"}, "more")
        new = git(self.author, "rev-parse", "HEAD")
        code, out = self.sync()
        self.assertEqual(code, 0, out)
        top = self.copy_head()
        self.assertEqual(self.history(), [top, self.head])
        self.assertEqual(git(self.copy, "rev-parse", f"{top}^"), self.head)
        message = git(self.copy, "log", "-1", "--format=%B", top)
        self.assertTrue(message.startswith(f"maps 1.0.0: snapshot of acme/hub-plugin-maps@{new[:12]}"), message)
        self.assertIn("Co-authored-by: acme <1+acme@users.noreply.github.com>", message)
        for c in (self.pin, new):
            self.assertNotEqual(subprocess.run(["git", "cat-file", "-e", c], cwd=self.copy).returncode, 0, c)
        self.assertEqual(git(self.copy, "show", "main:main.js"), "// maps, in English")
        self.assertEqual(git(self.copy, "show", "main:extra.js"), "// a new file")
        self.assertEqual(self.entry()["upstream"]["sha"], new)
        self.assertTrue(catalog.names_upstream(message, self.entry()["upstream"]))
        code, out = self.sync()                                          # and the next run sees nothing new
        self.assertEqual(code, 0, out)
        self.assertIn("up to date", out)

    def test_a_resume_that_dropped_our_commits_stops_and_pushes_nothing(self):
        self.commit(self.author, {"extra.js": 'const t = "Γεια";\n'}, "a new string")
        self.assertEqual(self.sync()[0], catalog.STOPPED)
        clone = os.path.join(self.clones, "maps")
        git(clone, "reset", "-q", "--hard", "FETCH_HEAD")                # the author's tree, our translation gone
        self.commit(clone, {"extra.js": 'const t = "Hello";\n'}, "translated")
        code, out = self.sync("--resume")
        self.assertEqual(code, catalog.STOPPED, out)
        self.assertIn("our commits", out)
        self.assertEqual(self.copy_head(), self.head)

    def test_a_note_without_the_mode_still_pushes_a_snapshot(self):
        self.commit(self.author, {"extra.js": 'const t = "Γεια";\n'}, "a new string")
        self.assertEqual(self.sync()[0], catalog.STOPPED)
        clone = os.path.join(self.clones, "maps")
        note = os.path.join(clone, ".git", "hub-sync.json")
        with open(note) as f:
            st = json.load(f)
        st.pop("snapshot", None)
        with open(note, "w") as f:
            json.dump(st, f)
        self.commit(clone, {"extra.js": 'const t = "Hello";\n'}, "translated")
        code, out = self.sync("--resume")
        self.assertEqual(code, 0, out)
        self.assertEqual(len(self.history()), 2)
        self.assertNotEqual(subprocess.run(["git", "cat-file", "-e", self.pin], cwd=self.copy).returncode, 0)

    def test_the_authors_commit_off_the_default_branch_is_fetched_by_sha(self):
        git(self.author, "config", "uploadpack.allowReachableSHA1InWant", "true")   # as GitHub does
        git(self.author, "checkout", "-q", "-b", "side")
        self.commit(self.author, {"extra.js": "// on a side branch\n"}, "side")
        self.entry()["upstream"]["sha"] = git(self.author, "rev-parse", "HEAD")   # the copy holds a commit of «side»
        git(self.author, "checkout", "-q", "main")                               # the default branch is behind it
        code, out = self.sync()
        self.assertEqual(code, 0, out)
        self.assertIn("up to date", out)

    def test_a_resolved_conflict_is_pushed_as_one_snapshot(self):
        self.commit(self.author, {"main.js": "// χάρτες\n"}, "the author edits the same line")
        self.assertEqual(self.sync()[0], catalog.STOPPED)
        clone = os.path.join(self.clones, "maps")
        with open(os.path.join(clone, "main.js"), "w") as f:
            f.write("// maps, in English\n")
        git(clone, "add", "main.js")
        git(clone, "commit", "-q", "--no-edit")
        code, out = self.sync("--resume")
        self.assertEqual(code, 0, out)
        self.assertEqual(len(self.history()), 2)
        self.assertEqual(git(self.copy, "show", "main:main.js"), "// maps, in English")


class UpstreamWrongTest(unittest.TestCase):
    UP = {"github": "acme/hub-plugin-maps", "sha": "c" * 40}
    SRC = {"github": "icons8/hub-plugin-maps", "sha": "d" * 40}

    def check(self, *messages, status="diverged"):
        def gh(path, *args):
            if path.startswith("repos/acme/"):
                return "c" * 40
            if "/commits?sha=" in path:
                return json.dumps(list(messages))
            return status
        with mock.patch.object(catalog, "gh", gh):
            return catalog.upstream_wrong(self.SRC, self.UP)

    def test_a_snapshot_naming_the_authors_commit_is_right(self):
        self.assertIsNone(self.check(catalog.snapshot_message("maps", "1.0.0", self.UP["github"], self.UP["sha"], "")))

    def test_a_snapshot_of_another_commit_is_wrong(self):
        self.assertIn("is not the copy's snapshot",
                      self.check("maps 1.0.0: snapshot of acme/hub-plugin-maps@eeeeeeeeeeee"))

    def test_a_fix_of_ours_on_top_of_the_snapshot_is_right(self):
        snap = catalog.snapshot_message("maps", "1.0.0", self.UP["github"], self.UP["sha"], "")
        self.assertIsNone(self.check("A fix of the translation", snap))

    def test_a_copy_with_the_authors_history_is_still_right(self):
        self.assertIsNone(self.check("Merge acme/hub-plugin-maps", status="ahead"))


class ContributorsTest(unittest.TestCase):
    def test_authors_come_from_upstream_and_from_plain_entries(self):
        data = {"plugins": [
            {"name": "maps", "source": {"github": "icons8/hub-plugin-maps"}, "upstream": {"github": "acme/hub-plugin-maps"}},
            {"name": "owl", "source": {"github": "bob/hub-plugin-owl"}},
            {"name": "local", "source": "./plugins/local"}, {"name": "far", "source": {"git": "https://example.com/x.git"}}]}
        text = catalog.credits(data)
        self.assertIn("## [@acme](https://github.com/acme)\n\n- **maps** — original repo [acme/hub-plugin-maps]", text)
        self.assertIn("**owl** — original repo [bob/hub-plugin-owl]", text)
        self.assertIn("[@acme](https://github.com/acme) (maps), [@bob](https://github.com/bob) (owl)",
                      catalog.authors_block(data))


if __name__ == "__main__":
    unittest.main()
