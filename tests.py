#!/usr/bin/env python

import os
import os.path
import sys
import tempfile
import time
import unittest

import hardlink

testdata1 = "1234" * 1024 + "abc"
testdata2 = "1234" * 1024 + "xyz"


def get_inode(filename):
    return os.lstat(filename).st_ino


class TestHappy(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.pathnames = [] # Keep track of all files/dirs, for deleting later
        os.chdir(self.root)

        self.testfs = {
            "dir1/name1.ext": testdata1,
            "dir1/name2.ext": testdata1,
            "dir1/name3.ext": testdata2,
            "dir2/name1.ext": testdata1,
            "dir3/name1.ext": testdata2,
            "dir3/name1.noext": testdata1,
            "dir4/name1.ext": testdata1,
        }

        for dir in ("dir1", "dir2", "dir3", "dir4", "dir5"):
            os.mkdir(dir)
            self.pathnames.append(dir)

        for filename, contents in self.testfs.items():
            with open(filename, "w") as f:
                f.write(contents)
                self.pathnames.append(filename)

        now = time.time()
        other = now - 2

        for filename in ("dir1/name1.ext", "dir1/name2.ext", "dir1/name3.ext",
                         "dir2/name1.ext", "dir3/name1.ext", "dir3/name1.noext"):
            os.utime(filename, (now, now))

        os.utime("dir4/name1.ext", (other, other))

        # os.chown("dir5/name1.ext", os.getuid(), ...)
        # -c, --content-only    Only file contents have to match

        os.link("dir1/name1.ext", "dir1/link")
        self.pathnames.append("dir1/link")

        self.verify_file_contents()

    def tearDown(self):
        os.chdir(self.root)
        for pathname in self.pathnames:
            assert not pathname.lstrip().startswith('/')
            if os.path.isfile(pathname):
                os.unlink(pathname)

            if os.path.isdir(pathname):
                try:
                    os.rmdir(pathname)
                except OSError:
                    pass

            if (os.path.dirname(pathname) and
                    os.path.isdir(os.path.dirname(pathname))):
                try:
                    os.rmdir(os.path.dirname(pathname))
                except OSError:
                    pass

        os.rmdir(self.root)

    def verify_file_contents(self):
        for filename, contents in self.testfs.items():
            with open(filename, "r") as f:
                actual = f.read()
                self.assertEqual(actual, contents)

        # Bug?  Should hardlink to the file with most existing links?
        # self.assertEqual(get_inode("dir1/name1.ext"), get_inode("dir1/link"))

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

    def test_hardlink_tree_filenames_equal(self):
        sys.argv = ["hardlink.py", "--no-stats", "--filenames-equal", self.root]
        hardlink.main()

        self.verify_file_contents()

        self.assertNotEqual(get_inode("dir1/name1.ext"), get_inode("dir1/name2.ext"))
        self.assertEqual(get_inode("dir1/name1.ext"), get_inode("dir2/name1.ext"))
        self.assertNotEqual(get_inode("dir1/name1.ext"), get_inode("dir3/name1.noext"))

        self.assertNotEqual(get_inode("dir1/name3.ext"), get_inode("dir3/name1.ext"))

        self.assertNotEqual(get_inode("dir1/name1.ext"), get_inode("dir4/name1.ext"))

    def test_hardlink_tree_filenames_equal_reverse_iteration(self):
        """Since os.listdir() can return items in arbitrary order, this test
        confirms that if the iteration over the directories is reversed
        (lexicographically), the --filenames-equal option still works."""

        # This test confirms that the --filenames-equal option works whether
        # dir1/name1.ext or dir2/name1.ext is found first.
        os.unlink("dir1/link")
        os.link("dir2/name1.ext", "dir1/link")

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
        self.assertNotEqual(get_inode("dir1/name1.ext"), get_inode("dir3/name1.noext"))

        self.assertEqual(get_inode("dir1/name3.ext"), get_inode("dir3/name1.ext"))

        self.assertNotEqual(get_inode("dir1/name1.ext"), get_inode("dir4/name1.ext"))

    def test_hardlink_tree_timestamp_ignore(self):
        sys.argv = ["hardlink.py", "--no-stats", "--timestamp-ignore", self.root]
        hardlink.main()

        self.verify_file_contents()

        self.assertEqual(get_inode("dir1/name1.ext"), get_inode("dir1/name2.ext"))
        self.assertEqual(get_inode("dir1/name1.ext"), get_inode("dir2/name1.ext"))
        self.assertEqual(get_inode("dir1/name1.ext"), get_inode("dir3/name1.noext"))

        self.assertEqual(get_inode("dir1/name3.ext"), get_inode("dir3/name1.ext"))

        self.assertEqual(get_inode("dir1/name1.ext"), get_inode("dir4/name1.ext"))

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
        self.assertNotEqual(get_inode("dir1/name1.ext"), get_inode("dir3/name1.noext"))

        self.assertEqual(get_inode("dir1/name3.ext"), get_inode("dir3/name1.ext"))

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

if __name__ == '__main__':
    unittest.main()
