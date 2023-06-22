PKG := feelancer

black:
	black . 

black-check:
	black . --check

isort:
	isort --profile black src/ tests/

isort-check:
	isort --profile black --check --diff src/ tests/

ruff:
	ruff check . --fix

ruff-check:
	ruff check .

pyright:
	pyright .

format: black
	make isort
	make ruff

check: black-check isort-check ruff-check pyright

clean:
	rm -r $(PKG).egg-info/ || true
	rm -r src/$(PKG).egg-info/ || true
	rm -rf .ruff_cache || true
	rm -rf .pytest_cache || true
	find . -name ".DS_Store" -exec rm -f {} \; || true
	find . -name "__pycache__" -exec rm -rf {} \; || true
	rm -rf dist || true
	rm -rf build || true

test:
	pytest tests --cov-report xml --cov $(PKG)

install:
	make clean
	pip install -r requirements.in .

install-dev:
	make clean
	pip install -r dev-requirements.in
	pip install -e .

pyenv_reset:
	pyenv virtualenv-delete -f feelancer-dev
	pyenv virtualenv feelancer-dev