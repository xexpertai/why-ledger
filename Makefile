.PHONY: test data run replay eval

PYTHON := .venv/bin/python
PYTEST := .venv/bin/pytest
UVICORN := .venv/bin/uvicorn

test:
	$(PYTEST) -q

data:
	$(PYTHON) data/generate.py

run:
	$(UVICORN) whyledger.api:app --host 127.0.0.1 --port 8090

replay:
	$(PYTHON) scripts/record_replay.py

eval:
	$(PYTHON) scripts/eval.py
