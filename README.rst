=========================================================================
 hardlink.py – hardlink together identical files in order to save space.
=========================================================================

`hardlink.py` is a tool to hardlink together identical files in order
to save space.  It is a complete rewrite and improvement over the
original hardlink.c code (by Jakub Jelinek at Red Hat).  The purpose
of the two is the same but they do it in vastly different ways.

This code has only been tested on Linux and should work on other Unix
variants.  We have no idea if it will work on Windows as we have never
tested it there and don't know about Windows support for hardlinks.

This code is very useful for people who mirror FTP sites in that it
can save a large amount of space when you have identical files on the
system.

John L. Villalovos (sodarock) first wrote the code in C++ and then
decided to port it to Python.  It was later forked and copied from
Google code to GitHub by Antti Kaihola, and some modifications and
improvements were made there.

Performance is orders of magnitude faster than hardlink.c due to a
more efficient algorithm.  Plus readability is much better too.


 ------------------------------------------------------------------------
 John Villalovos
 email: john@sodarock.com
 http://www.sodarock.com/

 Inspiration for this program came from the hardlink.c code. I liked what it
 did but did not like the code itself, to me it was very unmaintainable.  So I
 rewrote in C++ and then I rewrote it in python.  In reality this code is
 nothing like the original hardlink.c, since I do things quite differently.
 Even though this code is written in python the performance of the python
 version is much faster than the hardlink.c code, in my limited testing.  This
 is mainly due to use of different algorithms.

 Original inspirational hardlink.c code was written by:  Jakub Jelinek
 <jakub@redhat.com>

 ------------------------------------------------------------------------

 TODO:
   *   Thinking it might make sense to walk the entire tree first and collect
       up all the file information before starting to do comparisons.  Thought
       here is we could find all the files which are hardlinked to each other
       and then do a comparison.  If they are identical then hardlink
       everything at once.
