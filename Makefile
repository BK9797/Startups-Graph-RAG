.PHONY: install test api frontend

install:
	pip install -r requirements.txt

api:
	uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# macOS: OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES must be *exported* (not just
# set inline) so that ALL child processes Streamlit spawns (file-watcher,
# component iframe server, etc.) inherit it and don't crash with SIGSEGV.
# The shell script uses `export` to achieve this.
frontend:
	bash scripts/run_frontend.sh

test:
	pytest -q
