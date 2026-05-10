
# ============================================================
# COMPONENT A
# Texas boundary(High Plains) + 4 class CDL crop map(Corn, Cotton, Soybean, Wheat)
# ============================================================

!pip -q install earthengine-api geemap geopandas shapely fiona pyproj gcsfs google-cloud-storage

import os
import json
import time
import ee
import geemap
import geopandas as gpd
from shapely.geometry import box
from google.cloud import storage

PROJECT_ID = "gcp-clag-remote-mapping"
CDL_YEAR = 2024

GCS_BUCKET = "storage_cropmapping"
GCS_PREFIX = "Finals/high_plains_crop_mapping"

GCS_BASE = f"gs://{GCS_BUCKET}/{GCS_PREFIX}"
print("GCS output folder:", GCS_BASE)

# EARTH ENGINE
try:
    ee.Initialize(project=PROJECT_ID)
    print("EE initialized with project:", PROJECT_ID)
except Exception as e:
    print("Need to authenticate:", e)
    ee.Authenticate()
    ee.Initialize(project=PROJECT_ID)
    print("EE initialized with project:", PROJECT_ID)

# INITIALIZE GCS CLIENT
storage_client = storage.Client(project=PROJECT_ID)
bucket = storage_client.bucket(GCS_BUCKET)
print("GCS client ready")

# UPLOAD TEXT/JSON/GEOJSON TO GCS
def upload_text_to_gcs(text, gcs_blob_name):
    blob = bucket.blob(gcs_blob_name)
    blob.upload_from_string(text)
    print(f"Saved: gs://{GCS_BUCKET}/{gcs_blob_name}")

# TEXAS BOUNDARY
TX_URL = "https://www2.census.gov/geo/tiger/GENZ2020/shp/cb_2020_us_state_20m.zip"

states = gpd.read_file(TX_URL).to_crs("EPSG:4326")
tx = states[states["STUSPS"] == "TX"].copy()
assert len(tx) == 1, "Texas boundary not found or duplicated."

tx_fc = geemap.gdf_to_ee(tx)
tx_geom_ee = tx_fc.geometry()

print("Texas boundary loaded")

# HIGH PLAINS / PANHANDLE ROI (west, south, east, north)
HP_BBOX = (-103.20, 33.20, -100.00, 36.50)

hp_gdf = gpd.GeoDataFrame(
    {"name": ["tx_high_plains_selected_area"]},
    geometry=[box(*HP_BBOX)],
    crs="EPSG:4326"
)

hp_fc = geemap.gdf_to_ee(hp_gdf)
hp_geom_ee = hp_fc.geometry()

print("Selected ROI ready")
print("Selected ROI bounds:", HP_BBOX)

# LUBBOCK area
lubbock_box_gdf = gpd.GeoDataFrame(
    {"name": ["lubbock_test_box"]},
    geometry=[box(-101.5359, 33.5400, -101.4359, 33.6402)],
    crs="EPSG:4326"
)

lubbock_fc = geemap.gdf_to_ee(lubbock_box_gdf)

# SAVE ROI FILES 
hp_geojson = hp_gdf.to_json()
lubbock_geojson = lubbock_box_gdf.to_json()

upload_text_to_gcs(
    hp_geojson,
    f"{GCS_PREFIX}/roi_high_plains_bbox_{CDL_YEAR}.geojson"
)

upload_text_to_gcs(
    lubbock_geojson,
    f"{GCS_PREFIX}/lubbock_test_box_{CDL_YEAR}.geojson"
)

# CDL
cdl = ee.Image(f"USDA/NASS/CDL/{CDL_YEAR}").select("cropland")
print("CDL image loaded for year", CDL_YEAR)

# TARGET CROP MASK
# 1  = Corn
# 2  = Cotton
# 5  = Soybean
# 23 = Spring Wheat
# 24 = Winter Wheat
TARGET_CODES = ee.List([
    1,   # Corn
    2,   # Cotton
    5,   # Soybean
    23,  # Spring Wheat
    24   # Winter Wheat
])

target_crop_mask = cdl.remap(
    TARGET_CODES,
    ee.List.repeat(1, TARGET_CODES.length()),
    0
).eq(1)

target_crop_mask_hp = target_crop_mask.selfMask().clip(hp_geom_ee)
print("target_crop_mask_hp prepared")


# class_id:
# 1 = Corn
# 2 = Cotton
# 3 = Soybean
# 4 = Wheat
label_img = ee.Image(0).rename("class_id")

label_img = label_img.where(cdl.eq(1), 1)                    # Corn
label_img = label_img.where(cdl.eq(2), 2)                    # Cotton
label_img = label_img.where(cdl.eq(5), 3)                    # Soybean
label_img = label_img.where(cdl.eq(23).Or(cdl.eq(24)), 4)    # Wheat

label_img_hp = label_img.updateMask(target_crop_mask_hp).clip(hp_geom_ee).toInt8()
target_crop_mask_hp = target_crop_mask_hp.toInt8()

print("label_img_hp 4-class image created")

# SAVE METADATA 
metadata = {
    "component": "Component A",
    "description": "High Plains 4-class CDL crop map and target crop mask",
    "project_id": PROJECT_ID,
    "cdl_year": CDL_YEAR,
    "gcs_output_folder": GCS_BASE,
    "roi_bbox_west_south_east_north": HP_BBOX,
    "classes": {
        "1": "corn",
        "2": "cotton",
        "3": "soybean",
        "4": "wheat"
    },
    "cdl_codes_used": {
        "corn": [1],
        "cotton": [2],
        "soybean": [5],
        "wheat": [23, 24]
    },
    "outputs": {
        "class_map": f"{GCS_BASE}/cdl_4class_hp_{CDL_YEAR}.tif",
        "target_mask": f"{GCS_BASE}/cdl_target_mask_hp_{CDL_YEAR}.tif",
        "roi_geojson": f"{GCS_BASE}/roi_high_plains_bbox_{CDL_YEAR}.geojson",
        "lubbock_geojson": f"{GCS_BASE}/lubbock_test_box_{CDL_YEAR}.geojson"
    }
}

upload_text_to_gcs(
    json.dumps(metadata, indent=2),
    f"{GCS_PREFIX}/metadata_componentA_{CDL_YEAR}.json"
)

# EXPORT IMAGE OUTPUTS 
class_export_prefix = f"{GCS_PREFIX}/cdl_4class_hp_{CDL_YEAR}"
mask_export_prefix = f"{GCS_PREFIX}/cdl_target_mask_hp_{CDL_YEAR}"

print("\nStarting Earth Engine exports...")
print("Class map will save to:")
print(f"gs://{GCS_BUCKET}/{class_export_prefix}.tif")
print("Target mask will save to:")
print(f"gs://{GCS_BUCKET}/{mask_export_prefix}.tif")

task_class = ee.batch.Export.image.toCloudStorage(
    image=label_img_hp,
    description=f"componentA_cdl_4class_hp_{CDL_YEAR}",
    bucket=GCS_BUCKET,
    fileNamePrefix=class_export_prefix,
    region=hp_geom_ee,
    scale=10,
    crs="EPSG:4326",
    maxPixels=1e13,
    fileFormat="GeoTIFF"
)

task_mask = ee.batch.Export.image.toCloudStorage(
    image=target_crop_mask_hp,
    description=f"componentA_cdl_target_mask_hp_{CDL_YEAR}",
    bucket=GCS_BUCKET,
    fileNamePrefix=mask_export_prefix,
    region=hp_geom_ee,
    scale=10,
    crs="EPSG:4326",
    maxPixels=1e13,
    fileFormat="GeoTIFF"
)

task_class.start()
task_mask.start()

print("\nExport tasks started.")
print("Check status at: https://code.earthengine.google.com/tasks")

def monitor_tasks(tasks, check_every_seconds=30):
    while True:
        statuses = [t.status() for t in tasks]

        print("\nCurrent task status:")
        for s in statuses:
            print(s["description"], "->", s["state"])

        states = [s["state"] for s in statuses]

        if all(state in ["COMPLETED", "FAILED", "CANCELLED"] for state in states):
            print("\nFinal task status:")
            for s in statuses:
                print(json.dumps(s, indent=2))
            break

        time.sleep(check_every_seconds)


# VISUALIZATION
Map = geemap.Map(center=[31.0, -99.0], zoom=6)

palette = [
    "ffff00",  # 1 Corn
    "ff0000",  # 2 Cotton
    "00ff00",  # 3 Soybean
    "8000ff",  # 4 Wheat
]

Map.add_basemap("Esri.WorldImagery")

Map.addLayer(
    tx_fc.style(color="red", fillColor="00000000", width=3),
    {},
    "Texas boundary"
)

Map.addLayer(
    hp_fc.style(color="black", fillColor="00000000", width=3),
    {},
    "Selected ROI"
)

Map.addLayer(
    lubbock_fc.style(color="cyan", fillColor="00000000", width=3),
    {},
    "Lubbock test box"
)

Map.addLayer(
    label_img_hp,
    {"min": 1, "max": 4, "palette": palette},
    "High Plains 4-class CDL"
)

Map.addLayerControl()
Map.centerObject(tx_fc, 6)

print("\nMap ready")
print("\nExpected saved outputs:")
print(f"1. gs://{GCS_BUCKET}/{GCS_PREFIX}/cdl_4class_hp_{CDL_YEAR}.tif")
print(f"2. gs://{GCS_BUCKET}/{GCS_PREFIX}/cdl_target_mask_hp_{CDL_YEAR}.tif")
print(f"3. gs://{GCS_BUCKET}/{GCS_PREFIX}/roi_high_plains_bbox_{CDL_YEAR}.geojson")
print(f"4. gs://{GCS_BUCKET}/{GCS_PREFIX}/lubbock_test_box_{CDL_YEAR}.geojson")
print(f"5. gs://{GCS_BUCKET}/{GCS_PREFIX}/metadata_componentA_{CDL_YEAR}.json")

display(Map)


# ============================================================
# COMPONENT B
# Generate OSM road points every 10 m for High Plains / Panhandle
# ============================================================

!pip -q install osmnx geopandas shapely pyproj rtree fiona tqdm gcsfs

import os
import json
import time
import warnings

import numpy as np
import pandas as pd
import geopandas as gpd
import osmnx as ox

from shapely.geometry import box, MultiLineString
from tqdm import tqdm


GCS_BUCKET = "storage_cropmapping"
GCS_PREFIX = "Finals/high_plains_crop_mapping/01_osm_roads_points"
GCS_DIR_OSM = f"gs://{GCS_BUCKET}/{GCS_PREFIX}"

LOCAL_DIR_OSM = "/content/high_plains_crop_mapping/01_osm_roads_points"
os.makedirs(LOCAL_DIR_OSM, exist_ok=True)

print("Local OSM dir:", LOCAL_DIR_OSM)
print("GCS OSM dir:", GCS_DIR_OSM)

# HIGH PLAINS / PANHANDLE ROI
HP_BBOX = (-103.20, 33.20, -100.00, 36.50)
hp_gdf = gpd.GeoDataFrame(
    {"name": ["tx_high_plains_panhandle"]},
    geometry=[box(*HP_BBOX)],
    crs="EPSG:4326"
)

hp_geom = hp_gdf.geometry.iloc[0]
print("High Plains bounds:", hp_geom.bounds)

# 0.2 DEGREE GRID OVER ROI
def make_grid(geom, tile_deg=0.2):
    minx, miny, maxx, maxy = geom.bounds
    xs = np.arange(minx, maxx, tile_deg)
    ys = np.arange(miny, maxy, tile_deg)

    cells = []
    ids = []
    tid = 0

    for x in xs:
        for y in ys:
            cell = box(x, y, x + tile_deg, y + tile_deg)
            if cell.intersects(geom):
                cells.append(cell.intersection(geom))
                ids.append(tid)
            tid += 1

    return gpd.GeoDataFrame({"tile_id": ids}, geometry=cells, crs="EPSG:4326")

TILE_DEG = 0.2
grid = make_grid(hp_geom, tile_deg=TILE_DEG)

grid_path = os.path.join(LOCAL_DIR_OSM, f"high_plains_grid_{TILE_DEG}deg.gpkg")
grid_gcs_path = f"{GCS_DIR_OSM}/high_plains_grid_{TILE_DEG}deg.gpkg"

grid.to_file(grid_path, layer="grid", driver="GPKG")

print("Grid tiles:", len(grid))
print("Saved grid locally to:", grid_path)
print("Will upload grid to:", grid_gcs_path)
display(grid.head())

# Upload grid to GCS
!gsutil cp "{grid_path}" "{grid_gcs_path}"

# OSMNX
ox.settings.use_cache = True
ox.settings.cache_folder = os.path.join(LOCAL_DIR_OSM, "_osm_cache")
os.makedirs(ox.settings.cache_folder, exist_ok=True)

ox.settings.timeout = 300
ox.settings.log_console = False

OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.openstreetmap.fr/api/interpreter",
]

def set_overpass_url(i):
    url = OVERPASS_URLS[i % len(OVERPASS_URLS)]
    ox.settings.overpass_url = url
    print("Using Overpass endpoint:", url)

def utm_epsg_from_lonlat(lon, lat):
    zone = int((lon + 180) / 6) + 1
    return 32600 + zone if lat >= 0 else 32700 + zone

def line_points_every_meters(lines_gdf, spacing_m, utm_epsg):
    g = lines_gdf.to_crs(f"EPSG:{utm_epsg}")
    pts = []

    for geom in g.geometry:
        if geom is None or geom.is_empty:
            continue

        parts = list(geom.geoms) if isinstance(geom, MultiLineString) else [geom]

        for ln in parts:
            L = ln.length
            if L < spacing_m:
                continue

            n = int(L // spacing_m)
            for i in range(n + 1):
                p = ln.interpolate(i * spacing_m)
                pts.append(p)

    if not pts:
        return gpd.GeoDataFrame(geometry=[], crs=f"EPSG:{utm_epsg}")

    pts_gdf = gpd.GeoDataFrame(geometry=pts, crs=f"EPSG:{utm_epsg}")
    pts_gdf = pts_gdf.to_crs("EPSG:4326")
    return pts_gdf

def fetch_roads_for_polygon(poly, network_type="drive", max_tries=3):
    last_err = None

    for t in range(max_tries):
        try:
            set_overpass_url(t)
            G = ox.graph_from_polygon(poly, network_type=network_type, simplify=True)
            edges = ox.graph_to_gdfs(G, nodes=False, edges=True)

            edges = edges[["geometry"]].copy()
            edges = edges[~edges.geometry.is_empty]

            return edges

        except Exception as e:
            print(f"fetch_roads attempt {t+1}/{max_tries} failed: {e}")
            last_err = e
            time.sleep(5 + 5 * t)

    raise last_err

# OUTPUT FILES
SPACING_M = 10

POINTS_GPKG = os.path.join(LOCAL_DIR_OSM, f"high_plains_osm_points_{SPACING_M}m.gpkg")
DONE_JSON = os.path.join(LOCAL_DIR_OSM, f"done_tiles_{SPACING_M}m.json")

POINTS_GCS = f"{GCS_DIR_OSM}/high_plains_osm_points_{SPACING_M}m.gpkg"
DONE_GCS = f"{GCS_DIR_OSM}/done_tiles_{SPACING_M}m.json"

if os.path.exists(DONE_JSON):
    with open(DONE_JSON, "r") as f:
        done_tiles = set(json.load(f))
else:
    done_tiles = set()

print(f"Resume: {len(done_tiles)} tiles already done.")
print("Points will be saved locally to:", POINTS_GPKG)
print("Points will be uploaded to:", POINTS_GCS)

warnings.filterwarnings("ignore", category=UserWarning)

for idx, row in tqdm(grid.iterrows(), total=len(grid)):
    tile_id = int(row["tile_id"])

    if tile_id in done_tiles:
        continue

    poly = row.geometry

    centroid = poly.centroid
    utm = utm_epsg_from_lonlat(centroid.x, centroid.y)

    try:
        edges = fetch_roads_for_polygon(poly)

        if edges is None or len(edges) == 0:
            print(f"Tile {tile_id}: no roads, marking done.")
            done_tiles.add(tile_id)
            with open(DONE_JSON, "w") as f:
                json.dump(sorted(list(done_tiles)), f)

            !gsutil cp "{DONE_JSON}" "{DONE_GCS}"
            continue

        pts = line_points_every_meters(edges, SPACING_M, utm)

        if pts is None or len(pts) == 0:
            print(f"Tile {tile_id}: roads but no points, marking done.")
            done_tiles.add(tile_id)
            with open(DONE_JSON, "w") as f:
                json.dump(sorted(list(done_tiles)), f)

            !gsutil cp "{DONE_JSON}" "{DONE_GCS}"
            continue

        pts["tile_id"] = tile_id

        if os.path.exists(POINTS_GPKG):
            pts.to_file(POINTS_GPKG, layer="points", driver="GPKG", mode="a")
        else:
            pts.to_file(POINTS_GPKG, layer="points", driver="GPKG")

        done_tiles.add(tile_id)
        with open(DONE_JSON, "w") as f:
            json.dump(sorted(list(done_tiles)), f)

        # Upload after every successful tile
        !gsutil cp "{POINTS_GPKG}" "{POINTS_GCS}"
        !gsutil cp "{DONE_JSON}" "{DONE_GCS}"

        time.sleep(1.0)

    except Exception as e:
        print(f"\nTile {tile_id} failed: {e}\nWill retry this tile on next run.")
        time.sleep(10)
        continue

print("Finished.")
print("Local points file:", POINTS_GPKG)
print("GCS points file:", POINTS_GCS)
print("Local done log:", DONE_JSON)
print("GCS done log:", DONE_GCS)

# PREVIEW
if os.path.exists(POINTS_GPKG):
    pts_preview = gpd.read_file(POINTS_GPKG, layer="points", rows=5)
    display(pts_preview)
else:
    print("No points file created yet.")


# ============================================================
# COMPONENT C + D + E
# ============================================================

!pip -q install geopandas pyogrio shapely pyproj fiona tqdm pyarrow requests earthengine-api geemap gcsfs

import os
import re
import gc
import json
import glob
import time
import hashlib
import subprocess
import warnings
from typing import Dict, List

import numpy as np
import pandas as pd
import geopandas as gpd
import pyogrio
import requests

from tqdm import tqdm
from shapely.geometry import box
from pyproj import CRS, Transformer, Geod
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import ee
import geemap

warnings.filterwarnings("ignore", category=UserWarning)

PROJECT_ID = "gcp-clag-remote-mapping"

YEAR_LABEL = 2024
TEST_YEAR = 2025

HP_BBOX = (-103.20, 33.20, -100.00, 36.50)

GCS_BUCKET = "storage_cropmapping"
GCS_BASE_PREFIX = "Finals/high_plains_crop_mapping"
GCS_BASE_ROOT = f"gs://{GCS_BUCKET}/{GCS_BASE_PREFIX}"

# Local working root
LOCAL_ROOT = "/content/high_plains_crop_mapping"
os.makedirs(LOCAL_ROOT, exist_ok=True)

# Component B local restored copy
DIR_OSM = os.path.join(LOCAL_ROOT, "01_osm_roads_points")
os.makedirs(DIR_OSM, exist_ok=True)

POINTS_GPKG = os.path.join(DIR_OSM, "high_plains_osm_points_10m.gpkg")
GRID_GPKG   = os.path.join(DIR_OSM, "high_plains_grid_0.2deg.gpkg")

# Component B existing GCS outputs
POINTS_GPKG_GCS = f"{GCS_BASE_ROOT}/01_osm_roads_points/high_plains_osm_points_10m.gpkg"
GRID_GPKG_GCS   = f"{GCS_BASE_ROOT}/01_osm_roads_points/high_plains_grid_0.2deg.gpkg"

# Component C/D/E local working root
LOCAL_PIPELINE_ROOT = os.path.join(
    LOCAL_ROOT,
    "componentC_D_E_outputs",
    "high_plains_multicrop_pipeline"
)
os.makedirs(LOCAL_PIPELINE_ROOT, exist_ok=True)

# Component C/D/E root
GCS_OUT_ROOT = f"{GCS_BASE_ROOT}/componentC_D_E_outputs/high_plains_multicrop_pipeline"
ENABLE_GCS_SYNC = True

# CDL (2024 and 2025 are in 10 m)
CDL_SOURCE_MODE = "public_gee"
CDL_SCALE_M = 10
CDL_BAND = "cropland"


# class_id:
#   1 = corn
#   2 = cotton
#   3 = soybean
#   4 = wheat
CROP_CONFIG = {
    "cotton": {
        "class_id": 2,
        "cdl_codes": [2],
        "grow_months": {6, 7, 8, 9, 10},
    },
    "soybean": {
        "class_id": 3,
        "cdl_codes": [5],
        "grow_months": {5, 6, 7, 8, 9},
    },
    "wheat": {
        "class_id": 4,
        "cdl_codes": [23, 24],
        "grow_months": {3, 4, 5, 6},
    },
    "corn": {
        "class_id": 1,
        "cdl_codes": [1],
        "grow_months": {6, 7, 8, 9},
    },
}
CLASS_ID_TO_CROP = {v["class_id"]: k for k, v in CROP_CONFIG.items()}

# GSV / HEADING / DOWNLOAD SETTINGS
BUFFER_M = 30
FIELD_OFFSET_M = 30
FIELD_BUF_M = 15

MIN_CROPFRAC = 0.50
POINT_CHUNK = 2000
HEADING_CHUNK = 250

GSV_SIZE = "640x640"
GSV_FOV = 90
GSV_PITCH = -5
GSV_REQ_PER_SEC = 5.0

GSV_KEY = os.environ.get("GSV_KEY", "")
if GSV_KEY is None:
    print("GSV_KEY is not set. Set it before running Component D/E.")

def crop_base(crop: str) -> str:
    return os.path.join(LOCAL_PIPELINE_ROOT, crop)

def crop_paths(crop: str) -> Dict[str, str]:
    base = crop_base(crop)
    return {
        "base": base,

        # Component C
        "compC_dir": os.path.join(base, "componentC_points_labels"),

        # Component D
        "compD_meta_dir": os.path.join(base, "componentD_metadata_hits_heading", "03_gsv_metadata_parquet"),
        "compD_hits_dir": os.path.join(base, "componentD_metadata_hits_heading", "04_hits"),
        "compD_heading_dir": os.path.join(base, "componentD_metadata_hits_heading", "05_componentD"),
        "logs": os.path.join(base, "componentD_metadata_hits_heading", "logs"),

        # Component E
        "compE_train_dir": os.path.join(base, "componentE_gsv_images", "06_images_train_NOT2025"),
        "compE_test_dir": os.path.join(base, "componentE_gsv_images", "06_images_test_2025"),
    }

def make_dirs_for_crop(crop: str) -> Dict[str, str]:
    p = crop_paths(crop)
    for _, v in p.items():
        os.makedirs(v, exist_ok=True)
    return p

for crop in CROP_CONFIG.keys():
    make_dirs_for_crop(crop)

def run_cmd(cmd: str, check: bool = True, allow_no_match: bool = False):
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if result.stdout.strip():
        print(result.stdout)
    if result.stderr.strip():
        print(result.stderr)

    if check and result.returncode != 0:
        msg = (result.stdout or "") + "\n" + (result.stderr or "")
        if allow_no_match and ("No URLs matched" in msg or "matched no objects" in msg):
            return
        raise RuntimeError(f"Command failed:\n{cmd}\n\n{msg}")

def gcs_exists(path: str) -> bool:
    result = subprocess.run(f'gcloud storage ls "{path}"', shell=True, capture_output=True, text=True)
    return result.returncode == 0

def sync_file_to_gcs(local_path: str, gcs_path: str):
    if not ENABLE_GCS_SYNC:
        return
    if not os.path.exists(local_path):
        return
    run_cmd(f'gcloud storage cp "{local_path}" "{gcs_path}"', check=True)

def sync_prefix_from_gcs(gcs_prefix: str, local_parent: str):
    """
    Copies a GCS folder/prefix to local parent if it exists.
    """
    os.makedirs(local_parent, exist_ok=True)
    if not gcs_exists(gcs_prefix):
        return
    run_cmd(f'gcloud storage cp -r "{gcs_prefix}" "{local_parent}"', check=True)

def sync_wildcard_from_gcs(gcs_pattern: str, local_dir: str):
    """
    Copies matching files if present; ignores no-match.
    """
    os.makedirs(local_dir, exist_ok=True)
    run_cmd(f'gcloud storage cp "{gcs_pattern}" "{local_dir}/"', check=False, allow_no_match=True)

def local_to_gcs(local_path: str) -> str:
    rel = os.path.relpath(local_path, LOCAL_PIPELINE_ROOT)
    return f"{GCS_OUT_ROOT}/{rel}"

_RESUME_PREPARED = False

def componentC_done_dir():
    d = os.path.join(LOCAL_PIPELINE_ROOT, "_componentC_done")
    os.makedirs(d, exist_ok=True)
    return d

def componentC_done_marker(tile_id: int) -> str:
    return os.path.join(componentC_done_dir(), f"tile_{tile_id:04d}_{YEAR_LABEL}.done.json")

def is_componentC_tile_done(tile_id: int) -> bool:
    return os.path.exists(componentC_done_marker(tile_id))

def mark_componentC_tile_done(tile_id: int, stats: dict):
    marker = componentC_done_marker(tile_id)
    with open(marker, "w") as f:
        json.dump(stats, f, indent=2)
    sync_file_to_gcs(marker, local_to_gcs(marker))

def componentD_meta_done_dir(crop: str):
    d = os.path.join(LOCAL_PIPELINE_ROOT, "_componentD_meta_done", crop)
    os.makedirs(d, exist_ok=True)
    return d

def componentD_meta_done_marker(crop: str, tile_id: int) -> str:
    return os.path.join(componentD_meta_done_dir(crop), f"tile_{tile_id:04d}_{YEAR_LABEL}.done.json")

def is_componentD_meta_tile_done(crop: str, tile_id: int) -> bool:
    return os.path.exists(componentD_meta_done_marker(crop, tile_id))

def mark_componentD_meta_tile_done(crop: str, tile_id: int, stats: dict):
    marker = componentD_meta_done_marker(crop, tile_id)
    with open(marker, "w") as f:
        json.dump(stats, f, indent=2)
    sync_file_to_gcs(marker, local_to_gcs(marker))

def bootstrap_componentC_done_from_existing_outputs():
    seen = set()
    for crop in CROP_CONFIG.keys():
        p = crop_paths(crop)
        files = glob.glob(os.path.join(p["compC_dir"], f"tile_*_{YEAR_LABEL}.parquet"))
        for fp in files:
            m = re.search(r"tile_(\d+)_", os.path.basename(fp))
            if m:
                seen.add(int(m.group(1)))

    print(f"Bootstrapping Component C done markers for {len(seen)} tiles")
    for tile_id in sorted(seen):
        if not is_componentC_tile_done(tile_id):
            mark_componentC_tile_done(tile_id, {
                "tile_id": int(tile_id),
                "year_label": int(YEAR_LABEL),
                "status": "done",
                "reason": "bootstrapped_from_existing_outputs",
                "n_input_points": None,
                "n_labeled_rows": None,
                "crops_written": None,
            })

def bootstrap_componentD_meta_done_from_existing_outputs():
    total = 0
    for crop in CROP_CONFIG.keys():
        p = crop_paths(crop)
        files = glob.glob(os.path.join(p["compD_meta_dir"], f"tile_*_{YEAR_LABEL}.parquet"))
        for fp in files:
            m = re.search(r"tile_(\d+)_", os.path.basename(fp))
            if not m:
                continue
            tile_id = int(m.group(1))
            if not is_componentD_meta_tile_done(crop, tile_id):
                mark_componentD_meta_tile_done(crop, tile_id, {
                    "crop": crop,
                    "tile_id": int(tile_id),
                    "year_label": int(YEAR_LABEL),
                    "status": "done",
                    "reason": "bootstrapped_from_existing_outputs",
                })
                total += 1
    print(f"Bootstrapped Component D metadata done markers: {total}")

def ensure_componentB_points_ready():
    os.makedirs(DIR_OSM, exist_ok=True)

    if not os.path.exists(POINTS_GPKG):
        print("Local GPKG missing. Restoring Component B points from GCS...")
        run_cmd(
            f'gcloud storage cp "{POINTS_GPKG_GCS}" "{POINTS_GPKG}"',
            check=True
        )

    if not os.path.exists(GRID_GPKG):
        print("Local grid missing. Restoring Component B grid from GCS...")
        run_cmd(
            f'gcloud storage cp "{GRID_GPKG_GCS}" "{GRID_GPKG}"',
            check=True
        )

    if not os.path.exists(POINTS_GPKG):
        raise FileNotFoundError(
            f"Component B points not found locally: {POINTS_GPKG}"
        )

    if not os.path.exists(GRID_GPKG):
        raise FileNotFoundError(
            f"Component B grid not found locally: {GRID_GPKG}"
        )

    print("Component B files ready.")
    print("POINTS_GPKG local:", POINTS_GPKG)
    print("GRID_GPKG local:", GRID_GPKG)
    print("POINTS_GPKG GCS:", POINTS_GPKG_GCS)
    print("GRID_GPKG GCS:", GRID_GPKG_GCS)

def prepare_resume_environment(force: bool = False):
    global _RESUME_PREPARED
    if _RESUME_PREPARED and not force:
        return

    ensure_componentB_points_ready()

    # Restore small-state files from GCS
    sync_prefix_from_gcs(f"{GCS_OUT_ROOT}/_componentC_done", LOCAL_PIPELINE_ROOT)
    sync_prefix_from_gcs(f"{GCS_OUT_ROOT}/_componentD_meta_done", LOCAL_PIPELINE_ROOT)

    for crop in CROP_CONFIG.keys():
        p = crop_paths(crop)
        base_gcs = f"{GCS_OUT_ROOT}/{crop}"

        # Component C outputs
        sync_prefix_from_gcs(f"{base_gcs}/componentC_points_labels", crop_base(crop))

        # Component D small files
        sync_prefix_from_gcs(f"{base_gcs}/componentD_metadata_hits_heading/03_gsv_metadata_parquet", os.path.dirname(p["compD_meta_dir"]))
        sync_prefix_from_gcs(f"{base_gcs}/componentD_metadata_hits_heading/04_hits", os.path.dirname(p["compD_hits_dir"]))
        sync_prefix_from_gcs(f"{base_gcs}/componentD_metadata_hits_heading/05_componentD", os.path.dirname(p["compD_heading_dir"]))
        sync_prefix_from_gcs(f"{base_gcs}/componentD_metadata_hits_heading/logs", os.path.dirname(p["logs"]))

        # Component E small files: only manifests, not all images
        sync_wildcard_from_gcs(
            f"{base_gcs}/componentE_gsv_images/06_images_train_NOT2025/_download_manifest_*",
            p["compE_train_dir"]
        )
        sync_wildcard_from_gcs(
            f"{base_gcs}/componentE_gsv_images/06_images_test_2025/_download_manifest_*",
            p["compE_test_dir"]
        )

    bootstrap_componentC_done_from_existing_outputs()
    bootstrap_componentD_meta_done_from_existing_outputs()

    _RESUME_PREPARED = True
    print("Resume environment prepared")

#GEE
try:
    ee.Initialize(project=PROJECT_ID)
    print("EE initialized with project:", PROJECT_ID)
except Exception as e:
    print("Need to authenticate:", e)
    ee.Authenticate()
    ee.Initialize(project=PROJECT_ID)
    print("EE initialized with project:", PROJECT_ID)

# ROI
hp_gdf = gpd.GeoDataFrame(
    {"name": ["tx_high_plains_selected_area"]},
    geometry=[box(*HP_BBOX)],
    crs="EPSG:4326"
)
hp_fc = geemap.gdf_to_ee(hp_gdf)
hp_geom_ee = hp_fc.geometry()


def md5(s: str) -> str:
    return hashlib.md5(s.encode("utf-8")).hexdigest()

def parse_ym(date_val):
    if pd.isna(date_val):
        return (None, None, None)
    s = str(date_val)
    m = re.match(r"^(\d{4})-(\d{2})", s)
    if not m:
        return (None, None, None)
    year = int(m.group(1))
    month = int(m.group(2))
    ym = int(f"{year:04d}{month:02d}")
    return (year, month, ym)

def haversine_m(lon1, lat1, lon2, lat2):
    R = 6371000.0
    lon1 = np.radians(lon1)
    lat1 = np.radians(lat1)
    lon2 = np.radians(lon2)
    lat2 = np.radians(lat2)
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    a = np.sin(dlat / 2.0) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2.0) ** 2
    return 2.0 * R * np.arcsin(np.sqrt(a))

def chunk_list(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i:i+n]


def get_cdl_image(year: int) -> ee.Image:
    return ee.Image(f"USDA/NASS/CDL/{year}").select(CDL_BAND).clip(hp_geom_ee)

def get_class_image(year: int) -> ee.Image:
    cdl = get_cdl_image(year)
    label_img = ee.Image(0).rename("class_id")
    label_img = label_img.where(cdl.eq(1), 1)
    label_img = label_img.where(cdl.eq(2), 2)
    label_img = label_img.where(cdl.eq(5), 3)
    label_img = label_img.where(cdl.eq(23).Or(cdl.eq(24)), 4)
    return label_img.clip(hp_geom_ee)

def get_crop_mask(crop: str, year: int) -> ee.Image:
    cdl = get_cdl_image(year)
    codes = CROP_CONFIG[crop]["cdl_codes"]
    mask = None
    for code in codes:
        m = cdl.eq(code)
        mask = m if mask is None else mask.Or(m)
    return mask.rename("is_crop").clip(hp_geom_ee)


def get_tile_ids_from_gpkg(gpkg_path: str, layer: str = "points") -> List[int]:
    if not os.path.exists(gpkg_path):
        raise FileNotFoundError(f"GPKG not found: {gpkg_path}")

    df = pyogrio.read_dataframe(
        gpkg_path,
        layer=layer,
        columns=["tile_id"],
        read_geometry=False
    )
    if "tile_id" not in df.columns:
        raise ValueError(f"'tile_id' column not found in {gpkg_path}, layer={layer}")

    return sorted(df["tile_id"].dropna().astype(int).unique().tolist())

def read_points_tile(gpkg_path: str, tile_id: int, layer: str = "points") -> gpd.GeoDataFrame:
    if not os.path.exists(gpkg_path):
        raise FileNotFoundError(f"GPKG not found: {gpkg_path}")

    gdf = pyogrio.read_dataframe(gpkg_path, layer=layer, where=f"tile_id = {int(tile_id)}")
    if len(gdf) == 0:
        return gdf

    gdf = gdf[gdf.geometry.intersects(hp_gdf.geometry.iloc[0])].copy()
    if len(gdf) == 0:
        return gdf

    gdf["lon"] = gdf.geometry.x.astype(float)
    gdf["lat"] = gdf.geometry.y.astype(float)
    return gdf

# ============================================================
# COMPONENT C
# ============================================================
def build_crop_point_tiles_from_componentB():
    prepare_resume_environment()

    class_img = get_class_image(YEAR_LABEL)
    tile_ids = get_tile_ids_from_gpkg(POINTS_GPKG, layer="points")

    done_tiles = [tid for tid in tile_ids if is_componentC_tile_done(tid)]
    pending_tiles = [tid for tid in tile_ids if not is_componentC_tile_done(tid)]

    print(f"Component C: total tiles   = {len(tile_ids)}")
    print(f"Component C: done tiles    = {len(done_tiles)}")
    print(f"Component C: pending tiles = {len(pending_tiles)}")

    for tile_id in tqdm(pending_tiles, desc="Component C"):
        gdf = read_points_tile(POINTS_GPKG, tile_id=tile_id, layer="points")

        if len(gdf) == 0:
            mark_componentC_tile_done(tile_id, {
                "tile_id": int(tile_id),
                "year_label": int(YEAR_LABEL),
                "status": "done",
                "reason": "empty_tile",
                "n_input_points": 0,
                "n_labeled_rows": 0,
                "crops_written": []
            })
            continue

        gdf = gdf.reset_index(drop=True)
        gdf["point_id"] = [
            md5(f"{tile_id}::{i}::{lon:.7f}::{lat:.7f}")
            for i, (lon, lat) in enumerate(zip(gdf["lon"], gdf["lat"]))
        ]

        sampled_rows = []
        recs = gdf[["point_id", "tile_id", "lon", "lat"]].to_dict("records")

        for chunk in chunk_list(recs, POINT_CHUNK):
            feats = [
                ee.Feature(
                    ee.Geometry.Point([float(r["lon"]), float(r["lat"])]),
                    {
                        "point_id": str(r["point_id"]),
                        "tile_id": int(r["tile_id"]),
                        "lon": float(r["lon"]),
                        "lat": float(r["lat"]),
                    }
                )
                for r in chunk
            ]

            fc = ee.FeatureCollection(feats)
            sampled = class_img.sampleRegions(
                collection=fc,
                scale=CDL_SCALE_M,
                geometries=False
            )
            info = sampled.getInfo()["features"]

            for f in info:
                p = f["properties"]
                class_id = int(p.get("class_id", 0))
                if class_id in CLASS_ID_TO_CROP:
                    crop = CLASS_ID_TO_CROP[class_id]
                    sampled_rows.append({
                        "point_id": p["point_id"],
                        "tile_id": int(p["tile_id"]),
                        "lon": float(p["lon"]),
                        "lat": float(p["lat"]),
                        "class_id": class_id,
                        "crop": crop,
                        "cdl_year": YEAR_LABEL,
                        "cdl_scale_m": CDL_SCALE_M,
                    })

        crops_written = []

        if len(sampled_rows) > 0:
            sdf = pd.DataFrame(sampled_rows)

            for crop in CROP_CONFIG.keys():
                sub = sdf[sdf["crop"] == crop].copy()
                if len(sub) == 0:
                    continue

                p = crop_paths(crop)
                out_path = os.path.join(p["compC_dir"], f"tile_{tile_id:04d}_{YEAR_LABEL}.parquet")
                sub.to_parquet(out_path, index=False, engine="pyarrow", compression="snappy")
                sync_file_to_gcs(out_path, local_to_gcs(out_path))
                crops_written.append(crop)

        mark_componentC_tile_done(tile_id, {
            "tile_id": int(tile_id),
            "year_label": int(YEAR_LABEL),
            "status": "done",
            "reason": "processed",
            "n_input_points": int(len(gdf)),
            "n_labeled_rows": int(len(sampled_rows)),
            "crops_written": crops_written
        })

# ============================================================
# COMPONENT D
# Concurrent Street View metadata requests
# Sample smoothed crop raster at left/right offsets
# ============================================================

from concurrent.futures import ThreadPoolExecutor, as_completed
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import requests
import numpy as np
import pandas as pd
import os, re, glob, json, time
from tqdm import tqdm
from pyproj import CRS, Transformer, Geod


D_METADATA_MAX_WORKERS = 8

def componentD_meta_done_dir(crop: str):
    d = os.path.join(LOCAL_PIPELINE_ROOT, "_componentD_meta_done", crop)
    os.makedirs(d, exist_ok=True)
    return d

def componentD_meta_done_marker(crop: str, tile_id: int) -> str:
    return os.path.join(componentD_meta_done_dir(crop), f"tile_{tile_id:04d}_{YEAR_LABEL}.done.json")

def is_componentD_meta_tile_done(crop: str, tile_id: int) -> bool:
    return os.path.exists(componentD_meta_done_marker(crop, tile_id))

def mark_componentD_meta_tile_done(crop: str, tile_id: int, stats: dict):
    marker = componentD_meta_done_marker(crop, tile_id)
    with open(marker, "w") as f:
        json.dump(stats, f, indent=2)
    sync_file_to_gcs(marker, local_to_gcs(marker))

def bootstrap_componentD_meta_done_from_existing_outputs():
    total = 0
    for crop in CROP_CONFIG.keys():
        p = crop_paths(crop)
        files = glob.glob(os.path.join(p["compD_meta_dir"], f"tile_*_{YEAR_LABEL}.parquet"))
        for fp in files:
            m = re.search(r"tile_(\d+)_", os.path.basename(fp))
            if not m:
                continue
            tile_id = int(m.group(1))
            if not is_componentD_meta_tile_done(crop, tile_id):
                mark_componentD_meta_tile_done(crop, tile_id, {
                    "crop": crop,
                    "tile_id": int(tile_id),
                    "year_label": int(YEAR_LABEL),
                    "status": "done",
                    "reason": "bootstrapped_from_existing_outputs",
                })
                total += 1
    print(f"Bootstrapped Component D metadata done markers: {total}")


def _make_gsv_session():
    s = requests.Session()
    retries = Retry(
        total=5,
        backoff_factor=0.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    adapter = HTTPAdapter(max_retries=retries, pool_connections=50, pool_maxsize=50)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s

def _gsv_metadata_request(session, lat, lon, radius_m=30, source="outdoor"):
    if GSV_KEY is None:
        raise ValueError("GSV_KEY is not set.")
    url = "https://maps.googleapis.com/maps/api/streetview/metadata"
    params = {
        "location": f"{lat},{lon}",
        "radius": radius_m,
        "source": source,
        "key": GSV_KEY,
    }
    r = session.get(url, params=params, timeout=30)
    r.raise_for_status()
    return r.json()

def _metadata_worker(rec, crop):
    session = _make_gsv_session()
    try:
        js = _gsv_metadata_request(
            session=session,
            lat=float(rec["lat"]),
            lon=float(rec["lon"]),
            radius_m=BUFFER_M,
            source="outdoor"
        )
        return {
            "point_id": rec["point_id"],
            "tile_id": int(rec["tile_id"]),
            "crop": crop,
            "class_id": int(rec["class_id"]),
            "query_lon": float(rec["lon"]),
            "query_lat": float(rec["lat"]),
            "status": js.get("status", "UNKNOWN"),
            "pano_id": js.get("pano_id"),
            "date": js.get("date"),
            "pano_lon": (js.get("location") or {}).get("lng"),
            "pano_lat": (js.get("location") or {}).get("lat"),
            "cdl_year": int(rec["cdl_year"]),
            "cdl_scale_m": int(rec["cdl_scale_m"]),
            "error": None,
        }
    except Exception as e:
        return {
            "point_id": rec["point_id"],
            "tile_id": int(rec["tile_id"]),
            "crop": crop,
            "class_id": int(rec["class_id"]),
            "query_lon": float(rec["lon"]),
            "query_lat": float(rec["lat"]),
            "status": "ERROR",
            "pano_id": None,
            "date": None,
            "pano_lon": None,
            "pano_lat": None,
            "cdl_year": int(rec["cdl_year"]),
            "cdl_scale_m": int(rec["cdl_scale_m"]),
            "error": str(e),
        }
    finally:
        session.close()

# GSV METADATA
def run_gsv_metadata(crop: str, max_workers: int = D_METADATA_MAX_WORKERS):
    prepare_resume_environment()

    p = crop_paths(crop)
    points_dir = p["compC_dir"]
    meta_dir = p["compD_meta_dir"]
    os.makedirs(meta_dir, exist_ok=True)

    tile_files = sorted(glob.glob(os.path.join(points_dir, f"tile_*_{YEAR_LABEL}.parquet")))
    pending = []

    for pf in tile_files:
        m = re.search(r"tile_(\d+)_", os.path.basename(pf))
        if not m:
            continue
        tile_id = int(m.group(1))
        if not is_componentD_meta_tile_done(crop, tile_id):
            pending.append((tile_id, pf))

    print(f"{crop} metadata: total tiles   = {len(tile_files)}")
    print(f"{crop} metadata: pending tiles = {len(pending)}")
    print(f"{crop} metadata: max_workers   = {max_workers}")

    for tile_id, pf in tqdm(pending, desc=f"{crop} metadata"):
        out_path = os.path.join(meta_dir, f"tile_{tile_id:04d}_{YEAR_LABEL}.parquet")

        # non-destructive skip if local output already exists
        if os.path.exists(out_path):
            mark_componentD_meta_tile_done(crop, tile_id, {
                "crop": crop,
                "tile_id": int(tile_id),
                "year_label": int(YEAR_LABEL),
                "status": "done",
                "reason": "existing_local_output",
            })
            continue

        df = pd.read_parquet(pf)
        if len(df) == 0:
            pd.DataFrame().to_parquet(out_path, index=False, engine="pyarrow", compression="snappy")
            sync_file_to_gcs(out_path, local_to_gcs(out_path))
            mark_componentD_meta_tile_done(crop, tile_id, {
                "crop": crop,
                "tile_id": int(tile_id),
                "year_label": int(YEAR_LABEL),
                "status": "done",
                "reason": "empty_input_tile",
                "n_rows": 0,
            })
            continue

        records = df.to_dict("records")
        out_rows = []

        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = [ex.submit(_metadata_worker, rec, crop) for rec in records]
            for fut in as_completed(futures):
                out_rows.append(fut.result())

        out_df = pd.DataFrame(out_rows)
        if len(out_df) > 0:
            out_df = out_df.sort_values(["tile_id", "point_id"]).reset_index(drop=True)

        out_df.to_parquet(out_path, index=False, engine="pyarrow", compression="snappy")
        sync_file_to_gcs(out_path, local_to_gcs(out_path))

        mark_componentD_meta_tile_done(crop, tile_id, {
            "crop": crop,
            "tile_id": int(tile_id),
            "year_label": int(YEAR_LABEL),
            "status": "done",
            "reason": "processed",
            "n_rows": int(len(out_df)),
            "n_ok": int((out_df["status"] == "OK").sum()) if "status" in out_df.columns else 0,
            "n_error": int((out_df["status"] == "ERROR").sum()) if "status" in out_df.columns else 0,
        })

# HITS + UNIQUE PANOS + SPLIT
def build_hits_and_unique_panos(crop: str, force: bool = False):
    prepare_resume_environment()

    p = crop_paths(crop)
    out_hits  = os.path.join(p["compD_hits_dir"], f"gsv_hits_{crop}_CDL{YEAR_LABEL}_radius{BUFFER_M}m.csv")
    out_panos = os.path.join(p["compD_hits_dir"], f"gsv_panos_unique_{crop}_{YEAR_LABEL}.csv")
    out_train = os.path.join(p["compD_hits_dir"], f"gsv_panos_unique_{crop}_NOT{TEST_YEAR}.csv")
    out_test  = os.path.join(p["compD_hits_dir"], f"gsv_panos_unique_{crop}_{TEST_YEAR}.csv")

    if (not force and
        os.path.exists(out_hits) and os.path.exists(out_panos) and
        os.path.exists(out_train) and os.path.exists(out_test)):
        print(f"{crop} hits: outputs already exist, skipping")
        return

    files = sorted(glob.glob(os.path.join(p["compD_meta_dir"], f"tile_*_{YEAR_LABEL}.parquet")))
    rows = []

    for fp in tqdm(files, desc=f"{crop} hits"):
        df = pd.read_parquet(fp)
        ok = df[
            (df["status"] == "OK") &
            df["pano_id"].notna() &
            df["pano_lon"].notna() &
            df["pano_lat"].notna()
        ].copy()

        if len(ok) == 0:
            continue

        parsed = ok["date"].apply(parse_ym)
        ok["year"] = parsed.apply(lambda t: t[0])
        ok["month"] = parsed.apply(lambda t: t[1])
        ok["ym"] = parsed.apply(lambda t: t[2])

        ok = ok[ok["month"].isin(CROP_CONFIG[crop]["grow_months"])].copy()
        if len(ok) == 0:
            continue

        ok["road_dist_m"] = haversine_m(
            ok["query_lon"].values, ok["query_lat"].values,
            ok["pano_lon"].values, ok["pano_lat"].values
        )

        ok = ok.rename(columns={"pano_lon": "lon_road", "pano_lat": "lat_road"})
        rows.append(ok)

    hits = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
    hits.to_csv(out_hits, index=False)
    sync_file_to_gcs(out_hits, local_to_gcs(out_hits))

    if len(hits) == 0:
        pd.DataFrame().to_csv(out_panos, index=False)
        pd.DataFrame().to_csv(out_train, index=False)
        pd.DataFrame().to_csv(out_test, index=False)
        sync_file_to_gcs(out_panos, local_to_gcs(out_panos))
        sync_file_to_gcs(out_train, local_to_gcs(out_train))
        sync_file_to_gcs(out_test, local_to_gcs(out_test))
        return

    panos = hits.sort_values(["pano_id", "road_dist_m"]).drop_duplicates(subset=["pano_id"], keep="first").copy()
    panos.to_csv(out_panos, index=False)
    sync_file_to_gcs(out_panos, local_to_gcs(out_panos))

    panos_train = panos[panos["year"] != TEST_YEAR].copy()
    panos_test  = panos[panos["year"] == TEST_YEAR].copy()

    panos_train.to_csv(out_train, index=False)
    panos_test.to_csv(out_test, index=False)

    sync_file_to_gcs(out_train, local_to_gcs(out_train))
    sync_file_to_gcs(out_test, local_to_gcs(out_test))

# FIELD-FACING HEADING
GEOD = Geod(ellps="WGS84")

def utm_epsg_from_lonlat(lon, lat):
    zone = int(np.floor((lon + 180) / 6) + 1)
    return (32600 + zone) if lat >= 0 else (32700 + zone)

def bearing_deg_from_vec(dx, dy):
    ang = np.degrees(np.arctan2(dx, dy))
    return (ang + 360) % 360

def pca_bearing_xy(xy):
    if xy.shape[0] < 2:
        return np.nan
    centered = xy - xy.mean(axis=0, keepdims=True)
    cov = centered.T @ centered
    if not np.isfinite(cov).all():
        return np.nan
    vals, vecs = np.linalg.eig(cov)
    vals = np.real(vals)
    vecs = np.real(vecs)
    v = vecs[:, int(np.argmax(vals))]
    return bearing_deg_from_vec(float(v[0]), float(v[1]))

def circular_mean_deg(deg):
    deg = np.asarray(deg, dtype=float)
    deg = deg[np.isfinite(deg)]
    if deg.size == 0:
        return np.nan
    rad = np.deg2rad(deg)
    m = np.arctan2(np.mean(np.sin(rad)), np.mean(np.cos(rad)))
    return float((np.rad2deg(m) + 360) % 360)

def geodesic_fwd(lon, lat, heading_deg, dist_m):
    lon2, lat2, _ = GEOD.fwd(lon, lat, heading_deg, dist_m)
    return lon2, lat2

def compute_bearing_for_tile(dft, k=10):
    lon0 = float(dft["lon_road"].median())
    lat0 = float(dft["lat_road"].median())
    epsg = utm_epsg_from_lonlat(lon0, lat0)
    tf = Transformer.from_crs(CRS.from_epsg(4326), CRS.from_epsg(epsg), always_xy=True)
    E, N = tf.transform(dft["lon_road"].values, dft["lat_road"].values)
    coords = np.vstack([E, N]).T

    bearing = np.full(len(dft), np.nan, dtype=float)
    for i in range(len(dft)):
        d2 = np.sum((coords - coords[i]) ** 2, axis=1)
        nn = np.argsort(d2)[:min(k, len(dft))]
        bearing[i] = pca_bearing_xy(coords[nn])

    out = dft.copy()
    out["utm_epsg"] = epsg
    out["bearing_road"] = bearing
    fb = circular_mean_deg(out["bearing_road"].values)
    if not np.isfinite(fb):
        fb = 0.0
    out["bearing_road"] = out["bearing_road"].fillna(fb)
    out["heading_left"] = (out["bearing_road"] - 90) % 360
    out["heading_right"] = (out["bearing_road"] + 90) % 360
    return out

def run_componentD_for_crop(crop: str, split: str = "train", force: bool = False):
    prepare_resume_environment()

    p = crop_paths(crop)
    if split == "train":
        in_csv = os.path.join(p["compD_hits_dir"], f"gsv_panos_unique_{crop}_NOT{TEST_YEAR}.csv")
        out_csv = os.path.join(p["compD_heading_dir"], f"componentD_{crop}_NOT{TEST_YEAR}_fieldtargets_YEAR{YEAR_LABEL}.csv")
    else:
        in_csv = os.path.join(p["compD_hits_dir"], f"gsv_panos_unique_{crop}_{TEST_YEAR}.csv")
        out_csv = os.path.join(p["compD_heading_dir"], f"componentD_{crop}_{TEST_YEAR}_fieldtargets_YEAR{YEAR_LABEL}.csv")

    if not force and os.path.exists(out_csv):
        print(f"{crop} {split} heading: output already exists, skipping")
        return

    panos = pd.read_csv(in_csv).dropna(subset=["lon_road", "lat_road"]).copy()
    if len(panos) == 0:
        pd.DataFrame().to_csv(out_csv, index=False)
        sync_file_to_gcs(out_csv, local_to_gcs(out_csv))
        return

    tiles = sorted(panos["tile_id"].unique())
    pieces = []
    for tid in tqdm(tiles, desc=f"{crop} {split} heading"):
        pieces.append(compute_bearing_for_tile(panos[panos["tile_id"] == tid].copy(), k=10))

    d = pd.concat(pieces, ignore_index=True)
    is_crop = get_crop_mask(crop, YEAR_LABEL)

    def build_fc_chunk(df_chunk):
        feats = []
        for i, r in df_chunk.iterrows():
            lon = float(r["lon_road"])
            lat = float(r["lat_road"])
            hL = float(r["heading_left"])
            hR = float(r["heading_right"])

            lonL, latL = geodesic_fwd(lon, lat, hL, FIELD_OFFSET_M)
            lonR, latR = geodesic_fwd(lon, lat, hR, FIELD_OFFSET_M)

            feats.append(
                ee.Feature(ee.Geometry.Point([lon, lat]), {
                    "idx": int(i),
                    "lonL": float(lonL), "latL": float(latL),
                    "lonR": float(lonR), "latR": float(latR)
                })
            )
        return ee.FeatureCollection(feats)

    def add_cropfrac(feat):
        lonL = ee.Number(feat.get("lonL"))
        latL = ee.Number(feat.get("latL"))
        lonR = ee.Number(feat.get("lonR"))
        latR = ee.Number(feat.get("latR"))

        bufL = ee.Geometry.Point([lonL, latL]).buffer(FIELD_BUF_M)
        bufR = ee.Geometry.Point([lonR, latR]).buffer(FIELD_BUF_M)

        cfL = is_crop.reduceRegion(
            reducer=ee.Reducer.mean(),
            geometry=bufL,
            scale=CDL_SCALE_M,
            maxPixels=1e7,
            bestEffort=True
        ).get("is_crop")

        cfR = is_crop.reduceRegion(
            reducer=ee.Reducer.mean(),
            geometry=bufR,
            scale=CDL_SCALE_M,
            maxPixels=1e7,
            bestEffort=True
        ).get("is_crop")

        cfL = ee.Algorithms.If(cfL, cfL, 0)
        cfR = ee.Algorithms.If(cfR, cfR, 0)

        return feat.set({"cropfrac_left": cfL, "cropfrac_right": cfR})

    d = d.reset_index(drop=True)
    cropL = np.full(len(d), np.nan)
    cropR = np.full(len(d), np.nan)

    for start in tqdm(range(0, len(d), HEADING_CHUNK), desc=f"{crop} {split} cropfrac"):
        end = min(len(d), start + HEADING_CHUNK)
        chunk = d.iloc[start:end].copy()
        fc = build_fc_chunk(chunk)
        info = fc.map(add_cropfrac).getInfo()["features"]

        idx_to_pos = {int(idx): pos for pos, idx in enumerate(chunk.index.values)}
        for f in info:
            idx = int(f["properties"]["idx"])
            pos = idx_to_pos[idx]
            cropL[start + pos] = float(f["properties"]["cropfrac_left"])
            cropR[start + pos] = float(f["properties"]["cropfrac_right"])

        d["cropfrac_left"] = cropL
        d["cropfrac_right"] = cropR

    def choose_side(row):
        L = row["cropfrac_left"]
        R = row["cropfrac_right"]
        if not np.isfinite(L):
            L = -1
        if not np.isfinite(R):
            R = -1
        if L > R:
            return row["heading_left"], "left", L
        if R > L:
            return row["heading_right"], "right", R
        return row["heading_right"], "tie_right", R

    chosen = d.apply(choose_side, axis=1, result_type="expand")
    d["heading_best"] = chosen[0].astype(float)
    d["side_choice"] = chosen[1].astype(str)
    d["cropfrac_best"] = chosen[2].astype(float)

    field = d.apply(
        lambda r: geodesic_fwd(
            float(r["lon_road"]),
            float(r["lat_road"]),
            float(r["heading_best"]),
            FIELD_OFFSET_M
        ),
        axis=1,
        result_type="expand"
    )
    d["field_lon"] = field[0].astype(float)
    d["field_lat"] = field[1].astype(float)

    d.to_csv(out_csv, index=False)
    sync_file_to_gcs(out_csv, local_to_gcs(out_csv))


def run_all_componentD(max_workers: int = 8):
    prepare_resume_environment()
    bootstrap_componentD_meta_done_from_existing_outputs()

    for crop in CROP_CONFIG.keys():
        run_gsv_metadata(crop, max_workers=max_workers)
        build_hits_and_unique_panos(crop)
        run_componentD_for_crop(crop, split="train")
        run_componentD_for_crop(crop, split="test")

# ============================================================
# COMPONENT E: DOWNLOAD GSV IMAGES
# ============================================================
def download_crop_images(crop: str, split: str = "train"):
    prepare_resume_environment()

    p = crop_paths(crop)

    if split == "train":
        in_csv = os.path.join(p["compD_heading_dir"], f"componentD_{crop}_NOT{TEST_YEAR}_fieldtargets_YEAR{YEAR_LABEL}.csv")
        img_dir = p["compE_train_dir"]
        done_log = os.path.join(p["logs"], f"done_download_train_YEAR{YEAR_LABEL}.json")
        manifest_csv = os.path.join(p["compE_train_dir"], f"_download_manifest_{crop}_NOT{TEST_YEAR}.csv")
    else:
        in_csv = os.path.join(p["compD_heading_dir"], f"componentD_{crop}_{TEST_YEAR}_fieldtargets_YEAR{YEAR_LABEL}.csv")
        img_dir = p["compE_test_dir"]
        done_log = os.path.join(p["logs"], f"done_download_test_YEAR{YEAR_LABEL}.json")
        manifest_csv = os.path.join(p["compE_test_dir"], f"_download_manifest_{crop}_{TEST_YEAR}.csv")

    os.makedirs(img_dir, exist_ok=True)

    # Restore latest done log if GCS has it and local doesn't
    if not os.path.exists(done_log):
        sync_file = local_to_gcs(done_log)
        if gcs_exists(sync_file):
            sync_wildcard_from_gcs(sync_file, os.path.dirname(done_log))

    done = set(json.load(open(done_log))) if os.path.exists(done_log) else set()

    df = pd.read_csv(in_csv)
    if len(df) == 0:
        pd.DataFrame().to_csv(manifest_csv, index=False)
        sync_file_to_gcs(manifest_csv, local_to_gcs(manifest_csv))
        return

    df = df[df["cropfrac_best"].fillna(-1) >= MIN_CROPFRAC].copy()

    session = requests.Session()
    retries = Retry(total=5, backoff_factor=0.5, status_forcelist=[429, 500, 502, 503, 504])
    session.mount("https://", HTTPAdapter(max_retries=retries))

    def streetview_image_url(pano_id, heading):
        url = "https://maps.googleapis.com/maps/api/streetview"
        params = {
            "pano": pano_id,
            "size": GSV_SIZE,
            "fov": str(GSV_FOV),
            "pitch": str(GSV_PITCH),
            "heading": str(float(heading)),
            "source": "outdoor",
            "key": GSV_KEY
        }
        return url, params

    manifest_rows = []
    new_dl = 0

    for r in tqdm(df.itertuples(index=False), total=len(df), desc=f"{crop} {split} download"):
        pano_id = str(r.pano_id)
        point_id = str(r.point_id)

        if pano_id in done:
            continue

        heading_int = int(round(float(r.heading_best)))
        fname = f"{point_id}__{pano_id}_h{heading_int}_f{GSV_FOV}_p{GSV_PITCH}_{GSV_SIZE}.jpg"
        out_path = os.path.join(img_dir, fname)

        ok = False
        url, params = streetview_image_url(pano_id, r.heading_best)

        try:
            resp = session.get(url, params=params, timeout=60)
            resp.raise_for_status()

            with open(out_path, "wb") as f:
                f.write(resp.content)

            if os.path.getsize(out_path) >= 10_000:
                ok = True
                done.add(pano_id)
                new_dl += 1
                sync_file_to_gcs(out_path, local_to_gcs(out_path))
        except Exception:
            ok = False

        manifest_rows.append({
            "point_id": point_id,
            "pano_id": pano_id,
            "img_path": out_path,
            "heading_best": float(r.heading_best),
            "cropfrac_best": float(r.cropfrac_best),
            "date": getattr(r, "date", None),
            "year": getattr(r, "year", None),
            "month": getattr(r, "month", None),
            "crop": crop,
            "split": split,
            "download_ok": ok,
        })

        if new_dl > 0 and new_dl % 25 == 0:
            with open(done_log, "w") as f:
                json.dump(sorted(list(done)), f)
            sync_file_to_gcs(done_log, local_to_gcs(done_log))

        time.sleep(0.5)

    with open(done_log, "w") as f:
        json.dump(sorted(list(done)), f)
    sync_file_to_gcs(done_log, local_to_gcs(done_log))

    if len(manifest_rows) > 0:
        old = pd.read_csv(manifest_csv) if os.path.exists(manifest_csv) else pd.DataFrame()
        new = pd.DataFrame(manifest_rows)
        merged = pd.concat([old, new], ignore_index=True)
        merged = merged.drop_duplicates(subset=["pano_id"], keep="last")
        merged.to_csv(manifest_csv, index=False)
    else:
        if not os.path.exists(manifest_csv):
            pd.DataFrame().to_csv(manifest_csv, index=False)

    sync_file_to_gcs(manifest_csv, local_to_gcs(manifest_csv))


def run_all_componentC():
    prepare_resume_environment()
    print("Using POINTS_GPKG:", POINTS_GPKG)
    print("Exists:", os.path.exists(POINTS_GPKG))
    if os.path.exists(POINTS_GPKG):
        print("Size (bytes):", os.path.getsize(POINTS_GPKG))
    build_crop_point_tiles_from_componentB()

def run_all_componentD():
    prepare_resume_environment()
    for crop in CROP_CONFIG.keys():
        run_gsv_metadata(crop)
        build_hits_and_unique_panos(crop)
        run_componentD_for_crop(crop, split="train")
        run_componentD_for_crop(crop, split="test")

def run_all_componentE():
    prepare_resume_environment()
    for crop in CROP_CONFIG.keys():
        download_crop_images(crop, split="train")
        download_crop_images(crop, split="test")

# SUMMARY
print("PROJECT_ID:", PROJECT_ID)
print("YEAR_LABEL:", YEAR_LABEL)
print("TEST_YEAR:", TEST_YEAR)
print("CDL_SOURCE_MODE:", CDL_SOURCE_MODE)
print("CDL_SCALE_M:", CDL_SCALE_M)
print("POINTS_GPKG:", POINTS_GPKG)
print("GCS output root:", GCS_OUT_ROOT)
print("Component C folder example:", f"{GCS_OUT_ROOT}/cotton/componentC_points_labels/")
print("Component D folder example:", f"{GCS_OUT_ROOT}/cotton/componentD_metadata_hits_heading/")
print("Component E folder example:", f"{GCS_OUT_ROOT}/cotton/componentE_gsv_images/")

def download_crop_images(crop: str, split: str = "train"):
    prepare_resume_environment()

    p = crop_paths(crop)

    if split == "train":
        in_csv = os.path.join(
            p["compD_heading_dir"],
            f"componentD_{crop}_NOT{TEST_YEAR}_fieldtargets_YEAR{YEAR_LABEL}.csv"
        )
        img_dir = p["compE_train_dir"]
        done_log = os.path.join(p["logs"], f"done_download_train_YEAR{YEAR_LABEL}.json")
        manifest_csv = os.path.join(
            p["compE_train_dir"],
            f"_download_manifest_{crop}_NOT{TEST_YEAR}.csv"
        )
    else:
        in_csv = os.path.join(
            p["compD_heading_dir"],
            f"componentD_{crop}_{TEST_YEAR}_fieldtargets_YEAR{YEAR_LABEL}.csv"
        )
        img_dir = p["compE_test_dir"]
        done_log = os.path.join(p["logs"], f"done_download_test_YEAR{YEAR_LABEL}.json")
        manifest_csv = os.path.join(
            p["compE_test_dir"],
            f"_download_manifest_{crop}_{TEST_YEAR}.csv"
        )

    os.makedirs(img_dir, exist_ok=True)

    # restore done log if present in GCS
    if not os.path.exists(done_log):
        gcs_done = local_to_gcs(done_log)
        if gcs_exists(gcs_done):
            sync_wildcard_from_gcs(gcs_done, os.path.dirname(done_log))

    done = set(json.load(open(done_log))) if os.path.exists(done_log) else set()

    # SAFE READ OF INPUT CSV
    if not os.path.exists(in_csv):
        print(f"{crop} {split}: input CSV not found, skipping")
        if not os.path.exists(manifest_csv):
            pd.DataFrame(columns=[
                "point_id", "pano_id", "img_path", "heading_best",
                "cropfrac_best", "date", "year", "month",
                "crop", "split", "download_ok"
            ]).to_csv(manifest_csv, index=False)
            sync_file_to_gcs(manifest_csv, local_to_gcs(manifest_csv))
        return

    if os.path.getsize(in_csv) == 0:
        print(f"{crop} {split}: input CSV is empty, skipping")
        if not os.path.exists(manifest_csv):
            pd.DataFrame(columns=[
                "point_id", "pano_id", "img_path", "heading_best",
                "cropfrac_best", "date", "year", "month",
                "crop", "split", "download_ok"
            ]).to_csv(manifest_csv, index=False)
            sync_file_to_gcs(manifest_csv, local_to_gcs(manifest_csv))
        return

    try:
        df = pd.read_csv(in_csv)
    except pd.errors.EmptyDataError:
        print(f"{crop} {split}: input CSV has no columns, skipping")
        if not os.path.exists(manifest_csv):
            pd.DataFrame(columns=[
                "point_id", "pano_id", "img_path", "heading_best",
                "cropfrac_best", "date", "year", "month",
                "crop", "split", "download_ok"
            ]).to_csv(manifest_csv, index=False)
            sync_file_to_gcs(manifest_csv, local_to_gcs(manifest_csv))
        return

    if len(df) == 0:
        print(f"{crop} {split}: input CSV has 0 rows, skipping")
        if not os.path.exists(manifest_csv):
            pd.DataFrame(columns=[
                "point_id", "pano_id", "img_path", "heading_best",
                "cropfrac_best", "date", "year", "month",
                "crop", "split", "download_ok"
            ]).to_csv(manifest_csv, index=False)
            sync_file_to_gcs(manifest_csv, local_to_gcs(manifest_csv))
        return

    # make sure required columns exist
    required_cols = ["pano_id", "heading_best", "cropfrac_best"]
    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        print(f"{crop} {split}: missing required columns {missing}, skipping")
        if not os.path.exists(manifest_csv):
            pd.DataFrame(columns=[
                "point_id", "pano_id", "img_path", "heading_best",
                "cropfrac_best", "date", "year", "month",
                "crop", "split", "download_ok"
            ]).to_csv(manifest_csv, index=False)
            sync_file_to_gcs(manifest_csv, local_to_gcs(manifest_csv))
        return

    df = df[df["cropfrac_best"].fillna(-1) >= MIN_CROPFRAC].copy()

    if len(df) == 0:
        print(f"{crop} {split}: no rows passed cropfrac threshold")
        if not os.path.exists(manifest_csv):
            pd.DataFrame(columns=[
                "point_id", "pano_id", "img_path", "heading_best",
                "cropfrac_best", "date", "year", "month",
                "crop", "split", "download_ok"
            ]).to_csv(manifest_csv, index=False)
            sync_file_to_gcs(manifest_csv, local_to_gcs(manifest_csv))
        return

    session = requests.Session()
    retries = Retry(total=5, backoff_factor=0.5, status_forcelist=[429, 500, 502, 503, 504])
    session.mount("https://", HTTPAdapter(max_retries=retries))

    def streetview_image_url(pano_id, heading):
        url = "https://maps.googleapis.com/maps/api/streetview"
        params = {
            "pano": pano_id,
            "size": GSV_SIZE,
            "fov": str(GSV_FOV),
            "pitch": str(GSV_PITCH),
            "heading": str(float(heading)),
            "source": "outdoor",
            "key": GSV_KEY
        }
        return url, params

    manifest_rows = []
    new_dl = 0

    for r in tqdm(df.itertuples(index=False), total=len(df), desc=f"{crop} {split} download"):
        pano_id = str(r.pano_id)
        point_id = str(getattr(r, "point_id", pano_id))

        if pano_id in done:
            continue

        heading_int = int(round(float(r.heading_best)))
        fname = f"{point_id}__{pano_id}_h{heading_int}_f{GSV_FOV}_p{GSV_PITCH}_{GSV_SIZE}.jpg"
        out_path = os.path.join(img_dir, fname)

        ok = False
        url, params = streetview_image_url(pano_id, r.heading_best)

        try:
            resp = session.get(url, params=params, timeout=60)
            resp.raise_for_status()

            with open(out_path, "wb") as f:
                f.write(resp.content)

            if os.path.getsize(out_path) >= 10_000:
                ok = True
                done.add(pano_id)
                new_dl += 1
                sync_file_to_gcs(out_path, local_to_gcs(out_path))
        except Exception:
            ok = False

        manifest_rows.append({
            "point_id": point_id,
            "pano_id": pano_id,
            "img_path": out_path,
            "heading_best": float(r.heading_best),
            "cropfrac_best": float(r.cropfrac_best),
            "date": getattr(r, "date", None),
            "year": getattr(r, "year", None),
            "month": getattr(r, "month", None),
            "crop": crop,
            "split": split,
            "download_ok": ok,
        })

        if new_dl > 0 and new_dl % 25 == 0:
            with open(done_log, "w") as f:
                json.dump(sorted(list(done)), f)
            sync_file_to_gcs(done_log, local_to_gcs(done_log))

        time.sleep(0.5)

    with open(done_log, "w") as f:
        json.dump(sorted(list(done)), f)
    sync_file_to_gcs(done_log, local_to_gcs(done_log))

    manifest_cols = [
        "point_id", "pano_id", "img_path", "heading_best",
        "cropfrac_best", "date", "year", "month",
        "crop", "split", "download_ok"
    ]

    old = pd.read_csv(manifest_csv) if (os.path.exists(manifest_csv) and os.path.getsize(manifest_csv) > 0) else pd.DataFrame(columns=manifest_cols)
    new = pd.DataFrame(manifest_rows, columns=manifest_cols) if len(manifest_rows) > 0 else pd.DataFrame(columns=manifest_cols)

    merged = pd.concat([old, new], ignore_index=True)
    if len(merged) > 0 and "pano_id" in merged.columns:
        merged = merged.drop_duplicates(subset=["pano_id"], keep="last")

    merged.to_csv(manifest_csv, index=False)
    sync_file_to_gcs(manifest_csv, local_to_gcs(manifest_csv))

run_all_componentC()

run_all_componentD()

run_all_componentE()

"""#ComponentF"""

!pip install -q open_clip_torch segment-anything google-cloud-storage
from google.colab import auth
auth.authenticate_user()

import google.auth
from google.cloud import storage

creds, project = google.auth.default()
print("Authenticated project from ADC:", project)

gcs_client = storage.Client(project="gcp-clag-remote-mapping", credentials=creds)
print("GCS client authenticated")

# ============================================================
# COMPONENT F, PANHANDLE, ML model(Crop Classifier)
# ============================================================

!pip -q install open_clip_torch segment-anything google-cloud-storage pillow pandas numpy torch

import os
import io
import json
import urllib.request
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Dict, Tuple

import numpy as np
import pandas as pd
from PIL import Image

import torch
import open_clip
from google.cloud import storage
from segment_anything import sam_model_registry, SamAutomaticMaskGenerator


RUN_ID = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

@dataclass
class Model2PanhandleConfig:
    project_id: str = "gcp-clag-remote-mapping"
    device: str = "cuda" if torch.cuda.is_available() else "cpu"

    # Correct Component C/D/E roots
    local_pipeline_root: str = "/content/high_plains_crop_mapping/componentC_D_E_outputs/high_plains_multicrop_pipeline"
    gcs_pipeline_root: str = "gs://storage_cropmapping/Finals/high_plains_crop_mapping/componentC_D_E_outputs/high_plains_multicrop_pipeline"

    test_year: int = 2025

    # Component F outputs
    local_out_root: str = f"/content/componentF_panhandle_inference_model2/{RUN_ID}"
    gcs_out_root: str = f"gs://storage_cropmapping/Finals/high_plains_crop_mapping/componentF_panhandle_inference_model2/{RUN_ID}"

    crops: Tuple[str, ...] = ("corn", "cotton", "soybean", "wheat")

    clip_model_name: str = "ViT-L-14"
    clip_pretrained: str = "laion2b_s32b_b82k"

    sam_checkpoint: str = "/content/sam_vit_l_0b3195.pth"
    sam_type: str = "vit_l"

    top_frac: float = 0.40
    bot_frac: float = 0.37
    side_frac: float = 0.20

CFG2 = Model2PanhandleConfig()
os.makedirs(CFG2.local_out_root, exist_ok=True)

print("RUN_ID:", RUN_ID)
print("Device:", CFG2.device)
print("Component E local root:", CFG2.local_pipeline_root)
print("Component E GCS root:", CFG2.gcs_pipeline_root)
print("Output local root:", CFG2.local_out_root)
print("Output GCS root:", CFG2.gcs_out_root)


gcs_client = storage.Client(project=CFG2.project_id)

def split_gcs_path(gcs_path: str):
    no_prefix = gcs_path.replace("gs://", "", 1)
    bucket = no_prefix.split("/")[0]
    blob = "/".join(no_prefix.split("/")[1:])
    return bucket, blob

def gcs_exists(gcs_path: str) -> bool:
    if not str(gcs_path).startswith("gs://"):
        return os.path.exists(gcs_path)
    bucket, blob = split_gcs_path(gcs_path)
    return gcs_client.bucket(bucket).blob(blob).exists()

def upload_file_to_gcs(local_path: str, gcs_path: str):
    if not os.path.exists(local_path):
        print("SKIP upload missing:", local_path)
        return
    bucket, blob = split_gcs_path(gcs_path)
    gcs_client.bucket(bucket).blob(blob).upload_from_filename(local_path)
    print(f"Uploaded: {local_path} -> {gcs_path}")

def read_csv_any(path: str) -> pd.DataFrame:
    if str(path).startswith("gs://"):
        if not gcs_exists(path):
            raise FileNotFoundError(f"GCS CSV does not exist: {path}")
        bucket, blob = split_gcs_path(path)
        buf = io.BytesIO()
        gcs_client.bucket(bucket).blob(blob).download_to_file(buf)
        buf.seek(0)
        return pd.read_csv(buf)
    return pd.read_csv(path)

def load_image_any(path: str) -> Image.Image:
    path = str(path)
    if path.startswith("gs://"):
        if not gcs_exists(path):
            raise FileNotFoundError(f"GCS image does not exist: {path}")
        bucket, blob = split_gcs_path(path)
        buf = io.BytesIO()
        gcs_client.bucket(bucket).blob(blob).download_to_file(buf)
        buf.seek(0)
        return Image.open(buf).convert("RGB")
    return Image.open(path).convert("RGB")

# COMPONENT E MANIFEST 
def componentE_manifest_local_path(crop: str, split: str) -> str:
    if split == "test":
        return os.path.join(
            CFG2.local_pipeline_root,
            crop,
            "componentE_gsv_images",
            f"06_images_test_{CFG2.test_year}",
            f"_download_manifest_{crop}_{CFG2.test_year}.csv",
        )
    elif split == "train":
        return os.path.join(
            CFG2.local_pipeline_root,
            crop,
            "componentE_gsv_images",
            f"06_images_train_NOT{CFG2.test_year}",
            f"_download_manifest_{crop}_NOT{CFG2.test_year}.csv",
        )
    else:
        raise ValueError("split must be 'train' or 'test'")

def componentE_manifest_gcs_path(crop: str, split: str) -> str:
    if split == "test":
        return (
            f"{CFG2.gcs_pipeline_root}/{crop}/componentE_gsv_images/"
            f"06_images_test_{CFG2.test_year}/"
            f"_download_manifest_{crop}_{CFG2.test_year}.csv"
        )
    elif split == "train":
        return (
            f"{CFG2.gcs_pipeline_root}/{crop}/componentE_gsv_images/"
            f"06_images_train_NOT{CFG2.test_year}/"
            f"_download_manifest_{crop}_NOT{CFG2.test_year}.csv"
        )
    else:
        raise ValueError("split must be 'train' or 'test'")

def local_componentE_img_to_gcs(local_img_path: str) -> str:
    p = str(local_img_path)

    if p.startswith("gs://"):
        return p

    prefix = CFG2.local_pipeline_root.rstrip("/") + "/"

    if p.startswith(prefix):
        rel = p[len(prefix):]
        return CFG2.gcs_pipeline_root.rstrip("/") + "/" + rel

    # Handles paths like:
    # /content/high_plains_crop_mapping/componentC_D_E_outputs/high_plains_multicrop_pipeline/...
    if "/componentC_D_E_outputs/high_plains_multicrop_pipeline/" in p:
        rel = p.split("/componentC_D_E_outputs/high_plains_multicrop_pipeline/", 1)[1]
        return CFG2.gcs_pipeline_root.rstrip("/") + "/" + rel

    return p

def read_componentE_manifest(crop: str, split: str = "test") -> pd.DataFrame:
    local_csv = componentE_manifest_local_path(crop, split)
    gcs_csv = componentE_manifest_gcs_path(crop, split)

    print(f"\nLooking for manifest: {crop} / {split}")
    print("Local:", local_csv)
    print("GCS:", gcs_csv)

    if os.path.exists(local_csv):
        df = pd.read_csv(local_csv)
        source_csv = local_csv
    elif gcs_exists(gcs_csv):
        df = read_csv_any(gcs_csv)
        source_csv = gcs_csv
    else:
        print(f"WARNING: manifest not found for {crop} {split}. Skipping.")
        return pd.DataFrame()

    if len(df) == 0:
        print(f"WARNING: empty manifest for {crop} {split}")
        return df

    df = df.copy()

    if "download_ok" in df.columns:
        df = df[df["download_ok"] == True].copy()

    if len(df) == 0:
        print(f"WARNING: no download_ok=True rows for {crop} {split}")
        return df

    df["expected_crop"] = crop
    df["componentE_crop"] = crop
    df["componentE_split"] = split
    df["source_manifest"] = source_csv

    if "img_path" in df.columns:
        df["resolved_gcs_image_path"] = df["img_path"].astype(str).map(local_componentE_img_to_gcs)
    elif "image_path" in df.columns:
        df["resolved_gcs_image_path"] = df["image_path"].astype(str).map(local_componentE_img_to_gcs)
    else:
        raise ValueError(f"No img_path/image_path column found in {source_csv}")

    if "month" not in df.columns:
        if "date" in df.columns:
            tmp = pd.to_datetime(df["date"], errors="coerce")
            df["month"] = tmp.dt.month.fillna(6).astype(int)
        else:
            df["month"] = 6

    if "cropfrac_best" not in df.columns:
        df["cropfrac_best"] = 0.5

    return df.reset_index(drop=True)

# LOAD CLIP + SAM Models
print("\nLoading CLIP...")
model, _, preprocess = open_clip.create_model_and_transforms(
    CFG2.clip_model_name,
    pretrained=CFG2.clip_pretrained
)
tokenizer = open_clip.get_tokenizer(CFG2.clip_model_name)
model = model.to(CFG2.device).eval()
print("CLIP ready.")

print("\nLoading SAM...")
if not os.path.exists(CFG2.sam_checkpoint):
    print("Downloading SAM checkpoint...")
    urllib.request.urlretrieve(
        "https://dl.fbaipublicfiles.com/segment_anything/sam_vit_l_0b3195.pth",
        CFG2.sam_checkpoint,
    )

sam = sam_model_registry[CFG2.sam_type](checkpoint=CFG2.sam_checkpoint).to(CFG2.device)
mask_gen = SamAutomaticMaskGenerator(
    sam,
    points_per_side=24,
    pred_iou_thresh=0.86,
    stability_score_thresh=0.92,
    crop_n_layers=1,
    crop_n_points_downscale_factor=2,
    min_mask_region_area=1200,
)
print("SAM ready.")

# PROMPTS
CLASS_PROMPTS = {
    "cotton": [
        "a Texas cotton field with short separated bushes and visible soil between rows",
        "a cotton field with widely spaced green bushes and visible brown soil between rows",
        "a cotton field with white fluffy cotton bolls from a road",
        "rows of cotton plants with open white bolls visible from a highway",
    ],
    "soybean": [
        "a soybean field with dense continuous green canopy where plants touch each other",
        "a soybean field forming a solid blanket of green leaves with no soil visible",
        "rows of soybean plants merging into a dense uniform canopy",
        "a dense soybean field with little soil visible from a roadside",
    ],
    "wheat": [
        "a wheat field with a uniform grassy texture from a road",
        "a mature wheat field with golden dense heads and continuous cover",
        "a green wheat field with narrow grass-like leaves forming a dense carpet",
        "a harvested or nearly mature wheat-like cereal field seen from the roadside",
    ],
    "maize": [
        "a field of tall dense stalks taller than a fence from a highway",
        "a tall green corn field with large leaves from a roadside",
        "a maize field with tall stalks visible from a road",
        "rows of tall green corn plants along a highway",
        "a corn field with dense tall stalks photographed from street level",
        "a maize crop field with thick green leaves seen from a road",
        "a corn field with tassels visible at the top of plants",
        "a photo of a corn field from a country road",
        "a tall corn field with light yellow tassels on top of plants with large leaves from a roadside",
        "a corn field with pale yellow tops and broad green leaves visible from a highway",
    ],
}

FIELD_NEG_PROMPTS = [
    "a photo of a major road",
    "a photo of a residential street with houses",
    "a photo of a neighborhood with buildings",
    "a photo of a parking lot with cars",
    "a photo of a downtown city street",
    "a photo taken inside a building",
    "a photo of a dense forest",
    "a photo of a lake or river",
    "a photo of an industrial area",
]

crop_labels = list(CLASS_PROMPTS.keys())
ALL_PROMPTS = {**CLASS_PROMPTS, "field_negative": FIELD_NEG_PROMPTS}
all_labels = list(ALL_PROMPTS.keys())

@torch.no_grad()
def build_text_features(prompt_dict, label_list):
    feats = []
    for lab in label_list:
        prompts = prompt_dict[lab]
        tok = tokenizer(prompts).to(CFG2.device)
        txt = model.encode_text(tok)
        txt = txt / txt.norm(dim=-1, keepdim=True)
        txt = txt.mean(dim=0, keepdim=True)
        txt = txt / txt.norm(dim=-1, keepdim=True)
        feats.append(txt)
    return torch.cat(feats, dim=0)

text_features = build_text_features(ALL_PROMPTS, all_labels)
print("Text features ready.")
print("All labels:", all_labels)
print("Crop labels:", crop_labels)

# IMAGE PREP
def crop_band(img, top_frac=CFG2.top_frac, bot_frac=CFG2.bot_frac, side_frac=CFG2.side_frac):
    W, H = img.size
    left = int(W * side_frac)
    right = int(W * (1 - side_frac))
    top = int(H * top_frac)
    bottom = int(H * (1 - bot_frac))
    if right <= left + 10 or bottom <= top + 10:
        return img
    return img.crop((left, top, right, bottom))

def make_field_masked_image(img):
    arr = np.array(img).astype(np.float32) / 255.0
    r = arr[:, :, 0]
    g = arr[:, :, 1]
    b = arr[:, :, 2]

    maxc = np.max(arr, axis=2)
    minc = np.min(arr, axis=2)
    sat = maxc - minc

    veg_mask = (g >= r * 0.85) & (g >= b * 0.85) & (g > 0.18)
    brown_mask = (r > 0.25) & (g > 0.20) & (b < 0.55) & (r >= g * 0.85)
    sky_mask = (b > g) & (b > r) & (b > 0.45) & (sat < 0.35)
    gray_mask = (sat < 0.10) & (maxc > 0.18) & (maxc < 0.85)

    keep = (veg_mask | brown_mask) & ~sky_mask & ~gray_mask
    out = arr.copy()
    out[~keep] = 0.10
    return Image.fromarray((out * 255).clip(0, 255).astype(np.uint8))

def choose_best_sam_region(cropped_pil, masks):
    img = np.array(cropped_pil).astype(np.float32) / 255.0
    H, W, _ = img.shape

    if len(masks) == 0:
        return make_field_masked_image(cropped_pil), None

    r = img[:, :, 0]
    g = img[:, :, 1]
    b = img[:, :, 2]

    best_score = -1e9
    best_mask = None
    best_bbox = None

    yy, xx = np.mgrid[0:H, 0:W]
    x_center = W / 2.0
    y_pref = H * 0.60

    x_norm = np.abs(xx - x_center) / (W / 2.0)
    y_norm = np.abs(yy - y_pref) / (H / 2.0)
    center_weight = np.exp(-(x_norm**2 * 2.2 + y_norm**2 * 1.4))

    for m in masks:
        seg = m["segmentation"]
        area = float(seg.sum())
        if area < 1200:
            continue

        x, y, w, h = m["bbox"]
        bbox_area = max(w * h, 1)

        green_score = float(((g >= r * 0.85) & (g >= b * 0.85) & seg).sum()) / max(area, 1)
        brown_score = float(((r > 0.25) & (g > 0.20) & (b < 0.55) & seg).sum()) / max(area, 1)
        fill_score = area / bbox_area
        center_score = float(center_weight[seg].mean()) if seg.sum() > 0 else 0.0
        y_mid = y + h / 2.0
        lower_pref = y_mid / H

        score = (
            2.8 * center_score +
            1.4 * green_score +
            1.0 * brown_score +
            1.0 * fill_score +
            0.9 * lower_pref +
            0.4 * np.log1p(area)
        )

        if score > best_score:
            best_score = score
            best_mask = seg
            best_bbox = (x, y, w, h)

    if best_mask is None:
        return make_field_masked_image(cropped_pil), None

    x, y, w, h = best_bbox
    pad_x = int(0.05 * W)
    pad_y = int(0.05 * H)

    x0 = max(int(x) - pad_x, 0)
    y0 = max(int(y) - pad_y, 0)
    x1 = min(int(x + w) + pad_x, W)
    y1 = min(int(y + h) + pad_y, H)

    cropped_np = np.array(cropped_pil)
    region = cropped_np[y0:y1, x0:x1].copy()
    local_mask = best_mask[y0:y1, x0:x1]
    region[~local_mask] = np.array([25, 25, 25], dtype=np.uint8)

    return Image.fromarray(region), best_bbox

def sam_prepare(img_pil):
    cropped = crop_band(img_pil)
    masks = mask_gen.generate(np.array(cropped))
    return choose_best_sam_region(cropped, masks)

# INFERENCE
@torch.no_grad()
def predict_one_model2(img_path):
    img = load_image_any(img_path)
    prepared, bbox = sam_prepare(img)

    x = preprocess(prepared).unsqueeze(0).to(CFG2.device)
    img_feat = model.encode_image(x)
    img_feat = img_feat / img_feat.norm(dim=-1, keepdim=True)

    sims = (img_feat @ text_features.T).squeeze(0)
    probs = torch.softmax(60.0 * sims, dim=0).cpu().numpy()

    pred_id = int(np.argmax(probs))
    pred_label = all_labels[pred_id]

    crop_indices = [all_labels.index(lab) for lab in crop_labels]
    crop_probs = probs[crop_indices]
    crop_probs = crop_probs / crop_probs.sum() if crop_probs.sum() > 0 else crop_probs

    crop_pred_id = int(np.argmax(crop_probs))
    crop_pred_label = crop_labels[crop_pred_id]

    return probs, pred_label, crop_probs, crop_pred_label, bbox

# RUN ONE MANIFEST
def run_model2_on_manifest(crop: str, split: str = "test") -> pd.DataFrame:
    df = read_componentE_manifest(crop, split)

    if len(df) == 0:
        print(f"Skipping {crop} {split}: no manifest rows.")
        return pd.DataFrame()

    rows = []
    fail = 0

    print(f"\nRunning Model_2 on {crop} / {split} / {len(df)} rows")

    for i, row in df.iterrows():
        if i % 25 == 0:
            print(f"[{crop} {split}] {i}/{len(df)} failures={fail}")

        try:
            probs, pred, crop_probs, crop_pred, bbox = predict_one_model2(
                row["resolved_gcs_image_path"]
            )
            status = "ok"
        except Exception as e:
            fail += 1
            pred = "error"
            crop_pred = np.nan
            probs = [np.nan] * len(all_labels)
            crop_probs = [np.nan] * len(crop_labels)
            bbox = None
            status = str(e)[:200]

        prob_map = dict(zip(all_labels, probs))
        crop_prob_map = dict(zip(crop_labels, crop_probs))

        out_row = row.to_dict()
        out_row.update({
            "pred_label": pred,
            "pred_crop_label": crop_pred,
            "pred_final_label": "other" if pred == "field_negative" else pred,
            "pred_final_label_panhandle": (
                "corn" if pred == "maize" else ("other" if pred == "field_negative" else pred)
            ),
            "pred_prob": float(np.nanmax(probs)) if len(probs) else np.nan,
            "pred_crop_prob": float(np.nanmax(crop_probs)) if len(crop_probs) else np.nan,
            "p_cotton": float(crop_prob_map.get("cotton", np.nan)),
            "p_soybean": float(crop_prob_map.get("soybean", np.nan)),
            "p_wheat": float(crop_prob_map.get("wheat", np.nan)),
            "p_maize": float(crop_prob_map.get("maize", np.nan)),
            "p_field_negative": float(prob_map.get("field_negative", np.nan)),
            "sam_bbox": str(bbox),
            "_status": status,
        })
        rows.append(out_row)

    out_df = pd.DataFrame(rows)

    out_dir = os.path.join(CFG2.local_out_root, split)
    os.makedirs(out_dir, exist_ok=True)

    out_csv = os.path.join(out_dir, f"componentF_model2_{crop}_{split}_predictions.csv")
    out_df.to_csv(out_csv, index=False)

    upload_file_to_gcs(
        out_csv,
        f"{CFG2.gcs_out_root}/{split}/{os.path.basename(out_csv)}"
    )

    print(f"\nSaved: {out_csv}")
    print("\nPrediction counts:")
    print(out_df["pred_final_label"].value_counts(dropna=False).to_string())

    return out_df

# SUMMARY
def evaluate_predictions(pred_df: pd.DataFrame) -> Dict:
    valid = pred_df[
        pred_df["pred_final_label_panhandle"].notna() &
        (pred_df["pred_final_label_panhandle"] != "error")
    ].copy()

    if len(valid) == 0:
        return {
            "n_total": int(len(pred_df)),
            "n_valid": 0,
            "pseudo_accuracy_vs_expected_crop": None,
        }

    acc = float(
        (
            valid["expected_crop"].astype(str)
            == valid["pred_final_label_panhandle"].astype(str)
        ).mean()
    )

    return {
        "n_total": int(len(pred_df)),
        "n_valid": int(len(valid)),
        "pseudo_accuracy_vs_expected_crop": acc,
    }

# RUN ALL AVAILABLE TEST MANIFESTS
SPLIT_TO_RUN = "test"   # keep as "test" for Component E 2025 test images

all_frames = []
summary_rows = []
skipped = []

for crop in CFG2.crops:
    pred_df = run_model2_on_manifest(crop=crop, split=SPLIT_TO_RUN)

    if len(pred_df):
        all_frames.append(pred_df)
        metrics = evaluate_predictions(pred_df)
        metrics["crop"] = crop
        metrics["split"] = SPLIT_TO_RUN
        summary_rows.append(metrics)
    else:
        skipped.append({"crop": crop, "split": SPLIT_TO_RUN, "reason": "missing_or_empty_manifest"})

combined_df = pd.concat(all_frames, ignore_index=True) if all_frames else pd.DataFrame()
summary_df = pd.DataFrame(summary_rows)
skipped_df = pd.DataFrame(skipped)

combined_csv = os.path.join(CFG2.local_out_root, f"componentF_model2_ALL_{SPLIT_TO_RUN}_predictions.csv")
summary_csv = os.path.join(CFG2.local_out_root, f"componentF_model2_{SPLIT_TO_RUN}_summary.csv")
skipped_csv = os.path.join(CFG2.local_out_root, f"componentF_model2_{SPLIT_TO_RUN}_skipped.csv")
meta_json = os.path.join(CFG2.local_out_root, "run_metadata.json")

combined_df.to_csv(combined_csv, index=False)
summary_df.to_csv(summary_csv, index=False)
skipped_df.to_csv(skipped_csv, index=False)

meta = {
    "run_id": RUN_ID,
    "config": asdict(CFG2),
    "model_type": "zero_shot_sam_clip",
    "all_labels": all_labels,
    "crop_labels": crop_labels,
    "split_run": SPLIT_TO_RUN,
    "final_rule": "field_negative -> other, maize -> corn alias only in pred_final_label_panhandle",
    "n_rows_total": int(len(combined_df)),
    "skipped": skipped,
}

with open(meta_json, "w") as f:
    json.dump(meta, f, indent=2)

upload_file_to_gcs(combined_csv, f"{CFG2.gcs_out_root}/componentF_model2_ALL_{SPLIT_TO_RUN}_predictions.csv")
upload_file_to_gcs(summary_csv, f"{CFG2.gcs_out_root}/componentF_model2_{SPLIT_TO_RUN}_summary.csv")
upload_file_to_gcs(skipped_csv, f"{CFG2.gcs_out_root}/componentF_model2_{SPLIT_TO_RUN}_skipped.csv")
upload_file_to_gcs(meta_json, f"{CFG2.gcs_out_root}/run_metadata.json")

print("\n Model_2 Panhandle inference finished.")
print("Combined output:", combined_csv)
print("Summary output:", summary_csv)
print("Skipped output:", skipped_csv)
print("Run metadata:", meta_json)
print("GCS output root:", CFG2.gcs_out_root)


# ============================================================
# COMPONENT G, HYBRID BIAS-AWARE SOFT LABELS + COORDINATE REPAIR
# ============================================================

!pip -q install google-cloud-storage pandas numpy

import os, io, json
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Dict

import numpy as np
import pandas as pd
from google.cloud import storage


RUN_ID_G = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

@dataclass
class ComponentGConfig:
    gcp_project: str = "gcp-clag-remote-mapping"

    f_model2_gcs_csv: str = (
        "gs://storage_cropmapping/Finals/high_plains_crop_mapping/"
        "componentF_panhandle_inference_model2/20260502_165219/"
        "componentF_model2_ALL_test_predictions.csv"
    )

    comp_cde_root: str = (
        "gs://storage_cropmapping/Finals/high_plains_crop_mapping/"
        "componentC_D_E_outputs/high_plains_multicrop_pipeline"
    )

    local_out_root: str = f"/content/componentG_biasaware_from_model2/{RUN_ID_G}"
    gcs_out_root: str = (
        "gs://storage_cropmapping/Finals/high_plains_crop_mapping/"
        f"componentG_biasaware_from_model2/{RUN_ID_G}"
    )

    crops: tuple = ("corn", "cotton", "soybean", "wheat")

    corn_weight: float = 1.00
    cotton_weight: float = 1.15
    soybean_weight: float = 1.08
    wheat_weight: float = 0.58

    raw_weight_base: float = 0.58
    cdl_weight_base: float = 0.27
    season_weight_base: float = 0.15

    min_field_conf: float = 0.45
    min_crop_conf: float = 0.55
    min_margin: float = 0.12
    max_entropy_norm: float = 0.88

    wheat_min_crop_conf: float = 0.62
    wheat_min_margin: float = 0.16
    wheat_max_entropy_norm: float = 0.78

    uncertain_label: str = "uncertain"

CFG = ComponentGConfig()
os.makedirs(CFG.local_out_root, exist_ok=True)

LOCAL_IMG_CSV = os.path.join(CFG.local_out_root, "componentG_image_softlabels.csv")
LOCAL_PT_CSV = os.path.join(CFG.local_out_root, "componentG_sv_point_softlabels.csv")
LOCAL_SUMMARY_CSV = os.path.join(CFG.local_out_root, "componentG_summary.csv")
LOCAL_EVAL_CSV = os.path.join(CFG.local_out_root, "componentG_eval.csv")
LOCAL_META_JSON = os.path.join(CFG.local_out_root, "componentG_metadata.json")

OUT_GCS_IMG_CSV = f"{CFG.gcs_out_root}/componentG_image_softlabels.csv"
OUT_GCS_PT_CSV = f"{CFG.gcs_out_root}/componentG_sv_point_softlabels.csv"
OUT_GCS_SUMMARY_CSV = f"{CFG.gcs_out_root}/componentG_summary.csv"
OUT_GCS_EVAL_CSV = f"{CFG.gcs_out_root}/componentG_eval.csv"
OUT_GCS_META_JSON = f"{CFG.gcs_out_root}/componentG_metadata.json"

print("RUN_ID_G:", RUN_ID_G)
print("Input Component F:", CFG.f_model2_gcs_csv)
print("Output GCS root:", CFG.gcs_out_root)

gcs_client = storage.Client(project=CFG.gcp_project)

def split_gcs_path(gcs_path: str):
    assert gcs_path.startswith("gs://")
    x = gcs_path.replace("gs://", "", 1)
    bucket = x.split("/")[0]
    blob = "/".join(x.split("/")[1:])
    return bucket, blob

def gcs_exists(gcs_path: str) -> bool:
    bucket, blob = split_gcs_path(gcs_path)
    return gcs_client.bucket(bucket).blob(blob).exists()

def read_gcs_csv(gcs_path: str) -> pd.DataFrame:
    bucket, blob = split_gcs_path(gcs_path)
    b = gcs_client.bucket(bucket).blob(blob)
    text = b.download_as_text()
    if len(text.strip()) == 0:
        return pd.DataFrame()
    return pd.read_csv(io.StringIO(text))

def upload_no_overwrite(local_path: str, gcs_path: str):
    if gcs_exists(gcs_path):
        print("Exists, skip upload:", gcs_path)
        return
    bucket, blob = split_gcs_path(gcs_path)
    gcs_client.bucket(bucket).blob(blob).upload_from_filename(local_path)
    print("Uploaded:", gcs_path)

def list_gcs_prefix(gs_prefix: str):
    bucket, prefix = split_gcs_path(gs_prefix)
    return [f"gs://{bucket}/{b.name}" for b in gcs_client.list_blobs(bucket, prefix=prefix)]

def first_existing_col(df, candidates):
    for c in candidates:
        if c in df.columns:
            return c
    return None

def clean_label(x):
    if pd.isna(x):
        return ""
    x = str(x).strip().lower()
    if x == "maize":
        return "corn"
    if x in ["bare_soil", "roadside_nonfield", "reject", "bad_view", "field_negative", "other"]:
        return "other"
    return x

def normalize_prob_dict(d: Dict[str, float], eps: float = 1e-12) -> Dict[str, float]:
    keys = list(d.keys())
    vals = []
    for k in keys:
        v = d[k]
        if pd.isna(v):
            v = 0.0
        vals.append(max(float(v), 0.0))
    vals = np.array(vals, dtype=float)
    s = vals.sum()
    if s <= eps:
        vals = np.ones(len(keys)) / len(keys)
    else:
        vals = vals / s
    return {k: float(v) for k, v in zip(keys, vals)}

def entropy_norm_from_probs(prob_map: Dict[str, float], eps: float = 1e-12) -> float:
    p = np.array(list(prob_map.values()), dtype=float)
    p = np.clip(p, eps, 1.0)
    h = float(-(p * np.log(p)).sum())
    return h / np.log(len(p))

def top2_stats(prob_map):
    items = sorted(prob_map.items(), key=lambda kv: kv[1], reverse=True)
    top_label, top_prob = items[0]
    second_label, second_prob = items[1]
    return top_label, top_prob, second_label, second_prob, top_prob - second_prob

def weighted_mean(values, weights):
    v = pd.to_numeric(values, errors="coerce").to_numpy(float)
    w = pd.to_numeric(weights, errors="coerce").to_numpy(float)
    mask = np.isfinite(v) & np.isfinite(w)
    if mask.sum() == 0:
        return np.nan
    v, w = v[mask], np.clip(w[mask], 1e-6, None)
    return float(np.sum(v * w) / np.sum(w))

def season_prior_for_month(month):
    month = int(month) if pd.notna(month) else 6
    if month in [3, 4]:
        p = {"corn": 0.10, "cotton": 0.08, "soybean": 0.12, "wheat": 0.70}
    elif month in [5, 6]:
        p = {"corn": 0.26, "cotton": 0.16, "soybean": 0.18, "wheat": 0.40}
    elif month == 7:
        p = {"corn": 0.30, "cotton": 0.28, "soybean": 0.24, "wheat": 0.18}
    elif month in [8, 9]:
        p = {"corn": 0.22, "cotton": 0.34, "soybean": 0.28, "wheat": 0.16}
    elif month == 10:
        p = {"corn": 0.16, "cotton": 0.42, "soybean": 0.26, "wheat": 0.16}
    else:
        p = {"corn": 0.25, "cotton": 0.25, "soybean": 0.25, "wheat": 0.25}
    return normalize_prob_dict(p)

def cdl_prior_from_row(row):
    crop = clean_label(row.get("componentE_crop", row.get("crop", "")))
    cropfrac = pd.to_numeric(row.get("cropfrac_best", 0.50), errors="coerce")
    cropfrac = 0.50 if pd.isna(cropfrac) else float(np.clip(cropfrac, 0, 1))

    base = {c: 1.0 / len(CFG.crops) for c in CFG.crops}

    if crop in base:
        target_mass = 0.45 + 0.45 * cropfrac
        other_mass = (1.0 - target_mass) / (len(base) - 1)
        for c in base:
            base[c] = other_mass
        base[crop] = target_mass

    return normalize_prob_dict(base)

def raw_probs_from_row(row):
    raw = {
        "corn": row.get("p_maize", row.get("p_corn", np.nan)),
        "cotton": row.get("p_cotton", np.nan),
        "soybean": row.get("p_soybean", np.nan),
        "wheat": row.get("p_wheat", np.nan),
    }
    raw = normalize_prob_dict(raw)

    weights = {
        "corn": CFG.corn_weight,
        "cotton": CFG.cotton_weight,
        "soybean": CFG.soybean_weight,
        "wheat": CFG.wheat_weight,
    }

    for c in raw:
        raw[c] *= weights[c]

    return normalize_prob_dict(raw)

def apply_wheat_veto(row, prob_map):
    month = int(row.get("month", 6)) if pd.notna(row.get("month", np.nan)) else 6
    source_crop = clean_label(row.get("componentE_crop", row.get("crop", "")))
    cropfrac = pd.to_numeric(row.get("cropfrac_best", 0.50), errors="coerce")
    cropfrac = 0.50 if pd.isna(cropfrac) else float(np.clip(cropfrac, 0, 1))

    top_label, top_prob, second_label, second_prob, margin = top2_stats(prob_map)

    if top_label != "wheat":
        return prob_map

    suspicious_month = month in [7, 8, 9, 10]
    close_competitor = margin < 0.18
    source_disagrees = source_crop in ["corn", "cotton", "soybean"] and cropfrac >= 0.55

    if suspicious_month and (close_competitor or source_disagrees):
        prob_map = prob_map.copy()
        prob_map["wheat"] *= 0.55
        prob_map["corn"] *= 1.10
        prob_map["cotton"] *= 1.10
        prob_map["soybean"] *= 1.10
        prob_map = normalize_prob_dict(prob_map)

    return prob_map

def fuse_row(row):
    p_field_negative = pd.to_numeric(row.get("p_field_negative", np.nan), errors="coerce")
    field_conf = 1.0 - float(p_field_negative) if pd.notna(p_field_negative) else 0.75
    field_conf = float(np.clip(field_conf, 0, 1))

    cropfrac = pd.to_numeric(row.get("cropfrac_best", 0.50), errors="coerce")
    cropfrac = 0.50 if pd.isna(cropfrac) else float(np.clip(cropfrac, 0, 1))

    field_conf = float(np.clip(0.80 * field_conf + 0.20 * cropfrac, 0, 1))

    pred_crop_prob = pd.to_numeric(row.get("pred_crop_prob", np.nan), errors="coerce")
    if pd.isna(pred_crop_prob):
        pred_crop_prob = max(raw_probs_from_row(row).values())
    pred_crop_prob = float(np.clip(pred_crop_prob, 0, 1))

    raw = raw_probs_from_row(row)
    cdl = cdl_prior_from_row(row)
    season = season_prior_for_month(row.get("month", 6))

    raw_w = CFG.raw_weight_base * (0.35 + 0.65 * field_conf) * (0.50 + 0.50 * pred_crop_prob)
    cdl_w = CFG.cdl_weight_base * (0.60 + 0.40 * cropfrac)
    season_w = CFG.season_weight_base

    total_w = raw_w + cdl_w + season_w
    raw_w, cdl_w, season_w = raw_w / total_w, cdl_w / total_w, season_w / total_w

    fused = {c: raw_w * raw[c] + cdl_w * cdl[c] + season_w * season[c] for c in CFG.crops}
    fused = normalize_prob_dict(fused)
    fused = apply_wheat_veto(row, fused)

    top_label, top_prob, second_label, second_prob, margin = top2_stats(fused)
    entropy_norm = entropy_norm_from_probs(fused)

    if top_label == "wheat":
        hard = top_label if (
            field_conf >= CFG.min_field_conf and
            top_prob >= CFG.wheat_min_crop_conf and
            margin >= CFG.wheat_min_margin and
            entropy_norm <= CFG.wheat_max_entropy_norm
        ) else CFG.uncertain_label
    else:
        hard = top_label if (
            field_conf >= CFG.min_field_conf and
            top_prob >= CFG.min_crop_conf and
            margin >= CFG.min_margin and
            entropy_norm <= CFG.max_entropy_norm
        ) else CFG.uncertain_label

    out = row.to_dict()
    out.update({
        "field_conf": field_conf,
        "p_img_corn": fused["corn"],
        "p_img_cotton": fused["cotton"],
        "p_img_soybean": fused["soybean"],
        "p_img_wheat": fused["wheat"],
        "soft_label_img": top_label,
        "second_label_img": second_label,
        "max_prob_img": top_prob,
        "second_prob_img": second_prob,
        "margin_img": margin,
        "entropy_img": entropy_norm,
        "hard_label_img": hard,
        "raw_w": raw_w,
        "cdl_w": cdl_w,
        "season_w": season_w,
        "image_weight": max(field_conf, 0.05) * max(cropfrac, 0.10) * max(pred_crop_prob, 0.10),
    })
    return pd.Series(out)

# LOAD COMPONENT F
f = read_gcs_csv(CFG.f_model2_gcs_csv).copy()
print("Loaded Component F rows:", len(f))
print("Columns:", f.columns.tolist())

if "point_id" not in f.columns:
    raise ValueError("point_id is required.")

if "componentE_crop" not in f.columns:
    if "crop" in f.columns:
        f["componentE_crop"] = f["crop"]
    elif "expected_crop" in f.columns:
        f["componentE_crop"] = f["expected_crop"]
    else:
        f["componentE_crop"] = np.nan

if "month" not in f.columns:
    if "date" in f.columns:
        f["month"] = pd.to_datetime(f["date"], errors="coerce").dt.month.fillna(6).astype(int)
    else:
        f["month"] = 6

if "cropfrac_best" not in f.columns:
    f["cropfrac_best"] = 0.50

# IMAGE-LEVEL FUSION
g_img = f.apply(fuse_row, axis=1)

# POINT-LEVEL AGGREGATION
def first_valid(sub, cols):
    for c in cols:
        if c in sub.columns:
            s = sub[c].dropna()
            if len(s):
                return s.iloc[0]
    return np.nan

keep_first_cols = [
    "date", "year", "month", "componentE_crop", "expected_crop",
    "cropfrac_best", "img_path", "resolved_gcs_image_path",
    "source_manifest", "pano_id", "heading_best", "download_ok"
]

point_rows = []

for pid, sub in g_img.groupby("point_id", dropna=False):
    w = sub["image_weight"].fillna(0.01)

    pmap = {
        "corn": weighted_mean(sub["p_img_corn"], w),
        "cotton": weighted_mean(sub["p_img_cotton"], w),
        "soybean": weighted_mean(sub["p_img_soybean"], w),
        "wheat": weighted_mean(sub["p_img_wheat"], w),
    }
    pmap = normalize_prob_dict(pmap)

    top_label, top_prob, second_label, second_prob, margin = top2_stats(pmap)
    entropy_norm = entropy_norm_from_probs(pmap)
    field_conf = weighted_mean(sub["field_conf"], w)

    if top_label == "wheat":
        hard = top_label if (
            field_conf >= CFG.min_field_conf and
            top_prob >= CFG.wheat_min_crop_conf and
            margin >= CFG.wheat_min_margin and
            entropy_norm <= CFG.wheat_max_entropy_norm
        ) else CFG.uncertain_label
    else:
        hard = top_label if (
            field_conf >= CFG.min_field_conf and
            top_prob >= CFG.min_crop_conf and
            margin >= CFG.min_margin and
            entropy_norm <= CFG.max_entropy_norm
        ) else CFG.uncertain_label

    row = {
        "point_id": pid,
        "n_images_for_point": int(len(sub)),
        "n_unique_months_gsv": int(sub["month"].nunique(dropna=True)),
        "field_conf": field_conf,
        "conf_point": top_prob,
        "entropy_point": entropy_norm,
        "margin_point": margin,
        "p_point_corn": pmap["corn"],
        "p_point_cotton": pmap["cotton"],
        "p_point_soybean": pmap["soybean"],
        "p_point_wheat": pmap["wheat"],
        "soft_label_point": top_label,
        "second_label_point": second_label,
        "max_prob_point": top_prob,
        "second_prob_point": second_prob,
        "hard_label_point": hard,
        "overall_conf_point": float(np.clip(field_conf * top_prob, 0, 1)),
        "source_rows": int(len(sub)),
    }

    for c in keep_first_cols:
        if c in sub.columns:
            s = sub[c].dropna()
            row[c] = s.iloc[0] if len(s) else pd.NA

    point_rows.append(row)

g_point = pd.DataFrame(point_rows)

# COORDINATE REPAIR FROM COMPONENT C/D/E
print("\nSearching Component C/D/E coordinate files...")

all_files = list_gcs_prefix(CFG.comp_cde_root)

coord_csvs = [
    p for p in all_files
    if p.endswith(".csv") and (
        "componentD" in p or
        "download_manifest" in p or
        "manifest" in p or
        "gsv_hits" in p or
        "fieldtargets" in p
    )
]

wanted_cols = [
    "point_id",
    "field_lon", "field_lat",
    "lon_field", "lat_field",
    "field_lon_best", "field_lat_best",
    "lon_road", "lat_road",
    "cropfrac_best", "crop", "componentE_crop",
    "year", "month", "date"
]

coord_frames = []

for fp in coord_csvs:
    try:
        d = read_gcs_csv(fp)
        if d.empty or "point_id" not in d.columns:
            continue

        keep = [c for c in wanted_cols if c in d.columns]
        d = d[keep].copy()
        d["_coord_source_gcs_csv"] = fp

        lat_col = first_existing_col(d, ["field_lat", "lat_field", "field_lat_best"])
        lon_col = first_existing_col(d, ["field_lon", "lon_field", "field_lon_best"])
        road_lat_col = first_existing_col(d, ["lat_road"])
        road_lon_col = first_existing_col(d, ["lon_road"])

        if lat_col is None and road_lat_col is None:
            continue
        if lon_col is None and road_lon_col is None:
            continue

        d["coord_lat"] = pd.to_numeric(d[lat_col], errors="coerce") if lat_col else np.nan
        d["coord_lon"] = pd.to_numeric(d[lon_col], errors="coerce") if lon_col else np.nan

        if road_lat_col:
            d["coord_lat"] = d["coord_lat"].fillna(pd.to_numeric(d[road_lat_col], errors="coerce"))
        if road_lon_col:
            d["coord_lon"] = d["coord_lon"].fillna(pd.to_numeric(d[road_lon_col], errors="coerce"))

        d["_has_coord"] = d["coord_lon"].notna() & d["coord_lat"].notna()
        d = d[d["_has_coord"]].copy()

        if len(d):
            coord_frames.append(d)

    except Exception as e:
        print("Skipping:", fp, "|", repr(e))

if coord_frames:
    coords_df = pd.concat(coord_frames, ignore_index=True)
    coords_df = (
        coords_df
        .sort_values("_has_coord", ascending=False)
        .drop_duplicates("point_id", keep="first")
        .copy()
    )

    print("Recovered coordinate point IDs:", coords_df["point_id"].nunique())

    coord_keep = ["point_id", "coord_lon", "coord_lat", "_coord_source_gcs_csv"]
    if "cropfrac_best" in coords_df.columns:
        coord_keep.append("cropfrac_best")

    g_point = g_point.merge(
        coords_df[coord_keep],
        on="point_id",
        how="left",
        suffixes=("", "_coord")
    )

    g_point["lon"] = pd.to_numeric(g_point["coord_lon"], errors="coerce")
    g_point["lat"] = pd.to_numeric(g_point["coord_lat"], errors="coerce")
else:
    print("No external coordinate files found. Trying coordinates in G/F itself.")
    lat_col = first_existing_col(g_point, ["lat", "lat_field", "field_lat"])
    lon_col = first_existing_col(g_point, ["lon", "lon_field", "field_lon"])
    g_point["lat"] = pd.to_numeric(g_point[lat_col], errors="coerce") if lat_col else np.nan
    g_point["lon"] = pd.to_numeric(g_point[lon_col], errors="coerce") if lon_col else np.nan

valid_coords = int((g_point["lon"].notna() & g_point["lat"].notna()).sum())

print("\nCoordinate check:")
print("Valid coordinates:", valid_coords, "of", len(g_point))

# SUMMARY / EVAL
summary_rows = [
    {"metric": "run_id", "value": RUN_ID_G},
    {"metric": "input_componentF_rows", "value": int(len(f))},
    {"metric": "image_rows", "value": int(len(g_img))},
    {"metric": "point_rows", "value": int(len(g_point))},
    {"metric": "valid_point_coordinates", "value": int(valid_coords)},
    {"metric": "mean_image_field_conf", "value": float(g_img["field_conf"].mean())},
    {"metric": "mean_point_field_conf", "value": float(g_point["field_conf"].mean())},
    {"metric": "mean_point_conf", "value": float(g_point["conf_point"].mean())},
]

for k, v in g_point["soft_label_point"].value_counts(dropna=False).to_dict().items():
    summary_rows.append({"metric": f"point_soft_label::{k}", "value": int(v)})

for k, v in g_point["hard_label_point"].value_counts(dropna=False).to_dict().items():
    summary_rows.append({"metric": f"point_hard_label::{k}", "value": int(v)})

summary_df = pd.DataFrame(summary_rows)

eval_rows = []

if "componentE_crop" in g_point.columns:
    ref = g_point["componentE_crop"].apply(clean_label)
    pred = g_point["hard_label_point"].apply(clean_label)

    mask_excl_unc = (ref != "") & (pred != "") & (pred != "uncertain")
    if mask_excl_unc.sum() > 0:
        eval_rows.append({
            "scope": "point_hard_vs_componentE_crop_excluding_uncertain",
            "n": int(mask_excl_unc.sum()),
            "accuracy": float((ref[mask_excl_unc] == pred[mask_excl_unc]).mean())
        })

    mask_all = (ref != "") & (pred != "")
    if mask_all.sum() > 0:
        eval_rows.append({
            "scope": "point_hard_vs_componentE_crop_including_uncertain",
            "n": int(mask_all.sum()),
            "accuracy": float((ref[mask_all] == pred[mask_all]).mean())
        })

eval_df = pd.DataFrame(eval_rows)

# SAVE
g_img.to_csv(LOCAL_IMG_CSV, index=False)
g_point.to_csv(LOCAL_PT_CSV, index=False)
summary_df.to_csv(LOCAL_SUMMARY_CSV, index=False)
eval_df.to_csv(LOCAL_EVAL_CSV, index=False)

meta = {
    "run_id": RUN_ID_G,
    "config": asdict(CFG),
    "input_componentF_csv": CFG.f_model2_gcs_csv,
    "valid_point_coordinates": valid_coords,
    "outputs_gcs": {
        "image_csv": OUT_GCS_IMG_CSV,
        "point_csv": OUT_GCS_PT_CSV,
        "summary_csv": OUT_GCS_SUMMARY_CSV,
        "eval_csv": OUT_GCS_EVAL_CSV,
        "metadata_json": OUT_GCS_META_JSON,
    },
    "notes": [
        "This Component G includes coordinate repair.",
        "Use componentG_sv_point_softlabels.csv as Component H input.",
        "Standard coordinate columns are lon and lat.",
    ]
}

with open(LOCAL_META_JSON, "w") as f:
    json.dump(meta, f, indent=2)

upload_no_overwrite(LOCAL_IMG_CSV, OUT_GCS_IMG_CSV)
upload_no_overwrite(LOCAL_PT_CSV, OUT_GCS_PT_CSV)
upload_no_overwrite(LOCAL_SUMMARY_CSV, OUT_GCS_SUMMARY_CSV)
upload_no_overwrite(LOCAL_EVAL_CSV, OUT_GCS_EVAL_CSV)
upload_no_overwrite(LOCAL_META_JSON, OUT_GCS_META_JSON)

print("\nComponent G finished.")
print("G point CSV for Component H:")
print(OUT_GCS_PT_CSV)
print("\nPoint hard labels:")
print(g_point["hard_label_point"].value_counts(dropna=False))
print("\nPoint soft labels:")
print(g_point["soft_label_point"].value_counts(dropna=False))
print("\nValid coordinates:", valid_coords, "of", len(g_point))


# ============================================================
# COMPONENT H — SENTINEL-2 MONTHLY FEATURES
# Historical 2024–2025 + in-season/partial 2026 prediction year
# ============================================================

!pip -q install earthengine-api google-cloud-storage pandas numpy tqdm

import os, io, json, time, calendar
from datetime import datetime, timezone, date
from dataclasses import dataclass, asdict

import ee
import numpy as np
import pandas as pd
from tqdm.auto import tqdm
from google.cloud import storage

RUN_ID_H = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

@dataclass
class ComponentHConfig:
    project_id: str = "gcp-clag-remote-mapping"
    bucket_name: str = "storage_cropmapping"

    # Uses latest variable from Component G if available.
    g_point_csv: str = globals().get(
        "OUT_GCS_PT_CSV",
        "gs://storage_cropmapping/Finals/high_plains_crop_mapping/"
        "componentG_biasaware_from_model2/20260502_182824/"
        "componentG_sv_point_softlabels.csv"
    )

    out_gcs_root: str = (
        "gs://storage_cropmapping/Finals/high_plains_crop_mapping/"
        f"componentH_monthly_s2_historical2024_2025_predict2026/{RUN_ID_H}"
    )

    local_root: str = f"/content/componentH_monthly_s2/{RUN_ID_H}"

    hp_bbox: tuple = (-103.20, 33.20, -100.00, 36.50)

    historical_years: tuple = (2024, 2025)
    prediction_year: int = 2026

    start_month: int = 3
    end_month: int = 10

    # For 2026, this keeps only months that are available so far.
    # Set to None for automatic current UTC date.
    prediction_cutoff_date: str = None

    scale_m: int = 10
    point_buffer_m: int = 20
    max_cloudy_pixel_percentage: int = 70
    max_retries: int = 3
    sleep_between_retries: int = 10

CFG_H = ComponentHConfig()

LOCAL_ROOT = CFG_H.local_root
os.makedirs(LOCAL_ROOT, exist_ok=True)

LOCAL_CKPT_DIR = os.path.join(LOCAL_ROOT, "checkpoints")
os.makedirs(LOCAL_CKPT_DIR, exist_ok=True)

LOCAL_LONG_CSV = os.path.join(LOCAL_ROOT, "componentH_s2_monthly_point_timeseries.csv")
LOCAL_SUMMARY_JSON = os.path.join(LOCAL_ROOT, "componentH_summary.json")
LOCAL_INPUT_COPY = os.path.join(LOCAL_ROOT, "componentG_points_used.csv")

OUT_GCS_LONG_CSV = f"{CFG_H.out_gcs_root}/02_outputs/componentH_s2_monthly_point_timeseries.csv"
OUT_GCS_SUMMARY_JSON = f"{CFG_H.out_gcs_root}/componentH_summary.json"
OUT_GCS_INPUT_COPY = f"{CFG_H.out_gcs_root}/00_input/componentG_points_used.csv"
OUT_GCS_CKPT_DIR = f"{CFG_H.out_gcs_root}/01_checkpoints"

print("RUN_ID_H:", RUN_ID_H)
print("Input G point CSV:", CFG_H.g_point_csv)
print("Output GCS root:", CFG_H.out_gcs_root)


gcs_client = storage.Client(project=CFG_H.project_id)

def split_gcs_path(gs_path):
    assert gs_path.startswith("gs://")
    x = gs_path.replace("gs://", "", 1)
    bucket = x.split("/")[0]
    blob = "/".join(x.split("/")[1:])
    return bucket, blob

def gcs_exists(gs_path):
    bucket, blob = split_gcs_path(gs_path)
    return gcs_client.bucket(bucket).blob(blob).exists()

def read_gcs_csv(gs_path):
    bucket, blob = split_gcs_path(gs_path)
    text = gcs_client.bucket(bucket).blob(blob).download_as_text()
    return pd.read_csv(io.StringIO(text))

def upload_no_overwrite(local_path, gs_path):
    if gcs_exists(gs_path):
        print("Exists, skip upload:", gs_path)
        return
    bucket, blob = split_gcs_path(gs_path)
    gcs_client.bucket(bucket).blob(blob).upload_from_filename(local_path)
    print("Uploaded:", gs_path)

def download_gcs(gs_path, local_path):
    bucket, blob = split_gcs_path(gs_path)
    os.makedirs(os.path.dirname(local_path), exist_ok=True)
    gcs_client.bucket(bucket).blob(blob).download_to_filename(local_path)

def first_existing_col(df, candidates):
    for c in candidates:
        if c in df.columns:
            return c
    return None

# GEE
try:
    ee.Initialize(project=CFG_H.project_id)
    print("Earth Engine initialized:", CFG_H.project_id)
except Exception:
    ee.Authenticate()
    ee.Initialize(project=CFG_H.project_id)
    print("Earth Engine initialized after auth:", CFG_H.project_id)

# LOAD G POINTS
g = read_gcs_csv(CFG_H.g_point_csv).copy()

print("\nLoaded G rows:", len(g))
print("Columns:", g.columns.tolist())

if "point_id" not in g.columns:
    raise ValueError("Component G point file must contain point_id.")

lat_col = first_existing_col(g, ["lat", "lat_field", "field_lat", "sample_lat"])
lon_col = first_existing_col(g, ["lon", "lon_field", "field_lon", "sample_lon"])

if lat_col is None or lon_col is None:
    raise ValueError("No coordinate columns found. Component G must contain lon/lat.")

g["lat"] = pd.to_numeric(g[lat_col], errors="coerce")
g["lon"] = pd.to_numeric(g[lon_col], errors="coerce")

xmin, ymin, xmax, ymax = CFG_H.hp_bbox

g = g.dropna(subset=["lon", "lat"]).copy()
g = g[
    g["lon"].between(xmin, xmax) &
    g["lat"].between(ymin, ymax)
].copy()

if len(g) == 0:
    raise RuntimeError("No valid points after coordinate + ROI filtering.")

# Make sure probability columns exist.
for c in ["p_point_corn", "p_point_cotton", "p_point_soybean", "p_point_wheat"]:
    if c not in g.columns:
        g[c] = np.nan
    g[c] = pd.to_numeric(g[c], errors="coerce")

for c in ["field_conf", "conf_point", "overall_conf_point", "cropfrac_best"]:
    if c not in g.columns:
        g[c] = np.nan
    g[c] = pd.to_numeric(g[c], errors="coerce")

for c in ["hard_label_point", "soft_label_point", "componentE_crop", "expected_crop"]:
    if c not in g.columns:
        g[c] = ""

g.to_csv(LOCAL_INPUT_COPY, index=False)
upload_no_overwrite(LOCAL_INPUT_COPY, OUT_GCS_INPUT_COPY)

print("\nValid points for H:", len(g))
print("Unique points:", g["point_id"].nunique())

# BUILD PERIODS
def month_start_end(year, month):
    start = date(year, month, 1)
    last_day = calendar.monthrange(year, month)[1]
    end = date(year, month, last_day)
    return start, end

def make_periods():
    periods = []

    for y in CFG_H.historical_years:
        for m in range(CFG_H.start_month, CFG_H.end_month + 1):
            start, end_day = month_start_end(y, m)
            end_exclusive = date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1)
            periods.append({
                "year": y,
                "month": m,
                "period": f"{y}_{m:02d}",
                "start": start.isoformat(),
                "end": end_exclusive.isoformat(),
                "period_role": "historical_full"
            })

    if CFG_H.prediction_cutoff_date is None:
        cutoff = datetime.now(timezone.utc).date()
    else:
        cutoff = datetime.fromisoformat(CFG_H.prediction_cutoff_date).date()

    for m in range(CFG_H.start_month, CFG_H.end_month + 1):
        start, _ = month_start_end(CFG_H.prediction_year, m)

        if start > cutoff:
            continue

        next_month = date(CFG_H.prediction_year + 1, 1, 1) if m == 12 else date(CFG_H.prediction_year, m + 1, 1)
        end_exclusive = min(next_month, cutoff)

        if end_exclusive <= start:
            continue

        periods.append({
            "year": CFG_H.prediction_year,
            "month": m,
            "period": f"{CFG_H.prediction_year}_{m:02d}",
            "start": start.isoformat(),
            "end": end_exclusive.isoformat(),
            "period_role": "prediction_partial" if end_exclusive < next_month else "prediction_available"
        })

    return periods

periods = make_periods()

print("\nPeriods to extract:")
for p in periods:
    print(p)

# EE FUNCTIONS
roi = ee.Geometry.Rectangle(CFG_H.hp_bbox)

S2_BANDS = ["B2", "B3", "B4", "B8", "B11", "B12"]

FEATURE_BANDS = [
    "B2", "B3", "B4", "B8", "B11", "B12",
    "NDVI", "EVI2", "NDMI", "SWIR1_RATIO", "SWIR2_INDEX"
]

def mask_s2_sr(img):
    scl = img.select("SCL")
    mask = (
        scl.neq(3)    # cloud shadow
        .And(scl.neq(8))    # medium cloud
        .And(scl.neq(9))    # high cloud
        .And(scl.neq(10))   # cirrus
        .And(scl.neq(11))   # snow/ice
    )
    return img.updateMask(mask)

def add_indices(img):
    scaled = img.select(S2_BANDS).divide(10000).rename(S2_BANDS)

    b2 = scaled.select("B2")
    b4 = scaled.select("B4")
    b8 = scaled.select("B8")
    b11 = scaled.select("B11")
    b12 = scaled.select("B12")

    ndvi = b8.subtract(b4).divide(b8.add(b4)).rename("NDVI")

    evi2 = (
        b8.subtract(b4)
        .multiply(2.5)
        .divide(b8.add(b4.multiply(2.4)).add(1.0))
        .rename("EVI2")
    )

    ndmi = b8.subtract(b11).divide(b8.add(b11)).rename("NDMI")
    swir1_ratio = b11.divide(b8).rename("SWIR1_RATIO")
    swir2_index = b11.subtract(b12).divide(b11.add(b12)).rename("SWIR2_INDEX")

    return scaled.addBands([ndvi, evi2, ndmi, swir1_ratio, swir2_index])

def get_s2_collection(start_date, end_date):
    return (
        ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
        .filterBounds(roi)
        .filterDate(start_date, end_date)
        .filter(ee.Filter.lte("CLOUDY_PIXEL_PERCENTAGE", CFG_H.max_cloudy_pixel_percentage))
        .map(mask_s2_sr)
        .map(add_indices)
    )

def pandas_to_ee_fc(df):
    feats = []

    prop_cols = [
        "point_id", "lon", "lat",
        "p_point_corn", "p_point_cotton", "p_point_soybean", "p_point_wheat",
        "field_conf", "conf_point", "overall_conf_point", "cropfrac_best",
        "hard_label_point", "soft_label_point", "componentE_crop", "expected_crop"
    ]

    for _, r in df.iterrows():
        props = {}
        for c in prop_cols:
            v = r.get(c, None)
            if pd.isna(v):
                if c in ["hard_label_point", "soft_label_point", "componentE_crop", "expected_crop"]:
                    v = ""
                else:
                    v = None
            props[c] = v

        geom = ee.Geometry.Point([float(r["lon"]), float(r["lat"])]).buffer(CFG_H.point_buffer_m)
        feats.append(ee.Feature(geom, props))

    return ee.FeatureCollection(feats)

points_fc = pandas_to_ee_fc(g)

def ee_fc_to_pandas(fc):
    data = fc.getInfo()
    return pd.DataFrame([feat["properties"] for feat in data["features"]])

# EXTRACT ONE PERIOD
def extract_period(period):
    period_id = period["period"]

    ckpt_local = os.path.join(LOCAL_CKPT_DIR, f"checkpoint_{period_id}.csv")
    ckpt_gcs = f"{OUT_GCS_CKPT_DIR}/checkpoint_{period_id}.csv"

    if os.path.exists(ckpt_local):
        return pd.read_csv(ckpt_local)

    if gcs_exists(ckpt_gcs):
        download_gcs(ckpt_gcs, ckpt_local)
        return pd.read_csv(ckpt_local)

    last_error = None

    for attempt in range(1, CFG_H.max_retries + 1):
        try:
            col = get_s2_collection(period["start"], period["end"])
            n_imgs = int(col.size().getInfo())

            if n_imgs == 0:
                df_empty = g[[
                    "point_id", "lon", "lat",
                    "p_point_corn", "p_point_cotton", "p_point_soybean", "p_point_wheat",
                    "field_conf", "conf_point", "overall_conf_point",
                    "hard_label_point", "soft_label_point", "componentE_crop", "expected_crop"
                ]].copy()

                for b in FEATURE_BANDS:
                    df_empty[b] = np.nan

                df_empty["n_s2_images_roi_month"] = 0
                df_empty["year"] = period["year"]
                df_empty["month"] = period["month"]
                df_empty["period"] = period["period"]
                df_empty["start_date"] = period["start"]
                df_empty["end_date"] = period["end"]
                df_empty["period_role"] = period["period_role"]

                df_empty.to_csv(ckpt_local, index=False)
                upload_no_overwrite(ckpt_local, ckpt_gcs)
                return df_empty

            composite = col.select(FEATURE_BANDS).median()
            composite = composite.addBands(
                ee.Image.constant(n_imgs).rename("n_s2_images_roi_month").toFloat()
            )

            sampled = composite.reduceRegions(
                collection=points_fc,
                reducer=ee.Reducer.mean(),
                scale=CFG_H.scale_m,
                crs="EPSG:4326",
                tileScale=4
            )

            sampled = sampled.map(lambda f: f.set({
                "year": period["year"],
                "month": period["month"],
                "period": period["period"],
                "start_date": period["start"],
                "end_date": period["end"],
                "period_role": period["period_role"],
            }))

            df_p = ee_fc_to_pandas(sampled)

            if df_p.empty:
                raise RuntimeError(f"Empty EE result for {period_id}")

            df_p.to_csv(ckpt_local, index=False)
            upload_no_overwrite(ckpt_local, ckpt_gcs)
            return df_p

        except Exception as e:
            last_error = e
            print(f"Attempt {attempt}/{CFG_H.max_retries} failed for {period_id}: {repr(e)}")
            time.sleep(CFG_H.sleep_between_retries * attempt)

    raise RuntimeError(f"Failed period {period_id}: {repr(last_error)}")

# RUN EXTRACTION
parts = []
failed = []

for p in tqdm(periods, desc="Extracting monthly S2 periods"):
    try:
        df_p = extract_period(p)
        parts.append(df_p)
        print("Done:", p["period"], df_p.shape)
    except Exception as e:
        failed.append({"period": p["period"], "error": repr(e)})
        print("FAILED:", p["period"], repr(e))

if len(parts) == 0:
    raise RuntimeError("No Component H periods succeeded.")

h_long = pd.concat(parts, ignore_index=True)

# CLEAN VALUES
for c in FEATURE_BANDS + ["n_s2_images_roi_month"]:
    if c in h_long.columns:
        h_long[c] = pd.to_numeric(h_long[c], errors="coerce")

for b in ["B2", "B3", "B4", "B8", "B11", "B12"]:
    if b in h_long.columns:
        h_long.loc[(h_long[b] < 0) | (h_long[b] > 1.5), b] = np.nan

for idx in ["NDVI", "EVI2", "NDMI", "SWIR1_RATIO", "SWIR2_INDEX"]:
    if idx in h_long.columns:
        h_long.loc[(h_long[idx] < -5) | (h_long[idx] > 5), idx] = np.nan

# Keep rows even if future/prediction periods are incomplete.
h_long["has_s2_value"] = h_long[FEATURE_BANDS].notna().any(axis=1)

h_long.to_csv(LOCAL_LONG_CSV, index=False)
upload_no_overwrite(LOCAL_LONG_CSV, OUT_GCS_LONG_CSV)

summary = {
    "run_id_h": RUN_ID_H,
    "input_g_point_csv": CFG_H.g_point_csv,
    "output_long_csv": OUT_GCS_LONG_CSV,
    "config": asdict(CFG_H),
    "n_input_points": int(len(g)),
    "n_unique_points": int(g["point_id"].nunique()),
    "n_periods_requested": int(len(periods)),
    "periods": periods,
    "failed_periods": failed,
    "n_output_rows": int(len(h_long)),
    "n_rows_with_s2_value": int(h_long["has_s2_value"].sum()),
    "period_role_counts": h_long["period_role"].value_counts(dropna=False).to_dict(),
}

with open(LOCAL_SUMMARY_JSON, "w") as f:
    json.dump(summary, f, indent=2)

upload_no_overwrite(LOCAL_SUMMARY_JSON, OUT_GCS_SUMMARY_JSON)

print("\nComponent H finished.")
print("Long monthly S2 output:")
print(OUT_GCS_LONG_CSV)
print("\nSummary:")
print(OUT_GCS_SUMMARY_JSON)

print("\nOutput shape:", h_long.shape)
print("Rows with S2 value:", h_long["has_s2_value"].sum())
print("\nPeriod role counts:")
print(h_long["period_role"].value_counts(dropna=False))
display(h_long.head())


# ============================================================
# COMPONENT I — ENGINEERED TEMPORAL FEATURES
# Creates point-year wide features for historical training + 2026 prediction
# ============================================================

!pip -q install google-cloud-storage pandas numpy scikit-learn

import os, io, json
from datetime import datetime, timezone
from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd
from google.cloud import storage


RUN_ID_I = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

@dataclass
class ComponentIConfig:
    project_id: str = "gcp-clag-remote-mapping"

    # Uses latest H output variable if available.
    h_long_csv: str = globals().get(
        "OUT_GCS_LONG_CSV",
        "gs://storage_cropmapping/Finals/high_plains_crop_mapping/"
        "componentH_monthly_s2_historical2024_2025_predict2026/"
        "PASTE_RUN_ID/02_outputs/componentH_s2_monthly_point_timeseries.csv"
    )

    out_gcs_root: str = (
        "gs://storage_cropmapping/Finals/high_plains_crop_mapping/"
        f"componentI_temporal_features_historical2024_2025_predict2026/{RUN_ID_I}"
    )

    local_root: str = f"/content/componentI_temporal_features/{RUN_ID_I}"

    historical_years: tuple = (2024, 2025)
    prediction_year: int = 2026

CFG_I = ComponentIConfig()
os.makedirs(CFG_I.local_root, exist_ok=True)

LOCAL_FEATURES_CSV = os.path.join(CFG_I.local_root, "componentI_temporal_features_point_year.csv")
LOCAL_TRAIN_CSV = os.path.join(CFG_I.local_root, "componentI_train_2024_2025.csv")
LOCAL_PREDICT_CSV = os.path.join(CFG_I.local_root, "componentI_predict_2026.csv")
LOCAL_SUMMARY_JSON = os.path.join(CFG_I.local_root, "componentI_summary.json")

OUT_GCS_FEATURES_CSV = f"{CFG_I.out_gcs_root}/componentI_temporal_features_point_year.csv"
OUT_GCS_TRAIN_CSV = f"{CFG_I.out_gcs_root}/componentI_train_2024_2025.csv"
OUT_GCS_PREDICT_CSV = f"{CFG_I.out_gcs_root}/componentI_predict_2026.csv"
OUT_GCS_SUMMARY_JSON = f"{CFG_I.out_gcs_root}/componentI_summary.json"

print("RUN_ID_I:", RUN_ID_I)
print("Input H long CSV:", CFG_I.h_long_csv)
print("Output GCS root:", CFG_I.out_gcs_root)


gcs_client = storage.Client(project=CFG_I.project_id)

def split_gcs_path(gs_path):
    assert gs_path.startswith("gs://")
    x = gs_path.replace("gs://", "", 1)
    bucket = x.split("/")[0]
    blob = "/".join(x.split("/")[1:])
    return bucket, blob

def gcs_exists(gs_path):
    bucket, blob = split_gcs_path(gs_path)
    return gcs_client.bucket(bucket).blob(blob).exists()

def read_gcs_csv(gs_path):
    bucket, blob = split_gcs_path(gs_path)
    text = gcs_client.bucket(bucket).blob(blob).download_as_text()
    return pd.read_csv(io.StringIO(text))

def upload_no_overwrite(local_path, gs_path):
    if gcs_exists(gs_path):
        print("Exists, skip upload:", gs_path)
        return
    bucket, blob = split_gcs_path(gs_path)
    gcs_client.bucket(bucket).blob(blob).upload_from_filename(local_path)
    print("Uploaded:", gs_path)

# LOAD H
h = read_gcs_csv(CFG_I.h_long_csv).copy()

print("\nLoaded H rows:", len(h))
print("Columns:", h.columns.tolist())

required = ["point_id", "year", "month"]
for c in required:
    if c not in h.columns:
        raise ValueError(f"Missing required column: {c}")

h["year"] = pd.to_numeric(h["year"], errors="coerce").astype("Int64")
h["month"] = pd.to_numeric(h["month"], errors="coerce").astype("Int64")

feature_vars = [
    "B2", "B3", "B4", "B8", "B11", "B12",
    "NDVI", "EVI2", "NDMI", "SWIR1_RATIO", "SWIR2_INDEX"
]

feature_vars = [v for v in feature_vars if v in h.columns]

for v in feature_vars:
    h[v] = pd.to_numeric(h[v], errors="coerce")

for c in ["lon", "lat", "p_point_corn", "p_point_cotton", "p_point_soybean", "p_point_wheat",
          "field_conf", "conf_point", "overall_conf_point", "cropfrac_best"]:
    if c in h.columns:
        h[c] = pd.to_numeric(h[c], errors="coerce")


id_cols = [
    "point_id", "year",
    "lon", "lat",
    "p_point_corn", "p_point_cotton", "p_point_soybean", "p_point_wheat",
    "field_conf", "conf_point", "overall_conf_point", "cropfrac_best",
    "hard_label_point", "soft_label_point", "componentE_crop", "expected_crop"
]

id_cols = [c for c in id_cols if c in h.columns]

base = (
    h[id_cols]
    .sort_values(["point_id", "year"])
    .drop_duplicates(["point_id", "year"], keep="first")
    .copy()
)

# MONTHLY WIDE FEATURES
wide_parts = []

for var in feature_vars:
    tmp = h.pivot_table(
        index=["point_id", "year"],
        columns="month",
        values=var,
        aggfunc="median"
    )

    tmp.columns = [f"{var}_m{int(m):02d}" for m in tmp.columns]
    wide_parts.append(tmp)

if not wide_parts:
    raise RuntimeError("No feature variables found to pivot.")

wide = pd.concat(wide_parts, axis=1).reset_index()

feat = base.merge(wide, on=["point_id", "year"], how="left")

# ENGINEERED TEMPORAL FEATURES
def month_cols_for(var):
    return sorted([c for c in feat.columns if c.startswith(f"{var}_m")])

for var in feature_vars:
    cols = month_cols_for(var)
    if not cols:
        continue

    feat[f"{var}_mean"] = feat[cols].mean(axis=1)
    feat[f"{var}_median"] = feat[cols].median(axis=1)
    feat[f"{var}_max"] = feat[cols].max(axis=1)
    feat[f"{var}_min"] = feat[cols].min(axis=1)
    feat[f"{var}_range"] = feat[f"{var}_max"] - feat[f"{var}_min"]
    feat[f"{var}_std"] = feat[cols].std(axis=1)
    feat[f"{var}_n_months"] = feat[cols].notna().sum(axis=1)

    try:
        feat[f"{var}_peak_month"] = (
            feat[cols]
            .idxmax(axis=1)
            .str.extract(r"_m(\d+)$")[0]
            .astype(float)
        )
    except Exception:
        feat[f"{var}_peak_month"] = np.nan

    early_cols = [c for c in cols if any(c.endswith(f"_m{m:02d}") for m in [3, 4, 5])]
    mid_cols = [c for c in cols if any(c.endswith(f"_m{m:02d}") for m in [6, 7])]
    late_cols = [c for c in cols if any(c.endswith(f"_m{m:02d}") for m in [8, 9, 10])]

    if early_cols:
        feat[f"{var}_early_mean"] = feat[early_cols].mean(axis=1)
    if mid_cols:
        feat[f"{var}_mid_mean"] = feat[mid_cols].mean(axis=1)
    if late_cols:
        feat[f"{var}_late_mean"] = feat[late_cols].mean(axis=1)

    if early_cols and late_cols:
        feat[f"{var}_late_minus_early"] = feat[f"{var}_late_mean"] - feat[f"{var}_early_mean"]

    if early_cols and mid_cols:
        feat[f"{var}_mid_minus_early"] = feat[f"{var}_mid_mean"] - feat[f"{var}_early_mean"]

# Crop-specific useful features
if "NDVI_max" in feat.columns and "NDVI_min" in feat.columns:
    feat["NDVI_amplitude"] = feat["NDVI_max"] - feat["NDVI_min"]

if "NDMI_max" in feat.columns and "NDMI_min" in feat.columns:
    feat["NDMI_amplitude"] = feat["NDMI_max"] - feat["NDMI_min"]

if "B11_mean" in feat.columns and "B8_mean" in feat.columns:
    feat["mean_SWIR1_to_NIR"] = feat["B11_mean"] / feat["B8_mean"].replace(0, np.nan)

if "NDVI_peak_month" in feat.columns:
    feat["is_early_peak"] = feat["NDVI_peak_month"].isin([3, 4, 5]).astype(int)
    feat["is_late_peak"] = feat["NDVI_peak_month"].isin([8, 9, 10]).astype(int)

# TRAIN / PREDICT SPLIT
feat["dataset_role"] = np.where(
    feat["year"].astype(int).isin(CFG_I.historical_years),
    "train_historical",
    np.where(
        feat["year"].astype(int) == CFG_I.prediction_year,
        "predict_2026",
        "other"
    )
)

# Training label preference:
# hard_label_point if not uncertain, otherwise soft_label_point.
def choose_training_label(row):
    hard = str(row.get("hard_label_point", "")).strip().lower()
    soft = str(row.get("soft_label_point", "")).strip().lower()

    if hard not in ["", "nan", "none", "uncertain"]:
        return hard
    if soft not in ["", "nan", "none", "uncertain"]:
        return soft
    return "uncertain"

feat["training_label"] = feat.apply(choose_training_label, axis=1)

train_df = feat[
    (feat["dataset_role"] == "train_historical") &
    (feat["training_label"].isin(["corn", "cotton", "soybean", "wheat"]))
].copy()

predict_df = feat[feat["dataset_role"] == "predict_2026"].copy()

# SAVE
feat.to_csv(LOCAL_FEATURES_CSV, index=False)
train_df.to_csv(LOCAL_TRAIN_CSV, index=False)
predict_df.to_csv(LOCAL_PREDICT_CSV, index=False)

summary = {
    "run_id_i": RUN_ID_I,
    "input_h_long_csv": CFG_I.h_long_csv,
    "output_features_csv": OUT_GCS_FEATURES_CSV,
    "output_train_csv": OUT_GCS_TRAIN_CSV,
    "output_predict_csv": OUT_GCS_PREDICT_CSV,
    "config": asdict(CFG_I),
    "n_h_rows": int(len(h)),
    "n_feature_rows": int(len(feat)),
    "n_train_rows": int(len(train_df)),
    "n_predict_2026_rows": int(len(predict_df)),
    "feature_vars": feature_vars,
    "dataset_role_counts": feat["dataset_role"].value_counts(dropna=False).to_dict(),
    "training_label_counts": train_df["training_label"].value_counts(dropna=False).to_dict(),
}

with open(LOCAL_SUMMARY_JSON, "w") as f:
    json.dump(summary, f, indent=2)

upload_no_overwrite(LOCAL_FEATURES_CSV, OUT_GCS_FEATURES_CSV)
upload_no_overwrite(LOCAL_TRAIN_CSV, OUT_GCS_TRAIN_CSV)
upload_no_overwrite(LOCAL_PREDICT_CSV, OUT_GCS_PREDICT_CSV)
upload_no_overwrite(LOCAL_SUMMARY_JSON, OUT_GCS_SUMMARY_JSON)

print("\nComponent I finished.")
print("All point-year features:")
print(OUT_GCS_FEATURES_CSV)
print("\nTraining table 2024–2025:")
print(OUT_GCS_TRAIN_CSV)
print("\nPrediction table 2026:")
print(OUT_GCS_PREDICT_CSV)
print("\nSummary:")
print(OUT_GCS_SUMMARY_JSON)

print("\nFeature shape:", feat.shape)
print("Train shape:", train_df.shape)
print("Predict 2026 shape:", predict_df.shape)

display(feat.head())


# ============================================================
# COMPONENT J, 2026 COTTON ACTIVE-LEARNING CANDIDATE SELECTOR
# ============================================================

!pip -q install google-cloud-storage scikit-learn pandas numpy joblib

import os, io, json, math
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from google.cloud import storage

from sklearn.preprocessing import StandardScaler
from sklearn.impute import SimpleImputer
from sklearn.metrics.pairwise import cosine_similarity
from sklearn.neighbors import NearestNeighbors
from sklearn.pipeline import Pipeline

PROJECT_ID = "gcp-clag-remote-mapping"
BUCKET_NAME = "storage_cropmapping"

RUN_ID_J = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

COMPONENT_I_FEATURES_CSV = (
    "gs://storage_cropmapping/Finals/high_plains_crop_mapping/"
    "componentI_temporal_features_historical2024_2025_predict2026/"
    "20260504_185705/componentI_temporal_features_point_year.csv"
)

OUT_ROOT = (
    "gs://storage_cropmapping/Finals/high_plains_crop_mapping/"
    f"componentJ_v8_cotton_active_learning_2026/{RUN_ID_J}"
)

LOCAL_ROOT = f"/content/componentJ_v8_cotton_active_learning_2026/{RUN_ID_J}"
os.makedirs(LOCAL_ROOT, exist_ok=True)

TOP_N_REVIEW = 80
MIN_DISTANCE_KM_BETWEEN_SELECTED = 3.0

TRUSTED_COTTON_CONF_MIN = 0.50
TRUSTED_COTTON_FIELD_CONF_MIN = 0.60

EXCLUDE_ALREADY_COTTON_2026 = False

print("RUN_ID_J:", RUN_ID_J)
print("Input Component I:", COMPONENT_I_FEATURES_CSV)
print("Output root:", OUT_ROOT)


storage_client = storage.Client(project=PROJECT_ID)

def parse_gcs(gs_path):
    assert gs_path.startswith("gs://")
    bucket, blob = gs_path.replace("gs://", "").split("/", 1)
    return bucket, blob

def read_gcs_csv(gs_path):
    bucket, blob = parse_gcs(gs_path)
    text = storage_client.bucket(bucket).blob(blob).download_as_text()
    return pd.read_csv(io.StringIO(text))

def upload_no_overwrite(local_path, gs_path):
    bucket, blob = parse_gcs(gs_path)
    b = storage_client.bucket(bucket).blob(blob)
    if b.exists():
        print("Exists, skip:", gs_path)
        return
    b.upload_from_filename(local_path)
    print("Uploaded:", gs_path)

# LOAD COMPONENT I
df = read_gcs_csv(COMPONENT_I_FEATURES_CSV).copy()

print("\nLoaded Component I:", df.shape)
print("Columns:", len(df.columns))

df["year"] = pd.to_numeric(df["year"], errors="coerce").astype("Int64")

for c in [
    "lon", "lat",
    "p_point_corn", "p_point_cotton", "p_point_soybean", "p_point_wheat",
    "field_conf", "conf_point", "overall_conf_point"
]:
    if c in df.columns:
        df[c] = pd.to_numeric(df[c], errors="coerce")

for c in ["training_label", "hard_label_point", "soft_label_point", "componentE_crop", "expected_crop"]:
    if c not in df.columns:
        df[c] = ""
    df[c] = df[c].astype(str).str.lower().str.strip()

print("\nRows by year:")
print(df["year"].value_counts(dropna=False).sort_index())

print("\nTraining label counts:")
print(df["training_label"].value_counts(dropna=False))

# SELECT FEATURES
drop_cols = {
    "point_id", "year", "lon", "lat",
    "dataset_role", "training_label",
    "hard_label_point", "soft_label_point",
    "componentE_crop", "expected_crop",
    "p_point_corn", "p_point_cotton", "p_point_soybean", "p_point_wheat",
    "field_conf", "conf_point", "overall_conf_point",
}

feature_keywords = [
    "NDVI", "EVI2", "NDMI",
    "SWIR1_RATIO", "SWIR2_INDEX",
    "B8", "B11", "B12",
    "_m03", "_m04", "_m05",
    "_mean", "_median", "_max", "_min", "_range", "_std",
    "_early_mean", "_mid_mean",
    "peak_month", "amplitude", "SWIR1_to_NIR",
    "is_early_peak", "is_late_peak",
]

numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()

feature_cols = [
    c for c in numeric_cols
    if c not in drop_cols
    and any(k in c for k in feature_keywords)
    and df[c].notna().mean() >= 0.25
]

if len(feature_cols) == 0:
    raise RuntimeError("No usable temporal Sentinel-2 feature columns found.")

print("\nSelected feature count:", len(feature_cols))
print(feature_cols[:80])

# TRUSTED COTTON SEEDS FROM 2024–2025
train = df[df["year"].isin([2024, 2025])].copy()

trusted_cotton = train[
    (
        (train["training_label"] == "cotton") |
        (train["hard_label_point"] == "cotton") |
        (train["soft_label_point"] == "cotton") |
        (train["componentE_crop"] == "cotton")
    )
    &
    (train["p_point_cotton"].fillna(0) >= TRUSTED_COTTON_CONF_MIN)
    &
    (train["field_conf"].fillna(0) >= TRUSTED_COTTON_FIELD_CONF_MIN)
].copy()

trusted_cotton = trusted_cotton.dropna(subset=["lon", "lat"]).copy()

if len(trusted_cotton) < 5:
    raise RuntimeError(
        f"Too few trusted cotton seeds: {len(trusted_cotton)}. "
        "Lower thresholds or add manual cotton labels."
    )

print("\nTrusted cotton seeds:", trusted_cotton.shape)
print(trusted_cotton["year"].value_counts().sort_index())

# 2026 CANDIDATE POOL
cand_2026 = df[df["year"] == 2026].copy()
cand_2026 = cand_2026.dropna(subset=["lon", "lat"]).copy()

if EXCLUDE_ALREADY_COTTON_2026:
    cand_2026 = cand_2026[cand_2026["training_label"] != "cotton"].copy()

print("\n2026 candidate pool:", cand_2026.shape)

# COTTON PROTOTYPE SIMILARITY
X_all_raw = cand_2026[feature_cols].copy()
X_seed_raw = trusted_cotton[feature_cols].copy()

pipe = Pipeline([
    ("imputer", SimpleImputer(strategy="median")),
    ("scaler", StandardScaler())
])

X_seed_scaled = pipe.fit_transform(X_seed_raw)
X_all_scaled = pipe.transform(X_all_raw)

cotton_proto = X_seed_scaled.mean(axis=0).reshape(1, -1)

cand_2026["cotton_proto_cosine"] = cosine_similarity(X_all_scaled, cotton_proto).ravel()

nn = NearestNeighbors(
    n_neighbors=min(5, len(trusted_cotton)),
    metric="euclidean"
)
nn.fit(X_seed_scaled)

distances, indices = nn.kneighbors(X_all_scaled)

cand_2026["cotton_nn_dist_mean5"] = distances.mean(axis=1)
cand_2026["cotton_nn_dist_min"] = distances.min(axis=1)
cand_2026["cotton_nn_score"] = 1 / (1 + cand_2026["cotton_nn_dist_mean5"])

cand_2026["cotton_candidate_score"] = (
    0.45 * cand_2026["cotton_proto_cosine"].rank(pct=True) +
    0.35 * cand_2026["cotton_nn_score"].rank(pct=True) +
    0.20 * cand_2026["p_point_cotton"].fillna(0).rank(pct=True)
)

print("\nScore summary:")
display(cand_2026[[
    "cotton_proto_cosine",
    "cotton_nn_score",
    "p_point_cotton",
    "cotton_candidate_score"
]].describe())

# SPATIAL DIVERSITY SELECTION
def haversine_km(lon1, lat1, lon2, lat2):
    R = 6371.0
    lon1, lat1, lon2, lat2 = map(np.radians, [lon1, lat1, lon2, lat2])
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    a = np.sin(dlat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
    return R * 2 * np.arcsin(np.sqrt(a))

ranked = cand_2026.sort_values("cotton_candidate_score", ascending=False).reset_index(drop=True)

selected = []

for _, row in ranked.iterrows():
    if len(selected) == 0:
        selected.append(row)
    else:
        too_close = False
        for s in selected:
            d = haversine_km(row["lon"], row["lat"], s["lon"], s["lat"])
            if d < MIN_DISTANCE_KM_BETWEEN_SELECTED:
                too_close = True
                break

        if not too_close:
            selected.append(row)

    if len(selected) >= TOP_N_REVIEW:
        break

review_candidates = pd.DataFrame(selected).reset_index(drop=True)

review_candidates["review_rank"] = np.arange(1, len(review_candidates) + 1)
review_candidates["google_maps_url"] = review_candidates.apply(
    lambda r: f"https://www.google.com/maps?q={r['lat']},{r['lon']}",
    axis=1
)

review_candidates["manual_label"] = ""
review_candidates["manual_confidence"] = ""
review_candidates["manual_notes"] = ""
review_candidates["review_status"] = "pending"

print("\nSelected review candidates:", review_candidates.shape)

display(review_candidates[[
    "review_rank",
    "point_id", "lon", "lat", "google_maps_url",
    "training_label", "hard_label_point", "soft_label_point", "componentE_crop",
    "p_point_cotton", "field_conf", "conf_point",
    "cotton_proto_cosine", "cotton_nn_score", "cotton_candidate_score",
    "manual_label", "manual_confidence", "manual_notes"
]].head(30))

# SAVE
all_scores_local = os.path.join(
    LOCAL_ROOT,
    "componentJ_v8_2026_all_points_cotton_similarity_scores.csv"
)

review_local = os.path.join(
    LOCAL_ROOT,
    "componentJ_v8_2026_manual_review_candidates.csv"
)

trusted_local = os.path.join(
    LOCAL_ROOT,
    "componentJ_v8_trusted_cotton_seeds_2024_2025.csv"
)

features_local = os.path.join(
    LOCAL_ROOT,
    "componentJ_v8_similarity_features.csv"
)

metadata_local = os.path.join(
    LOCAL_ROOT,
    "metadata_componentJ_v8_cotton_active_learning.json"
)

cand_2026.to_csv(all_scores_local, index=False)
review_candidates.to_csv(review_local, index=False)
trusted_cotton.to_csv(trusted_local, index=False)
pd.DataFrame({"feature": feature_cols}).to_csv(features_local, index=False)

outputs = {
    "all_2026_scores": f"{OUT_ROOT}/componentJ_v8_2026_all_points_cotton_similarity_scores.csv",
    "manual_review_candidates": f"{OUT_ROOT}/componentJ_v8_2026_manual_review_candidates.csv",
    "trusted_cotton_seeds": f"{OUT_ROOT}/componentJ_v8_trusted_cotton_seeds_2024_2025.csv",
    "similarity_features": f"{OUT_ROOT}/componentJ_v8_similarity_features.csv",
    "metadata": f"{OUT_ROOT}/metadata_componentJ_v8_cotton_active_learning.json",
}

metadata = {
    "run_id_j": RUN_ID_J,
    "task": "2026 cotton active-learning candidate selector",
    "input_componentI_features_csv": COMPONENT_I_FEATURES_CSV,
    "n_all_rows": int(len(df)),
    "n_train_rows_2024_2025": int(len(train)),
    "n_predict_rows_2026": int(len(cand_2026)),
    "n_trusted_cotton_seeds": int(len(trusted_cotton)),
    "n_review_candidates": int(len(review_candidates)),
    "top_n_review": int(TOP_N_REVIEW),
    "min_distance_km_between_selected": float(MIN_DISTANCE_KM_BETWEEN_SELECTED),
    "trusted_cotton_rule": {
        "cotton_sources": [
            "training_label",
            "hard_label_point",
            "soft_label_point",
            "componentE_crop"
        ],
        "p_point_cotton_min": TRUSTED_COTTON_CONF_MIN,
        "field_conf_min": TRUSTED_COTTON_FIELD_CONF_MIN
    },
    "outputs": outputs,
    "next_step": (
        "Open manual_review_candidates, inspect Google Maps/GSV, "
        "fill manual_label as cotton/non_cotton/uncertain/non_field/bad_view, "
        "then use reviewed labels to train a supervised Component J model."
    )
}

with open(metadata_local, "w") as f:
    json.dump(metadata, f, indent=2)

upload_no_overwrite(all_scores_local, outputs["all_2026_scores"])
upload_no_overwrite(review_local, outputs["manual_review_candidates"])
upload_no_overwrite(trusted_local, outputs["trusted_cotton_seeds"])
upload_no_overwrite(features_local, outputs["similarity_features"])
upload_no_overwrite(metadata_local, outputs["metadata"])

print("\nComponent J v8 active-learning selector finished.")
print("\nManual review candidates:")
print(outputs["manual_review_candidates"])
print("\nAll 2026 scores:")
print(outputs["all_2026_scores"])
print("\nMetadata:")
print(outputs["metadata"])

# ============================================================
# COMPONENT J — MANUAL REVIEW UI
# Loads images from Component E manifests
# ============================================================

!pip -q install ipywidgets google-cloud-storage pillow pandas

import os, io, glob, json
import pandas as pd
import numpy as np
from PIL import Image
from IPython.display import display, clear_output, HTML
import ipywidgets as widgets
from google.cloud import storage

PROJECT_ID = "gcp-clag-remote-mapping"
RUN_ID_J = "20260504_193951"

REVIEW_CSV_GCS = (
    "gs://storage_cropmapping/Finals/high_plains_crop_mapping/"
    f"componentJ_v8_cotton_active_learning_2026/{RUN_ID_J}/"
    "componentJ_v8_2026_manual_review_candidates.csv"
)

OUT_REVIEWED_GCS = (
    "gs://storage_cropmapping/Finals/high_plains_crop_mapping/"
    f"componentJ_v8_cotton_active_learning_2026/{RUN_ID_J}/"
    "componentJ_v8_2026_manual_review_candidates_REVIEWED.csv"
)

COMPONENT_E_GCS_ROOT = (
    "gs://storage_cropmapping/Finals/high_plains_crop_mapping/"
    "componentC_D_E_outputs/high_plains_multicrop_pipeline"
)

LOCAL_ROOT = f"/content/componentJ_v8_manual_review_existing_images/{RUN_ID_J}"
os.makedirs(LOCAL_ROOT, exist_ok=True)

LOCAL_REVIEW_CSV = os.path.join(LOCAL_ROOT, "review_candidates.csv")
LOCAL_REVIEWED_CSV = os.path.join(LOCAL_ROOT, "review_candidates_REVIEWED.csv")

CROPS = ["corn", "cotton", "soybean", "wheat"]
SPLITS = ["test", "train"]

LABEL_OPTIONS = [
    "cotton",
    "non_cotton",
    "wheat",
    "corn",
    "soybean",
    "non_field",
    "bad_view",
    "bare_soil",
    "uncertain",
]

CONF_OPTIONS = ["high", "medium", "low"]

client = storage.Client(project=PROJECT_ID)

def parse_gcs(gs_path):
    bucket, blob = gs_path.replace("gs://", "").split("/", 1)
    return bucket, blob

def gcs_exists(gs_path):
    bucket, blob = parse_gcs(gs_path)
    return client.bucket(bucket).blob(blob).exists()

def read_gcs_csv(gs_path):
    bucket, blob = parse_gcs(gs_path)
    text = client.bucket(bucket).blob(blob).download_as_text()
    return pd.read_csv(io.StringIO(text))

def upload_file(local_path, gs_path):
    bucket, blob = parse_gcs(gs_path)
    client.bucket(bucket).blob(blob).upload_from_filename(local_path)
    print("Uploaded:", gs_path)

def load_image_any(path):
    path = str(path)
    if path.startswith("gs://"):
        bucket, blob = parse_gcs(path)
        buf = io.BytesIO()
        client.bucket(bucket).blob(blob).download_to_file(buf)
        buf.seek(0)
        return Image.open(buf).convert("RGB")
    return Image.open(path).convert("RGB")

def pil_to_bytes(img):
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()

# COMPONENT E MANIFEST PATHS
def manifest_gcs_path(crop, split):
    if split == "test":
        return (
            f"{COMPONENT_E_GCS_ROOT}/{crop}/componentE_gsv_images/"
            f"06_images_test_2025/_download_manifest_{crop}_2025.csv"
        )
    else:
        return (
            f"{COMPONENT_E_GCS_ROOT}/{crop}/componentE_gsv_images/"
            f"06_images_train_NOT2025/_download_manifest_{crop}_NOT2025.csv"
        )

def local_img_to_gcs(img_path):
    p = str(img_path)

    if p.startswith("gs://"):
        return p

    marker = "/componentC_D_E_outputs/high_plains_multicrop_pipeline/"
    if marker in p:
        rel = p.split(marker, 1)[1]
        return COMPONENT_E_GCS_ROOT.rstrip("/") + "/" + rel

    marker2 = "/high_plains_multicrop_pipeline/"
    if marker2 in p:
        rel = p.split(marker2, 1)[1]
        return COMPONENT_E_GCS_ROOT.rstrip("/") + "/" + rel

    return p

# LOAD REVIEW CANDIDATES
review_df = read_gcs_csv(REVIEW_CSV_GCS).copy()

for c in ["manual_label", "manual_confidence", "manual_notes", "review_status"]:
    if c not in review_df.columns:
        review_df[c] = ""

review_df["manual_label"] = review_df["manual_label"].fillna("")
review_df["manual_confidence"] = review_df["manual_confidence"].fillna("")
review_df["manual_notes"] = review_df["manual_notes"].fillna("")
review_df["review_status"] = review_df["review_status"].fillna("pending")

# resume from local if exists
if os.path.exists(LOCAL_REVIEWED_CSV):
    print("Resuming from local reviewed CSV.")
    review_df = pd.read_csv(LOCAL_REVIEWED_CSV)

review_df.to_csv(LOCAL_REVIEW_CSV, index=False)

print("Loaded review candidates:", review_df.shape)

# LOAD ALL COMPONENT E MANIFESTS
manifest_parts = []

for crop in CROPS:
    for split in SPLITS:
        gcs_csv = manifest_gcs_path(crop, split)
        print("Checking:", gcs_csv)

        if not gcs_exists(gcs_csv):
            print("  Missing, skip.")
            continue

        m = read_gcs_csv(gcs_csv)

        if len(m) == 0:
            continue

        if "download_ok" in m.columns:
            m = m[m["download_ok"] == True].copy()

        if len(m) == 0:
            continue

        m["manifest_crop"] = crop
        m["manifest_split"] = split
        m["source_manifest"] = gcs_csv

        if "img_path" in m.columns:
            m["resolved_image_path"] = m["img_path"].astype(str).map(local_img_to_gcs)
        elif "image_path" in m.columns:
            m["resolved_image_path"] = m["image_path"].astype(str).map(local_img_to_gcs)
        else:
            continue

        manifest_parts.append(m)

if len(manifest_parts) == 0:
    raise RuntimeError("No Component E image manifests found.")

manifest = pd.concat(manifest_parts, ignore_index=True)
manifest = manifest.drop_duplicates(subset=["point_id", "pano_id", "resolved_image_path"], keep="last")

print("\nLoaded image manifest rows:", manifest.shape)
print(manifest[["manifest_crop", "manifest_split"]].value_counts())

# IMAGE MATCHING
def get_images_for_candidate(row):
    point_id = str(row.get("point_id", ""))
    pano_id = str(row.get("pano_id", "")) if "pano_id" in row.index else ""
    crop_hint = str(row.get("componentE_crop", row.get("training_label", ""))).lower().strip()

    matches = pd.DataFrame()

    if point_id:
        matches = manifest[manifest["point_id"].astype(str) == point_id].copy()

    if matches.empty and pano_id and pano_id != "nan":
        matches = manifest[manifest["pano_id"].astype(str) == pano_id].copy()

    # fallback: crop-based nearby not possible without exact id, so return empty
    if not matches.empty and crop_hint in CROPS and "manifest_crop" in matches.columns:
        crop_matches = matches[matches["manifest_crop"] == crop_hint].copy()
        if len(crop_matches) > 0:
            matches = crop_matches

    if "year" in matches.columns:
        matches["year"] = pd.to_numeric(matches["year"], errors="coerce")
        matches = matches.sort_values(["manifest_split", "year"], ascending=[True, False])

    return matches.head(6).copy()

def next_pending_index(start=0):
    pending = review_df.index[
        review_df["review_status"].astype(str).str.lower() != "done"
    ].tolist()
    if len(pending) == 0:
        return None
    for idx in pending:
        if idx >= start:
            return idx
    return pending[0]

def save_local():
    review_df.to_csv(LOCAL_REVIEWED_CSV, index=False)

def save_to_gcs():
    save_local()
    upload_file(LOCAL_REVIEWED_CSV, OUT_REVIEWED_GCS)

# UI
state = {"idx": next_pending_index(0)}

out = widgets.Output()

label_dropdown = widgets.Dropdown(
    options=LABEL_OPTIONS,
    value="uncertain",
    description="Label:",
    layout=widgets.Layout(width="330px")
)

conf_dropdown = widgets.Dropdown(
    options=CONF_OPTIONS,
    value="medium",
    description="Confidence:",
    layout=widgets.Layout(width="300px")
)

notes_text = widgets.Textarea(
    value="",
    placeholder="Optional notes...",
    description="Notes:",
    layout=widgets.Layout(width="700px", height="80px")
)

save_next_btn = widgets.Button(description="Save + Next", button_style="success")
skip_btn = widgets.Button(description="Skip", button_style="")
prev_btn = widgets.Button(description="Previous", button_style="")
save_gcs_btn = widgets.Button(description="Save CSV to GCS", button_style="info")

def show_current():
    with out:
        clear_output(wait=True)

        idx = state["idx"]
        if idx is None:
            print("All candidates reviewed.")
            print("Reviewed output:")
            print(OUT_REVIEWED_GCS)
            display(review_df)
            return

        row = review_df.loc[idx]

        print(f"Candidate {idx + 1} of {len(review_df)}")
        print("point_id:", row.get("point_id", ""))
        print("lon/lat:", row.get("lon", ""), row.get("lat", ""))
        print("Google Maps:", row.get("google_maps_url", ""))
        print()
        print("Weak labels:")
        print("training_label:", row.get("training_label", ""))
        print("hard_label_point:", row.get("hard_label_point", ""))
        print("soft_label_point:", row.get("soft_label_point", ""))
        print("componentE_crop:", row.get("componentE_crop", ""))
        print("p_point_cotton:", row.get("p_point_cotton", ""))
        print("cotton_candidate_score:", row.get("cotton_candidate_score", ""))
        print()

        matches = get_images_for_candidate(row)

        if matches.empty:
            print("No downloaded image found for this point_id/pano_id in Component E manifests.")
            print("Use Google Maps link above for review.")
        else:
            print("Matched downloaded images:", len(matches))
            cards = []

            for _, m in matches.iterrows():
                img_path = m["resolved_image_path"]

                try:
                    img = load_image_any(img_path)
                    caption = (
                        f"{m.get('manifest_crop','')} | {m.get('manifest_split','')} | "
                        f"date={m.get('date','')} | heading={m.get('heading_best','')}"
                    )

                    cards.append(
                        widgets.VBox([
                            widgets.Label(caption),
                            widgets.Image(
                                value=pil_to_bytes(img),
                                format="jpeg",
                                width=320,
                                height=320
                            ),
                            widgets.HTML(f"<small>{img_path}</small>")
                        ])
                    )

                except Exception as e:
                    print("Could not load image:", img_path)
                    print("Error:", e)

            if cards:
                display(widgets.HBox(cards[:3]))
                if len(cards) > 3:
                    display(widgets.HBox(cards[3:6]))

        existing_label = str(row.get("manual_label", "")).strip()
        existing_conf = str(row.get("manual_confidence", "")).strip()
        existing_notes = str(row.get("manual_notes", "")).strip()

        label_dropdown.value = existing_label if existing_label in LABEL_OPTIONS else "uncertain"
        conf_dropdown.value = existing_conf if existing_conf in CONF_OPTIONS else "medium"
        notes_text.value = "" if existing_notes.lower() == "nan" else existing_notes

def on_save_next_clicked(b):
    idx = state["idx"]
    if idx is None:
        return

    review_df.loc[idx, "manual_label"] = label_dropdown.value
    review_df.loc[idx, "manual_confidence"] = conf_dropdown.value
    review_df.loc[idx, "manual_notes"] = notes_text.value
    review_df.loc[idx, "review_status"] = "done"

    save_local()

    state["idx"] = next_pending_index(idx + 1)
    show_current()

def on_skip_clicked(b):
    idx = state["idx"]
    if idx is None:
        return
    state["idx"] = (idx + 1) % len(review_df)
    show_current()

def on_prev_clicked(b):
    idx = state["idx"]
    if idx is None:
        state["idx"] = max(len(review_df) - 1, 0)
    else:
        state["idx"] = max(idx - 1, 0)
    show_current()

def on_save_gcs_clicked(b):
    with out:
        save_to_gcs()
        print("Saved reviewed CSV to:")
        print(OUT_REVIEWED_GCS)

save_next_btn.on_click(on_save_next_clicked)
skip_btn.on_click(on_skip_clicked)
prev_btn.on_click(on_prev_clicked)
save_gcs_btn.on_click(on_save_gcs_clicked)

display(widgets.VBox([
    widgets.HBox([prev_btn, skip_btn, save_next_btn, save_gcs_btn]),
    widgets.HBox([label_dropdown, conf_dropdown]),
    notes_text,
    out
]))

show_current()

# ============================================================
# COMPONENT J — Cotton vs Non-cotton model
# ============================================================

!pip -q install google-cloud-storage scikit-learn pandas numpy joblib

import os, io, json, joblib
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from google.cloud import storage

from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.model_selection import GroupKFold
from sklearn.metrics import classification_report, confusion_matrix, accuracy_score, f1_score


PROJECT_ID = "gcp-clag-remote-mapping"
RUN_ID_J_FINAL = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

COMPONENT_I_FEATURES_CSV = (
    "gs://storage_cropmapping/Finals/high_plains_crop_mapping/"
    "componentI_temporal_features_historical2024_2025_predict2026/"
    "20260504_185705/componentI_temporal_features_point_year.csv"
)

REVIEWED_CSV = "gs://storage_cropmapping/Finals/high_plains_crop_mapping/componentJ_v8_cotton_active_learning_2026/20260504_193951/componentJ_v8_2026_manual_review_candidates_REVIEWED.csv"

OUT_ROOT = (
    "gs://storage_cropmapping/Finals/high_plains_crop_mapping/"
    f"componentJ_v8_final_cotton_binary_2026/{RUN_ID_J_FINAL}"
)

LOCAL_ROOT = f"/content/componentJ_v8_final_cotton_binary_2026/{RUN_ID_J_FINAL}"
os.makedirs(LOCAL_ROOT, exist_ok=True)

print("RUN_ID_J_FINAL:", RUN_ID_J_FINAL)
print("Input Component I:", COMPONENT_I_FEATURES_CSV)
print("Reviewed labels:", REVIEWED_CSV)
print("Output root:", OUT_ROOT)


client = storage.Client(project=PROJECT_ID)

def parse_gcs(gs_path):
    bucket, blob = gs_path.replace("gs://", "").split("/", 1)
    return bucket, blob

def read_gcs_csv(gs_path):
    bucket, blob = parse_gcs(gs_path)
    text = client.bucket(bucket).blob(blob).download_as_text()
    return pd.read_csv(io.StringIO(text))

def upload(local_path, gs_path):
    bucket, blob = parse_gcs(gs_path)
    client.bucket(bucket).blob(blob).upload_from_filename(local_path)
    print("Uploaded:", gs_path)

# LOAD DATA
df = read_gcs_csv(COMPONENT_I_FEATURES_CSV).copy()
review = read_gcs_csv(REVIEWED_CSV).copy()

df["year"] = pd.to_numeric(df["year"], errors="coerce").astype("Int64")

for c in ["training_label", "hard_label_point", "soft_label_point", "componentE_crop"]:
    if c not in df.columns:
        df[c] = ""
    df[c] = df[c].astype(str).str.lower().str.strip()

review["manual_label"] = review["manual_label"].astype(str).str.lower().str.strip()
review["manual_confidence"] = review["manual_confidence"].astype(str).str.lower().str.strip()

print("\nComponent I:", df.shape)
print(df["year"].value_counts().sort_index())

print("\nReviewed labels:")
print(review["manual_label"].value_counts(dropna=False))

# FEATURE SELECTION
drop_cols = {
    "point_id", "year", "lon", "lat",
    "dataset_role", "training_label",
    "hard_label_point", "soft_label_point",
    "componentE_crop", "expected_crop",
    "p_point_corn", "p_point_cotton", "p_point_soybean", "p_point_wheat",
    "field_conf", "conf_point", "overall_conf_point",
}

feature_keywords = [
    "NDVI", "EVI2", "NDMI",
    "SWIR1_RATIO", "SWIR2_INDEX",
    "B8", "B11", "B12",
    "_m03", "_m04", "_m05",
    "_mean", "_median", "_max", "_min", "_range", "_std",
    "_early_mean", "_mid_mean",
    "peak_month", "amplitude", "SWIR1_to_NIR",
    "is_early_peak", "is_late_peak",
]

numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()

feature_cols = [
    c for c in numeric_cols
    if c not in drop_cols
    and any(k in c for k in feature_keywords)
    and df[c].notna().mean() >= 0.25
]

print("\nSelected feature count:", len(feature_cols))

# TRAINING DATA
hist = df[df["year"].isin([2024, 2025])].copy()

# Weak historical binary label
hist["binary_label"] = np.where(hist["training_label"] == "cotton", 1, 0)
hist["label_source"] = "weak_2024_2025"
hist["sample_weight"] = np.where(hist["training_label"] == "cotton", 1.0, 0.75)

# Add reviewed 2026 labels
valid_manual = review[
    review["manual_label"].isin([
        "cotton", "non_cotton", "wheat", "corn", "soybean",
        "non_field", "bad_view", "bare_soil"
    ])
].copy()

valid_manual["binary_label"] = np.where(valid_manual["manual_label"] == "cotton", 1, 0)

conf_weight = {"high": 1.60, "medium": 1.00, "low": 0.60}
valid_manual["sample_weight"] = valid_manual["manual_confidence"].map(conf_weight).fillna(0.80)

manual = df[df["year"] == 2026].merge(
    valid_manual[["point_id", "manual_label", "manual_confidence", "binary_label", "sample_weight"]],
    on="point_id",
    how="inner"
)

manual["label_source"] = "manual_2026"

train_df = pd.concat([hist, manual], ignore_index=True)

print("\nTraining rows:", train_df.shape)
print("Binary labels:")
print(train_df["binary_label"].value_counts())
print("\nLabel sources:")
print(train_df["label_source"].value_counts())

# TRAIN FINAL MODEL
X = train_df[feature_cols].copy()
y = train_df["binary_label"].astype(int)
w = pd.to_numeric(train_df["sample_weight"], errors="coerce").fillna(1.0)

model = HistGradientBoostingClassifier(
    max_iter=250,
    learning_rate=0.04,
    max_leaf_nodes=15,
    l2_regularization=0.05,
    random_state=42
)

model.fit(X, y, sample_weight=w)

# OOF EVALUATION
groups = train_df["point_id"].astype(str)
n_splits = min(5, groups.nunique())

oof = pd.DataFrame()

if n_splits >= 2:
    rows = []
    gkf = GroupKFold(n_splits=n_splits)

    for fold, (tr_idx, va_idx) in enumerate(gkf.split(X, y, groups)):
        m = HistGradientBoostingClassifier(
            max_iter=250,
            learning_rate=0.04,
            max_leaf_nodes=15,
            l2_regularization=0.05,
            random_state=42 + fold
        )

        m.fit(X.iloc[tr_idx], y.iloc[tr_idx], sample_weight=w.iloc[tr_idx])

        p = m.predict_proba(X.iloc[va_idx])[:, 1]
        pred50 = (p >= 0.50).astype(int)
        pred70 = (p >= 0.70).astype(int)

        tmp = train_df.iloc[va_idx][[
            "point_id", "year", "training_label", "label_source"
        ]].copy()

        tmp["y_true"] = y.iloc[va_idx].values
        tmp["oof_p_cotton"] = p
        tmp["oof_pred_thr050"] = pred50
        tmp["oof_pred_thr070"] = pred70
        tmp["fold"] = fold
        rows.append(tmp)

    oof = pd.concat(rows, ignore_index=True)

    print("\nOOF report threshold 0.50:")
    print(classification_report(oof["y_true"], oof["oof_pred_thr050"], target_names=["non_cotton", "cotton"]))

    print("\nOOF confusion matrix threshold 0.50:")
    print(confusion_matrix(oof["y_true"], oof["oof_pred_thr050"]))

    print("\nOOF report threshold 0.70:")
    print(classification_report(oof["y_true"], oof["oof_pred_thr070"], target_names=["non_cotton", "cotton"]))

# PREDICT 2026
pred_2026 = df[df["year"] == 2026].copy()

pred_2026["p_cotton_J_v8_final"] = model.predict_proba(pred_2026[feature_cols])[:, 1]

pred_2026["pred_cotton_thr050"] = (pred_2026["p_cotton_J_v8_final"] >= 0.50).astype(int)
pred_2026["pred_cotton_thr070"] = (pred_2026["p_cotton_J_v8_final"] >= 0.70).astype(int)
pred_2026["pred_cotton_thr080"] = (pred_2026["p_cotton_J_v8_final"] >= 0.80).astype(int)

print("\n2026 prediction probability summary:")
print(pred_2026["p_cotton_J_v8_final"].describe())

print("\n2026 cotton count by threshold:")
print("thr050:", int(pred_2026["pred_cotton_thr050"].sum()))
print("thr070:", int(pred_2026["pred_cotton_thr070"].sum()))
print("thr080:", int(pred_2026["pred_cotton_thr080"].sum()))

display(pred_2026[[
    "point_id", "lon", "lat",
    "training_label", "hard_label_point", "soft_label_point", "componentE_crop",
    "p_point_cotton", "p_cotton_J_v8_final",
    "pred_cotton_thr050", "pred_cotton_thr070", "pred_cotton_thr080"
]].sort_values("p_cotton_J_v8_final", ascending=False).head(30))

# SAVE 
model_local = os.path.join(LOCAL_ROOT, "componentJ_v8_final_cotton_binary_model.joblib")
features_local = os.path.join(LOCAL_ROOT, "componentJ_v8_final_selected_features.csv")
train_local = os.path.join(LOCAL_ROOT, "componentJ_v8_final_training_table.csv")
pred_local = os.path.join(LOCAL_ROOT, "componentJ_v8_2026_point_predictions.csv")
oof_local = os.path.join(LOCAL_ROOT, "componentJ_v8_oof_predictions.csv")
meta_local = os.path.join(LOCAL_ROOT, "componentJ_v8_final_metadata.json")

bundle = {
    "pipeline": model,
    "feature_cols": feature_cols,
    "model_type": "HistGradientBoostingClassifier",
    "target": "cotton_vs_non_cotton",
    "run_id": RUN_ID_J_FINAL,
}

joblib.dump(bundle, model_local)

pd.DataFrame({"feature": feature_cols}).to_csv(features_local, index=False)
train_df.to_csv(train_local, index=False)
pred_2026.to_csv(pred_local, index=False)
oof.to_csv(oof_local, index=False)

outputs = {
    "model": f"{OUT_ROOT}/componentJ_v8_final_cotton_binary_model.joblib",
    "selected_features": f"{OUT_ROOT}/componentJ_v8_final_selected_features.csv",
    "training_table": f"{OUT_ROOT}/componentJ_v8_final_training_table.csv",
    "predictions_2026": f"{OUT_ROOT}/componentJ_v8_2026_point_predictions.csv",
    "oof_predictions": f"{OUT_ROOT}/componentJ_v8_oof_predictions.csv",
    "metadata": f"{OUT_ROOT}/componentJ_v8_final_metadata.json",
}

metadata = {
    "run_id": RUN_ID_J_FINAL,
    "input_componentI_features": COMPONENT_I_FEATURES_CSV,
    "reviewed_csv": REVIEWED_CSV,
    "n_features": len(feature_cols),
    "n_training_rows": int(len(train_df)),
    "n_manual_rows": int(len(manual)),
    "n_2026_predictions": int(len(pred_2026)),
    "binary_label_counts": train_df["binary_label"].value_counts().to_dict(),
    "label_source_counts": train_df["label_source"].value_counts().to_dict(),
    "outputs": outputs,
    "recommended_threshold": 0.70,
    "note": "Use this as cotton vs non-cotton Component J. Corn, soybean, wheat, bare soil, non-field, and bad views are treated as non-cotton."
}

with open(meta_local, "w") as f:
    json.dump(metadata, f, indent=2)

upload(model_local, outputs["model"])
upload(features_local, outputs["selected_features"])
upload(train_local, outputs["training_table"])
upload(pred_local, outputs["predictions_2026"])
upload(oof_local, outputs["oof_predictions"])
upload(meta_local, outputs["metadata"])

print("\nComponent J v8 final finished.")
print(json.dumps(outputs, indent=2))


# ============================================================
# COMPONENT K+L
# ============================================================

!pip -q install google-cloud-storage rasterio geopandas shapely scipy matplotlib geemap earthengine-api joblib pyogrio fiona

import os, json
from dataclasses import dataclass
from datetime import datetime, timezone, date

import numpy as np
import pandas as pd
import rasterio
from rasterio.warp import reproject, Resampling, transform_bounds
from rasterio.features import shapes
import geopandas as gpd
from shapely.geometry import shape
from scipy import ndimage as ndi
from google.cloud import storage

import ee
import geemap
import joblib


RUN_ID_KL = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

@dataclass
class CFG:
    project_id: str = "gcp-clag-remote-mapping"

    # Reference tile/grid
    old_tile_gcs: str = "gs://storage_cropmapping/componentJ_outputs/texas_tiles/componentJ_texas_tile_1788_class.tif"

    model_gcs: str = (
        "gs://storage_cropmapping/Finals/high_plains_crop_mapping/"
        "componentJ_v8_final_cotton_binary_2026/20260504_205022/"
        "componentJ_v8_final_cotton_binary_model.joblib"
    )

    selected_features_gcs: str = (
        "gs://storage_cropmapping/Finals/high_plains_crop_mapping/"
        "componentJ_v8_final_cotton_binary_2026/20260504_205022/"
        "componentJ_v8_final_selected_features.csv"
    )

    # Existing 2025 teacher product
    existing_2025_root_gcs: str = (
        "gs://storage_cropmapping/componentJ_v4_tile_specific/"
        "tile1788_exact_grid_fixed"
    )

    local_root: str = f"/content/componentKL_Jv8_tile1788/{RUN_ID_KL}"

    bank_gcs_root: str = (
        f"gs://storage_cropmapping/Finals/high_plains_crop_mapping/"
        f"componentK_product_bank_Jv8_tile1788_binary_cotton/{RUN_ID_KL}"
    )

    update_gcs_root: str = (
        f"gs://storage_cropmapping/Finals/high_plains_crop_mapping/"
        f"componentL_update_Jv8_tile1788_2025_to_2026/{RUN_ID_KL}"
    )

    teacher_year: int = 2025
    prediction_year: int = 2026
    historical_years_to_generate: tuple = (2024,)

    months_full: tuple = (3, 4, 5, 6, 7, 8, 9, 10)


    prediction_cutoff_date: str = "2026-05-04"

    ee_export_scale: int = 30
    pred_chunk_size: int = 50000
    threshold_map: float = 0.70

    min_patch_pixels: int = 25
    fill_holes_max_pixels: int = 64
    connectivity: int = 2
    max_export_retries: int = 3

    teacher_conf_high: float = 0.70
    teacher_conf_low: float = 0.30
    change_threshold_ndvi: float = 0.15
    change_threshold_b11: float = 0.03
    change_threshold_prob: float = 0.25
    student_weight_base: float = 0.35
    student_weight_change: float = 0.80

CFG = CFG()
os.makedirs(CFG.local_root, exist_ok=True)

LOCAL_OLD_TILE = os.path.join(CFG.local_root, "old_tile_1788_class.tif")
LOCAL_MODEL = os.path.join(CFG.local_root, "componentJ_v8_model.joblib")
LOCAL_FEATURES = os.path.join(CFG.local_root, "componentJ_v8_selected_features.csv")

print("RUN_ID_KL:", RUN_ID_KL)
print("Local root:", CFG.local_root)
print("K product bank:", CFG.bank_gcs_root)
print("L update root:", CFG.update_gcs_root)


storage_client = storage.Client(project=CFG.project_id)

def parse_gs_path(gs_path):
    bucket = gs_path.replace("gs://", "").split("/", 1)[0]
    blob = gs_path.replace(f"gs://{bucket}/", "")
    return bucket, blob

def gs_exists(gs_path):
    bucket, blob = parse_gs_path(gs_path)
    return storage_client.bucket(bucket).blob(blob).exists()

def gs_download_file(gs_path, local_path):
    os.makedirs(os.path.dirname(local_path), exist_ok=True)
    bucket, blob = parse_gs_path(gs_path)
    storage_client.bucket(bucket).blob(blob).download_to_filename(local_path)
    print("Downloaded:", gs_path)

def gs_upload_no_overwrite(local_path, gs_path):
    if not os.path.exists(local_path):
        print("Missing local file, skip:", local_path)
        return
    if gs_exists(gs_path):
        print("Exists, skip:", gs_path)
        return
    bucket, blob = parse_gs_path(gs_path)
    storage_client.bucket(bucket).blob(blob).upload_from_filename(local_path)
    print("Uploaded:", gs_path)

def save_json(path, obj):
    with open(path, "w") as f:
        json.dump(obj, f, indent=2)

def month_suffix(m):
    return f"m{int(m):02d}"

def read_single_band(path):
    with rasterio.open(path) as src:
        return (
            src.read(1),
            src.profile.copy(),
            src.transform,
            src.crs,
            src.nodata,
            src.bounds,
        )

def read_multiband(path):
    with rasterio.open(path) as src:
        arr = src.read().astype(np.float32)
        profile = src.profile.copy()
        transform = src.transform
        crs = src.crs
        nodata = src.nodata
    if nodata is not None:
        arr[arr == nodata] = np.nan
    return arr, profile, transform, crs

def clean_binary_mask(binary):
    structure = ndi.generate_binary_structure(2, CFG.connectivity)

    labeled, _ = ndi.label(binary, structure=structure)
    sizes = np.bincount(labeled.ravel())
    keep = sizes >= CFG.min_patch_pixels
    keep[0] = False
    cleaned = keep[labeled]

    holes = ~cleaned
    hole_labels, _ = ndi.label(holes, structure=structure)
    hole_sizes = np.bincount(hole_labels.ravel())

    fill_mask = np.zeros_like(cleaned, dtype=bool)
    for i in range(1, len(hole_sizes)):
        if hole_sizes[i] <= CFG.fill_holes_max_pixels:
            fill_mask |= (hole_labels == i)

    return cleaned | fill_mask

def vectorize_mask(mask_arr, transform, crs, out_gpkg, out_geojson):
    valid_binary = (mask_arr == 1).astype(np.uint8)

    geoms, vals = [], []
    for geom, val in shapes(valid_binary, mask=(valid_binary == 1), transform=transform):
        if val == 1:
            geoms.append(shape(geom))
            vals.append(int(val))

    if len(geoms) == 0:
        return None

    gdf = gpd.GeoDataFrame({"class_id": vals}, geometry=geoms, crs=crs)
    gdf["dissolve_key"] = 1
    gdf = gdf.dissolve(by="dissolve_key").explode(index_parts=False).reset_index(drop=True)
    gdf["poly_id"] = np.arange(1, len(gdf) + 1)

    try:
        gdf_proj = gdf.to_crs(gdf.estimate_utm_crs())
        gdf["area_m2"] = gdf_proj.area.values
        gdf["area_ha"] = gdf["area_m2"] / 10000.0
    except Exception:
        gdf["area_m2"] = np.nan
        gdf["area_ha"] = np.nan

    gdf.to_file(out_gpkg, driver="GPKG")
    gdf.to_file(out_geojson, driver="GeoJSON")
    return gdf

# GEE
try:
    ee.Initialize(project=CFG.project_id)
    print("Earth Engine initialized:", CFG.project_id)
except Exception:
    ee.Authenticate()
    ee.Initialize(project=CFG.project_id)
    print("Earth Engine initialized after auth:", CFG.project_id)

#INPUTS
if not os.path.exists(LOCAL_OLD_TILE):
    gs_download_file(CFG.old_tile_gcs, LOCAL_OLD_TILE)

if not os.path.exists(LOCAL_MODEL):
    gs_download_file(CFG.model_gcs, LOCAL_MODEL)

if not os.path.exists(LOCAL_FEATURES):
    gs_download_file(CFG.selected_features_gcs, LOCAL_FEATURES)

bundle = joblib.load(LOCAL_MODEL)
pipeline = bundle["pipeline"]
selected_features = [str(x).strip() for x in bundle["feature_cols"]]

print("Loaded J model.")
print("Selected features:", len(selected_features))

old_arr, old_profile, old_transform, old_crs, old_nodata, old_bounds = read_single_band(LOCAL_OLD_TILE)
old_height, old_width = old_arr.shape

if str(old_crs) != "EPSG:4326":
    bounds_4326 = transform_bounds(
        old_crs, "EPSG:4326",
        old_bounds.left, old_bounds.bottom, old_bounds.right, old_bounds.top,
        densify_pts=21
    )
else:
    bounds_4326 = (old_bounds.left, old_bounds.bottom, old_bounds.right, old_bounds.top)

region_ee = ee.Geometry.Rectangle(list(bounds_4326), proj="EPSG:4326", geodesic=False)

print("Reference tile shape:", old_height, old_width)
print("Bounds EPSG:4326:", bounds_4326)

# MONTH LOGIC
def months_for_year(year):
    if year != CFG.prediction_year:
        return list(CFG.months_full)

    cutoff = datetime.fromisoformat(CFG.prediction_cutoff_date).date()
    months = []
    for m in CFG.months_full:
        if date(year, m, 1) <= cutoff:
            months.append(m)
    return months

# FEATURE ENGINEERING COMPATIBLE WITH COMPONENT I/J
BASE_VARS = ["B2", "B3", "B4", "B8", "B11", "B12", "NDVI", "EVI2", "NDMI", "SWIR1_RATIO", "SWIR2_INDEX"]

def get_month_cols(var, months):
    return [f"{var}_{month_suffix(m)}" for m in months]

def safe_nanargmax(arr):
    out = np.full(arr.shape[0], -1, dtype=int)
    valid = np.isfinite(arr).any(axis=1)
    if valid.any():
        filled = np.where(np.isfinite(arr[valid]), arr[valid], -np.inf)
        out[valid] = np.argmax(filled, axis=1)
    return out

def build_features_from_monthly_stack(monthly_df, required_features, months):
    feat = pd.DataFrame(index=monthly_df.index)

    raw_cols = {}
    for var in BASE_VARS:
        for m in months:
            col = f"{var}_{month_suffix(m)}"
            if col in monthly_df.columns:
                raw_cols[col] = pd.to_numeric(monthly_df[col], errors="coerce")

    feat = pd.concat([feat, pd.DataFrame(raw_cols, index=feat.index)], axis=1)

    derived = {}

    for var in BASE_VARS:
        cols = [c for c in get_month_cols(var, months) if c in feat.columns]
        if len(cols) == 0:
            continue

        arr = feat[cols].to_numpy(dtype=np.float32)

        derived[f"{var}_mean"] = np.nanmean(arr, axis=1)
        derived[f"{var}_median"] = np.nanmedian(arr, axis=1)
        derived[f"{var}_max"] = np.nanmax(arr, axis=1)
        derived[f"{var}_min"] = np.nanmin(arr, axis=1)
        derived[f"{var}_range"] = derived[f"{var}_max"] - derived[f"{var}_min"]
        derived[f"{var}_std"] = np.nanstd(arr, axis=1)
        derived[f"{var}_n_months"] = np.isfinite(arr).sum(axis=1)

        peak_idx = safe_nanargmax(arr)
        month_values = np.array([int(c.split("_m")[-1]) for c in cols])
        peak_month = np.full(arr.shape[0], np.nan)

        valid_peak = peak_idx >= 0
        peak_month[valid_peak] = month_values[peak_idx[valid_peak]]
        derived[f"{var}_peak_month"] = peak_month

        early_cols = [f"{var}_m03", f"{var}_m04", f"{var}_m05"]
        mid_cols = [f"{var}_m06", f"{var}_m07"]
        late_cols = [f"{var}_m08", f"{var}_m09", f"{var}_m10"]

        if all(c in feat.columns for c in early_cols):
            derived[f"{var}_early_mean"] = np.nanmean(feat[early_cols].to_numpy(dtype=np.float32), axis=1)
        else:
            derived[f"{var}_early_mean"] = np.nan

        if all(c in feat.columns for c in mid_cols):
            derived[f"{var}_mid_mean"] = np.nanmean(feat[mid_cols].to_numpy(dtype=np.float32), axis=1)
        else:
            derived[f"{var}_mid_mean"] = np.nan

        if all(c in feat.columns for c in late_cols):
            derived[f"{var}_late_mean"] = np.nanmean(feat[late_cols].to_numpy(dtype=np.float32), axis=1)
        else:
            derived[f"{var}_late_mean"] = np.nan

        derived[f"{var}_late_minus_early"] = derived[f"{var}_late_mean"] - derived[f"{var}_early_mean"]
        derived[f"{var}_mid_minus_early"] = derived[f"{var}_mid_mean"] - derived[f"{var}_early_mean"]

    feat = pd.concat([feat, pd.DataFrame(derived, index=feat.index)], axis=1)

    if {"NDVI_max", "NDVI_min"}.issubset(feat.columns):
        feat["NDVI_amplitude"] = feat["NDVI_max"] - feat["NDVI_min"]
    else:
        feat["NDVI_amplitude"] = np.nan

    if {"NDMI_max", "NDMI_min"}.issubset(feat.columns):
        feat["NDMI_amplitude"] = feat["NDMI_max"] - feat["NDMI_min"]
    else:
        feat["NDMI_amplitude"] = np.nan

    if {"B11_mean", "B8_mean"}.issubset(feat.columns):
        feat["mean_SWIR1_to_NIR"] = feat["B11_mean"] / feat["B8_mean"].replace(0, np.nan)
    else:
        feat["mean_SWIR1_to_NIR"] = np.nan

    if "NDVI_peak_month" in feat.columns:
        feat["is_early_peak"] = feat["NDVI_peak_month"].isin([3, 4, 5]).astype(int)
        feat["is_late_peak"] = feat["NDVI_peak_month"].isin([8, 9, 10]).astype(int)
    else:
        feat["is_early_peak"] = np.nan
        feat["is_late_peak"] = np.nan

    for c in required_features:
        if c not in feat.columns:
            feat[c] = np.nan

    return feat[required_features].copy()

# YEAR PATHS
def year_paths(year):
    year_root = os.path.join(CFG.local_root, f"year_{year}")
    monthly_raw_dir = os.path.join(year_root, "monthly_raw")
    monthly_aligned_dir = os.path.join(year_root, "monthly_aligned")
    outputs_dir = os.path.join(year_root, "outputs")

    os.makedirs(monthly_raw_dir, exist_ok=True)
    os.makedirs(monthly_aligned_dir, exist_ok=True)
    os.makedirs(outputs_dir, exist_ok=True)

    thr_tag = str(CFG.threshold_map).replace(".", "")

    return {
        "year_root": year_root,
        "monthly_raw_dir": monthly_raw_dir,
        "monthly_aligned_dir": monthly_aligned_dir,
        "outputs_dir": outputs_dir,
        "prob_raw": os.path.join(outputs_dir, f"tile1788_{year}_prob_raw.tif"),
        "prob_rescaled": os.path.join(outputs_dir, f"tile1788_{year}_prob_rescaled.tif"),
        "mask": os.path.join(outputs_dir, f"tile1788_{year}_mask_{thr_tag}.tif"),
        "gpkg": os.path.join(outputs_dir, f"tile1788_{year}_polygons_{thr_tag}.gpkg"),
        "geojson": os.path.join(outputs_dir, f"tile1788_{year}_polygons_{thr_tag}.geojson"),
        "summary": os.path.join(outputs_dir, f"tile1788_{year}_summary.json"),
    }

# EE MONTHLY IMAGE
def mask_s2_sr(img):
    scl = img.select("SCL")
    mask = (
        scl.neq(3)
        .And(scl.neq(8))
        .And(scl.neq(9))
        .And(scl.neq(10))
        .And(scl.neq(11))
    )
    return img.updateMask(mask)

def build_monthly_s2_image(year, month, region):
    start = pd.Timestamp(year=year, month=month, day=1)
    end = start + pd.offsets.MonthEnd(1) + pd.Timedelta(days=1)

    col = (
        ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
        .filterDate(start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
        .filterBounds(region)
        .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 70))
        .map(mask_s2_sr)
    )

    img = col.median().select(["B2", "B3", "B4", "B8", "B11", "B12"]).divide(10000).toFloat()

    b2 = img.select("B2")
    b4 = img.select("B4")
    b8 = img.select("B8")
    b11 = img.select("B11")
    b12 = img.select("B12")

    ndvi = b8.subtract(b4).divide(b8.add(b4)).rename("NDVI")
    evi2 = b8.subtract(b4).multiply(2.5).divide(
        b8.add(b4.multiply(2.4)).add(1.0)
    ).rename("EVI2")
    ndmi = b8.subtract(b11).divide(b8.add(b11)).rename("NDMI")
    swir1_ratio = b11.divide(b8).rename("SWIR1_RATIO")
    swir2_index = b11.subtract(b12).divide(b11.add(b12)).rename("SWIR2_INDEX")

    out = ee.Image.cat([img, ndvi, evi2, ndmi, swir1_ratio, swir2_index]).toFloat()

    band_names = ["B2", "B3", "B4", "B8", "B11", "B12", "NDVI", "EVI2", "NDMI", "SWIR1_RATIO", "SWIR2_INDEX"]
    renamed = [f"{b}_{month_suffix(month)}" for b in band_names]

    return out.rename(renamed)

def export_month_safe(year, month, out_dir):
    key = month_suffix(month)
    raw_tif = os.path.join(out_dir, f"tile1788_{year}_{key}.tif")

    if os.path.exists(raw_tif):
        return raw_tif

    img = build_monthly_s2_image(year, month, region_ee)

    for attempt in range(1, CFG.max_export_retries + 1):
        try:
            geemap.ee_export_image(
                img.clip(region_ee),
                filename=raw_tif,
                scale=CFG.ee_export_scale,
                region=region_ee,
                file_per_band=False,
            )
            if os.path.exists(raw_tif):
                return raw_tif
        except Exception as e:
            print(f"Retry {attempt}/{CFG.max_export_retries} failed for {year}-{key}: {e}")

    print(f"Skipping {year}-{key}; export failed.")
    return None

def align_month_to_old_grid(raw_path, aligned_path):
    if raw_path is None or not os.path.exists(raw_path):
        return None

    if os.path.exists(aligned_path):
        return aligned_path

    with rasterio.open(raw_path) as src:
        src_arr = src.read().astype(np.float32)
        src_transform = src.transform
        src_crs = src.crs
        src_nodata = src.nodata

        dst_arr = np.full((src_arr.shape[0], old_height, old_width), np.nan, dtype=np.float32)

        for b in range(src_arr.shape[0]):
            reproject(
                source=src_arr[b],
                destination=dst_arr[b],
                src_transform=src_transform,
                src_crs=src_crs,
                src_nodata=src_nodata,
                dst_transform=old_transform,
                dst_crs=old_crs,
                dst_nodata=np.nan,
                resampling=Resampling.bilinear,
            )

    out_profile = old_profile.copy()
    out_profile.update(dtype="float32", count=dst_arr.shape[0], nodata=np.nan, compress="lzw")

    with rasterio.open(aligned_path, "w", **out_profile) as dst:
        dst.write(dst_arr)

    return aligned_path

# PACKAGE PRODUCT BANK
def package_year_to_bank(year):
    yp = year_paths(year)
    bank_prefix = f"{CFG.bank_gcs_root}/year={year}"

    uploads = {
        f"{bank_prefix}/P_raw.tif": yp["prob_raw"],
        f"{bank_prefix}/P_rescaled.tif": yp["prob_rescaled"],
        f"{bank_prefix}/M_mask.tif": yp["mask"],
        f"{bank_prefix}/V_polygons.gpkg": yp["gpkg"],
        f"{bank_prefix}/V_polygons.geojson": yp["geojson"],
        f"{bank_prefix}/metadata.json": yp["summary"],
    }

    for gcs_path, local_path in uploads.items():
        gs_upload_no_overwrite(local_path, gcs_path)

# 2025 TEACHER BOOTSTRAP
def bootstrap_existing_2025():
    year = CFG.teacher_year
    yp = year_paths(year)

    mapping = {
        f"{CFG.existing_2025_root_gcs}/tile_1788_prob_raw_aligned.tif": yp["prob_raw"],
        f"{CFG.existing_2025_root_gcs}/tile_1788_prob_rescaled.tif": yp["prob_rescaled"],
        f"{CFG.existing_2025_root_gcs}/tile_1788_rescaled_mask_07.tif": yp["mask"],
        f"{CFG.existing_2025_root_gcs}/tile_1788_rescaled_polygons_07.gpkg": yp["gpkg"],
        f"{CFG.existing_2025_root_gcs}/tile_1788_rescaled_polygons_07.geojson": yp["geojson"],
    }

    for gcs_path, local_path in mapping.items():
        if not os.path.exists(local_path):
            gs_download_file(gcs_path, local_path)

    if not os.path.exists(yp["summary"]):
        mask_arr, _, _, _, _, _ = read_single_band(yp["mask"])
        prob_arr, _, _, _, _, _ = read_single_band(yp["prob_rescaled"])

        n_polygons = 0
        total_area_ha = None
        if os.path.exists(yp["gpkg"]):
            gdf = gpd.read_file(yp["gpkg"])
            n_polygons = int(len(gdf))
            if "area_ha" in gdf.columns:
                total_area_ha = float(np.nansum(gdf["area_ha"]))

        summary = {
            "year": year,
            "source": "bootstrapped_existing_2025_teacher",
            "prob_raw": yp["prob_raw"],
            "prob_rescaled": yp["prob_rescaled"],
            "mask": yp["mask"],
            "gpkg": yp["gpkg"],
            "geojson": yp["geojson"],
            "n_valid_pixels": int(np.isfinite(prob_arr).sum()),
            "n_cotton_pixels": int((mask_arr == 1).sum()),
            "cotton_fraction": float((mask_arr == 1).sum() / max((mask_arr != 255).sum(), 1)),
            "n_polygons": n_polygons,
            "total_area_ha": total_area_ha,
            "threshold_map": CFG.threshold_map,
            "existing_2025_root_gcs": CFG.existing_2025_root_gcs,
        }

        save_json(yp["summary"], summary)

    package_year_to_bank(year)

    with open(yp["summary"], "r") as f:
        return json.load(f)

# GENERATE YEAR PRODUCT
def generate_year_product(year):
    yp = year_paths(year)

    if os.path.exists(yp["summary"]):
        with open(yp["summary"], "r") as f:
            return json.load(f)

    months = months_for_year(year)
    print(f"Year {year} months:", months)

    aligned_months = {}
    available_months = []

    for month in months:
        key = month_suffix(month)
        raw_path = export_month_safe(year, month, yp["monthly_raw_dir"])
        aligned_path = os.path.join(yp["monthly_aligned_dir"], f"tile1788_{year}_{key}_aligned.tif")
        aligned_path = align_month_to_old_grid(raw_path, aligned_path)

        if aligned_path is not None and os.path.exists(aligned_path):
            with rasterio.open(aligned_path) as src:
                aligned_months[key] = src.read().astype(np.float32)
            available_months.append(month)

    if len(available_months) == 0:
        raise RuntimeError(f"No valid months available for year {year}.")

    valid_mask = np.zeros((old_height, old_width), dtype=bool)
    for key, arr in aligned_months.items():
        valid_mask |= np.isfinite(arr[0])

    n_valid = int(valid_mask.sum())
    print(f"Year {year}: valid pixels = {n_valid}")

    valid_flat = valid_mask.ravel()
    band_order = ["B2", "B3", "B4", "B8", "B11", "B12", "NDVI", "EVI2", "NDMI", "SWIR1_RATIO", "SWIR2_INDEX"]

    monthly_flat = {}
    for month in available_months:
        key = month_suffix(month)
        arr = aligned_months[key]
        for band_idx, band_name in enumerate(band_order):
            monthly_flat[f"{band_name}_{key}"] = arr[band_idx].ravel()[valid_flat]

    probs = np.full(n_valid, np.nan, dtype=np.float32)

    for start in range(0, n_valid, CFG.pred_chunk_size):
        end = min(start + CFG.pred_chunk_size, n_valid)
        chunk_df = pd.DataFrame({k: v[start:end] for k, v in monthly_flat.items()})
        X_chunk = build_features_from_monthly_stack(chunk_df, selected_features, available_months)
        probs[start:end] = pipeline.predict_proba(X_chunk)[:, 1].astype(np.float32)
        print(f"Year {year}: predicted pixels {start} to {end - 1}")

    prob_full = np.full(valid_mask.size, np.nan, dtype=np.float32)
    prob_full[valid_flat] = probs
    prob_full = prob_full.reshape(valid_mask.shape)

    prob_profile = old_profile.copy()
    prob_profile.update(dtype="float32", count=1, nodata=np.nan, compress="lzw")

    with rasterio.open(yp["prob_raw"], "w", **prob_profile) as dst:
        dst.write(prob_full, 1)

    vals = prob_full[np.isfinite(prob_full)]
    p_min = np.quantile(vals, 0.01)
    p_max = np.quantile(vals, 0.99)
    den = max(float(p_max - p_min), 1e-12)

    prob_rescaled = (prob_full - p_min) / den
    prob_rescaled = np.clip(prob_rescaled, 0, 1)
    prob_rescaled[~np.isfinite(prob_full)] = np.nan

    with rasterio.open(yp["prob_rescaled"], "w", **prob_profile) as dst:
        dst.write(prob_rescaled.astype(np.float32), 1)

    binary = np.isfinite(prob_rescaled) & (prob_rescaled >= CFG.threshold_map)
    cleaned = clean_binary_mask(binary)
    out_mask = np.where(np.isfinite(prob_rescaled), cleaned.astype(np.uint8), 255).astype(np.uint8)

    mask_profile = old_profile.copy()
    mask_profile.update(dtype="uint8", count=1, nodata=255, compress="lzw")

    with rasterio.open(yp["mask"], "w", **mask_profile) as dst:
        dst.write(out_mask, 1)

    gdf = vectorize_mask(out_mask, old_transform, old_crs, yp["gpkg"], yp["geojson"])

    summary = {
        "year": year,
        "available_months": available_months,
        "n_valid_pixels": n_valid,
        "raw_min": float(np.nanmin(vals)),
        "raw_max": float(np.nanmax(vals)),
        "rescale_q01": float(p_min),
        "rescale_q99": float(p_max),
        "prob_raw": yp["prob_raw"],
        "prob_rescaled": yp["prob_rescaled"],
        "mask": yp["mask"],
        "gpkg": yp["gpkg"],
        "geojson": yp["geojson"],
        "n_cotton_pixels": int((out_mask == 1).sum()),
        "cotton_fraction": float((out_mask == 1).sum() / max((out_mask != 255).sum(), 1)),
        "n_polygons": 0 if gdf is None else int(len(gdf)),
        "total_area_ha": 0.0 if gdf is None else float(np.nansum(gdf["area_ha"])) if "area_ha" in gdf.columns else None,
        "threshold_map": CFG.threshold_map,
        "model_gcs": CFG.model_gcs,
        "selected_features_gcs": CFG.selected_features_gcs,
    }

    save_json(yp["summary"], summary)
    package_year_to_bank(year)
    return summary


def load_year_prob(year):
    yp = year_paths(year)
    arr, profile, transform, crs, nodata, bounds = read_single_band(yp["prob_rescaled"])
    return arr, profile

def load_year_month_band(year, month, band_index):
    key = month_suffix(month)
    aligned_path = os.path.join(year_paths(year)["monthly_aligned_dir"], f"tile1788_{year}_{key}_aligned.tif")
    if not os.path.exists(aligned_path):
        return np.full((old_height, old_width), np.nan, dtype=np.float32)
    arr, _, _, _ = read_multiband(aligned_path)
    return arr[band_index]

def mean_band_for_months(year, months, band_index):
    arrs = []
    for m in months:
        arr = load_year_month_band(year, m, band_index)
        if np.isfinite(arr).any():
            arrs.append(arr)
    if len(arrs) == 0:
        return np.full((old_height, old_width), np.nan, dtype=np.float32)
    return np.nanmean(np.stack(arrs, axis=0), axis=0)

def build_change_mask(prob_teacher, prob_student, ndvi_teacher, ndvi_student, b11_teacher, b11_student):
    d_ndvi = np.abs(ndvi_student - ndvi_teacher)
    d_b11 = np.abs(b11_student - b11_teacher)
    d_prob = np.abs(prob_student - prob_teacher)

    change = (
        (d_ndvi >= CFG.change_threshold_ndvi) |
        (d_b11 >= CFG.change_threshold_b11) |
        (d_prob >= CFG.change_threshold_prob)
    )

    change[~np.isfinite(prob_teacher)] = False
    change[~np.isfinite(prob_student)] = False

    return change.astype(np.uint8), d_ndvi, d_b11, d_prob

# ------------------------------------------------------------
# COMPONENT L — 2025 -2026 UPDATE
# ------------------------------------------------------------
def cross_year_update_2026():
    teacher, _ = load_year_prob(CFG.teacher_year)
    student, _ = load_year_prob(CFG.prediction_year)

    common_months = months_for_year(CFG.prediction_year)
    print("Change detection months:", common_months)

    # Band order: B2,B3,B4,B8,B11,B12,NDVI,EVI2,NDMI,SWIR1_RATIO,SWIR2_INDEX
    ndvi_idx = 6
    b11_idx = 4

    ndvi_teacher = mean_band_for_months(CFG.teacher_year, common_months, ndvi_idx)
    ndvi_student = mean_band_for_months(CFG.prediction_year, common_months, ndvi_idx)

    b11_teacher = mean_band_for_months(CFG.teacher_year, common_months, b11_idx)
    b11_student = mean_band_for_months(CFG.prediction_year, common_months, b11_idx)

    teacher_conf = ((teacher >= CFG.teacher_conf_high) | (teacher <= CFG.teacher_conf_low))

    change_mask, d_ndvi, d_b11, d_prob = build_change_mask(
        teacher, student, ndvi_teacher, ndvi_student, b11_teacher, b11_student
    )

    student_weight = np.full(teacher.shape, CFG.student_weight_base, dtype=np.float32)
    student_weight[change_mask == 1] = CFG.student_weight_change
    student_weight[~teacher_conf] = np.maximum(student_weight[~teacher_conf], 0.60)

    teacher_weight = 1.0 - student_weight

    updated = teacher_weight * teacher + student_weight * student
    updated[~np.isfinite(teacher) & np.isfinite(student)] = student[~np.isfinite(teacher) & np.isfinite(student)]
    updated[np.isfinite(teacher) & ~np.isfinite(student)] = teacher[np.isfinite(teacher) & ~np.isfinite(student)]
    updated[~np.isfinite(teacher) & ~np.isfinite(student)] = np.nan

    update_root = os.path.join(CFG.local_root, "year_2026_updated")
    os.makedirs(update_root, exist_ok=True)

    prob_updated_tif = os.path.join(update_root, "tile1788_2026_updated_prob.tif")
    change_mask_tif = os.path.join(update_root, "tile1788_2026_change_mask.tif")
    teacher_conf_tif = os.path.join(update_root, "tile1788_2026_teacher_conf.tif")
    updated_mask_tif = os.path.join(update_root, "tile1788_2026_updated_mask_07.tif")
    updated_gpkg = os.path.join(update_root, "tile1788_2026_updated_polygons_07.gpkg")
    updated_geojson = os.path.join(update_root, "tile1788_2026_updated_polygons_07.geojson")
    update_summary_json = os.path.join(update_root, "tile1788_2026_update_summary.json")

    prob_profile = old_profile.copy()
    prob_profile.update(dtype="float32", count=1, nodata=np.nan, compress="lzw")

    with rasterio.open(prob_updated_tif, "w", **prob_profile) as dst:
        dst.write(updated.astype(np.float32), 1)

    mask_profile = old_profile.copy()
    mask_profile.update(dtype="uint8", count=1, nodata=255, compress="lzw")

    with rasterio.open(change_mask_tif, "w", **mask_profile) as dst:
        dst.write(np.where(np.isfinite(updated), change_mask.astype(np.uint8), 255).astype(np.uint8), 1)

    with rasterio.open(teacher_conf_tif, "w", **mask_profile) as dst:
        dst.write(np.where(np.isfinite(updated), teacher_conf.astype(np.uint8), 255).astype(np.uint8), 1)

    binary = np.isfinite(updated) & (updated >= CFG.threshold_map)
    cleaned = clean_binary_mask(binary)
    out_mask = np.where(np.isfinite(updated), cleaned.astype(np.uint8), 255).astype(np.uint8)

    with rasterio.open(updated_mask_tif, "w", **mask_profile) as dst:
        dst.write(out_mask, 1)

    gdf = vectorize_mask(out_mask, old_transform, old_crs, updated_gpkg, updated_geojson)

    summary = {
        "teacher_year": CFG.teacher_year,
        "student_year": CFG.prediction_year,
        "updated_year": CFG.prediction_year,
        "change_detection_months": common_months,
        "updated_prob_tif": prob_updated_tif,
        "change_mask_tif": change_mask_tif,
        "teacher_conf_tif": teacher_conf_tif,
        "updated_mask_tif": updated_mask_tif,
        "updated_gpkg": updated_gpkg,
        "updated_geojson": updated_geojson,
        "n_valid_pixels": int(np.isfinite(updated).sum()),
        "n_change_pixels": int((change_mask == 1).sum()),
        "change_fraction": float((change_mask == 1).sum() / max(np.isfinite(updated).sum(), 1)),
        "updated_cotton_pixels": int((out_mask == 1).sum()),
        "updated_cotton_fraction": float((out_mask == 1).sum() / max((out_mask != 255).sum(), 1)),
        "n_polygons": 0 if gdf is None else int(len(gdf)),
        "total_area_ha": 0.0 if gdf is None else float(np.nansum(gdf["area_ha"])) if "area_ha" in gdf.columns else None,
        "threshold_map": CFG.threshold_map,
        "student_weight_base": CFG.student_weight_base,
        "student_weight_change": CFG.student_weight_change,
        "note": "2026 update uses available in-season months only.",
    }

    save_json(update_summary_json, summary)

    update_prefix = f"{CFG.update_gcs_root}/year=2026"

    uploads = {
        f"{update_prefix}/P_updated.tif": prob_updated_tif,
        f"{update_prefix}/ChangeMask.tif": change_mask_tif,
        f"{update_prefix}/TeacherConf.tif": teacher_conf_tif,
        f"{update_prefix}/M_updated.tif": updated_mask_tif,
        f"{update_prefix}/V_updated.gpkg": updated_gpkg,
        f"{update_prefix}/V_updated.geojson": updated_geojson,
        f"{update_prefix}/metadata.json": update_summary_json,
    }

    for gcs_path, local_path in uploads.items():
        gs_upload_no_overwrite(local_path, gcs_path)

    return summary


year_summaries = {}

print("\n COMPONENT K: BOOTSTRAP 2025 TEACHER ")
year_summaries[CFG.teacher_year] = bootstrap_existing_2025()

for year in CFG.historical_years_to_generate:
    print(f"\n COMPONENT K: GENERATE HISTORICAL YEAR {year} ")
    year_summaries[year] = generate_year_product(year)

print(f"\n COMPONENT K: GENERATE 2026 STUDENT ")
year_summaries[CFG.prediction_year] = generate_year_product(CFG.prediction_year)

print("\n COMPONENT L: 2025 → 2026 UPDATE ")
update_summary = cross_year_update_2026()

final_summary = {
    "run_id_kl": RUN_ID_KL,
    "componentJ_model_gcs": CFG.model_gcs,
    "componentJ_features_gcs": CFG.selected_features_gcs,
    "componentK_product_bank_years": year_summaries,
    "componentL_update_2026": update_summary,
    "product_bank_gcs_root": CFG.bank_gcs_root,
    "update_gcs_root": CFG.update_gcs_root,
}

FINAL_SUMMARY_LOCAL = os.path.join(CFG.local_root, "componentKL_final_summary.json")
save_json(FINAL_SUMMARY_LOCAL, final_summary)

FINAL_SUMMARY_GCS = f"{CFG.update_gcs_root}/componentKL_final_summary.json"
gs_upload_no_overwrite(FINAL_SUMMARY_LOCAL, FINAL_SUMMARY_GCS)

print("\nDONE.")
print("Final summary:")
print(FINAL_SUMMARY_GCS)
print(json.dumps(final_summary, indent=2))


# ============================================================
# COMPONENT M — BOUNDARY EVIDENCE LAYERS
# ============================================================

!pip -q install google-cloud-storage rasterio geopandas shapely scipy matplotlib

import os
import json
import numpy as np
import rasterio
import matplotlib.pyplot as plt

from scipy import ndimage as ndi
from google.cloud import storage


class CFG:
    project_id = "gcp-clag-remote-mapping"

    run_id_kl = "20260504_210425"
    local_root = f"/content/componentKL_Jv8_tile1788/{run_id_kl}"

    year = 2026
    tile = "1788"

    updated_prob_local = f"{local_root}/year_2026_updated/tile1788_2026_updated_prob.tif"
    updated_mask_local = f"{local_root}/year_2026_updated/tile1788_2026_updated_mask_07.tif"
    change_mask_local = f"{local_root}/year_2026_updated/tile1788_2026_change_mask.tif"
    teacher_conf_local = f"{local_root}/year_2026_updated/tile1788_2026_teacher_conf.tif"

    available_months = [3, 4]

    output_gcs_root = (
        "gs://storage_cropmapping/Finals/high_plains_crop_mapping/"
        f"componentM_boundary_evidence_Jv8_tile1788_binary_cotton/{run_id_kl}/year=2026"
    )

    out_dir = f"{local_root}/componentM_boundary_2026"
    os.makedirs(out_dir, exist_ok=True)


    # [B2, B3, B4, B8, B11, B12, NDVI, EVI2, NDMI, SWIR1_RATIO, SWIR2_INDEX]
    band_idx = {
        "B2": 0,
        "B3": 1,
        "B4": 2,
        "B8": 3,
        "B11": 4,
        "B12": 5,
        "NDVI": 6,
        "EVI2": 7,
        "NDMI": 8,
        "SWIR1_RATIO": 9,
        "SWIR2_INDEX": 10,
    }

    w_prob = 0.30
    w_spectral = 0.35
    w_temporal = 0.20
    w_class = 0.15

    gaussian_sigma = 1.0
    boundary_threshold = 0.65

# OUTPUT paths
OUT_PROB_GRAD = f"{CFG.out_dir}/tile1788_2026_prob_gradient.tif"
OUT_SPECTRAL_GRAD = f"{CFG.out_dir}/tile1788_2026_spectral_gradient.tif"
OUT_TEMPORAL_GRAD = f"{CFG.out_dir}/tile1788_2026_temporal_gradient.tif"
OUT_CLASS_BOUNDARY = f"{CFG.out_dir}/tile1788_2026_class_boundary.tif"
OUT_BOUNDARY_SCORE = f"{CFG.out_dir}/tile1788_2026_boundary_score.tif"
OUT_BOUNDARY_BINARY = f"{CFG.out_dir}/tile1788_2026_boundary_binary.tif"
OUT_METADATA = f"{CFG.out_dir}/tile1788_2026_componentM_metadata.json"

# INPUTS

required_inputs = [
    CFG.updated_prob_local,
    CFG.updated_mask_local,
    CFG.change_mask_local,
    CFG.teacher_conf_local,
]

for p in required_inputs:
    if not os.path.exists(p):
        raise FileNotFoundError(f"Missing required K/L output: {p}")

print("Using K/L local root:", CFG.local_root)
print("Updated probability:", CFG.updated_prob_local)
print("Updated mask:", CFG.updated_mask_local)
print("Available months:", CFG.available_months)
print("Output GCS root:", CFG.output_gcs_root)

client = storage.Client(project=CFG.project_id)

def parse_gs_path(gs_path):
    assert gs_path.startswith("gs://")
    bucket = gs_path.replace("gs://", "").split("/", 1)[0]
    blob = gs_path.replace(f"gs://{bucket}/", "")
    return bucket, blob

def upload_no_overwrite(local_path, gs_path):
    bucket, blob = parse_gs_path(gs_path)
    b = client.bucket(bucket).blob(blob)
    if b.exists():
        print("Exists, skip:", gs_path)
        return
    b.upload_from_filename(local_path)
    print("Uploaded:", gs_path)


def read_single(path):
    with rasterio.open(path) as src:
        arr = src.read(1).astype(np.float32)
        profile = src.profile.copy()
        nodata = src.nodata

    if nodata is not None:
        arr[arr == nodata] = np.nan

    return arr, profile

def read_multi(path):
    with rasterio.open(path) as src:
        arr = src.read().astype(np.float32)
        profile = src.profile.copy()
        nodata = src.nodata

    if nodata is not None:
        arr[arr == nodata] = np.nan

    return arr, profile

def write_float(path, arr, profile):
    out_profile = profile.copy()
    out_profile.update(dtype="float32", count=1, nodata=np.nan, compress="lzw")

    with rasterio.open(path, "w", **out_profile) as dst:
        dst.write(arr.astype(np.float32), 1)

def write_uint8(path, arr, profile, nodata=255):
    out_profile = profile.copy()
    out_profile.update(dtype="uint8", count=1, nodata=nodata, compress="lzw")

    with rasterio.open(path, "w", **out_profile) as dst:
        dst.write(arr.astype(np.uint8), 1)

def normalize_01(arr):
    out = arr.copy().astype(np.float32)
    valid = np.isfinite(out)

    if valid.sum() == 0:
        return np.full_like(out, np.nan, dtype=np.float32)

    vals = out[valid]
    lo = np.nanpercentile(vals, 2)
    hi = np.nanpercentile(vals, 98)

    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        out[valid] = 0.0
        out[~valid] = np.nan
        return out.astype(np.float32)

    out = (out - lo) / (hi - lo)
    out = np.clip(out, 0, 1)
    out[~valid] = np.nan

    return out.astype(np.float32)

def gradient_mag(arr, sigma=1.0):
    x = arr.astype(np.float32).copy()
    valid = np.isfinite(x)

    if valid.sum() == 0:
        return np.full_like(x, np.nan, dtype=np.float32)

    fill_val = np.nanmedian(x[valid])
    x[~valid] = fill_val

    if sigma and sigma > 0:
        x = ndi.gaussian_filter(x, sigma=sigma)

    gx = ndi.sobel(x, axis=1)
    gy = ndi.sobel(x, axis=0)
    g = np.sqrt(gx**2 + gy**2).astype(np.float32)
    g[~valid] = np.nan

    return g

def month_suffix(m):
    return f"m{int(m):02d}"

def aligned_month_path(year, month):
    key = month_suffix(month)
    return (
        f"{CFG.local_root}/year_{year}/monthly_aligned/"
        f"tile1788_{year}_{key}_aligned.tif"
    )

# UPDATED PRODUCTS
prob, profile = read_single(CFG.updated_prob_local)
mask, _ = read_single(CFG.updated_mask_local)
change_mask, _ = read_single(CFG.change_mask_local)
teacher_conf, _ = read_single(CFG.teacher_conf_local)

valid = np.isfinite(prob)

print("\nUpdated probability shape:", prob.shape)
print("Valid pixels:", int(valid.sum()))
print("Probability stats:")
print("  min:", float(np.nanmin(prob)))
print("  max:", float(np.nanmax(prob)))
print("  mean:", float(np.nanmean(prob)))

# LOAD MONTHLY ALIGNED RASTERS
monthly = {}

for m in CFG.available_months:
    p = aligned_month_path(CFG.year, m)

    if not os.path.exists(p):
        print(f"Missing month {m}, skip:", p)
        continue

    arr, _ = read_multi(p)
    monthly[m] = arr
    print(f"Loaded {CFG.year}-{month_suffix(m)}:", arr.shape)

if len(monthly) == 0:
    raise RuntimeError("No aligned monthly rasters found for Component M.")

sorted_months = sorted(monthly.keys())

# ------------------------------------------------------------
# 1. PROBABILITY GRADIENT
# ------------------------------------------------------------
prob_grad = normalize_01(gradient_mag(prob, sigma=CFG.gaussian_sigma))

# ------------------------------------------------------------
# 2. SPECTRAL GRADIENT
# ------------------------------------------------------------
def mean_band_over_months(band_name):
    idx = CFG.band_idx[band_name]
    stacks = []

    for m, arr in monthly.items():
        if idx < arr.shape[0]:
            stacks.append(arr[idx])

    if len(stacks) == 0:
        return np.full_like(prob, np.nan, dtype=np.float32)

    return np.nanmean(np.stack(stacks, axis=0), axis=0).astype(np.float32)

spectral_bands = [
    "B8", "B11", "B12",
    "NDVI", "EVI2", "NDMI",
    "SWIR1_RATIO", "SWIR2_INDEX"
]

spectral_terms = []

for band in spectral_bands:
    bmean = mean_band_over_months(band)
    g = normalize_01(gradient_mag(bmean, sigma=CFG.gaussian_sigma))
    spectral_terms.append(g)

spectral_grad = np.nanmean(np.stack(spectral_terms, axis=0), axis=0).astype(np.float32)
spectral_grad = normalize_01(spectral_grad)

# ------------------------------------------------------------
# 3. TEMPORAL GRADIENT
# ------------------------------------------------------------
temporal_terms = []

for m1, m2 in zip(sorted_months[:-1], sorted_months[1:]):
    arr1 = monthly[m1]
    arr2 = monthly[m2]

    for band in ["NDVI", "EVI2", "NDMI", "B8", "B11", "B12"]:
        idx = CFG.band_idx[band]

        if idx < arr1.shape[0] and idx < arr2.shape[0]:
            diff = np.abs(arr2[idx] - arr1[idx]).astype(np.float32)
            temporal_terms.append(normalize_01(diff))

if len(temporal_terms) == 0:
    temporal_grad = np.zeros_like(prob, dtype=np.float32)
    temporal_grad[~valid] = np.nan
else:
    temporal_grad = np.nanmean(np.stack(temporal_terms, axis=0), axis=0).astype(np.float32)
    temporal_grad = normalize_01(temporal_grad)

# ------------------------------------------------------------
# 4. CLASS BOUNDARY
# ------------------------------------------------------------
mask_binary = (mask == 1).astype(np.float32)
mask_binary[~valid] = np.nan

class_boundary = normalize_01(gradient_mag(mask_binary, sigma=0.5))
class_boundary = np.nan_to_num(class_boundary, nan=0.0).astype(np.float32)
class_boundary[~valid] = np.nan

# ------------------------------------------------------------
# 5. FINAL BOUNDARY SCORE
# ------------------------------------------------------------
boundary_score = (
    CFG.w_prob * np.nan_to_num(prob_grad, nan=0.0) +
    CFG.w_spectral * np.nan_to_num(spectral_grad, nan=0.0) +
    CFG.w_temporal * np.nan_to_num(temporal_grad, nan=0.0) +
    CFG.w_class * np.nan_to_num(class_boundary, nan=0.0)
).astype(np.float32)

boundary_score[~valid] = np.nan
boundary_score = normalize_01(boundary_score)

boundary_binary = np.where(
    np.isfinite(boundary_score),
    (boundary_score >= CFG.boundary_threshold).astype(np.uint8),
    255
).astype(np.uint8)

# OUTPUTS
write_float(OUT_PROB_GRAD, prob_grad, profile)
write_float(OUT_SPECTRAL_GRAD, spectral_grad, profile)
write_float(OUT_TEMPORAL_GRAD, temporal_grad, profile)
write_float(OUT_CLASS_BOUNDARY, class_boundary, profile)
write_float(OUT_BOUNDARY_SCORE, boundary_score, profile)
write_uint8(OUT_BOUNDARY_BINARY, boundary_binary, profile)

metadata = {
    "component": "M_boundary_evidence",
    "run_id_kl": CFG.run_id_kl,
    "year": CFG.year,
    "tile": CFG.tile,
    "input_kl_local_root": CFG.local_root,
    "input_updated_probability": CFG.updated_prob_local,
    "input_updated_mask": CFG.updated_mask_local,
    "input_change_mask": CFG.change_mask_local,
    "input_teacher_conf": CFG.teacher_conf_local,
    "available_months_used": sorted_months,
    "warning": (
        "2026 is in-season. May failed in Component K/L, so Component M uses only March-April. "
        "Temporal boundary evidence is diagnostic until later 2026 months are available."
    ),
    "band_indices": CFG.band_idx,
    "weights": {
        "probability_gradient": CFG.w_prob,
        "spectral_gradient": CFG.w_spectral,
        "temporal_gradient": CFG.w_temporal,
        "class_boundary": CFG.w_class,
    },
    "boundary_threshold": CFG.boundary_threshold,
    "outputs_local": {
        "prob_gradient": OUT_PROB_GRAD,
        "spectral_gradient": OUT_SPECTRAL_GRAD,
        "temporal_gradient": OUT_TEMPORAL_GRAD,
        "class_boundary": OUT_CLASS_BOUNDARY,
        "boundary_score": OUT_BOUNDARY_SCORE,
        "boundary_binary": OUT_BOUNDARY_BINARY,
        "metadata": OUT_METADATA,
    },
    "outputs_gcs": {
        "prob_gradient": f"{CFG.output_gcs_root}/prob_gradient.tif",
        "spectral_gradient": f"{CFG.output_gcs_root}/spectral_gradient.tif",
        "temporal_gradient": f"{CFG.output_gcs_root}/temporal_gradient.tif",
        "class_boundary": f"{CFG.output_gcs_root}/class_boundary.tif",
        "boundary_score": f"{CFG.output_gcs_root}/boundary_score.tif",
        "boundary_binary": f"{CFG.output_gcs_root}/boundary_binary.tif",
        "metadata": f"{CFG.output_gcs_root}/metadata.json",
    },
    "stats": {
        "valid_pixels": int(valid.sum()),
        "updated_cotton_pixels": int((mask == 1).sum()),
        "change_pixels": int((change_mask == 1).sum()),
        "teacher_conf_pixels": int((teacher_conf == 1).sum()),
        "boundary_score_min": float(np.nanmin(boundary_score)),
        "boundary_score_max": float(np.nanmax(boundary_score)),
        "boundary_score_mean": float(np.nanmean(boundary_score)),
        "boundary_pixels": int((boundary_binary == 1).sum()),
    }
}

with open(OUT_METADATA, "w") as f:
    json.dump(metadata, f, indent=2)

# SAVE
upload_no_overwrite(OUT_PROB_GRAD, f"{CFG.output_gcs_root}/prob_gradient.tif")
upload_no_overwrite(OUT_SPECTRAL_GRAD, f"{CFG.output_gcs_root}/spectral_gradient.tif")
upload_no_overwrite(OUT_TEMPORAL_GRAD, f"{CFG.output_gcs_root}/temporal_gradient.tif")
upload_no_overwrite(OUT_CLASS_BOUNDARY, f"{CFG.output_gcs_root}/class_boundary.tif")
upload_no_overwrite(OUT_BOUNDARY_SCORE, f"{CFG.output_gcs_root}/boundary_score.tif")
upload_no_overwrite(OUT_BOUNDARY_BINARY, f"{CFG.output_gcs_root}/boundary_binary.tif")
upload_no_overwrite(OUT_METADATA, f"{CFG.output_gcs_root}/metadata.json")

# VISUALIZATION
plt.figure(figsize=(18, 10))

plt.subplot(2, 3, 1)
plt.imshow(prob, cmap="viridis")
plt.title("Updated 2026 probability")
plt.colorbar(fraction=0.046, pad=0.04)
plt.axis("off")

plt.subplot(2, 3, 2)
plt.imshow(prob_grad, cmap="magma", vmin=0, vmax=1)
plt.title("Probability gradient")
plt.colorbar(fraction=0.046, pad=0.04)
plt.axis("off")

plt.subplot(2, 3, 3)
plt.imshow(spectral_grad, cmap="magma", vmin=0, vmax=1)
plt.title("Spectral gradient")
plt.colorbar(fraction=0.046, pad=0.04)
plt.axis("off")

plt.subplot(2, 3, 4)
plt.imshow(temporal_grad, cmap="magma", vmin=0, vmax=1)
plt.title("Temporal gradient, Mar-Apr only")
plt.colorbar(fraction=0.046, pad=0.04)
plt.axis("off")

plt.subplot(2, 3, 5)
plt.imshow(boundary_score, cmap="magma", vmin=0, vmax=1)
plt.title("Final boundary score")
plt.colorbar(fraction=0.046, pad=0.04)
plt.axis("off")

plt.subplot(2, 3, 6)
plt.imshow(np.where(boundary_binary == 255, np.nan, boundary_binary), cmap="gray", vmin=0, vmax=1)
plt.title("Boundary binary")
plt.axis("off")

plt.tight_layout()
plt.show()

print("\nComponent M finished.")
print("GCS output root:")
print(CFG.output_gcs_root)
print(json.dumps(metadata, indent=2))

# ============================================================
# COMPONENT O, Boundary-aware polygon QC/refinement
# ============================================================

!pip -q install geopandas rasterio shapely numpy pandas google-cloud-storage pyogrio fiona matplotlib scipy

import os
import json
import numpy as np
import pandas as pd
import geopandas as gpd
import rasterio
from rasterio.features import rasterize
from scipy import ndimage as ndi
from google.cloud import storage
import matplotlib.pyplot as plt

class CFG:
    project_id = "gcp-clag-remote-mapping"
    run_id_kl = "20260504_210425"

    local_root = f"/content/componentO_lite_Jv8_tile1788/{run_id_kl}"

    output_gcs_root = (
        "gs://storage_cropmapping/Finals/high_plains_crop_mapping/"
        f"componentO_lite_boundary_qc_Jv8_tile1788_binary_cotton/{run_id_kl}/year=2026"
    )

    teacher_polygons_local = (
        f"/content/componentKL_Jv8_tile1788/{run_id_kl}/"
        "year_2025/outputs/tile1788_2025_polygons_07.gpkg"
    )

    updated_prob_local = (
        f"/content/componentKL_Jv8_tile1788/{run_id_kl}/"
        "year_2026_updated/tile1788_2026_updated_prob.tif"
    )

    updated_mask_local = (
        f"/content/componentKL_Jv8_tile1788/{run_id_kl}/"
        "year_2026_updated/tile1788_2026_updated_mask_07.tif"
    )

    # Component M boundary evidence local outputs
    boundary_score_local = (
        f"/content/componentKL_Jv8_tile1788/{run_id_kl}/"
        "componentM_boundary_2026/tile1788_2026_boundary_score.tif"
    )

    boundary_binary_local = (
        f"/content/componentKL_Jv8_tile1788/{run_id_kl}/"
        "componentM_boundary_2026/tile1788_2026_boundary_binary.tif"
    )

    change_mask_local = (
        f"/content/componentKL_Jv8_tile1788/{run_id_kl}/"
        "year_2026_updated/tile1788_2026_change_mask.tif"
    )

    teacher_conf_local = (
        f"/content/componentKL_Jv8_tile1788/{run_id_kl}/"
        "year_2026_updated/tile1788_2026_teacher_conf.tif"
    )

    # QC thresholds
    min_area_ha = 1.0
    review_area_ha = 3.0
    min_mean_prob_keep = 0.10
    min_mean_boundary_keep = 0.15
    min_max_prob_review = 0.30
    strong_boundary_threshold = 0.65

    boundary_buffer_pixels = 2

os.makedirs(CFG.local_root, exist_ok=True)

OUT_GPKG = os.path.join(CFG.local_root, "tile1788_2026_Olite_refined_polygons.gpkg")
OUT_GEOJSON = os.path.join(CFG.local_root, "tile1788_2026_Olite_refined_polygons.geojson")
OUT_QC_CSV = os.path.join(CFG.local_root, "tile1788_2026_Olite_qc_table.csv")
OUT_KEEP_GPKG = os.path.join(CFG.local_root, "tile1788_2026_Olite_keep_review_polygons.gpkg")
OUT_METADATA = os.path.join(CFG.local_root, "tile1788_2026_Olite_metadata.json")

# INPUTS
for p in [
    CFG.teacher_polygons_local,
    CFG.updated_prob_local,
    CFG.updated_mask_local,
    CFG.boundary_score_local,
    CFG.boundary_binary_local,
    CFG.change_mask_local,
    CFG.teacher_conf_local,
]:
    if not os.path.exists(p):
        raise FileNotFoundError(f"Missing input: {p}")

print("Component O-lite inputs ready.")
print("Teacher polygons:", CFG.teacher_polygons_local)
print("Updated prob:", CFG.updated_prob_local)
print("Boundary score:", CFG.boundary_score_local)
print("Output GCS root:", CFG.output_gcs_root)


client = storage.Client(project=CFG.project_id)

def parse_gs_path(gs_path):
    bucket = gs_path.replace("gs://", "").split("/", 1)[0]
    blob = gs_path.replace(f"gs://{bucket}/", "")
    return bucket, blob

def upload_file_to_gcs(local_path, gs_path):
    bucket_name, blob_name = parse_gs_path(gs_path)
    blob = client.bucket(bucket_name).blob(blob_name)
    blob.upload_from_filename(local_path)
    print(f"Uploaded: {gs_path}")


def read_raster(path):
    with rasterio.open(path) as src:
        arr = src.read(1).astype(np.float32)
        profile = src.profile.copy()
        transform = src.transform
        crs = src.crs
        nodata = src.nodata

    if nodata is not None:
        arr[arr == nodata] = np.nan

    return arr, profile, transform, crs

def rasterize_geom(geom, out_shape, transform):
    return rasterize(
        [(geom, 1)],
        out_shape=out_shape,
        transform=transform,
        fill=0,
        dtype="uint8"
    ).astype(bool)

def boundary_ring_mask(poly_mask, pixels=2):
    eroded = ndi.binary_erosion(poly_mask, iterations=pixels)
    ring = poly_mask & (~eroded)
    if ring.sum() == 0:
        ring = poly_mask
    return ring

def safe_nanmean(x):
    vals = x[np.isfinite(x)]
    return np.nan if len(vals) == 0 else float(np.nanmean(vals))

def safe_nanmax(x):
    vals = x[np.isfinite(x)]
    return np.nan if len(vals) == 0 else float(np.nanmax(vals))

def safe_frac_true(x):
    vals = x[np.isfinite(x)]
    if len(vals) == 0:
        return np.nan
    return float(np.nanmean(vals == 1))

# INPUTS
gdf = gpd.read_file(CFG.teacher_polygons_local)

prob, prob_profile, transform, raster_crs = read_raster(CFG.updated_prob_local)
updated_mask, _, _, _ = read_raster(CFG.updated_mask_local)
boundary_score, _, _, _ = read_raster(CFG.boundary_score_local)
boundary_binary, _, _, _ = read_raster(CFG.boundary_binary_local)
change_mask, _, _, _ = read_raster(CFG.change_mask_local)
teacher_conf, _, _, _ = read_raster(CFG.teacher_conf_local)

if gdf.crs != raster_crs:
    gdf = gdf.to_crs(raster_crs)

print("\nLoaded teacher polygons:", len(gdf))
print("Raster shape:", prob.shape)
print("Raster CRS:", raster_crs)

# GEOMETRY METRICS
try:
    utm_crs = gdf.estimate_utm_crs()
    gdf_area = gdf.to_crs(utm_crs)
    gdf["area_m2"] = gdf_area.area.values
    gdf["area_ha"] = gdf["area_m2"] / 10000.0
    gdf["perimeter_m"] = gdf_area.length.values
    gdf["compactness"] = (4 * np.pi * gdf["area_m2"]) / np.maximum(gdf["perimeter_m"] ** 2, 1e-9)
except Exception:
    gdf["area_m2"] = np.nan
    gdf["area_ha"] = np.nan
    gdf["perimeter_m"] = np.nan
    gdf["compactness"] = np.nan

# POLYGON QC METRICS
rows = []
out_shape = prob.shape

for idx, row in gdf.iterrows():
    geom = row.geometry

    if geom is None or geom.is_empty:
        rows.append({
            "poly_index": idx,
            "qc_flag": "remove",
            "qc_reason": "empty_geometry"
        })
        continue

    poly_mask = rasterize_geom(geom, out_shape, transform)

    if poly_mask.sum() == 0:
        rows.append({
            "poly_index": idx,
            "qc_flag": "remove",
            "qc_reason": "no_raster_overlap"
        })
        continue

    ring = boundary_ring_mask(poly_mask, pixels=CFG.boundary_buffer_pixels)

    inside_prob = prob[poly_mask]
    inside_boundary = boundary_score[poly_mask]
    ring_boundary = boundary_score[ring]
    ring_binary = boundary_binary[ring]
    inside_change = change_mask[poly_mask]
    inside_teacher_conf = teacher_conf[poly_mask]
    inside_updated_mask = updated_mask[poly_mask]

    mean_prob = safe_nanmean(inside_prob)
    max_prob = safe_nanmax(inside_prob)
    mean_boundary_inside = safe_nanmean(inside_boundary)
    mean_boundary_ring = safe_nanmean(ring_boundary)
    strong_boundary_frac = safe_frac_true(ring_binary)
    change_frac = safe_frac_true(inside_change)
    teacher_conf_frac = safe_frac_true(inside_teacher_conf)
    updated_mask_frac = safe_frac_true(inside_updated_mask)

    area_ha = float(row.get("area_ha", np.nan))
    compactness = float(row.get("compactness", np.nan))

    reasons = []

    if not np.isfinite(area_ha) or area_ha < CFG.min_area_ha:
        reasons.append("too_small")

    if np.isfinite(area_ha) and area_ha < CFG.review_area_ha:
        reasons.append("small_review")

    if np.isfinite(mean_prob) and mean_prob < CFG.min_mean_prob_keep:
        reasons.append("low_mean_updated_prob")

    if np.isfinite(max_prob) and max_prob < CFG.min_max_prob_review:
        reasons.append("low_max_updated_prob")

    if np.isfinite(mean_boundary_ring) and mean_boundary_ring < CFG.min_mean_boundary_keep:
        reasons.append("weak_boundary_alignment")

    if np.isfinite(compactness) and compactness < 0.05:
        reasons.append("sliver_shape")

    if np.isfinite(change_frac) and change_frac > 0.50:
        reasons.append("high_change_area")

    # Decision rule
    if "too_small" in reasons or "sliver_shape" in reasons or "low_max_updated_prob" in reasons:
        qc_flag = "remove"
    elif (
        "low_mean_updated_prob" in reasons
        or "weak_boundary_alignment" in reasons
        or "high_change_area" in reasons
        or "small_review" in reasons
    ):
        qc_flag = "review"
    else:
        qc_flag = "keep"

    rows.append({
        "poly_index": idx,
        "area_ha": area_ha,
        "compactness": compactness,
        "mean_updated_prob": mean_prob,
        "max_updated_prob": max_prob,
        "mean_boundary_inside": mean_boundary_inside,
        "mean_boundary_ring": mean_boundary_ring,
        "strong_boundary_frac_ring": strong_boundary_frac,
        "change_frac_inside": change_frac,
        "teacher_conf_frac_inside": teacher_conf_frac,
        "updated_mask_frac_inside": updated_mask_frac,
        "n_pixels_inside": int(poly_mask.sum()),
        "n_pixels_boundary_ring": int(ring.sum()),
        "qc_flag": qc_flag,
        "qc_reason": ";".join(reasons) if reasons else "ok"
    })

qc = pd.DataFrame(rows)

# MERGE QC BACK TO POLYGONS
gdf = gdf.reset_index(drop=True)

qc_join = qc.drop(
    columns=["area_ha", "compactness"],
    errors="ignore"
).set_index("poly_index")

gdf = gdf.join(qc_join, how="left")

# Geometry repair
gdf["geometry"] = gdf.geometry.buffer(0)

gdf["component"] = "O_lite_boundary_qc"
gdf["source_year"] = 2025
gdf["target_year"] = 2026
gdf["source_type"] = "2025_teacher_polygon_qc_with_2026_Jv8_boundary_evidence"
gdf["run_id_kl"] = CFG.run_id_kl

gdf_keep_review = gdf[gdf["qc_flag"].isin(["keep", "review"])].copy()


# SAVE
gdf.to_file(OUT_GPKG, driver="GPKG")
gdf.to_file(OUT_GEOJSON, driver="GeoJSON")
gdf_keep_review.to_file(OUT_KEEP_GPKG, driver="GPKG")
qc.to_csv(OUT_QC_CSV, index=False)

metadata = {
    "component": "O_lite_boundary_aware_polygon_qc",
    "run_id_kl": CFG.run_id_kl,
    "tile": "1788",
    "source_year": 2025,
    "target_year": 2026,
    "input_teacher_polygons": CFG.teacher_polygons_local,
    "input_updated_probability": CFG.updated_prob_local,
    "input_updated_mask": CFG.updated_mask_local,
    "input_boundary_score": CFG.boundary_score_local,
    "input_boundary_binary": CFG.boundary_binary_local,
    "input_change_mask": CFG.change_mask_local,
    "input_teacher_conf": CFG.teacher_conf_local,
    "note": (
        "Uses 2025 teacher polygons as candidate parcels and evaluates them with "
        "2026 updated probability, Component M boundary score, change mask, and teacher confidence."
    ),
    "thresholds": {
        "min_area_ha": CFG.min_area_ha,
        "review_area_ha": CFG.review_area_ha,
        "min_mean_prob_keep": CFG.min_mean_prob_keep,
        "min_mean_boundary_keep": CFG.min_mean_boundary_keep,
        "min_max_prob_review": CFG.min_max_prob_review,
        "strong_boundary_threshold": CFG.strong_boundary_threshold,
        "boundary_buffer_pixels": CFG.boundary_buffer_pixels
    },
    "summary": {
        "n_input_polygons": int(len(gdf)),
        "n_keep": int((gdf["qc_flag"] == "keep").sum()),
        "n_review": int((gdf["qc_flag"] == "review").sum()),
        "n_remove": int((gdf["qc_flag"] == "remove").sum()),
        "n_keep_review": int(len(gdf_keep_review)),
        "mean_area_ha": float(np.nanmean(gdf["area_ha"])),
        "mean_updated_prob": float(np.nanmean(gdf["mean_updated_prob"])),
        "mean_boundary_ring": float(np.nanmean(gdf["mean_boundary_ring"])),
        "mean_change_frac_inside": float(np.nanmean(gdf["change_frac_inside"])),
    },
    "outputs_local": {
        "all_polygons_gpkg": OUT_GPKG,
        "all_polygons_geojson": OUT_GEOJSON,
        "keep_review_gpkg": OUT_KEEP_GPKG,
        "qc_csv": OUT_QC_CSV,
        "metadata": OUT_METADATA
    },
    "outputs_gcs": {
        "all_polygons_gpkg": f"{CFG.output_gcs_root}/V_2026_Olite_all_polygons_qc.gpkg",
        "all_polygons_geojson": f"{CFG.output_gcs_root}/V_2026_Olite_all_polygons_qc.geojson",
        "keep_review_gpkg": f"{CFG.output_gcs_root}/V_2026_Olite_keep_review_polygons.gpkg",
        "qc_csv": f"{CFG.output_gcs_root}/O_lite_qc_table.csv",
        "metadata": f"{CFG.output_gcs_root}/metadata.json"
    }
}

with open(OUT_METADATA, "w") as f:
    json.dump(metadata, f, indent=2)

# SAVE
upload_file_to_gcs(OUT_GPKG, f"{CFG.output_gcs_root}/V_2026_Olite_all_polygons_qc.gpkg")
upload_file_to_gcs(OUT_GEOJSON, f"{CFG.output_gcs_root}/V_2026_Olite_all_polygons_qc.geojson")
upload_file_to_gcs(OUT_KEEP_GPKG, f"{CFG.output_gcs_root}/V_2026_Olite_keep_review_polygons.gpkg")
upload_file_to_gcs(OUT_QC_CSV, f"{CFG.output_gcs_root}/O_lite_qc_table.csv")
upload_file_to_gcs(OUT_METADATA, f"{CFG.output_gcs_root}/metadata.json")

# SUMMARY + PLOTS
print("\nComponent O-lite finished.")
print("\nQC counts:")
print(gdf["qc_flag"].value_counts(dropna=False))

print("\nGCS output root:")
print(CFG.output_gcs_root)

print("\nMetadata:")
print(json.dumps(metadata, indent=2))

plt.figure(figsize=(8, 6))
gdf.plot(column="qc_flag", legend=True, figsize=(8, 6))
plt.title("Component O-lite QC flags")
plt.axis("off")
plt.show()

plt.figure(figsize=(7, 4))
gdf["mean_boundary_ring"].hist(bins=30)
plt.title("Boundary alignment score along polygon boundary")
plt.xlabel("mean boundary score on polygon ring")
plt.ylabel("polygon count")
plt.show()

plt.figure(figsize=(7, 4))
gdf["mean_updated_prob"].hist(bins=30)
plt.title("Mean updated 2026 probability inside polygons")
plt.xlabel("mean updated probability")
plt.ylabel("polygon count")
plt.show()

plt.figure(figsize=(7, 4))
gdf["change_frac_inside"].hist(bins=30)
plt.title("Change fraction inside teacher polygons")
plt.xlabel("change fraction")
plt.ylabel("polygon count")
plt.show()


# ============================================================
# COMPONENT P — FINAL PROVISIONAL PRODUCTS PACKAGE + TIMELY MAPS
#   Tile = 1788
#   Year = 2026
# ============================================================

!pip -q install geopandas pandas numpy google-cloud-storage pyogrio fiona shapely rasterio scipy matplotlib geemap earthengine-api

import os
import json
import shutil
import time
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import geopandas as gpd
import rasterio
from rasterio.warp import reproject, Resampling, transform_bounds
from rasterio.features import shapes
from shapely.geometry import shape
from scipy import ndimage as ndi
from google.cloud import storage

import ee
import geemap



class CFG:
    project_id = "gcp-clag-remote-mapping"
    run_id_kl = "20260504_210425"

    year = 2026
    tile = "1788"

    local_root = f"/content/componentP_final_products_Jv8_tile1788/{run_id_kl}"
    timely_local_root = f"/content/componentP_timely_maps_Jv8_tile1788/{run_id_kl}"

    output_gcs_root = (
        "gs://storage_cropmapping/Finals/high_plains_crop_mapping/"
        f"componentP_final_products_Jv8_tile1788_binary_cotton/{run_id_kl}/year=2026"
    )

    timely_gcs_root = (
        "gs://storage_cropmapping/Finals/high_plains_crop_mapping/"
        f"componentP_timely_maps_Jv8_tile1788_binary_cotton/{run_id_kl}/year=2026"
    )

    # Component O-lite inputs
    olite_gpkg_gcs = (
        "gs://storage_cropmapping/Finals/high_plains_crop_mapping/"
        f"componentO_lite_boundary_qc_Jv8_tile1788_binary_cotton/{run_id_kl}/year=2026/"
        "V_2026_Olite_all_polygons_qc.gpkg"
    )

    olite_qc_csv_gcs = (
        "gs://storage_cropmapping/Finals/high_plains_crop_mapping/"
        f"componentO_lite_boundary_qc_Jv8_tile1788_binary_cotton/{run_id_kl}/year=2026/"
        "O_lite_qc_table.csv"
    )

    olite_metadata_gcs = (
        "gs://storage_cropmapping/Finals/high_plains_crop_mapping/"
        f"componentO_lite_boundary_qc_Jv8_tile1788_binary_cotton/{run_id_kl}/year=2026/"
        "metadata.json"
    )

    olite_local_dir = f"/content/componentO_lite_boundary_qc_Jv8_tile1788_binary_cotton/{run_id_kl}/year_2026"

    olite_gpkg = f"{olite_local_dir}/V_2026_Olite_all_polygons_qc.gpkg"
    olite_qc_csv = f"{olite_local_dir}/O_lite_qc_table.csv"
    olite_metadata = f"{olite_local_dir}/metadata.json"

    # Component L inputs
    updated_prob_gcs = (
        "gs://storage_cropmapping/Finals/high_plains_crop_mapping/"
        f"componentL_update_Jv8_tile1788_2025_to_2026/{run_id_kl}/year=2026/"
        "P_updated.tif"
    )

    updated_mask_gcs = (
        "gs://storage_cropmapping/Finals/high_plains_crop_mapping/"
        f"componentL_update_Jv8_tile1788_2025_to_2026/{run_id_kl}/year=2026/"
        "M_updated.tif"
    )

    change_mask_gcs = (
        "gs://storage_cropmapping/Finals/high_plains_crop_mapping/"
        f"componentL_update_Jv8_tile1788_2025_to_2026/{run_id_kl}/year=2026/"
        "ChangeMask.tif"
    )

    teacher_conf_gcs = (
        "gs://storage_cropmapping/Finals/high_plains_crop_mapping/"
        f"componentL_update_Jv8_tile1788_2025_to_2026/{run_id_kl}/year=2026/"
        "TeacherConf.tif"
    )

    # Component M inputs
    boundary_score_gcs = (
        "gs://storage_cropmapping/Finals/high_plains_crop_mapping/"
        f"componentM_boundary_evidence_Jv8_tile1788_binary_cotton/{run_id_kl}/year=2026/"
        "boundary_score.tif"
    )

    boundary_binary_gcs = (
        "gs://storage_cropmapping/Finals/high_plains_crop_mapping/"
        f"componentM_boundary_evidence_Jv8_tile1788_binary_cotton/{run_id_kl}/year=2026/"
        "boundary_binary.tif"
    )

    spectral_gradient_gcs = (
        "gs://storage_cropmapping/Finals/high_plains_crop_mapping/"
        f"componentM_boundary_evidence_Jv8_tile1788_binary_cotton/{run_id_kl}/year=2026/"
        "spectral_gradient.tif"
    )

    temporal_gradient_gcs = (
        "gs://storage_cropmapping/Finals/high_plains_crop_mapping/"
        f"componentM_boundary_evidence_Jv8_tile1788_binary_cotton/{run_id_kl}/year=2026/"
        "temporal_gradient.tif"
    )

    prob_gradient_gcs = (
        "gs://storage_cropmapping/Finals/high_plains_crop_mapping/"
        f"componentM_boundary_evidence_Jv8_tile1788_binary_cotton/{run_id_kl}/year=2026/"
        "prob_gradient.tif"
    )

    # Component P
    updated_prob = (
        f"/content/componentKL_Jv8_tile1788/{run_id_kl}/"
        "year_2026_updated/tile1788_2026_updated_prob.tif"
    )

    updated_mask = (
        f"/content/componentKL_Jv8_tile1788/{run_id_kl}/"
        "year_2026_updated/tile1788_2026_updated_mask_07.tif"
    )

    change_mask = (
        f"/content/componentKL_Jv8_tile1788/{run_id_kl}/"
        "year_2026_updated/tile1788_2026_change_mask.tif"
    )

    teacher_conf = (
        f"/content/componentKL_Jv8_tile1788/{run_id_kl}/"
        "year_2026_updated/tile1788_2026_teacher_conf.tif"
    )

    boundary_score = (
        f"/content/componentKL_Jv8_tile1788/{run_id_kl}/"
        "componentM_boundary_2026/tile1788_2026_boundary_score.tif"
    )

    boundary_binary = (
        f"/content/componentKL_Jv8_tile1788/{run_id_kl}/"
        "componentM_boundary_2026/tile1788_2026_boundary_binary.tif"
    )

    spectral_gradient = (
        f"/content/componentKL_Jv8_tile1788/{run_id_kl}/"
        "componentM_boundary_2026/tile1788_2026_spectral_gradient.tif"
    )

    temporal_gradient = (
        f"/content/componentKL_Jv8_tile1788/{run_id_kl}/"
        "componentM_boundary_2026/tile1788_2026_temporal_gradient.tif"
    )

    prob_gradient = (
        f"/content/componentKL_Jv8_tile1788/{run_id_kl}/"
        "componentM_boundary_2026/tile1788_2026_prob_gradient.tif"
    )

    # Timely-map inputs
    old_tile_gcs = "gs://storage_cropmapping/componentJ_outputs/texas_tiles/componentJ_texas_tile_1788_class.tif"

    teacher_prob_2025_gcs = (
        "gs://storage_cropmapping/Finals/high_plains_crop_mapping/"
        f"componentK_product_bank_Jv8_tile1788_binary_cotton/{run_id_kl}/year=2025/P_rescaled.tif"
    )

    ee_tmp_gcs_root = (
        "gs://storage_cropmapping/Finals/high_plains_crop_mapping/"
        f"_tmp_componentP_EE_exports_Jv8_tile1788/{run_id_kl}/year=2026"
    )

    start_date_2026 = "2026-03-01"
    end_date_2026 = "2026-04-30"
    window_days = 14

    scale_m = 10

    threshold_asof = 0.50
    teacher_cotton_thr = 0.70
    change_thr = 0.55
    strong_veg_thr = 0.60
    boundary_threshold = 0.65

    min_patch_pixels = 20
    fill_holes_max_pixels = 50
    connectivity = 2

    ee_export_timeout_sec = 60 * 60
    ee_export_poll_sec = 20


os.makedirs(CFG.local_root, exist_ok=True)
os.makedirs(CFG.timely_local_root, exist_ok=True)
os.makedirs(CFG.olite_local_dir, exist_ok=True)



client = storage.Client(project=CFG.project_id)

def parse_gs_path(gs_path):
    bucket = gs_path.replace("gs://", "").split("/", 1)[0]
    blob = gs_path.replace(f"gs://{bucket}/", "")
    return bucket, blob

def gcs_file_exists(gs_path):
    bucket, blob = parse_gs_path(gs_path)
    return client.bucket(bucket).blob(blob).exists()

def upload_file_to_gcs(local_path, gs_path):
    bucket, blob = parse_gs_path(gs_path)
    client.bucket(bucket).blob(blob).upload_from_filename(local_path)
    print("Uploaded:", gs_path)

def download_gcs(gs_path, local_path, overwrite=False):
    if os.path.exists(local_path) and not overwrite:
        print("Exists locally:", local_path)
        return local_path

    if not gcs_file_exists(gs_path):
        raise FileNotFoundError(f"GCS file not found: {gs_path}")

    bucket, blob = parse_gs_path(gs_path)
    os.makedirs(os.path.dirname(local_path), exist_ok=True)
    client.bucket(bucket).blob(blob).download_to_filename(local_path)

    print("Downloaded:", gs_path)
    print("      to:", local_path)
    return local_path

def safe_copy(src, dst):
    if os.path.exists(src):
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        return dst
    print("Missing, skipped:", src)
    return None

def save_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(obj, f, indent=2)

def list_gcs_blobs_with_prefix(gs_prefix):
    bucket_name, blob_prefix = parse_gs_path(gs_prefix)
    return list(client.bucket(bucket_name).list_blobs(prefix=blob_prefix))

# OTHER INPUTS

print("\nDownloading Component O-lite inputs...")
download_gcs(CFG.olite_gpkg_gcs, CFG.olite_gpkg)
download_gcs(CFG.olite_qc_csv_gcs, CFG.olite_qc_csv)
download_gcs(CFG.olite_metadata_gcs, CFG.olite_metadata)

print("\nDownloading Component L inputs...")
download_gcs(CFG.updated_prob_gcs, CFG.updated_prob)
download_gcs(CFG.updated_mask_gcs, CFG.updated_mask)
download_gcs(CFG.change_mask_gcs, CFG.change_mask)
download_gcs(CFG.teacher_conf_gcs, CFG.teacher_conf)

print("\nDownloading Component M inputs...")
download_gcs(CFG.boundary_score_gcs, CFG.boundary_score)
download_gcs(CFG.boundary_binary_gcs, CFG.boundary_binary)
download_gcs(CFG.spectral_gradient_gcs, CFG.spectral_gradient)
download_gcs(CFG.temporal_gradient_gcs, CFG.temporal_gradient)
download_gcs(CFG.prob_gradient_gcs, CFG.prob_gradient)

required_local_inputs = [
    CFG.olite_gpkg,
    CFG.olite_qc_csv,
    CFG.olite_metadata,
    CFG.updated_prob,
    CFG.updated_mask,
    CFG.change_mask,
    CFG.teacher_conf,
    CFG.boundary_score,
    CFG.boundary_binary,
    CFG.spectral_gradient,
    CFG.temporal_gradient,
    CFG.prob_gradient,
]

missing = [p for p in required_local_inputs if not os.path.exists(p)]

if missing:
    print("\nMissing local files:")
    for p in missing:
        print(" -", p)
    raise FileNotFoundError("Some required files are still missing.")
else:
    print("\nAll required Component O/L/M inputs are ready.")

# PART 1: PACKAGE FINAL PROVISIONAL PRODUCTS
OUT_CANDIDATE_GPKG = os.path.join(CFG.local_root, "tile1788_2026_candidate_parcels_keep_review.gpkg")
OUT_CANDIDATE_GEOJSON = os.path.join(CFG.local_root, "tile1788_2026_candidate_parcels_keep_review.geojson")

OUT_CONSERVATIVE_GPKG = os.path.join(CFG.local_root, "tile1788_2026_conservative_parcels_keep_only.gpkg")
OUT_CONSERVATIVE_GEOJSON = os.path.join(CFG.local_root, "tile1788_2026_conservative_parcels_keep_only.geojson")

OUT_REMOVED_GPKG = os.path.join(CFG.local_root, "tile1788_2026_removed_parcels.gpkg")
OUT_REVIEW_CSV = os.path.join(CFG.local_root, "tile1788_2026_manual_review_table.csv")
OUT_METADATA = os.path.join(CFG.local_root, "tile1788_2026_final_product_metadata.json")

RASTER_OUT_DIR = os.path.join(CFG.local_root, "rasters")
os.makedirs(RASTER_OUT_DIR, exist_ok=True)

gdf = gpd.read_file(CFG.olite_gpkg)

print("\nLoaded O-lite polygons:", len(gdf))
print(gdf["qc_flag"].value_counts(dropna=False))

candidate = gdf[gdf["qc_flag"].isin(["keep", "review"])].copy()
conservative = gdf[gdf["qc_flag"] == "keep"].copy()
removed = gdf[gdf["qc_flag"] == "remove"].copy()

for layer_name, layer in [
    ("candidate_keep_review", candidate),
    ("conservative_keep_only", conservative),
    ("removed", removed),
]:
    if len(layer) > 0:
        layer["final_product_year"] = CFG.year
        layer["tile_id"] = CFG.tile
        layer["crop_label"] = "cotton_candidate"
        layer["product_type"] = layer_name
        layer["provisional_status"] = "diagnostic_2026_march_april_only"
        layer["run_id_kl"] = CFG.run_id_kl

candidate.to_file(OUT_CANDIDATE_GPKG, driver="GPKG")
candidate.to_file(OUT_CANDIDATE_GEOJSON, driver="GeoJSON")

conservative.to_file(OUT_CONSERVATIVE_GPKG, driver="GPKG")
conservative.to_file(OUT_CONSERVATIVE_GEOJSON, driver="GeoJSON")

if len(removed) > 0:
    removed.to_file(OUT_REMOVED_GPKG, driver="GPKG")

review_cols = [
    "poly_id", "qc_flag", "qc_reason", "area_ha", "compactness",
    "mean_updated_prob", "max_updated_prob",
    "mean_boundary_inside", "mean_boundary_ring",
    "strong_boundary_frac_ring", "change_frac_inside",
    "teacher_conf_frac_inside", "updated_mask_frac_inside"
]
review_cols = [c for c in review_cols if c in gdf.columns]

review_table = gdf[gdf["qc_flag"].isin(["review", "remove"])][review_cols].copy()
review_table.to_csv(OUT_REVIEW_CSV, index=False)

raster_outputs = {}

raster_sources = {
    "P_updated_2026": CFG.updated_prob,
    "M_updated_2026": CFG.updated_mask,
    "ChangeMask_2026": CFG.change_mask,
    "TeacherConf_2026": CFG.teacher_conf,
    "BoundaryScore_2026": CFG.boundary_score,
    "BoundaryBinary_2026": CFG.boundary_binary,
    "SpectralGradient_2026": CFG.spectral_gradient,
    "TemporalGradient_2026": CFG.temporal_gradient,
    "ProbabilityGradient_2026": CFG.prob_gradient,
}

for name, src in raster_sources.items():
    dst = os.path.join(RASTER_OUT_DIR, f"{name}.tif")
    copied = safe_copy(src, dst)
    if copied:
        raster_outputs[name] = copied

def summarize_layer(layer):
    if len(layer) == 0:
        return {
            "n_polygons": 0,
            "total_area_ha": 0.0,
            "mean_area_ha": None,
            "mean_updated_prob": None,
            "mean_boundary_ring": None,
        }

    return {
        "n_polygons": int(len(layer)),
        "total_area_ha": float(np.nansum(layer["area_ha"])) if "area_ha" in layer.columns else None,
        "mean_area_ha": float(np.nanmean(layer["area_ha"])) if "area_ha" in layer.columns else None,
        "mean_updated_prob": float(np.nanmean(layer["mean_updated_prob"])) if "mean_updated_prob" in layer.columns else None,
        "mean_boundary_ring": float(np.nanmean(layer["mean_boundary_ring"])) if "mean_boundary_ring" in layer.columns else None,
    }

metadata = {
    "component": "P_final_products",
    "run_id_kl": CFG.run_id_kl,
    "tile": CFG.tile,
    "year": CFG.year,
    "status": "provisional_diagnostic",
    "important_note": (
        "2026 product is provisional because current run uses March-April imagery only. "
        "Candidate layer includes keep + review polygons. Conservative layer includes keep only."
    ),
    "inputs": {
        "olite_gpkg": CFG.olite_gpkg_gcs,
        "olite_qc_csv": CFG.olite_qc_csv_gcs,
        "olite_metadata": CFG.olite_metadata_gcs,
        "updated_probability": CFG.updated_prob_gcs,
        "updated_mask": CFG.updated_mask_gcs,
        "change_mask": CFG.change_mask_gcs,
        "teacher_conf": CFG.teacher_conf_gcs,
        "boundary_score": CFG.boundary_score_gcs,
        "boundary_binary": CFG.boundary_binary_gcs,
        "spectral_gradient": CFG.spectral_gradient_gcs,
        "temporal_gradient": CFG.temporal_gradient_gcs,
        "probability_gradient": CFG.prob_gradient_gcs,
    },
    "summary": {
        "all_olite": summarize_layer(gdf),
        "candidate_keep_review": summarize_layer(candidate),
        "conservative_keep_only": summarize_layer(conservative),
        "removed": summarize_layer(removed),
        "qc_counts": gdf["qc_flag"].value_counts(dropna=False).to_dict(),
    },
    "outputs_local": {
        "candidate_gpkg": OUT_CANDIDATE_GPKG,
        "candidate_geojson": OUT_CANDIDATE_GEOJSON,
        "conservative_gpkg": OUT_CONSERVATIVE_GPKG,
        "conservative_geojson": OUT_CONSERVATIVE_GEOJSON,
        "removed_gpkg": OUT_REMOVED_GPKG if os.path.exists(OUT_REMOVED_GPKG) else None,
        "manual_review_csv": OUT_REVIEW_CSV,
        "rasters": raster_outputs,
        "metadata": OUT_METADATA,
    }
}

save_json(OUT_METADATA, metadata)

upload_file_to_gcs(OUT_CANDIDATE_GPKG, f"{CFG.output_gcs_root}/vectors/tile1788_2026_candidate_parcels_keep_review.gpkg")
upload_file_to_gcs(OUT_CANDIDATE_GEOJSON, f"{CFG.output_gcs_root}/vectors/tile1788_2026_candidate_parcels_keep_review.geojson")

upload_file_to_gcs(OUT_CONSERVATIVE_GPKG, f"{CFG.output_gcs_root}/vectors/tile1788_2026_conservative_parcels_keep_only.gpkg")
upload_file_to_gcs(OUT_CONSERVATIVE_GEOJSON, f"{CFG.output_gcs_root}/vectors/tile1788_2026_conservative_parcels_keep_only.geojson")

if os.path.exists(OUT_REMOVED_GPKG):
    upload_file_to_gcs(OUT_REMOVED_GPKG, f"{CFG.output_gcs_root}/vectors/tile1788_2026_removed_parcels.gpkg")

upload_file_to_gcs(OUT_REVIEW_CSV, f"{CFG.output_gcs_root}/tables/tile1788_2026_manual_review_table.csv")
upload_file_to_gcs(OUT_METADATA, f"{CFG.output_gcs_root}/metadata/tile1788_2026_final_product_metadata.json")

for name, local_path in raster_outputs.items():
    upload_file_to_gcs(local_path, f"{CFG.output_gcs_root}/rasters/{name}.tif")

print("\nComponent P final package finished.")
print("GCS root:", CFG.output_gcs_root)
print(json.dumps(metadata, indent=2))

# PART 2: TIMELY BIWEEKLY 10-m CHANGE MAPS
LOCAL_OLD_TILE = os.path.join(CFG.timely_local_root, "old_tile_1788_class.tif")
LOCAL_TEACHER_2025 = os.path.join(CFG.timely_local_root, "teacher_2025_P_rescaled.tif")

def read_single(path):
    with rasterio.open(path) as src:
        arr = src.read(1).astype(np.float32)
        profile = src.profile.copy()
        transform = src.transform
        crs = src.crs
        nodata = src.nodata
        bounds = src.bounds
    if nodata is not None:
        arr[arr == nodata] = np.nan
    return arr, profile, transform, crs, bounds

def read_multi(path):
    with rasterio.open(path) as src:
        arr = src.read().astype(np.float32)
        profile = src.profile.copy()
        nodata = src.nodata
    if nodata is not None:
        arr[arr == nodata] = np.nan
    return arr, profile

def write_float(path, arr, profile):
    out_profile = profile.copy()
    out_profile.update(dtype="float32", count=1, nodata=np.nan, compress="lzw")
    with rasterio.open(path, "w", **out_profile) as dst:
        dst.write(arr.astype(np.float32), 1)

def write_uint8(path, arr, profile, nodata=0):
    out_profile = profile.copy()
    out_profile.update(dtype="uint8", count=1, nodata=nodata, compress="lzw")
    with rasterio.open(path, "w", **out_profile) as dst:
        dst.write(arr.astype(np.uint8), 1)

def normalize_01(arr):
    out = arr.astype(np.float32).copy()
    valid = np.isfinite(out)

    if valid.sum() == 0:
        return np.full_like(out, np.nan, dtype=np.float32)

    vals = out[valid]
    lo = np.nanpercentile(vals, 2)
    hi = np.nanpercentile(vals, 98)

    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        out[valid] = 0
        out[~valid] = np.nan
        return out.astype(np.float32)

    out = (out - lo) / (hi - lo)
    out = np.clip(out, 0, 1)
    out[~valid] = np.nan
    return out.astype(np.float32)

def gradient_mag(arr, sigma=1.0):
    x = arr.astype(np.float32).copy()
    valid = np.isfinite(x)

    if valid.sum() == 0:
        return np.full_like(x, np.nan, dtype=np.float32)

    fill = np.nanmedian(x[valid])
    x[~valid] = fill

    if sigma > 0:
        x = ndi.gaussian_filter(x, sigma=sigma)

    gx = ndi.sobel(x, axis=1)
    gy = ndi.sobel(x, axis=0)
    g = np.sqrt(gx**2 + gy**2)
    g[~valid] = np.nan
    return g.astype(np.float32)

def clean_binary(binary):
    structure = ndi.generate_binary_structure(2, CFG.connectivity)

    labeled, _ = ndi.label(binary, structure=structure)
    sizes = np.bincount(labeled.ravel())

    keep = sizes >= CFG.min_patch_pixels
    keep[0] = False
    cleaned = keep[labeled]

    holes = ~cleaned
    hole_labels, _ = ndi.label(holes, structure=structure)
    hole_sizes = np.bincount(hole_labels.ravel())

    fill = np.zeros_like(cleaned, dtype=bool)
    for i in range(1, len(hole_sizes)):
        if hole_sizes[i] <= CFG.fill_holes_max_pixels:
            fill |= hole_labels == i

    return cleaned | fill

def vectorize_mask(mask_arr, transform, crs, out_gpkg, out_geojson):
    binary = (mask_arr == 1).astype(np.uint8)

    geoms, vals = [], []
    for geom, val in shapes(binary, mask=(binary == 1), transform=transform):
        if val == 1:
            geoms.append(shape(geom))
            vals.append(1)

    if len(geoms) == 0:
        return None

    gdf_out = gpd.GeoDataFrame({"class_id": vals}, geometry=geoms, crs=crs)
    gdf_out["dissolve_key"] = 1
    gdf_out = gdf_out.dissolve(by="dissolve_key").explode(index_parts=False).reset_index(drop=True)
    gdf_out["poly_id"] = np.arange(1, len(gdf_out) + 1)

    try:
        gdf_proj = gdf_out.to_crs(gdf_out.estimate_utm_crs())
        gdf_out["area_m2"] = gdf_proj.area.values
        gdf_out["area_ha"] = gdf_out["area_m2"] / 10000.0
    except Exception:
        gdf_out["area_m2"] = np.nan
        gdf_out["area_ha"] = np.nan

    gdf_out.to_file(out_gpkg, driver="GPKG")
    gdf_out.to_file(out_geojson, driver="GeoJSON")
    return gdf_out

def make_windows(start_date, end_date, window_days):
    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")

    windows = []
    cur = start
    while cur <= end:
        nxt = min(cur + timedelta(days=window_days - 1), end)
        windows.append((cur.strftime("%Y-%m-%d"), nxt.strftime("%Y-%m-%d")))
        cur = nxt + timedelta(days=1)

    return windows

def previous_year_window(start_date, end_date):
    s = datetime.strptime(start_date, "%Y-%m-%d")
    e = datetime.strptime(end_date, "%Y-%m-%d")
    return s.replace(year=s.year - 1).strftime("%Y-%m-%d"), e.replace(year=e.year - 1).strftime("%Y-%m-%d")

try:
    ee.Initialize(project=CFG.project_id)
    print("Earth Engine initialized:", CFG.project_id)
except Exception:
    ee.Authenticate()
    ee.Initialize(project=CFG.project_id)
    print("Earth Engine initialized:", CFG.project_id)

download_gcs(CFG.old_tile_gcs, LOCAL_OLD_TILE)
download_gcs(CFG.teacher_prob_2025_gcs, LOCAL_TEACHER_2025)

old_arr, old_profile, old_transform, old_crs, old_bounds = read_single(LOCAL_OLD_TILE)

if str(old_crs) != "EPSG:4326":
    bounds_4326 = transform_bounds(
        old_crs, "EPSG:4326",
        old_bounds.left, old_bounds.bottom,
        old_bounds.right, old_bounds.top,
        densify_pts=21
    )
else:
    bounds_4326 = (old_bounds.left, old_bounds.bottom, old_bounds.right, old_bounds.top)

region_ee = ee.Geometry.Rectangle(list(bounds_4326), proj="EPSG:4326", geodesic=False)

def mask_s2_sr(img):
    scl = img.select("SCL")
    mask = (
        scl.neq(3)
        .And(scl.neq(8))
        .And(scl.neq(9))
        .And(scl.neq(10))
        .And(scl.neq(11))
    )
    return img.updateMask(mask)

def build_s2_window_image(start_date, end_date, region):
    end_exclusive = (datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")

    col = (
        ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
        .filterDate(start_date, end_exclusive)
        .filterBounds(region)
        .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 70))
        .map(mask_s2_sr)
    )

    n = col.size().getInfo()

    if n == 0:
        return None, 0

    img = (
        col.median()
        .select(["B2", "B3", "B4", "B8", "B11", "B12"])
        .divide(10000)
        .toFloat()
    )

    red = img.select("B4")
    nir = img.select("B8")
    b11 = img.select("B11")

    ndvi = nir.subtract(red).divide(nir.add(red)).rename("NDVI")
    evi2 = nir.subtract(red).multiply(2.5).divide(
        nir.add(red.multiply(2.4)).add(1.0)
    ).rename("EVI2")
    ndmi = nir.subtract(b11).divide(nir.add(b11)).rename("NDMI")

    out = ee.Image.cat([img, ndvi, evi2, ndmi]).toFloat()
    return out.rename(["B2", "B3", "B4", "B8", "B11", "B12", "NDVI", "EVI2", "NDMI"]), n

def wait_for_ee_task(task, timeout_sec=3600, poll_sec=20):
    start_time = time.time()

    while True:
        status = task.status()
        state = status.get("state")

        if state == "COMPLETED":
            return status

        if state in ["FAILED", "CANCELLED"]:
            raise RuntimeError(f"EE task ended with state={state}: {status}")

        if time.time() - start_time > timeout_sec:
            raise TimeoutError(f"EE export timed out: {status}")

        print("EE task state:", state)
        time.sleep(poll_sec)

def export_s2_window(start_date, end_date, out_tif):
    if os.path.exists(out_tif):
        return out_tif, "exists", None

    img, n_images = build_s2_window_image(start_date, end_date, region_ee)

    if img is None:
        return None, "no_images", n_images

    tag = f"{start_date.replace('-', '')}_{end_date.replace('-', '')}"
    base_name = os.path.splitext(os.path.basename(out_tif))[0]

    gcs_prefix = f"{CFG.ee_tmp_gcs_root}/{base_name}_{tag}"
    bucket_name, blob_prefix = parse_gs_path(gcs_prefix)

    existing = list_gcs_blobs_with_prefix(gcs_prefix)
    tif_blobs = [b for b in existing if b.name.endswith(".tif")]

    if len(tif_blobs) == 0:
        task = ee.batch.Export.image.toCloudStorage(
            image=img.clip(region_ee),
            description=f"componentP_{base_name}_{tag}",
            bucket=bucket_name,
            fileNamePrefix=blob_prefix,
            region=region_ee,
            scale=CFG.scale_m,
            crs="EPSG:4326",
            maxPixels=1e13,
            fileFormat="GeoTIFF",
            formatOptions={"cloudOptimized": True}
        )

        print("Starting EE batch export:", gcs_prefix)
        task.start()

        try:
            wait_for_ee_task(
                task,
                timeout_sec=CFG.ee_export_timeout_sec,
                poll_sec=CFG.ee_export_poll_sec
            )
        except Exception as e:
            return None, f"failed: {str(e)}", n_images

        existing = list_gcs_blobs_with_prefix(gcs_prefix)
        tif_blobs = [b for b in existing if b.name.endswith(".tif")]

    if len(tif_blobs) == 0:
        return None, "failed: no tif found after EE export", n_images

    tif_blob = sorted(tif_blobs, key=lambda b: b.name)[0]
    exported_gs = f"gs://{bucket_name}/{tif_blob.name}"

    print("Downloading EE export:", exported_gs)
    download_gcs(exported_gs, out_tif)

    if os.path.exists(out_tif):
        return out_tif, "exported_via_gcs_batch", n_images

    return None, "failed: local tif missing after download", n_images

def align_to_reference_grid(src_path, ref_path, out_path, resampling=Resampling.bilinear):
    if src_path is None:
        return None

    if os.path.exists(out_path):
        return out_path

    with rasterio.open(ref_path) as ref:
        ref_profile = ref.profile.copy()
        ref_transform = ref.transform
        ref_crs = ref.crs
        ref_height = ref.height
        ref_width = ref.width

    with rasterio.open(src_path) as src:
        src_arr = src.read().astype(np.float32)
        dst_arr = np.full((src_arr.shape[0], ref_height, ref_width), np.nan, dtype=np.float32)

        for b in range(src_arr.shape[0]):
            reproject(
                source=src_arr[b],
                destination=dst_arr[b],
                src_transform=src.transform,
                src_crs=src.crs,
                src_nodata=src.nodata,
                dst_transform=ref_transform,
                dst_crs=ref_crs,
                dst_nodata=np.nan,
                resampling=resampling
            )

    out_profile = ref_profile.copy()
    out_profile.update(dtype="float32", count=src_arr.shape[0], nodata=np.nan, compress="lzw")

    with rasterio.open(out_path, "w", **out_profile) as dst:
        dst.write(dst_arr)

    return out_path

def align_teacher_to_10m(ref_10m_path, out_path):
    if os.path.exists(out_path):
        return out_path

    with rasterio.open(ref_10m_path) as ref:
        ref_profile = ref.profile.copy()
        ref_transform = ref.transform
        ref_crs = ref.crs
        ref_height = ref.height
        ref_width = ref.width

    dst_arr = np.full((ref_height, ref_width), np.nan, dtype=np.float32)

    with rasterio.open(LOCAL_TEACHER_2025) as src:
        src_arr = src.read(1).astype(np.float32)

        reproject(
            source=src_arr,
            destination=dst_arr,
            src_transform=src.transform,
            src_crs=src.crs,
            src_nodata=src.nodata,
            dst_transform=ref_transform,
            dst_crs=ref_crs,
            dst_nodata=np.nan,
            resampling=Resampling.bilinear
        )

    out_profile = ref_profile.copy()
    out_profile.update(dtype="float32", count=1, nodata=np.nan, compress="lzw")

    with rasterio.open(out_path, "w", **out_profile) as dst:
        dst.write(dst_arr, 1)

    return out_path

def process_window(start_2026, end_2026):
    tag = f"{start_2026.replace('-', '')}_{end_2026.replace('-', '')}"
    local_dir = os.path.join(CFG.timely_local_root, f"window_{tag}")
    os.makedirs(local_dir, exist_ok=True)

    metadata_path = os.path.join(local_dir, "metadata.json")
    gcs_prefix = f"{CFG.timely_gcs_root}/window={tag}"

    if os.path.exists(metadata_path):
        with open(metadata_path, "r") as f:
            old_meta = json.load(f)

        if old_meta.get("status") == "done":
            print(f"Skipping {tag}: already done locally")
            return old_meta
        else:
            print(f"Reprocessing {tag}: previous status was {old_meta.get('status')}")

    start_2025, end_2025 = previous_year_window(start_2026, end_2026)

    raw_2026 = os.path.join(local_dir, f"S2_2026_{tag}_10m.tif")
    raw_2025 = os.path.join(local_dir, f"S2_2025_ref_{tag}_10m.tif")

    cur_path, cur_status, cur_n = export_s2_window(start_2026, end_2026, raw_2026)
    ref_path, ref_status, ref_n = export_s2_window(start_2025, end_2025, raw_2025)

    if cur_path is None or ref_path is None:
        metadata = {
            "window": tag,
            "status": "skipped_missing_imagery",
            "start_2026": start_2026,
            "end_2026": end_2026,
            "start_2025": start_2025,
            "end_2025": end_2025,
            "cur_status": cur_status,
            "ref_status": ref_status,
            "cur_n_images": cur_n,
            "ref_n_images": ref_n,
        }
        save_json(metadata_path, metadata)
        upload_file_to_gcs(metadata_path, f"{gcs_prefix}/metadata.json")
        return metadata

    aligned_2025 = os.path.join(local_dir, f"S2_2025_ref_{tag}_aligned_to_2026_10m.tif")
    align_to_reference_grid(ref_path, cur_path, aligned_2025)

    teacher_10m_path = os.path.join(local_dir, "TeacherCotton_2025_10m.tif")
    align_teacher_to_10m(cur_path, teacher_10m_path)

    cur, cur_profile = read_multi(cur_path)
    ref, _ = read_multi(aligned_2025)
    teacher10, _, _, _, _ = read_single(teacher_10m_path)

    B8_26, B11_26, B12_26, NDVI_26, EVI2_26, NDMI_26 = cur[3], cur[4], cur[5], cur[6], cur[7], cur[8]
    B8_25, B11_25, B12_25, NDVI_25, EVI2_25, NDMI_25 = ref[3], ref[4], ref[5], ref[6], ref[7], ref[8]

    valid = (
        np.isfinite(NDVI_26) &
        np.isfinite(NDVI_25) &
        np.isfinite(EVI2_26) &
        np.isfinite(EVI2_25) &
        np.isfinite(teacher10)
    )

    ndvi_diff = NDVI_26 - NDVI_25
    evi2_diff = EVI2_26 - EVI2_25
    ndmi_diff = NDMI_26 - NDMI_25

    d_ndvi = normalize_01(np.abs(ndvi_diff))
    d_evi2 = normalize_01(np.abs(evi2_diff))
    d_ndmi = normalize_01(np.abs(ndmi_diff))
    d_b8 = normalize_01(np.abs(B8_26 - B8_25))
    d_b11 = normalize_01(np.abs(B11_26 - B11_25))
    d_b12 = normalize_01(np.abs(B12_26 - B12_25))

    change_score = np.nanmean(
        np.stack([d_ndvi, d_evi2, d_ndmi, d_b8, d_b11, d_b12], axis=0),
        axis=0
    )
    change_score = normalize_01(change_score)
    change_score[~valid] = np.nan

    veg_2026 = normalize_01(np.nanmean(np.stack([NDVI_26, EVI2_26], axis=0), axis=0))

    teacher_cotton = teacher10 >= CFG.teacher_cotton_thr
    high_change = change_score >= CFG.change_thr
    strong_veg_2026 = veg_2026 >= CFG.strong_veg_thr

    change_class = np.zeros(change_score.shape, dtype=np.uint8)
    change_class[valid] = 1
    change_class[valid & teacher_cotton & (~high_change)] = 2
    change_class[valid & teacher_cotton & high_change] = 3
    change_class[valid & (~teacher_cotton) & high_change & strong_veg_2026] = 4

    loss_clean = clean_binary(change_class == 3)
    gain_clean = clean_binary(change_class == 4)

    change_class[(change_class == 3) & (~loss_clean)] = 1
    change_class[(change_class == 4) & (~gain_clean)] = 1

    p_asof = teacher10 * (1.0 - 0.70 * change_score)
    p_asof = np.clip(p_asof, 0, 1)
    p_asof[~valid] = np.nan

    m_asof = np.where(
        np.isfinite(p_asof),
        clean_binary(p_asof >= CFG.threshold_asof).astype(np.uint8),
        255
    ).astype(np.uint8)

    g_prob = normalize_01(gradient_mag(p_asof))

    g_spectral = np.nanmean(
        np.stack([
            normalize_01(gradient_mag(B8_26)),
            normalize_01(gradient_mag(B11_26)),
            normalize_01(gradient_mag(B12_26)),
            normalize_01(gradient_mag(NDVI_26)),
            normalize_01(gradient_mag(EVI2_26)),
            normalize_01(gradient_mag(NDMI_26)),
        ], axis=0),
        axis=0
    )

    g_change = normalize_01(gradient_mag(change_score))

    boundary_score = normalize_01(
        0.35 * np.nan_to_num(g_spectral, nan=0) +
        0.35 * np.nan_to_num(g_prob, nan=0) +
        0.30 * np.nan_to_num(g_change, nan=0)
    )
    boundary_score[~valid] = np.nan

    boundary_binary = np.where(
        np.isfinite(boundary_score),
        (boundary_score >= CFG.boundary_threshold).astype(np.uint8),
        255
    ).astype(np.uint8)

    p_asof_tif = os.path.join(local_dir, "P_asof_10m.tif")
    m_asof_tif = os.path.join(local_dir, "M_asof_10m.tif")
    change_class_tif = os.path.join(local_dir, "CottonChangeClass_10m.tif")
    change_score_tif = os.path.join(local_dir, "ChangeScore_10m.tif")
    ndvi_diff_tif = os.path.join(local_dir, "NDVI_2026_minus_2025_10m.tif")
    evi2_diff_tif = os.path.join(local_dir, "EVI2_2026_minus_2025_10m.tif")
    ndmi_diff_tif = os.path.join(local_dir, "NDMI_2026_minus_2025_10m.tif")
    veg_2026_tif = os.path.join(local_dir, "VegSignal_2026_10m.tif")
    boundary_score_tif = os.path.join(local_dir, "BoundaryScore_10m.tif")
    boundary_binary_tif = os.path.join(local_dir, "BoundaryBinary_10m.tif")

    write_float(p_asof_tif, p_asof, cur_profile)
    write_uint8(m_asof_tif, m_asof, cur_profile, nodata=255)
    write_uint8(change_class_tif, change_class, cur_profile, nodata=0)
    write_float(change_score_tif, change_score, cur_profile)
    write_float(ndvi_diff_tif, ndvi_diff, cur_profile)
    write_float(evi2_diff_tif, evi2_diff, cur_profile)
    write_float(ndmi_diff_tif, ndmi_diff, cur_profile)
    write_float(veg_2026_tif, veg_2026, cur_profile)
    write_float(boundary_score_tif, boundary_score, cur_profile)
    write_uint8(boundary_binary_tif, boundary_binary, cur_profile, nodata=255)

    with rasterio.open(cur_path) as src_ref:
        src_transform = src_ref.transform
        src_crs = src_ref.crs

    gpkg = os.path.join(local_dir, "V_asof_10m.gpkg")
    geojson = os.path.join(local_dir, "V_asof_10m.geojson")
    gdf_asof = vectorize_mask(m_asof, src_transform, src_crs, gpkg, geojson)

    stats = {
        "nodata": int((change_class == 0).sum()),
        "stable_non_cotton": int((change_class == 1).sum()),
        "stable_cotton": int((change_class == 2).sum()),
        "possible_cotton_loss": int((change_class == 3).sum()),
        "possible_cotton_gain": int((change_class == 4).sum()),
    }

    metadata = {
        "component": "P_timely_biweekly_10m_cotton_change_map",
        "run_id_kl": CFG.run_id_kl,
        "tile": CFG.tile,
        "resolution_m": CFG.scale_m,
        "window": tag,
        "start_2026": start_2026,
        "end_2026": end_2026,
        "reference_start_2025": start_2025,
        "reference_end_2025": end_2025,
        "current_n_images": cur_n,
        "reference_n_images": ref_n,
        "cur_export_status": cur_status,
        "ref_export_status": ref_status,
        "status": "done",
        "interpretation": "as-of monitoring/change product, not final annual crop map",
        "class_legend": {
            "0": "nodata",
            "1": "stable_non_cotton_or_unchanged",
            "2": "stable_cotton",
            "3": "possible_cotton_loss_or_changed_from_2025_cotton",
            "4": "possible_cotton_gain_or_new_cotton_candidate"
        },
        "thresholds": {
            "threshold_asof": CFG.threshold_asof,
            "teacher_cotton_thr": CFG.teacher_cotton_thr,
            "change_thr": CFG.change_thr,
            "strong_veg_thr": CFG.strong_veg_thr,
            "boundary_threshold": CFG.boundary_threshold,
        },
        "stats": stats,
        "valid_pixels": int(valid.sum()),
        "asof_cotton_pixels": int((m_asof == 1).sum()),
        "asof_cotton_fraction": float((m_asof == 1).sum() / max((m_asof != 255).sum(), 1)),
        "mean_change_score": float(np.nanmean(change_score)),
        "n_asof_polygons": 0 if gdf_asof is None else int(len(gdf_asof)),
        "outputs": {
            "P_asof_10m": p_asof_tif,
            "M_asof_10m": m_asof_tif,
            "CottonChangeClass_10m": change_class_tif,
            "ChangeScore_10m": change_score_tif,
            "NDVI_2026_minus_2025_10m": ndvi_diff_tif,
            "EVI2_2026_minus_2025_10m": evi2_diff_tif,
            "NDMI_2026_minus_2025_10m": ndmi_diff_tif,
            "TeacherCotton_2025_10m": teacher_10m_path,
            "VegSignal_2026_10m": veg_2026_tif,
            "BoundaryScore_10m": boundary_score_tif,
            "BoundaryBinary_10m": boundary_binary_tif,
            "V_asof_gpkg": gpkg if os.path.exists(gpkg) else None,
            "V_asof_geojson": geojson if os.path.exists(geojson) else None,
        }
    }

    save_json(metadata_path, metadata)

    for local_file, remote_name in [
        (p_asof_tif, "P_asof_10m.tif"),
        (m_asof_tif, "M_asof_10m.tif"),
        (change_class_tif, "CottonChangeClass_10m.tif"),
        (change_score_tif, "ChangeScore_10m.tif"),
        (ndvi_diff_tif, "NDVI_2026_minus_2025_10m.tif"),
        (evi2_diff_tif, "EVI2_2026_minus_2025_10m.tif"),
        (ndmi_diff_tif, "NDMI_2026_minus_2025_10m.tif"),
        (teacher_10m_path, "TeacherCotton_2025_10m.tif"),
        (veg_2026_tif, "VegSignal_2026_10m.tif"),
        (boundary_score_tif, "BoundaryScore_10m.tif"),
        (boundary_binary_tif, "BoundaryBinary_10m.tif"),
        (metadata_path, "metadata.json"),
    ]:
        upload_file_to_gcs(local_file, f"{gcs_prefix}/{remote_name}")

    if os.path.exists(gpkg):
        upload_file_to_gcs(gpkg, f"{gcs_prefix}/V_asof_10m.gpkg")
    if os.path.exists(geojson):
        upload_file_to_gcs(geojson, f"{gcs_prefix}/V_asof_10m.geojson")

    return metadata

# TIMELY WINDOWS
windows = make_windows(CFG.start_date_2026, CFG.end_date_2026, CFG.window_days)

print("\nTimely biweekly windows:")
for w in windows:
    print(w)

summaries = []

for start_date, end_date in windows:
    print(f"\n===== Processing {start_date} to {end_date} =====")
    summaries.append(process_window(start_date, end_date))

summary_csv = os.path.join(CFG.timely_local_root, "biweekly_10m_change_summary.csv")
pd.DataFrame(summaries).to_csv(summary_csv, index=False)

upload_file_to_gcs(summary_csv, f"{CFG.timely_gcs_root}/biweekly_10m_change_summary.csv")

print("\nComponent P timely 10-m products finished.")
print("Timely GCS root:", CFG.timely_gcs_root)
print("Summary CSV:", summary_csv)


# Component P: GEE Animated GIF
!pip -q install earthengine-api geemap

import ee
import geemap
from IPython.display import display, Image


try:
    ee.Initialize(project="gcp-clag-remote-mapping")
except:
    ee.Authenticate()
    ee.Initialize(project="gcp-clag-remote-mapping")

RUN_ID = "20260504_210425"

root = (
    "gs://storage_cropmapping/Finals/high_plains_crop_mapping/"
    "componentP_timely_maps_Jv8_tile1788_binary_cotton/"
    f"{RUN_ID}/year=2026/"
)

windows = [
    "20260301_20260314",
    "20260315_20260328",
    "20260329_20260411",
    "20260412_20260425"
]

# VISUALIZATION
changeVis = {
    "min": 0,
    "max": 4,
    "palette": [
        "000000",  # 0 nodata
        "d9d9d9",  # 1 stable non-cotton
        "2ca25f",  # 2 stable cotton
        "de2d26",  # 3 loss
        "3182bd"   # 4 gain
    ]
}

# BUILD IMAGE COLLECTION
images = []

for w in windows:
    path = root + f"window={w}/CottonChangeClass_10m.tif"

    img = ee.Image.loadGeoTIFF(path)

    date = ee.Date.parse("YYYYMMdd", w[:8])

    vis = img.visualize(**changeVis).set({
        "label": w,
        "system:time_start": date.millis()
    })

    images.append(vis)

collection = ee.ImageCollection(images)

# REGION
first_raw = ee.Image.loadGeoTIFF(
    root + f"window={windows[0]}/CottonChangeClass_10m.tif"
)

region = first_raw.geometry()

# FIRST FRAME (MAP)
Map = geemap.Map()
Map.centerObject(region, 12)
Map.addLayer(collection.first(), {}, "First Frame")
display(Map)

# GIF PARAMETERS
gif_params = {
    "region": region,
    "dimensions": 900,
    "framesPerSecond": 1,
    "crs": "EPSG:3857"
}

# CREATE GIF
gif_url = collection.getVideoThumbURL(gif_params)

print("\n GIF URL:")
print(gif_url)

# DISPLAY GIF INLINE
display(Image(url=gif_url))


# ============================================================
# COMPONENT P vs CDL EVALUATION
# Pixel-level confusion matrix
# ============================================================

!pip -q install earthengine-api geemap pandas

import ee
import geemap
import pandas as pd

try:
    ee.Initialize(project="gcp-clag-remote-mapping")
except:
    ee.Authenticate()
    ee.Initialize(project="gcp-clag-remote-mapping")


RUN_ID = "20260504_210425"

ROOT = (
    "gs://storage_cropmapping/Finals/high_plains_crop_mapping/"
    "componentP_timely_maps_Jv8_tile1788_binary_cotton/"
    f"{RUN_ID}/year=2026/"
)

WINDOW = "20260412_20260425"

CDL_YEAR = 2025
CDL_COTTON = 2

PRED_PATH = ROOT + f"window={WINDOW}/M_asof_10m.tif"

# LOAD MAPS
m_asof = ee.Image.loadGeoTIFF(PRED_PATH)
pred = m_asof.eq(1).rename("pred").unmask(0).toByte()

cdl = ee.Image(f"USDA/NASS/CDL/{CDL_YEAR}").select("cropland")
cdl_cotton = cdl.eq(CDL_COTTON).rename("cdl").unmask(0).toByte()

region = m_asof.geometry()

# CONFUSION MAP
# 0 = true non-cotton, predicted non-cotton = TN
# 1 = true non-cotton, predicted cotton     = FP
# 2 = true cotton, predicted non-cotton     = FN
# 3 = true cotton, predicted cotton         = TP
conf_code = cdl_cotton.multiply(2).add(pred).rename("conf")

hist = conf_code.reduceRegion(
    reducer=ee.Reducer.frequencyHistogram(),
    geometry=region,
    scale=10,
    maxPixels=1e13,
    tileScale=8
).get("conf")

hist_dict = ee.Dictionary(hist).getInfo()

TN = int(hist_dict.get("0", 0))
FP = int(hist_dict.get("1", 0))
FN = int(hist_dict.get("2", 0))
TP = int(hist_dict.get("3", 0))

total = TN + FP + FN + TP

precision = TP / (TP + FP + 1e-9)
recall = TP / (TP + FN + 1e-9)
f1 = 2 * precision * recall / (precision + recall + 1e-9)
accuracy = (TP + TN) / max(total, 1)
iou = TP / (TP + FP + FN + 1e-9)

print("\nConfusion Matrix:")
print(pd.DataFrame(
    [[TN, FP], [FN, TP]],
    index=["CDL non-cotton", "CDL cotton"],
    columns=["Pred non-cotton", "Pred cotton"]
))

print("\n--- Metrics ---")
print(f"Total pixels: {total:,}")
print(f"TN: {TN:,}")
print(f"FP: {FP:,}")
print(f"FN: {FN:,}")
print(f"TP: {TP:,}")
print(f"Precision: {precision:.4f}")
print(f"Recall:    {recall:.4f}")
print(f"F1-score:  {f1:.4f}")
print(f"Accuracy:  {accuracy:.4f}")
print(f"IoU:       {iou:.4f}")

# MAP VISUALIZATION
# Difference classes:
# 0 TN = gray
# 1 FP = blue
# 2 FN = red
# 3 TP = green

Map = geemap.Map()
Map.centerObject(region, 12)

Map.addLayer(
    pred,
    {"min": 0, "max": 1, "palette": ["ffffff", "3182bd"]},
    "Your Component P Cotton"
)

Map.addLayer(
    cdl_cotton,
    {"min": 0, "max": 1, "palette": ["ffffff", "2ca25f"]},
    f"CDL {CDL_YEAR} Cotton"
)

Map.addLayer(
    conf_code,
    {
        "min": 0,
        "max": 3,
        "palette": [
            "d9d9d9",  # TN
            "3182bd",  # FP
            "de2d26",  # FN
            "2ca25f"   # TP
        ]
    },
    "Agreement Map: TN/FP/FN/TP"
)

Map
