import io
import zipfile

import pytest
from pyproj import Geod
from shapely.geometry import LineString, MultiPolygon, Point, box

from tests.factories import build_kml, build_shapefile_zip, kml_placemark

GEOD = Geod(ellps="WGS84")
PLOT = box(76.96, 11.00, 76.97, 11.01)
ROAD = LineString([(76.96, 11.0), (76.97, 11.0), (76.97, 11.02)])
WELL = Point(76.965, 11.005)


def upload(client, content: bytes | str, filename: str, **form):
    data = content.encode() if isinstance(content, str) else content
    return client.post("/api/files/", files={"file": (filename, data)}, data=form)


@pytest.fixture
def kml_text():
    return build_kml([
        kml_placemark("Plot A", PLOT, "north field"),
        kml_placemark("Road", ROAD),
        kml_placemark("Well", WELL),
    ])


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


# ---------- upload + info ----------

def test_upload_kml_returns_file_info(client, kml_text):
    r = upload(client, kml_text, "survey.kml")
    assert r.status_code == 201
    body = r.json()
    assert body["filename"] == "survey.kml"
    assert body["feature_count"] == 3
    assert body["crs"] == "EPSG:4326"
    assert body["status"] == "COMPLETED"
    assert r.headers["location"] == f"/api/files/{body['id']}/"


def test_get_file_info(client, kml_text):
    file_id = upload(client, kml_text, "survey.kml").json()["id"]
    r = client.get(f"/api/files/{file_id}/")
    assert r.status_code == 200
    assert r.json()["id"] == file_id
    assert r.json()["feature_count"] == 3
    assert client.get(f"/api/files/{file_id}").status_code == 200  # no trailing slash also works


def test_unknown_file_is_404(client):
    for path in ("", "features/", "measurements/"):
        assert client.get(f"/api/files/nope/{path}").status_code == 404


def test_upload_shapefile_zip(client):
    z = build_shapefile_zip([PLOT, box(76.98, 11.0, 76.99, 11.01)], names=["A", "B"])
    r = upload(client, z, "plots.zip")
    assert r.status_code == 201
    assert r.json()["feature_count"] == 2
    assert r.json()["crs"] == "EPSG:4326"
    assert r.json()["file_type"] == "shapefile"


# ---------- features ----------

def test_features_expose_id_type_geometry_crs_properties(client, kml_text):
    file_id = upload(client, kml_text, "survey.kml").json()["id"]
    body = client.get(f"/api/files/{file_id}/features/").json()
    assert body["total"] == 3
    plot, road, well = body["features"]
    assert [f["id"] for f in body["features"]] == [0, 1, 2]
    assert [f["geometry_type"] for f in body["features"]] == ["Polygon", "LineString", "Point"]
    assert plot["crs"] == "EPSG:4326"
    assert plot["geometry"]["type"] == "Polygon"
    assert plot["properties"]["Name"] == "Plot A"
    assert plot["properties"]["description"] == "north field"
    assert well["geometry"]["coordinates"][:2] == [76.965, 11.005]
    assert "tessellate" not in plot["properties"]  # GDAL KML bookkeeping is filtered out


def test_features_pagination(client, kml_text):
    file_id = upload(client, kml_text, "survey.kml").json()["id"]
    body = client.get(f"/api/files/{file_id}/features/", params={"limit": 1, "offset": 1}).json()
    assert body["total"] == 3
    assert [f["id"] for f in body["features"]] == [1]


def test_shapefile_attributes_are_returned(client):
    file_id = upload(client, build_shapefile_zip([PLOT], names=["Paddy"]), "p.zip").json()["id"]
    feature = client.get(f"/api/files/{file_id}/features/").json()["features"][0]
    assert feature["properties"] == {"name": "Paddy"}


# ---------- measurements ----------

def test_measurements_for_kml(client, kml_text):
    file_id = upload(client, kml_text, "survey.kml").json()["id"]
    r = client.get(f"/api/files/{file_id}/measurements/")
    assert r.status_code == 200
    body = r.json()
    plot, road, well = body["measurements"]

    assert plot["status"] == "MEASURED"
    assert plot["projected_crs"] == "EPSG:32643"
    assert plot["area_m2"] == pytest.approx(abs(GEOD.geometry_area_perimeter(PLOT)[0]), rel=1e-3)
    assert plot["length_m"] is None

    assert road["length_m"] == pytest.approx(GEOD.geometry_length(ROAD), rel=1e-3)
    assert road["area_m2"] is None

    assert well["status"] == "NOT_REQUIRED"
    assert well["area_m2"] is None and well["length_m"] is None

    summary = body["summary"]
    assert summary["total_features"] == 3
    assert summary["measured"] == 2
    assert summary["not_required"] == 1
    assert summary["total_area_m2"] == pytest.approx(plot["area_m2"])
    assert summary["total_length_m"] == pytest.approx(road["length_m"])


def test_measurements_for_shapefile_match_kml(client, kml_text):
    kml_id = upload(client, kml_text, "survey.kml").json()["id"]
    shp_id = upload(client, build_shapefile_zip([PLOT], geometry_type="Polygon"), "p.zip").json()["id"]
    kml_area = client.get(f"/api/files/{kml_id}/measurements/").json()["measurements"][0]["area_m2"]
    shp_area = client.get(f"/api/files/{shp_id}/measurements/").json()["measurements"][0]["area_m2"]
    assert shp_area == pytest.approx(kml_area)


def test_projected_shapefile_is_reprojected(client):
    # 100 m x 100 m square in UTM 43N; the .prj says EPSG:32643
    z = build_shapefile_zip([box(500000, 1217000, 500100, 1217100)], crs="EPSG:32643")
    file_id = upload(client, z, "utm.zip").json()["id"]
    info = client.get(f"/api/files/{file_id}/").json()
    assert info["crs"] == "EPSG:32643"
    m = client.get(f"/api/files/{file_id}/measurements/").json()["measurements"][0]
    assert m["area_m2"] == pytest.approx(10_000, rel=1e-6)
    assert m["projected_crs"] == "EPSG:32643"


def test_multipolygon_kml(client):
    mp = MultiPolygon([box(76.96, 11.0, 76.97, 11.01), box(76.98, 11.0, 76.99, 11.01)])
    file_id = upload(client, build_kml([kml_placemark("Parcels", mp)]), "mp.kml").json()["id"]
    m = client.get(f"/api/files/{file_id}/measurements/").json()["measurements"][0]
    assert m["geometry_type"] == "MultiPolygon"
    assert m["area_m2"] == pytest.approx(2 * abs(GEOD.geometry_area_perimeter(PLOT)[0]), rel=1e-3)


def test_measurements_filter_and_pagination(client, kml_text):
    file_id = upload(client, kml_text, "survey.kml").json()["id"]
    body = client.get(f"/api/files/{file_id}/measurements/", params={"geometry_type": "polygon"}).json()
    assert body["total"] == 1
    assert body["measurements"][0]["geometry_type"] == "Polygon"
    assert body["summary"]["total_features"] == 3  # summary always covers the whole file

    page = client.get(f"/api/files/{file_id}/measurements/", params={"limit": 1, "offset": 2}).json()
    assert [m["feature_id"] for m in page["measurements"]] == [2]


def test_unsupported_geometry_is_handled_gracefully(client):
    kml = (
        '<?xml version="1.0"?><kml xmlns="http://www.opengis.net/kml/2.2"><Document><Folder><name>F</name>'
        "<Placemark><name>Mixed</name><MultiGeometry>"
        "<Point><coordinates>76.96,11.0</coordinates></Point>"
        "<LineString><coordinates>76.96,11.0 76.97,11.0</coordinates></LineString>"
        "</MultiGeometry></Placemark>"
        + kml_placemark("Plot", PLOT)
        + "</Folder></Document></kml>"
    )
    r = upload(client, kml, "mixed.kml")
    assert r.status_code == 201
    body = client.get(f"/api/files/{r.json()['id']}/measurements/").json()
    mixed, plot = body["measurements"]
    assert mixed["geometry_type"] == "GeometryCollection"
    assert mixed["status"] == "UNSUPPORTED"
    assert "not supported" in mixed["message"]
    assert plot["status"] == "MEASURED"  # the rest of the file is unaffected
    assert body["summary"]["unsupported"] == 1


def test_multiple_kml_folders_are_all_read(client):
    kml = (
        '<?xml version="1.0"?><kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
        f"<Folder><name>Plots</name>{kml_placemark('A', PLOT)}</Folder>"
        f"<Folder><name>Roads</name>{kml_placemark('R', ROAD)}</Folder>"
        "</Document></kml>"
    )
    file_id = upload(client, kml, "folders.kml").json()["id"]
    feats = client.get(f"/api/files/{file_id}/features/").json()["features"]
    assert [(f["id"], f["layer"]) for f in feats] == [(0, "Plots"), (1, "Roads")]


# ---------- error handling ----------

def test_unsupported_extension_is_415(client):
    r = upload(client, b"a,b\n1,2", "data.csv")
    assert r.status_code == 415


def test_empty_file_is_422(client):
    assert upload(client, b"", "empty.kml").status_code == 422


def test_garbage_kml_is_recorded_as_failed(client):
    r = upload(client, "<not-kml>hello</not-kml>", "bad.kml")
    assert r.status_code == 422
    file_id = r.json()["file_id"]
    info = client.get(f"/api/files/{file_id}/").json()
    assert info["status"] == "FAILED"
    assert info["error"]
    assert client.get(f"/api/files/{file_id}/measurements/").status_code == 409


def test_corrupt_zip_is_422(client):
    r = upload(client, b"PK\x03\x04 definitely not a zip", "bad.zip")
    assert r.status_code == 422


def test_zip_without_shp_is_422(client):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("readme.txt", "hello")
    r = upload(client, buf.getvalue(), "nothing.zip")
    assert r.status_code == 422
    assert ".shp" in r.json()["detail"]


def test_zip_slip_is_rejected(client):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../../evil.shp", "x")
    r = upload(client, buf.getvalue(), "evil.zip")
    assert r.status_code == 422
    assert "unsafe path" in r.json()["detail"]


def test_oversized_upload_is_413(client):
    r = upload(client, b"<" + b"x" * (2 * 1024 * 1024), "big.kml")  # limit is 1 MB in tests
    assert r.status_code == 413


def test_shapefile_without_prj_requires_assume_crs(client):
    z = build_shapefile_zip([PLOT], crs=None)
    r = upload(client, z, "noprj.zip")
    assert r.status_code == 422
    assert "assume_crs" in r.json()["detail"]

    r = upload(client, z, "noprj.zip", assume_crs="EPSG:4326")
    assert r.status_code == 201
    assert r.json()["crs"] == "EPSG:4326"


def test_invalid_assume_crs_is_422(client):
    z = build_shapefile_zip([PLOT], crs=None)
    assert upload(client, z, "noprj.zip", assume_crs="not-a-crs").status_code == 422


def test_mislabelled_crs_reports_error_per_feature(client):
    # projected metres but declared as lon/lat
    z = build_shapefile_zip([box(500000, 1217000, 500100, 1217100)], crs=None)
    file_id = upload(client, z, "wrong.zip", assume_crs="EPSG:4326").json()["id"]
    m = client.get(f"/api/files/{file_id}/measurements/").json()
    assert m["measurements"][0]["status"] == "ERROR"
    assert m["summary"]["errors"] == 1


# ---------- list + delete ----------

def test_list_and_delete(client, kml_text, settings):
    a = upload(client, kml_text, "a.kml").json()["id"]
    b = upload(client, kml_text, "b.kml").json()["id"]
    listing = client.get("/api/files/").json()
    assert listing["total"] == 2
    assert {f["id"] for f in listing["files"]} == {a, b}

    assert (settings.upload_dir / a).exists()
    assert client.delete(f"/api/files/{a}/").status_code == 204
    assert not (settings.upload_dir / a).exists()
    assert client.get(f"/api/files/{a}/").status_code == 404
    assert client.get("/api/files/").json()["total"] == 1


def test_timestamps_are_utc_on_upload_and_get(client, kml_text):
    created = upload(client, kml_text, "survey.kml").json()
    fetched = client.get(f"/api/files/{created['id']}/").json()
    for body in (created, fetched):
        assert body["created_at"].endswith(("Z", "+00:00"))
