# WaitWise: shortcuts for installing, running and testing the app.
# Run from module2/. `make` on its own lists the targets.
#
# Recipes only use npm and uv with --prefix/--directory, so they behave the same
# whether make runs them through sh (macOS, Linux, Git Bash) or cmd (Windows).
# Keep `help` text free of ( ) < > | & ; and quotes: sh and cmd parse those differently.

FRONTEND := frontend
BACKEND := backend
BACKEND_PORT := 9127

.DEFAULT_GOAL := help
MAKEFLAGS += --no-print-directory

.PHONY: help install dev run backend frontend mock test test-frontend test-backend build

help:
	@echo WaitWise make targets, run from module2/
	@echo   make install         Install frontend and backend dependencies with npm and uv
	@echo   make dev             Run backend and frontend together - http://localhost:3417
	@echo   make backend         Run only the API - http://localhost:$(BACKEND_PORT)/docs
	@echo   make frontend        Run only the frontend, against the API
	@echo   make mock            Run the frontend on the in-browser mock, no backend
	@echo   make test            Run frontend and backend tests
	@echo   make test-frontend   Run the Vitest suite
	@echo   make test-backend    Run the pytest suite
	@echo   make build           Typecheck and build the frontend
	@echo Demo logins: admin, bluebird, oakember - password: password

install:
	npm --prefix $(FRONTEND) install
	uv --directory $(BACKEND) sync

# Both servers in one terminal; Ctrl+C stops both.
dev:
	$(MAKE) -j 2 backend frontend

run: dev

backend:
	uv --directory $(BACKEND) run uvicorn app.main:app --reload --port $(BACKEND_PORT)

frontend:
	npm --prefix $(FRONTEND) run dev

mock:
	npm --prefix $(FRONTEND) run dev:mock

test: test-frontend test-backend

test-frontend:
	npm --prefix $(FRONTEND) test

test-backend:
	uv --directory $(BACKEND) run pytest

build:
	npm --prefix $(FRONTEND) run build
