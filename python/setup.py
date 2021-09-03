# -*- coding: utf-8 -*-
from os import chdir
from os.path import abspath, dirname

from setuptools import find_packages, setup

chdir(dirname(abspath(__file__)))


with open('README.md') as f:
    readme = f.read()

with open('requirements.txt') as f:
    requirements = f.read().splitlines()

setup(
    author='Brandon Davidson',
    author_email='brad@oatmail.org',
    classifiers=[
        'Development Status :: 4 - Beta',
        'License :: OSI Approved :: Apache Software License',
    ],
    description='OBRA Hacks',
    entry_points={
        'console_scripts': ['obra-upgrade-calculator=obra_hacks.backend.commands:cli']
    },
    include_package_data=True,
    install_requires=requirements,
    long_description=readme,
    name='obra-hacks',
    packages=find_packages(exclude=('docs')),
    python_requires='>=2.7',
    url='https://github.com/brandond/obra-hacks',
    version='v0.0.0-dev',
)
