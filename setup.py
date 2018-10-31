#!/usr/bin/env python3

from setuptools import setup
from repobot import __version__


setup(name='repobot',
      version=__version__,
      description='server for build artifact storage',
      url='',
      author='dpedu',
      author_email='dave@davepedu.com',
      packages=['repobot'],
      entry_points={
          "console_scripts": [
              "repobotd = repobot.server:main"
          ]
      },
      include_package_data=True,
      package_data={'repobot': ['../templates/pypi/*.html']},
      install_requires=[
          'ZODB==5.5.0',
          'CherryPy==18.0.1',
          'Jinja2==2.10',
          'requests==2.20.0'
      ],
      zip_safe=False)
