#!/usr/bin/env python

# hardlinkable - Goes through a directory structure and reports files which are
# identical and could be hard-linked together.  Optionally performs the
# hardlinking.
#
# Copyright 2007-2018  Antti Kaihola, Carl Henrik Lunde, Chad Netzer, et al
# Copyright 2003-2018  John L. Villalovos, Hillsboro, Oregon
#
# This program is free software; you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation; either version 2 of the License, or (at your option) any later
# version.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE.  See the GNU General Public License for more
# details.
#
# You should have received a copy of the GNU General Public License along with
# this program; if not, write to the Free Software Foundation, Inc., 59 Temple
# Place, Suite 330, Boston, MA  02111-1307, USA.

import copy as _copy
import filecmp as _filecmp
import logging as _logging
import os as _os
import re as _re
import stat as _stat
import sys as _sys
import time as _time

from optparse import OptionParser as _OptionParser
from optparse import OptionGroup as _OptionGroup
from optparse import SUPPRESS_HELP as _SUPPRESS_HELP
from optparse import TitledHelpFormatter as _TitledHelpFormatter

try:
    from zlib import crc32 as _crc32
    DEFAULT_LINEAR_SEARCH_THRESH = 1
except ImportError:
    try:
        from binascii import crc32 as _crc32
        DEFAULT_LINEAR_SEARCH_THRESH = 1
    except ImportError:
        DEFAULT_LINEAR_SEARCH_THRESH = None

# Python 2.3 has the sets module, not the set type
try:
    set
except NameError:
    from sets import Set as set

# Python 3 moved intern() to sys module
try:
    _intern = intern
except NameError:
    _intern = _sys.intern

__all__ = ["Hardlinkable", "LinkingStats"]

# global declarations
__version__ = '0.8'
_VERSION = "0.8 alpha - 2018-07-09 (09-Jul-2018)"


def _parse_command_line(get_default_options=False):
    usage = "usage: %prog [options] directory [ directory ... ]"
    version = "%prog: " + _VERSION
    description = """\
This is a tool to scan directories and report on the space that could be saved
by hard linking identical files.  It can also perform the linking."""

    formatter = _TitledHelpFormatter(max_help_position=26)
    parser = _OptionParser(usage=usage,
                           version=version,
                           description=description,
                           formatter=formatter)
    parser.add_option("-q", "--no-stats", dest="printstats",
                      help="Do not print the statistics",
                      action="store_false", default=True,)

    parser.add_option("-v", "--verbose", dest="verbosity",
                      help="Increase verbosity level (Up to 3 times)",
                      action="count", default=0,)

    parser.add_option("--enable-linking", dest="linking_enabled",
                      help="Perform the actual hardlinking",
                      action="store_true", default=False,)

    # hidden debug option, each repeat increases debug level (long option only)
    parser.add_option("-d", "--debug", dest="debug_level",
                      help=_SUPPRESS_HELP,
                      action="count", default=0,)

    # hidden linear search threshold option, allows tuning content digest usage
    parser.add_option("--linear-search-thresh", dest="linear_search_thresh",
                      help=_SUPPRESS_HELP,
                      action="store", default=DEFAULT_LINEAR_SEARCH_THRESH,)

    group = _OptionGroup(parser, title="File Matching", description="""\
File content must always match exactly to be linkable.  Use --content-only with
caution, as it can lead to surprising results, including files becoming owned
by another user.
""")
    parser.add_option_group(group)

    group.add_option("-f", "--same-name", dest="samename",
                     help="Filenames have to be identical",
                     action="store_true", default=False,)

    group.add_option("-p", "--ignore-perms", dest="ignore_perm",
                     help="File permissions do not need to match",
                     action="store_true", default=False,)

    group.add_option("-t", "--ignore-time", dest="ignore_time",
                     help="File modification times do not need to match",
                     action="store_true", default=False,)

    group.add_option("-s", "--min-size", dest="min_file_size", metavar="SZ",
                     help="Minimum file size (default: %default)",
                     default="1",)

    group.add_option("-S", "--max-size", dest="max_file_size", metavar="SZ",
                     help="Maximum file size (Can add 'k', 'm', etc.)",
                     default=None,)

    group.add_option("-c", "--content-only", dest="contentonly",
                     help="Only file contents have to match",
                     action="store_true", default=False,)

    group = _OptionGroup(parser, title="Name Matching (may specify multiple times)",)
    parser.add_option_group(group)

    group.add_option("-m", "--match", dest="matches", metavar="RE",
                     help="Regular expression used to match files",
                     action="append", default=[],)

    group.add_option("-x", "--exclude", dest="excludes", metavar="RE",
                     help="Regular expression used to exclude files/dirs",
                     action="append", default=[],)

    # Allow for a way to get a default options object (for Statistics)
    if get_default_options:
        (options, args) = parser.parse_args([""])
        options_validation(options)
        return options

    (options, args) = parser.parse_args()
    if not args:
        parser.print_help()
        _sys.stderr.write("\nMust supply one or more directories\n")
        _sys.exit(2)
    args = [_os.path.normpath(_os.path.expanduser(dirname)) for dirname in args]
    for dirname in args:
        if not _os.path.isdir(dirname):
            parser.error("%s is NOT a directory" % dirname)

    options_validation(options)

    return options, args


def options_validation(options):
    if options.debug_level > 1:
        _logging.getLogger().setLevel(_logging.DEBUG)

    # Convert "humanized" size inputs to integer bytes
    try:
        options.min_file_size = _humanized_number_to_bytes(options.min_file_size)
    except ValueError:
        parser.error("option -s: invalid integer value: '%s'" % options.min_file_size)
    if options.max_file_size is not None:
        try:
            options.max_file_size = _humanized_number_to_bytes(options.max_file_size)
        except ValueError:
            parser.error("option -S: invalid integer value: '%s'" % options.max_file_size)
    # Check validity of min/max size options
    if options.min_file_size < 0:
        parser.error("--min_size cannot be negative")
    if options.max_file_size is not None and options.max_file_size < 0:
        parser.error("--max_size cannot be negative")
    if options.max_file_size is not None and options.max_file_size < options.min_file_size:
        parser.error("--max_size cannot be smaller than --min_size")

    # If linking is enabled, output a message early to indicate what is
    # happening in case the program is set to zero verbosity and is taking a
    # long time doing comparisons with no output.  It's helpful to know
    # definitively that the program is set to modify the filesystem.
    if options.linking_enabled:
        print("----- Hardlinking enabled.  The filesystem will be modified -----")


class Hardlinkable:
    def __init__(self, options=None):
        if options is None:
            options = _parse_command_line(get_default_options=True)
        self.options = options
        self.stats = LinkingStats(options)
        self._fsdevs = {}

    def linkables(self, directories):
        """Yield pairs of linkable pathnames in the given directories"""
        for (src_file_info, dst_file_info) in self._sorted_links(directories):
            src_namepair = src_file_info[:2]
            dst_namepair = dst_file_info[:2]
            src_pathname = _os.path.join(*src_namepair)
            dst_pathname = _os.path.join(*dst_namepair)

            assert not self.options.samename or src_namepair[1] == dst_namepair[1]
            yield (src_pathname, dst_pathname)

    def run(self, directories):
        """Run link scan, and perform linking if requested.  Return stats."""
        # Prevent 'directories' from accidentally being a stringlike or
        # byteslike.  We don't want to "walk" each string character as a dir,
        # especially since it has a good chance of starting with an '/'.
        if _sys.version_info[0] == 2:
            if isinstance(directories, basestring):
                directories = [directories]
        elif isinstance(directories, str) or isinstance(directories, bytes):
            directories = [directories]

        for dirname in directories:
            if not _os.path.isdir(dirname):
                raise IOError("%s is not a directory" % dirname)

        aborted_early = False
        for (src_file_info, dst_file_info) in self._sorted_links(directories):
            assert not self.options.samename or src_file_info[1] == dst_file_info[1]
            if self.options.linking_enabled:
                # DO NOT call hardlink_files() unless link creation
                # is selected. It unconditionally performs links.
                hardlink_succeeded = self._hardlink_files(src_file_info, dst_file_info)

                # If hardlinking fails, we assume the worst and abort early.
                # This is partly because it could mean the filesystem tree is
                # being modified underneath us, which we aren't prepared to
                # deal with.
                if not hardlink_succeeded:
                    _logging.error("Hardlinking failed. Aborting early... Statistics may be incomplete")
                    aborted_early = True
                    break

            assert not aborted_early

        self.stats.print_stats(aborted_early)

        if not aborted_early:
            self._postlink_inode_stats = self._inode_stats()
            self._inode_stats_sanity_check(self._prelink_inode_stats,
                                           self._postlink_inode_stats)

            # Store the inode stats with the LinkingStats, useful for testing
            self.stats.inode_stats = [self._prelink_inode_stats,
                                      self._postlink_inode_stats]

        return self.stats

    def matched_file_info(self, directories):
        """Yield (dirname, filename, stat_info) triplets for all non-excluded/matched files"""
        options = self.options

        # Now go through all the directories that have been added.
        for top_dir in directories:
            # Use topdown=True for directory search pruning. followlinks is False
            for dirpath, dirs, filenames in _os.walk(top_dir, topdown=True):
                assert dirpath

                # If excludes match any of the subdirs (or the current dir), skip
                # them.
                unculled_dirs = dirs[:]
                _cull_excluded_directories(dirs, options.excludes)
                self.stats.excluded_dirs(dirpath, set(unculled_dirs) - set(dirs))
                cur_dir = _os.path.basename(dirpath)
                if cur_dir and _found_excluded_regex(cur_dir, options.excludes):
                    self.stats.excluded_dir(dirpath)
                    continue

                self.stats.found_directory()

                # Loop through all the files in the directory
                for filename in filenames:
                    assert filename
                    pathname = _os.path.normpath(_os.path.join(dirpath, filename))
                    if _found_excluded_regex(filename, options.excludes):
                        self.stats.excluded_file(pathname)
                        continue
                    if not _found_matched_filename_regex(filename, options.matches):
                        self.stats.included_file(pathname)
                        continue

                    try:
                        stat_info = _os.lstat(pathname)
                    except OSError:
                        error = _sys.exc_info()[1]
                        _logging.warning("Unable to get stat info for: %s\n%s" % (pathname, error))
                        continue

                    # Is it a regular file?
                    assert not _stat.S_ISDIR(stat_info.st_mode)
                    if not _stat.S_ISREG(stat_info.st_mode):
                        continue

                    # Is the file within the selected size range?
                    if ((options.max_file_size is not None and
                         stat_info.st_size > options.max_file_size) or
                        (stat_info.st_size < options.min_file_size)):
                        self.stats.file_outside_size_range(pathname, stat_info.st_size)
                        continue

                    if stat_info.st_dev not in self._fsdevs:
                        # Try to discover the maximum number of nlinks possible for
                        # each new device.
                        try:
                            max_nlinks = _os.pathconf(pathname, "PC_LINK_MAX")
                        except OSError:
                            # Avoid retrying if PC_LINK_MAX fails for a device
                            max_nlinks = None
                        fsdev = self._get_fsdev(stat_info.st_dev)
                        fsdev.max_nlinks = max_nlinks

                    # Bump statistics count of regular files found.
                    self.stats.found_regular_file(pathname)

                    # Extract the normalized path directory name
                    dirname = _os.path.dirname(pathname)

                    # Try to save space on redundant dirname and filename
                    # storage by interning
                    dirname = _intern(dirname)
                    filename = _intern(filename)
                    yield (dirname, filename, stat_info)

    def _sorted_links(self, directories):
        """Perform the walk, collect and sort linking data, and yield link tuples."""
        for dirname, filename, stat_info in self.matched_file_info(directories):
            self._find_identical_files(dirname, filename, stat_info)

        self._prelink_inode_stats = self._inode_stats()
        for fsdev in self._fsdevs.values():
            for linkable_set in _linkable_inode_sets(fsdev.linked_inodes):
                # Decorate-sort-undecorate with st_link as primary key
                # Order inodes from greatest to least st_nlink
                nlinks_list = [(fsdev.ino_stat[ino].st_nlink, ino) for ino in linkable_set]
                nlinks_list.sort(reverse=True)
                ino_list = [x[1] for x in nlinks_list]  # strip nlinks sort key

                # Keep a list if inos from the end of the ino_list that cannot
                # be linked to (such as when in 'samename' mode), and reappend
                # them to nlist when the src inode advances.
                remaining_inos = []

                assert len(ino_list) > 0
                while ino_list or remaining_inos:  # outer while
                    # reappend remaining_inos stack to ino_list
                    if remaining_inos:
                        ino_list.extend(remaining_inos[::-1])
                        remaining_inos = []

                    assert len(remaining_inos) == 0
                    assert len(ino_list) > 0

                    # Ensure we don't try to combine inodes that would create
                    # more links than the maximum allowed nlinks, by advancing
                    # src until src + dst nlink <= max_nlinks
                    #
                    # Every loop shortens the nlinks_list, so the loop will
                    # terminate.
                    src_ino = ino_list[0]
                    ino_list = ino_list[1:]
                    while ino_list:  # inner while
                        # Always removes either first or last element, so loop
                        # must terminate
                        dst_ino = ino_list.pop()
                        src_stat_info = fsdev.ino_stat[src_ino]
                        dst_stat_info = fsdev.ino_stat[dst_ino]

                        # Samename can break nlink ordering invariant
                        assert self.options.samename or src_stat_info.st_nlink >= dst_stat_info.st_nlink

                        # Ignore samename when checking max_nlink invariant
                        if (fsdev.max_nlinks is not None and
                            src_stat_info.st_nlink + dst_stat_info.st_nlink > fsdev.max_nlinks):
                            # Move inos to remaining_inos, so that src_ino will advance
                            remaining_inos.append(dst_ino)
                            remaining_inos.extend(ino_list[::-1])
                            ino_list = []
                            break

                        # Loop through all linkable pathnames in the last inode
                        for dst_dirname, dst_filename in _namepairs_per_inode(fsdev.ino_pathnames[dst_ino]):
                            if self.options.samename and dst_filename not in fsdev.ino_pathnames[src_ino]:
                                assert dst_filename not in fsdev.ino_pathnames[src_ino]
                                continue
                            lookup_filename = self.options.samename and dst_filename
                            src_dirname, src_filename = fsdev.arbitrary_namepair_from_ino(src_ino, lookup_filename)
                            src_file_info = (src_dirname, src_filename, src_stat_info)
                            dst_file_info = (dst_dirname, dst_filename, dst_stat_info)

                            yield (src_file_info, dst_file_info)

                            # After yielding, we can update stat_info to
                            # account for hard-linking
                            self.stats.did_hardlink(src_file_info, dst_file_info)

                            src_stat_info = fsdev.updated_stat_info(src_ino, nlink=src_stat_info.st_nlink + 1)
                            dst_stat_info = fsdev.updated_stat_info(dst_ino, nlink=dst_stat_info.st_nlink - 1)
                            assert src_stat_info.st_nlink <= fsdev.max_nlinks
                            assert dst_stat_info is None or dst_stat_info.st_nlink > 0

                            dst_namepair = tuple(dst_file_info[:2])
                            fsdev.move_linked_namepair(dst_namepair, src_ino, dst_ino)

                        # if there are still pathnames to the dest inode, save
                        # it for possible linking later (for samename, mainly)
                        if fsdev.ino_pathnames[dst_ino]:
                            remaining_inos.append(dst_ino)

    # dirname is the directory component and filename is just the file name
    # component (ie. the basename) without the path.  The tree walking provides
    # this, so we don't have to extract it with _os.path.split()
    def _find_identical_files(self, dirname, filename, stat_info):
        options = self.options

        fsdev = self._get_fsdev(stat_info.st_dev)
        ino = stat_info.st_ino
        file_info = (dirname, filename, stat_info)
        namepair = (dirname, filename)

        if ino not in fsdev.ino_stat:
            self.stats.found_inode()

        inode_hash = _stat_hash_value(stat_info, options)
        if inode_hash not in fsdev.inode_hashes:
            self.stats.missed_hash()
            # Create a new entry for this hash value and store inode number.
            fsdev.inode_hashes[inode_hash] = set([ino])
            assert ino not in fsdev.ino_stat
        else:
            self.stats.found_hash()
            # See if the new file has the same inode as one we've already seen.
            if ino in fsdev.ino_stat:
                prev_namepair = fsdev.arbitrary_namepair_from_ino(ino)
                prev_stat_info = fsdev.ino_stat[ino]
                self.stats.found_existing_hardlink(prev_namepair, namepair, prev_stat_info)
            # We have file(s) that have the same hash as our current file.  If
            # our inode is already cached, we might be able to use past
            # comparison work to avoid further file comparisons, by looking to
            # see if it's an inode we've already seen and linked to others.
            linked_inodes = _linked_inode_set(ino, fsdev.linked_inodes)
            found_linked_ino = (len(linked_inodes & fsdev.inode_hashes[inode_hash]) > 0)
            if not found_linked_ino:
                cached_inodes_seq = fsdev.inode_hashes[inode_hash]
                # Since the cached inodes use a simple linear search, they can
                # devolve to O(n**2) worst case, typically when contentonly
                # option encounters a large number of same-size files.
                #
                # Use content hashing to hopefully shortcut the searches.  The
                # downside is that the content hash must access the file data
                # (not just the inode metadata), and currently only uses
                # differences at the beginnings of files.  But it can help
                # quickly differentiate many files with (for example) the same
                # size, but different contents.
                use_content_digest = (options.linear_search_thresh is not None and
                                      len(cached_inodes_seq) > int(options.linear_search_thresh))
                if use_content_digest:
                    digest = _content_digest(_os.path.join(*namepair))
                    # Revert to full search if digest can't be computed
                    if digest is not None:
                        cached_inodes_no_digest = cached_inodes_seq - fsdev.inodes_with_digest
                        self.stats.computed_digest()
                        fsdev.add_content_digest(file_info, digest)
                        cached_inodes_same_digest = cached_inodes_seq & fsdev.digest_inode_map[digest]
                        cached_inodes_different_digest = (cached_inodes_seq -
                                                          cached_inodes_same_digest -
                                                          cached_inodes_no_digest)

                        assert len(cached_inodes_same_digest &
                                   cached_inodes_different_digest &
                                   cached_inodes_no_digest) == 0

                        # Search matching digest inos first (as they may have the
                        # same content).  Don't search those with differing digests
                        # at all (as they cannot be equal).
                        cached_inodes_seq = list(cached_inodes_same_digest) + list(cached_inodes_no_digest)

                # We did not find this file as linked to any other cached
                # inodes yet.  So now lets see if our file should be hardlinked
                # to any of the other files with the same hash.
                self.stats.search_hash_list()
                for cached_ino in cached_inodes_seq:
                    self.stats.inc_hash_list_iteration()

                    cached_file_info = fsdev.fileinfo_from_ino(cached_ino)

                    if self._are_files_hardlinkable(cached_file_info, file_info, use_content_digest):
                        self._found_hardlinkable_file(cached_file_info, file_info)
                        break
                else:  # nobreak
                    self.stats.no_hash_match()
                    # The file should NOT be hardlinked to any of the other
                    # files with the same hash. Add to the list of unlinked
                    # inodes for this hash value.
                    fsdev.inode_hashes[inode_hash].add(ino)
                    fsdev.ino_stat[ino] = stat_info

        # Always add the new file to the stored inode information
        fsdev.ino_stat[ino] = stat_info
        fsdev.ino_append_namepair(ino, filename, namepair)

    def _hardlink_files(self, src_file_info, dst_file_info):
        """Actually perform the filesystem hardlinking of two files."""
        src_dirname, src_filename, src_stat_info = src_file_info
        dst_dirname, dst_filename, dst_stat_info = dst_file_info

        src_pathname = _os.path.join(src_dirname, src_filename)
        dst_pathname = _os.path.join(dst_dirname, dst_filename)

        # Quit early if the src or dst files have been updated since we first
        # lstat()-ed them. The cached mtime needs to be kept up to date for
        # this to work correctly.
        if (file_has_been_modified(src_pathname, src_stat_info) or
            file_has_been_modified(dst_pathname, dst_stat_info)):
            return False

        hardlink_succeeded = False
        # rename the destination file to save it
        tmp_pathname = dst_pathname + "._tmp_while_linking"
        try:
            _os.rename(dst_pathname, tmp_pathname)
        except OSError:
            error = _sys.exc_info()[1]
            _logging.error("Failed to rename: %s to %s\n%s" % (dst_pathname, tmp_pathname, error))
        else:
            # Now link the sourcefile to the destination file
            try:
                _os.link(src_pathname, dst_pathname)
            except Exception:
                error = _sys.exc_info()[1]
                _logging.error("Failed to hardlink: %s to %s\n%s" % (src_pathname, dst_pathname, error))
                # Try to recover
                try:
                    _os.rename(tmp_pathname, dst_pathname)
                except Exception:
                    error = _sys.exc_info()[1]
                    _logging.critical("Failed to rename temp filename %s back to %s\n%s" % (tmp_pathname, dst_pathname, error))
                    _sys.exit(3)
            else:
                hardlink_succeeded = True

                # Delete the renamed version since we don't need it.
                try:
                    _os.unlink(tmp_pathname)
                except Exception:
                    error = _sys.exc_info()[1]
                    # Failing to remove the temp file could lead to endless
                    # attempts to link to it in the future.
                    _logging.critical("Failed to remove temp filename: %s\n%s" % (tmp_pathname, error))
                    _sys.exit(3)

                # Use the destination file times if it's most recently modified
                dst_mtime = dst_atime = None
                if dst_stat_info.st_mtime > src_stat_info.st_mtime:
                    try:
                        _os.utime(src_pathname, (dst_stat_info.st_atime, dst_stat_info.st_mtime))
                        dst_atime = dst_stat_info.st_atime
                        dst_mtime = dst_stat_info.st_mtime
                    except Exception:
                        error = _sys.exc_info()[1]
                        _logging.warning("Failed to update file time attributes for %s\n%s" % (src_pathname, error))

                    self._updated_stat_info(src_stat_info,
                                            mtime=dst_mtime,
                                            atime=dst_atime)
        return hardlink_succeeded

    def _get_fsdev(self, st_dev, max_nlinks=None):
        """Return an FSDev for given stat_info.st_dev"""
        fsdev = self._fsdevs.get(st_dev, None)
        if fsdev is None:
            fsdev = _FSDev(st_dev, max_nlinks)
            self._fsdevs[st_dev] = fsdev
        return fsdev

    # Determine if a file is eligibile for hardlinking.  Files will only be
    # considered for hardlinking if this function returns true.
    def _eligible_for_hardlink(self, st1, st2):
        options = self.options
        # A chain of required criteria:
        result = (not _is_already_hardlinked(st1, st2) and
                  st1.st_dev == st2.st_dev and
                  st1.st_size == st2.st_size)

        if not options.contentonly:
            result = (result and
                      (options.ignore_time or st1.st_mtime == st2.st_mtime) and
                      (options.ignore_perm or st1.st_mode == st2.st_mode) and
                      (st1.st_uid == st2.st_uid and st1.st_gid == st2.st_gid))

        fsdev = self._get_fsdev(st1.st_dev)
        if result and (fsdev.max_nlinks is not None):
            # The justification for not linking a pair of files if their nlinks sum
            # to more than the device maximum, is that linking them won't change
            # the overall link count, meaning no space saving is possible overall
            # even when all their filenames are found and re-linked.
            result = ((st1.st_nlink + st2.st_nlink) <= fsdev.max_nlinks)

        # Add some stats on the factors which may have falsified result
        if st1.st_mtime != st2.st_mtime:
            self.stats.found_mismatched_time()
        if st1.st_mode != st2.st_mode:
            self.stats.found_mismatched_mode()
        if (st1.st_uid != st2.st_uid or st1.st_gid != st2.st_gid):
            self.stats.found_mismatched_ownership()

        return result

    def _are_file_contents_equal(self, pathname1, pathname2):
        """Determine if the contents of two files are equal"""
        result = _filecmp.cmp(pathname1, pathname2, shallow=False)
        self.stats.did_comparison(pathname1, pathname2, result)
        return result

    # Determines if two files should be hard linked together.
    def _are_files_hardlinkable(self, file_info1, file_info2, use_digest):
        dirname1, filename1, stat1 = file_info1
        dirname2, filename2, stat2 = file_info2
        if not self._eligible_for_hardlink(stat1, stat2):
            result = False
        else:
            # Since we are going to read the content anyway (to compare them),
            # there is no i/o penalty in calculating a content hash.
            if use_digest:
                fsdev = self._get_fsdev(stat1.st_dev)
                fsdev.add_content_digest(file_info1)
                fsdev.add_content_digest(file_info2)
                self.stats.computed_digest(2)

            result = self._are_file_contents_equal(_os.path.join(dirname1, filename1),
                                                   _os.path.join(dirname2, filename2))
        return result

    def _found_hardlinkable_file(self, src_file_info, dst_file_info):
        src_dirname, src_filename, src_stat_info = src_file_info
        dst_dirname, dst_filename, dst_stat_info = dst_file_info

        self.stats.found_hardlinkable((src_dirname, src_filename),
                                      (dst_dirname, dst_filename))

        assert src_stat_info.st_dev == dst_stat_info.st_dev
        fsdev = self._get_fsdev(src_stat_info.st_dev)
        fsdev.add_linked_inodes(src_stat_info.st_ino, dst_stat_info.st_ino)

    def _updated_stat_info(self, stat_info, nlink=None, mtime=None, atime=None, uid=None, gid=None):
        """Updates an ino_stat stat_info with the given values."""
        fsdev = self._get_fsdev(stat_info.st_dev)
        return fsdev.updated_stat_info(stat_info.st_ino, nlink=nlink, mtime=mtime, atime=atime, uid=uid, gid=gid)

    def _updated_file_info(self, file_info):
        """Return a file_info tuple with the current stat_info value."""
        dirname, filename, stat_info = file_info
        fsdev = self._get_fsdev(stat_info.st_dev)
        new_file_info = (dirname, filename, fsdev.ino_stat[stat_info.st_ino])
        return new_file_info

    def _inode_stats(self):
        """Gather some basic inode stats from caches."""
        total_inodes = 0
        total_bytes = 0  # st_nlinks * st_size
        total_nlinks = 0
        total_redundant_bytes = 0  # Each nlink > 1 is counted as "redundant" space
        total_path_links = 0  # Total number of found paths to inodes
        total_redundant_path_bytes = 0  # Only accounts for the seen paths to an inode
        for fsdev in self._fsdevs.values():
            for ino, stat_info in fsdev.ino_stat.items():
                total_inodes += 1
                total_bytes += stat_info.st_size

                # Total nlinks value can account for pathnames skipped, or
                # outside of the walked directory trees, etc.
                total_nlinks += stat_info.st_nlink
                total_redundant_bytes += (stat_info.st_size * (stat_info.st_nlink - 1))

                # path_count is merely the number of paths to an inode that
                # we've seen (ie. that weren't excluded or outside the
                # directory tree)
                path_count = fsdev.count_pathnames_this_inode(ino)
                total_path_links += path_count
                total_redundant_path_bytes += (stat_info.st_size * (path_count - 1))

        return {'total_inodes': total_inodes,
                'total_bytes': total_bytes,
                'total_nlinks': total_nlinks,
                'total_redundant_bytes': total_redundant_bytes,
                'total_path_links': total_path_links,
                'total_redundant_path_bytes': total_redundant_path_bytes}

    def _inode_stats_sanity_check(self, prelink_inode_stats, postlink_inode_stats):
        """Check stats directly from inode data."""
        # double check figures based on direct inode stats
        totalsavedbytes = self.stats.bytes_saved_thisrun + self.stats.bytes_saved_previously
        bytes_saved_thisrun = postlink_inode_stats['total_redundant_path_bytes'] - prelink_inode_stats['total_redundant_path_bytes']
        assert totalsavedbytes == postlink_inode_stats['total_redundant_path_bytes']
        assert self.stats.bytes_saved_thisrun == bytes_saved_thisrun


class _FSDev:
    """Per filesystem (ie. st_dev) operations"""
    def __init__(self, st_dev, max_nlinks):
        self.st_dev = st_dev
        self.max_nlinks = max_nlinks  # Can be None

        # For each hash value, track inode (and optionally filename)
        # inode_hashes <- {hash_val: set(ino)}
        self.inode_hashes = {}

        # For each stat hash, keep a digest of the first 8K of content.  Used
        # to reduce linear search when looking through comparable files.
        # digest_inode_map <- {digest: set(ino)}
        self.digest_inode_map = {}
        self.inodes_with_digest = set()

        # Keep track of per-inode stat info
        # ino_stat <- {st_ino: stat_info}
        self.ino_stat = {}

        # For each inode, keep track of all the pathnames
        # ino_pathnames <- {st_ino: {filename: list((dirname, filename))}}
        self.ino_pathnames = {}

        # For each linkable file pair found, add their inodes as a pair (ie.
        # ultimately we want to "link" the inodes together).  Each pair is
        # added twice, in each order, so that a pair can be found from either
        # inode.
        # linked_inodes = {largest_ino_num: set(ino_nums)}
        self.linked_inodes = {}

    def arbitrary_namepair_from_ino(self, ino, filename=None):
        # Get the dict of filename: [pathnames] for ino_key
        d = self.ino_pathnames[ino]
        if filename:
            l = d[filename]
        else:
            # Get an arbitrary pathnames list
            l = next(iter(d.values()))
        return l[0]

    def ino_append_namepair(self, ino, filename, namepair):
        d = self.ino_pathnames.setdefault(ino, {})
        l = d.setdefault(filename, [])
        l.append(namepair)

    def fileinfo_from_ino(self, ino, filename=None):
        """When filename is None, chooses an arbitrary namepair linked to the inode"""
        if filename:
            assert ino in self.ino_pathnames
            assert filename in self.ino_pathnames[ino]
            l = self.ino_pathnames[ino][filename]
            dirname, filename = l[0]
        else:
            dirname, filename = self.arbitrary_namepair_from_ino(ino)
        return (dirname, filename, self.ino_stat[ino])

    def updated_stat_info(self, ino, nlink=None, mtime=None, atime=None, uid=None, gid=None):
        """Updates an ino_stat stat_info with the given values."""
        stat_info = self.ino_stat[ino]
        l = list(stat_info)
        if nlink is not None:
            l[_stat.ST_NLINK] = nlink
        if mtime is not None:
            l[_stat.ST_MTIME] = mtime
        if atime is not None:
            l[_stat.ST_ATIME] = atime
        if uid is not None:
            l[_stat.ST_UID] = uid
        if gid is not None:
            l[_stat.ST_GID] = gid

        new_stat_info = stat_info.__class__(l)
        self.ino_stat[ino] = new_stat_info
        if self.ino_stat[ino].st_nlink < 1:
            assert self.ino_stat[ino].st_nlink == 0
            del self.ino_stat[ino]
            new_stat_info = None
        return new_stat_info

    def ino_has_filename(self, ino, filename):
        """Return true if the given ino has 'filename' linked to it."""
        return (filename in self.ino_pathnames[ino])

    def add_linked_inodes(self, ino1, ino2):
        """Adds to the dictionary of ino1 to ino2 mappings."""
        assert ino1 != ino2
        s = self.linked_inodes.setdefault(ino1, set())
        s.add(ino2)
        s = self.linked_inodes.setdefault(ino2, set())
        s.add(ino1)

    def move_linked_namepair(self, namepair, src_ino, dst_ino):
        """Move namepair from dst_ino to src_ino (yes, backwards)"""
        dirname, filename = namepair
        pathnames = self.ino_pathnames[dst_ino][filename]
        pathnames.remove(namepair)
        assert namepair not in pathnames
        if not pathnames:
            del self.ino_pathnames[dst_ino][filename]
        self.ino_append_namepair(src_ino, filename, namepair)

    def count_pathnames_this_inode(self, ino):
        """Because of file matching and exclusions, or links to unwalked
        directory entries, the number of links that we care about may not equal
        the total nlink count for the inode."""
        # Count the number of links to this inode that we have discovered
        count = 0
        for pathnames in self.ino_pathnames[ino].values():
            count += len(pathnames)
        return count

    def add_content_digest(self, file_info, digest=None):
        dirname, filename, stat_info = file_info
        if stat_info.st_ino not in self.inodes_with_digest:
            pathname = _os.path.join(dirname, filename)
            if digest is None:
                digest = _content_digest(pathname)
                if digest is None:
                    return
            digests = self.digest_inode_map.get(digest, None)
            if digests is None:
                self.digest_inode_map[digest] = set([stat_info.st_ino])
            else:
                digests.add(stat_info.st_ino)
            self.inodes_with_digest.add(stat_info.st_ino)


class LinkingStats:
    def __init__(self, options):
        self.options = options
        self.reset()

    def reset(self):
        self.dircount = 0                   # how many directories we find
        self.regularfiles = 0               # how many regular files we find
        self.num_excluded_dirs = 0          # how many directories we exclude
        self.num_excluded_files = 0         # how many files we exclude (by regex)
        self.num_included_files = 0         # how many files we include (by regex)
        self.num_files_too_large = 0        # how many files are too large
        self.num_files_too_small = 0        # how many files are too small
        self.num_mismatched_file_times = 0  # same sized files with different mtimes
        self.num_mismatched_file_modes = 0  # same sized files with different perms
        self.num_mismatched_file_ownership = 0  # same sized files with different ownership
        self.comparisons = 0                # how many file content comparisons
        self.equal_comparisons = 0          # how many file comparisons found equal
        self.hardlinked_thisrun = 0         # hardlinks done this run
        self.num_inodes = 0                 # inodes found this run
        self.nlinks_to_zero_thisrun = 0     # how man nlinks actually went to zero
        self.hardlinked_previously = 0      # hardlinks that are already existing (based on walked dirs only)
        self.bytes_saved_thisrun = 0        # bytes saved by hardlinking this run (ie. when nlink goes to zero)
        self.bytes_saved_previously = 0     # bytes saved by previous hardlinks (in walked dirs only)
        self.hardlinkpairs = []             # list of files hardlinkable this run
        self.starttime = _time.time()       # track how long it takes
        self.currently_hardlinked = {}      # list of files currently hardlinked

        # Debugging stats
        self.num_hash_hits = 0              # Amount of times a hash is found in inode_hashes
        self.num_hash_misses = 0            # Amount of times a hash is not found in inode_hashes
        self.num_hash_mismatches = 0        # Times a hash is found, but is not a file match
        self.num_hash_list_searches = 0     # Times a hash list search is initiated
        self.num_list_iterations = 0        # Number of iterations over a list in inode_hashes
        self.num_digests_computed = 0       # Number of times content digest was computed

    def found_directory(self):
        self.dircount += 1

    def found_regular_file(self, pathname):
        self.regularfiles += 1
        if self.options.debug_level > 4:
            _logging.debug("File          : %s" % pathname)

    def excluded_dirs(self, dirname, basenames):
        self.num_excluded_dirs += len(basenames)
        if self.options.debug_level > 5:
            for name in basenames:
                pathname = os.path.join(dirname, name)
                _logging.debug("Excluded dir  : %s" % pathname)

    def excluded_dir(self, pathname):
        self.num_excluded_dirs += 1
        if self.options.debug_level > 5:
            _logging.debug("Excluded dir  : %s" % pathname)

    def excluded_file(self, pathname):
        self.num_excluded_files += 1
        if self.options.debug_level > 5:
            _logging.debug("Excluded file : %s" % pathname)

    def included_file(self, pathname):
        self.num_included_files += 1
        if self.options.debug_level > 5:
            _logging.debug("Included file : %s" % pathname)

    def file_outside_size_range(self, pathname, filesize):
        if (self.options.max_file_size is not None and
            filesize > self.options.max_file_size):
            self.num_files_too_large += 1
            if self.options.debug_level > 5:
                _logging.debug("File too large: %s" % pathname)

        if filesize < self.options.min_file_size:
            self.num_files_too_small += 1
            if self.options.debug_level > 5:
                _logging.debug("File too small: %s" % pathname)

    def found_mismatched_time(self):
        self.num_mismatched_file_times += 1

    def found_mismatched_mode(self):
        self.num_mismatched_file_modes += 1

    def found_mismatched_ownership(self):
        self.num_mismatched_file_ownership += 1

    def did_comparison(self, pathname1, pathname2, result):
        self.comparisons += 1
        if result:
            self.equal_comparisons += 1
        if self.options.debug_level > 2:
            if result:
                _logging.debug("Compared equal: %s" % pathname1)
                _logging.debug(" to           : %s" % pathname2)
            else:
                _logging.debug("Compared      : %s" % pathname1)
                _logging.debug(" to           : %s" % pathname2)

    def found_existing_hardlink(self, src_namepair, dst_namepair, stat_info):
        assert len(src_namepair) == 2
        assert len(dst_namepair) == 2
        if self.options.debug_level > 3:
            _logging.debug("Existing link : %s" % _os.path.join(*src_namepair))
            _logging.debug(" with         : %s" % _os.path.join(*dst_namepair))
        filesize = stat_info.st_size
        self.hardlinked_previously += 1
        self.bytes_saved_previously += filesize
        if (self.options.verbosity > 1 or
            getattr(self.options, '_force_stats_to_store_old_hardlinks', False)):
            if src_namepair not in self.currently_hardlinked:
                self.currently_hardlinked[src_namepair] = (filesize, [dst_namepair])
            else:
                self.currently_hardlinked[src_namepair][1].append(dst_namepair)

    def found_hardlinkable(self, src_namepair, dst_namepair):
        # We don't actually keep these stats, and we record the actual links
        # later, after the ordering by nlink count.  Just log.
        if self.options.debug_level > 1:
            assert src_namepair != dst_namepair
            _logging.debug("Linkable      : %s" % _os.path.join(*src_namepair))
            _logging.debug(" to           : %s" % _os.path.join(*dst_namepair))

    def found_inode(self):
        self.num_inodes += 1

    def did_hardlink(self, src_file_info, dst_file_info):
        src_namepair = tuple(src_file_info[:2])
        dst_namepair = tuple(dst_file_info[:2])
        dst_stat_info = dst_file_info[2]

        if (self.options.verbosity > 0 or
            getattr(self.options, '_force_stats_to_store_new_hardlinks', False)):
            self.hardlinkpairs.append((tuple(src_namepair),
                                       tuple(dst_namepair)))
        filesize = dst_stat_info.st_size
        self.hardlinked_thisrun += 1
        if dst_stat_info.st_nlink == 1:
            # We only save bytes if the last link was actually removed.
            self.bytes_saved_thisrun += filesize
            self.nlinks_to_zero_thisrun += 1

    def found_hash(self):
        self.num_hash_hits += 1

    def missed_hash(self):
        """When a hash lookup isn't found"""
        self.num_hash_misses += 1

    def no_hash_match(self):
        """When a hash lookup succeeds, but no matching value found"""
        self.num_hash_mismatches += 1

    def search_hash_list(self):
        self.num_hash_list_searches += 1

    def inc_hash_list_iteration(self):
        self.num_list_iterations += 1

    def computed_digest(self, num=1):
        self.num_digests_computed += num

    def _count_hardlinked_previously(self):
        count = 0
        for filesize, namepairs in self.currently_hardlinked.values():
            count += len(namepairs)
        return count

    def print_stats(self, possibly_incomplete=False):
        if not self.options.printstats:
            return

        if possibly_incomplete:
            print("Statistics possibly incomplete due to errors")

        if self.options.verbosity > 1 and self.currently_hardlinked:
            print("Currently hardlinked files")
            print("-----------------------")
            keys = list(self.currently_hardlinked.keys())
            keys.sort()  # Could use sorted() once we only support >= Python 2.4
            for key in keys:
                size, file_list = self.currently_hardlinked[key]
                print("Currently hardlinked: %s" % _os.path.join(*key))
                for namepair in file_list:
                    pathname = _os.path.join(*namepair)
                    print("                    : %s" % pathname)
                print("Size per file: %s  Total saved: %s" % (_humanize_number(size),
                                                              _humanize_number(size * len(file_list))))
            print("")
        # Print out the stats for the files we hardlinked, if any
        if self.options.verbosity > 0 and self.hardlinkpairs:
            if self.options.linking_enabled:
                print("Files that were hardlinked this run")
            else:
                print("Files that are hardlinkable")
            print("-----------------------")
            for (src_namepair, dst_namepair) in self.hardlinkpairs:
                print("from: %s" % _os.path.join(*src_namepair))
                print("  to: %s" % _os.path.join(*dst_namepair))
            print("")
        print("Hard linking statistics")
        print("-----------------------")
        if not self.options.linking_enabled:
            print("Statistics reflect what would result if actual linking were enabled")
        print("Directories                : %s" % self.dircount)
        print("Files                      : %s" % self.regularfiles)
        print("Comparisons                : %s" % self.comparisons)
        if self.options.linking_enabled:
            s1 = "Consolidated inodes        : %s"
            s2 = "Hardlinked this run        : %s"
        else:
            s1 = "Consolidatable inodes found: %s"
            s2 = "Hardlinkable files found   : %s"
        print(s1 % self.nlinks_to_zero_thisrun)
        print(s2 % self.hardlinked_thisrun)
        print("Total old and new hardlinks: %s" % (self.hardlinked_previously + self.hardlinked_thisrun))
        print("Currently hardlinked bytes : %s (%s)" % (self.bytes_saved_previously,
                                                        _humanize_number(self.bytes_saved_previously)))
        if self.options.linking_enabled:
            s3 = "Additional linked bytes    : %s (%s)"
        else:
            s3 = "Additional linkable bytes  : %s (%s)"
        print(s3 % (self.bytes_saved_thisrun, _humanize_number(self.bytes_saved_thisrun)))
        totalbytes = self.bytes_saved_thisrun + self.bytes_saved_previously
        if self.options.linking_enabled:
            s4 = "Total hardlinked bytes     : %s (%s)"
        else:
            s4 = "Total hardlinkable bytes   : %s (%s)"
        print(s4 % (totalbytes, _humanize_number(totalbytes)))
        if self.options.verbosity > 0 or self.options.debug_level > 0:
            print("Inodes found               : %s" % self.num_inodes)
            print("Current hardlinks          : %s" % self.hardlinked_previously)
            if self.num_excluded_dirs:
                print("Total excluded dirs        : %s" % self.num_excluded_dirs)
            if self.num_excluded_files:
                print("Total excluded files       : %s" % self.num_excluded_files)
            if self.num_included_files:
                print("Total included files       : %s" % self.num_included_files)
            if self.num_files_too_large:
                print("Total too large files      : %s" % self.num_files_too_large)
            if self.num_files_too_small:
                print("Total too small files      : %s" % self.num_files_too_small)
            if self.num_mismatched_file_times:
                print("Total unequal file times   : %s" % self.num_mismatched_file_times)
            if self.num_mismatched_file_modes:
                print("Total unequal file modes   : %s" % self.num_mismatched_file_modes)
            if self.num_mismatched_file_ownership:
                print("Total unequal file uid/gid : %s" % self.num_mismatched_file_ownership)
            print("Total remaining inodes     : %s" % (self.num_inodes - self.nlinks_to_zero_thisrun))
            assert (self.num_inodes - self.nlinks_to_zero_thisrun) > 0
        if self.options.debug_level > 0:
            print("Total run time             : %s seconds" % round(_time.time() - self.starttime, 3))
            print("Total file hash hits       : %s  misses: %s  sum total: %s" % (self.num_hash_hits,
                                                                                  self.num_hash_misses,
                                                                                  (self.num_hash_hits +
                                                                                   self.num_hash_misses)))
            print("Total hash mismatches      : %s  (+ total hardlinks): %s" % (self.num_hash_mismatches,
                                                                                (self.num_hash_mismatches +
                                                                                 self.hardlinked_previously +
                                                                                 self.hardlinked_thisrun)))
            print("Total hash searches        : %s" % self.num_hash_list_searches)
            if self.num_hash_list_searches == 0:
                avg_per_search = "N/A"
            else:
                avg_per_search = round(float(self.num_list_iterations) / self.num_hash_list_searches, 3)
            print("Total hash list iterations : %s  (avg per-search: %s)" % (self.num_list_iterations, avg_per_search))
            print("Total equal comparisons    : %s" % self.equal_comparisons)
            print("Total digests computed     : %s" % self.num_digests_computed)


#################
# Module functions
#################

def _stat_hash_value(stat_info, options):
    """Return a value appropriate for a python dict or shelve key, which can
    differentiate files which cannot be hardlinked."""
    size = stat_info.st_size
    if options.ignore_time or options.contentonly:
        value = size
    else:
        mtime = int(stat_info.st_mtime)
        value = size ^ mtime

    return value


def _cull_excluded_directories(dirs, excludes):
    """Remove any excluded directories from dirs.

    Note that it modifies dirs in place, as required by os.walk()
    """
    for dirname in dirs[:]:
        if _found_excluded_regex(dirname, excludes):
            try:
                dirs.remove(dirname)
            except ValueError:
                break
            # os.walk() will ensure no repeated dirnames
            assert dirname not in dirs


def _found_excluded_regex(name, excludes):
    """If excludes option is given, return True if name matches any regex."""
    for exclude in excludes:
        if _re.search(exclude, name):
            return True
    return False


def _found_matched_filename_regex(name, matches):
    """If matches option is given, return False if name doesn't match any
    patterns.  If no matches are given, return True."""
    if not matches:
        return True
    for match in matches:
        if _re.search(match, name):
            return True
    return False


def _linked_inode_set(ino, linked_inodes):
    """Return set of inodes that are connected to given inode"""

    if ino not in linked_inodes:
        return set([ino])
    remaining_inodes = linked_inodes.copy()
    result_set = set()
    pending = [ino]
    while pending:
        ino = pending.pop()
        result_set.add(ino)
        try:
            connected_links = remaining_inodes.pop(ino)
            pending.extend(connected_links)
        except KeyError:
            pass
    return result_set


# Note that this function is similar to the above, but doesn't rely upon it for
# it's implementation.  This is partly to avoid making unnecessary copies of
# the linked_inodes dictionary, and to keep the calling args simple.
def _linkable_inode_sets(linked_inodes):
    """Generate sets of inodes that can be connected.  Starts with a mapping of
    inode # keys, and set values, which are the inodes which are determined to
    be equal (and thus linkable) to the key inode."""

    remaining_inodes = linked_inodes.copy()
    # iterate once over each inode key, building a set of it's connected
    # inodes, by direct or indirect association
    for start_ino in linked_inodes:
        if start_ino not in remaining_inodes:
            continue
        result_set = set()
        pending = [start_ino]
        # We know this loop terminates because we always remove an item from
        # the pending list, and a key from the remaining_inodes dict.  Since no
        # additions are made to the remaining_inodes, eventually the pending
        # list must empty.
        while pending:
            ino = pending.pop()
            result_set.add(ino)
            try:
                connected_links = remaining_inodes.pop(ino)
                pending.extend(connected_links)
            except KeyError:
                pass
        yield result_set


def _namepairs_per_inode(d):
    """Yield namepairs for each value in the dictionary d"""
    # A dictionary of {filename:[namepair]}, ie. a filename and list of
    # namepairs.  Make a copy as d and it's list values may be modified between
    # yields.
    d = _copy.deepcopy(d)
    for filename, namepairs in d.items():
        for namepair in namepairs:
            yield namepair


def _is_already_hardlinked(st1, st2):
    """If two files have the same inode and are on the same device then they
    are already hardlinked."""
    result = (st1.st_ino == st2.st_ino and  # Inodes equal
              st1.st_dev == st2.st_dev)     # Devices equal
    return result


def file_has_been_modified(pathname, stat_info):
    """Return True if file is known to have been modified."""
    try:
        current_stat = _os.lstat(pathname)
    except OSError:
        error = _sys.exc_info()[1]
        _logging.error("Failed to stat: %s\n%s" % (pathname, error))
        return False

    # Check inode stats to see an indication that the file (or possibly the
    # inode) was updated.
    if (current_stat.st_mtime != stat_info.st_mtime or
        current_stat.st_size != stat_info.st_size or
        current_stat.st_mode != stat_info.st_mode or
        current_stat.st_uid != stat_info.st_uid or
        current_stat.st_gid != stat_info.st_gid):
        return False


def _humanize_number(number):
    if number >= 1024 ** 5:
        return ("%.3f PiB" % (number / (1024.0 ** 5)))
    if number >= 1024 ** 4:
        return ("%.3f TiB" % (number / (1024.0 ** 4)))
    if number >= 1024 ** 3:
        return ("%.3f GiB" % (number / (1024.0 ** 3)))
    if number >= 1024 ** 2:
        return ("%.3f MiB" % (number / (1024.0 ** 2)))
    if number >= 1024:
        return ("%.3f KiB" % (number / 1024.0))
    return ("%d bytes" % number)


def _humanized_number_to_bytes(s):
    """Parses numbers with size specifiers like 'k', 'm', 'g', or 't'.
    Deliberately ignores multi-letter abbrevs like 'kb' or 'kib'"""

    # Assumes string/bytes input
    if not s:
        int(s)  # Deliberately raise ValueError on empty input

    s = s.lower()
    multipliers = {'k': 1024,
                   'm': 1024**2,
                   'g': 1024**3,
                   't': 1024**4,
                   'p': 1024**5}

    last_char = s[-1]
    if last_char not in multipliers:
        return int(s)
    else:
        s = s[:-1]
        multiplier = multipliers[last_char]
        return multiplier * int(s)


def _content_digest(pathname):
    """Return a hash value based on all (or some) of a file"""
    # Currently uses just the first 8K of the file (same buffer size as
    # filecmp)

    if DEFAULT_LINEAR_SEARCH_THRESH is None:
        return None

    try:
        f = open(pathname, 'rb')
    except OSError:
        return None

    try:
        byte_data = f.read(8192)
    except OSError:
        return None
    finally:
        f.close()

    return (0xFFFFFFFF & _crc32(byte_data))


def main():
    # 'logging' package forces at least Python 2.3
    assert _sys.version_info >= (2, 3), ("%s requires at least Python 2.3" % _sys.argv[0])

    if _sys.version_info >= (2, 4):
        # logging.basicConfig in Python 2.3 accepted no args
        # Remove user from logging output
        _logging.basicConfig(format='%(levelname)s:%(message)s')

    # Parse our argument list and get our list of directories
    options, directories = _parse_command_line()

    hl = Hardlinkable(options)
    hl.run(directories)


if __name__ == '__main__':
    main()
