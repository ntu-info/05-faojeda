# app.py
from flask import Flask, jsonify, abort, send_file, request
import os
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.exc import OperationalError

_engine = None

def get_engine():
    global _engine
    if _engine is not None:
        return _engine
    db_url = os.getenv("DB_URL")
    if not db_url:
        raise RuntimeError("Missing DB_URL (or DATABASE_URL) environment variable.")
    # Normalize old 'postgres://' scheme to 'postgresql://'
    if db_url.startswith("postgres://"):
        db_url = "postgresql://" + db_url[len("postgres://"):]
    _engine = create_engine(
        db_url,
        pool_pre_ping=True,
    )
    return _engine

def create_app():
    app = Flask(__name__)

    @app.get("/", endpoint="health")
    def health():
        return "<p>Server working!</p>"

    @app.get("/img", endpoint="show_img")
    def show_img():
        return send_file("amygdala.gif", mimetype="image/gif")

    @app.get("/terms/<term>/studies", endpoint="terms_studies")
    def get_studies_by_term(term):
        return term

    @app.get("/locations/<coords>/studies", endpoint="locations_studies")
    def get_studies_by_coordinates(coords):
        x, y, z = map(int, coords.split("_"))
        return jsonify([x, y, z])

    @app.get("/test_db", endpoint="test_db")
    
    def test_db():
        eng = get_engine()
        payload = {"ok": False, "dialect": eng.dialect.name}

        try:
            with eng.begin() as conn:
                # Ensure we are in the correct schema
                conn.execute(text("SET search_path TO ns, public;"))
                payload["version"] = conn.exec_driver_sql("SELECT version()").scalar()

                # Counts
                payload["coordinates_count"] = conn.execute(text("SELECT COUNT(*) FROM ns.coordinates")).scalar()
                payload["metadata_count"] = conn.execute(text("SELECT COUNT(*) FROM ns.metadata")).scalar()
                payload["annotations_terms_count"] = conn.execute(text("SELECT COUNT(*) FROM ns.annotations_terms")).scalar()

                # Samples
                try:
                    rows = conn.execute(text(
                        "SELECT study_id, ST_X(geom) AS x, ST_Y(geom) AS y, ST_Z(geom) AS z FROM ns.coordinates LIMIT 3"
                    )).mappings().all()
                    payload["coordinates_sample"] = [dict(r) for r in rows]
                except Exception:
                    payload["coordinates_sample"] = []

                try:
                    # Select a few columns if they exist; otherwise select a generic subset
                    rows = conn.execute(text("SELECT * FROM ns.metadata LIMIT 3")).mappings().all()
                    payload["metadata_sample"] = [dict(r) for r in rows]
                except Exception:
                    payload["metadata_sample"] = []

                try:
                    rows = conn.execute(text(
                        "SELECT study_id, contrast_id, term, weight FROM ns.annotations_terms LIMIT 3"
                    )).mappings().all()
                    payload["annotations_terms_sample"] = [dict(r) for r in rows]
                except Exception:
                    payload["annotations_terms_sample"] = []

            payload["ok"] = True
            return jsonify(payload), 200

        except Exception as e:
            payload["error"] = str(e)
            return jsonify(payload), 500

    # --- Dissociation by terms: returns both directions in one response ---
    @app.get("/dissociate/terms/<term_a>/<term_b>", endpoint="dissociate_terms")
    def dissociate_by_terms(term_a: str, term_b: str):
        eng = get_engine()
        try:
            with eng.begin() as conn:
                conn.execute(text("SET search_path TO ns, public;"))

                def fetch_study_ids(term: str):
                    rows = conn.execute(text(
                        """
                        SELECT DISTINCT study_id
                        FROM annotations_terms
                        WHERE term = :t AND weight > 0
                        """
                    ), {"t": term}).all()
                    return {r[0] for r in rows}

                a_ids = fetch_study_ids(term_a)
                b_ids = fetch_study_ids(term_b)

                result = {
                    "a": term_a,
                    "b": term_b,
                    "a_minus_b": sorted(a_ids - b_ids),
                    "b_minus_a": sorted(b_ids - a_ids),
                }
                return jsonify(result), 200
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    # --- Dissociation by MNI coordinates (3D radius) ---
    @app.get("/dissociate/locations/<c1>/<c2>", endpoint="dissociate_locations")
    def dissociate_by_locations(c1: str, c2: str):
        eng = get_engine()
        # Optional radius (in coordinate units). Default 6.
        try:
            radius = float(request.args.get("r", 6))
        except Exception:
            radius = 6.0

        def parse_coords(s: str):
            try:
                x, y, z = [float(p) for p in s.split("_")]
            except Exception:
                abort(400, description="Coordinates must be in x_y_z format")
            return x, y, z

        x1, y1, z1 = parse_coords(c1)
        x2, y2, z2 = parse_coords(c2)

        try:
            with eng.begin() as conn:
                conn.execute(text("SET search_path TO ns, public;"))

                def nearby_ids(x, y, z):
                    rows = conn.execute(text(
                        """
                        SELECT DISTINCT study_id
                        FROM coordinates
                        WHERE ST_3DDWithin(
                            geom,
                            ST_SetSRID(ST_MakePoint(:x, :y, :z), 4326)::geometry(POINTZ, 4326),
                            :r
                        )
                        """
                    ), {"x": x, "y": y, "z": z, "r": radius}).all()
                    return {r[0] for r in rows}

                a_ids = nearby_ids(x1, y1, z1)
                b_ids = nearby_ids(x2, y2, z2)

                result = {
                    "a": [x1, y1, z1],
                    "b": [x2, y2, z2],
                    "radius": radius,
                    "a_minus_b": sorted(a_ids - b_ids),
                    "b_minus_a": sorted(b_ids - a_ids),
                }
                return jsonify(result), 200
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    return app


# WSGI entry point (no __main__)
app = create_app()
