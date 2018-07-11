=========================================================================
 hardlinkable.py – discover hardlinkable files and optionally link them
=========================================================================

`hardlinkable.py` is a tool to scan directories and report files that could be
hardlinked together because they have matching content, and other possible
criteria such as timestamp, permissions and ownership.  It can optionally
perform the linking as well, saving storage space.
