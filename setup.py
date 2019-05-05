#!/usr/bin/env python3

from setuptools import setup
from repobot import __version__


with open("requirements.txt") as f:
    requirements = f.readlines()


setup(name='repobot',
      version=__version__,
      description='server for build artifact storage',
      url='https://git.davepedu.com/dave/docker-artifact',
      author='dpedu',
      author_email='dave@davepedu.com',
      packages=['repobot'],
      entry_points={
          "console_scripts": [
              "repobotd = repobot.server:main"
          ]
      },
      include_package_data=True,
      # package_data={'repobot': ['../templates/pypi/*.html']},
      install_requires=requirements,
      package_data={'repobot': ['../templates/pypi/*.html']},
      zip_safe=False)
