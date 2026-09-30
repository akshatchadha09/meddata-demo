# MedData — Phase 8.1

Integrated local demo with robust server-side authentication/navigation.

## Run on Windows

1. Stop any older MedData server.
2. Double-click `run_demo.bat`.
3. If port 8000 is occupied, the launcher asks whether to stop the process using it.
4. Open http://127.0.0.1:8000

## Demo logins
- Government: gov@MedData.demo / gov123
- District: district@MedData.demo / district123
- PHC: phc@MedData.demo / phc123
- Supplier: supplier@MedData.demo / supplier123

## Authentication architecture

The login page is separate from the authenticated application. Successful sign-in creates an HttpOnly server session and redirects to `/app`. This means role buttons and normal sign-in do not depend on browser JavaScript to establish a session.

The authenticated app also relies on same-origin cookies for API calls, so stale localStorage tokens from older phases do not block login. Logout revokes both possible token sources and clears the session cookie.

## Important

Do not open HTML files directly. Start the local server with `run_demo.bat`.

This is a local synthetic-data prototype. Models are demonstrators, not clinically validated systems.


## Final Phase Visual Direction
The final MedData website uses a light, clean, accessible visual theme with white surfaces, soft blue/teal accents, subtle borders and restrained shadows.


## Publish a shareable demo (Render)

This repository includes `render.yaml` for a basic Render Web Service. Upload the extracted project to a GitHub repository, then in Render choose **New + → Blueprint** and connect that repository. Render will read `render.yaml` and deploy the web service. If you create a Web Service manually instead, use:

- Build command: `pip install -r backend/requirements.txt`
- Start command: `uvicorn backend.main:app --host 0.0.0.0 --port $PORT`

The database is a demo SQLite database. On free hosting, file changes may not persist across redeploys/restarts, and the service may sleep when idle. Use synthetic data only; do not enter real patient information. Demo login credentials are included in the project and should not be reused for a real deployment.
