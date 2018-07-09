#!/usr/bin/env python
from setuptools import setup

setup(name='hardlinkable',
      version='0.8',
      description='Find identical files in directory trees and optionally hard-link them',
      author='Chad Netzer',
      author_email='chad.netzer+hardlinkable@gmail.com',
      py_modules=["hardlinkable"],
      test_suite="tests",
      entry_points={
          'console_scripts': ['hardlinkable=hardlinkable.main']
      },
      classifiers=(
          "License :: OSI Approved :: GNU General Public License v2 or later (GPLv2+)",
          "Programming Language :: Python :: 2",
          "Programming Language :: Python :: 3",
          "Operating System :: POSIX",
          "Operating System :: MacOS",
          "Operating System :: MacOS :: MacOS X",
          "Operating System :: Unix",
      ),
)
