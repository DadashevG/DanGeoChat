"""
GNN inference service — lazy-loaded on first request.
Heavy imports (torch, torch_geometric, osmnx) are deferred so the
backend starts instantly even if GNN files are missing.
"""
import threading
from pathlib import Path
import numpy as np

GNN_DIR       = Path(__file__).parent.parent.parent.parent / "אימון GNN"
MODEL_PATH    = GNN_DIR / "outputs" / "metro_gnn_best.pt"
FEATURES_PATH = GNN_DIR / "data"    / "node_features.npz"
GRAPH_PATH    = GNN_DIR / "data"    / "osm_road.graphml"

# Bounding box of the trained area (Tel Aviv-Yafo OSM graph)
LAT_MIN, LAT_MAX = 31.99, 32.16
LON_MIN, LON_MAX = 34.73, 34.87

CONN_LABELS = ['low', 'medium', 'high']
PT_LABELS   = ['poor', 'moderate', 'rich']
ROLE_LABELS = ['isolated', 'residential', 'transit_served', 'local_hub', 'metropolitan_hub']


def is_in_trained_area(lat: float, lon: float) -> bool:
    return LAT_MIN <= lat <= LAT_MAX and LON_MIN <= lon <= LON_MAX


class GNNService:
    def __init__(self):
        self._lock  = threading.Lock()
        self._ready = False
        self._error: str | None = None

    @property
    def available(self) -> bool:
        return MODEL_PATH.exists() and FEATURES_PATH.exists() and GRAPH_PATH.exists()

    def _load(self):
        if self._ready or self._error:
            return
        with self._lock:
            if self._ready or self._error:
                return
            try:
                self._do_load()
                self._ready = True
            except Exception as exc:
                self._error = str(exc)
                raise

    def _do_load(self):
        import torch
        import torch.nn.functional as F
        from torch.nn import Linear
        from torch_geometric.nn import SAGEConv, BatchNorm, global_mean_pool
        from torch_geometric.utils import subgraph as pyg_subgraph
        from sklearn.neighbors import BallTree
        from sklearn.preprocessing import StandardScaler
        import osmnx as ox

        print("[GNN] Loading OSM graph …")
        G = ox.load_graphml(GRAPH_PATH)
        node_ids = list(G.nodes())
        id2idx   = {nid: i for i, nid in enumerate(node_ids)}

        src_list, dst_list = [], []
        for u, v in G.edges():
            if u in id2idx and v in id2idx:
                src_list.append(id2idx[u])
                dst_list.append(id2idx[v])
        edge_index_global = torch.tensor([src_list, dst_list], dtype=torch.long)

        node_coords_rad = np.deg2rad(
            [[G.nodes[nid]['y'], G.nodes[nid]['x']] for nid in node_ids]
        )
        node_tree = BallTree(node_coords_rad, metric='haversine')

        print("[GNN] Loading features …")
        saved = np.load(FEATURES_PATH, allow_pickle=True)
        X     = saved["X"]                          # (N, 8)

        FEAT_COLS = [0, 1, 2, 4, 5, 6, 7]          # drop col 3 (road_service_count)
        X_proc    = X[:, FEAT_COLS].copy()
        X_proc[:, -1] = np.log1p(X_proc[:, -1])    # log1p on unique_gtfs_routes_count
        scaler    = StandardScaler()
        X_norm    = scaler.fit_transform(X_proc).astype(np.float32)

        X_raw_tensor  = torch.tensor(X,      dtype=torch.float)
        X_norm_tensor = torch.tensor(X_norm, dtype=torch.float)
        IN_DIM        = X_norm_tensor.shape[1]      # 7

        # ── Model definition (must match notebook) ───────────────────────────
        class MetroGNN(torch.nn.Module):
            def __init__(self, in_dim, hidden_dim=64, dropout=0.5):
                super().__init__()
                self.dropout = dropout
                self.conv1 = SAGEConv(in_dim,     hidden_dim); self.bn1 = BatchNorm(hidden_dim)
                self.conv2 = SAGEConv(hidden_dim, hidden_dim); self.bn2 = BatchNorm(hidden_dim)
                self.conv3 = SAGEConv(hidden_dim, hidden_dim); self.bn3 = BatchNorm(hidden_dim)
                self.head_conn = Linear(hidden_dim, 3)
                self.head_pt   = Linear(hidden_dim, 3)
                self.head_role = Linear(hidden_dim, 5)

            def encode(self, x, edge_index):
                h = F.relu(self.bn1(self.conv1(x, edge_index)))
                h = F.dropout(h, p=self.dropout, training=self.training)
                h = F.relu(self.bn2(self.conv2(h, edge_index)))
                h = F.dropout(h, p=self.dropout, training=self.training)
                return F.relu(self.bn3(self.conv3(h, edge_index)))

            def forward(self, x, edge_index, batch):
                g = global_mean_pool(self.encode(x, edge_index), batch)
                return {
                    'conn_logits': self.head_conn(g),
                    'pt_logits':   self.head_pt(g),
                    'role_logits': self.head_role(g),
                }

        print("[GNN] Loading model weights …")
        model = MetroGNN(in_dim=IN_DIM)
        model.load_state_dict(torch.load(MODEL_PATH, weights_only=True, map_location='cpu'))
        model.eval()

        # ── Store all state ──────────────────────────────────────────────────
        self._G                 = G
        self._node_ids          = node_ids
        self._id2idx            = id2idx
        self._edge_index_global = edge_index_global
        self._node_coords_rad   = node_coords_rad
        self._node_tree         = node_tree
        self._X_raw_tensor      = X_raw_tensor
        self._X_norm_tensor     = X_norm_tensor
        self._N_TOTAL           = len(node_ids)
        self._model             = model
        self._pyg_subgraph      = pyg_subgraph
        print("[GNN] Ready ✓")

    def infer(self, lat: float, lon: float) -> dict:
        self._load()

        import torch
        import osmnx as ox

        nearest_nid = ox.nearest_nodes(self._G, X=lon, Y=lat)
        ci          = self._id2idx[nearest_nid]
        R_RAD       = 250 / 6_371_000

        local_np  = self._node_tree.query_radius(
            self._node_coords_rad[ci:ci+1], r=R_RAD
        )[0]
        local_idx = torch.tensor(local_np, dtype=torch.long)
        sub_ei, _ = self._pyg_subgraph(
            local_idx, self._edge_index_global,
            relabel_nodes=True, num_nodes=self._N_TOTAL
        )

        sub_x_raw  = self._X_raw_tensor[local_idx]
        sub_x_norm = self._X_norm_tensor[local_idx]

        batch_vec = torch.zeros(len(local_idx), dtype=torch.long)
        with torch.no_grad():
            out = self._model(sub_x_norm, sub_ei, batch_vec)

        # Extract street name from OSM edges of nearest node
        street_name = None
        for _, _, data in self._G.edges(nearest_nid, data=True):
            name = data.get("name")
            if name and isinstance(name, str):
                street_name = name
                break
            elif isinstance(name, list) and name:
                street_name = name[0]
                break

        return {
            'nearest_osm_node':       int(nearest_nid),
            'subgraph_nodes':         len(local_idx),
            'street_name':            street_name,
            'connectivity_level':     CONN_LABELS[out['conn_logits'].argmax(1).item()],
            'public_transport_level': PT_LABELS[out['pt_logits'].argmax(1).item()],
            'network_role':           ROLE_LABELS[out['role_logits'].argmax(1).item()],
            'evidence': {
                'major_intersections_count': int((sub_x_raw[:, 1] > 0).sum()),
                'bus_stop_count':            int(sub_x_raw[:, 4].sum()),
                'light_rail_stop_count':     int(sub_x_raw[:, 5].sum()),
                'train_stop_count':          int(sub_x_raw[:, 6].sum()),
                'unique_gtfs_routes_count':  int(sub_x_raw[:, 7].sum()),
            },
        }


gnn_service = GNNService()
