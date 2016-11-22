from setuptools import setup, find_packages

setup(
    name='fc.megacli',
    version='0.0',
    author='Flying Circus',
    author_email='mail@flyingcircus.io',
    description="""\
Utilities for LSI/Avago controllers.
""",
    packages=find_packages('src'),
    package_dir={'': 'src'},
    include_package_data=True,
    zip_safe=False,
    license='GPL',
    namespace_packages=['fc'],
    install_requires=[
        'setuptools',
    ],
    entry_points="""
        [console_scripts]
        fc-megacli = fc.megacli.app:summary""",
    classifiers=[
        'Programming Language :: Python :: 2.7',
    ],
)
