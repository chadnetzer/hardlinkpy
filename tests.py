#!/usr/bin/env python

import os
import os.path
import stat
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
            "dir5/name1.ext": testdata2,
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

        # -c, --content-only    Only file contents have to match
        # It's possible for a umask setting of 0466 or 0577 to confuse the
        # tests that rely on this file's chmod value.
        os.chmod("dir5/name1.ext", stat.S_IRUSR)

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
        self.assertNotEqual(get_inode("dir1/name3.ext"), get_inode("dir5/name1.ext"))

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

        self.assertNotEqual(get_inode("dir1/name3.ext"), get_inode("dir5/name1.ext"))

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
class TestMaxNLinks(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        os.chdir(self.root)
        try:
            max_nlinks = os.pathconf(self.root, "PC_LINK_MAX")
        except:
            os.rmdir(self.root)
            raise

        self.max_nlinks = max_nlinks
        self.filenames = []

        self.make_hardlinkable_file("a")
        self.make_hardlinkable_file("b")
        for i in range(max_nlinks-1):
            filename = "b"+str(i)
            self.make_linked_file("b", filename)

    def tearDown(self):
        os.chdir(self.root)
        for filename in self.filenames:
            os.unlink(filename)
        os.rmdir(self.root)

    def make_hardlinkable_file(self, filename):
        with open(filename, 'w') as f:
            f.write(" ")
        self.filenames.append(filename)

    def make_linked_file(self, src, dst):
        os.link(src, dst)
        self.filenames.append(dst)

    def remove_file(self, filename):
        os.unlink(filename)
        self.filenames.remove(filename)

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
        self.make_hardlinkable_file("a")
        self.make_hardlinkable_file("b")
        hardlink.main()

        self.assertTrue(os.lstat("a").st_nlink == os.lstat("b").st_nlink or
                        os.lstat("a").st_nlink == self.max_nlinks or
                        os.lstat("b").st_nlink == self.max_nlinks)

        self.remove_file("a")
        self.remove_file("b")
        self.make_hardlinkable_file("b")
        hardlink.main()

        num_c_links = 1000
        for i in range(num_c_links):
            filename = "c"+str(i)
            self.make_hardlinkable_file(filename)
        # Should link just the c's to each other
        hardlink.main()

        self.assertEqual(os.lstat("b").st_nlink, self.max_nlinks)
        self.assertEqual(os.lstat("c0").st_nlink, num_c_links)


@unittest.skip("Forces filesystem permission errors to test logging and recovery")
class TestErrorLogging(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.pathnames = [] # Keep track of all files/dirs, for deleting later
        os.chdir(self.root)

        for filename in ["a", "b"]:
            with open(filename, "w") as f:
                f.write("foobar")
                self.pathnames.append(filename)

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
        for pathname in self.pathnames:
            assert not pathname.lstrip().startswith('/')
            if os.path.isfile(pathname):
                os.unlink(pathname)
        os.rmdir(self.root)


if __name__ == '__main__':
    unittest.main()
