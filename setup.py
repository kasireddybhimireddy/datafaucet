import os
from glob import glob
from setuptools import setup

# the name of the package
name = 'datafaucet'
submodules = ['cli', 'spark', 'elastic']
packages = [name] + ['{}.{}'.format(name, sm) for sm in submodules]

pjoin = os.path.join
here = os.path.abspath(os.path.dirname(__file__))
pkg_root = pjoin(here, name)

version_ns = {}
with open(pjoin(here, name, '_version.py')) as f:
    exec(f.read(), {}, version_ns)

setup_args = dict(
    name=name,
    version=version_ns['__version__'],
    description='Productivity Utilities for Data Science with Python Notebooks',
    url='http://github.com/natbusa/datafaucet',
    author='Natalino Busa',
    author_email='natalino.busa@gmail.com',
    license='MIT',
    packages=packages,
    scripts=glob(pjoin('scripts', '*')),
    zip_safe=False,
    platforms="Linux, Mac OS X, Windows",
    keywords=['Interactive', 'Interpreter', 'Shell', 'Web'],
    classifiers=[
        'Intended Audience :: Developers',
        'Intended Audience :: Data Engineers',
        'Intended Audience :: Data Scientists',
        'Intended Audience :: Science/Research'],
)

install_requires = [
    'unidecode',
    'requests',
    'traitlets',
    'ipykernel',
    'ruamel.yaml',
    'jinja2',
    'cookiecutter',
    'faker',
    'gitpython',
    'jsonschema',
    'python-dateutil',
    'kafka-python',
    'pandas',
    'numpy',
    'python-dotenv'
]

try:
    #if pyspark is already installed skip pyspark
    import pyspark
except:
    install_requires += ['pyspark', 'pyarrow']

setup_args['include_package_data'] = True

if __name__ == '__main__':
    setup(**setup_args, install_requires = install_requires )
