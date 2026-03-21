from setuptools import setup, find_packages

setup(
    name="hermes_p2p",
    version="0.1.0",
    packages=find_packages(),
    install_requires=[
        "cryptography>=41.0.0",
        "pynacl>=1.5.0",
    ],
    extras_require={
        "test": ["pytest>=7.0.0"],
    },
    python_requires=">=3.9",
)
