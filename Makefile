# gcloud-axi
#
# `make test` and `./test.sh` are the same offline suite; both run against the
# fake-gcloud shim and never touch a real gcloud or the network.

PYTHON ?= python3
PREFIX ?= /usr/local

.PHONY: help test lint install uninstall clean

help:
	@echo "make test       run the offline test suite"
	@echo "make lint       byte-compile every source file"
	@echo "make install    symlink gcloud-axi into \$$PREFIX/bin (default $(PREFIX)/bin)"
	@echo "make uninstall  remove that symlink"
	@echo "make clean      remove build artefacts"

test:
	./test.sh

lint:
	$(PYTHON) -m compileall -q src tests gcloud-axi

install:
	install -d $(PREFIX)/bin
	ln -sf $(CURDIR)/gcloud-axi $(PREFIX)/bin/gcloud-axi
	@echo "linked $(PREFIX)/bin/gcloud-axi -> $(CURDIR)/gcloud-axi"

uninstall:
	rm -f $(PREFIX)/bin/gcloud-axi

clean:
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
	find . -name '*.py[co]' -delete
