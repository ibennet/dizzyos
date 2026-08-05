# dizzyos dev commands. Runs against the local venv if present, else python3.
#
#   make dev              # live browser emulator, full app rotation
#   make dev APP=weather  # live browser emulator, just one app (dev preview)
#   make frames APP=weather   # render PNG frames headlessly (no display)
#   make install          # install Python dependencies
#
# The emulator opens at http://localhost:8888 and is tuned in emulator_config.json
# to simulate two chained Adafruit 64x64 3mm-pitch (P3) panels as a 128x64 canvas.

PYTHON ?= $(shell [ -x .venv/bin/python ] && echo .venv/bin/python || echo python3)

.DEFAULT_GOAL := help
.PHONY: help dev run frames install

help:
	@echo "dizzyos dev commands:"
	@echo "  make dev [APP=<name>]     live emulator (one app if APP set, else full rotation)"
	@echo "  make frames APP=<name>    render PNG frames to frames/<name>/ (headless)"
	@echo "  make install              install Python dependencies"
	@echo ""
	@echo "Emulator preview: http://localhost:8888"

# Live preview in the browser emulator.
dev:
ifeq ($(strip $(APP)),)
	$(PYTHON) run.py
else
	$(PYTHON) run.py --app $(APP)
endif

run: dev

# Headless frame dump for quick checks / CI. APP is required.
frames:
ifeq ($(strip $(APP)),)
	$(error APP is required, e.g. `make frames APP=weather`)
endif
	$(PYTHON) run.py --dump-frames frames/$(APP) --app $(APP)

install:
	$(PYTHON) -m pip install -r requirements.txt
