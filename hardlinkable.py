#!/usr/bin/python

# hardlinkable - Goes through a directory structure and reports files which are
# identical and could be hard-linked together.  Optionally performs the
# hardlinking.
#
# Copyright     2007 - 2018  Chad Netzer and contributors
# Copyright (C) 2003 - 2018  John L. Villalovos, Hillsboro, Oregon
#
# This program is free software; you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation; either version 2 of the License, or (at your option) any later
# version.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
# FITNESS FOR A PARTICULAR PURPOSE.  See the GNU General Public License for
# more details.
#
# You should have received a copy of the GNU General Public License along
# with this program; if not, write to the Free Software Foundation, Inc., 59
# Temple Place, Suite 330, Boston, MA  02111-1307, USA.

import filecmp
import fnmatch
import logging
import os
import re
import stat
import sys
import time

from optparse import OptionParser, OptionGroup, SUPPRESS_HELP


# Python 3 moved intern() to sys module
try:
    intern
except NameError:
    intern = sys.intern


# global declarations
OLD_VERBOSE_OPTION_ERROR = True

VERSION = "0.8 alpha - 2018-07-09 (09-Jul-2018)"

# Compile up our regexes ahead of time
MIRROR_PL_REGEX = re.compile(r'^\.in\.')
RSYNC_TEMP_REGEX = re.compile((r'^\..*\.\?{6,6}$'))


def parse_command_line():
    usage = "usage: %prog [options] directory [ directory ... ]"
    version = "%prog: " + VERSION
    description = """\
This is a tool to scan directories and report identical files that could be
hard-linked together in order to save space.  Linked files can save space, but
a change to one hardlinked file changes them all."""

    parser = OptionParser(usage=usage, version=version, description=description)
    parser.add_option("--enable-linking", dest="linking_enabled",
                      help="Perform the actual hardlinking",
                      action="store_true", default=False,)

    parser.add_option("-q", "--no-stats", dest="printstats",
                      help="Do not print the statistics",
                      action="store_false", default=True,)

    parser.add_option("-v", "--verbose", dest="verbosity",
                      help="Increase verbosity level (Repeatable up to 3 times)",
                      action="count", default=0,)

    # hidden debug option, each repeat increases debug level (long option only)
    parser.add_option("-d", "--debug", dest="debug_level",
                      help=SUPPRESS_HELP,
                      action="count", default=0,)

    properties_description= """
File content must always match exactly.  By default, ownership, permissions,
and mtime must also match.
Use --content-only with caution, as it can lead to surprising results,
including files becoming owned by another user.
"""
    group = OptionGroup(parser, title="File Matching",
            description=properties_description,)
    parser.add_option_group(group)

    group.add_option("-c", "--content-only", dest="contentonly",
                     help="Only file contents have to match",
                     action="store_true", default=False,)

    group.add_option("-f", "--filenames-equal", dest="samename",
                     help="Filenames have to be identical",
                     action="store_true", default=False,)

    group.add_option("-s", "--min-size", dest="min_file_size", type="int",
                     help="Minimum file size",
                     action="store", default=0,)

    group.add_option("-S", "--max-size", dest="max_file_size", type="int",
                     help="Maximum file size",
                     action="store", default=0,)

    group.add_option("-t", "--ignore-timestamp", dest="notimestamp",
                     help="File modification times do NOT have to be identical",
                     action="store_true", default=False,)

    group.add_option("--timestamp-ignore",
                     dest="deprecated_timestamp_option_name",
                     help=SUPPRESS_HELP,
                     action="store_true", default=False,)

    # Can't think of a good short option.  Should be used rarely anyway.
    group.add_option("--ignore-permissions", dest="nosameperm",
                     help="File permissions do not need to match",
                     action="store_true", default=False,)

    group = OptionGroup(parser, title="Name Matching",)
    parser.add_option_group(group)

    group.add_option("-m", "--match", dest="matches", metavar="PATTERN",
                     help="Shell patterns used to match files (may specify multiple times)",
                     action="append", default=[],)

    group.add_option("-x", "--exclude", dest="excludes", metavar="REGEX",
                     help="Regular expression used to exclude files/dirs (may specify multiple times)",
                     action="append", default=[],)

    (options, args) = parser.parse_args()
    if not args:
        parser.print_help()
        parser.error("Must supply one or more directories")
    args = [os.path.abspath(os.path.expanduser(dirname)) for dirname in args]
    for dirname in args:
        if not os.path.isdir(dirname):
            parser.error("%s is NOT a directory" % dirname)
    if options.min_file_size < 0:
        parser.error("--min_size cannot be negative")
    if options.max_file_size < 0:
        parser.error("--max_size cannot be negative")
    if options.max_file_size and options.max_file_size < options.min_file_size:
        parser.error("--max_size cannot be smaller than --min_size")

    # If linking is enabled, output a message early to indicate what is
    # happening in case the program is set to zero verbosity and is taking a
    # long time doing comparisons with no output.  It's helpful to know
    # definitively that the program is set to modify the filesystem.
    if options.linking_enabled:
        print("----- Hardlinking enabled.  The filesystem will be modified -----")

    # Accept --timestamp-ignore for backwards compatibility
    if options.deprecated_timestamp_option_name:
        logging.warning("Enabling --ignore-timestamp. "
                        "Option name --timestamp-ignore is deprecated.")
        options.notimestamp = True
        del options.deprecated_timestamp_option_name

    if OLD_VERBOSE_OPTION_ERROR:
        # When old style verbose options (-v 1) are parsed using the new
        # verbosity option (as a counter), the numbers end up being interpreted
        # as directories.  As long as the directories don't exist, the program
        # will catch this and exit.  However, if there so happens to be a
        # directory with a typical number value (ie. '0', '1', etc.), it could
        # falsely be scanned for hardlinking.  So we directly check the
        # sys.argv list and explicitly disallow this case.
        #
        # This could also reject a technically valid case where a new style
        # verbosity argument is given, followed by a number-like directory name
        # that is intentionally meant to be scanned.  Since it seems rare, we
        # intentionally disallow it as protection against misinterpretation of
        # the old style verbose option argument.  Eventually, when enough time
        # has passed to assume that hardlinkable users have switched over to
        # the new verbosity argument, we can remove this safeguard.

        # Iterate over a reversed argument list, looking for options pairs of
        # type ['-v', '<num>']
        for i,s in enumerate(sys.argv[::-1]):
            if i == 0:
                continue
            n_str = sys.argv[-i]
            if s in ('-v', '--verbose') and n_str.isdigit():
                parser.error("Use of deprecated numeric verbosity option (%s)." % ('-v ' + n_str))

    return options, args


class Hardlinkable:
    def __init__(self, options):
        self.options = options
        self.stats = Statistics(options)
        self._fsdevs = {}

    def linkables(self, directories):
        """Yield pairs of linkable pathnames in the given directories"""
        for (src_tup, dest_tup) in self._sorted_links(directories):
            src_namepair = src_tup[:2]
            dest_namepair = dest_tup[:2]
            src_pathname = os.path.join(*src_namepair)
            dest_pathname = os.path.join(*dest_namepair)

            yield (src_pathname, dest_pathname)

    def run(self, directories):
        """Run link scan, and perform linking if requested.  Return stats."""
        for (src_tup, dest_tup) in self._sorted_links(directories):
            hardlink_succeeded = False
            if self.options.linking_enabled:
                # DO NOT call hardlink_files() unless link creation
                # is selected. It unconditionally performs links.
                hardlink_succeeded = self._hardlink_files(src_tup, dest_tup)

            if not self.options.linking_enabled or hardlink_succeeded:
                assert src_tup[3] == dest_tup[3]
                fsdev = src_tup[3]

                src_namepair, dest_namepair = src_tup[:2], dest_tup[:2]
                src_ino, dest_ino = src_tup[2], dest_tup[2]

                src_stat_info = fsdev.ino_stat[src_ino]
                dest_stat_info = fsdev.ino_stat[dest_ino]

                self.stats.did_hardlink(src_namepair, dest_namepair, dest_stat_info)

                self._update_stat_info(src_stat_info, nlink=src_stat_info.st_nlink + 1)
                self._update_stat_info(dest_stat_info, nlink=dest_stat_info.st_nlink - 1)
                fsdev._move_linked_namepair(dest_namepair, src_ino, dest_ino)

        if self.options.printstats:
            self.stats.print_stats()

        # double check figures based on direct inode stats
        postlink_inode_stats = self._inode_stats()
        totalsavedbytes = self.stats.bytes_saved_thisrun + self.stats.bytes_saved_previously
        bytes_saved_thisrun = postlink_inode_stats['total_saved_bytes'] - self._prelink_inode_stats['total_saved_bytes']
        assert totalsavedbytes == postlink_inode_stats['total_saved_bytes'], (totalsavedbytes, postlink_inode_stats['total_saved_bytes'])
        assert self.stats.bytes_saved_thisrun == bytes_saved_thisrun

        return self.stats

    def matched_file_info(self, directories):
        """Yield (dirname, filename, stat_info) triplets for all non-excluded/matched files"""
        options = self.options
        gStats = self.stats

        # Now go through all the directories that have been added.
        for top_dir in directories:
            # Use topdown=True for directory search pruning. followlinks is False
            for dirpath, dirs, filenames in os.walk(top_dir, topdown=True):
                assert dirpath

                # If excludes match any of the subdirs (or the current dir), skip
                # them.
                cull_excluded_directories(dirs, options.excludes)
                cur_dir = os.path.basename(dirpath)
                if cur_dir and found_excluded(cur_dir, options.excludes):
                    continue

                gStats.found_directory()

                # Loop through all the files in the directory
                for filename in filenames:
                    assert filename
                    if found_excluded(filename, options.excludes):
                        continue
                    if found_excluded_dotfile(filename):
                        continue
                    if not found_matched_filename(filename, options.matches):
                        continue

                    pathname = os.path.normpath(os.path.join(dirpath, filename))
                    try:
                        stat_info = os.lstat(pathname)
                    except OSError:
                        error = sys.exc_info()[1]
                        logging.warning("Unable to get stat info for: %s\n%s" % (pathname, error))
                        continue

                    # Is it a regular file?
                    assert not stat.S_ISDIR(stat_info.st_mode)
                    if not stat.S_ISREG(stat_info.st_mode):
                        continue

                    # Is the file within the selected size range?
                    if ((options.max_file_size and
                         stat_info.st_size > options.max_file_size) or
                        (stat_info.st_size < options.min_file_size)):
                        continue

                    if stat_info.st_dev not in self._fsdevs:
                        # Try to discover the maximum number of nlinks possible for
                        # each new device.
                        try:
                            max_nlinks = os.pathconf(pathname, "PC_LINK_MAX")
                        except:
                            # Avoid retrying if PC_LINK_MAX fails for a device
                            max_nlinks = None
                        fsdev = self._get_fsdev(stat_info.st_dev)
                        fsdev.max_nlinks = max_nlinks

                    # Bump statistics count of regular files found.
                    gStats.found_regular_file()
                    if options.debug_level > 3:
                        print("File: %s" % pathname)

                    # Extract the normalized path directory name
                    dirname = os.path.dirname(pathname)

                    # Try to save space on redundant dirname and filename
                    # storage by interning
                    dirname = intern(dirname)
                    filename = intern(filename)
                    yield (dirname, filename, stat_info)

    def _get_fsdev(self, st_dev, max_nlinks=None):
        """Return an FSDev for given stat_info.st_dev"""
        fsdev = self._fsdevs.get(st_dev, None)
        if fsdev is None:
            fsdev = FSDev(st_dev, max_nlinks)
            self._fsdevs[st_dev] = fsdev
        return fsdev

    def _sorted_links(self, directories):
        """Perform the walk, collect and sort linking data, and yield link tuples."""
        for dirname, filename, stat_info in self.matched_file_info(directories):
            self._find_identical_files(dirname, filename, stat_info)

        self._prelink_inode_stats = self._inode_stats()
        for fsdev in self._fsdevs.values():
            for linkable_set in linkable_inode_sets(fsdev.linked_inodes):
                nlinks_list = [(fsdev.ino_stat[ino].st_nlink, ino) for ino in linkable_set]
                nlinks_list.sort(reverse=True)
                assert len(nlinks_list) > 1
                while len(nlinks_list) > 1:
                    # Ensure we don't try to combine inodes that would create
                    # more links than the maximum allowed nlinks, by looping
                    # until src + dest nlink < max_nlinks
                    #
                    # Every loop shortens the nlinks_list, so the loop will
                    # terminate.
                    src_nlink, src_ino = nlinks_list[0]
                    nlinks_list = nlinks_list[1:]
                    src_dirname, src_filename = fsdev._arbitrary_namepair_from_ino(src_ino)
                    while nlinks_list:
                        # Always removes last element, so loop must terminate
                        dest_nlink, dest_ino = nlinks_list.pop()
                        assert src_nlink >= dest_nlink
                        if (fsdev.max_nlinks is not None and
                            src_nlink + dest_nlink > fsdev.max_nlinks):
                            nlinks_list = nlinks_list[1:]
                            break
                        # Keep track of src/dest nlinks so that we can ensure
                        # we don't exceed the max_nlinks for the device.  We
                        # return the unmodified stat_infos because they may end
                        # up just being reported, not actually linked.
                        for dest_dirname, dest_filename in namepairs_per_inode(fsdev.ino_pathnames[dest_ino]):
                            src_tup = (src_dirname, src_filename, src_ino, fsdev)
                            dest_tup = (dest_dirname, dest_filename, dest_ino, fsdev)
                            yield (src_tup, dest_tup)
                            src_nlink += 1
                            dest_nlink -= 1
                            assert dest_nlink >= 0

    def _inode_stats(self):
        total_inodes = 0
        total_bytes = 0
        total_saved_bytes = 0 # Each nlink > 1 is counted as "saved" space
        for fsdev in self._fsdevs.values():
            for ino, stat_info in fsdev.ino_stat.items():
                total_inodes += 1
                total_bytes += stat_info.st_size
                count = fsdev._count_nlinks_this_inode(ino)
                total_saved_bytes += (stat_info.st_size * (count - 1))
        return {'total_inodes' : total_inodes,
                'total_bytes': total_bytes,
                'total_saved_bytes': total_saved_bytes}

    # dirname is the directory component and filename is just the file name
    # component (ie. the basename) without the path.  The tree walking provides
    # this, so we don't have to extract it with os.path.split()
    def _find_identical_files(self, dirname, filename, stat_info):
        options = self.options
        gStats = self.stats

        fsdev = self._get_fsdev(stat_info.st_dev)
        ino = stat_info.st_ino
        file_info = (dirname, filename, stat_info)
        namepair = (dirname, filename)

        file_hash = hash_value(stat_info, options)
        if file_hash in fsdev.file_hashes:
            gStats.found_hash()
            # See if the new file has the same inode as one we've already seen.
            if ino in fsdev.ino_stat:
                prev_namepair = fsdev._arbitrary_namepair_from_ino(ino)
                pathname = os.path.join(dirname, filename)
                if options.debug_level > 2:
                    prev_pathname = os.path.join(*prev_namepair)
                    print("Existing link: %s" % prev_pathname)
                    print("        with : %s" % pathname)
                prev_stat_info = fsdev.ino_stat[ino]
                gStats.found_existing_hardlink(prev_namepair, namepair, prev_stat_info)
            # We have file(s) that have the same hash as our current file.
            # Let's go through the list of files with the same hash and see if
            # we are already hardlinked to any of them.
            found_cached_ino = (ino in fsdev.file_hashes[file_hash])
            if (not found_cached_ino or
                (options.samename and not fsdev._ino_has_filename(ino, filename))):
                # We did not find this file as hardlinked to any other file
                # yet.  So now lets see if our file should be hardlinked to any
                # of the other files with the same hash.
                for cached_ino in fsdev.file_hashes[file_hash]:
                    gStats.inc_hash_list_iteration()
                    if (options.samename and not fsdev._ino_has_filename(cached_ino, filename)):
                        continue

                    if options.samename:
                        cached_file_info = fsdev._fileinfo_from_ino(cached_ino, filename)
                    else:
                        cached_file_info = fsdev._fileinfo_from_ino(cached_ino)
                    if self._are_files_hardlinkable(cached_file_info, file_info):
                        self._found_hardlinkable_file(cached_file_info, file_info)
                        break
                else:  # nobreak
                    # The file should NOT be hardlinked to any of the other
                    # files with the same hash.  So we will add it to the list
                    # of files.
                    fsdev.file_hashes[file_hash].add(ino)
                    fsdev.ino_stat[ino] = stat_info
                    gStats.no_hash_match()
        else: # if file_hash NOT in file_hashes
            # There weren't any other files with the same hash value so we will
            # create a new entry and store our file.
            fsdev.file_hashes[file_hash] = set([ino])
            assert ino not in fsdev.ino_stat
            gStats.missed_hash()

        fsdev.ino_stat[ino] = stat_info
        fsdev._ino_append_namepair(ino, filename, namepair)

    # Determine if a file is eligibile for hardlinking.  Files will only be
    # considered for hardlinking if this function returns true.
    def _eligible_for_hardlink(self, st1, st2):
        options = self.options
        result = (
            # Must meet the following
            # criteria:
            not is_already_hardlinked(st1, st2) and  # NOT already hard linked

            st1.st_size == st2.st_size and           # size is the same

            st1.st_size != 0 and                     # size is not zero

            (st1.st_mode == st2.st_mode or           # file mode is the same
             options.nosameperm or                   # OR we are ignoring file mode
             options.contentonly) and                # OR we are comparing content only

            (st1.st_uid == st2.st_uid or             # owner user id is the same
             options.contentonly) and                # OR we are comparing content only

            (st1.st_gid == st2.st_gid or             # owner group id is the same
             options.contentonly) and                # OR we are comparing content only

            (st1.st_mtime == st2.st_mtime or         # modified time is the same
             options.notimestamp or                  # OR date hashing is off
             options.contentonly) and                # OR we are comparing content only

            st1.st_dev == st2.st_dev                 # device is the same
        )
        fsdev = self._get_fsdev(st1.st_dev)
        if result and (fsdev.max_nlinks is not None):
            # The justification for not linking a pair of files if their nlinks sum
            # to more than the device maximum, is that linking them won't change
            # the overall link count, meaning no space saving is possible overall
            # even when all their filenames are found and re-linked.
            result = ((st1.st_nlink + st2.st_nlink) <= fsdev.max_nlinks)
        return result

    def _are_file_contents_equal(self, pathname1, pathname2):
        """Determine if the contents of two files are equal"""
        options = self.options
        gStats = self.stats
        if options.debug_level > 1:
            print("Comparing: %s" % pathname1)
            print("     to  : %s" % pathname2)
        gStats.did_comparison()
        return filecmp.cmp(pathname1, pathname2, shallow=False)

    # Determines if two files should be hard linked together.
    def _are_files_hardlinkable(self, file_info1, file_info2):
        options = self.options
        gStats = self.stats

        dirname1,filename1,stat1 = file_info1
        dirname2,filename2,stat2 = file_info2
        assert not options.samename or filename1 == filename2
        if not self._eligible_for_hardlink(stat1, stat2):
            result = False
        else:
            result = self._are_file_contents_equal(os.path.join(dirname1,filename1),
                                                   os.path.join(dirname2,filename2))
        return result

    def _found_hardlinkable_file(self, source_file_info, dest_file_info):
        source_dirname, source_filename, source_stat_info = source_file_info
        dest_dirname, dest_filename, dest_stat_info = dest_file_info

        assert source_stat_info.st_dev == dest_stat_info.st_dev
        fsdev = self._get_fsdev(source_stat_info.st_dev)
        fsdev._add_linked_inodes(source_stat_info.st_ino, dest_stat_info.st_ino)

        # update our stats (Note: dest_stat_info is from pre-link())
        if self.options.debug_level > 0:
            source_pathname = os.path.join(source_dirname, source_filename)
            dest_pathname = os.path.join(dest_dirname, dest_filename)
            assert source_pathname != dest_pathname
            print("Linkable: %s" % source_pathname)
            print("      to: %s" % dest_pathname)

    def _update_stat_info(self, stat_info, nlink=None, mtime=None, atime=None, uid=None, gid=None):
        """Updates an ino_stat stat_info with the given values."""
        l = list(stat_info)
        if nlink is not None:
            l[stat.ST_NLINK] = nlink
        if mtime is not None:
            l[stat.ST_MTIME] = mtime
        if atime is not None:
            l[stat.ST_ATIME] = atime
        if uid is not None:
            l[stat.ST_UID] = uid
        if gid is not None:
            l[stat.ST_GID] = gid

        fsdev = self._get_fsdev(stat_info.st_dev)
        fsdev.ino_stat[stat_info.st_ino] = stat_info.__class__(l)
        if fsdev.ino_stat[stat_info.st_ino].st_nlink < 1:
            assert fsdev.ino_stat[stat_info.st_ino].st_nlink == 0
            del fsdev.ino_stat[stat_info.st_ino]

    def _updated_file_info(self, file_info):
        """Return a file_info tuple with the current stat_info value."""
        dirname, filename, stat_info = file_info
        fsdev = self._get_fsdev(stat_info.st_dev)
        new_file_info = (dirname, filename, fsdev.ino_stat[stat_info.st_ino])
        return new_file_info

    def _hardlink_files(self, source_tup, dest_tup):
        """Actually perform the filesystem hardlinking of two files."""
        source_dirname, source_filename, source_ino, source_fsdev = source_tup
        dest_dirname, dest_filename, dest_ino, dest_fsdev = dest_tup
        source_pathname = os.path.join(source_dirname, source_filename)
        dest_pathname = os.path.join(dest_dirname, dest_filename)

        hardlink_succeeded = False
        # rename the destination file to save it
        temp_pathname = dest_pathname + ".$$$___cleanit___$$$"
        try:
            os.rename(dest_pathname, temp_pathname)
        except OSError:
            error = sys.exc_info()[1]
            logging.error("Failed to rename: %s to %s\n%s" % (dest_pathname, temp_pathname, error))
        else:
            # Now link the sourcefile to the destination file
            try:
                os.link(source_pathname, dest_pathname)
            except Exception:
                error = sys.exc_info()[1]
                logging.error("Failed to hardlink: %s to %s\n%s" % (source_pathname, dest_pathname, error))
                # Try to recover
                try:
                    os.rename(temp_pathname, dest_pathname)
                except Exception:
                    error = sys.exc_info()[1]
                    logging.critical("Failed to rename temp filename %s back to %s\n%s" % (temp_pathname, dest_pathname, error))
                    sys.exit(3)
            else:
                hardlink_succeeded = True

                # Delete the renamed version since we don't need it.
                try:
                    os.unlink(temp_pathname)
                except Exception:
                    error = sys.exc_info()[1]
                    # Failing to remove the temp file could lead to endless
                    # attempts to link to it in the future.
                    logging.critical("Failed to remove temp filename: %s\n%s" % (temp_pathname, error))
                    sys.exit(3)

                source_stat_info = source_fsdev.ino_stat[source_ino]
                dest_stat_info = dest_fsdev.ino_stat[dest_ino]

                # Use the destination file attributes if it's most recently modified
                dest_mtime = dest_atime = dest_uid = dest_gid = None
                if dest_stat_info.st_mtime > source_stat_info.st_mtime:
                    try:
                        os.utime(source_pathname, (dest_stat_info.st_atime, dest_stat_info.st_mtime))
                        dest_atime = dest_stat_info.st_atime
                        dest_mtime = dest_stat_info.st_mtime
                    except Exception:
                        error = sys.exc_info()[1]
                        logging.warning("Failed to update file time attributes for %s\n%s" % (source_pathname, error))

                    try:
                        os.chown(source_pathname, dest_stat_info.st_uid, dest_stat_info.st_gid)
                        dest_uid = dest_stat_info.st_uid
                        dest_gid = dest_stat_info.st_gid
                    except Exception:
                        error = sys.exc_info()[1]
                        logging.warning("Failed to update file owner attributes for %s\n%s" % (source_pathname, error))

                    self._update_stat_info(source_stat_info,
                                           mtime=dest_mtime,
                                           atime=dest_atime,
                                           uid=dest_uid,
                                           gid=dest_gid)
        return hardlink_succeeded


class FSDev:
    """Per filesystem (ie. st_dev) operations"""
    def __init__(self, st_dev, max_nlinks):
        self.st_dev = st_dev
        self.max_nlinks = max_nlinks  # Can be None

        # For each hash value, track inode (and optionally filename)
        # file_hashes <- {hash_val: set(ino)}
        self.file_hashes = {}

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

    def _arbitrary_namepair_from_ino(self, ino):
        # Get the dict of filename: [pathnames] for ino_key
        d = self.ino_pathnames[ino]
        # Get an arbitrary pathnames list
        l = next(iter(d.values()))
        return l[0]

    def _ino_append_namepair(self, ino, filename, namepair):
        d = self.ino_pathnames.setdefault(ino, {})
        l = d.setdefault(filename, [])
        l.append(namepair)

    def _fileinfo_from_ino(self, ino, filename=None):
        """When filename is None, chooses an arbitrary namepair linked to the inode"""
        if filename is not None:
            assert ino in self.ino_pathnames
            assert filename in self.ino_pathnames[ino]
            l = self.ino_pathnames[ino][filename]
            dirname, filename = l[0]
        else:
            dirname, filename = self._arbitrary_namepair_from_ino(ino)
        return (dirname, filename, self.ino_stat[ino])

    def _ino_has_filename(self, ino, filename):
        """Return true if the given ino has 'filename' linked to it."""
        return (filename in self.ino_pathnames[ino])

    def _add_linked_inodes(self, ino1, ino2):
        """Adds to the dictionary of ino1 to ino2 mappings."""
        assert ino1 != ino2
        s = self.linked_inodes.setdefault(ino1, set())
        s.add(ino2)
        s = self.linked_inodes.setdefault(ino2, set())
        s.add(ino1)

    def _move_linked_namepair(self, namepair, src_ino, dest_ino):
        """Move namepair from dest_ino to src_ino (yes, backwards)"""
        dirname, filename = namepair
        pathnames = self.ino_pathnames[dest_ino][filename]
        pathnames.remove(namepair)
        assert namepair not in pathnames
        if not pathnames:
            del self.ino_pathnames[dest_ino][filename]
        self._ino_append_namepair(src_ino, filename, namepair)

    def _count_nlinks_this_inode(self, ino):
        """Because of file matching and exclusions, the number of links that we
        care about may not equal the total nlink count for the inode."""
        # Count the number of links to this inode that we have discovered
        count = 0
        for pathnames in self.ino_pathnames[ino].values():
            count += len(pathnames)
        return count


class Statistics:
    def __init__(self, options):
        self.options = options
        self.dircount = 0                   # how many directories we find
        self.regularfiles = 0               # how many regular files we find
        self.comparisons = 0                # how many file content comparisons
        self.hardlinked_thisrun = 0         # hardlinks done this run
        self.nlinks_to_zero_thisrun = 0     # how man nlinks actually went to zero
        self.hardlinked_previously = 0      # hardlinks that are already existing
        self.bytes_saved_thisrun = 0        # bytes saved by hardlinking this run
        self.bytes_saved_previously = 0     # bytes saved by previous hardlinks
        self.hardlinkstats = []             # list of files hardlinked this run
        self.starttime = time.time()        # track how long it takes
        self.previouslyhardlinked = {}      # list of files hardlinked previously

        # Debugging stats
        self.num_hash_hits = 0              # Amount of times a hash is found in file_hashes
        self.num_hash_misses = 0            # Amount of times a hash is not found in file_hashes
        self.num_hash_mismatches = 0        # Times a hash is found, but is not a file match
        self.num_list_iterations = 0        # Number of iterations over a list in file_hashes

    def found_directory(self):
        self.dircount = self.dircount + 1

    def found_regular_file(self):
        self.regularfiles = self.regularfiles + 1

    def did_comparison(self):
        self.comparisons = self.comparisons + 1

    def found_existing_hardlink(self, source_namepair, dest_namepair, stat_info):
        assert len(source_namepair) == 2
        assert len(dest_namepair) == 2
        filesize = stat_info.st_size
        self.hardlinked_previously = self.hardlinked_previously + 1
        self.bytes_saved_previously = self.bytes_saved_previously + filesize
        if self.options.verbosity > 1:
            if source_namepair not in self.previouslyhardlinked:
                self.previouslyhardlinked[source_namepair] = (filesize, [dest_namepair])
            else:
                self.previouslyhardlinked[source_namepair][1].append(dest_namepair)

    def did_hardlink(self, src_namepair, dest_namepair, dest_stat_info):
        # nlink count is not necessarily accurate at the moment
        self.hardlinkstats.append((tuple(src_namepair),
                                   tuple(dest_namepair)))
        filesize = dest_stat_info.st_size
        self.hardlinked_thisrun = self.hardlinked_thisrun + 1
        if dest_stat_info.st_nlink == 1:
            # We only save bytes if the last destination link was actually
            # removed.
            self.bytes_saved_thisrun = self.bytes_saved_thisrun + filesize
            self.nlinks_to_zero_thisrun = self.nlinks_to_zero_thisrun + 1

    def found_hash(self):
        self.num_hash_hits += 1

    def missed_hash(self):
        """When a hash lookup isn't found"""
        self.num_hash_misses += 1

    def no_hash_match(self):
        """When a hash lookup succeeds, but no matching value found"""
        self.num_hash_mismatches += 1

    def inc_hash_list_iteration(self):
        self.num_list_iterations += 1

    def print_stats(self):
        print("Hard linking Statistics:")
        # Print out the stats for the files we hardlinked, if any
        if self.options.verbosity > 1 and self.previouslyhardlinked:
            keys = list(self.previouslyhardlinked.keys())
            keys.sort()  # Could use sorted() once we only support >= Python 2.4
            print("Files Previously Hardlinked:")
            for key in keys:
                size, file_list = self.previouslyhardlinked[key]
                print("Currently hardlinked: %s" % os.path.join(*key))
                for namepair in file_list:
                    pathname = os.path.join(*namepair)
                    print("                    : %s" % pathname)
                print("Size per file: %s  Total saved: %s" % (humanize_number(size),
                                                              humanize_number(size * len(file_list))))
            print("")
        if self.options.verbosity > 0 and self.hardlinkstats:
            if not self.options.linking_enabled:
                print("Files that are hardlinkable:")
            else:
                print("Files that were hardlinked this run:")
            for (source_namepair, dest_namepair) in self.hardlinkstats:
                print("from: %s" % os.path.join(*source_namepair))
                print("  to: %s" % os.path.join(*dest_namepair))
            print("")
        if not self.options.linking_enabled:
            print("Statistics reflect what would result if actual linking were enabled")
        print("Directories               : %s" % self.dircount)
        print("Regular files             : %s" % self.regularfiles)
        print("Comparisons               : %s" % self.comparisons)
        if self.options.linking_enabled:
            s1 = "Consolidated inodes       : %s"
            s2 = "Hardlinked this run       : %s"
        else:
            s1 = "Consolidatable inodes     : %s"
            s2 = "Hardlinkable files        : %s"
        print(s1 % self.nlinks_to_zero_thisrun)
        print(s2 % self.hardlinked_thisrun)
        print("Total hardlinks           : %s" % (self.hardlinked_previously + self.hardlinked_thisrun))
        if self.options.linking_enabled:
            s3 = "Additional bytes saved    : %s (%s)"
        else:
            s3 = "Additional bytes saveable : %s (%s)"
        print(s3 % (self.bytes_saved_thisrun, humanize_number(self.bytes_saved_thisrun)))
        totalbytes = self.bytes_saved_thisrun + self.bytes_saved_previously
        if self.options.linking_enabled:
            s4 = "Total bytes saved         : %s (%s)"
        else:
            s4 = "Total bytes saveable      : %s (%s)"
        print(s4 % (totalbytes, humanize_number(totalbytes)))
        print("Total run time            : %s seconds" % (time.time() - self.starttime))
        if self.options.debug_level > 0:
            print("Total file hash hits       : %s  misses: %s  sum total: %s" % (self.num_hash_hits,
                                                                                  self.num_hash_misses,
                                                                                  (self.num_hash_hits +
                                                                                   self.num_hash_misses)))
            print("Total hash mismatches      : %s  (+ total hardlinks): %s" % (self.num_hash_mismatches,
                                                                                    (self.num_hash_mismatches +
                                                                                     self.hardlinked_previously +
                                                                                     self.hardlinked_thisrun)))
            print("Total hash list iterations : %s" % self.num_list_iterations)


### Module functions ###

def hash_value(stat_info, options):
    """Return a value appropriate for a python dict or shelve key, which can
    differentiate files which cannot be hardlinked."""
    size = stat_info.st_size
    if options.notimestamp or options.contentonly:
        value = size
    else:
        mtime = int(stat_info.st_mtime)
        value = size ^ mtime

    return value


def cull_excluded_directories(dirs, excludes):
    """Remove any excluded directories from dirs.

    Note that it modifies dirs in place, as required by os.walk()
    """
    for dirname in dirs[:]:
        if found_excluded(dirname, excludes):
            try:
                dirs.remove(dirname)
            except ValueError:
                break
            # os.walk() will ensure no repeated dirnames
            assert dirname not in dirs


def found_excluded(name, excludes):
    """If excludes option is given, return True if name matches any regex."""
    for exclude in excludes:
        if re.search(exclude, name):
            return True
    return False


def found_excluded_dotfile(name):
    """Return True if any excluded dotfile pattern is found."""
    # Look at files beginning with "."
    if name.startswith("."):
        # Ignore any mirror.pl files.  These are the files that
        # start with ".in."
        if MIRROR_PL_REGEX.match(name):
            return True
        # Ignore any RSYNC files.  These are files that have the
        # format .FILENAME.??????
        if RSYNC_TEMP_REGEX.match(name):
            return True
    return False


def found_matched_filename(name, matches):
    """If matches option is given, return False if name doesn't match any
    patterns.  If no matches are given, return True."""
    if not matches:
        return True
    for match in matches:
        if fnmatch.fnmatch(name, match):
            return True
    return False


def linkable_inode_sets(linked_inodes):
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


def namepairs_per_inode(d):
    """Yield namepairs for each value in the dictionary d"""
    # A dictionary of {filename:[namepair]}, ie. a filename and list of
    # namepairs.
    for filename, namepairs in d.copy().items():
        for namepair in namepairs:
            yield namepair


# If two files have the same inode and are on the same device then they are
# already hardlinked.
def is_already_hardlinked(st1,     # first file's status
                          st2):    # second file's status
    result = (st1.st_ino == st2.st_ino and  # Inodes equal
              st1.st_dev == st2.st_dev)     # Devices equal
    return result


def humanize_number(number):
    if number > 1024 ** 3:
        return ("%.3f GiB" % (number / (1024.0 ** 3)))
    if number > 1024 ** 2:
        return ("%.3f MiB" % (number / (1024.0 ** 2)))
    if number > 1024:
        return ("%.3f KiB" % (number / 1024.0))
    return ("%d bytes" % number)


def main():
    # 'logging' package forces at least Python 2.3
    assert sys.version_info >= (2,3), ("%s requires at least Python 2.3" % sys.argv[0])

    if sys.version_info >= (2,4):
        # logging.basicConfig in Python 2.3 accepted no args
        # Remove user from logging output
        logging.basicConfig(format='%(levelname)s:%(message)s')

    # Parse our argument list and get our list of directories
    options, directories = parse_command_line()

    hl = Hardlinkable(options)
    hl.run(directories)


if __name__ == '__main__':
    main()
