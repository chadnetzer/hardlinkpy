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

import hardlinkable

testdata0 = ""
testdata1 = "1234" * 1024 + "abc"
testdata2 = "1234" * 1024 + "xyz"
testdata3 = "foo"  # Short so that filesystems may back into inodes


def get_inode(filename):
    return os.lstat(filename).st_ino

class TestModuleFunctions(unittest.TestCase):
    def test_humanize_number(self):
        f = hardlinkable._humanize_number
        self.assertEqual("0 bytes", f(0))
        self.assertEqual("1 bytes", f(1))
        self.assertEqual("1023 bytes", f(1023))
        self.assertEqual("1.000 KiB", f(1024))
        self.assertEqual("1.000 MiB", f(1024**2))
        self.assertEqual("1.000 GiB", f(1024**3))
        self.assertEqual("1.000 TiB", f(1024**4))
        self.assertEqual("1.000 PiB", f(1024**5))

    def test_humanized_number_to_bytes(self):
        f = hardlinkable._humanized_number_to_bytes
        self.assertEqual(0, f("0"))
        self.assertEqual(1, f("1"))
        self.assertEqual(1023, f("1023"))
        self.assertEqual(1024, f("1024"))
        self.assertEqual(1024, f("1k"))
        self.assertEqual(1024, f("1K"))
        self.assertEqual(2048, f("2k"))
        self.assertEqual(1023*1024, f("1023k"))
        self.assertEqual(1024**2, f("1m"))
        self.assertEqual(1024**2, f("1M"))
        self.assertEqual(1024**3, f("1g"))
        self.assertEqual(1024**4, f("1t"))
        self.assertEqual(1024**5, f("1p"))

        self.assertRaises(ValueError, f, "")
        self.assertRaises(ValueError, f, "1kk")
        self.assertRaises(ValueError, f, "1j")
        self.assertRaises(ValueError, f, "k")


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
                except OSError:
                    error = sys.exc_info()[1]
                    if error.errno == errno.EEXIST and os.path.isdir(dirname):
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
        self.make_hardlinkable_file("dir6/name1.ext", testdata3)
        self.make_hardlinkable_file("dir6/name2.ext", testdata3)

        now = time.time()
        other = now - 2

        for filename in ("dir1/name1.ext", "dir1/name2.ext", "dir1/name3.ext",
                         "dir2/name1.ext", "dir3/name1.ext", "dir3/name1.noext",
                         "dir5/name1.ext", "dir6/name1.ext", "dir6/name2.ext",
                         ):
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
        sys.argv = ["hardlinkable.py", "-q", self.root]
        hardlinkable.main()

        self.verify_file_contents()

        self.assertEqual(os.lstat("dir1/name1.ext").st_nlink, 2)  # Existing link
        self.assertEqual(os.lstat("dir1/name2.ext").st_nlink, 1)
        self.assertEqual(os.lstat("dir1/name3.ext").st_nlink, 1)
        self.assertEqual(os.lstat("dir2/name1.ext").st_nlink, 1)
        self.assertEqual(os.lstat("dir3/name1.ext").st_nlink, 1)
        self.assertEqual(os.lstat("dir3/name1.noext").st_nlink, 1)
        self.assertEqual(os.lstat("dir4/name1.ext").st_nlink, 1)
        self.assertEqual(os.lstat("dir5/name1.ext").st_nlink, 1)
        self.assertEqual(os.lstat("dir6/name1.ext").st_nlink, 1)

    def test_hardlink_tree(self):
        sys.argv = ["hardlinkable.py", "--enable-linking", "-q", self.root]
        hardlinkable.main()

        self.verify_file_contents()

        self.assertEqual(get_inode("dir1/name1.ext"), get_inode("dir1/name2.ext"))
        self.assertEqual(get_inode("dir1/name1.ext"), get_inode("dir2/name1.ext"))
        self.assertEqual(get_inode("dir1/name1.ext"), get_inode("dir3/name1.noext"))
        self.assertEqual(get_inode("dir1/name3.ext"), get_inode("dir3/name1.ext"))
        self.assertEqual(get_inode("dir6/name1.ext"), get_inode("dir6/name2.ext"))
        self.assertEqual(os.lstat("dir4/name1.ext").st_nlink, 1)
        self.assertEqual(os.lstat("dir5/name1.ext").st_nlink, 1)

        self.assertNotEqual(get_inode("dir1/name1.ext"), get_inode("dir4/name1.ext"))
        self.assertNotEqual(get_inode("dir1/name3.ext"), get_inode("dir5/name1.ext"))

    def test_hardlink_multiple_dir_args(self):
        sys.argv = ["hardlinkable.py", "--enable-linking", "-q",
                os.path.join(self.root, 'dir1'),
                os.path.join(self.root, 'dir2'),
                ]
        hardlinkable.main()

        # remove unused directories from content check dictionary
        for pathname in self.file_contents.copy():
            if (pathname.startswith('dir1') or pathname.startswith('dir2')):
                continue
            else:
                del self.file_contents[pathname]

        self.verify_file_contents()

        self.assertEqual(get_inode("dir1/name1.ext"), get_inode("dir1/name2.ext"))
        self.assertEqual(get_inode("dir1/name1.ext"), get_inode("dir2/name1.ext"))

    def test_hardlink_tree_filenames_equal(self):
        sys.argv = ["hardlinkable.py", "--enable-linking", "-q", "--same-name", self.root]
        hardlinkable.main()

        self.verify_file_contents()

        self.assertEqual(get_inode("dir1/name1.ext"), get_inode("dir2/name1.ext"))

        self.assertNotEqual(get_inode("dir1/name1.ext"), get_inode("dir1/name2.ext"))
        self.assertNotEqual(get_inode("dir1/name1.ext"), get_inode("dir3/name1.noext"))
        self.assertNotEqual(get_inode("dir1/name3.ext"), get_inode("dir3/name1.ext"))
        self.assertNotEqual(get_inode("dir1/name1.ext"), get_inode("dir4/name1.ext"))
        self.assertNotEqual(get_inode("dir6/name1.ext"), get_inode("dir6/name2.ext"))
        self.assertEqual(os.lstat("dir6/name1.ext").st_nlink, 1)
        self.assertEqual(os.lstat("dir6/name2.ext").st_nlink, 1)

    def test_hardlink_tree_filenames_equal_reverse_iteration(self):
        """Since os.listdir() can return items in arbitrary order, this test
        confirms that if the iteration over the directories is reversed
        (lexicographically), the --same-name option still works."""

        # This test confirms that the --same-name option works whether
        # dir1/name1.ext or dir2/name1.ext is found first.
        self.remove_file("dir1/link")
        self.make_linked_file("dir2/name1.ext", "dir1/link")

        sys.argv = ["hardlinkable.py", "--enable-linking", "-q", "--same-name", self.root]
        hardlinkable.main()

        self.verify_file_contents()

        self.assertEqual(get_inode("dir1/name1.ext"), get_inode("dir2/name1.ext"))

    def test_hardlink_tree_exclude(self):
        sys.argv = ["hardlinkable.py", "--enable-linking", "-q", "--exclude", ".*noext$", self.root]
        hardlinkable.main()

        self.verify_file_contents()

        self.assertEqual(get_inode("dir1/name1.ext"), get_inode("dir1/name2.ext"))
        self.assertEqual(get_inode("dir1/name1.ext"), get_inode("dir2/name1.ext"))
        self.assertEqual(get_inode("dir1/name3.ext"), get_inode("dir3/name1.ext"))
        self.assertEqual(get_inode("dir6/name1.ext"), get_inode("dir6/name2.ext"))

        self.assertNotEqual(get_inode("dir1/name1.ext"), get_inode("dir3/name1.noext"))
        self.assertNotEqual(get_inode("dir1/name1.ext"), get_inode("dir4/name1.ext"))

    def test_hardlink_tree_timestamp_ignore(self):
        sys.argv = ["hardlinkable.py", "--enable-linking", "-q", "--ignore-time", self.root]
        hardlinkable.main()

        self.verify_file_contents()

        self.assertEqual(get_inode("dir1/name1.ext"), get_inode("dir1/name2.ext"))
        self.assertEqual(get_inode("dir1/name1.ext"), get_inode("dir2/name1.ext"))
        self.assertEqual(get_inode("dir1/name1.ext"), get_inode("dir3/name1.noext"))
        self.assertEqual(get_inode("dir1/name3.ext"), get_inode("dir3/name1.ext"))
        self.assertEqual(get_inode("dir1/name1.ext"), get_inode("dir4/name1.ext"))
        self.assertEqual(get_inode("dir6/name1.ext"), get_inode("dir6/name2.ext"))

        self.assertNotEqual(get_inode("dir1/name3.ext"), get_inode("dir5/name1.ext"))

    def test_hardlink_tree_ignore_permissions(self):
        sys.argv = ["hardlinkable.py", "--enable-linking", "-q", "--ignore-perms", self.root]
        hardlinkable.main()

        self.verify_file_contents()

        self.assertEqual(get_inode("dir1/name1.ext"), get_inode("dir1/name2.ext"))
        self.assertEqual(get_inode("dir1/name1.ext"), get_inode("dir2/name1.ext"))
        self.assertEqual(get_inode("dir1/name1.ext"), get_inode("dir3/name1.noext"))
        self.assertEqual(get_inode("dir1/name3.ext"), get_inode("dir3/name1.ext"))
        self.assertEqual(get_inode("dir1/name3.ext"), get_inode("dir5/name1.ext"))
        self.assertEqual(get_inode("dir6/name1.ext"), get_inode("dir6/name2.ext"))

        self.assertNotEqual(get_inode("dir1/name1.ext"), get_inode("dir4/name1.ext"))

    def test_hardlink_tree_match_extension(self):
        sys.argv = ["hardlinkable.py", "--enable-linking", "-q", "--match", "*.ext", self.root]
        hardlinkable.main()

        self.verify_file_contents()

        self.assertEqual(get_inode("dir1/name1.ext"), get_inode("dir1/name2.ext"))
        self.assertEqual(get_inode("dir1/name1.ext"), get_inode("dir2/name1.ext"))
        self.assertEqual(get_inode("dir1/name3.ext"), get_inode("dir3/name1.ext"))
        self.assertEqual(get_inode("dir6/name1.ext"), get_inode("dir6/name2.ext"))

        self.assertNotEqual(get_inode("dir1/name1.ext"), get_inode("dir3/name1.noext"))
        self.assertNotEqual(get_inode("dir1/name1.ext"), get_inode("dir4/name1.ext"))

    def test_hardlink_tree_match_prefix(self):
        sys.argv = ["hardlinkable.py", "--enable-linking", "-q", "--match", "name1*", self.root]
        hardlinkable.main()

        self.verify_file_contents()

        self.assertEqual(get_inode("dir1/name1.ext"), get_inode("dir2/name1.ext"))
        self.assertEqual(get_inode("dir1/name1.ext"), get_inode("dir3/name1.noext"))

        self.assertNotEqual(get_inode("dir1/name1.ext"), get_inode("dir1/name2.ext"))
        self.assertNotEqual(get_inode("dir1/name3.ext"), get_inode("dir3/name1.ext"))
        self.assertNotEqual(get_inode("dir6/name1.ext"), get_inode("dir6/name2.ext"))

        # utime mismatch despite name match
        self.assertNotEqual(get_inode("dir1/name1.ext"), get_inode("dir4/name1.ext"))

    def test_hardlink_tree_multiple_matches(self):
        sys.argv = ["hardlinkable.py", "--enable-linking", "-q", "-m", "name2*", "-m", "*.noext", self.root]
        hardlinkable.main()

        self.verify_file_contents()

        self.assertEqual(get_inode("dir1/name2.ext"), get_inode("dir3/name1.noext"))

        self.assertNotEqual(get_inode("dir1/name1.ext"), get_inode("dir1/name2.ext"))
        self.assertNotEqual(get_inode("dir1/name1.ext"), get_inode("dir2/name1.ext"))
        self.assertNotEqual(get_inode("dir1/name1.ext"), get_inode("dir1/name2.ext"))
        self.assertNotEqual(get_inode("dir1/name3.ext"), get_inode("dir3/name1.ext"))
        self.assertNotEqual(get_inode("dir6/name1.ext"), get_inode("dir6/name2.ext"))

    def test_hardlink_tree_content_only(self):
        sys.argv = ["hardlinkable.py", "--enable-linking", "-q", "--content-only", self.root]
        hardlinkable.main()

        self.verify_file_contents()

        self.assertEqual(get_inode("dir1/name1.ext"), get_inode("dir1/name2.ext"))
        self.assertEqual(get_inode("dir1/name1.ext"), get_inode("dir2/name1.ext"))
        self.assertEqual(get_inode("dir1/name1.ext"), get_inode("dir3/name1.noext"))
        self.assertEqual(get_inode("dir1/name1.ext"), get_inode("dir4/name1.ext"))
        self.assertEqual(get_inode("dir1/name3.ext"), get_inode("dir3/name1.ext"))
        self.assertEqual(get_inode("dir1/name3.ext"), get_inode("dir5/name1.ext"))
        self.assertEqual(get_inode("dir6/name1.ext"), get_inode("dir6/name2.ext"))
        self.assertEqual(os.lstat("dir6/name1.ext").st_nlink, 2)
        self.assertEqual(os.lstat("dir6/name2.ext").st_nlink, 2)


class TestMinMaxSize(BaseTests):
    def setUp(self):
        self.setup_tempdir()

        self.make_hardlinkable_file("zero_len_1", testdata0)
        self.make_hardlinkable_file("zero_len_2", testdata0)
        self.make_hardlinkable_file("zero_len_3", testdata0)

        self.make_hardlinkable_file("small_file_1", testdata3)
        self.make_hardlinkable_file("small_file_2", testdata3)
        self.make_hardlinkable_file("small_file_3", testdata3)

        self.make_hardlinkable_file("a1", testdata1)
        self.make_hardlinkable_file("b1", testdata1)
        self.make_hardlinkable_file("c1", testdata1)
        self.make_hardlinkable_file("a2", testdata2)
        self.make_hardlinkable_file("b2", testdata2)
        self.make_hardlinkable_file("c2", testdata2)

        self.verify_file_contents()

        self.max_datasize = max(len(testdata0),
                                len(testdata1),
                                len(testdata2),
                                len(testdata3))

        self.min_datasize = min(len(testdata1),
                                len(testdata2),
                                len(testdata3))

    def tearDown(self):
        self.remove_tempdir()

    def test_hardlink_tree_smaller_than_minsize(self):
        """Set a minimum size larger than the test data, inhibiting linking"""
        sys.argv = ["hardlinkable.py", "--enable-linking", "-q", "-c",
                    "--min-size", str(self.max_datasize + 1), self.root]
        hardlinkable.main()

        self.verify_file_contents()

        self.assertEqual(os.lstat("zero_len_1").st_nlink, 1)
        self.assertEqual(os.lstat("zero_len_2").st_nlink, 1)
        self.assertEqual(os.lstat("zero_len_3").st_nlink, 1)

        self.assertEqual(os.lstat("small_file_1").st_nlink, 1)
        self.assertEqual(os.lstat("small_file_2").st_nlink, 1)
        self.assertEqual(os.lstat("small_file_3").st_nlink, 1)

        self.assertEqual(os.lstat("a1").st_nlink, 1)
        self.assertEqual(os.lstat("b1").st_nlink, 1)
        self.assertEqual(os.lstat("c1").st_nlink, 1)
        self.assertEqual(os.lstat("a2").st_nlink, 1)
        self.assertEqual(os.lstat("b2").st_nlink, 1)
        self.assertEqual(os.lstat("c2").st_nlink, 1)

    def test_hardlink_tree_default_minsize(self):
        """By default, length zero files aren't hardlinked."""
        sys.argv = ["hardlinkable.py", "--enable-linking", "-q", "-c", self.root]
        hardlinkable.main()

        self.verify_file_contents()

        self.assertEqual(os.lstat("zero_len_1").st_nlink, 1)
        self.assertEqual(os.lstat("zero_len_2").st_nlink, 1)
        self.assertEqual(os.lstat("zero_len_3").st_nlink, 1)

        self.assertEqual(os.lstat("small_file_1").st_nlink, 3)
        self.assertEqual(os.lstat("small_file_2").st_nlink, 3)
        self.assertEqual(os.lstat("small_file_3").st_nlink, 3)
        self.assertEqual(get_inode("small_file_1"), get_inode("small_file_2"))
        self.assertEqual(get_inode("small_file_1"), get_inode("small_file_3"))

        self.assertEqual(os.lstat("a1").st_nlink, 3)
        self.assertEqual(os.lstat("b1").st_nlink, 3)
        self.assertEqual(os.lstat("c1").st_nlink, 3)
        self.assertEqual(os.lstat("a2").st_nlink, 3)
        self.assertEqual(os.lstat("b2").st_nlink, 3)
        self.assertEqual(os.lstat("c2").st_nlink, 3)
        self.assertEqual(get_inode("a1"), get_inode("b1"))
        self.assertEqual(get_inode("a1"), get_inode("c1"))
        self.assertEqual(get_inode("a2"), get_inode("b2"))
        self.assertEqual(get_inode("a2"), get_inode("c2"))
        self.assertNotEqual(get_inode("a1"), get_inode("a2"))

    def test_hardlink_tree_zero_minsize(self):
        sys.argv = ["hardlinkable.py", "--enable-linking", "-q", "-c",
                    "--min-size", "0", self.root]
        hardlinkable.main()

        self.verify_file_contents()

        self.assertEqual(os.lstat("zero_len_1").st_nlink, 3)
        self.assertEqual(os.lstat("zero_len_2").st_nlink, 3)
        self.assertEqual(os.lstat("zero_len_3").st_nlink, 3)
        self.assertEqual(get_inode("zero_len_1"), get_inode("zero_len_2"))
        self.assertEqual(get_inode("zero_len_1"), get_inode("zero_len_3"))

        self.assertEqual(os.lstat("small_file_1").st_nlink, 3)
        self.assertEqual(os.lstat("small_file_2").st_nlink, 3)
        self.assertEqual(os.lstat("small_file_3").st_nlink, 3)
        self.assertEqual(get_inode("small_file_1"), get_inode("small_file_2"))
        self.assertEqual(get_inode("small_file_1"), get_inode("small_file_3"))

        self.assertEqual(os.lstat("a1").st_nlink, 3)
        self.assertEqual(os.lstat("b1").st_nlink, 3)
        self.assertEqual(os.lstat("c1").st_nlink, 3)
        self.assertEqual(os.lstat("a2").st_nlink, 3)
        self.assertEqual(os.lstat("b2").st_nlink, 3)
        self.assertEqual(os.lstat("c2").st_nlink, 3)
        self.assertEqual(get_inode("a1"), get_inode("b1"))
        self.assertEqual(get_inode("a1"), get_inode("c1"))
        self.assertEqual(get_inode("a2"), get_inode("b2"))
        self.assertEqual(get_inode("a2"), get_inode("c2"))
        self.assertNotEqual(get_inode("a1"), get_inode("a2"))

    def test_hardlink_tree_larger_than_maxsize(self):
        """Set a minimum size larger than the test data, inhibiting linking (zero excluded)"""
        sys.argv = ["hardlinkable.py", "--enable-linking", "-q", "-c",
                    "--max-size", str(self.min_datasize - 1), self.root]

        hardlinkable.main()

        self.verify_file_contents()

        self.assertEqual(os.lstat("zero_len_1").st_nlink, 1)
        self.assertEqual(os.lstat("zero_len_2").st_nlink, 1)
        self.assertEqual(os.lstat("zero_len_3").st_nlink, 1)

        self.assertEqual(os.lstat("small_file_1").st_nlink, 1)
        self.assertEqual(os.lstat("small_file_2").st_nlink, 1)
        self.assertEqual(os.lstat("small_file_3").st_nlink, 1)

        self.assertEqual(os.lstat("a1").st_nlink, 1)
        self.assertEqual(os.lstat("b1").st_nlink, 1)
        self.assertEqual(os.lstat("c1").st_nlink, 1)
        self.assertEqual(os.lstat("a2").st_nlink, 1)
        self.assertEqual(os.lstat("b2").st_nlink, 1)
        self.assertEqual(os.lstat("c2").st_nlink, 1)

    def test_hardlink_tree_zero_minsize(self):
        sys.argv = ["hardlinkable.py", "--enable-linking", "-q", "-c",
                    "--max-size", "0", "--min-size", "0", self.root]
        hardlinkable.main()

        self.verify_file_contents()

        self.assertEqual(os.lstat("zero_len_1").st_nlink, 3)
        self.assertEqual(os.lstat("zero_len_2").st_nlink, 3)
        self.assertEqual(os.lstat("zero_len_3").st_nlink, 3)
        self.assertEqual(get_inode("zero_len_1"), get_inode("zero_len_2"))
        self.assertEqual(get_inode("zero_len_1"), get_inode("zero_len_3"))

        self.assertEqual(os.lstat("small_file_1").st_nlink, 1)
        self.assertEqual(os.lstat("small_file_2").st_nlink, 1)
        self.assertEqual(os.lstat("small_file_3").st_nlink, 1)

        self.assertEqual(os.lstat("a1").st_nlink, 1)
        self.assertEqual(os.lstat("b1").st_nlink, 1)
        self.assertEqual(os.lstat("c1").st_nlink, 1)
        self.assertEqual(os.lstat("a2").st_nlink, 1)
        self.assertEqual(os.lstat("b2").st_nlink, 1)
        self.assertEqual(os.lstat("c2").st_nlink, 1)

    def test_hardlink_tree_minsize_maxsize_excluding_all_files(self):
        """Test using both min and max size restrictions"""
        sys.argv = ["hardlinkable.py", "--enable-linking", "-q",
                    "--min-size", str(len(testdata3) + 1),
                    "--max-size", str(len(testdata1) - 1),
                    self.root]
        hardlinkable.main()

        self.verify_file_contents()

        self.assertEqual(os.lstat("zero_len_1").st_nlink, 1)
        self.assertEqual(os.lstat("zero_len_2").st_nlink, 1)
        self.assertEqual(os.lstat("zero_len_3").st_nlink, 1)

        self.assertEqual(os.lstat("small_file_1").st_nlink, 1)
        self.assertEqual(os.lstat("small_file_2").st_nlink, 1)
        self.assertEqual(os.lstat("small_file_3").st_nlink, 1)

        self.assertEqual(os.lstat("a1").st_nlink, 1)
        self.assertEqual(os.lstat("b1").st_nlink, 1)
        self.assertEqual(os.lstat("c1").st_nlink, 1)
        self.assertEqual(os.lstat("a2").st_nlink, 1)
        self.assertEqual(os.lstat("b2").st_nlink, 1)
        self.assertEqual(os.lstat("c2").st_nlink, 1)

    def test_hardlink_tree_minsize_maxsize_equal(self):
        """Test equal max and min size restrictions"""
        sys.argv = ["hardlinkable.py", "--enable-linking", "-q",
                    "--min-size", str(len(testdata3)),
                    "--max-size", str(len(testdata3)),
                    self.root]
        hardlinkable.main()

        self.verify_file_contents()

        self.assertEqual(os.lstat("zero_len_1").st_nlink, 1)
        self.assertEqual(os.lstat("zero_len_2").st_nlink, 1)
        self.assertEqual(os.lstat("zero_len_3").st_nlink, 1)

        self.assertEqual(os.lstat("small_file_1").st_nlink, 3)
        self.assertEqual(os.lstat("small_file_2").st_nlink, 3)
        self.assertEqual(os.lstat("small_file_3").st_nlink, 3)
        self.assertEqual(get_inode("small_file_1"), get_inode("small_file_2"))
        self.assertEqual(get_inode("small_file_1"), get_inode("small_file_3"))

        self.assertEqual(os.lstat("a1").st_nlink, 1)
        self.assertEqual(os.lstat("b1").st_nlink, 1)
        self.assertEqual(os.lstat("c1").st_nlink, 1)
        self.assertEqual(os.lstat("a2").st_nlink, 1)
        self.assertEqual(os.lstat("b2").st_nlink, 1)
        self.assertEqual(os.lstat("c2").st_nlink, 1)


@unittest.skip("Max nlinks tests are slow.  Skipping...")
class TestMaxNLinks(BaseTests):
    def setUp(self):
        self.setup_tempdir()
        try:
            self.max_nlinks = os.pathconf(self.root, "PC_LINK_MAX")
        except:
            os.rmdir(self.root)
            raise

        # Start off with an amount of "b"-prefixed files 1-greater than the max
        # nlinks.
        self.make_hardlinkable_file("b", testdata3)
        for i in range(self.max_nlinks):
            filename = "b"+str(i)
            self.make_hardlinkable_file(filename, testdata3)

    def tearDown(self):
        self.remove_tempdir()

    def test_hardlink_max_nlinks_at_start(self):
        # Note that we re-run the hardlinker multiple times after making some
        # changes.  Saves on overhead of destroying and recreating the
        # max_nlinks files.  But makes tests very sensitive to ordering and
        # edits.
        sys.argv = ["hardlinkable.py", "--enable-linking", "-q", "--content-only", self.root]
        hardlinkable.main()

        # Since the directory traversal can occur in arbitrary order, we test
        # the final st_nlink counts regardless of which files they are.
        #
        # Confirm that all but one of the identical 'b' files were linked
        # together.
        count_list = list(self.count_nlinks().values())
        self.assertTrue(set(count_list) == set([1, self.max_nlinks]))

        # There should be only one inode with an nlink count of 1 (ie. a
        # cluster, and a leftover)
        N_1 = len(self.find_nlinks(1))
        N_max_nlinks = len(self.find_nlinks(self.max_nlinks))
        self.assertEqual(N_1, 1)
        self.assertEqual(N_max_nlinks, self.max_nlinks)

        # Make a new 'a' file, and confirm it gets linked to the leftover file
        # (which could be any of the original 'b' files)
        self.make_hardlinkable_file("a", testdata3)
        hardlinkable.main()

        self.assertEqual(len(self.find_nlinks(2)), 2)
        self.assertEqual(os.lstat("a").st_nlink, 2)

        # Remove 'a' and two of the 'b' files and consolidate any leftovers.
        self.remove_file("a")
        self.remove_file("b")
        self.remove_file("b1")
        hardlinkable.main()

        self.assertEqual(len(self.find_nlinks(1)), 0)
        self.assertEqual(len(self.find_nlinks(2)), 0)
        self.assertEqual(len(self.find_nlinks(self.max_nlinks)), 0)

        # Now make an 'a' that should be linked to the remaining files as a
        # cluster (at max link count)
        self.make_hardlinkable_file("a", testdata3)
        hardlinkable.main()

        self.assertEqual(os.lstat("a").st_nlink, self.max_nlinks)
        self.assertEqual(len(self.find_nlinks(1)), 0)
        self.assertEqual(len(self.find_nlinks(2)), 0)

        # Make two new files which may be linked to the max_nlinks cluster, or
        # to each other.
        self.remove_file("a")
        self.make_hardlinkable_file("b", testdata3)
        self.make_hardlinkable_file("c", testdata3)
        hardlinkable.main()

        self.assertTrue(os.lstat("b").st_nlink in [1, 2, self.max_nlinks])
        self.assertTrue(os.lstat("c").st_nlink in [1, 2, self.max_nlinks])
        if os.lstat("b") == 1:
            self.assertEqual(os.lstat("c").st_nlink, self.max_nlinks)
        if os.lstat("c") == 1:
            self.assertEqual(os.lstat("b").st_nlink, self.max_nlinks)
        if os.lstat("b") == 2:
            self.assertEqual(os.lstat("c").st_nlink, 2)

        # Remove our work files, and make a "b" that will link up with the
        # cluster and maximize the nlink count again
        self.remove_file("b")
        self.remove_file("c")
        hardlinkable.main()
        self.make_hardlinkable_file("b", testdata3)
        hardlinkable.main()

        self.assertEqual(os.lstat("b").st_nlink, self.max_nlinks)
        self.assertEqual(len(self.find_nlinks(1)), 0)
        self.assertEqual(len(self.find_nlinks(2)), 0)

        # Make a bunch of new files, which should all link together (since 'b'
        # cluster is full)
        num_c_links = 1000
        for i in range(num_c_links):
            filename = "c"+str(i)
            self.make_hardlinkable_file(filename, testdata3)
        # Should link just the c's to each other
        hardlinkable.main()

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

        sys.argv = ["hardlinkable.py", "--enable-linking", "-q", self.root]
        # This should log an error message when the rename() fails (check
        # buffering option to unittests is set to False)
        hardlinkable.main()

        self.assertEqual(os.lstat("a").st_nlink, 1)
        self.assertEqual(os.lstat("b").st_nlink, 1)

        os.chmod(self.root, stat.S_IRWXU)

    def tearDown(self):
        self.remove_tempdir()


@unittest.skip("Differing device tests require manual setup")
class TestDifferentDevices(BaseTests):
    def setUp(self):
        self.dev1_root = None
        self.dev2_root = None

        # These two variable need to be set to point to directories on
        # different devices (ie. different filesystems).  Best to make empty
        # 'tmp' directories on each device, and put their full paths here.
        DEVICE1_DIR_PATH = None  # requires manual setup
        DEVICE2_DIR_PATH = None  # set to a different filesystem than above

        assert DEVICE1_DIR_PATH
        assert DEVICE2_DIR_PATH

        self.dev1_root = tempfile.mkdtemp(dir=DEVICE1_DIR_PATH)
        self.dev2_root = tempfile.mkdtemp(dir=DEVICE2_DIR_PATH)

        # Provide pre-made paths to the test filenames
        self.path_a = os.path.join(self.dev1_root, 'a')
        self.path_b = os.path.join(self.dev1_root, 'b')
        self.path_c = os.path.join(self.dev2_root, 'c')
        self.path_d = os.path.join(self.dev2_root, 'd')

        # Helper functions require file_contents dict
        self.file_contents = {}

    def tearDown(self):
        if self.dev1_root is not None:
            os.unlink(self.path_a)
            os.unlink(self.path_b)
            os.rmdir(self.dev1_root)
        if self.dev2_root is not None:
            os.unlink(self.path_c)
            os.unlink(self.path_d)
            os.rmdir(self.dev2_root)

    def test_differing_devices_no_link(self):
        assert self.dev1_root is not None
        assert self.dev2_root is not None

        os.chdir(self.dev1_root)
        self.make_hardlinkable_file('a', testdata3)
        self.make_hardlinkable_file('b', testdata1)
        os.chdir(self.dev2_root)
        self.make_hardlinkable_file('c', testdata3)
        self.make_hardlinkable_file('d', testdata1)

        stat_a = os.lstat(self.path_a)
        stat_b = os.lstat(self.path_b)
        stat_c = os.lstat(self.path_c)
        stat_d = os.lstat(self.path_d)

        self.assertEqual(stat_a.st_nlink, 1)
        self.assertEqual(stat_b.st_nlink, 1)
        self.assertEqual(stat_c.st_nlink, 1)
        self.assertEqual(stat_d.st_nlink, 1)

        sys.argv = ["hardlinkable.py", "--enable-linking", "-q", "--content-only",
                    self.dev1_root, self.dev2_root,
                    ]
        hardlinkable.main()

        # Ideally we would be able to check the statistics directly to ensure
        # that no links were attempted.
        #
        # Basically just ensure that the files haven't been deleted, and that
        # the program didn't crash.
        #
        # The atimes of the files should be the same, since the files
        # themselves shouldn't be read (it should skip comparing content by the
        # device equality check).  So we can compare before and after stat
        # values.
        self.assertEqual(stat_a, os.lstat(self.path_a))
        self.assertEqual(stat_b, os.lstat(self.path_b))
        self.assertEqual(stat_c, os.lstat(self.path_c))
        self.assertEqual(stat_d, os.lstat(self.path_d))


    def test_differing_devices_with_link(self):
        assert self.dev1_root is not None
        assert self.dev2_root is not None

        os.chdir(self.dev1_root)
        self.make_hardlinkable_file('a', testdata1)
        self.make_hardlinkable_file('b', testdata1)
        os.chdir(self.dev2_root)
        self.make_hardlinkable_file('c', testdata2)
        self.make_hardlinkable_file('d', testdata2)

        sys.argv = ["hardlinkable.py", "--enable-linking", "-q", "--content-only",
                    self.dev1_root, self.dev2_root,
                    ]
        hardlinkable.main()

        # Check that linking on the same devices occurred
        self.assertEqual(get_inode(self.path_a), get_inode(self.path_b))
        self.assertEqual(get_inode(self.path_c), get_inode(self.path_d))

        # And that no cross-device migration occurred
        self.assertNotEqual(os.lstat(self.path_a).st_dev, os.lstat(self.path_c).st_dev)
        self.assertNotEqual(os.lstat(self.path_b).st_dev, os.lstat(self.path_d).st_dev)

        # And that content is correct
        f1 = open(self.path_a)
        f2 = open(self.path_b)
        f3 = open(self.path_c)
        f4 = open(self.path_d)

        self.assertEqual(f1.read(), testdata1)
        self.assertEqual(f2.read(), testdata1)
        self.assertEqual(f3.read(), testdata2)
        self.assertEqual(f4.read(), testdata2)

        f1.close()
        f2.close()
        f3.close()
        f4.close()


class TestNLinkOrderBug(BaseTests):
    """A proposed solution to the 'clustering' issue (where an inode with a
    high number of links has each individual link deleted and recreated) was to
    order the link() so that the destination always has the lowest number of
    links.  However, this means that filenames that have already been
    processed, can have their inodes orphaned, since we only visit each path
    once.
    """
    def setUp(self):
        self.setup_tempdir()

    def test_missed_link_opportunity(self):
        # Create 3 clusters
        self.make_hardlinkable_file("a", testdata3)
        self.make_linked_file("a", "b")
        self.make_hardlinkable_file("m", testdata3)
        self.make_linked_file("m", "n")
        self.make_linked_file("m", "o")
        self.make_hardlinkable_file("z", testdata3)
        self.make_linked_file("z", "y")
        self.make_linked_file("z", "x")

        self.assertEqual(os.lstat('a').st_nlink, 2)
        self.assertEqual(os.lstat('b').st_nlink, 2)
        self.assertEqual(os.lstat('m').st_nlink, 3)
        self.assertEqual(os.lstat('n').st_nlink, 3)
        self.assertEqual(os.lstat('o').st_nlink, 3)
        self.assertEqual(os.lstat('x').st_nlink, 3)
        self.assertEqual(os.lstat('y').st_nlink, 3)
        self.assertEqual(os.lstat('z').st_nlink, 3)

        sys.argv = ["hardlinkable.py", "--enable-linking", "-q", "--content-only", self.root]
        hardlinkable.main()

        self.verify_file_contents()

        # The algorithm should be able to link all these files together
        # However, if the walk proceeds alphabetically, and the link() source
        # is always the higher than the destination nlink, the 'a' or 'b' file
        # can get orphaned, because it isn't re-scanned.
        self.assertEqual(os.lstat('a').st_nlink, 8)

    def test_missed_link_opportunity_reverse_order(self):
        """Same idea as the other test, but with the initial links set to
        exhibit the problem if the directory entries are traversed in reverse
        alphabetical order."""
        # Create 3 clusters
        self.make_hardlinkable_file("a", testdata3)
        self.make_linked_file("a", "b")
        self.make_linked_file("a", "c")
        self.make_hardlinkable_file("m", testdata3)
        self.make_linked_file("m", "n")
        self.make_linked_file("m", "o")
        self.make_hardlinkable_file("z", testdata3)
        self.make_linked_file("z", "y")

        self.assertEqual(os.lstat('a').st_nlink, 3)
        self.assertEqual(os.lstat('b').st_nlink, 3)
        self.assertEqual(os.lstat('c').st_nlink, 3)
        self.assertEqual(os.lstat('m').st_nlink, 3)
        self.assertEqual(os.lstat('n').st_nlink, 3)
        self.assertEqual(os.lstat('o').st_nlink, 3)
        self.assertEqual(os.lstat('y').st_nlink, 2)
        self.assertEqual(os.lstat('z').st_nlink, 2)

        sys.argv = ["hardlinkable.py", "--enable-linking", "-q", "--content-only", self.root]
        hardlinkable.main()

        self.verify_file_contents()

        # The algorithm should be able to link all these files together
        # However, if the walk proceeds alphabetically, and the link() source
        # is always the higher than the destination nlink, the 'a' or 'b' file
        # can get orphaned, because it isn't re-scanned.
        self.assertEqual(os.lstat('a').st_nlink, 8)


if __name__ == '__main__':
    # Although the program currently runs on older Python 2, the test suite
    # doesn't work that far back.
    assert sys.version_info >= (2,7), "Running tests requires at least Python 2.7+"

    unittest.main(buffer=True)
