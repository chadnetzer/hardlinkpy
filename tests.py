#!/usr/bin/env python

import errno
import os
import os.path
import stat
import sys
import tempfile
import time
import unittest

from shutil import rmtree

import hardlink

testdata1 = "1234" * 1024 + "abc"
testdata2 = "1234" * 1024 + "xyz"


def get_inode(filename):
    return os.lstat(filename).st_ino


class BaseTests(unittest.TestCase):
    # self.file_contents = { name: data }

    def setup_tempdir(self):
        self.root = tempfile.mkdtemp()
        os.chdir(self.root)

        # Keep track of all files, and their content, for deleting later
        self.file_contents = {}

    def remove_tempdir(self):
        rmtree(self.root)

    def verify_file_contents(self):
        for pathname, contents in self.file_contents.items():
            if contents is not None:
                with open(pathname, "r") as f:
                    actual = f.read()
                    self.assertEqual(actual, contents)

    def make_hardlinkable_file(self, pathname, contents):
        assert pathname not in self.file_contents
        assert not pathname.lstrip().startswith('/')
        if contents is None:
            dirname = pathname
            os.makedirs(dirname)
        else:
            dirname = os.path.dirname(pathname)
            if dirname:
                try:
                    os.makedirs(dirname)
                except OSError as exc:
                    if exc.errno == errno.EEXIST and os.path.isdir(dirname):
                        pass
                    else:
                        raise
            with open(pathname, 'w') as f:
                f.write(contents)

            self.file_contents[pathname] = contents

    def make_linked_file(self, src, dst):
        assert dst not in self.file_contents
        os.link(src, dst)
        self.file_contents[dst] = self.file_contents[src]

    def remove_file(self, pathname):
        assert pathname in self.file_contents
        os.unlink(pathname)
        del self.file_contents[pathname]

    def count_nlinks(self):
        """Return a dictionary of the nlink count for each tracked file."""
        nlink_counts = {}
        for pathname in self.file_contents:
            nlink_counts[pathname] = os.lstat(pathname).st_nlink
        return nlink_counts

    def find_nlinks(self, nlink):
        """Return a dictionary of the nlink count for each tracked file."""
        pathnames = []
        for pathname in self.file_contents:
            if os.lstat(pathname).st_nlink == nlink:
                pathnames.append(pathname)
        return pathnames


class TestTester(BaseTests):
    def setUp(self):
        self.setup_tempdir()

    def tearDown(self):
        self.remove_tempdir()

    def test_setup(self):
        self.make_hardlinkable_file('dir1', None)
        self.make_hardlinkable_file('dir3', None)
        self.make_hardlinkable_file('dir2/name1.ext', testdata1)
        self.assertTrue(os.path.isdir('dir1'))
        self.assertTrue(os.path.isdir('dir3'))
        self.assertTrue(os.path.isfile('dir2/name1.ext'))
        self.assertEqual(os.lstat('dir2/name1.ext').st_nlink, 1)

        self.make_linked_file('dir2/name1.ext', 'dir3/name2.ext')
        self.assertEqual(os.lstat('dir2/name1.ext').st_nlink, 2)
        self.assertEqual(os.lstat('dir3/name2.ext').st_nlink, 2)

        self.remove_file('dir2/name1.ext')
        self.assertFalse(os.path.exists('dir2/name1.ext'))
        self.assertEqual(os.lstat('dir3/name2.ext').st_nlink, 1)

        self.verify_file_contents()


class TestHappy(BaseTests):
    def setUp(self):
        self.setup_tempdir()

        self.make_hardlinkable_file("dir1/name1.ext", testdata1)
        self.make_hardlinkable_file("dir1/name2.ext", testdata1)
        self.make_hardlinkable_file("dir1/name3.ext", testdata2)
        self.make_hardlinkable_file("dir2/name1.ext", testdata1)
        self.make_hardlinkable_file("dir3/name1.ext", testdata2)
        self.make_hardlinkable_file("dir3/name1.noext", testdata1)
        self.make_hardlinkable_file("dir4/name1.ext", testdata1)
        self.make_hardlinkable_file("dir5/name1.ext", testdata2)

        now = time.time()
        other = now - 2

        for filename in ("dir1/name1.ext", "dir1/name2.ext", "dir1/name3.ext",
                         "dir2/name1.ext", "dir3/name1.ext", "dir3/name1.noext"):
            os.utime(filename, (now, now))

        os.utime("dir4/name1.ext", (other, other))

        # -c, --content-only    Only file contents have to match
        # It's possible for a umask setting of 0466 or 0577 to confuse the
        # tests that rely on this file's chmod value.
        os.chmod("dir5/name1.ext", stat.S_IRUSR)

        self.make_linked_file("dir1/name1.ext", "dir1/link")

        self.verify_file_contents()

    def tearDown(self):
        self.remove_tempdir()

    def test_hardlink_tree_dryrun(self):
        sys.argv = ["hardlink.py", "--no-stats", "--dry-run", self.root]
        hardlink.main()

        self.verify_file_contents()

        self.assertEqual(os.lstat("dir1/name1.ext").st_nlink, 2)  # Existing link
        self.assertEqual(os.lstat("dir1/name2.ext").st_nlink, 1)
        self.assertEqual(os.lstat("dir1/name3.ext").st_nlink, 1)
        self.assertEqual(os.lstat("dir2/name1.ext").st_nlink, 1)
        self.assertEqual(os.lstat("dir3/name1.ext").st_nlink, 1)
        self.assertEqual(os.lstat("dir3/name1.noext").st_nlink, 1)
        self.assertEqual(os.lstat("dir4/name1.ext").st_nlink, 1)

    def test_hardlink_tree(self):
        sys.argv = ["hardlink.py", "--no-stats", self.root]
        hardlink.main()

        self.verify_file_contents()

        self.assertEqual(get_inode("dir1/name1.ext"), get_inode("dir1/name2.ext"))
        self.assertEqual(get_inode("dir1/name1.ext"), get_inode("dir2/name1.ext"))
        self.assertEqual(get_inode("dir1/name1.ext"), get_inode("dir3/name1.noext"))
        self.assertEqual(get_inode("dir1/name3.ext"), get_inode("dir3/name1.ext"))

        self.assertNotEqual(get_inode("dir1/name1.ext"), get_inode("dir4/name1.ext"))
        self.assertNotEqual(get_inode("dir1/name3.ext"), get_inode("dir5/name1.ext"))

    def test_hardlink_tree_filenames_equal(self):
        sys.argv = ["hardlink.py", "--no-stats", "--filenames-equal", self.root]
        hardlink.main()

        self.verify_file_contents()

        self.assertEqual(get_inode("dir1/name1.ext"), get_inode("dir2/name1.ext"))

        self.assertNotEqual(get_inode("dir1/name1.ext"), get_inode("dir1/name2.ext"))
        self.assertNotEqual(get_inode("dir1/name1.ext"), get_inode("dir3/name1.noext"))
        self.assertNotEqual(get_inode("dir1/name3.ext"), get_inode("dir3/name1.ext"))
        self.assertNotEqual(get_inode("dir1/name1.ext"), get_inode("dir4/name1.ext"))

    def test_hardlink_tree_filenames_equal_reverse_iteration(self):
        """Since os.listdir() can return items in arbitrary order, this test
        confirms that if the iteration over the directories is reversed
        (lexicographically), the --filenames-equal option still works."""

        # This test confirms that the --filenames-equal option works whether
        # dir1/name1.ext or dir2/name1.ext is found first.
        self.remove_file("dir1/link")
        self.make_linked_file("dir2/name1.ext", "dir1/link")

        sys.argv = ["hardlink.py", "--no-stats", "--filenames-equal", self.root]
        hardlink.main()

        self.verify_file_contents()

        self.assertEqual(get_inode("dir1/name1.ext"), get_inode("dir2/name1.ext"))

    def test_hardlink_tree_exclude(self):
        sys.argv = ["hardlink.py", "--no-stats", "--exclude", ".*noext$", self.root]
        hardlink.main()

        self.verify_file_contents()

        self.assertEqual(get_inode("dir1/name1.ext"), get_inode("dir1/name2.ext"))
        self.assertEqual(get_inode("dir1/name1.ext"), get_inode("dir2/name1.ext"))
        self.assertEqual(get_inode("dir1/name3.ext"), get_inode("dir3/name1.ext"))

        self.assertNotEqual(get_inode("dir1/name1.ext"), get_inode("dir3/name1.noext"))
        self.assertNotEqual(get_inode("dir1/name1.ext"), get_inode("dir4/name1.ext"))

    def test_hardlink_tree_timestamp_ignore(self):
        sys.argv = ["hardlink.py", "--no-stats", "--ignore-timestamp", self.root]
        hardlink.main()

        self.verify_file_contents()

        self.assertEqual(get_inode("dir1/name1.ext"), get_inode("dir1/name2.ext"))
        self.assertEqual(get_inode("dir1/name1.ext"), get_inode("dir2/name1.ext"))
        self.assertEqual(get_inode("dir1/name1.ext"), get_inode("dir3/name1.noext"))
        self.assertEqual(get_inode("dir1/name3.ext"), get_inode("dir3/name1.ext"))
        self.assertEqual(get_inode("dir1/name1.ext"), get_inode("dir4/name1.ext"))

        self.assertNotEqual(get_inode("dir1/name3.ext"), get_inode("dir5/name1.ext"))

    def test_hardlink_tree_ignore_permissions(self):
        sys.argv = ["hardlink.py", "--no-stats", "--ignore-permissions", self.root]
        hardlink.main()

        self.verify_file_contents()

        self.assertEqual(get_inode("dir1/name1.ext"), get_inode("dir1/name2.ext"))
        self.assertEqual(get_inode("dir1/name1.ext"), get_inode("dir2/name1.ext"))
        self.assertEqual(get_inode("dir1/name1.ext"), get_inode("dir3/name1.noext"))
        self.assertEqual(get_inode("dir1/name3.ext"), get_inode("dir3/name1.ext"))
        self.assertEqual(get_inode("dir1/name3.ext"), get_inode("dir5/name1.ext"))

        self.assertNotEqual(get_inode("dir1/name1.ext"), get_inode("dir4/name1.ext"))

    def test_hardlink_tree_minsize(self):
        """Set a minimum size larger than the test data, inhibiting linking"""
        sys.argv = ["hardlink.py", "--no-stats", "--min-size",
                    str(len(testdata1) + 1), self.root]
        hardlink.main()

        self.verify_file_contents()

        self.assertEqual(os.lstat("dir1/name1.ext").st_nlink, 2)  # Existing link
        self.assertEqual(os.lstat("dir1/name2.ext").st_nlink, 1)
        self.assertEqual(os.lstat("dir1/name3.ext").st_nlink, 1)
        self.assertEqual(os.lstat("dir2/name1.ext").st_nlink, 1)
        self.assertEqual(os.lstat("dir3/name1.ext").st_nlink, 1)
        self.assertEqual(os.lstat("dir3/name1.noext").st_nlink, 1)
        self.assertEqual(os.lstat("dir4/name1.ext").st_nlink, 1)
        self.assertEqual(get_inode("dir1/name1.ext"), get_inode("dir1/link"))

    def test_hardlink_tree_maxsize(self):
        """Set a maximum size smaller than the test data, inhibiting linking"""
        sys.argv = ["hardlink.py", "--no-stats", "--max-size",
                    str(len(testdata1) - 1), self.root]
        hardlink.main()

        self.verify_file_contents()

        self.assertEqual(os.lstat("dir1/name1.ext").st_nlink, 2)  # Existing link
        self.assertEqual(os.lstat("dir1/name2.ext").st_nlink, 1)
        self.assertEqual(os.lstat("dir1/name3.ext").st_nlink, 1)
        self.assertEqual(os.lstat("dir2/name1.ext").st_nlink, 1)
        self.assertEqual(os.lstat("dir3/name1.ext").st_nlink, 1)
        self.assertEqual(os.lstat("dir3/name1.noext").st_nlink, 1)
        self.assertEqual(os.lstat("dir4/name1.ext").st_nlink, 1)
        self.assertEqual(get_inode("dir1/name1.ext"), get_inode("dir1/link"))

    def test_hardlink_tree_match_extension(self):
        sys.argv = ["hardlink.py", "--no-stats", "--match", "*.ext", self.root]
        hardlink.main()

        self.verify_file_contents()

        self.assertEqual(get_inode("dir1/name1.ext"), get_inode("dir1/name2.ext"))
        self.assertEqual(get_inode("dir1/name1.ext"), get_inode("dir2/name1.ext"))
        self.assertEqual(get_inode("dir1/name3.ext"), get_inode("dir3/name1.ext"))

        self.assertNotEqual(get_inode("dir1/name1.ext"), get_inode("dir3/name1.noext"))
        self.assertNotEqual(get_inode("dir1/name1.ext"), get_inode("dir4/name1.ext"))

    def test_hardlink_tree_match_prefix(self):
        sys.argv = ["hardlink.py", "--no-stats", "--match", "name1*", self.root]
        hardlink.main()

        self.verify_file_contents()

        self.assertEqual(get_inode("dir1/name1.ext"), get_inode("dir2/name1.ext"))
        self.assertEqual(get_inode("dir1/name1.ext"), get_inode("dir3/name1.noext"))

        self.assertNotEqual(get_inode("dir1/name1.ext"), get_inode("dir1/name2.ext"))
        self.assertNotEqual(get_inode("dir1/name3.ext"), get_inode("dir3/name1.ext"))

        # utime mismatch despite name match
        self.assertNotEqual(get_inode("dir1/name1.ext"), get_inode("dir4/name1.ext"))

    def test_hardlink_tree_multiple_matches(self):
        sys.argv = ["hardlink.py", "--no-stats", "-m", "name2*", "-m", "*.noext", self.root]
        hardlink.main()

        self.verify_file_contents()

        self.assertEqual(get_inode("dir1/name2.ext"), get_inode("dir3/name1.noext"))

        self.assertNotEqual(get_inode("dir1/name1.ext"), get_inode("dir1/name2.ext"))
        self.assertNotEqual(get_inode("dir1/name1.ext"), get_inode("dir2/name1.ext"))
        self.assertNotEqual(get_inode("dir1/name1.ext"), get_inode("dir1/name2.ext"))
        self.assertNotEqual(get_inode("dir1/name3.ext"), get_inode("dir3/name1.ext"))

    def test_hardlink_tree_content_only(self):
        sys.argv = ["hardlink.py", "--no-stats", "--content-only", self.root]
        hardlink.main()

        self.verify_file_contents()

        self.assertEqual(get_inode("dir1/name1.ext"), get_inode("dir1/name2.ext"))
        self.assertEqual(get_inode("dir1/name1.ext"), get_inode("dir2/name1.ext"))
        self.assertEqual(get_inode("dir1/name1.ext"), get_inode("dir3/name1.noext"))
        self.assertEqual(get_inode("dir1/name1.ext"), get_inode("dir4/name1.ext"))
        self.assertEqual(get_inode("dir1/name3.ext"), get_inode("dir3/name1.ext"))
        self.assertEqual(get_inode("dir1/name3.ext"), get_inode("dir5/name1.ext"))


@unittest.skip("Max nlinks tests are slow.  Skipping...")
class TestMaxNLinks(BaseTests):
    def setUp(self):
        self.setup_tempdir()
        try:
            self.max_nlinks = os.pathconf(self.root, "PC_LINK_MAX")
        except:
            os.rmdir(self.root)
            raise

        self.make_hardlinkable_file("a", testdata1)
        self.make_hardlinkable_file("b", testdata1)
        for i in range(self.max_nlinks-1):
            filename = "b"+str(i)
            self.make_linked_file("b", filename)

    def tearDown(self):
        self.remove_tempdir()

    def test_hardlink_max_nlinks_at_start(self):
        self.assertEqual(os.lstat("a").st_nlink, 1)
        self.assertEqual(os.lstat("b").st_nlink, self.max_nlinks)

        sys.argv = ["hardlink.py", "--no-stats", "--content-only", self.root]
        hardlink.main()

        self.assertEqual(os.lstat("a").st_nlink, 1)
        self.assertEqual(os.lstat("b").st_nlink, self.max_nlinks)

        # Re-run hardlinker after some changes.  Saves on overhead of
        # destroying and recreating the max_nlinks files.
        self.remove_file("b")
        hardlink.main()

        self.assertEqual(os.lstat("a").st_nlink, self.max_nlinks)
        self.assertEqual(os.lstat("b1").st_nlink, self.max_nlinks)

        self.remove_file("a")
        self.make_hardlinkable_file("a", testdata1)
        self.make_hardlinkable_file("b", testdata1)
        hardlink.main()

        self.assertTrue(os.lstat("a").st_nlink == os.lstat("b").st_nlink or
                        os.lstat("a").st_nlink == self.max_nlinks or
                        os.lstat("b").st_nlink == self.max_nlinks)

        self.remove_file("a")
        self.remove_file("b")
        self.make_hardlinkable_file("b", testdata1)
        hardlink.main()

        num_c_links = 1000
        for i in range(num_c_links):
            filename = "c"+str(i)
            self.make_hardlinkable_file(filename, testdata1)
        # Should link just the c's to each other
        hardlink.main()

        self.assertEqual(os.lstat("b").st_nlink, self.max_nlinks)
        self.assertEqual(os.lstat("c0").st_nlink, num_c_links)


@unittest.skip("Forces filesystem permission errors to test logging and recovery")
class TestErrorLogging(BaseTests):
    def setUp(self):
        self.setup_tempdir()

        for filename in ["a", "b"]:
            self.make_hardlinkable_file(filename, testdata1)

    def test_no_parent_dir_write_permission(self):
        # Remove write permission from tmp root dir, to deliberately cause the
        # rename(), link(), and unlink() functions to fail, and forcing logging
        # output.
        os.chmod(self.root, stat.S_IRUSR | stat.S_IXUSR)

        sys.argv = ["hardlink.py", "--no-stats", self.root]
        # This should log an error message when the rename() fails
        hardlink.main()

        self.assertEqual(os.lstat("a").st_nlink, 1)
        self.assertEqual(os.lstat("b").st_nlink, 1)

        os.chmod(self.root, stat.S_IRWXU)

    def tearDown(self):
        self.remove_tempdir()


if __name__ == '__main__':
    unittest.main()
