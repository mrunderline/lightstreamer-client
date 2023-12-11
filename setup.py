from os import path

from setuptools import setup, find_packages

here = path.abspath(path.dirname(__file__))
with open(path.join(here, 'README.md'), encoding='utf-8') as readme_file_stream:
    long_description = readme_file_stream.read()

setup(
    name='lightstreamer-client',
    version='0.1',
    description='Lightstreamer Python client',
    long_description=long_description,
    long_description_content_type='text/markdown',
    url='https://github.com/mrunderline/lightstreamer-client',
    author='Wojciech Szlachta',
    author_email='wojciech@szlachta.net',
    classifiers=[
        'Development Status :: 3 - Alpha',
        'Intended Audience :: Developers',
        'Topic :: Software Development :: Libraries :: Python Modules',
        'License :: OSI Approved :: Apache Software License',
        'Programming Language :: Python :: 3'
    ],
    keywords='lightstreamer',
    project_urls={
        'Bug Reports': 'https://github.com/mrunderline/lightstreamer-client/issues',
        'Source': 'https://github.com/mrunderline/lightstreamer-client'
    },

    packages=find_packages(exclude=['contrib', 'docs', 'tests']),

    install_requires=[],

    include_package_data=True
)
