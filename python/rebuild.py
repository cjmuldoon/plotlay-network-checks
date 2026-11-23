"""Rebuild the made-up network from its GeoJSON with NetworkX and Shapely, on its own, without Plotlay, and answer the
showcase's three questions: which meters hang off transformer tr_15, which feeder supplies meter m_0283, and what loses
supply when recloser sw_hv02 opens. The notebook (network_checks.ipynb) runs this file's functions step by step.

The method is the one the page describes: line ends and points closer than 0.5 m are joined; a line end that lands on
another line's middle splits it there (a tee); high and low voltage meet only at a transformer; a switch recorded open
splits the network at its place; a switch with no recorded state is taken as closed."""
import json, math, os
from collections import defaultdict
import networkx as nx
from shapely.geometry import LineString, Point
from shapely.strtree import STRtree

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, '..', 'data', 'made-up-network')
TOL = 0.5
LAT0, LNG0 = -34.9855, 138.5965          # the made-up network's origin (planted.json)
R = 6378137.0


def to_m(lng, lat):
    """Longitude and latitude to metres east and north of the origin (equirectangular: under a centimetre out over 2 km)."""
    return ((lng - LNG0) * math.pi / 180 * R * math.cos(LAT0 * math.pi / 180), (lat - LAT0) * math.pi / 180 * R)


def load(name):
    return json.load(open(os.path.join(DATA, f'{name}.geojson')))['features']


def read():
    lines, points = [], []
    for name, level in (('hv_lines', 'hv'), ('lv_lines', 'lv'), ('service_lines', 'lv')):
        for f in load(name):
            lines.append({'id': f['properties']['ASSET_ID'], 'level': level, 'kind': name, 'props': f['properties'],
                          'pts': [to_m(*c) for c in f['geometry']['coordinates']]})
    for name, role in (('feeder_heads', 'source'), ('transformers', 'transformer'), ('switches', 'switch'), ('meters', 'meter')):
        for f in load(name):
            points.append({'id': f['properties']['ASSET_ID'], 'role': role, 'props': f['properties'], 'at': to_m(*f['geometry']['coordinates'])})
    return lines, points


class Vertices:
    """Places on the lines, joined when closer than TOL (a grid of TOL-sized cells, so a search looks at 9 cells)."""
    def __init__(self):
        self.at, self.level, self.cells = [], [], defaultdict(list)

    def find(self, x, y, level):
        cx, cy = math.floor(x / TOL), math.floor(y / TOL)
        best, bd = None, TOL
        for i in range(cx - 1, cx + 2):
            for j in range(cy - 1, cy + 2):
                for v in self.cells[(i, j)]:
                    if level is not None and self.level[v] != level: continue
                    d = math.dist(self.at[v], (x, y))
                    if d <= bd: best, bd = v, d
        return best

    def add(self, x, y, level):
        v = self.find(x, y, level)
        if v is not None: return v
        self.at.append((x, y)); self.level.append(level); self.cells[(math.floor(x / TOL), math.floor(y / TOL))].append(len(self.at) - 1)
        return len(self.at) - 1


def build(lines, points):
    """The network as a NetworkX graph: vertices are places on the lines, edges the lines between them; each point is
    a node of its own joined to the vertex under it. Open switches are left unjoined, so nothing passes them."""
    V = Vertices()
    # tees: a line end within TOL of another line of the same level, away from its ends, splits that line there
    geoms = [LineString(l['pts']) for l in lines]
    tree = STRtree(geoms)
    cuts = defaultdict(list)
    for i, l in enumerate(lines):
        for end in (l['pts'][0], l['pts'][-1]):
            p = Point(end)
            for j in tree.query(p.buffer(TOL)):
                if j == i or lines[j]['level'] != l['level']: continue
                g = geoms[j]
                if g.distance(p) > TOL: continue
                s = g.project(p)
                if TOL < s < g.length - TOL: cuts[j].append(s)
    # transformers are where high and low voltage meet: a line passing one is cut there too
    for pt in points:
        p = Point(pt['at'])
        for j in tree.query(p.buffer(TOL)):
            g = geoms[j]
            if g.distance(p) <= TOL:
                s = g.project(p)
                if TOL < s < g.length - TOL: cuts[j].append(s)
    G = nx.Graph()
    for i, l in enumerate(lines):
        g = geoms[i]
        stops = [0.0] + sorted(set(round(s, 3) for s in cuts[i])) + [g.length]
        prev = None
        for s in stops:
            q = g.interpolate(s)
            v = V.add(q.x, q.y, l['level'])
            if prev is not None and prev != v:
                G.add_edge(('v', prev), ('v', v), line=l['id'], level=l['level'], length=abs(s - last))
            prev, last = v, s
    for v, (x, y) in enumerate(V.at): G.add_node(('v', v), at=(x, y), level=V.level[v])
    # points: a meter joins the low voltage vertex under it, a feeder head the high voltage one, a transformer both
    unjoined = []
    for pt in points:
        x, y = pt['at']
        levels = {'meter': ['lv'], 'source': ['hv'], 'transformer': ['hv', 'lv'], 'switch': [None]}[pt['role']]
        hit = [V.find(x, y, lv) for lv in levels]
        hit = [h for h in hit if h is not None]
        G.add_node(('p', pt['id']), role=pt['role'], at=pt['at'], props=pt['props'])
        if not hit: unjoined.append(pt['id']); continue
        state = str(pt['props'].get('NORMAL_STATE') or '').lower()
        if pt['role'] == 'switch' and state.startswith('open'):
            # an open switch: the lines on either side keep apart. Each vertex was one place, so split it: the
            # switch's lines are moved to a copy of the vertex, joined to nothing (the network is cut there)
            v = hit[0]; nbrs = list(G.neighbors(('v', v)))
            if len(nbrs) >= 2:
                keep, *move = nbrs
                for n in move[:1]:
                    data = G.edges[('v', v), n]; G.remove_edge(('v', v), n); G.add_edge(('cut', v), n, **data)
            continue
        for h in hit: G.add_edge(('p', pt['id']), ('v', h), line=None, level='point', length=0)
    return G, unjoined


def through(G, start, stop_roles=('transformer',), keep=None):
    """Everything reached from a node without passing a node whose role is in stop_roles (a transformer feeds only its
    own low voltage network). keep: a set of nodes left out (a switch opened as a what-if)."""
    seen, todo = {start}, [start]
    while todo:
        u = todo.pop()
        for w in G.neighbors(u):
            if w in seen or (keep and w in keep): continue
            seen.add(w)
            if G.nodes[w].get('role') in stop_roles and w != start: continue
            todo.append(w)
    return seen


def meters_of(G, nodes):
    return sorted(n[1] for n in nodes if n[0] == 'p' and G.nodes[n].get('role') == 'meter')


def low_side(G, tr):
    """A transformer's low voltage side: its node and the low voltage vertices joined to it."""
    return [n for n in G.neighbors(('p', tr)) if G.nodes[n].get('level') == 'lv']


def trace_down(G, tr):
    seen = {('p', tr)}
    for v in low_side(G, tr): seen |= through(G, v, keep={('p', tr)})
    return meters_of(G, seen)


def trace_up(G, meter):
    """The way back from a meter: the shortest path, by length, to a feeder head, and the transformer on it."""
    heads = [n for n in G if G.nodes[n].get('role') == 'source']
    best = None
    for h in heads:
        try: p = nx.shortest_path(G, ('p', meter), h, weight='length')
        except nx.NetworkXNoPath: continue
        L = sum(G.edges[a, b]['length'] for a, b in zip(p, p[1:]))
        if best is None or L < best[0]: best = (L, h[1], [n[1] for n in p if n[0] == 'p' and G.nodes[n]['role'] == 'transformer'])
    return best


def isolate(G, switch):
    """Meters supplied with every switch as it stands, then with this one open: the difference loses supply."""
    heads = [n for n in G if G.nodes[n].get('role') == 'source']
    sw = ('p', switch); v = next(G.neighbors(sw))
    H = G.copy()
    # opening it: the vertex under it splits, its lines one each side
    nbrs = [n for n in H.neighbors(v) if n != sw]
    H.remove_node(v)
    for k, n in enumerate(nbrs): H.add_edge(('open', k), n)
    before = set().union(*[through(G, h, stop_roles=()) for h in heads])
    after = set().union(*[through(H, h, stop_roles=()) for h in heads])
    return meters_of(G, before - after)


if __name__ == '__main__':
    lines, points = read()
    G, unjoined = build(lines, points)
    print(f'{len(lines)} lines, {len(points)} points; graph {G.number_of_nodes()} nodes, {G.number_of_edges()} edges; points joined to nothing: {unjoined}')
    d = trace_down(G, 'tr_15'); print('tr_15 feeds', len(d), 'meters')
    print('m_0283 is fed from', trace_up(G, 'm_0283'))
    print('sw_hv02 open:', len(isolate(G, 'sw_hv02')), 'meters lose supply')
