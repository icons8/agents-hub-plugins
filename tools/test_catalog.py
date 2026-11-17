"""Tests of tools/catalog.py: HUB_CORE=<checkout of the core> python3 -m unittest discover -s tools -p 'test_catalog.py'"""
import contextlib
import io
import os
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
        args = mock.Mock(repo="acme/hub-plugin-maps", sha=SHA)
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


if __name__ == "__main__":
    unittest.main()
