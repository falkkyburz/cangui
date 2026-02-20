# Minimal Makefile for Sphinx docs
# Usage: make html
# Or:    uv run sphinx-build -b html docs docs/_build/html

SPHINXBUILD   = uv run sphinx-build
SOURCEDIR     = .
BUILDDIR      = _build

.PHONY: help html clean

help:
	@echo "Usage: make html"
	@echo "       make clean"

html:
	$(SPHINXBUILD) -b html $(SOURCEDIR) $(BUILDDIR)/html -W --keep-going
	@echo
	@echo "Build finished. Open $(BUILDDIR)/html/index.html"

clean:
	rm -rf $(BUILDDIR)
