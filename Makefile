PKG := feelancer
UID = $(shell id -u)
GID = $(shell id -g)

black:
	black .

black-check:
	black . --check

isort:
	isort --skip grpc_generated --profile black src/ tests/ itests/

isort-check:
	isort --skip grpc_generated --profile black --check --diff src/ tests/ itests/

ruff:
	ruff check . --fix --exclude grpc_generated

ruff-check:
	ruff check . --exclude grpc_generated

pyright:
	pyright .

pyupgrade:
	find src tests itests -name "*.py"  | grep -v grpc_generated | xargs -I {} pyupgrade --py312-plus {}

format: black
	make isort
	make ruff
	make pyupgrade

check: black-check isort-check ruff-check pyright pyupgrade

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

make compile:
	# Could require additional tools to be installed.
	# Debian: apt install libpq-dev gfortran cmake libopenblas-dev liblapack-dev
	pip install --upgrade pip
	pip install pip-tools
	pip-compile --output-file=requirements.txt base.in
	pip-compile --output-file=addon-requirements.txt base.in addon.in
	pip-compile --output-file=dev-requirements.txt base.in addon.in dev.in

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

pdf:
	sed -E -i 's/[[:space:]]+$$//' docs/concept.md
	docker run --rm --volume "./docs:/data" --user $(UID):$(GID) pandoc/latex:3.2.1 concept.md -o concept.pdf -V geometry:margin=2.75cm

cloc:
	cloc --exclude-dir grpc_generated,.vscode  .

proto-compile:
	@echo "Downloading and compiling lnd protos"
	cd src/feelancer/lnd/protos && ./protoc.sh