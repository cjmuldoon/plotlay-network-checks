"""Download City of Gold Coast water and stormwater assets for Burleigh Heads as GeoJSON (WGS 84), the extract the
showcase used: every feature that touches the envelope 153.425, -28.100 to 153.460, -28.075.
Source: City of Gold Coast, licensed under CC BY 3.0 AU (water service connections CC BY 4.0).
The services are re-synced nightly from the City's asset system, so a later download can differ a little.
python3 download_gold_coast.py [out_dir] [--count]   (--count: the number of features per layer only)"""
import json, os, sys, urllib.parse, urllib.request

BASE = 'https://services.arcgis.com/3vStCH7NDoBOZ5zn/arcgis/rest/services'
ENVELOPE = '153.425,-28.100,153.460,-28.075'
LAYERS = {
    'water_mains': 'Potable_Water_Pipe/FeatureServer/0',
    'water_valves': 'Water_Valve/FeatureServer/0',
    'water_hydrants': 'Water_Hydrant/FeatureServer/0',
    'water_fittings': 'Water_Fitting/FeatureServer/0',
    'water_service_connections': 'Water_Service_Connection/FeatureServer/1',
    'stormwater_pipes': 'Drainage_Pipe/FeatureServer/1',
    'stormwater_pits': 'Drainage_Pit/FeatureServer/0',
    'stormwater_end_structures': 'Stormwater_End_Structure/FeatureServer/0',
}
PAGE = 2000


def query(layer, **extra):
    q = {'where': '1=1', 'geometry': ENVELOPE, 'geometryType': 'esriGeometryEnvelope', 'inSR': 4326,
         'spatialRel': 'esriSpatialRelIntersects', 'outSR': 4326, **extra}
    url = f'{BASE}/{layer}/query?{urllib.parse.urlencode(q)}'
    req = urllib.request.Request(url, headers={'User-Agent': 'network-checks-download/1.0'})
    with urllib.request.urlopen(req, timeout=120) as r: return json.load(r)


def download(layer):
    feats, offset = [], 0
    for _ in range(200):   # at most 400,000 features
        page = query(layer, outFields='*', f='geojson', orderByFields='OBJECTID', resultOffset=offset, resultRecordCount=PAGE)
        got = page.get('features', [])
        feats += got
        if len(got) < PAGE and not page.get('exceededTransferLimit') and not page.get('properties', {}).get('exceededTransferLimit'): break
        offset += len(got)
        if not got: break
    return {'type': 'FeatureCollection', 'features': feats}


if __name__ == '__main__':
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    out = args[0] if args else os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data', 'gold-coast-burleigh')
    for name, layer in LAYERS.items():
        if '--count' in sys.argv:
            print(f"{name:28s} {query(layer, returnCountOnly='true', f='json').get('count')}")
            continue
        os.makedirs(out, exist_ok=True)
        fc = download(layer)
        json.dump(fc, open(os.path.join(out, f'{name}.geojson'), 'w'))
        print(f'{name:28s} {len(fc["features"]):,} features')
