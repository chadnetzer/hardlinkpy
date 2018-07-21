# hardlinkable - find and optionally link identical files

`hardlinkable` is a tool to scan directories and report files that could be
hardlinked together because they have matching content, and (by default) other
criteria such as modification time, permissions and ownership.  It can
optionally perform the linking as well, saving storage space.

## Example output
```
$ hardlinkable download_dir
Hard linking statistics
-----------------------
Statistics reflect what would result if actual linking were enabled
Directories                : 3748
Files                      : 89182
Comparisons                : 716196
Inodes found               : 89182
Consolidatable inodes found: 10908
Current hardlinks          : 0
Hardlinkable files found   : 10908
Total old and new hardlinks: 10908
Current bytes saved        : 0 (0 bytes)
Additional bytes saveable  : 259121960 (247.118 MiB)
Total bytes saveable       : 259121960 (247.118 MiB)
```

You can specify more verbosity to get a list of linkable files, and some additional stats:
```
$ hardlinkable -v download_dir
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
Comparisons                : 716196
Inodes found               : 89182
Consolidatable inodes found: 10908
Current hardlinks          : 0
Hardlinkable files found   : 10908
Total old and new hardlinks: 10908
Current bytes saved        : 0 (0 bytes)
Additional bytes saveable  : 259121960 (247.118 MiB)
Total bytes saveable       : 259121960 (247.118 MiB)
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

File Matching
-------------
File content must always match exactly to be linkable.  Use --content-only
with caution, as it can lead to surprising results, including files becoming
owned by another user.

--same-name, -f       Filenames have to be identical
--ignore-perms, -p    File permissions do not need to match
--ignore-time, -t     File modification times do not need to match
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
by Antti Kaihola, Carl Henrik Lunde, and others.  It is able to calculate
accurate statistics on how much space can be saved, without actually performing
the linking.
