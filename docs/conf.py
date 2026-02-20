"""Sphinx configuration for cangui documentation."""

import os
import sys

# Allow Sphinx autodoc to find the cangui package
sys.path.insert(0, os.path.abspath(".."))

# -- Project information -------------------------------------------------------

project = "cangui"
copyright = "2024, cangui contributors"
author = "cangui contributors"
release = "0.1"

# -- General configuration -----------------------------------------------------

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "sphinx.ext.intersphinx",
    "sphinx_autodoc_typehints",
    "myst_parser",
]

templates_path = ["_templates"]
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]

# -- Autodoc options -----------------------------------------------------------

autodoc_default_options = {
    "members": True,
    "undoc-members": False,
    "show-inheritance": True,
    "member-order": "bysource",
}

# sphinx-autodoc-typehints: show types in the description, not the signature
typehints_fully_qualified = False
always_document_param_types = False

# -- Napoleon (Google-style docstrings) ----------------------------------------

napoleon_google_docstring = True
napoleon_numpy_docstring = False
napoleon_include_init_with_doc = True
napoleon_include_private_with_doc = False
napoleon_use_param = True
napoleon_use_rtype = True

# -- MyST (Markdown support) ---------------------------------------------------

myst_enable_extensions = ["colon_fence", "deflist"]
source_suffix = {
    ".rst": "restructuredtext",
    ".md": "markdown",
}

# -- Intersphinx ---------------------------------------------------------------

intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
}

# -- HTML output ---------------------------------------------------------------

html_theme = "furo"
html_static_path = ["_static"]

# Furo theme options
html_theme_options = {
    "sidebar_hide_name": False,
    "navigation_with_keys": True,
}
