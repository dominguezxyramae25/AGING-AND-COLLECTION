.PHONY: install sample run test clean shortcut help

help:
	@echo "make shortcut  Put an 'AR Collections' shortcut on your Desktop"
	@echo "make install   Install Python dependencies"
	@echo "make sample    Generate a sample dataset in sample_data/out/"
	@echo "make run       Start the app at http://localhost:8501"
	@echo "make test      Run the test suite"
	@echo "make clean     Remove generated sample data and caches"

shortcut:
	python3 scripts/install_shortcut.py

install:
	pip install -r requirements.txt

sample:
	python3 sample_data/generate.py

run:
	streamlit run app.py

test:
	python3 -m pytest tests/ -q

clean:
	rm -rf sample_data/out __pycache__ arcollect/__pycache__ tests/__pycache__ .pytest_cache scripts/__pycache__
