from setuptools import setup, find_packages

with open("requirements.txt") as f:
	install_requires = f.read().strip().split("\n")

setup(
	name="heloc_tracker",
	version="0.0.1",
	description="Borrower-side amortization tracking for hybrid HELOC / Prêt Lié liabilities in ERPNext",
	author="Jonathan",
	author_email="you@example.com",
	packages=find_packages(),
	zip_safe=False,
	include_package_data=True,
	install_requires=install_requires,
)
