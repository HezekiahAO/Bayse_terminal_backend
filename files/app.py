"""
app.py — Root-level entry point for AfriSignal
===============================================
This file lets you start the server with simply:

    python app.py

Instead of the longer uvicorn command:

    uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

Why have both?
  - app/main.py  contains the FastAPI application object and all its configuration.
                 This is the "application" — imported by uvicorn, Docker, tests, etc.
  - app.py       (this file) is a convenience runner for local development.
                 It imports the app from app/main.py and runs it with uvicorn
                 programmatically.

The distinction matters because uvicorn needs to import app/main.py as a module
("app.main:app") to work correctly with hot reload. This root app.py just calls
uvicorn.run() for convenience.

Note on the name collision:
  This file is named app.py at the ROOT of the project (afrisignal/app.py).
  The app/ FOLDER also starts with "app" but is a package (has __init__.py).
  Python resolves these differently:
    - "import app"     -> finds app/ folder (the package)
    - running app.py   -> runs this file directly
  There is no collision as long as you run this from the afrisignal/ root.
"""
import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",      # module path to the FastAPI app object
        host="0.0.0.0",      # listen on all interfaces (not just localhost)
        port=8000,
        reload=True,         # auto-restart when any .py file changes (dev only)
        log_level="info",
    )
