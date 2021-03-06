#!/usr/bin/env python

# hardlinkable.py - Goes through a directory structure and reports files which
# are identical and could be hard-linked together.  Optionally performs the
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


# This version runs under Python 3, but still supports (or attempts to support)
# Python 2.3, which for example is the version provided with RHEL 4.  Support
# for such old versions (while simultaneously supporting 3), leads to some
# clunky coding practices at times, that could be made more elegant if support
# for older versions were dropped.
#
# Sometime after the first official, stable release it is likely that support
# for anything less than Python 2.7 will be dropped, allowing a number of
# cleanups of the code, but also providing those who still need a version that
# works with older Python releases to stick with a tested, working (though
# older) release.


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
from optparse import Values as _Values

try:
    from typing import Dict, Iterable, List, Optional, Set, Tuple, Union
    NamePair = Tuple[str, str]
    InoSet = Set[int]
except ImportError:
    # typing module not available for mypy.  Oh well.
    pass

try:
    from zlib import crc32 as _crc32
    DEFAULT_LINEAR_SEARCH_THRESH = 1  # type: Optional[int]
except ImportError:
    try:
        from binascii import crc32 as _crc32  # type: ignore
        DEFAULT_LINEAR_SEARCH_THRESH = 1
    except ImportError:
        DEFAULT_LINEAR_SEARCH_THRESH = None

try:
    import xattr  # type: ignore
except ImportError:
    xattr = None

try:
    import json  # type: ignore
except ImportError:
    try:
        import simplejson as json  # type: ignore
    except ImportError:
        json = None  # type: ignore

# Python 2.3 has the sets module, not the set type
try:
    set
except NameError:
    # Import of Set messes with mypy in --py2 mode
    from sets import Set as set  # type: ignore

# Python 3 moved intern() to sys module
try:
    _intern = intern  # type: ignore
except NameError:
    _intern = _sys.intern  # type: ignore

__all__ = ["Hardlinkable", "FileInfo", "LinkingStats", "get_default_parser_options"]

# global declarations
__version__ = '0.8'
_VERSION = "0.8 alpha - 2018-07-09 (09-Jul-2018)"


def get_default_parser_options():
    # type: () -> _Values
    options, args = _parse_command_line(get_default_options=True)
    return options


def _parse_command_line(get_default_options=False, show_progress_default=False):
    # type: (bool, bool) -> Tuple[_Values, List[str]]
    usage = "usage: %prog [options] directory [ directory ... ]"
    version = "%prog: " + _VERSION
    description = """\
This is a tool to scan directories and report on the space that could be saved
by hard linking identical files.  It can also perform the linking."""

    description += _missing_modules_str()

    formatter = _TitledHelpFormatter(max_help_position=26)
    parser = _OptionParser(usage=usage,
                           version=version,
                           description=description,
                           formatter=formatter)
    parser.add_option("--no-stats", dest="printstats",
                      help="Do not print the statistics",
                      action="store_false", default=True,)

    parser.add_option("-v", "--verbose", dest="verbosity",
                      help="Increase verbosity level (Up to 3 times)",
                      action="count", default=0,)

    parser.add_option("--enable-linking", dest="linking_enabled",
                      help="Perform the actual hardlinking",
                      action="store_true", default=False,)

    # Setup both --progress and --no-progress options, so that both are always
    # accepted.  Allows the default option to vary depending on isatty
    # detection, without having to change the command line when redirecting.
    progress_dest = "show_progress"
    no_progress_dest = "_dummy_show_progress"
    progress_help = no_progress_help = _SUPPRESS_HELP
    if show_progress_default:
        no_progress_help = "Disable progress output while processing"
        progress_dest, no_progress_dest = no_progress_dest, progress_dest
    else:
        progress_help = "Output progress information as the program proceeds"

    parser.add_option("--progress", dest=progress_dest,
                      help=progress_help,
                      action="store_true", default=False,)
    parser.add_option("--no-progress", dest=no_progress_dest,
                      help=no_progress_help,
                      action="store_false", default=True,)

    # Allow json output if json module is present
    if json is not None:
        parser.add_option("--json", dest="json_enabled",
                          help="Output results as JSON",
                          action="store_true", default=False,)

    # Do not print non-error output (overrides verbose)
    parser.add_option("--quiet", dest="quiet",
                      help=_SUPPRESS_HELP,
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

    if xattr is not None:
        group.add_option("--ignore-xattr", dest="ignore_xattr",
                         help="Xattrs do not need to match",
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

    group = _OptionGroup(parser,
                         title="Name Matching (may specify multiple times)",)
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
        _options_validation(parser, options)
        return options, []

    (options, args) = parser.parse_args()
    if not args:
        parser.print_help()
        _sys.stderr.write("\nMust supply one or more directories\n")
        _sys.exit(2)
    args = [_os.path.normpath(_os.path.expanduser(dirname)) for dirname in args]
    for dirname in args:
        if not _os.path.isdir(dirname):
            parser.error("%s is NOT a directory" % dirname)

    _options_validation(parser, options)

    return options, args


def _options_validation(parser, options):
    # type: (_OptionParser, _Values) -> None
    """Ensures given options are valid, and sets up options object"""
    if options.debug_level > 1:
        _logging.getLogger().setLevel(_logging.DEBUG)

    # Since mypy complains about missing attributes for options (the 'dest'
    # arguments), we ignore these errors below

    # Convert "humanized" size inputs to integer bytes
    try:
        options.min_file_size = _humanized_number_to_bytes(options.min_file_size)  # type: ignore
    except ValueError:
        parser.error("option -s: invalid integer value: '%s'" % options.min_file_size)
    if options.max_file_size is not None:
        try:
            options.max_file_size = _humanized_number_to_bytes(options.max_file_size)  # type: ignore
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
    if options.linking_enabled and not options.quiet:
        print("----- Hardlinking enabled.  The filesystem will be modified -----")

    # Verify that linear_search_thresh is an integer >= 0, or "none"
    if options.linear_search_thresh is not None:
        err_str = ("Invalid value '%s' for linear-search-thresh. "
                   "Should be a non-negative int")
        try:
            n = int(options.linear_search_thresh)
            if n < 0:
                parser.error(err_str % options.linear_search_thresh)
            options.linear_search_thresh = n  # type: ignore

        except ValueError:
            if options.linear_search_thresh.lower() == "none":
                options.linear_search_thresh = None  # type: ignore
            else:
                parser.error(err_str % options.linear_search_thresh)

    # Setup/reconcile output options (debugging is not overridden)
    if options.quiet:
        # Based on verbosity, enable extra stats storage when quiet option is
        # selected.  Useful with Hardlinkable objects directly.
        if options.verbosity > 1:
            options.store_old_hardlinks = True  # type: ignore
        if options.verbosity > 0:
            options.store_new_hardlinks = True  # type: ignore
        # Disable any remaining verbosity output
        options.verbosity = 0  # type: ignore
        options.show_progress = False  # type: ignore
        options.printstats = False  # type: ignore

    # Remove dummy show progress variable
    del options._dummy_show_progress


class Hardlinkable(object):
    """Allows scanning directories for hard-linkable files.  Can return
    iteratorable of pathname pairs that can be linked, statistics on what space
    would be saved by linking, and actually perform the linking if
    requested."""
    def __init__(self, options=None):
        # type: (Optional[_Values]) -> None
        if options is None:
            options = get_default_parser_options()
        self.options = options
        self.stats = LinkingStats(options)
        self.progress = _Progress(options, self.stats)
        self._fsdevs = {}  # type: Dict[int, _FSDev]

    def linkables(self, directories):
        # type: (List) -> Iterable[NamePair]
        """Yield pairs of linkable pathnames in the given directories"""
        for (src_fileinfo, dst_fileinfo) in self._linkable_fileinfo_pairs(directories):
            src_pathname = src_fileinfo.pathname()
            dst_pathname = dst_fileinfo.pathname()

            assert (not self.options.samename or
                    src_fileinfo.filename == dst_fileinfo.filename)
            yield (src_pathname, dst_pathname)

    def run(self, directories):
        # type: (List) -> LinkingStats
        """Run link scan, and perform linking if requested.  Return stats."""
        # Prevent 'directories' from accidentally being a stringlike or
        # byteslike.  We don't want to "walk" each string character as a dir,
        # especially since it has a good chance of starting with an '/'.
        if _sys.version_info[0] == 2:
            if isinstance(directories, basestring):  # type: ignore
                directories = [directories]
        elif isinstance(directories, str) or isinstance(directories, bytes):
            directories = [directories]

        for dirname in directories:
            if not _os.path.isdir(dirname):
                raise IOError("%s is not a directory" % dirname)

        aborted_early = False
        for (src_fileinfo, dst_fileinfo) in self._linkable_fileinfo_pairs(directories):
            assert (not self.options.samename or
                    src_fileinfo.filename == dst_fileinfo.filename)
            if self.options.linking_enabled:
                # DO NOT call hardlink_files() unless link creation
                # is selected. It unconditionally performs links.
                hardlink_succeeded = self._hardlink_files(src_fileinfo, dst_fileinfo)

                # If hardlinking fails, we assume the worst and abort early.
                # This is partly because it could mean the filesystem tree is
                # being modified underneath us, which we aren't prepared to
                # deal with.
                if not hardlink_succeeded:
                    _logging.error("Hardlinking failed. Aborting early... "
                                   "Statistics may be incomplete")
                    aborted_early = True
                    break

            assert not aborted_early

        self.stats.endtime = _time.time()

        if json is not None and self.options.json_enabled:
            if not self.options.quiet:
                print(json.dumps(self.stats.dict_results(aborted_early)))
        else:
            self.stats.output_results(aborted_early)

        if not aborted_early:
            self._postlink_inode_stats = self._inode_stats()
            self._inode_stats_sanity_check(self._prelink_inode_stats,
                                           self._postlink_inode_stats)

            # Store the inode stats with the LinkingStats, useful for testing
            self.stats.inode_stats = [self._prelink_inode_stats,
                                      self._postlink_inode_stats]

        return self.stats

    def matched_fileinfo(self, directories):
        # type: (List) -> Iterable[FileInfo]
        """Yield FileInfo for all non-excluded/matched files"""
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
                        statinfo = _os.lstat(pathname)
                    except OSError:
                        error = _sys.exc_info()[1]
                        _logging.warning("Unable to get stat info for: %s\n%s" % (pathname, error))
                        continue

                    # Is it a regular file?
                    assert not _stat.S_ISDIR(statinfo.st_mode)
                    if not _stat.S_ISREG(statinfo.st_mode):
                        continue

                    # Is the file within the selected size range?
                    if ((options.max_file_size is not None and
                         statinfo.st_size > options.max_file_size) or
                        (statinfo.st_size < options.min_file_size)):
                        self.stats.file_outside_size_range(pathname, statinfo.st_size)
                        continue

                    if statinfo.st_dev not in self._fsdevs:
                        # Try to discover the maximum number of nlinks possible for
                        # each new device.
                        try:
                            max_nlinks = _os.pathconf(pathname, "PC_LINK_MAX")  # type: Optional[int]
                        except OSError:
                            # Avoid retrying if PC_LINK_MAX fails for a device
                            max_nlinks = None
                        fsdev = self._get_fsdev(statinfo.st_dev)
                        fsdev.max_nlinks = max_nlinks

                    # Bump statistics count of regular files found.
                    self.stats.found_regular_file(pathname)

                    # Extract the normalized path directory name
                    dirname = _os.path.dirname(pathname)

                    # Try to save space on redundant dirname and filename
                    # storage by interning
                    dirname = _intern(dirname)
                    filename = _intern(filename)
                    yield FileInfo(dirname, filename, statinfo)

    def _linkable_fileinfo_pairs(self, directories):
        # type: (List) -> Iterable[Tuple[FileInfo, FileInfo]]
        """Perform the walk, collect and sort linking data, and yield linkable
        fileinfo pairs."""
        for fileinfo in self.matched_fileinfo(directories):
            self.progress.show_dirs_files_found()
            self._find_identical_files(fileinfo)

        self.progress.clear()
        self._prelink_inode_stats = self._inode_stats()
        for fsdev in self._fsdevs.values():
            for fileinfo_pair in fsdev.sorted_links(self.options, self.stats):
                yield fileinfo_pair
                self.progress.show_hardlinked_amount()
        self.progress.clear()

    def _find_identical_files(self, fileinfo):
        # type: (FileInfo) -> None
        """Add the given FileInfo to the internal state of which inodes are to
        be linked."""
        options = self.options

        statinfo = fileinfo.statinfo
        fsdev = self._get_fsdev(statinfo.st_dev)
        ino = statinfo.st_ino
        namepair = fileinfo.namepair()

        if ino not in fsdev.ino_stat:
            self.stats.found_inode()

        inode_hash = _stat_hash_value(statinfo, options)
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
                prev_statinfo = fsdev.ino_stat[ino]
                self.stats.found_existing_hardlink(prev_namepair, namepair, prev_statinfo)
            # We have file(s) that have the same hash as our current file.  If
            # our inode is already cached, we might be able to use past
            # comparison work to avoid further file comparisons, by looking to
            # see if it's an inode we've already seen and linked to others.
            inode_set = _linked_inode_set(ino, fsdev.linked_inodes)
            found_linked_ino = (len(inode_set & fsdev.inode_hashes[inode_hash]) > 0)
            if not found_linked_ino:
                cached_inodes_set = fsdev.inode_hashes[inode_hash]
                cached_inodes_seq = cached_inodes_set  # type: Union[InoSet, List[int]]
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
                search_thresh = options.linear_search_thresh
                use_content_digest = (search_thresh is not None and
                                      len(cached_inodes_set) > search_thresh)
                if use_content_digest:
                    digest = _content_digest(_os.path.join(*namepair))
                    # Revert to full search if digest can't be computed
                    if digest is not None:
                        if fileinfo.statinfo.st_ino not in fsdev.inodes_with_digest:
                            fsdev.add_content_digest(fileinfo, digest)
                            self.stats.computed_digest()

                        cached_inodes_no_digest = (cached_inodes_set -
                                                   fsdev.inodes_with_digest)
                        cached_inodes_same_digest = (cached_inodes_set &
                                                     fsdev.digest_inode_map[digest])
                        cached_inodes_different_digest = (cached_inodes_set -
                                                          cached_inodes_same_digest -
                                                          cached_inodes_no_digest)

                        assert len(cached_inodes_same_digest &
                                   cached_inodes_different_digest &
                                   cached_inodes_no_digest) == 0

                        # Search matching digest inos first (as they may have the
                        # same content).  Don't search those with differing digests
                        # at all (as they cannot be equal).
                        cached_inodes_seq = (list(cached_inodes_same_digest) +
                                             list(cached_inodes_no_digest))

                # We did not find this file as linked to any other cached
                # inodes yet.  So now lets see if our file should be hardlinked
                # to any of the other files with the same hash.
                self.stats.search_hash_list()
                for cached_ino in cached_inodes_seq:
                    self.stats.inc_hash_list_iteration()

                    cached_fileinfo = fsdev.fileinfo_from_ino(cached_ino)

                    if self._are_files_hardlinkable(cached_fileinfo,
                                                    fileinfo,
                                                    use_content_digest):
                        assert cached_fileinfo.statinfo.st_dev == fsdev.st_dev
                        fsdev.add_linked_inodes(cached_ino, ino)
                        break
                else:  # nobreak
                    self.stats.no_hash_match()
                    # The file should NOT be hardlinked to any of the other
                    # files with the same hash. Add to the list of unlinked
                    # inodes for this hash value.
                    fsdev.inode_hashes[inode_hash].add(ino)
                    fsdev.ino_stat[ino] = statinfo

        # Always add the new file to the stored inode information
        fsdev.ino_stat[ino] = statinfo
        fsdev.ino_append_namepair(ino, fileinfo.filename, namepair)

    def _hardlink_files(self, src_fileinfo, dst_fileinfo):
        # type: (FileInfo, FileInfo) -> bool
        """Actually perform the filesystem hardlinking of two files."""
        src_statinfo = src_fileinfo.statinfo
        dst_statinfo = dst_fileinfo.statinfo

        src_pathname = src_fileinfo.pathname()
        dst_pathname = dst_fileinfo.pathname()

        # Quit early if the src or dst files have been updated since we first
        # lstat()-ed them. The cached mtime needs to be kept up to date for
        # this to work correctly.
        if (_file_has_been_modified(src_pathname, src_statinfo) or
            _file_has_been_modified(dst_pathname, dst_statinfo)):
            return False

        hardlink_succeeded = False
        # rename the destination file to save it
        tmp_pathname = dst_pathname + "._tmp_while_linking"
        try:
            _os.rename(dst_pathname, tmp_pathname)
        except OSError:
            error = _sys.exc_info()[1]
            _logging.error("Failed to rename: %s to %s\n%s" %
                           (dst_pathname, tmp_pathname, error))
        else:
            # Now link the sourcefile to the destination file
            try:
                _os.link(src_pathname, dst_pathname)
            except Exception:
                error = _sys.exc_info()[1]
                _logging.error("Failed to hardlink: %s to %s\n%s" %
                               (src_pathname, dst_pathname, error))
                # Try to recover
                try:
                    _os.rename(tmp_pathname, dst_pathname)
                except Exception:
                    error = _sys.exc_info()[1]
                    _logging.critical("Failed to rename temp filename %s back to %s\n%s" %
                                      (tmp_pathname, dst_pathname, error))
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
                    _logging.critical("Failed to remove temp filename: %s\n%s" %
                                      (tmp_pathname, error))
                    _sys.exit(3)

                # Use the destination file times if it's most recently modified
                dst_mtime = dst_atime = None
                if dst_statinfo.st_mtime > src_statinfo.st_mtime:
                    try:
                        _os.utime(src_pathname, (dst_statinfo.st_atime,
                                                 dst_statinfo.st_mtime))
                        dst_atime = dst_statinfo.st_atime
                        dst_mtime = dst_statinfo.st_mtime
                    except Exception:
                        error = _sys.exc_info()[1]
                        _logging.warning("Failed to update file time attributes for %s\n%s" %
                                         (src_pathname, error))

                    self._updated_statinfo(src_statinfo,
                                           mtime=dst_mtime,
                                           atime=dst_atime)
        return hardlink_succeeded

    def _get_fsdev(self, st_dev, max_nlinks=None):
        # type: (int, Optional[int]) -> _FSDev
        """Return an FSDev for given statinfo.st_dev"""
        fsdev = self._fsdevs.get(st_dev, None)
        if fsdev is None:
            fsdev = _FSDev(st_dev, max_nlinks)
            self._fsdevs[st_dev] = fsdev
        return fsdev

    # Determine if a file is eligibile for hardlinking.  Files will only be
    # considered for hardlinking if this function returns true.
    def _eligible_for_hardlink(self, fileinfo1, fileinfo2):
        # type: (FileInfo, FileInfo) -> bool
        """Return True if inode meta-data would not preclude linking"""
        st1 = fileinfo1.statinfo
        st2 = fileinfo2.statinfo
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

            if xattr is not None and not options.ignore_xattr:
                pathname1 = fileinfo1.pathname()
                pathname2 = fileinfo2.pathname()
                xattr_result = _equal_xattr(pathname1, pathname2)
                result = result and xattr_result

        return result

    def _are_file_contents_equal(self, pathname1, pathname2):
        # type: (str, str) -> bool
        """Determine if the contents of two files are equal"""
        result = _filecmp.cmp(pathname1, pathname2, shallow=False)
        self.stats.did_comparison(pathname1, pathname2, result)
        return result

    # Determines if two files should be hard linked together.
    def _are_files_hardlinkable(self, fileinfo1, fileinfo2, use_digest):
        # type: (FileInfo, FileInfo, bool) -> bool
        """Return True if file contents and stat meta-data are equal"""
        if not self._eligible_for_hardlink(fileinfo1, fileinfo2):
            result = False
        else:
            # Since we are going to read the content anyway (to compare them),
            # there is no i/o penalty in calculating a content hash.
            stat1 = fileinfo1.statinfo
            stat2 = fileinfo2.statinfo
            if use_digest:
                fsdev = self._get_fsdev(stat1.st_dev)
                if fileinfo1.statinfo.st_ino not in fsdev.inodes_with_digest:
                    fsdev.add_content_digest(fileinfo1)
                    self.stats.computed_digest()

                if fileinfo2.statinfo.st_ino not in fsdev.inodes_with_digest:
                    fsdev.add_content_digest(fileinfo2)
                    self.stats.computed_digest()

            pathname1 = fileinfo1.pathname()
            pathname2 = fileinfo2.pathname()
            result = self._are_file_contents_equal(pathname1, pathname2)

            if result:
                # Record some stats when files are found to match, but stat
                # parameters are mismatched (such as in content-only mode).
                if stat1.st_mtime != stat2.st_mtime:
                    self.stats.found_mismatched_time()
                if stat1.st_mode != stat2.st_mode:
                    self.stats.found_mismatched_mode()
                if stat1.st_uid != stat2.st_uid:
                    self.stats.found_mismatched_uid()
                if stat1.st_gid != stat2.st_gid:
                    self.stats.found_mismatched_gid()
                if xattr is not None:
                    # Slower than stat mismatch data, but only done per-matched
                    # file
                    xattr_result = _equal_xattr(pathname1, pathname2)
                    if not xattr_result:
                        self.stats.found_mismatched_xattr()

        return result

    def _updated_statinfo(self,
                          statinfo,
                          nlink=None,
                          mtime=None,
                          atime=None,
                          uid=None,
                          gid=None):
        # type: (_os.stat_result, int, float, float, int, int) -> None
        """Updates an ino_stat statinfo with the given values."""
        fsdev = self._get_fsdev(statinfo.st_dev)
        return fsdev.updated_statinfo(statinfo.st_ino,
                                      nlink=nlink,
                                      mtime=mtime,
                                      atime=atime,
                                      uid=uid,
                                      gid=gid)

    def _inode_stats(self):
        # type: () -> Dict[str, int]
        """Gather some basic inode stats from caches."""
        total_inodes = 0
        total_bytes = 0  # st_nlinks * st_size
        total_nlinks = 0
        total_redundant_bytes = 0  # Each nlink > 1 is counted as "redundant" space
        total_path_links = 0  # Total number of found paths to inodes
        total_redundant_path_bytes = 0  # Only accounts for the seen paths to an inode
        for fsdev in self._fsdevs.values():
            for ino, statinfo in fsdev.ino_stat.items():
                total_inodes += 1
                total_bytes += statinfo.st_size

                # Total nlinks value can account for pathnames skipped, or
                # outside of the walked directory trees, etc.
                total_nlinks += statinfo.st_nlink
                total_redundant_bytes += (statinfo.st_size * (statinfo.st_nlink - 1))

                # path_count is merely the number of paths to an inode that
                # we've seen (ie. that weren't excluded or outside the
                # directory tree)
                path_count = fsdev.count_pathnames_this_inode(ino)
                total_path_links += path_count
                total_redundant_path_bytes += (statinfo.st_size * (path_count - 1))

        return {'total_inodes': total_inodes,
                'total_bytes': total_bytes,
                'total_nlinks': total_nlinks,
                'total_redundant_bytes': total_redundant_bytes,
                'total_path_links': total_path_links,
                'total_redundant_path_bytes': total_redundant_path_bytes}

    def _inode_stats_sanity_check(self, prelink_inode_stats, postlink_inode_stats):
        # type: (dict, dict) -> None
        """Check stats directly from inode data."""
        # double check figures based on direct inode stats
        totalsavedbytes = self.stats.bytes_saved_thisrun + self.stats.bytes_saved_previously
        bytes_saved_thisrun = (postlink_inode_stats['total_redundant_path_bytes'] -
                               prelink_inode_stats['total_redundant_path_bytes'])
        assert totalsavedbytes == postlink_inode_stats['total_redundant_path_bytes']
        assert self.stats.bytes_saved_thisrun == bytes_saved_thisrun


class FileInfo(object):
    """A class to hold pathname and stat/inode information."""
    __slots__ = 'dirname', 'filename', 'statinfo'

    def __init__(self, dirname, filename, statinfo):
        # type: (str, str, _os.stat_result) -> None
        self.dirname = dirname
        self.filename = filename
        self.statinfo = statinfo

    def __repr__(self):
        # type: () -> str
        """Return a representation of the FileInfo instance"""
        return "FileInfo(%s, %s, %s)" % (repr(self.dirname),
                                         repr(self.filename),
                                         repr(self.statinfo))

    def namepair(self):
        # type: () -> NamePair
        """Return a (dirname, filename) tuple."""
        return (self.dirname, self.filename)

    def pathname(self):
        # type: () -> str
        """Return a pathname"""
        return _os.path.join(self.dirname, self.filename)


class _FSDev(object):
    """Per filesystem (ie. st_dev) operations"""
    def __init__(self, st_dev, max_nlinks):
        # type: (int, Optional[int]) -> None
        self.st_dev = st_dev
        self.max_nlinks = max_nlinks

        # For each hash value, track inode (and optionally filename)
        self.inode_hashes = {}  # type: Dict[int, InoSet]

        # For each stat hash, keep a digest of the first 8K of content.  Used
        # to reduce linear search when looking through comparable files.
        self.digest_inode_map = {}  # type: Dict[int, InoSet]
        self.inodes_with_digest = set()  # type: InoSet

        # Keep track of per-inode stat info
        self.ino_stat = {}  # type: Dict[int, _os.stat_result]

        # For each inode, keep track of all the pathnames
        self.ino_pathnames = {}  # type: Dict[int, Dict[str, List[NamePair]]]

        # For each linkable file pair found, add their inodes as a pair (ie.
        # ultimately we want to "link" the inodes together).  Each pair is
        # added twice, in each order, so that a pair can be found from either
        # inode.
        self.linked_inodes = {}  # type: Dict[int, InoSet]

    def sorted_links(self, options, stats):
        # type: (_Values, LinkingStats) -> Iterable[Tuple[FileInfo, FileInfo]]
        """Generates pairs of linkeable FileInfos from the linked_inodes."""
        for linkable_set in _linkable_inode_sets(self.linked_inodes):
            # Decorate-sort-undecorate with st_link as primary key
            # Order inodes from greatest to least st_nlink
            nlinks_list = [(self.ino_stat[ino].st_nlink, ino) for ino in linkable_set]
            nlinks_list.sort()
            nlinks_list = nlinks_list[::-1]  # Reverse sort (Python 2.3 compat)
            ino_list = [x[1] for x in nlinks_list]  # strip nlinks sort key

            # Keep a list of inos from the end of the ino_list that cannot
            # be linked to (such as when in 'samename' mode), and reappend
            # them to nlist when the src inode advances.
            remaining_inos = []  # type: List[int]

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
                # Every loop shortens the ino_list, so the loop will
                # terminate.
                src_ino = ino_list[0]
                ino_list = ino_list[1:]
                while ino_list:  # inner while
                    # Always removes either first or last element, so loop
                    # must terminate
                    dst_ino = ino_list.pop()
                    src_statinfo = self.ino_stat[src_ino]
                    dst_statinfo = self.ino_stat[dst_ino]

                    # Samename can break nlink ordering invariant
                    assert (options.samename or
                            src_statinfo.st_nlink >= dst_statinfo.st_nlink)

                    # Ignore samename when checking max_nlink invariant
                    if (self.max_nlinks is not None and
                        src_statinfo.st_nlink + dst_statinfo.st_nlink > self.max_nlinks):
                        # Move inos to remaining_inos, so that src_ino will advance
                        remaining_inos.append(dst_ino)
                        remaining_inos.extend(ino_list[::-1])
                        ino_list = []
                        break

                    # Loop through all linkable pathnames in the last inode
                    p = self.ino_pathnames[dst_ino]
                    for dst_dirname, dst_filename in _namepairs_per_inode(p):
                        if (options.samename and
                            dst_filename not in self.ino_pathnames[src_ino]):
                            # Skip inodes without equal filenames in samename mode
                            assert dst_filename not in self.ino_pathnames[src_ino]
                            continue
                        lookup_filename = options.samename and dst_filename
                        src_namepair = self.arbitrary_namepair_from_ino(src_ino,
                                                                        lookup_filename)
                        src_dirname, src_filename = src_namepair
                        src_fileinfo = FileInfo(src_dirname, src_filename, src_statinfo)
                        dst_fileinfo = FileInfo(dst_dirname, dst_filename, dst_statinfo)

                        yield (src_fileinfo, dst_fileinfo)

                        # After yielding, we can update statinfo to
                        # account for hard-linking
                        stats.found_hardlinkable_files(src_fileinfo, dst_fileinfo)

                        new_src_nlink = src_statinfo.st_nlink + 1
                        new_dst_nlink = dst_statinfo.st_nlink - 1
                        src_statinfo = self.updated_statinfo(src_ino, nlink=new_src_nlink)
                        dst_statinfo = self.updated_statinfo(dst_ino, nlink=new_dst_nlink)
                        assert self.max_nlinks is None or src_statinfo.st_nlink <= self.max_nlinks
                        assert dst_statinfo is None or dst_statinfo.st_nlink > 0

                        dst_namepair = dst_fileinfo.namepair()
                        self.move_linked_namepair(dst_namepair, src_ino, dst_ino)

                    # if there are still pathnames to the dest inode, save
                    # it for possible linking later (for samename, mainly)
                    if self.ino_pathnames[dst_ino]:
                        remaining_inos.append(dst_ino)

    def arbitrary_namepair_from_ino(self, ino, filename=None):
        # type: (int, Optional[str]) -> NamePair
        """Return a (dirname, filename) tuple associated with the inode."""
        # Get the dict of filename: [pathnames] for ino_key
        d = self.ino_pathnames[ino]
        if filename:
            l = d[filename]
        else:
            # Get an arbitrary pathnames list (allowing pre-2.6 syntax)
            try:
                l = next(iter(d.values()))
            except NameError:
                l = iter(d.values()).next()  # type: ignore
        return l[0]

    def ino_append_namepair(self, ino, filename, namepair):
        # type: (int, str, NamePair) -> None
        """Add the (dirname, filename) tuple to the inode map (grouped by filename)"""
        d = self.ino_pathnames.setdefault(ino, {})
        l = d.setdefault(filename, [])
        l.append(namepair)

    def fileinfo_from_ino(self, ino):
        # type: (int) -> FileInfo
        """Return an arbitrary FileInfo associated with the given inode number."""
        dirname, filename = self.arbitrary_namepair_from_ino(ino)
        return FileInfo(dirname, filename, self.ino_stat[ino])

    def updated_statinfo(self,
            ino,         # type: int
            nlink=None,  # type: Optional[int]
            mtime=None,  # type: Optional[float]
            atime=None,  # type: Optional[float]
            uid=None,    # type: Optional[int]
            gid=None,    # type: Optional[int]
            ):
        """Updates an ino_stat statinfo with the given values."""
        statinfo = self.ino_stat[ino]
        l = list(statinfo)  # type: ignore
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

        new_statinfo = statinfo.__class__(l)
        self.ino_stat[ino] = new_statinfo
        if self.ino_stat[ino].st_nlink < 1:
            assert self.ino_stat[ino].st_nlink == 0
            del self.ino_stat[ino]
            new_statinfo = None
        return new_statinfo

    def add_linked_inodes(self, ino1, ino2):
        # type: (int, int) -> None
        """Adds to the dictionary of ino1 to ino2 mappings."""
        assert ino1 != ino2
        s = self.linked_inodes.setdefault(ino1, set())
        s.add(ino2)
        s = self.linked_inodes.setdefault(ino2, set())
        s.add(ino1)

    def move_linked_namepair(self, namepair, src_ino, dst_ino):
        # type: (NamePair, int, int) -> None
        """Move namepair from dst_ino to src_ino (yes, backwards)"""
        dirname, filename = namepair
        pathnames = self.ino_pathnames[dst_ino][filename]
        pathnames.remove(namepair)
        assert namepair not in pathnames
        if not pathnames:
            del self.ino_pathnames[dst_ino][filename]
        self.ino_append_namepair(src_ino, filename, namepair)

    def count_pathnames_this_inode(self, ino):
        # type: (int) -> int
        """Because of file matching and exclusions, or links to unwalked
        directory entries, the number of links that we care about may not equal
        the total nlink count for the inode."""
        # Count the number of links to this inode that we have discovered
        count = 0
        for pathnames in self.ino_pathnames[ino].values():
            count += len(pathnames)
        return count

    def add_content_digest(self, fileinfo, digest=None):
        # type: (FileInfo, Optional[int]) -> None
        """Store a given digest for an inode (or generate one if not provided)"""
        if digest is None:
            digest = _content_digest(fileinfo.pathname())
            if digest is None:
                return
        digests = self.digest_inode_map.get(digest, None)
        if digests is None:
            self.digest_inode_map[digest] = set([fileinfo.statinfo.st_ino])
        else:
            digests.add(fileinfo.statinfo.st_ino)
        self.inodes_with_digest.add(fileinfo.statinfo.st_ino)


class LinkingStats(object):
    def __init__(self, options):
        # type: (_Values) -> None
        self.options = options
        self.reset()

    def reset(self):
        # type: () -> None

        # Counter variables for number of directories and files found per run,
        # number of excluded/included dirs and files, how many file sizes are
        # outside of size range, and how many file contents are compared, etc.
        self.num_dirs = 0
        self.num_files = 0
        self.num_excluded_dirs = 0
        self.num_excluded_files = 0
        self.num_included_files = 0
        self.num_files_too_large = 0
        self.num_files_too_small = 0
        self.num_comparisons = 0
        self.num_equal_comparisons = 0

        # how man nlinks actually went to zero
        self.num_inodes_consolidated = 0
        self.num_inodes = 0

        # already existing hardlinks (based on walked dirs)
        self.num_hardlinked_previously = 0
        self.num_hardlinked_thisrun = 0

        # The 'mismatched' counters increment when a file with equal content
        # has been found (which was not rejected by the inode differences, such
        # as in 'content only' mode.
        self.num_mismatched_file_mtime = 0
        self.num_mismatched_file_mode = 0
        self.num_mismatched_file_uid = 0
        self.num_mismatched_file_gid = 0
        self.num_mismatched_file_xattr = 0

        # bytes saved by hardlinking this run (when st_nlink -> zero)
        self.bytes_saved_thisrun = 0

        # bytes saved by previous hardlinks (walked dirs only)
        self.bytes_saved_previously = 0

        # Time how long a run takes
        self.starttime = _time.time()
        self.endtime = None  # type: Optional[float]

        # Containers to store the new hardlinkable namepairs and
        # previously/currently linked namepairs found.
        self.hardlink_pairs = []  # type: List[Tuple[NamePair, NamePair]]
        self.currently_hardlinked = {}  # type: Dict[NamePair, Tuple[int, List[NamePair]]]

        # Debugging stats
        self.num_hash_hits = 0              # Amount of times a hash is found in inode_hashes
        self.num_hash_misses = 0            # Amount of times a hash is not found in inode_hashes
        self.num_hash_mismatches = 0        # Times a hash is found, but is not a file match
        self.num_hash_list_searches = 0     # Times a hash list search is initiated
        self.num_list_iterations = 0        # Number of iterations over a list in inode_hashes
        self.num_digests_computed = 0       # Number of times content digest was computed

        # sanity checking data
        self.inode_stats = []  # type: List[Dict[str, int]]

    def found_directory(self):
        # type: () -> None
        self.num_dirs += 1

    def found_regular_file(self, pathname):
        # type: (str) -> None
        self.num_files += 1
        if self.options.debug_level > 4:
            _logging.debug("File          : %s" % pathname)

    def excluded_dirs(self, dirname, basenames):
        # type: (str, Set[str]) -> None
        self.num_excluded_dirs += len(basenames)
        if self.options.debug_level > 5:
            for name in basenames:
                pathname = _os.path.join(dirname, name)
                _logging.debug("Excluded dir  : %s" % pathname)

    def excluded_dir(self, pathname):
        # type: (str) -> None
        self.num_excluded_dirs += 1
        if self.options.debug_level > 5:
            _logging.debug("Excluded dir  : %s" % pathname)

    def excluded_file(self, pathname):
        # type: (str) -> None
        self.num_excluded_files += 1
        if self.options.debug_level > 5:
            _logging.debug("Excluded file : %s" % pathname)

    def included_file(self, pathname):
        # type: (str) -> None
        self.num_included_files += 1
        if self.options.debug_level > 5:
            _logging.debug("Included file : %s" % pathname)

    def file_outside_size_range(self, pathname, filesize):
        # type: (str, int) -> None
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
        # type: () -> None
        self.num_mismatched_file_mtime += 1

    def found_mismatched_mode(self):
        # type: () -> None
        self.num_mismatched_file_mode += 1

    def found_mismatched_uid(self):
        # type: () -> None
        self.num_mismatched_file_uid += 1

    def found_mismatched_gid(self):
        # type: () -> None
        self.num_mismatched_file_gid += 1

    def found_mismatched_xattr(self):
        # type: () -> None
        self.num_mismatched_file_xattr += 1

    def did_comparison(self, pathname1, pathname2, result):
        # type: (str, str, bool) -> None
        self.num_comparisons += 1
        if result:
            self.num_equal_comparisons += 1
        if self.options.debug_level > 2:
            if result:
                _logging.debug("Compared equal: %s" % pathname1)
                _logging.debug(" to           : %s" % pathname2)
            else:
                _logging.debug("Compared      : %s" % pathname1)
                _logging.debug(" to           : %s" % pathname2)

    def found_existing_hardlink(self, src_namepair, dst_namepair, statinfo):
        # type: (NamePair, NamePair, _os.stat_result) -> None
        assert len(src_namepair) == 2
        assert len(dst_namepair) == 2
        if self.options.debug_level > 3:
            _logging.debug("Existing link : %s" % _os.path.join(*src_namepair))
            _logging.debug(" with         : %s" % _os.path.join(*dst_namepair))
        filesize = statinfo.st_size
        self.num_hardlinked_previously += 1
        self.bytes_saved_previously += filesize
        if (self.options.verbosity > 1 or
            getattr(self.options, 'store_old_hardlinks', False)):
            if src_namepair not in self.currently_hardlinked:
                self.currently_hardlinked[src_namepair] = (filesize, [dst_namepair])
            else:
                self.currently_hardlinked[src_namepair][1].append(dst_namepair)

    def found_hardlinkable_files(self, src_fileinfo, dst_fileinfo):
        # type: (FileInfo, FileInfo) -> None
        src_namepair = src_fileinfo.namepair()
        dst_namepair = dst_fileinfo.namepair()

        if self.options.debug_level > 1:
            assert src_namepair != dst_namepair
            _logging.debug("Linkable      : %s" % _os.path.join(*src_namepair))
            _logging.debug(" to           : %s" % _os.path.join(*dst_namepair))

        if (self.options.verbosity > 0 or
            getattr(self.options, 'store_new_hardlinks', False)):
            pair = (src_namepair, dst_namepair)
            self.hardlink_pairs.append(pair)

        self.num_hardlinked_thisrun += 1
        if dst_fileinfo.statinfo.st_nlink == 1:
            # We only save bytes if the last link was actually removed.
            self.bytes_saved_thisrun += dst_fileinfo.statinfo.st_size
            self.num_inodes_consolidated += 1

    def found_inode(self):
        # type: () -> None
        self.num_inodes += 1

    def found_hash(self):
        # type: () -> None
        self.num_hash_hits += 1

    def missed_hash(self):
        # type: () -> None
        """When a hash lookup isn't found"""
        self.num_hash_misses += 1

    def no_hash_match(self):
        # type: () -> None
        """When a hash lookup succeeds, but no matching value found"""
        self.num_hash_mismatches += 1

    def search_hash_list(self):
        # type: () -> None
        self.num_hash_list_searches += 1

    def inc_hash_list_iteration(self):
        # type: () -> None
        self.num_list_iterations += 1

    def computed_digest(self):
        # type: (int) -> None
        self.num_digests_computed += 1

    def _count_hardlinked_previously(self):
        # type: () -> int
        count = 0
        for filesize, namepairs in self.currently_hardlinked.values():
            count += len(namepairs)
        return count

    def dict_results(self, possibly_incomplete=False):
        """Destructively return the results as a dictionary, with namepairs
        converted to pathnames"""
        # Deletes currently_hardlinked and hardlink_pairs containers while
        # building new pathname containers, to save memory.  Could be
        # deepcopied first if required.
        stats_dict = _copy.copy(self.__dict__)
        del stats_dict['options']

        hardlink_pairs = stats_dict.pop('hardlink_pairs')
        if (self.options.verbosity > 0 or
            getattr(self.options, 'store_new_hardlinks', False)):
            hardlink_pathnames = []
            link_list = []
            # reverse initially, to build in the same order as the original
            hardlink_pairs = hardlink_pairs[::-1]
            while hardlink_pairs:
                src_namepair, dst_namepair = hardlink_pairs.pop()
                src_pathname, dst_pathname = (_os.path.join(*src_namepair),
                                              _os.path.join(*dst_namepair))
                # Output "compact" results, with multiple link destination
                # paths in the list after the initial source path
                if not link_list:
                    link_list = [src_pathname, dst_pathname]
                elif src_pathname != link_list[0]:
                    hardlink_pathnames.append(link_list)
                    link_list = [src_pathname, dst_pathname]
                else:
                    link_list.append(dst_pathname)
            if link_list:
                hardlink_pathnames.append(link_list)

        # Save space if verbosity doesn't indicate output of
        # currently_hardlinked
        currently_hardlinked = stats_dict.pop('currently_hardlinked')
        pathname_currently_hardlinked = {}
        if (self.options.verbosity > 1 or
            getattr(self.options, 'store_old_hardlinks', False)):
            while currently_hardlinked:
                namepair,value = currently_hardlinked.popitem()
                key = _os.path.join(*namepair)
                pathname_value = {'filesize': value[0], 'pathnames': []}
                for namepair in value[1]:
                    dst_pathname = _os.path.join(*namepair)
                    pathname_value['pathnames'].append(dst_pathname)

                pathname_currently_hardlinked[key] = pathname_value

        d = {}
        if self.options.verbosity > 1:
            d['currently_hardlinked'] = pathname_currently_hardlinked
        if self.options.verbosity > 0:
            desc_str = ("List-of-lists where inner lists are linkable paths, "
                    "with the first path the 'source' of each link, and the "
                    "remaining paths the destinations.")
            d['hardlink_pathnames_description'] = desc_str
            d['hardlink_pathnames'] = hardlink_pathnames
        if self.options.printstats:
            d['stats'] = stats_dict
        return d

    def output_results(self, possibly_incomplete=False):
        # type: (bool) -> None
        """Main output function after hardlink run completed"""
        if self.options.quiet and self.options.debug_level == 0:
            return

        if not self.options.quiet and possibly_incomplete:
            print("Results possibly incomplete due to errors")

        separator_needed = False
        if self.options.verbosity > 1 and self.currently_hardlinked:
            self.output_currently_linked()
            separator_needed = True

        if self.options.verbosity > 0 and self.hardlink_pairs:
            if separator_needed:
                print("")
            self.output_linked_pairs()
            separator_needed = True

        if self.options.printstats or self.options.debug_level > 0:
            if separator_needed:
                print("")
            self.print_stats()

    def output_currently_linked(self):
        # type: () -> None
        """Print out the already linked files that are found"""
        print("Currently hardlinked files")
        print("-----------------------")
        keys = list(self.currently_hardlinked.keys())
        keys.sort()  # Could use sorted() once we only support >= Python 2.4
        for key in keys:
            filesize, namepairs = self.currently_hardlinked[key]
            print("Currently hardlinked: %s" % _os.path.join(*key))
            for namepair in namepairs:
                pathname = _os.path.join(*namepair)
                print("                    : %s" % pathname)
            print("Filesize: %s  Total saved: %s" %
                  (_humanize_number(filesize),
                   _humanize_number(filesize * len(namepairs))))

    def output_linked_pairs(self):
        # type: () -> None
        """Print out the stats for the files we hardlinked, if any"""
        if self.options.linking_enabled:
            print("Files that were hardlinked this run")
        else:
            print("Files that are hardlinkable")
        print("-----------------------")
        prev_src_namepair = None
        for (src_namepair, dst_namepair) in self.hardlink_pairs:
            # Compactify output by combining multiple destinations in a row
            # with the same source
            if src_namepair != prev_src_namepair:
                print("from: %s" % _os.path.join(*src_namepair))
            print("  to: %s" % _os.path.join(*dst_namepair))
            prev_src_namepair = src_namepair

    def print_stats(self):
        # type: () -> None
        """Print statistics and data about the current run"""
        if self.endtime is None:
            self.endtime = _time.time()

        print("Hard linking statistics")
        print("-----------------------")
        if not self.options.linking_enabled:
            print("Statistics reflect what would result if actual linking were enabled")
        print("Directories                : %s" % self.num_dirs)
        print("Files                      : %s" % self.num_files)
        if self.options.linking_enabled:
            s1 = "Consolidated inodes        : %s"
            s2 = "Hardlinked this run        : %s"
        else:
            s1 = "Consolidatable inodes      : %s"
            s2 = "Hardlinkable files         : %s"
        print(s1 % self.num_inodes_consolidated)
        print(s2 % self.num_hardlinked_thisrun)
        print("Currently hardlinked bytes : %s (%s)" %
              (self.bytes_saved_previously, _humanize_number(self.bytes_saved_previously)))
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
        print("Total run time             : %s seconds" %
              round(self.endtime - self.starttime, 3))
        if self.options.verbosity > 0 or self.options.debug_level > 0:
            print("Comparisons                : %s" % self.num_comparisons)
            print("Inodes found               : %s" % self.num_inodes)
            print("Current hardlinks          : %s" % self.num_hardlinked_previously)
            print("Total old + new hardlinks  : %s" %
                  (self.num_hardlinked_previously + self.num_hardlinked_thisrun))
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
            if self.num_mismatched_file_mtime:
                print("Total file time mismatches : %s" % self.num_mismatched_file_mtime)
            if self.num_mismatched_file_mode:
                print("Total file mode mismatches : %s" % self.num_mismatched_file_mode)
            if self.num_mismatched_file_uid:
                print("Total file uid mismatches  : %s" % self.num_mismatched_file_uid)
            if self.num_mismatched_file_gid:
                print("Total file gid mismatches  : %s" % self.num_mismatched_file_gid)
            if self.num_mismatched_file_xattr:
                print("Total file xattr mismatches: %s" % self.num_mismatched_file_xattr)
            print("Total remaining inodes     : %s" %
                  (self.num_inodes - self.num_inodes_consolidated))
            assert (self.num_inodes - self.num_inodes_consolidated) >= 0
        if self.options.debug_level > 0:
            print("Total file hash hits       : %s  misses: %s  sum total: %s" %
                  (self.num_hash_hits, self.num_hash_misses,
                   (self.num_hash_hits + self.num_hash_misses)))
            print("Total hash mismatches      : %s  (+ total hardlinks): %s" %
                  (self.num_hash_mismatches,
                   (self.num_hash_mismatches +
                    self.num_hardlinked_previously +
                    self.num_hardlinked_thisrun)))
            print("Total hash searches        : %s" % self.num_hash_list_searches)
            if self.num_hash_list_searches == 0:
                avg_per_search = "N/A"  # type: Union[str, float]
            else:
                raw_avg = float(self.num_list_iterations) / self.num_hash_list_searches
                avg_per_search = round(raw_avg, 3)
            print("Total hash list iterations : %s  (avg per-search: %s)" %
                  (self.num_list_iterations, avg_per_search))
            print("Total equal comparisons    : %s" % self.num_equal_comparisons)
            print("Total digests computed     : %s" % self.num_digests_computed)


class _Progress(object):
    """Helps facilitate progress output repeatedly printed on the same line (ie. no scrolling)"""
    def __init__(self, options, stats):
        # type: (_Values, LinkingStats) -> None
        self.options = options
        self.stats = stats
        self.last_line_len = 0
        self.last_time = 0.0
        self.update_delay = 0.1
        self.dir_files_counter = 0
        self.counter_min = 11  # Prime number to make output values more dynamic
        self.last_n_fps = [0.0] * 10
        self.fps_index = 0  # Skip deque, and use a simple circular buffer

    def show_dirs_files_found(self):
        # type: () -> None
        if not self.options.show_progress:
            return

        # Allow progress updating only every counter_min iterations
        self.dir_files_counter += 1
        if self.dir_files_counter < self.counter_min:
            return
        else:
            self.dir_files_counter = 0

        # Also allow not updating before update_delay seconds have elapsed
        now = _time.time()
        time_since_last = now - self.last_time
        if time_since_last < self.update_delay:
            return
        self.last_time = now

        # Calculate some stats for progress output
        time_elapsed = now - self.stats.starttime
        num_dirs = self.stats.num_dirs
        num_files = self.stats.num_files
        fps = round(num_files/time_elapsed, 1)
        num_comparisons = self.stats.num_comparisons

        # Very simple running avg for prev fps (for smoothing)
        # Not even an attempt at anything sophisticated, just practical
        avg_fps = float(sum(self.last_n_fps))/len(self.last_n_fps)
        if fps > avg_fps:
            up_down = "+"
        else:
            up_down = "-"
        self.last_n_fps[self.fps_index] = fps
        self.fps_index = (self.fps_index + 1) % len(self.last_n_fps)

        # Generate and print the output string
        s = ("\r%s files in %s dirs (secs: %s files/sec: %s%s comparisons: %s)" %
             (num_files, num_dirs, int(time_elapsed), fps, up_down, num_comparisons))
        self.line(s)

    def show_hardlinked_amount(self):
        # type: () -> None
        if not self.options.show_progress:
            return

        now = _time.time()
        time_since_last = now - self.last_time
        if time_since_last < self.update_delay:
            return

        time_elapsed = now - self.stats.starttime
        num_hardlinked = self.stats.num_hardlinked_thisrun

        s = ("\rHardlinks this run %s (elapsed secs: %s)" %
             (num_hardlinked, int(time_elapsed)))
        self.line(s)
        self.last_time = now

    def clear(self):
        # type: () -> None
        self.line("\r")  # This erases the last line
        self.last_line_len = 1
        self.line("\r")  # This moves to the beginning

    def line(self, output_string):
        # type: (str) -> None
        """Output on the same line (must be given line starting with \r, not ending with \n)"""
        if not self.options.show_progress:
            return

        # Add enough spaces to overwrite last line
        num_spaces = self.last_line_len - len(output_string)
        if num_spaces > 0:
            output_string += (" " * num_spaces)
            assert len(output_string) == self.last_line_len
        self.last_line_len = len(output_string)
        _sys.stdout.write(output_string)
        _sys.stdout.flush()


#################
# Module functions
#################

def _stat_hash_value(statinfo, options):
    # type: (_os.stat_result, _Values) -> int
    """Return a value appropriate for a python dict or shelve key, which can
    differentiate files which cannot be hardlinked."""
    size = statinfo.st_size
    if options.ignore_time or options.contentonly:
        value = size
    else:
        mtime = int(statinfo.st_mtime)
        value = size ^ mtime

    return value


def _cull_excluded_directories(dirs, excludes):
    # type: (List[str], List[str]) -> None
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
    # type: (str, List[str]) -> bool
    """If excludes option is given, return True if name matches any regex."""
    for exclude in excludes:
        if _re.search(exclude, name):
            return True
    return False


def _found_matched_filename_regex(name, matches):
    # type: (str, List[str]) -> bool
    """If matches option is given, return False if name doesn't match any
    patterns.  If no matches are given, return True."""
    if not matches:
        return True
    for match in matches:
        if _re.search(match, name):
            return True
    return False


def _linked_inode_set(ino, linked_inodes):
    # type: (int, Dict[int, InoSet]) -> InoSet
    """Return set of inodes that are connected to given inode"""

    if ino not in linked_inodes:
        return set([ino])
    remaining_inodes = linked_inodes.copy()
    result_set = set()  # type: InoSet
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
    # type: (Dict[int, InoSet]) -> Iterable[InoSet]
    """Generate sets of inodes that can be connected.  Starts with a mapping of
    inode # keys, and set values, which are the inodes which are determined to
    be equal (and thus linkable) to the key inode."""

    remaining_inodes = linked_inodes.copy()
    # iterate once over each inode key, building a set of it's connected
    # inodes, by direct or indirect association
    for start_ino in linked_inodes:
        if start_ino not in remaining_inodes:
            continue
        result_set = set()  # type: InoSet
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
    # type: (Dict[str, List[NamePair]]) -> Iterable[NamePair]
    """Yield namepairs for each value in the dictionary d"""
    # A dictionary of {filename:[namepair]}, ie. a filename and list of
    # namepairs.  Make a copy as d and it's list values may be modified between
    # yields.
    d = _copy.deepcopy(d)
    for filename, namepairs in d.items():
        for namepair in namepairs:
            yield namepair


def _is_already_hardlinked(st1, st2):
    # type: (_os.stat_result, _os.stat_result) -> bool
    """If two files have the same inode and are on the same device then they
    are already hardlinked."""
    result = (st1.st_ino == st2.st_ino and  # Inodes equal
              st1.st_dev == st2.st_dev)     # Devices equal
    return result


def _file_has_been_modified(pathname, statinfo):
    # type: (str, _os.stat_result) -> bool
    """Return True if file is known to have been modified."""
    try:
        current_stat = _os.lstat(pathname)
    except OSError:
        error = _sys.exc_info()[1]
        _logging.error("Failed to stat: %s\n%s" % (pathname, error))
        return False

    # Check inode stats to see an indication that the file (or possibly the
    # inode) was updated.
    if (current_stat.st_mtime != statinfo.st_mtime or
        current_stat.st_size != statinfo.st_size or
        current_stat.st_mode != statinfo.st_mode or
        current_stat.st_uid != statinfo.st_uid or
        current_stat.st_gid != statinfo.st_gid):
        return True

    return False


def _humanize_number(number):
    # type: (int) -> str
    """Return string with number represented in 'human readable' form"""
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
    # type: (str) -> int
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
    # type: (str) -> Optional[int]
    """Return a hash value based on all (or some) of a file"""
    # Currently uses just the first 8K of the file (same buffer size as
    # filecmp)

    if DEFAULT_LINEAR_SEARCH_THRESH is None:
        return None

    try:
        f = open(pathname, 'rb')
    except OSError:
        return None

    # Python 2.3 disallows except/finally together
    try:
        byte_data = f.read(_filecmp.BUFSIZE)  # type: ignore  #BUG workaround?
    except OSError:
        f.close()
        return None
    f.close()

    return (0xFFFFFFFF & _crc32(byte_data))


def _equal_xattr(pathname1, pathname2):
    # type: (str, str) -> bool
    x1 = xattr.xattr(pathname1)
    x2 = xattr.xattr(pathname2)

    if len(x1) != len(x2):
        return False
    for k,v in x1.iteritems():
        if k not in x2:
            return False
        if v != x2[k]:
            return False
    return True


def _equal_xattr_dummy(pathname1, pathname2):
    # type: (str, str) -> bool
    return True

if xattr is None:
    _equal_xattr = _equal_xattr_dummy


def _missing_modules_str():
    # type: () -> str
    """Return string indicating useful but missing modules"""
    missing_modules = []
    if not json:
        missing_modules.append("'json'")
    if not xattr:
        missing_modules.append("'xattr'")
    if len(missing_modules) > 1:
        plural = 's'
    else:
        plural = ''
    modules_str = ",".join(missing_modules)
    if modules_str:
        s = (" Install %s Python module%s for more options." %
             (modules_str, plural))
    else:
        s = ''
    return s


def main():
    # type: () -> None
    # 'logging' package forces at least Python 2.3
    assert _sys.version_info >= (2, 3), ("%s requires at least Python 2.3" % _sys.argv[0])

    if _sys.version_info >= (2, 4):
        # logging.basicConfig in Python 2.3 accepted no args
        # Remove user from logging output
        _logging.basicConfig(format='%(levelname)s:%(message)s')

    # Parse our argument list and get our list of directories
    try:
        use_tty = _os.isatty(_sys.stdout.fileno())
    except (IOError, AttributeError):
        use_tty = False
    options, directories = _parse_command_line(show_progress_default=use_tty)

    # If no output or action possible from command, do nothing
    if not options.linking_enabled:
        if json is not None and options.json_enabled:
            if options.quiet:
                return
        elif options.debug_level == 0:
            # Note that debugging can override 'quiet' in non-json output
            if options.quiet or (options.verbosity == 0 and not options.printstats):
                return

    hl = Hardlinkable(options)
    try:
        hl.run(directories)
    except KeyboardInterrupt:
        if options.show_progress:
            hl.progress.clear()
        _logging.warning("\nExiting by keyboard interrupt...")
    except SystemExit:
        if options.show_progress:
            hl.progress.clear()
        _logging.error("\nSystem exit triggered.  Shutting down...")
        raise


if __name__ == '__main__':
    main()
