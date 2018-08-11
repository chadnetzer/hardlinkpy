# hardlinkable - find and optionally link identical files

`hardlinkable` is a tool to scan directories and report files that could be
hardlinked together because they have matching content, and (by default) other
criteria such as modification time, permissions and ownership.  It can
optionally perform the linking as well, saving storage space (but by default,
it only reports information).

This version is faster, with more accurate reporting of results than the other
variants that I have tried.  It works by gathering full inode information
before deciding what action (if any) to take.  Using the full information
allows it to produce exact reporting of what will happen, before any
modifications occur.  It also can use remembered file content digests to
drastically shortcut the search time when looking for matching files, which
leads to order-of-magnitude increases in speed under some circumstances.

It currently works with Python 3, as well as supporting Python 2 versions back
to 2.3.

## Example output
```
$ hardlinkable download_dirs
Hard linking statistics
-----------------------
Statistics reflect what would result if actual linking were enabled
Directories                : 3748
Files                      : 89182
Comparisons                : 29908
Consolidatable inodes found: 10908
Hardlinkable files found   : 10908
Total old and new hardlinks: 10908
Currently hardlinked bytes : 0 (0 bytes)
Additional linkable bytes  : 259121960 (247.118 MiB)
Total hardlinkable bytes   : 259121960 (247.118 MiB)
```

You can specify more verbosity to get a list of linkable files, and some additional stats:
```
$ hardlinkable -v download_dirs
Files that are hardlinkable
-----------------------
from: download_dir/bak1/some_image1.png
  to: download_dir/bak2/some_image1.png
...
from: download_dir/fonts1/some_font.otf
  to: download_dir/other_fonts1/some_font.otf

Hard linking statistics
-----------------------
Statistics reflect what would result if actual linking were enabled
Directories                : 3748
Files                      : 89182
Comparisons                : 29908
Consolidatable inodes found: 10908
Hardlinkable files found   : 10908
Total old and new hardlinks: 10908
Currently hardlinked bytes : 0 (0 bytes)
Additional linkable bytes  : 259121960 (247.118 MiB)
Total hardlinkable bytes   : 259121960 (247.118 MiB)
Inodes found               : 89182
Current hardlinks          : 0
Total too small files      : 71
Total unequal file times   : 771
Total unequal file modes   : 411
Total remaining inodes     : 78274
```

## Help
```
$ hardlinkable -h
Usage
=====
  hardlinkable [options] directory [ directory ... ]

This is a tool to scan directories and report on the space that could be saved
by hard linking identical files.  It can also perform the linking.

Options
=======
--version             show program's version number and exit
--help, -h            show this help message and exit
--no-stats, -q        Do not print the statistics
--verbose, -v         Increase verbosity level (Up to 3 times)
--enable-linking      Perform the actual hardlinking
--no-progress         Disable progress output while processing
--json                Output results as JSON

File Matching
-------------
File content must always match exactly to be linkable.  Use --content-only
with caution, as it can lead to surprising results, including files becoming
owned by another user.

--same-name, -f       Filenames have to be identical
--ignore-perms, -p    File permissions do not need to match
--ignore-time, -t     File modification times do not need to match
--ignore-xattr        Xattrs do not need to match
--min-size=SZ, -s SZ  Minimum file size (default: 1)
--max-size=SZ, -S SZ  Maximum file size (Can add 'k', 'm', etc.)
--content-only, -c    Only file contents have to match

Name Matching (may specify multiple times)
------------------------------------------
--match=RE, -m RE     Regular expression used to match files
--exclude=RE, -x RE   Regular expression used to exclude files/dirs
```

## History

This program is built on and evolved from hardlink.py (or hardlinkpy), which
was originally written by John L. Villalovos (sodarock), and developed further
by Antti Kaihola, Carl Henrik Lunde, Wolf Ó Spealáin, and others.  It is able
to calculate accurate statistics on how much space can be saved, without
actually performing the linking.

This version is named ```hardlinkable``` to indicate that, by default, it does
*not* perform any linking, and the user has to explicitly opt-in to having it
perform the linking step.  This (to me) is the safer and more-sensible default;
it's not unusual to want to run it a few times with different options, and see
the results, before actually deciding whether to perform the linking.

Besides having more accurate statistics, this version can be significantly
faster than other versions in certain circumstances, due to opportunistically
keeping track of simple file content hashes as the inode hash comparison lists
grow.  It computes these content hashes at first only when comparing files
(when the file data will be read anyway), to avoid unnecessary I/O.  Using this
data, and quick set operations, it can then drastically reduce the amount of
file comparisons attempted as many files with similar inode attributes (ie.
size, mtime) but different content are discovered.

Furthermore, because it gathers full inode/pathname information before
attempting to optimize the link ordering, it also handles the "--same-name"
option more accurately than many other versions (imo).

Certain features are optional depending on the available python packages, such
as json and xattr support.
