#!/usr/bin/python

# hardlink - Goes through a directory structure and creates hardlinks for
# files which are identical.
#
# Copyright (C) 2003 - 2010  John L. Villalovos, Hillsboro, Oregon
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
#
#
# ------------------------------------------------------------------------
# John Villalovos
# email: john@sodarock.com
# http://www.sodarock.com/
#
# Inspiration for this program came from the hardlink.c code. I liked what it
# did but did not like the code itself, to me it was very unmaintainable.  So I
# rewrote in C++ and then I rewrote it in python.  In reality this code is
# nothing like the original hardlink.c, since I do things quite differently.
# Even though this code is written in python the performance of the python
# version is much faster than the hardlink.c code, in my limited testing.  This
# is mainly due to use of different algorithms.
#
# Original inspirational hardlink.c code was written by:  Jakub Jelinek
# <jakub@redhat.com>
#
# ------------------------------------------------------------------------
#
# TODO:
#   *   Thinking it might make sense to walk the entire tree first and collect
#       up all the file information before starting to do comparisons.  Thought
#       here is we could find all the files which are hardlinked to each other
#       and then do a comparison.  If they are identical then hardlink
#       everything at once.

import filecmp
import fnmatch
import logging
import os
import re
import stat
import sys
import time

from optparse import OptionParser, OptionGroup, SUPPRESS_HELP


# Hash functions
# Create a hash from a file's size and time values
def hash_size_time(size, time):
    return (size ^ time) & (MAX_HASHES - 1)


def hash_size(size):
    return (size) & (MAX_HASHES - 1)


def hash_value(size, time, notimestamp):
    if notimestamp:
        return hash_size(size)
    else:
        return hash_size_time(size, int(time))


# If two files have the same inode and are on the same device then they are
# already hardlinked.
def is_already_hardlinked(st1,     # first file's status
                          st2):    # second file's status
    result = (st1.st_ino == st2.st_ino and  # Inodes equal
              st1.st_dev == st2.st_dev)     # Devices equal
    return result


# Determine if a file is eligibile for hardlinking.  Files will only be
# considered for hardlinking if this function returns true.
def eligible_for_hardlink(st1,        # first file's status
                          st2,        # second file's status
                          options):

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
    max_nlinks = max_nlinks_per_dev[st1.st_dev]
    if result and (max_nlinks is not None):
        # The justification for not linking a pair of files if their nlinks sum
        # to more than the device maximum, is that linking them won't change
        # the overall link count, meaning no space saving is possible overall
        # even when all their filenames are found and re-linked.
        result = ((st1.st_nlink + st2.st_nlink) <= max_nlinks)
    return result


def are_file_contents_equal(pathname1, pathname2, options):
    """Determine if the contents of two files are equal"""
    if options.verbosity > 1:
        print("Comparing: %s" % pathname1)
        print("     to  : %s" % pathname2)
    gStats.did_comparison()
    return filecmp.cmp(pathname1, pathname2, shallow=False)


# Determines if two files should be hard linked together.
def are_files_hardlinkable(filestat1_pair, filestat2_pair, options):
    pathname1,stat1 = filestat1_pair
    pathname2,stat2 = filestat2_pair
    if options.samename and os.path.basename(pathname1) != os.path.basename(pathname2):
        result = False
    elif not eligible_for_hardlink(stat1, stat2, options):
        result = False
    else:
        result = are_file_contents_equal(pathname1, pathname2, options)
    return result


# Hardlink two files together
def hardlink_files(source_file_info, dest_file_info, options):
    sourcefile, source_stat_info = source_file_info
    destfile, dest_stat_info = dest_file_info

    assert source_stat_info.st_nlink >= dest_stat_info.st_nlink

    hardlink_succeeded = False
    if not options.dryrun:
        # rename the destination file to save it
        temp_name = destfile + ".$$$___cleanit___$$$"
        try:
            os.rename(destfile, temp_name)
        except OSError as error:
            logging.error("Failed to rename: %s to %s\n%s" % (destfile, temp_name, error))
        else:
            # Now link the sourcefile to the destination file
            try:
                os.link(sourcefile, destfile)
            except Exception as error:
                logging.error("Failed to hardlink: %s to %s\n%s" % (sourcefile, destfile, error))
                # Try to recover
                try:
                    os.rename(temp_name, destfile)
                except Exception as error:
                    logging.critical("Failed to rename temp filename %s back to %s\n%s" % (temp_name, destfile, error))
                    sys.exit(3)
            else:
                # hard link succeeded
                # Delete the renamed version since we don't need it.
                os.unlink(temp_name)
                hardlink_succeeded = True

                # Use the destination file attributes if it's most recently modified
                if dest_stat_info.st_mtime > source_stat_info.st_mtime:
                    try:
                        os.utime(destfile, (dest_stat_info.st_atime, dest_stat_info.st_mtime))
                        os.chown(destfile, dest_stat_info.st_uid, dest_stat_info.st_gid)
                    except Exception as error:
                        logging.warning("Failed to update file attributes for %s\n%s" % (sourcefile, error))

    if hardlink_succeeded or options.dryrun:
        # update our stats (Note: dest_stat_info is from pre-link())
        gStats.did_hardlink(sourcefile, destfile, dest_stat_info)
        if options.verbosity > 0:
            if options.dryrun:
                preamble1 = "(Dry run) NOT "
                preamble2 = "              "
            else:
                preamble1 = ""
                preamble2 = ""

            print("%sLinked: %s" % (preamble1, sourcefile))
            if dest_stat_info.st_nlink == 1:
                print("%s    to: %s, saved %s" % (preamble2, destfile,
                                                  humanize_number(dest_stat_info.st_size)))
            else:
                print("%s    to: %s" % (preamble2, destfile))

    return hardlink_succeeded


def hardlink_identical_files(pathname, stat_info, options):
    """
    The purpose of this function is to hardlink files together if the files are
    the same.  To be considered the same they must be equal in the following
    criteria:
          * file size
          * file contents
          * file mode (default)
          * owner user id (default)
          * owner group id (default)
          * modified time (default)

    Also, files will only be hardlinked if they are on the same device.  This
    is because hardlink does not allow you to hardlink across file systems.

    The basic idea on how this is done is as follows:

        Walk the directory tree building up a list of the files.

     For each file, generate a simple hash based on the size and modified time.

     For any other files which share this hash make sure that they are not
     identical to this file.  If they are identical then hardlink the files.

     Add the file info to the list of files that have the same hash value."""

    # Create the hash for the file.
    file_hash = hash_value(stat_info.st_size, stat_info.st_mtime,
                           options.notimestamp or options.contentonly)
    # Bump statistics count of regular files found.
    gStats.found_regular_file()
    if options.verbosity > 2:
        print("File: %s" % pathname)
    file_info = (pathname, stat_info)
    if file_hash in file_hashes:
        # We have file(s) that have the same hash as our current file.
        # Let's go through the list of files with the same hash and see if
        # we are already hardlinked to any of them.
        filename = os.path.basename(pathname)
        for cached_file_info in file_hashes[file_hash]:
            cached_pathname, cached_stat_info = cached_file_info
            if is_already_hardlinked(stat_info, cached_stat_info):
                if not options.samename or (filename == os.path.basename(cached_pathname)):
                    if options.verbosity > 1:
                        print("Existing link: %s" % cached_pathname)
                        print("        with : %s" % pathname)
                    gStats.found_hardlink(cached_pathname, pathname,
                                          cached_stat_info)
                    break
        else:
            # We did not find this file as hardlinked to any other file
            # yet.  So now lets see if our file should be hardlinked to any
            # of the other files with the same hash.
            for i, cached_file_info in enumerate(file_hashes[file_hash]):
                cached_pathname, cached_stat_info = cached_file_info
                if are_files_hardlinkable(file_info, cached_file_info, options):
                    # Always use the file with the most hardlinks as the source
                    if stat_info.st_nlink > cached_stat_info.st_nlink:
                        source_file_info, dest_file_info = file_info, cached_file_info
                    else:
                        source_file_info, dest_file_info = cached_file_info, file_info

                    if hardlink_files(source_file_info, dest_file_info, options):
                        updated_stat_info = os.lstat(cached_pathname)

                        # A cached file's st_nlink should only ever increase
                        assert updated_stat_info.st_nlink > file_hashes[file_hash][i][1].st_nlink

                        # Update file_hashes stat_info data to be current
                        file_hashes[file_hash][i] = (cached_pathname, updated_stat_info)
                    break
            else:
                # The file should NOT be hardlinked to any of the other
                # files with the same hash.  So we will add it to the list
                # of files.
                file_hashes[file_hash].append(file_info)
    else:
        # There weren't any other files with the same hash value so we will
        # create a new entry and store our file.
        file_hashes[file_hash] = [file_info]


class Statistics:
    def __init__(self):
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

    def found_directory(self):
        self.dircount = self.dircount + 1

    def found_regular_file(self):
        self.regularfiles = self.regularfiles + 1

    def did_comparison(self):
        self.comparisons = self.comparisons + 1

    def found_hardlink(self, sourcefile, destfile, stat_info):
        filesize = stat_info.st_size
        self.hardlinked_previously = self.hardlinked_previously + 1
        self.bytes_saved_previously = self.bytes_saved_previously + filesize
        if sourcefile not in self.previouslyhardlinked:
            self.previouslyhardlinked[sourcefile] = (filesize, [destfile])
        else:
            self.previouslyhardlinked[sourcefile][1].append(destfile)

    def did_hardlink(self, sourcefile, destfile, dest_stat_info):
        filesize = dest_stat_info.st_size
        self.hardlinked_thisrun = self.hardlinked_thisrun + 1
        if dest_stat_info.st_nlink == 1:
            # We only save bytes if the last destination link was actually
            # removed.
            self.bytes_saved_thisrun = self.bytes_saved_thisrun + filesize
            self.nlinks_to_zero_thisrun = self.nlinks_to_zero_thisrun + 1
        self.hardlinkstats.append((sourcefile, destfile))

    def print_stats(self, options):
        print("\n")
        print("Hard linking Statistics:")
        # Print out the stats for the files we hardlinked, if any
        if self.previouslyhardlinked and options.printprevious:
            keys = list(self.previouslyhardlinked.keys())
            keys.sort()  # Could use sorted() once we only support >= Python 2.4
            print("Files Previously Hardlinked:")
            for key in keys:
                size, file_list = self.previouslyhardlinked[key]
                print("Hardlinked together: %s" % key)
                for pathname in file_list:
                    print("                   : %s" % pathname)
                print("Size per file: %s  Total saved: %s" % (humanize_number(size),
                                                              humanize_number(size * len(file_list))))
            print("")
        if self.hardlinkstats:
            if options.dryrun:
                print("Statistics reflect what would have happened if not a dry run")
            print("Files Hardlinked this run:")
            for (source, dest) in self.hardlinkstats:
                print("Hardlinked: %s" % source)
                print("        to: %s" % dest)
            print("")
        print("Directories           : %s" % self.dircount)
        print("Regular files         : %s" % self.regularfiles)
        print("Comparisons           : %s" % self.comparisons)
        print("Consolidated this run : %s" % self.nlinks_to_zero_thisrun)
        print("Hardlinked this run   : %s" % self.hardlinked_thisrun)
        print("Total hardlinks       : %s" % (self.hardlinked_previously + self.hardlinked_thisrun))
        print("Bytes saved this run  : %s (%s)" % (self.bytes_saved_thisrun, humanize_number(self.bytes_saved_thisrun)))
        totalbytes = self.bytes_saved_thisrun + self.bytes_saved_previously
        print("Total bytes saved     : %s (%s)" % (totalbytes, humanize_number(totalbytes)))
        print("Total run time        : %s seconds" % (time.time() - self.starttime))


def humanize_number(number):
    if number > 1024 ** 3:
        return ("%.3f GiB" % (number / (1024.0 ** 3)))
    if number > 1024 ** 2:
        return ("%.3f MiB" % (number / (1024.0 ** 2)))
    if number > 1024:
        return ("%.3f KiB" % (number / 1024.0))
    return ("%d bytes" % number)


def printversion(self):
    print("hardlink.py, Version %s" % VERSION)
    print("Copyright (C) 2003 - 2010 John L. Villalovos.")
    print("email: software@sodarock.com")
    print("web: http://www.sodarock.com/")
    print("""
This program is free software; you can redistribute it and/or modify it under
the terms of the GNU General Public License as published by the Free Software
Foundation; version 2 of the License.

This program is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE.  See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
this program; if not, write to the Free Software Foundation, Inc., 59 Temple
Place, Suite 330, Boston, MA  02111-1307, USA.
""")


def parse_command_line():
    usage = "usage: %prog [options] directory [ directory ... ]"
    version = "%prog: " + VERSION
    description = """\
This is a tool to hard-link together identical files in order to save space.
The tool scans the provided directories looking for identical files.  Linked
files can save space, but a change to one hardlinked file changes them all."""

    parser = OptionParser(usage=usage, version=version, description=description)
    parser.add_option("-n", "--dry-run", dest="dryrun",
                      help="Do NOT actually hardlink files",
                      action="store_true", default=False,)

    parser.add_option("-p", "--print-previous", dest="printprevious",
                      help="Print previously created hardlinks",
                      action="store_true", default=False,)

    parser.add_option("-q", "--no-stats", dest="printstats",
                      help="Do not print the statistics",
                      action="store_false", default=True,)

    parser.add_option("-v", "--verbose", dest="verbosity",
                      help="Increase verbosity level (Repeatable up to 3 times)",
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
        # has passed to assume that hardlinkpy users have switched over to the
        # new verbosity argument, we can remove this safeguard.

        # Iterate over a reversed argument list, looking for options pairs of
        # type ['-v', '<num>']
        for i,s in enumerate(sys.argv[::-1]):
            if i == 0:
                continue
            n_str = sys.argv[-i]
            if s in ('-v', '--verbose') and n_str.isdigit():
                parser.error("Use of deprecated numeric verbosity option (%s)." % ('-v ' + n_str))

    return options, args


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


# Start of global declarations
OLD_VERBOSE_OPTION_ERROR = True
MAX_HASHES = 128 * 1024

gStats = None

file_hashes = None
max_nlinks_per_dev = None

VERSION = "0.06 alpha - 2018-07-04 (04-Jul-2018)"


def main():
    global gStats, file_hashes, max_nlinks_per_dev

    logging.basicConfig(format='%(levelname)s:%(message)s')

    gStats = Statistics()
    file_hashes = {}
    max_nlinks_per_dev = {}

    # Compile up our regexes ahead of time
    global MIRROR_PL_REGEX, RSYNC_TEMP_REGEX
    MIRROR_PL_REGEX = re.compile(r'^\.in\.')
    RSYNC_TEMP_REGEX = re.compile((r'^\..*\.\?{6,6}$'))

    # Parse our argument list and get our list of directories
    options, directories = parse_command_line()
    # Now go through all the directories that have been added.
    # NOTE: hardlink_identical_files() will add more directories to the
    #       directories list as it finds them.
    for top_dir in directories:
        # Use topdown=True for directory search pruning. followlinks is False
        for dirpath, dirs, filenames in os.walk(top_dir, topdown=True):

            # If excludes match any of the subdirs (or the current dir), skip
            # them.
            cull_excluded_directories(dirs, options.excludes)
            cur_dir = os.path.basename(dirpath)
            if cur_dir and found_excluded(cur_dir, options.excludes):
                continue

            gStats.found_directory()

            # Loop through all the files in the directory
            for filename in filenames:
                if found_excluded(filename, options.excludes):
                    continue
                if found_excluded_dotfile(filename):
                    continue
                if not found_matched_filename(filename, options.matches):
                    continue

                pathname = os.path.normpath(os.path.join(dirpath, filename))
                try:
                    stat_info = os.lstat(pathname)
                except OSError as error:
                    logging.warning("Unable to get stat info for: %s\n%s" % (pathname, error))
                    continue

                # Is it a regular file?
                assert not stat.S_ISDIR(stat_info.st_mode)
                if not stat.S_ISREG(stat_info.st_mode):
                    continue

                if ((options.max_file_size and
                     stat_info.st_size > options.max_file_size) or
                    (stat_info.st_size < options.min_file_size)):
                    continue

                if stat_info.st_dev not in max_nlinks_per_dev:
                    # Try to discover the maximum number of nlinks possible for
                    # each new device.
                    try:
                        max_nlinks = os.pathconf(pathname, "PC_LINK_MAX")
                    except:
                        # Avoid retrying if PC_LINK_MAX fails for a device
                        max_nlinks = None
                    max_nlinks_per_dev[stat_info.st_dev] = max_nlinks

                hardlink_identical_files(pathname, stat_info, options)

    if options.printstats:
        gStats.print_stats(options)

if __name__ == '__main__':
    main()
