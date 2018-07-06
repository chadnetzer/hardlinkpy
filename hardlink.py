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

import os
import re
import stat
import sys
import time
import filecmp
import fnmatch

from optparse import OptionParser


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

        (st1.st_mode == st2.st_mode or
         options.contentonly) and                # file mode is the same

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
    if None:
    # if not result:
        print("\n***\n", st1)
        print(st2)
        print("Already hardlinked: %s" % (not is_already_hardlinked(st1, st2)))
        print("Modes:", st1.st_mode, st2.st_mode)
        print("UIDs:", st1.st_uid, st2.st_uid)
        print("GIDs:", st1.st_gid, st2.st_gid)
        print("SIZE:", st1.st_size, st2.st_size)
        print("MTIME:", st1.st_mtime, st2.st_mtime)
        print("Ignore date:", options.notimestamp)
        print("Device:", st1.st_dev, st2.st_dev)
    return result


def are_file_contents_equal(filename1, filename2, options):
    """Determine if the contents of two files are equal"""
    if options.verbosity > 1:
        print("Comparing: %s" % filename1)
        print("     to  : %s" % filename2)
    gStats.did_comparison()
    return filecmp.cmp(filename1, filename2, shallow=False)


# Determines if two files should be hard linked together.
def are_files_hardlinkable(filestat1_pair, filestat2_pair, options):
    filename1,stat1 = filestat1_pair
    filename2,stat2 = filestat2_pair
    if options.samename and os.path.basename(filename1) != os.path.basename(filename2):
        result = False
    elif not eligible_for_hardlink(stat1, stat2, options):
        result = False
    else:
        result = are_file_contents_equal(filename1, filename2, options)
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
            print("Failed to rename: %s to %s: %s" % (destfile, temp_name, error))
        else:
            # Now link the sourcefile to the destination file
            try:
                os.link(sourcefile, destfile)
            except Exception as error:
                print("Failed to hardlink: %s to %s: %s" % (sourcefile, destfile, error))
                # Try to recover
                try:
                    os.rename(temp_name, destfile)
                except Exception as error:
                    print("BAD BAD - failed to rename back %s to %s: %s" % (temp_name, destfile, error))
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
                        print("Failed to update file attributes for %s: %s" % (sourcefile, error))

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

            # Note - "saved" amount is overoptimistic, since we don't track if
            # the destination was already hardlinked to something else.
            print("%sLinked: %s" % (preamble1, sourcefile))
            print("%s    to: %s, saved %s" % (preamble2, destfile, dest_stat_info.st_size))

    return hardlink_succeeded


def hardlink_identical_files(filename, stat_info, options):
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
        print("File: %s" % filename)
    file_info = (filename, stat_info)
    if file_hash in file_hashes:
        # We have file(s) that have the same hash as our current file.
        # Let's go through the list of files with the same hash and see if
        # we are already hardlinked to any of them.
        base_filename = os.path.basename(filename)
        for cached_file_info in file_hashes[file_hash]:
            cached_filename, cached_stat_info = cached_file_info
            if is_already_hardlinked(stat_info, cached_stat_info):
                if not options.samename or (base_filename == os.path.basename(cached_filename)):
                    gStats.found_hardlink(cached_filename, filename,
                                          cached_stat_info)
                    break
        else:
            # We did not find this file as hardlinked to any other file
            # yet.  So now lets see if our file should be hardlinked to any
            # of the other files with the same hash.
            for i, cached_file_info in enumerate(file_hashes[file_hash]):
                cached_filename, cached_stat_info = cached_file_info
                if are_files_hardlinkable(file_info, cached_file_info, options):
                    # Always use the file with the most hardlinks as the source
                    if stat_info.st_nlink > cached_stat_info.st_nlink:
                        source_file_info, dest_file_info = file_info, cached_file_info
                    else:
                        source_file_info, dest_file_info = cached_file_info, file_info

                    if hardlink_files(source_file_info, dest_file_info, options):
                        updated_stat_info = os.lstat(cached_filename)

                        # A cached file's st_nlink should only ever increase
                        assert updated_stat_info.st_nlink > file_hashes[file_hash][i][1].st_nlink

                        # Update file_hashes stat_info data to be current
                        file_hashes[file_hash][i] = (cached_filename, updated_stat_info)
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
        self.bytes_saved_thisrun = self.bytes_saved_thisrun + filesize
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
                for filename in file_list:
                    print("                   : %s" % filename)
                print("Size per file: %s  Total saved: %s" % (size,
                                                              size * len(file_list)))
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
        print("Hardlinked this run   : %s" % self.hardlinked_thisrun)
        print("Total hardlinks       : %s" % (self.hardlinked_previously + self.hardlinked_thisrun))
        print("Bytes saved this run  : %s (%s)" % (self.bytes_saved_thisrun, humanize_number(self.bytes_saved_thisrun)))
        totalbytes = self.bytes_saved_thisrun + self.bytes_saved_previously
        print("Total bytes saved     : %s (%s)" % (totalbytes, humanize_number(totalbytes)))
        print("Total run time        : %s seconds" % (time.time() - self.starttime))


def humanize_number(number):
    if number > 1024 ** 3:
        return ("%.3f gibibytes" % (number / (1024.0 ** 3)))
    if number > 1024 ** 2:
        return ("%.3f mebibytes" % (number / (1024.0 ** 2)))
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
    parser = OptionParser(usage=usage, version=version)
    parser.add_option("-f", "--filenames-equal", help="Filenames have to be identical",
                      action="store_true", dest="samename", default=False,)

    parser.add_option("-m", "--match", metavar="PATTERN",
                      help="Shell patterns used to match files (may specify multiple times)",
                      action="append", dest="matches", default=[],)

    parser.add_option("-n", "--dry-run", help="Do NOT actually hardlink files",
                      action="store_true", dest="dryrun", default=False,)

    parser.add_option("-p", "--print-previous", help="Print previously created hardlinks",
                      action="store_true", dest="printprevious", default=False,)

    parser.add_option("-q", "--no-stats", help="Do not print the statistics",
                      action="store_false", dest="printstats", default=True,)

    parser.add_option("-s", "--min-size", type="int", help="Minimum file size",
                      action="store", dest="min_file_size", default=0,)

    parser.add_option("-S", "--max-size", type="int", help="Maximum file size",
                      action="store", dest="max_file_size", default=0,)

    parser.add_option("-t", "--timestamp-ignore",
                      help="File modification times do NOT have to be identical",
                      action="store_true", dest="notimestamp", default=False,)

    parser.add_option("-c", "--content-only",
                      help="Only file contents have to match",
                      action="store_true", dest="contentonly", default=False,)

    parser.add_option("-v", "--verbose",
                      help="Increase verbosity level (Repeatable up to 3 times)",
                      action="count", dest="verbosity", default=0)

    parser.add_option("-x", "--exclude", metavar="REGEX",
                      help="Regular expression used to exclude files/dirs (may specify multiple times)",
                      action="append", dest="excludes", default=[],)

    (options, args) = parser.parse_args()
    if not args:
        parser.print_help()
        print("")
        print("Error: Must supply one or more directories")
        sys.exit(1)
    args = [os.path.abspath(os.path.expanduser(dirname)) for dirname in args]
    for dirname in args:
        if not os.path.isdir(dirname):
            parser.print_help()
            print("")
            print("Error: %s is NOT a directory" % dirname)
            sys.exit(1)
    if options.min_file_size < 0:
        parser.error("--min_size cannot be negative")
    if options.max_file_size < 0:
        parser.error("--max_size cannot be negative")
    if options.max_file_size and options.max_file_size < options.min_file_size:
        parser.error("--max_size cannot be smaller than --min_size")

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
                print("Error: Use of deprecated numeric verbosity option (%s)." % ('-v ' + n_str))
                sys.exit(2)

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
                    print("Unable to get stat info for: %s: %s" % (pathname, error))
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
